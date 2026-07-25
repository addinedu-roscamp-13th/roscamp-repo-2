// cbs_planner.cpp — CBS + Space-Time A* 다중로봇 경로계획 (초안, 독립 실행).
// 근거: doc "03 교통관제 알고리즘 — CBS + Space-Time A*". 출발 전 전 로봇 경로를 미리
// 계산해 충돌 0(vertex/edge)으로 동시 출발.
//
// 상태: 아직 TrafficBase 플러그인 아님. fleet_node 의 교통 인터페이스는 노드단위 반응형
//   (request_move 한 칸씩)이라 배치 계획을 넘길 통로가 없다. 이 파일은 알고리즘 코어 +
//   self-check 만 담는다. 배선(fleet_node 가 전 로봇 start/goal 을 plan() 에 넘기고,
//   받은 타임라인을 노드별 GRANT 판정으로 되먹임)은 다음 단계.
//
// 빌드/검증:  g++ -std=c++17 cbs_planner.cpp -o cbs && ./cbs
// ponytail: 단위시간 간선(모든 이동/대기 = 1틱). 간선 길이 편차가 크면 가중 space-time 으로 승급.
// ponytail: CBS 고수준은 최선우선(비용순)이되 제약트리 크기 상한 없음 — 로봇 수 많아지면 캡 필요.

#include <cassert>
#include <cstdio>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <tuple>
#include <vector>

namespace libi_fleet {

using Graph = std::vector<std::vector<int>>;  // adj[v] = 인접 정점들
using Path  = std::vector<int>;               // path[t] = 시각 t 의 정점

// 한 로봇에 걸리는 제약. vertex: t 에 v 금지. edge: t 에 u→v 진입 금지(t-1 에 u, t 에 v).
struct Constraints {
  std::set<std::pair<int, int>> vertex;          // (v, t)
  std::set<std::tuple<int, int, int>> edge;      // (u, v, t)
};

// 시각 t 의 로봇 위치. 경로 끝을 넘어가면 목표에 머문다(도착 후 점유 유지).
static int loc_at(const Path& p, int t) {
  return t < static_cast<int>(p.size()) ? p[t] : p.back();
}

// 목표까지 홉 거리(BFS). space-time A* 의 admissible 휴리스틱(이동 1틱 이상).
static std::vector<int> bfs_dist(const Graph& g, int goal) {
  std::vector<int> d(g.size(), std::numeric_limits<int>::max());
  std::queue<int> q;
  d[goal] = 0;
  q.push(goal);
  while (!q.empty()) {
    int v = q.front();
    q.pop();
    for (int w : g[v]) {
      if (d[w] == std::numeric_limits<int>::max()) {
        d[w] = d[v] + 1;
        q.push(w);
      }
    }
  }
  return d;
}

// Space-Time A*: 제약을 지키며 start→goal 의 시각별 경로. 실패 시 빈 경로.
// max_goal_t: 이 시각 이후 goal 에 걸린 vertex 제약이 없어야 도착 인정(다른 로봇 통과 대기).
static Path space_time_astar(const Graph& g, int start, int goal,
                             const Constraints& c, int horizon) {
  const std::vector<int> h = bfs_dist(g, goal);
  if (h[start] == std::numeric_limits<int>::max()) return {};  // 도달 불가 그래프

  // goal 에 걸린 마지막 제약 시각 — 그 전에 도착해 앉아 있으면 안 된다.
  int max_goal_t = 0;
  for (const auto& [v, t] : c.vertex)
    if (v == goal) max_goal_t = std::max(max_goal_t, t);

  struct Node { int v, t, f; };
  struct Cmp { bool operator()(const Node& a, const Node& b) const { return a.f > b.f; } };
  std::priority_queue<Node, std::vector<Node>, Cmp> open;
  std::map<std::pair<int, int>, int> came_g;                 // (v,t) → g(=t)
  std::map<std::pair<int, int>, std::pair<int, int>> parent; // (v,t) → 이전 (v,t)

  open.push({start, 0, h[start]});
  came_g[{start, 0}] = 0;

  while (!open.empty()) {
    Node n = open.top();
    open.pop();
    if (n.v == goal && n.t >= max_goal_t) {  // 재구성
      Path p;
      std::pair<int, int> cur{n.v, n.t};
      while (true) {
        p.push_back(cur.first);
        auto it = parent.find(cur);
        if (it == parent.end()) break;
        cur = it->second;
      }
      return Path(p.rbegin(), p.rend());
    }
    if (n.t >= horizon) continue;  // 지평선 초과 — 이 가지 버림

    // 이웃 + 제자리 대기(wait 도 경로의 일부).
    std::vector<int> moves = g[n.v];
    moves.push_back(n.v);
    for (int w : moves) {
      int nt = n.t + 1;
      if (c.vertex.count({w, nt})) continue;                 // vertex 제약
      if (c.edge.count({n.v, w, nt})) continue;              // edge 제약
      auto key = std::make_pair(w, nt);
      if (came_g.count(key)) continue;                       // 이미 더 이르게 도달(g=t 단조)
      came_g[key] = nt;
      parent[key] = {n.v, n.t};
      if (h[w] == std::numeric_limits<int>::max()) continue; // 목표서 끊긴 정점
      open.push({w, nt, nt + h[w]});
    }
  }
  return {};
}

struct Conflict { bool exists = false; int a, b, u, v, t; bool is_edge; };

// 두 경로의 첫 충돌(시각 오름차순). vertex: 같은 t·같은 정점. edge: 서로 자리 맞바꿈.
static Conflict find_conflict(const std::vector<Path>& paths) {
  int T = 0;
  for (const auto& p : paths) T = std::max(T, static_cast<int>(p.size()));
  for (int t = 0; t < T; ++t) {
    for (size_t i = 0; i < paths.size(); ++i) {
      for (size_t j = i + 1; j < paths.size(); ++j) {
        int ai = loc_at(paths[i], t), aj = loc_at(paths[j], t);
        if (ai == aj) return {true, (int)i, (int)j, ai, ai, t, false};   // vertex
        if (t > 0) {
          int pi = loc_at(paths[i], t - 1), pj = loc_at(paths[j], t - 1);
          if (pi == aj && pj == ai)  // i: pi→ai, j: pj→aj, 자리 교환
            return {true, (int)i, (int)j, pi, ai, t, true};              // edge
        }
      }
    }
  }
  return {};
}

// CBS 고수준: 제약트리를 비용순 최선우선 탐색. 충돌 없는 잎이 해.
// 실패(해 없음/지평선 초과) 시 빈 벡터.
std::vector<Path> cbs_plan(const Graph& g, const std::vector<int>& starts,
                           const std::vector<int>& goals) {
  const int N = static_cast<int>(starts.size());
  const int horizon = 4 * static_cast<int>(g.size()) + 8;  // ponytail: 넉넉한 상한

  struct CbsNode {
    std::vector<Constraints> con;
    std::vector<Path> paths;
    int cost;
  };
  auto plan_all = [&](CbsNode& node) -> bool {
    node.cost = 0;
    for (int i = 0; i < N; ++i) {
      node.paths[i] = space_time_astar(g, starts[i], goals[i], node.con[i], horizon);
      if (node.paths[i].empty()) return false;
      node.cost += static_cast<int>(node.paths[i].size());
    }
    return true;
  };

  struct Cmp { bool operator()(const CbsNode& a, const CbsNode& b) const { return a.cost > b.cost; } };
  std::priority_queue<CbsNode, std::vector<CbsNode>, Cmp> tree;

  CbsNode root{std::vector<Constraints>(N), std::vector<Path>(N), 0};
  if (!plan_all(root)) return {};
  tree.push(root);

  while (!tree.empty()) {
    CbsNode cur = tree.top();
    tree.pop();
    Conflict cf = find_conflict(cur.paths);
    if (!cf.exists) return cur.paths;  // 충돌 없는 잎 = 해

    // 충돌한 두 로봇 각각에 제약을 하나 붙여 가지 둘로 분기.
    for (int side = 0; side < 2; ++side) {
      int agent = side == 0 ? cf.a : cf.b;
      CbsNode child = cur;
      if (cf.is_edge) {
        // edge 충돌: agent 쪽 진행 방향 u→v(또는 v→u)를 시각 t 에 금지.
        int u = side == 0 ? cf.u : cf.v;
        int v = side == 0 ? cf.v : cf.u;
        child.con[agent].edge.insert({u, v, cf.t});
      } else {
        child.con[agent].vertex.insert({cf.u, cf.t});  // vertex 충돌: 그 시각 그 정점 금지
      }
      // 해당 로봇만 재계획.
      child.paths[agent] = space_time_astar(g, starts[agent], goals[agent],
                                            child.con[agent], horizon);
      if (child.paths[agent].empty()) continue;  // 이 가지 막힘
      child.cost = 0;
      for (const auto& p : child.paths) child.cost += static_cast<int>(p.size());
      tree.push(child);
    }
  }
  return {};  // 제약트리 소진 — 해 없음
}

}  // namespace libi_fleet

// ── self-check ────────────────────────────────────────────────────────────
// 그래프(통과 대기소 3 있는 T자):   0 — 1 — 2
//                                        |
//                                        3
// A:0→2, B:2→0 은 좁은 1 에서 정면충돌(edge). 한쪽이 3 으로 비켜야 풀린다.
#ifndef CBS_NO_MAIN
int main() {
  using namespace libi_fleet;
  Graph g = {{1}, {0, 2, 3}, {1}, {1}};
  std::vector<int> starts = {0, 2};
  std::vector<int> goals  = {2, 0};

  std::vector<Path> sol = cbs_plan(g, starts, goals);
  assert(!sol.empty() && "해를 찾아야 한다");
  assert(sol.size() == 2);

  // 시작/목표 일치.
  for (int i = 0; i < 2; ++i) {
    assert(sol[i].front() == starts[i]);
    assert(sol[i].back() == goals[i]);
  }
  // 충돌 0 확인.
  Conflict cf = find_conflict(sol);
  assert(!cf.exists && "해에 vertex/edge 충돌이 없어야 한다");

  // 좁은 그래프라 한쪽은 반드시 3 을 경유(비켜서기)해야 함.
  bool used_bay = false;
  for (const auto& p : sol)
    for (int v : p) if (v == 3) used_bay = true;
  assert(used_bay && "정면충돌 회피에 대기소(3)를 써야 한다");

  for (int i = 0; i < 2; ++i) {
    std::printf("robot %d:", i);
    for (int v : sol[i]) std::printf(" %d", v);
    std::printf("\n");
  }
  std::printf("OK — 충돌 0, 대기소 경유 확인\n");
  return 0;
}
#endif
