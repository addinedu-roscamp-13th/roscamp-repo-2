import type { BtNodeFlag } from "@/components/admin/BtGraphView";

/**
 * BT 노드의 **정적** 검사 결과. `/libi/bt_snapshot` 은 `{name,status,children}` 뿐이라
 * "이 노드가 진짜 구현돼 있는가"는 런타임에 알 수 없다. 코드를 읽어서 판정한 뒤 여기 적는다.
 *
 * ## 판정 기준
 *   unwired      로직은 있는데 이 트리에서 부를 통로가 없다. 구현이 없다는 뜻이 **아니다**.
 *   partial      일부만 동작한다. 노드는 도는데 그 결과를 아무도 안 쓰는 경우 포함.
 *   unreachable  어떤 경로로도 진입할 수 없다. 서브트리 전체가 같이 흐려진다.
 *
 * ## 규칙
 * - 키는 스냅샷에 실리는 **노드 이름 그대로**다(py_trees `name`). 클래스명이 아니다.
 *   이름이 바뀌면 범례 숫자가 0 으로 떨어지므로 화면에서 바로 드러난다.
 * - 정상 동작하는 노드는 **여기 적지 않는다.** 비어 있는 게 기본값이다.
 * - 근거 없이 넣지 않는다. 아래 주석의 근거는 전부 코드에서 직접 확인한 것이다.
 *
 * ## 2026-07-27 갱신 — 추종·길잡이 완성, 복귀 5단계
 *
 * 기존 플래그 **둘 다 해제**했다. 끊겨 있던 통로가 배선됐기 때문이다:
 *
 *   FollowExec  `fleet_link.BT_LAYER_ACTIONS` 에 `follow_admin` 이 없어서, 실행 층이
 *               "알 수 없는 action" 실패 결과를 **먼저** 내고 FleetCmdDriver 가 그걸
 *               집어 세션이 시작 즉시 끝나고 있었다. 그 집합에 세션 명령을 추가했다.
 *               (fleet_link.py BT_LAYER_ACTIONS · follow_node.py RemoteControl)
 *   GuideExec   `/libi/requester_visible` 발행자가 없어 값이 None 이었고, GuideExec 은
 *               None 을 "감시 없음 → 그냥 주행"으로 읽어 사람을 놓쳐도 계속 갔다.
 *               이제 follow_node 가 guide/watch 역할일 때 발행한다. 정지가 실제로 안
 *               먹던 원인(nav goal 응답 콜백 미연결)도 고쳤다.
 *               (follow_node.py _publish_requester · ros_bridge.py send_nav_goal)
 *
 * 회복 BT 에 노드가 셋 늘었다(PeekBack/PeekBack2 는 SearchPhases 안, PeekReacquired 와
 * AlignHeading 은 BT_Searching 바로 아래). 전부 정상 동작이라 여기 적지 않는다.
 *
 * ## 2026-07-28 — 회복 탐색 시퀀스 교체
 *
 * `SearchPhases` 의 자식이 통째로 바뀌었다. 옛 이름(Hold·PeekBack·PeekBack2·
 * Scan1~3·Turn180)은 **더 이상 없다.**
 *
 *   HoldFront → HoldBack → SweepFront{Out,Across,Home} → SweepBack{Out,Across,Home} → GiveUp
 *
 * 앞뒤로 5초씩 서서 보고, 각각 좌우로 훑은 뒤 원위치로 돌아온다. 어느 구간에 있든
 * 위 Selector 가 매 tick 이기므로(앞캠 재획득 → 즉시 종료, 뒷캠 포착 → 180° 회전)
 * 구간을 끝까지 돌 필요가 없다.
 *
 * 이 목록에 플래그로 등록된 이름은 없어서 범례 숫자는 안 바뀐다 — 그래도 여기 적는
 * 이유는, 다음 사람이 옛 이름으로 플래그를 달면 **조용히 안 붙기 때문**이다.
 *
 * 새로 흐리게 표시하는 것은 복귀 5단계 중 **마커로 갈아끼울 두 자리**뿐이다.
 */
export const BT_NODE_FLAGS: Record<string, BtNodeFlag> = {
  // ── 복귀 5단계 (2026-07-27 신설) ─────────────────────────────────────────
  //   GoToParkingEntrance → FaceParking → GoToParking → TurnAround → AlignDock
  //
  // 주행 세 단계는 정상 동작한다. 아래 둘만 "나중에 ArUco 로 갈아끼울 자리"다.

  // 좌표만으로 각도를 낸다 — 현재 pose 와 주차장 좌표로 atan2. 동작은 하지만,
  // 설계상 여기는 **앞캠 ArUco** 가 들어갈 자리다(마커가 붙으면 이 플래그를 지운다).
  // nav2 가 방금 그 AMCL 로 입구까지 왔으므로 좌표 기반으로도 충분하다는 판단이라,
  // 미구현이 아니라 "정밀도를 나중에 올릴 곳"이라는 뜻의 partial 이다.
  //   return_steps.py FaceParking · _YawStep
  FaceParking: "partial",

  // 정렬을 위한 **미세 이동이 아직 없다.** 지금 하는 일은 `is_docked`(실제 도킹 확인)를
  // 기다리는 것뿐이다. 그 확인까지 빼면 로봇이 충전소에 닿지도 않은 채 CHARGING 을
  // 선언하므로 남겨 뒀다 — 화면은 멀쩡한데 배터리는 계속 떨어지는 상태가 된다.
  // **뒷캠 ArUco** 가 들어갈 자리이며, 그때 트리 배선은 안 바뀐다.
  //
  // [2026-07-28] `unwired` → `partial`. `unwired` 는 "부를 통로가 없다"는 뜻인데,
  // 이 노드는 `create_return_steps` 에 들어가 `ReturningBranch` 가 **실제로 실행한다**
  // (is_docked 대기 + timeout → AbsorbFailure 재시도 → 소진 시 fault). 통로는 있고
  // 정렬 동작만 비어 있으니 `partial` 이 맞다 — `FaceParking` 과 같은 성격이다.
  //   return_steps.py AlignDock · returning.py create_return_steps
  AlignDock: "partial",

  // ── 2026-07-27 해제 ───────────────────────────────────────────────────────
  // FollowExec: "unwired"  → fleet_link.BT_LAYER_ACTIONS 에 세션 명령 추가로 해제
  // GuideExec:  "partial"  → requester_visible/area 발행자 추가 + nav 취소 수정으로 해제
  //
  // ── 2026-07-26 해제 ───────────────────────────────────────────────────────
  // UiSessionTimer[20s] : "인터록을 소유한다"는 주석이 거짓이었다. 락을 읽는 production
  //   코드가 없고, BT 안에 검사를 넣어도 죽은 코드다(Priorities 가 Selector 라 락이 켜진
  //   동안 다른 브랜치 액션은 tick 되지 않는다). 주석을 사실대로 고쳐 결함을 없앴다.
  // CommandTimeout[120s] : providers 가 command_received_at 을 ROS 시계(epoch)로 찍어
  //   monotonic 과 섞였고, 그래서 120초 조건이 영원히 성립하지 않았다.
};
