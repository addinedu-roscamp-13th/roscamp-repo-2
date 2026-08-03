// cbs_planner.cpp — CBS + 가중 Space-Time A* 다중로봇 경로계획.
// 근거: doc "03 교통관제 알고리즘 — CBS + Space-Time A*". 출발 전 전 로봇 경로를 미리
// 계산해 충돌 0(vertex/edge)으로 동시 출발.
//
// [2026-07-26] 단위시간 간선 → **가중 간선**으로 승급. 예전 ponytail 주석의 숙제였다.
//   이유: 틱을 실제 시간으로 쓰려면 간선마다 소요가 달라야 한다. arte2 는 최소 레인이
//   0.062 m 인데 긴 레인은 그 몇 배다. 모든 간선을 1틱으로 두면 짧은 레인은 기어가야 하고
//   긴 레인은 못 지킨다 — 계획 시각과 실제 시각이 처음부터 어긋난다.
//   이제 상태는 (정점, 도착틱)이고, 정점 점유는 [arrive, depart] **구간**이다.
//
// [2026-07-26] clearance(여유 틱) 도입. 실제 로봇은 계획대로 정확히 도착하지 않으므로
//   점유 구간을 비교할 때 양쪽에 여유를 덧대 겹침을 판정한다. 계획에서 미리 벌려 두면
//   실행이 조금 밀려도 무충돌이 유지된다.
//
// 빌드/검증:  g++ -std=c++17 -I../include cbs_planner.cpp -o cbs && ./cbs
// ponytail: CBS 고수준은 최선우선(비용순) + 확장 상한(max_nodes). 상한을 넘으면 실패로
//           본다 — 조용히 오래 도는 것보다 낫다. 로봇이 늘면 상한부터 올린다.

#include "libi_fleet/cbs_planner.hpp"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <tuple>
#include <vector>

namespace libi_fleet
{
namespace
{

constexpr int kInf = std::numeric_limits<int>::max();

// 한 로봇에 걸리는 제약. 구간 단위다(가중 간선이라 한 시점으로는 못 막는다).
//   vertex: [t0, t1] 동안 v 점유 금지
//   edge  : [t0, t1] 과 겹치게 u→v 통과 금지
struct TimedConstraints
{
  std::vector<std::tuple<int, int, int>> vertex;       // (v, t0, t1)
  std::vector<std::tuple<int, int, int, int>> edge;    // (u, v, t0, t1)
};

bool overlaps(int a0, int a1, int b0, int b1)
{
  return a0 <= b1 && b0 <= a1;
}

// 시각 t 에 정점 v 에 있어도 되나.
bool vertex_blocked(const TimedConstraints & c, int v, int t)
{
  for (const auto & [cv, t0, t1] : c.vertex) {
    if (cv == v && t >= t0 && t <= t1) { return true; }
  }
  return false;
}

// [t_dep, t_arr] 동안 u→v 를 지나도 되나.
bool edge_blocked(const TimedConstraints & c, int u, int v, int t_dep, int t_arr)
{
  for (const auto & [cu, cv, t0, t1] : c.edge) {
    if (cu == u && cv == v && overlaps(t_dep, t_arr, t0, t1)) { return true; }
  }
  return false;
}

// 목표까지의 **가중** 최단 소요(틱). space-time A* 의 admissible 휴리스틱.
//
// ★ **역방향이어야 한다.** navgraph 의 lane 은 방향 간선이다 — yaml 이 [a,b] 와 [b,a] 를
//   따로 적는 것이 그 뜻이고, navgraph.cpp:21-35 로더도 각 lane 을 방향 인접으로만 넣는다.
//   goal 에서 나가는 간선을 따라가면 "goal **에서** v 까지"를 재게 되는데, 필요한 것은
//   "v **에서** goal 까지"다. 방향 그래프에서 이 둘은 다르다.
//
//   증상: 0→1→2 (goal=2), 2→3 만 있는 그래프에서 예전 코드는 d[0]=INF 를 내놓고
//         "도달 불가 그래프"로 즉시 포기했다. 실제로는 0→1→2 로 갈 수 있는데도.
//   무방향 그래프에서는 둘이 같아서 기존 self-check(T자)로는 드러나지 않았다.
//   (가중으로 바뀌면서 BFS → Dijkstra 가 됐다. 역방향이어야 하는 이유는 그대로다.)
std::vector<int> reverse_dijkstra(const TimedGraph & g, int goal)
{
  const int n = static_cast<int>(g.size());
  std::vector<std::vector<std::pair<int, int>>> rev(n);
  for (int v = 0; v < n; ++v) {
    for (const auto & [w, cost] : g[v]) { rev[w].push_back({v, cost}); }
  }
  std::vector<int> d(n, kInf);
  using QN = std::pair<int, int>;   // (거리, 정점)
  std::priority_queue<QN, std::vector<QN>, std::greater<QN>> pq;
  d[goal] = 0;
  pq.push({0, goal});
  while (!pq.empty()) {
    auto [dv, v] = pq.top();
    pq.pop();
    if (dv > d[v]) { continue; }
    for (const auto & [w, cost] : rev[v]) {
      if (dv + cost < d[w]) {
        d[w] = dv + cost;
        pq.push({d[w], w});
      }
    }
  }
  return d;
}

// 로봇 i 의 출발 시각(틱). 안 주면 0 — 예전과 같은 동작이다.
// 음수는 0 으로 본다("모른다" 를 미래로 해석하지 않는다).
int start_tick_of(const PlanOptions & opt, std::size_t i)
{
  if (i >= opt.start_ticks.size()) { return 0; }
  return std::max(0, opt.start_ticks[i]);
}

// 가중 Space-Time A*: 제약을 지키며 start→goal 의 시각별 경로. 실패 시 빈 Route.
//
// 상태는 (정점, 도착틱). 확장은 두 가지다:
//   · 대기  : (v, t) → (v, t+1)          — 그 틱에 v 가 막혀 있지 않아야 한다
//   · 이동  : (v, t) → (w, t+cost)       — [t, t+cost] 동안 v→w 가 막혀 있지 않아야 하고,
//                                          도착 시각에 w 가 막혀 있지 않아야 한다
// 대기를 1틱짜리 상태로 쪼개 두므로, 머무는 동안의 정점 제약이 자동으로 매 틱 검사된다.
Route timed_astar(
  const TimedGraph & g, int start, int goal, const TimedConstraints & c, int horizon,
  int max_expansions, int start_tick)
{
  const std::vector<int> h = reverse_dijkstra(g, goal);
  if (h[start] == kInf) { return {}; }   // 도달 불가 그래프

  // 목표에 도착하면 그 자리에 계속 앉아 있는다. 그러니 goal 에 걸린 제약이 끝난 **뒤에**
  // 도착해야 한다. 그 전에 도착하면 남의 통과를 막는다.
  int min_goal_arrive = 0;
  for (const auto & [cv, t0, t1] : c.vertex) {
    if (cv == goal) { min_goal_arrive = std::max(min_goal_arrive, t1 + 1); }
  }

  struct N { int v, t, f; };
  struct Cmp { bool operator()(const N & a, const N & b) const { return a.f > b.f; } };
  std::priority_queue<N, std::vector<N>, Cmp> open;
  std::set<std::pair<int, int>> closed;
  std::map<std::pair<int, int>, std::pair<int, int>> parent;

  // ★ 시작 상태에도 제약을 적용한다. 이게 없으면 두 로봇의 **출발 정점이 같은** 충돌을
  //   CBS 가 영원히 못 푼다: 고수준이 시작 정점에 제약을 걸어 분기해도, 저수준이
  //   시작 상태를 제약 검사 없이 open 에 넣어 같은 경로를 그대로 돌려주고, 고수준은
  //   같은 충돌을 다시 보고 또 분기한다 — **제약트리가 무한히 자란다.**
  //   (실측: test_cbs_planner.SameStartVertexDoesNotHang 이 8초 타임아웃으로 죽었다)
  //
  //   물리적으로 두 로봇이 한 정점에 겹칠 수는 없지만, arte2 처럼 정점 간격이 좁고
  //   도착 판정 반경이 0.05 m 인 맵에서는 **초기 위치가 같은 정점으로 스냅**될 수 있다.
  //   그때 플래너가 안 돌아오면 배차 전체가 멈춘다.
  // ⚠️ **출발 시각은 0 이 아닐 수 있다.** 이동 중 재계획이면 로봇이 `start` 에 닿기까지
  //    남은 시간이 있고, 그 값이 `start_tick` 으로 들어온다 — 근거는 `PlanOptions::start_ticks`.
  //    여기서 반영해야 arrive/depart 와 **충돌 제약이 같은 시간축**에서 밀린다.
  if (vertex_blocked(c, start, start_tick)) { return {}; }
  open.push({start, start_tick, start_tick + h[start]});

  int expansions = 0;
  while (!open.empty()) {
    if (++expansions > max_expansions) { return {}; }   // 상한 초과 — 계획 포기(멈추지 않는다)
    const N n = open.top();
    open.pop();
    const std::pair<int, int> key{n.v, n.t};
    if (closed.count(key)) { continue; }
    closed.insert(key);

    if (n.v == goal && n.t >= min_goal_arrive) {
      // 재구성 — (정점, 도착틱) 사슬을 되짚어 Step 으로 접는다.
      std::vector<std::pair<int, int>> chain;
      std::pair<int, int> cur = key;
      while (true) {
        chain.push_back(cur);
        auto it = parent.find(cur);
        if (it == parent.end()) { break; }
        cur = it->second;
      }
      std::reverse(chain.begin(), chain.end());

      Route r;
      for (const auto & [v, t] : chain) {
        if (!r.empty() && r.back().v == v) {
          r.back().depart = t;      // 같은 정점에서 대기 — 체류 구간을 늘린다
        } else {
          r.push_back(Step{v, t, t});
        }
      }
      r.back().depart = kNeverEnds;  // 목표에 계속 앉아 있음
      return r;
    }
    if (n.t >= horizon) { continue; }

    // 대기.
    if (!vertex_blocked(c, n.v, n.t + 1)) {
      const std::pair<int, int> nk{n.v, n.t + 1};
      if (!closed.count(nk) && !parent.count(nk)) {
        parent[nk] = key;
        open.push({n.v, n.t + 1, n.t + 1 + h[n.v]});
      }
    }
    // 이동.
    for (const auto & [w, cost] : g[n.v]) {
      const int nt = n.t + cost;
      if (nt > horizon) { continue; }
      if (h[w] == kInf) { continue; }                        // 목표에서 끊긴 정점
      if (edge_blocked(c, n.v, w, n.t, nt)) { continue; }
      if (vertex_blocked(c, w, nt)) { continue; }
      const std::pair<int, int> nk{w, nt};
      if (closed.count(nk) || parent.count(nk)) { continue; }
      parent[nk] = key;
      open.push({w, nt, nt + h[w]});
    }
  }
  return {};
}

struct TimedConflict
{
  bool exists{false};
  int a{0}, b{0};        // 충돌한 두 로봇
  bool is_edge{false};
  int u{0}, v{0};        // vertex 충돌이면 u==v==정점, edge 충돌이면 a 의 진행 방향 u→v
  int a0{0}, a1{0};      // a 쪽 점유/통과 구간
  int b0{0}, b1{0};      // b 쪽 점유/통과 구간
  int at{0};             // 정렬용 — 충돌이 시작되는 시각
};

// 두 계획의 첫 충돌(시각 오름차순).
//   vertex: 같은 정점의 점유 구간이 겹침(여유 clearance 포함)
//   edge  : 같은 간선을 서로 반대 방향으로 통과하는 구간이 겹침
//
// 같은 방향 추종(후미추돌)은 따로 보지 않는다 — 양 끝 정점의 점유 구간이 clearance 만큼
// 벌어져 있으면 사이 간격이 유지되기 때문이다(노드 예약만으로 충분하다는 기존 설계와 같다).
TimedConflict find_conflict(const std::vector<Route> & routes, int clearance)
{
  TimedConflict best;
  auto better = [&](const TimedConflict & c) { return !best.exists || c.at < best.at; };

  for (size_t i = 0; i < routes.size(); ++i) {
    for (size_t j = i + 1; j < routes.size(); ++j) {
      // vertex 충돌.
      for (const auto & si : routes[i]) {
        for (const auto & sj : routes[j]) {
          if (si.v != sj.v) { continue; }
          if (!overlaps(si.arrive - clearance, si.depart + clearance, sj.arrive, sj.depart)) {
            continue;
          }
          TimedConflict c;
          c.exists = true;
          c.a = static_cast<int>(i);
          c.b = static_cast<int>(j);
          c.is_edge = false;
          c.u = c.v = si.v;
          c.a0 = si.arrive; c.a1 = si.depart;
          c.b0 = sj.arrive; c.b1 = sj.depart;
          c.at = std::max(si.arrive, sj.arrive);
          if (better(c)) { best = c; }
        }
      }
      // edge 충돌(자리 맞바꿈).
      for (size_t k = 0; k + 1 < routes[i].size(); ++k) {
        const int iu = routes[i][k].v, iv = routes[i][k + 1].v;
        const int i0 = routes[i][k].depart, i1 = routes[i][k + 1].arrive;
        for (size_t m = 0; m + 1 < routes[j].size(); ++m) {
          const int ju = routes[j][m].v, jv = routes[j][m + 1].v;
          if (!(iu == jv && iv == ju)) { continue; }   // 반대 방향이 아니면 통과
          const int j0 = routes[j][m].depart, j1 = routes[j][m + 1].arrive;
          if (!overlaps(i0 - clearance, i1 + clearance, j0, j1)) { continue; }
          TimedConflict c;
          c.exists = true;
          c.a = static_cast<int>(i);
          c.b = static_cast<int>(j);
          c.is_edge = true;
          c.u = iu; c.v = iv;
          c.a0 = i0; c.a1 = i1;
          c.b0 = j0; c.b1 = j1;
          c.at = std::max(i0, j0);
          if (better(c)) { best = c; }
        }
      }
    }
  }
  return best;
}

int route_cost(const Route & r)
{
  return r.empty() ? 0 : r.back().arrive;
}

}  // namespace

int vertex_at(const Route & r, int t)
{
  for (const auto & s : r) {
    if (t >= s.arrive && t <= s.depart) { return s.v; }
  }
  return -1;
}

int ticks_for(double dist_m, double speed_mps, double tick_seconds)
{
  if (speed_mps <= 0.0 || tick_seconds <= 0.0) { return 1; }
  const double t = dist_m / speed_mps / tick_seconds;
  const int ticks = static_cast<int>(std::ceil(t));
  return ticks < 1 ? 1 : ticks;
}

std::string route_to_string(const Route & r)
{
  std::string s;
  char buf[64];
  for (size_t i = 0; i < r.size(); ++i) {
    const int dep = r[i].depart >= kNeverEnds ? -1 : r[i].depart;
    if (dep < 0) {
      std::snprintf(buf, sizeof(buf), "v%d@%d~", r[i].v, r[i].arrive);
    } else {
      std::snprintf(buf, sizeof(buf), "v%d@%d-%d", r[i].v, r[i].arrive, dep);
    }
    if (i) { s += " → "; }
    s += buf;
  }
  return s;
}

// 실제 계획 한 번. 지평선 재시도는 아래 cbs_plan_timed 가 감싼다.
std::vector<Route> cbs_plan_once(
  const TimedGraph & g, const std::vector<int> & starts, const std::vector<int> & goals,
  const PlanOptions & opt)
{
  const int N = static_cast<int>(starts.size());
  if (N == 0 || starts.size() != goals.size()) { return {}; }

  // 지평선. 0 이면 자동.
  //
  // 예전엔 `4 × |V| × 최대간선` 이었다. 단위시간(1틱) 간선일 땐 그럭저럭이었지만, 실물
  // 속도(0.07 m/s)로 한 레인이 10~16틱이 되면서 41정점 맵에서 2600틱을 넘었다. 저수준
  // 상태 수는 |V| × horizon 이라 그대로 십만 단위 탐색이 된다.
  //
  // 실제로 필요한 길이는 "제약 없는 최단 소요" + "남들에게 양보하며 기다릴 여유" 다.
  // 양보는 로봇 수만큼, 한 번에 최대 (가장 비싼 간선 + 여유) 만큼 밀린다고 본다.
  int horizon = opt.horizon;
  if (horizon <= 0) {
    int max_edge = 1;
    for (const auto & row : g) {
      for (const auto & [w, cost] : row) { (void)w; max_edge = std::max(max_edge, cost); }
    }
    int longest = 0;
    for (int i = 0; i < N; ++i) {
      const std::vector<int> h = reverse_dijkstra(g, goals[i]);
      if (starts[i] >= 0 && starts[i] < static_cast<int>(h.size()) && h[starts[i]] != kInf) {
        // ⚠️ 출발 시각을 더한다. 지평선은 **절대 시각**의 상한이라, 늦게 출발하는 로봇의
        //    도착이 그만큼 뒤로 밀린다. 안 더하면 그 로봇만 지평선 밖으로 나가 계획이
        //    실패하고, 편대 전체가 반응형으로 내려간다.
        longest = std::max(longest, h[starts[i]] + start_tick_of(opt, i));
      }
    }
    horizon = 2 * longest + N * (max_edge + opt.clearance + 1) + 8;
  }

  struct CbsNode
  {
    std::vector<TimedConstraints> con;
    std::vector<Route> routes;
    int cost{0};
  };
  struct Cmp { bool operator()(const CbsNode & a, const CbsNode & b) const { return a.cost > b.cost; } };

  CbsNode root;
  root.con.resize(N);
  root.routes.resize(N);
  for (int i = 0; i < N; ++i) {
    root.routes[i] = timed_astar(g, starts[i], goals[i], root.con[i], horizon, opt.max_expansions,
                                start_tick_of(opt, i));
    if (root.routes[i].empty()) { return {}; }
    root.cost += route_cost(root.routes[i]);
  }

  std::priority_queue<CbsNode, std::vector<CbsNode>, Cmp> tree;
  tree.push(root);

  // ── 폴백: 우선순위 계획 ──────────────────────────────────────────────────
  //
  // [2026-08-01] **CBS 가 상한에 걸리는 것은 해가 없어서가 아니다.** 실측으로 확인했다.
  //
  // 복도에서 두 로봇이 정면으로 만나면 **아무리 미뤄도 안 풀린다** — 한쪽이 대피선으로
  // 빠져야만 풀린다. 그런데 CBS 가 만드는 제약은 "그 몇 틱만 피해라" 뿐이라
  // "1틱 더 미룬 안"이 사실상 무한히 생기고, 그 전부가 우회안보다 싸다:
  //
  //     미룬 안   cost 212 → 234 → 238 → 242 → …   (수십만 개)
  //     우회안    cost ~400                        (그 뒤에 줄 서 있음)
  //
  // 최선우선은 400 에 닿기 전에 상한을 소진한다. 정답이 트리 안에 있는데 도달을 못 한다.
  // CBS 의 알려진 약점(corridor symmetry)이고, 정공법은 corridor reasoning(CBSH2-RTC)
  // 이지만 훨씬 크다. 실측(2900+ 케이스)에서 이 폴백으로 충분했다.
  //
  // ⚠️ **제약집합 중복검출은 이 문제의 답이 아니다.** 표준 CBS 위생으로 넣어 봤더니
  //    차단 건수가 **0** 이었다 — 같은 충돌이라도 누적 제약 이력이 달라 서로 다른 노드다.
  //
  // 우선순위 계획은 최적이 아니지만 **항상 끝나고**, 앞 로봇을 움직이는 장애물로 두므로
  // "남이 점유한 곳을 우회" 가 자연히 나온다 — 응대(INTERACTING)로 멈춘 로봇을 피해
  // 가야 한다는 요구와 같은 성질이다.
  //
  // 순서를 여러 개 시도한다. 고정 순서 하나는 해가 있는데도 실패할 수 있다(불완전성).
  auto plan_with_order = [&](const std::vector<int> & order) -> std::vector<Route> {
    std::vector<Route> out(N);
    TimedConstraints acc;                 // 앞 로봇들이 만든 누적 장애물
    for (int idx : order) {
      out[idx] = timed_astar(g, starts[idx], goals[idx], acc, horizon, opt.max_expansions,
                          start_tick_of(opt, idx));
      if (out[idx].empty()) { return {}; }
      for (size_t k = 0; k < out[idx].size(); ++k) {
        const Step & st = out[idx][k];
        // ⚠️ 목표 체류는 depart == kNeverEnds 다. 여유를 더할 때 포화시킨다 —
        //    무한을 평범한 정수처럼 더하면 넘칠 수 있다.
        const int dep = st.depart >= kNeverEnds ? kNeverEnds : st.depart + opt.clearance;
        acc.vertex.push_back({st.v, st.arrive - opt.clearance, dep});
        if (k + 1 < out[idx].size()) {
          // 역방향 통과 금지 — 자리 맞바꿈(swap)을 막는다.
          acc.edge.push_back({out[idx][k + 1].v, st.v,
                              st.depart - opt.clearance,
                              out[idx][k + 1].arrive + opt.clearance});
        }
      }
    }
    return out;
  };

  auto prioritized = [&]() -> std::vector<Route> {
    std::vector<int> order(N);
    for (int i = 0; i < N; ++i) { order[i] = i; }
    if (auto r = plan_with_order(order); !r.empty()) { return r; }

    std::vector<int> rev(order.rbegin(), order.rend());
    if (auto r = plan_with_order(rev); !r.empty()) { return r; }

    // 제약 많은 순 — 자유도가 적은 로봇에게 먼저 자리를 준다.
    std::vector<int> hard = order;
    std::vector<int> len(N, 0);
    for (int i = 0; i < N; ++i) {
      const std::vector<int> h = reverse_dijkstra(g, goals[i]);
      len[i] = (starts[i] >= 0 && starts[i] < static_cast<int>(h.size())) ? h[starts[i]] : 0;
    }
    std::sort(hard.begin(), hard.end(), [&](int a, int b) { return len[a] > len[b]; });
    return plan_with_order(hard);
  };

  int expanded = 0;
  while (!tree.empty()) {
    // 상한 초과 → **폴백으로 간다.** 예전엔 곧장 return 이라 폴백을 건너뛰었다.
    if (++expanded > opt.max_nodes) { return prioritized(); }
    CbsNode cur = tree.top();
    tree.pop();

    const TimedConflict cf = find_conflict(cur.routes, opt.clearance);
    if (!cf.exists) { return cur.routes; }           // 충돌 없는 잎 = 해

    // 충돌한 두 로봇 각각에 제약을 하나 붙여 가지 둘로 분기.
    // "상대가 쓰는 구간(+여유)" 을 나는 쓰지 말라는 형태다.
    for (int side = 0; side < 2; ++side) {
      const int agent = side == 0 ? cf.a : cf.b;
      const int o0 = side == 0 ? cf.b0 : cf.a0;      // 상대 구간
      const int o1 = side == 0 ? cf.b1 : cf.a1;
      CbsNode child = cur;
      if (cf.is_edge) {
        // 내 진행 방향을 상대 통과 구간(+여유) 동안 금지.
        const int u = side == 0 ? cf.u : cf.v;
        const int v = side == 0 ? cf.v : cf.u;
        child.con[agent].edge.push_back({u, v, o0 - opt.clearance, o1 + opt.clearance});
      } else {
        child.con[agent].vertex.push_back({cf.u, o0 - opt.clearance, o1 + opt.clearance});
      }
      child.routes[agent] = timed_astar(g, starts[agent], goals[agent],
                                        child.con[agent], horizon, opt.max_expansions,
                                        start_tick_of(opt, agent));
      if (child.routes[agent].empty()) { continue; }  // 이 가지 막힘
      child.cost = 0;
      for (const auto & r : child.routes) { child.cost += route_cost(r); }
      tree.push(child);
    }
  }
  return prioritized();   // 제약트리 소진 → 폴백(위 주석 참고)
}

// [2026-08-01] **지평선을 실패했을 때만 키운다.**
//
// 자동 지평선은 `2×(자기 최단거리) + …` 인데, 두 로봇이 **인접**하면 그 값이 몇 틱밖에
// 안 돼 비켜서는 우회로를 잘라 버린다. 실측(arte2): 순회경로-3↔순회경로-4 는 직선 4틱이라
// 자동값으로는 실패하고 `horizon=2000` 이면 바로 풀렸다 — 해가 없던 게 아니라 못 보고 있었다.
//
// 그렇다고 처음부터 크게 잡으면 안 된다. 상태 수가 |V| × horizon 이라, 틱이 잘게 쪼개진
// 설정(테스트의 TICK_SEC=0.02)에서 저수준 탐색이 통째로 터진다 — 실제로 테스트가 타임아웃했다.
//
// 그래서 **싼 값으로 먼저 풀고, 실패하면 그때 넓힌다.** 흔한 경우는 예전과 같은 비용이고,
// 어려운 경우만 대가를 치른다. 호출자가 horizon 을 명시했으면 그 뜻을 존중해 재시도하지 않는다.
std::vector<Route> cbs_plan_timed(
  const TimedGraph & g, const std::vector<int> & starts, const std::vector<int> & goals,
  const PlanOptions & opt)
{
  auto r = cbs_plan_once(g, starts, goals, opt);
  if (!r.empty() || opt.horizon > 0) { return r; }

  // 그래프 지름(모든 정점쌍 최단거리의 최댓값)을 하한으로 다시. 우회로도 결국
  // 그래프 안의 경로이므로 공간적으로는 이 정도면 덮인다.
  int diameter = 0;
  int max_edge = 1;
  for (int v = 0; v < static_cast<int>(g.size()); ++v) {
    const std::vector<int> h = reverse_dijkstra(g, v);
    for (int d : h) { if (d != kInf && d > diameter) { diameter = d; } }
    for (const auto & e : g[v]) { max_edge = std::max(max_edge, e.second); }
  }
  if (diameter <= 0) { return r; }
  PlanOptions wide = opt;
  // ⚠️ **지름만으로는 모자란다 — 그건 거리를 재지 줄서기를 못 잰다.**
  //    병목 정점 하나를 N 대가 차례로 통과해야 하면 완료 시각은 지름이 아니라 N 에 비례한다
  //    (별 모양 그래프가 극단이다: 지름 2인데 잎이 10개면 중심에서 10번 줄을 선다).
  //    그래서 대기 몫 N*(최대간선+clearance+1) 을 더한다.
  //
  //    ★ 이 항이 없으면 **"넓힌" 재시도가 첫 시도보다 좁아질 수 있다** — 첫 시도 식은
  //      2*longest + N*(...) + 8 이라 N 이 크면 4*지름+16 을 넘는다. 지금 식은 지름이
  //      longest 이상이므로(4D ≥ 2L, 16 > 8) 항상 첫 시도를 포함한다. 그 불변식을 깨지 말 것.
  wide.horizon = 4 * diameter +
    static_cast<int>(starts.size()) * (max_edge + opt.clearance + 1) + 16;
  // ⚠️ 재시도는 **CBS 트리를 다시 돌지 않는다.** max_nodes=1 이면 첫 확장에서 곧장
  //    우선순위 폴백으로 떨어진다 — 이 경우를 실제로 푸는 것이 그쪽이기 때문이다.
  //    넓은 지평선으로 CBS 트리(최대 4000노드 × A* 6만 확장)를 다시 돌리면
  //    틱이 잘게 쪼개진 설정에서 3분씩 걸린다(실측: 테스트 182초).
  //    폴백은 로봇 수만큼의 A* 라 넓은 지평선에서도 싸다.
  wide.max_nodes = 1;
  return cbs_plan_once(g, starts, goals, wide);
}

std::vector<Path> cbs_plan(
  const Graph & g, const std::vector<int> & starts, const std::vector<int> & goals)
{
  TimedGraph tg(g.size());
  for (size_t v = 0; v < g.size(); ++v) {
    for (int w : g[v]) { tg[v].push_back({w, 1}); }
  }
  PlanOptions opt;
  opt.clearance = 0;   // 하위호환 — 예전과 같은 빡빡한 판정
  const std::vector<Route> routes = cbs_plan_timed(tg, starts, goals, opt);
  if (routes.empty()) { return {}; }

  std::vector<Path> out;
  out.reserve(routes.size());
  for (const auto & r : routes) {
    Path p;
    for (size_t i = 0; i < r.size(); ++i) {
      const bool last = (i + 1 == r.size());
      const int end = last ? r[i].arrive : r[i].depart;
      for (int t = r[i].arrive; t <= end; ++t) { p.push_back(r[i].v); }
    }
    out.push_back(std::move(p));
  }
  return out;
}

}  // namespace libi_fleet

// ── self-check ────────────────────────────────────────────────────────────
// 그래프(통과 대기소 3 있는 T자):   0 — 1 — 2
//                                        |
//                                        3
// A:0→2, B:2→0 은 좁은 1 에서 정면충돌(edge). 한쪽이 3 으로 비켜야 풀린다.
#ifndef CBS_NO_MAIN
int main()
{
  using namespace libi_fleet;

  // 1) 무가중 하위호환 경로.
  const Graph g = {{1}, {0, 2, 3}, {1}, {1}};
  const std::vector<Path> sol = cbs_plan(g, {0, 2}, {2, 0});
  assert(!sol.empty() && "해를 찾아야 한다");
  assert(sol.size() == 2);
  bool used_bay = false;
  for (const auto & p : sol) {
    for (int v : p) { if (v == 3) { used_bay = true; } }
  }
  assert(used_bay && "정면충돌 회피에 대기소(3)를 써야 한다");

  // 2) 가중 + 여유. 1→2 가 긴 레인(3틱)이라 타이밍이 달라진다.
  TimedGraph tg = {{{1, 1}}, {{0, 1}, {2, 3}, {3, 1}}, {{1, 3}}, {{1, 1}}};
  PlanOptions opt;
  opt.clearance = 1;
  const std::vector<Route> timed = cbs_plan_timed(tg, {0, 2}, {2, 0}, opt);
  assert(!timed.empty() && "가중 그래프에서도 해를 찾아야 한다");
  for (size_t i = 0; i < timed.size(); ++i) {
    std::printf("robot %zu: %s\n", i, route_to_string(timed[i]).c_str());
  }

  std::printf("OK — 무가중 하위호환 + 가중/여유 계획 확인\n");
  return 0;
}
#endif
