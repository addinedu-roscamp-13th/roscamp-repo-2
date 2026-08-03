#pragma once
#include <algorithm>
#include <cctype>
#include <cstddef>
#include <string>
#include <vector>

// FMS 오케스트레이터(fleet_node)의 순수 데이터 모델 — 제어 상수 + 활성 task 상태.
// ROS·플러그인 비의존이라 폴더 이동·단위테스트에 독립적으로 따라다닌다.
namespace libi_fleet
{

// 로봇 이름 표기 차이를 지운 **비교용 키**. `pinky-3` 과 `pinky3` 이 같아진다.
//
// ⚠️ [2026-08-03] 이게 없어서 **실기에서 사람 차단 후퇴가 통째로 죽어 있었다.**
//    `fleet_node` 가 아는 이름은 `RmfRobotState.name`(=`pinky-3`)인데
//    `/fms/node_block` 의 `robot` 은 브릿지 키(`pinky3`)로 온다
//    (`fleet_dispatch_bridge.py:1300` → `on_person_blocked` → `_publish_node_block`).
//    `t.robot != m->robot` 이 한 번도 안 맞아 `moving=false` 가 안 내려갔고,
//    1초짜리 목표 재전송이 **사람 쪽으로 계속** 나갔다. 화면엔 새 경로가 그려지는데
//    (`publish_routes` 는 매 틱 무조건 낸다) 로봇은 서 있었다.
//    실기 로그: `[block] 정점 8 차단 60s ... 로봇=pinky3` vs `[P-pinky-3] pinky-3`.
//
// ⚠️ 같은 종류를 오늘 오전에 한 번 고쳤다(`fleet_dispatch_bridge.py:498` `_rkey`).
//    그때 `_nav_goal` 만 고치고 이 필드는 남겼다 — **받는 자리에서 흡수**해야
//    발행자가 무엇을 보내든 안 깨진다.
inline std::string norm_robot_name(const std::string & s)
{
  std::string o;
  for (unsigned char c : s) {
    if (std::isalnum(c)) { o += static_cast<char>(std::tolower(c)); }
  }
  return o;
}

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
  //: `pr.start == pr.goal` 로 이번 라운드 계획에서 빠졌나.
  //
  //  ⚠️ [2026-08-03] **재계획 루프를 막는 표시다**(codex 3차 P1).
  //     배달 로봇이 최종 정점으로 향하는 중이면 `start == goal` 이라 매 재계획에서
  //     빠진다 — 드문 경우가 아니라 **모든 배달의 마지막 구간**이다. 그때 낡은
  //     `plan_epoch`/`plan_arrive` 가 남아 있는데 이미 마감을 넘겼으면,
  //     `check_plan_deadline` 이 매 틱 재계획을 요청하고 → 또 제외되고 → 또 요청한다.
  //     기본 `replan_cooldown_ticks = 0` 이라 backoff 도 안 걸려 150ms 마다 CBS 가 돈다.
  //
  //     그 로봇에게 재계획은 **정의상 무의미하다** — 짤 것이 없다(start == goal).
  //     그래서 이 표시가 서 있는 동안은 그 task 의 마감으로 재계획을 요청하지 않는다.
  //     다른 로봇의 마감은 그대로 감시하므로 "0이면 재계획" 보장은 유지된다.
  //
  //  계획이 실제로 이 task 에 적용되거나(`apply_routes`) 노드에 도달하면 내려간다 —
  //  둘 다 "상황이 바뀌었다" 는 뜻이라 다시 볼 이유가 생긴 것이다.
  bool plan_excluded{false};
  //: 위 제외를 이미 로그로 남겼나. 매 재계획마다 찍으면 로그가 그것만으로 찬다.
  bool excluded_logged{false};
  bool patrol{false};      // 순회 task(주간/야간 공통): 끝에 도달해도 완료 안 하고 루프 반복
  bool security{false};    // 야간 보안순회면 true → security_patrol_route_ 사용(그 외 patrol_route_)
  bool stuck{false};       // 완전 막힘(우회 실패) → 우선순위 top 으로 escalate, 풀리면 원복
  int priority{0};         // (참고용) UI 지정 우선도. 교통 우선순위는 compute_priority 가 계산.
  int start_seq{0};        // 생성 순서(작을수록 오래됨) — 우선순위 나이 tiebreak
  int arm_actions{0};      // 팔 동작 횟수(배터리 소비 추정용)
  int reroutes{0};         // 연속 우회 횟수(노드 도달 시 리셋). 초과 시 우회 포기·escalate → livelock 방지.
  double last_x{0}, last_y{0};   // 직전 틱 위치 — 무진행(stuck) 감지용
  int no_move{0};          // 이동 지시 상태에서 무진행 틱 수
  int wait_ticks{0};       // 일반 WAIT 지속 틱 수(타임드 우회용). 진전 시 0 리셋.
  int resend_tick{0};      // 이동 중 경로 재발행 카운터 — 아래 주석 참고

  // ── 시간 계획(CBS) 전용. 반응형 교통에서는 비어 있다 ──────────────────────
  // path 와 같은 길이. 계획상 각 정점에 도착하기로 한 틱.
  // 이게 있어야 "언제까지 못 가면 계획이 깨진 것" 을 판정할 수 있다 — 실제 도착이 이 시각을
  // 넘기면(장애물·지체) 시간표는 이미 남의 통과를 잘못 열어 주고 있다.
  std::vector<int> plan_arrive;
  // 순회 전용: CBS 가 실제로 계획해 준 구간의 **끝 인덱스**(path 기준).
  //
  // 순회는 랩 전체를 계획하지 않는다 — 목표가 랩 끝이면 그 정점이 kNeverEnds 로 영구
  // 점유돼 주 통로가 통째로 막힌다. 그래서 **다음 한 정점만** 계획하고, 그 뒤는 canonical
  // 랩을 그대로 이어 붙인다. 로봇이 이 인덱스를 넘어가기 전에 재계획을 걸어야 한다 —
  // 안 걸면 실행 게이트가 "계획에 없는 칸" 으로 보고 강등한다(그게 바로 없애려던 churn 이다).
  // -1 이면 순회가 아니거나 계획 구간이 없다는 뜻.
  int plan_end_idx{-1};
  // 이 task 에서 **마감을 이미 놓친 것으로 센** 간선. `{-1,-1}` 이면 없다.
  // `check_plan_deadline` 은 매 틱 도는데, 그때마다 세면 한 번 늦은 간선이 초당 6~7번씩
  // 벌점을 먹어 문턱을 즉시 넘긴다. **한 번 지나는 동안 한 번만** 센다.
  //
  // ⚠️ [2026-08-02] **예전엔 path 인덱스였다. 재계획이 그 동일성을 깨뜨린다.**
  //    재계획이 적용되면 `t.idx` 가 1 로 되감기는데 이 값은 옛 인덱스를 들고 있다.
  //    그래서 두 방향으로 다 틀렸다:
  //      · 우연히 값이 겹치면 **다른 간선**을 "이미 셌다" 로 보고 건너뛴다
  //      · 안 겹치면 **같은 간선**을 두 번 센다
  //    실측(sim, 2026-08-02): 마감 초과가 162건인데 `kSlowEdgeMisses`(3연속)에 한 번도
  //    못 닿아 느린 간선 벌점이 **0건**이었다. 재계획이 3초마다 도니 인덱스 동일성이
  //    성립할 틈이 없다. 간선 자체로 들고 있으면 재계획과 무관하게 같은 뜻이 된다.
  int missed_from{-1};
  int missed_to{-1};
  double plan_epoch{0.0};        // 그 계획이 t=0 으로 잡은 시각(초, steady)
  double plan_tick_sec{1.0};     // 틱 하나의 실제 길이(초)

};

/// 정점에 도달했다 — **방금 지나온** 간선에 마감 준수 크레딧을 줄까.
///
/// `arrived_idx` 는 도달한 정점의 인덱스다(`ActiveTask::idx` 를 **올리기 전** 값).
/// 크레딧 대상은 `path[arrived_idx-1] → path[arrived_idx]` 다.
///
/// ⚠️ [2026-08-02] **이 판정을 `idx++` 뒤에 하면 세 가지가 한꺼번에 틀어진다**
///    (codex 적대적 검토에서 드러났고 실제로 그렇게 돌고 있었다):
///      ① 크레딧이 방금 지나온 간선이 아니라 **앞으로 갈 간선**에 붙는다.
///         지나온 길의 벌점은 안 지워지고, 아직 안 가 본 길의 벌점이 지워진다.
///      ② 늦었던 간선 표시(`missed_from/to`)와 맞춰 봐야 하는데, 올린 뒤 인덱스로
///         간선을 뽑으면 **다음 간선**과 비교하게 되어 늦게 도착한 간선에도 크레딧이 간다.
///      ③ 마지막 정점에서는 올린 `idx` 가 `path.size()` 라 `path[idx]` 가
///         **범위 밖 접근**이다(`std::vector::operator[]` — UB).
///    그래서 도달 인덱스를 명시적으로 받는 순수 함수로 뺐다. 여기서 범위도 같이 막는다.
///
/// 반환 true 면 `from`/`to` 에 그 간선을 담는다. false 면 둘 다 안 건드린다.
inline bool traversed_edge_to_credit(const std::vector<int> & path,
                                     std::size_t arrived_idx,
                                     int missed_from, int missed_to,
                                     int & from, int & to)
{
  if (arrived_idx < 1 || arrived_idx >= path.size()) { return false; }
  const int f = path[arrived_idx - 1];
  const int t = path[arrived_idx];
  // 이 **간선**에서 이미 마감을 놓쳤다고 셌으면 크레딧을 주지 않는다 — 늦게 온 것이다.
  // 인덱스가 아니라 간선으로 비교하는 이유는 `ActiveTask::missed_from` 주석 참고.
  if (f == missed_from && t == missed_to) { return false; }
  from = f;
  to = t;
  return true;
}

/// `from_idx` 부터 **처음으로 마감을 넘긴 칸**의 인덱스. 없으면 `npos` 상당(= 범위 끝).
///
/// ⚠️ [2026-08-03] **왜 커밋 칸 하나가 아니라 그 뒤 전부를 보나**
///
///   관제의 **예약 표**는 `FleetPlan.arrive_tick` 이 `>= 0` 인 **모든 칸**에 카운트다운을
///   찍는다(`WaypointEditor.tsx:908-921`). 예전에는 `fleet_node` 가 커밋 칸 하나만
///   검사해서, 로봇이 아직 안 닿은 **먼 칸**이 0을 지나 `지연 +16.3s` 로 빨갛게 떠
///   있는데 아무도 재계획을 안 거는 상태가 생겼다(실기 2026-08-03).
///
///   계획의 약속("이 정점을 이 시각에 비운다")은 모든 칸에 걸려 있다. 먼 칸이 밀렸다는
///   것은 그 시간표가 이미 남에게 틀린 통과 허가를 주고 있다는 뜻이다.
///
///   ⚠️ **가장 이른 초과 칸 하나만** 돌려준다. 재계획 요청은 어차피 bool 하나라 한 틱에
///      한 번이고, 로그도 원인에 가장 가까운 칸을 가리키는 편이 읽힌다.
///   ⚠️ `arrive < 0` 은 건너뛴다 — 이미 떠난 칸, 그리고 CBS 가 안 짠 순회 꼬리다.
///      화면도 같은 규칙으로 라벨을 안 붙인다(`WaypointEditor.tsx:914`).
///   ⚠️ 경계는 `arrive` 와 `path` 중 **짧은 쪽**이다. 순회 꼬리 때문에 `path` 가 더 길다.
///
/// `due(i) = epoch + arrive[i] * tick_sec + slack`. `now > due(i)` 여야 초과다
/// (같으면 아직 아니다 — 기존 `now_sec() <= due` 판정을 그대로 옮겼다).
inline std::size_t first_overdue_cell(const std::vector<int> & path,
                                      const std::vector<int> & arrive,
                                      std::size_t from_idx,
                                      double epoch, double tick_sec, double slack,
                                      double now)
{
  const std::size_t last = std::min(arrive.size(), path.size());
  for (std::size_t i = from_idx; i < last; ++i) {
    if (arrive[i] < 0) { continue; }
    const double due = epoch + arrive[i] * tick_sec + slack;
    if (now > due) { return i; }
  }
  return last;
}

/// 재계획할 때 이 로봇의 경로를 **어디서부터** 짤 것인가.
///
/// `start` 는 계획의 출발 정점, `committed_from` 은 "지금 타고 있는 간선의 출발점"
/// (없으면 -1). 플러그인은 `committed_from` 이 있어야 그 간선의 예산
/// (`commit_deadline_tick`)을 계산해 준다.
///
/// 규칙:
///   · 이동 중이 아니면 → 서 있는 정점(`path[idx-1]`)에서, 커밋 간선 없음
///   · 이동 중이면      → 향해 가는 정점(`path[idx]`)에서, 커밋 간선 `path[idx-1]→path[idx]`
///   · **이동 중인데 향해 가는 정점이 차단됐으면** → 떠나온 정점에서, 커밋 간선 없음
///
/// ⚠️ [2026-08-03] 마지막 줄이 이번에 생겼다. 사람이 막은 정점은 곧 로봇이 향하던
///    `path[idx]` 인데, 차단 때 `moving` 을 아무도 내리지 않아 예전에는 그 노드를
///    출발점으로 줬다. CBS 가 **"로봇이 이미 거기 도착했다"** 고 가정하고 그 너머에서
///    경로를 짜서, 새 계획의 첫 홉이 **사람 너머**에서 시작했다. 로봇에 내려보내면
///    nav2 가 사람을 뚫으려다 실패한다(실기: `Failed to create plan with tolerance
///    of: 0.100000`). 사람 차단 뒤 FMS 가 보내는 `backup` 도 `path[idx-1]` 로 물리므로,
///    이렇게 두면 계획과 실제 위치가 같은 곳을 가리킨다.
///
/// 범위 밖(`idx < 1` 또는 `idx >= path.size()`)이면 아무것도 안 건드리고 false.
inline bool plan_start_for(const std::vector<int> & path, std::size_t idx, bool moving,
                           const std::vector<int> & blocked,
                           int & start, int & committed_from)
{
  if (idx < 1 || idx >= path.size()) { return false; }
  const bool commit_blocked =
    moving && std::find(blocked.begin(), blocked.end(), path[idx]) != blocked.end();
  const bool ride = moving && !commit_blocked;
  start = ride ? path[idx] : path[idx - 1];
  committed_from = ride ? path[idx - 1] : -1;
  return true;
}

}  // namespace libi_fleet
