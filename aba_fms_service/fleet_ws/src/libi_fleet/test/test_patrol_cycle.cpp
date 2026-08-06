#include <gtest/gtest.h>
#include <algorithm>
#include <set>
#include <string>
#include "libi_fleet/navgraph.hpp"
#include "libi_fleet/patrol_cycle.hpp"

using libi_fleet::Navgraph;
using libi_fleet::patrol_path_from;
using libi_fleet::right_hand_boundary_cycle;

// 실제 지도 yaml 경로는 CMake 에서 TEST_NAVGRAPH_PATH 로 주입.
static std::string kNavgraph() { return std::string(TEST_NAVGRAPH_PATH); }
// 정점이 순회 루프 밖에도 있는 실제 지도(진입 구간이 여러 홉이 되는 상황을 만든다).
static std::string kArte2() { return std::string(TEST_ARTE2_NAVGRAPH_PATH); }

// a 와 b 가 navgraph 에서 실제 간선으로 이어져 있나.
static bool adjacent(const Navgraph & g, int a, int b) {
  const auto & nb = g.neighbors(a);
  return std::find(nb.begin(), nb.end(), b) != nb.end();
}

TEST(PatrolCycle, MatchesRightDownBoundaryOnLibraryMap) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  auto cyc = right_hand_boundary_cycle(g);
  std::vector<int> expected = {0, 1, 2, 3, 7, 6, 5, 4};
  EXPECT_EQ(cyc, expected);
}

TEST(PatrolCycle, VisitsAllBoundaryNodesOnce) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  auto cyc = right_hand_boundary_cycle(g);
  std::set<int> uniq(cyc.begin(), cyc.end());
  EXPECT_EQ(uniq.size(), cyc.size());          // 중복 없음
  EXPECT_EQ(cyc.front(), 0);                   // 최상단·최좌측에서 시작
}

// ── 순회에 **붙는 구간**도 정점 하나하나여야 한다 ────────────────────────────
//
// 교통관제는 경로의 다음 한 정점만 예약한다. 그래서 경로에 인접하지 않은 두 정점이
// 나란히 있으면, 그 사이를 지나는 동안 로봇은 **아무 예약도 없이** 달린다.
// 예전 `make_patrol_path` 가 Dijkstra 로 진입점을 고르면서 그 정점열은 버리고
// `[현재정점, 진입점, 랩…]` 만 만들어서 정확히 그 상태였다.
TEST(PatrolPath, EveryHopIsARealLane) {
  Navgraph g;
  ASSERT_TRUE(g.load(kArte2(), "L1"));
  const auto route = right_hand_boundary_cycle(g);
  ASSERT_GE(route.size(), 3u);

  int multi_hop_entries = 0;   // 진입 구간이 2홉 이상이었던 출발점 수
  for (int snap = 0; snap < g.size(); ++snap) {
    const auto path = patrol_path_from(g, snap, route);
    ASSERT_GE(path.size(), 2u) << "snap=" << snap << " 에서 경로가 안 나왔다";
    EXPECT_EQ(path.front(), snap) << "snap=" << snap << " 이 경로 앞에 없다";
    for (std::size_t i = 0; i + 1 < path.size(); ++i) {
      EXPECT_TRUE(adjacent(g, path[i], path[i + 1]))
        << "snap=" << snap << " 경로에 간선이 없는 구간: v" << path[i]
        << " → v" << path[i + 1] << " (그 사이는 예약되지 않는다)";
    }
    // 진입 구간 길이 = 전체 − 랩(route.size()) − 루프닫기 1칸.
    if (path.size() >= route.size() + 3) { ++multi_hop_entries; }
  }
  // ⚠️ 여기서 `EXPECT_GT(multi_hop_entries, 0)` 를 걸지 **않는다.**
  //    arte2 는 모든 정점이 순회 루프에서 한 홉 안이라 진입 구간이 늘 1홉이다 —
  //    즉 이 지도에서는 옛 코드도 우연히 간선을 지켰다. 걸어 두면 지도 때문에
  //    시험이 빨개지고, 그건 코드가 틀렸다는 뜻이 아니다.
  //    이 검사는 **지도가 커져 루프에서 두 홉 이상 떨어진 정점이 생기는 날**을 위한
  //    안전망이다. 그날 옛 코드였다면 그 구간이 통째로 예약 밖에서 달렸을 것이다.
  //    지금 실제로 옛 코드를 빨갛게 만드는 것은 아래 avoid_first 시험이다.
  RecordProperty("multi_hop_entries", multi_hop_entries);
}

// 진입점을 한 칸 미는 경우(avoid_first)에도 경로가 끊기면 안 된다.
// 진입점이 바뀌면 **진입 구간을 다시 구해야** 한다 — 안 그러면 옛 진입점으로 가는
// 정점열이 새 진입점 앞에 그대로 붙어 이가 안 맞는다.
TEST(PatrolPath, StaysConnectedWhenEntryIsPushedForward) {
  Navgraph g;
  ASSERT_TRUE(g.load(kArte2(), "L1"));
  const auto route = right_hand_boundary_cycle(g);
  ASSERT_GE(route.size(), 3u);

  for (int snap = 0; snap < g.size(); ++snap) {
    const auto plain = patrol_path_from(g, snap, route);
    ASSERT_GE(plain.size(), 2u);
    // 이 출발점이 실제로 고른 진입점의 **다음 칸**을 피하라고 준다 → k 가 한 칸 민다.
    const auto it = std::find(route.begin(), route.end(), plain.back());
    ASSERT_NE(it, route.end());
    const int next_of_entry = route[(std::distance(route.begin(), it) + 1) % route.size()];

    const auto pushed = patrol_path_from(g, snap, route, next_of_entry);
    ASSERT_GE(pushed.size(), 2u) << "snap=" << snap;
    EXPECT_EQ(pushed.front(), snap);
    for (std::size_t i = 0; i + 1 < pushed.size(); ++i) {
      EXPECT_TRUE(adjacent(g, pushed[i], pushed[i + 1]))
        << "snap=" << snap << " (avoid_first=v" << next_of_entry
        << ") 경로에 간선이 없는 구간: v" << pushed[i] << " → v" << pushed[i + 1];
    }
  }
}

TEST(PatrolPath, LeavesChargingStationDirectlyThroughChargingCorridor) {
  Navgraph g;
  ASSERT_TRUE(g.load(kArte2(), "L1"));
  const auto route = right_hand_boundary_cycle(g);
  ASSERT_GE(route.size(), 3u);

  // arte2 map: v19=충전소, v17=충전소통로. 충전 완료 후 순찰 재개는
  // 충전소에서 바로 통로로 나가야 한다(입구/본관을 먼저 찍으면 안 됨).
  const auto path = patrol_path_from(g, 19, route);
  ASSERT_GE(path.size(), 2u);
  EXPECT_EQ(path[0], 19);
  EXPECT_EQ(path[1], 17);
}
