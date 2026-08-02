#include "libi_fleet/patrol_cycle.hpp"

#include <cmath>
#include <limits>

namespace libi_fleet
{

namespace
{
// a 를 [0, 2π) 로 정규화.
double norm2pi(double a)
{
  const double tau = 2.0 * M_PI;
  while (a < 0) { a += tau; }
  while (a >= tau) { a -= tau; }
  return a;
}
}  // namespace

std::vector<int> right_hand_boundary_cycle(const Navgraph & g)
{
  const int n = g.size();
  if (n < 3) { return {}; }

  // 시작 노드: y 최대, 동률 시 x 최소.
  int start = 0;
  for (int i = 1; i < n; ++i) {
    const auto & v = g.vertex(i);
    const auto & b = g.vertex(start);
    if (v.y > b.y + 1e-9 || (std::abs(v.y - b.y) <= 1e-9 && v.x < b.x - 1e-9)) {
      start = i;
    }
  }

  std::vector<int> cycle;
  cycle.push_back(start);
  int cur = start;
  // 가상 이전 노드: 시작 진행 방향을 +x 로 두려면, 이전은 시작의 서쪽에 있는 것처럼 취급.
  // 즉 "들어온 방향(back)" = 서쪽(π). 첫 스텝에서 오른쪽 꺾기 우선이 +x 를 고른다.
  double back_angle = M_PI;   // cur 에서 이전 노드를 바라보는 각
  int prev = -1;

  const int max_steps = 4 * n + 4;   // 비정상 그래프 안전장치
  for (int step = 0; step < max_steps; ++step) {
    const auto & c = g.vertex(cur);
    const auto & nb = g.neighbors(cur);

    // back_angle 에서 시계방향으로 가장 먼저 만나는 이웃(=우수법 우회전). prev 는 제외하되
    // 다른 후보가 하나도 없으면(막다른 곳) prev 로 되돌아간다.
    int best = -1;
    double best_rel = std::numeric_limits<double>::infinity();
    int fallback_prev = -1;
    for (int nx : nb) {
      const auto & vn = g.vertex(nx);
      double a = std::atan2(vn.y - c.y, vn.x - c.x);
      double rel = norm2pi(back_angle - a);   // 시계방향 상대각(작을수록 더 오른쪽)
      if (rel < 1e-9) { rel += 2.0 * M_PI; }   // 되돌아감(rel≈0)은 최후순위로
      if (nx == prev) { fallback_prev = nx; continue; }
      if (rel < best_rel) { best_rel = rel; best = nx; }
    }
    if (best < 0) { best = fallback_prev; }   // 막다른 곳
    if (best < 0) { return {}; }              // 고립 노드

    if (best == start) { break; }             // 시작으로 복귀 → 사이클 완성
    cycle.push_back(best);

    // 다음 스텝의 back_angle = best 에서 cur 을 바라보는 각.
    const auto & vb = g.vertex(best);
    back_angle = std::atan2(c.y - vb.y, c.x - vb.x);
    prev = cur;
    cur = best;
  }

  if (cycle.size() < 3) { return {}; }
  return cycle;
}

double signed_area_2x(const Navgraph & g, const std::vector<int> & route)
{
  const size_t n = route.size();
  if (n < 3) { return 0.0; }
  double area2 = 0.0;
  for (size_t i = 0; i < n; ++i) {
    const Vertex & a = g.vertex(route[i]);
    const Vertex & b = g.vertex(route[(i + 1) % n]);
    area2 += a.x * b.y - b.x * a.y;
  }
  return area2;
}

std::size_t patrol_tail_index(const std::vector<int> & path, std::size_t idx, int plan_goal)
{
  for (std::size_t k = idx; k < path.size(); ++k) {
    if (path[k] == plan_goal) { return k + 1; }
  }
  return idx + 2;
}

std::vector<int> patrol_path_from(const Navgraph & g, int snap,
                                  const std::vector<int> & route, int avoid_first)
{
  const std::size_t n = route.size();
  if (n == 0 || snap < 0 || snap >= g.size()) { return {}; }

  // ── 진입점 고르기 ─────────────────────────────────────────────────────────
  // 각 순회 정점까지 Dijkstra 를 돌려 **경로비용**이 최소인 것을 고른다.
  // 도달불가 후보(빈 경로)는 건너뛴다 — path_cost({}) == 0 이라 그냥 두면 최소로 오인한다.
  std::size_t k = 0;
  double bd = 1e18;
  std::vector<int> approach;              // snap → route[k] 의 실제 정점열(양끝 포함)
  for (std::size_t i = 0; i < n; ++i) {
    if (route[i] == snap) {               // 이미 순회 정점 위 — 더 가까울 수 없다
      bd = 0.0; k = i; approach = {snap};
      break;
    }
    const auto p = g.dijkstra(snap, route[i]);
    if (p.empty()) { continue; }
    const double dd = g.path_cost(p);
    if (dd < bd) { bd = dd; k = i; approach = p; }
  }
  if (bd > 1e17) {
    // 전부 도달불가(비정상 그래프) → 직선거리 폴백. 이 경우엔 이을 정점열이 없으므로
    // 예전처럼 한 홉으로 둔다. 그래프가 끊긴 상황이라 어차피 주행이 성립하지 않는다.
    for (std::size_t i = 0; i < n; ++i) {
      const Vertex & v = g.vertex(route[i]);
      const double dd = std::hypot(g.vertex(snap).x - v.x, g.vertex(snap).y - v.y);
      if (dd < bd) { bd = dd; k = i; }
    }
    approach = (route[k] == snap) ? std::vector<int>{snap} : std::vector<int>{snap, route[k]};
  }

  // 진입점의 다음 홉이 피해야 할 정점이면 한 칸 앞에서 시작한다(방향은 유지).
  // 진입점이 바뀌었으니 **진입 구간도 다시 구한다** — 안 구하면 옛 진입점으로 가는
  // 정점열이 새 진입점 앞에 붙어 경로가 끊긴다.
  if (avoid_first >= 0 && route[(k + 1) % n] == avoid_first) {
    k = (k + 1) % n;
    if (route[k] == snap) {
      approach = {snap};
    } else {
      const auto p = g.dijkstra(snap, route[k]);
      approach = p.empty() ? std::vector<int>{snap, route[k]} : p;
    }
  }

  std::vector<int> path;
  // 진입 구간(마지막 = 진입점은 빼고 — 바로 아래 랩이 그 정점부터 시작한다).
  // 로봇이 이미 진입점 위면 approach == {snap} 이라 아무것도 안 붙고, 랩이 곧장 시작한다.
  for (std::size_t i = 0; i + 1 < approach.size(); ++i) { path.push_back(approach[i]); }
  for (std::size_t i = 0; i < n; ++i) { path.push_back(route[(k + i) % n]); }
  path.push_back(route[k]);   // 루프 닫기(마지막 == 처음)
  return path;
}

}  // namespace libi_fleet
