#pragma once
#include <cstddef>
#include <string>
#include <vector>

// FMS 오케스트레이터(fleet_node)의 순수 데이터 모델 — 제어 상수 + 활성 task 상태.
// ROS·플러그인 비의존이라 폴더 이동·단위테스트에 독립적으로 따라다닌다.
namespace libi_fleet
{

// 도착 판정 거리(m) — **맵 축척에 따라 달라져야 해서 파라미터(`arrive_radius`)로 뺐다.**
// 이 값은 실제 건물 크기(수십 m) 기준 기본값이다.
//
// ⚠️ 맵마다 반드시 다음 범위 안에 있어야 한다:
//     하한  nav2 의 xy_goal_tolerance
//           (이보다 작으면 nav2 는 도착했는데 fleet_node 가 인정 안 해 그 노드에서 멈춘다)
//     상한  navgraph 의 **최소 레인 길이**
//           (이보다 크면 다음 노드가 이미 반경 안이라 안 움직이고 통과해 버린다)
//
// 실제로 arte2(1.26m × 2.16m 축소 맵, 최소 레인 0.062m)에서 이 기본값 0.35 를 그대로 쓰다
// 로봇이 가만히 선 채로 fleet_node 만 0.15초마다 경로를 훑고 나갔고, path_request_driver 가
// 매번 nav2 목표를 갈아치워 status=6(ABORTED)로 **출발하자마자 멈추는** 증상이 났다.
constexpr double kArriveDefault = 0.35;

// 경유 노드 선행 통과 반경(m) — `prefetch_radius`. 0 이면 꺼짐(기존 동작).
//
// 경유 노드는 도착 전 **이 거리 안에 들면 지난 것으로 보고** 다음 노드를 미리 예약·발행한다.
// 로봇이 감속해 서기 전에 새 목표를 받아 그대로 이어 달리게 하려는 것이다.
// 마지막 노드에는 적용하지 않는다 — 거기서는 실제로 서야 한다(정밀 정지·완료 판정).
//
// ⚠️ 실효값은 **레인 길이의 절반**으로 한 번 더 깎인다(fleet_node.cpp). 이 값만으로
//    판정하면 반경이 레인보다 커지는 순간(arte2 최소 레인 0.062m) 출발과 동시에 다음
//    노드를 잡아 그 노드를 건너뛰고 코너를 가로지른다.
//
// ⚠️ **arrive_radius 보다 커야 효과가 있다.** 작거나 같으면 도착 판정이 먼저 걸려
//    조용히 꺼진 것과 같아진다. 기본값 0.10 은 arte2(arrive_radius 0.05) 기준이고,
//    실제 건물 축척(arrive_radius 기본 0.35)에서는 이 값이 더 작아 자동으로 꺼진다 —
//    맵을 키우면 arrive_radius 와 함께 이 값도 올려야 한다.
//    지금 켜졌는지 꺼졌는지는 fleet_node 시작 로그에 찍는다.
constexpr double kPrefetchDefault = 0.10;

// 교통 우선순위 인코딩(단일 int): tier(가장 큼) > task 나이(오래된=큼) > 배터리(낮은=큼).
//   tier: 순회=0 · 작업=1 · 충전복귀(CHARGE)=2 · 완전막힘(STUCK, 동적)=3
constexpr int kTierStep = 50000000;      // tier 간 간격 (age 최대치 kSeqMax*kAgeStep 보다 큼)
constexpr int kAgeStep  = 128;           // task 나이 1스텝 (배터리 최대 100 보다 큼)
constexpr int kSeqMax   = 100000;        // 나이 정규화 상한(세션 태스크 수 가정)
constexpr int kStopPrio = 4 * kTierStep; // STOP 장애물(사다리 밖, 항상 최상위)
constexpr int kMaxReroutes = 3;          // 교착 우회 최대 연속 횟수 — 초과 시 우회 포기·escalate(livelock 방지)
constexpr int kStuckTicks  = 100;        // 이동 지시됐는데 무진행이 이 틱(≈15s@150ms) 넘으면 slotcar stuck으로 보고 task 취소
constexpr int kRerouteWaitTicks = 33;    // 일반 WAIT 가 이 틱(≈5s@150ms) 넘으면 우회 재탐색(작업·순회)

struct ActiveTask
{
  std::string id;
  std::string robot;
  std::vector<int> path;   // 정점 인덱스 경로(시작 포함)
  size_t idx{1};           // 현재 향하는 path 인덱스
  bool moving{false};
  bool wait_logged{false};
  bool patrol{false};      // 순회 task: 끝에 도달해도 완료 안 하고 루프 반복
  bool stuck{false};       // 완전 막힘(우회 실패) → 우선순위 top 으로 escalate, 풀리면 원복
  int priority{0};         // (참고용) UI 지정 우선도. 교통 우선순위는 compute_priority 가 계산.
  int start_seq{0};        // 생성 순서(작을수록 오래됨) — 우선순위 나이 tiebreak
  int arm_actions{0};      // 팔 동작 횟수(배터리 소비 추정용)
  int reroutes{0};         // 연속 우회 횟수(노드 도달 시 리셋). 초과 시 우회 포기·escalate → livelock 방지.
  double last_x{0}, last_y{0};   // 직전 틱 위치 — 무진행(stuck) 감지용
  int no_move{0};          // 이동 지시 상태에서 무진행 틱 수
  int wait_ticks{0};       // 일반 WAIT 지속 틱 수(타임드 우회용). 진전 시 0 리셋.
  int resend_tick{0};      // 이동 중 경로 재발행 카운터 — 아래 주석 참고

};

}  // namespace libi_fleet
