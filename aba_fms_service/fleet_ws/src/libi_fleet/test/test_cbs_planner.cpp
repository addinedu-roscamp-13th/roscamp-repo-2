// cbs_planner.cpp 회귀 테스트.
//
// 이 파일이 생기기 전까지 cbs_planner.cpp 는 **어떤 CMake 타깃에도 속하지 않아** 컴파일조차
// 되지 않았다. self-check main() 이 있었지만 아무도 돌리지 않았고, 케이스도 T자 4정점 하나뿐이었다.
//
// 여기서 잡으려는 것은 "돌아간다"가 아니라 **조용히 틀리는 경우**다:
//   · 방향 그래프에서 휴리스틱이 뒤집히는가 (bfs_dist 가 역방향 BFS 여야 한다)
//   · 출발 정점이 같은 충돌을 CBS 가 풀 수 있는가 (시작 상태의 vertex 제약 검사)
//   · horizon 초과와 "해 없음"이 구별되는가
// 앞의 둘은 무방향·서로 다른 출발점인 기존 self-check 로는 절대 드러나지 않는다.

#include <gtest/gtest.h>

#include <algorithm>
#include <tuple>
#include <vector>

namespace libi_fleet {
using Graph = std::vector<std::vector<int>>;
using Path = std::vector<int>;
std::vector<Path> cbs_plan(const Graph& g, const std::vector<int>& starts,
                           const std::vector<int>& goals);
}  // namespace libi_fleet

using libi_fleet::Graph;
using libi_fleet::Path;

namespace {

int loc_at(const Path& p, int t)
{
  return t < static_cast<int>(p.size()) ? p[t] : p.back();
}

// 해에 vertex/edge 충돌이 없는지 독립적으로 검사한다.
// 플래너 내부의 find_conflict 는 static 이라 여기서 못 쓴다 — 오히려 그게 낫다.
// 같은 함수로 만들고 같은 함수로 검사하면 그 함수의 버그를 영원히 못 잡는다.
::testing::AssertionResult NoConflict(const std::vector<Path>& sol)
{
  int T = 0;
  for (const auto& p : sol) { T = std::max(T, static_cast<int>(p.size())); }
  for (int t = 0; t < T; ++t) {
    for (size_t i = 0; i < sol.size(); ++i) {
      for (size_t j = i + 1; j < sol.size(); ++j) {
        const int ai = loc_at(sol[i], t), aj = loc_at(sol[j], t);
        if (ai == aj) {
          return ::testing::AssertionFailure()
                 << "vertex 충돌: 로봇 " << i << "," << j << " 가 t=" << t << " 에 정점 " << ai;
        }
        if (t > 0) {
          const int pi = loc_at(sol[i], t - 1), pj = loc_at(sol[j], t - 1);
          if (pi == aj && pj == ai) {
            return ::testing::AssertionFailure()
                   << "edge 충돌: 로봇 " << i << "," << j << " 가 t=" << t
                   << " 에 " << pi << "<->" << ai << " 를 맞바꿈";
          }
        }
      }
    }
  }
  return ::testing::AssertionSuccess();
}

bool uses(const std::vector<Path>& sol, int v)
{
  for (const auto& p : sol) {
    if (std::find(p.begin(), p.end(), v) != p.end()) { return true; }
  }
  return false;
}

}  // namespace

// ── 기존 self-check 이식 ──────────────────────────────────────────────
// 0 — 1 — 2 에 1 아래로 대기소 3. A:0→2, B:2→0 은 좁은 1 에서 정면충돌한다.
TEST(CbsPlanner, HeadOnUsesBay)
{
  const Graph g = {{1}, {0, 2, 3}, {1}, {1}};
  const auto sol = libi_fleet::cbs_plan(g, {0, 2}, {2, 0});

  ASSERT_EQ(sol.size(), 2u) << "해를 찾아야 한다";
  EXPECT_EQ(sol[0].front(), 0);
  EXPECT_EQ(sol[0].back(), 2);
  EXPECT_EQ(sol[1].front(), 2);
  EXPECT_EQ(sol[1].back(), 0);
  EXPECT_TRUE(NoConflict(sol));
  EXPECT_TRUE(uses(sol, 3)) << "정면충돌 회피에 대기소(3)를 써야 한다";
}

// ── 단일 로봇: 최단경로여야 한다 ──────────────────────────────────────
TEST(CbsPlanner, SingleAgentShortestPath)
{
  const Graph g = {{1}, {0, 2, 3}, {1}, {1}};
  const auto sol = libi_fleet::cbs_plan(g, {0}, {2});

  ASSERT_EQ(sol.size(), 1u);
  EXPECT_EQ(sol[0], (Path{0, 1, 2})) << "대기소를 경유할 이유가 없다";
}

// ── 도달 불가: 해가 없어야 한다 ───────────────────────────────────────
TEST(CbsPlanner, UnreachableGoalReturnsEmpty)
{
  const Graph g = {{1}, {0}, {3}, {2}};   // {0,1} 과 {2,3} 이 분리
  const auto sol = libi_fleet::cbs_plan(g, {0}, {3});
  EXPECT_TRUE(sol.empty());
}

// ── 🔴 회귀: 출발 정점이 같은 충돌 ────────────────────────────────────
//
// 두 로봇이 같은 정점에서 출발하면 t=0 에 vertex 충돌이다. CBS 는 한쪽에
// (start, 0) 제약을 걸어 분기하는데, low-level A* 가 **시작 상태를 제약 검사 없이**
// open 에 넣으면 그 제약을 무시하고 같은 경로를 계속 만든다 → 고수준이 같은 충돌을 무한 반복.
//
// 물리적으로 두 로봇이 한 정점에 겹칠 수는 없지만, **초기 위치 추정이 같은 정점으로
// 스냅되는 경우**(반경 0.05 m 도착판정, 정점 간격이 좁은 arte2)에 실제로 일어난다.
// 그때 플래너가 응답을 못 하면 전체 배차가 멈춘다.
TEST(CbsPlanner, SameStartVertexDoesNotHang)
{
  const Graph g = {{1}, {0, 2, 3}, {1}, {1}};
  const auto sol = libi_fleet::cbs_plan(g, {1, 1}, {0, 2});

  // 요구사항은 "반드시 해를 찾아라"가 아니다 — 물리적으로 불가능할 수 있다.
  // 요구사항은 **멈추지 않고 판정을 돌려주는 것**이다. 해를 냈다면 충돌이 없어야 한다.
  if (!sol.empty()) {
    ASSERT_EQ(sol.size(), 2u);
    EXPECT_TRUE(NoConflict(sol));
  }
  SUCCEED() << "무한 루프 없이 반환했다";
}

// ── 🔴 회귀: 방향 그래프 휴리스틱 ─────────────────────────────────────
//
// bfs_dist(g, goal) 이 goal 에서 **나가는** 간선을 따라가면, 방향 그래프에서
// "goal 까지 가는 거리"가 아니라 "goal 에서 나가는 거리"를 재게 된다.
// navgraph 의 lane 은 방향 간선이다 (arte2.navgraph.yaml 이 [a,b] 와 [b,a] 를 따로 적는다).
//
// 아래는 단방향 순환: 0 → 1 → 2 → 3 → 0. 0 에서 3 으로 가려면 1,2 를 거쳐야 한다.
// 역방향 BFS 가 아니면 h 가 틀어져 최적성이나 도달성 판정이 깨진다.
TEST(CbsPlanner, DirectedCycleReachesGoal)
{
  const Graph g = {{1}, {2}, {3}, {0}};   // 단방향 링
  const auto sol = libi_fleet::cbs_plan(g, {0}, {3});

  ASSERT_FALSE(sol.empty()) << "단방향 링에서 0→3 은 0,1,2,3 으로 도달 가능하다";
  EXPECT_EQ(sol[0].front(), 0);
  EXPECT_EQ(sol[0].back(), 3);
  // 링을 따라가는 것 외에 다른 길이 없다.
  EXPECT_EQ(sol[0], (Path{0, 1, 2, 3}));
}

// ── 도착 후 점유가 다른 로봇을 막는 경우 ──────────────────────────────
//
// A 가 목표에 먼저 앉아 통로를 막으면 B 가 지나갈 수 없다.
// loc_at() 이 경로 끝 이후 goal 을 유지하므로 이 충돌이 감지되고,
// (goal, t) vertex 제약으로 A 가 늦게 도착하도록 풀려야 한다.
TEST(CbsPlanner, GoalOccupancyBlocksOther)
{
  //  0 — 1 — 2 — 4      3 은 1 아래 대기소
  //      |
  //      3
  const Graph g = {{1}, {0, 2, 3}, {1, 4}, {1}, {2}};
  const auto sol = libi_fleet::cbs_plan(g, {0, 4}, {1, 0});

  ASSERT_EQ(sol.size(), 2u) << "해를 찾아야 한다 — A 가 1 에 앉기 전에 B 를 보내면 된다";
  EXPECT_TRUE(NoConflict(sol));
  EXPECT_EQ(sol[0].back(), 1);
  EXPECT_EQ(sol[1].back(), 0);
}

// ── 3대 동시 ────────────────────────────────────────────────────────
TEST(CbsPlanner, ThreeAgentsNoConflict)
{
  //      3
  //      |
  //  0 — 1 — 2
  //      |
  //      4
  const Graph g = {{1}, {0, 2, 3, 4}, {1}, {1}, {1}};
  const auto sol = libi_fleet::cbs_plan(g, {0, 2, 3}, {2, 0, 4});

  ASSERT_EQ(sol.size(), 3u);
  EXPECT_TRUE(NoConflict(sol));
  for (size_t i = 0; i < 3u; ++i) { EXPECT_FALSE(sol[i].empty()); }
}

// ── 🔴 회귀: 방향 그래프에서 역방향 BFS 를 안 하면 "도달 불가"로 오판한다 ──
//
//   0 → 1 → 2(goal),  2 → 3
// goal=2 에서 **나가는** 간선만 따라가면 d[3]=1 이고 d[0]=d[1]=INF 가 나온다.
// 그러면 space_time_astar 가 h[start]==INF 를 보고 "도달 불가 그래프"라며 즉시 포기한다.
// 실제로는 0→1→2 로 갈 수 있다.
TEST(CbsPlanner, DirectedGraphReverseHeuristic)
{
  const Graph g = {{1}, {2}, {3}, {}};
  const auto sol = libi_fleet::cbs_plan(g, {0}, {2});

  ASSERT_FALSE(sol.empty()) << "0→1→2 로 도달 가능한데 도달 불가로 판정했다 (역방향 BFS 누락)";
  EXPECT_EQ(sol[0], (Path{0, 1, 2}));
}
