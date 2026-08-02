#pragma once
#include <string>
#include <utility>
#include <vector>

#include "libi_fleet/navgraph.hpp"

namespace libi_fleet
{

enum class MoveDecision { GRANT, WAIT, DEADLOCK };

// ── 배치 계획형 교통(CBS 등)을 위한 입출력 ─────────────────────────────────
//
// 반응형 플러그인(ReservationDeadlock)은 "이번 한 칸을 들어가도 되나"만 답하면 된다.
// 배치 계획형은 그것만으로 부족하다 — **경로 자체를 다시 고를 수 있어야** 하기 때문이다.
// 정면충돌을 대기소로 비켜서 푸는 것이 바로 경로 변경이고, 그러려면
//   ① 우회할 그래프를 받아야 하고
//   ② 새 경로를 호출자에게 **돌려줄** 통로가 있어야 한다.
// 그래서 replan() 은 void 가 아니라 경로를 반환한다.
//
// 기본 구현은 "나는 계획하지 않는다"(빈 결과)라, 기존 두 플러그인은 한 줄도 안 바뀐다.

struct PlanRequest
{
  std::string robot;
  int start{-1};      // 관측상 **확정된** 현재 정점 (계획상 위치가 아니다)
  int goal{-1};
  int priority{0};
  // 지금 **가고 있는 중**이면 그 간선의 출발 정점. 아니면 -1.
  //
  // `start` 는 이동 중일 때 **목적지 정점**이다(위 `pr.start` 계산 참고). 그래서 로봇이
  // 그 정점에 도달하는 데 아직 시간이 남아 있는데, 계획은 그 정점을 t=0 으로 잡는다.
  // 그 어긋남 때문에 `fleet_node` 는 커밋 노드의 마감을 아예 **버려 왔고**(센티널 -1),
  // 그 결과 커밋 구간에서 늦는 로봇을 아무도 못 잡았다(sim 실측 2026-08-02:
  // 25초 지연을 먹인 로봇의 마감 초과 경고가 **0건**).
  //
  // 이 값을 실어 보내면 플러그인이 **자기 시간 모델 그대로** 그 간선의 예산을 계산해
  // `PlannedRoute::commit_deadline_tick` 으로 돌려줄 수 있다. `fleet_node` 가 거리·속도로
  // 직접 계산하지 않는 것이 요점이다 — 그러면 시간 모델이 두 곳으로 갈린다.
  int committed_from{-1};
};

// 반복해서 계획 마감을 못 지킨 간선. **막지 않고 비싸게 만든다.**
//
// ⚠️ 막으면(blocked) 그래프가 끊길 수 있다 — 순회 루프는 고리 하나라 간선 한 줄만
//    빼도 반대편으로 돌아가는 길이 없어지고, 그러면 시간표가 통째로 실패한다
//    ("시간표를 세우지 못했습니다" → 편대 전체가 반응형으로 내려간다).
//    비용만 올리면 **대안이 있을 때만** 갈아타고, 없으면 그 길로 계속 간다.
//    "이 길이 실제로 느리다"는 관측을 모델에 되먹이는 것이지 통행 금지가 아니다.
struct SlowEdge
{
  int from{-1};
  int to{-1};
  int extra_ticks{0};   // 이 간선 통과 비용에 더할 틱
};

struct PlanSnapshot
{
  const Navgraph * graph{nullptr};
  std::vector<PlanRequest> robots;
  // 계획에 참여하지 않지만 자리를 차지하는 정점(수동 정지·오프라인 로봇 등).
  // 계획은 이 정점들을 영구 장애물로 보고 피해 간다.
  std::vector<int> blocked;
  // 마감을 반복해서 못 지킨 간선(방향 있음). 비었으면 예전과 완전히 같은 동작이다.
  std::vector<SlowEdge> slow_edges;
};

struct PlannedRoute
{
  std::string robot;
  std::vector<int> path;          // 정점 인덱스 경로(시작·끝 포함)
  std::vector<int> arrive_tick;   // path 와 같은 길이. 계획상 각 정점 도착 틱
  // 커밋 노드(= `path[0]`, 지금 가고 있는 정점)에 **닿기까지 줄 예산**(틱).
  // `PlanRequest::committed_from` 이 주어졌을 때만 채운다. -1 이면 "모른다".
  //
  // ⚠️ 이건 `arrive_tick[0]`(항상 0)과 **다른 뜻**이다. 계획은 커밋 노드를 t=0 으로
  //    잡지만 로봇은 아직 가는 중이라, 0 을 마감으로 쓰면 계획이 세워지자마자 초과가
  //    되어 재계획이 3초마다 돈다(fleet_node.cpp 의 실측 기록).
  //    그렇다고 마감을 버리면 그 구간이 무감지가 된다. 그 사이 값이 이것이다.
  int commit_deadline_tick{-1};
};

// 교통협상 전략 인터페이스(pluginlib base). navgraph 노드 진입 허가/대기.
// 전체 로봇을 한 인스턴스가 본다(공유 1개).
class TrafficBase
{
public:
  virtual ~TrafficBase() = default;
  // robot 이 from_node→to_node 이동 요청. from==to 는 현재 노드 점유(claim).
  // priority: 로봇의 현재 우선순위(높을수록 우선). 교착 시 최저 우선순위가 양보.
  //  GRANT   : 목표 노드 예약 성공.
  //  WAIT    : 점유 중 → 양보 대기(우선순위 높아 직진 대기 포함).
  //  DEADLOCK: 대기 사이클(교착)에서 이 로봇이 최저 우선순위 → 호출측이 우회(재경로).
  // ⚠️ [2026-08-01] 예전 주석은 "노드예약만으로 정면·후미충돌이 다 막힌다"였다.
  //    **그 전제는 지도에 달려 있고, arte2 에서는 깨져 있다.** 아래 set_min_separation 참고.
  virtual MoveDecision request_move(const std::string & robot, int from_node, int to_node,
                                    int priority) = 0;
  // robot 이 node 점유 해제. 취소/정지용.
  virtual void release(const std::string & robot, int node) = 0;
  // 출발/도착 순간: 떠나는 노드 해제.
  virtual void release_node(const std::string & robot, int node) = 0;
  // 현재 점유 중인 (노드, 로봇) 목록 — 시각화용.
  virtual std::vector<std::pair<int, std::string>> occupancy() const = 0;

  // 이 플러그인이 경로를 직접 만드나. false 면 호출자는 snapshot 조립을 건너뛴다.
  virtual bool plans_routes() const { return false; }
  // 전 로봇 동시 재계획. 빈 결과면 "계획 없음" — 호출자는 자기 경로를 그대로 쓴다.
  virtual std::vector<PlannedRoute> replan(const PlanSnapshot &) { return {}; }

  // 시간표가 못 쓰게 됐으니 다시 짜 달라. 호출자(fleet_node)가 주기적으로 물어본다.
  //
  // 이게 없으면 계획형 플러그인은 한 번 밀린 뒤 **영영 반응형으로 남는다**. 장애물·지체로
  // 늦는 것은 정상 운영에서 늘 일어나므로, 그때마다 계획으로 되돌아올 길이 있어야 한다.
  virtual bool needs_replan() const { return false; }
  // 계획 도착틱을 실제 초로 바꿀 때 쓰는 환산값. 이 값의 주인은 플러그인이다.
  virtual double tick_seconds() const { return 1.0; }
  // 계획 대비 **몇 틱까지 늦어도 봐주는가**. 화면이 "지연" 과 "도착 중" 을 가르는 데 쓴다.
  // 이 값을 안 주면 화면이 자기 나름의 기준을 지어내고, 1~2초 늦은 정상 주행을
  // 빨간 "지연" 으로 표시해 없는 문제를 만든다.
  virtual int drift_limit() const { return 0; }
  // 마지막으로 계획을 버린 이유(사람이 읽는 문구). 재계획이 잦을 때 원인을 가른다.
  virtual std::string last_demote_reason() const { return {}; }

  // ── [2026-08-01] 근접 정점 상호배제 ──────────────────────────────────────
  //
  // **노드 예약은 "서로 다른 정점 = 안전"을 전제한다. arte2 에서 그 전제가 깨져 있다.**
  //
  //   로봇 반경 0.088 m (nav2 inscribed) → 두 대는 0.176 m 이상 떨어져야 한다
  //   그런데 arte2 는 순회 통로(x=0.601)와 서비스 지점(x=0.752)이 **0.151 m** 간격이다:
  //       순회경로-1 ↔ 화장실 · 순회경로-2 ↔ 미술작품
  //       순회경로-3 ↔ 수거함 · 충전소입구 ↔ 미정
  //
  // 서로 다른 정점을 점유하는 것만으로 **물리적으로 겹친다.** sim 실측에서 CBS·반응형
  // 모두 최소 0.081~0.084 m 까지 붙었다(관제를 끄면 0.002 m — 관제 자체는 일하고 있다).
  // 정점 간격이 넉넉한 지도(new_map, 최단 1.8 m)에서는 위반이 0 이라 지도 문제임이 확인된다.
  //
  // 지도를 고치는 것이 근본이지만 그건 로봇이 실제로 서는 자리를 바꾸는 일이라 현장
  // 확인이 필요하다. 그때까지 **예약 계층에서** 막는다: 어떤 정점을 잡으면 그보다
  // 가까운 정점들도 같이 잠긴다.
  //
  // 0 이하를 주면 규칙이 꺼진다(예전 동작).
  virtual void set_min_separation(const Navgraph & g, double min_sep)
  {
    too_close_.assign(g.size(), {});
    if (min_sep <= 0.0) { return; }
    for (int i = 0; i < g.size(); ++i) {
      for (int j = i + 1; j < g.size(); ++j) {
        const Vertex & a = g.vertex(i);
        const Vertex & b = g.vertex(j);
        if (std::hypot(a.x - b.x, a.y - b.y) < min_sep) {
          too_close_[i].push_back(j);
          too_close_[j].push_back(i);
        }
      }
    }
  }

  // ⚠️ **노드 충돌만 본다. 간선을 지나는 동안 쓸고 가는 통로는 보호하지 않는다.**
  //    두 레인의 중심선이 2R 보다 가까우면 이 규칙을 통과해도 물리적으로 겹친다
  //    (arte2 에 그런 레인 조합이 13쌍 있다). 그건 지도를 고쳐야 하는 문제다.
  // node 와 물리적으로 겹치는(=동시 점유 불가) 이웃들. 설정 전이면 빈 목록.
  const std::vector<int> & too_close_to(int node) const
  {
    static const std::vector<int> kNone;
    return (node >= 0 && node < static_cast<int>(too_close_.size())) ? too_close_[node] : kNone;
  }

protected:
  std::vector<std::vector<int>> too_close_;
};

}  // namespace libi_fleet
