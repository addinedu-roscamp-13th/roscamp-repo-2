#pragma once
// CBS + 가중 Space-Time A* 다중로봇 경로계획 — 공개 인터페이스.
//
// 원래 planning/cbs_planner.cpp 안에만 있던 타입을 밖으로 뺀다. 플러그인(cbs_traffic)이
// 링크해서 써야 하는데, .cpp 안의 static 함수와 전방선언만으로는 붙일 수 없기 때문이다.
//
// ## 왜 "가중"인가 — 단위시간 간선을 버린 이유
// 예전 코어는 모든 이동·대기가 1틱이었다. arte2 는 최소 레인 0.062 m 인데 긴 레인은 그
// 몇 배다. 1틱을 실제 시간으로 해석하는 순간, 짧은 레인은 기어가야 하고 긴 레인은 못 지킨다.
// 그래서 간선마다 **실제 주행 소요를 틱 수로** 넣는다(`ticks_for()`). 이제 틱은 진짜 시간이다.
//
// ## 여유(clearance)를 계획에 넣는 이유
// 실제 로봇은 계획대로 정확히 도착하지 않는다(가감속·회피·리로컬라이제이션). 그래서
// 점유 구간을 비교할 때 **양쪽에 clearance 틱을 덧대서** 겹침을 판정한다. 계획 단계에서
// 미리 벌려 두면, 실행이 조금 밀려도 계획의 무충돌 성질이 유지된다.
// clearance=0 이면 예전과 같은 빡빡한 판정이다.

#include <cstdint>
#include <limits>
#include <string>
#include <vector>

namespace libi_fleet
{

// 가중 그래프. adj[u] = {(v, 이동 소요 틱)}. 틱은 1 이상.
using TimedGraph = std::vector<std::vector<std::pair<int, int>>>;

// 무가중 그래프(하위호환 경로). adj[v] = 인접 정점들.
using Graph = std::vector<std::vector<int>>;
// 무가중 결과. path[t] = 시각 t 의 정점.
using Path = std::vector<int>;

// "끝나지 않음" 을 나타내는 시각. 목표 도착 후 그 자리에 머무는 구간의 끝으로 쓴다.
constexpr int kNeverEnds = std::numeric_limits<int>::max() / 4;

// 정점 체류 한 칸. [arrive, depart] 동안 v 를 점유하고, depart 에 다음 간선으로 떠난다.
// 마지막 칸은 depart == kNeverEnds (목표에 계속 앉아 있음).
struct Step
{
  int v{0};
  int arrive{0};
  int depart{0};
};

// 로봇 한 대의 계획.
using Route = std::vector<Step>;

struct PlanOptions
{
  // 계획 지평선(틱). 0 이면 그래프 크기에서 자동으로 잡는다.
  int horizon{0};
  // 로봇 간 최소 여유 틱. 실행 드리프트 흡수용. 0 이면 빡빡한 판정.
  int clearance{1};
  // CBS 고수준 제약트리 확장 상한. 넘으면 실패로 본다(무한 탐색 방지).
  int max_nodes{4000};
  // 저수준(Space-Time A*) 한 번의 확장 상한.
  //
  // 고수준만 막아서는 부족하다. 가중 간선이 커지면(실물 0.07 m/s 에서 한 레인이 10~16틱)
  // 지평선이 수천 틱이 되고, 상태 수는 |V| × horizon 이라 **한 번의 저수준 탐색만으로도**
  // 십만 단위가 된다. 관제가 그 자리에서 멈추느니 계획을 포기하고 반응형으로 도는 편이 낫다.
  int max_expansions{60000};
};

// 전 로봇 동시 계획. 실패(해 없음/상한 초과) 시 빈 벡터.
// starts/goals 는 정점 인덱스. 반환 순서는 입력 순서와 같다.
std::vector<Route> cbs_plan_timed(
  const TimedGraph & g, const std::vector<int> & starts, const std::vector<int> & goals,
  const PlanOptions & opt = PlanOptions{});

// 하위호환: 모든 간선 1틱, clearance 0. 결과를 path[t] 형태로 펴서 돌려준다.
std::vector<Path> cbs_plan(
  const Graph & g, const std::vector<int> & starts, const std::vector<int> & goals);

// 틱 t 에 이 계획이 있는 정점. 간선 위(이동 중)면 -1.
// 실행 게이트와 애니메이션 뷰어가 "계획상 지금 어디" 를 묻는 데 쓴다.
int vertex_at(const Route & r, int t);

// 거리(m) → 틱 수. 최소 1틱. 플러그인/노드가 navgraph 레인 길이를 틱으로 바꿀 때 쓴다.
//   speed_mps    : 로봇 순항 속도
//   tick_seconds : 틱 하나의 실제 길이
int ticks_for(double dist_m, double speed_mps, double tick_seconds);

// Route 를 사람이 읽을 수 있는 한 줄로. 로그·디버그용.
std::string route_to_string(const Route & r);

}  // namespace libi_fleet
