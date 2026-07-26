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
};

struct PlanSnapshot
{
  const Navgraph * graph{nullptr};
  std::vector<PlanRequest> robots;
  // 계획에 참여하지 않지만 자리를 차지하는 정점(수동 정지·오프라인 로봇 등).
  // 계획은 이 정점들을 영구 장애물로 보고 피해 간다.
  std::vector<int> blocked;
};

struct PlannedRoute
{
  std::string robot;
  std::vector<int> path;          // 정점 인덱스 경로(시작·끝 포함)
  std::vector<int> arrive_tick;   // path 와 같은 길이. 계획상 각 정점 도착 틱
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
  // ※ 노드예약만으로 정면충돌·후미추돌이 다 막히므로 엣지예약은 두지 않는다.
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
};

}  // namespace libi_fleet
