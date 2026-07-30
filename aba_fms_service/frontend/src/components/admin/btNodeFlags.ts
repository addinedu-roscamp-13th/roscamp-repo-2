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
 * ## 2026-07-30 — 복귀 6단계 재편 (뒷캠 ArUco 정밀 주차)
 *
 * 노드 **넷이 사라지고 다섯이 생겼다.** 옛 이름으로 단 플래그는 조용히 안 붙는다.
 *
 *   사라짐  FaceParking · GoToParking · TurnAround · AlignDock
 *   생김    FaceApproachYaw · ReleaseNav · ArucoApproach · DockNudge · DockSettle
 *
 * 새 순서: GoToParkingEntrance → FaceApproachYaw → ReleaseNav → ArucoApproach
 *          → DockNudge → DockSettle
 *
 * ## 2026-07-30 (2) — 마커 도킹 이식 + 도킹 탈출
 *
 * 노드 **넷이 더 생겼다.**
 *
 *   BackCamOn                   ReturnAndWatch 의 첫 자식. 도킹 동안 뒷캠을 선택 상태로
 *                               유지한다(대기 캠은 8틱에 한 번 = 1.9Hz 라 시각 서보가 못 돈다).
 *                               절대 끝나지 않는다 — SUCCESS 를 내면 Parallel(SuccessOnOne)이
 *                               복귀를 통째로 끝낸다.
 *   UndockOrSkip                PATROL·WORKING·SECURITY_PATROL 의 주행 **앞**. Selector.
 *   ├ UndockNotNeeded           도킹 아니거나 이미 나왔으면 통과(평소엔 여기서 끝)
 *   └ Absorb[Undock]            6cm 전진 + **pose 로 실제 이동 확인**
 *
 * ⚠️ `UndockOrSkip` 은 **세 브랜치에 각각 별개 인스턴스**다(py_trees 노드는 부모를 하나만
 *    갖는다). 스냅샷에 같은 이름이 세 번 나오는 것이 정상이다.
 */
export const BT_NODE_FLAGS: Record<string, BtNodeFlag> = {
  // ── 복귀 6단계 (2026-07-27 신설 · 2026-07-30 재편) ────────────────────────
  //
  // ① GoToParkingEntrance  nav2 로 주차장 입구까지        — 정상
  // ② FaceApproachYaw      접근 자세로 회전(절대각)        — 정상 (플래그 없음)
  //      [2026-07-30] 옛 `FaceParking` 의 partial 을 **해제**했다. 좌표로 atan2 를 내던
  //      것이 "앞캠 ArUco 로 갈아끼울 자리"였는데, 재편으로 그 자리 자체가 없어졌다 —
  //      이제 고정 절대각 한 번이고, 정밀도는 ④가 책임진다.

  // ③ ReleaseNav  nav2 목표를 끊고 바퀴를 외부에 넘긴다 — 정상 (플래그 없음)
  //      [2026-07-30, codex 리뷰] ①은 x·y 만 보고 성공하고 성공한 단계는 stop 을 안
  //      보낸다. 예전엔 `GoToParking` 이 새 goal 로 선점해 줬는데 그 단계가 없어져,
  //      입구 goal 이 살아 있는 채로 ArUco 접근이 시작될 수 있었다.

  // ④ 뒷캠 ArUco 로 6cm 까지. **구현이 이 저장소에 없다.**
  //    `/fleet_cmd{dock_action}` 을 내고 `/fleet_cmd_result` 를 기다리는 통로는 있다
  //    (main.py `return_aruco` · FleetCmdDriver). 그래서 unwired 가 아니라 partial 이다.
  //    기본 액션 이름은 `aruco_dock` 이고 **아무도 답하지 않는다** — fleet_link 가 모르는
  //    이름이라 `400 알 수 없는 action` 이 즉시 와서 재시도 끝에 ERROR 로 간다.
  //    일부러 그렇게 뒀다: `"dock"` 이면 fleet_link 의 `park_dock`(라인 트레이싱, 실물
  //    미검증)이 대신 로봇을 움직이고 성공까지 답한다 — 조용한 오작동보다 시끄러운
  //    실패가 낫다.
  //      main.py `dock_action` · fleet_link.py `if action == "dock"` · dock_runner.py
  //    ⚠️ 외부 노드를 붙이는 날 **답하는 쪽을 하나로 만들어야 한다.** 둘이 답하면
  //       먼저 온 결과로 ⑤가 시작된다(팔의 `LIBI_ARM_VIA_BT` 와 같은 함정).
  //    ⚠️ 취소 계약도 없다 — timeout 뒤의 `stop` 은 nav2 만 끊는다.
  //    해제 조건: 뒷캠 ArUco 구현이 이 액션에 답하고 6cm 정지가 실물에서 확인될 때.
  ArucoApproach: "partial",

  // ⑤ 마지막 3cm 를 개루프로 민다(cmd_vel_dock, twist_mux priority 120).
  //    코드는 완결이고 단위시험도 있다(test_nudge_driver.py). partial 인 이유는 하나 —
  //    **거리가 실측 보정 전이다.** 바퀴 슬립·모터 데드밴드 때문에 명령값과 실이동이
  //    어긋나는데, 그 오차가 그대로 충전 단자 정렬 오차가 된다.
  //      params.yaml returning.nudge_distance_m / nudge_speed_mps
  //    해제 조건: 평지에서 ⑤만 돌려 자로 재고 params 를 맞춘 뒤.
  DockNudge: "partial",

  // ⑥ 안정화 대기 후 **도킹으로 친다.** 접촉을 확인하지 않는다.
  //    ⚠️ 접점이 안 붙어도 CHARGING 이 선언된다. ChargingBranch 의 이탈 조건은 fault
  //       또는 battery >= 40 뿐이라, 충전이 안 되면 방전까지 갇힌다(charging.py).
  //    [2026-07-30] 옛 `AlignDock` 은 `is_docked` 를 기다렸는데, 그 신호는 주차장 정점
  //    반경 0.12m 판정(dock_confirm.py)이라 ④⑤를 지난 뒤에는 이미 참이었다 — 검증하는
  //    척만 했다. 그래서 사용자 결정으로 시간 대기로 바꿨다. 대기가 하는 일은 개루프
  //    후진의 관성이 가라앉을 시간을 주는 것뿐이다.
  //      return_steps.py DockSettle · params.yaml returning.settle_sec
  //    해제 조건: 진짜 접촉 확인(충전 전류·전압 상승 등)이 생겨 그 신호를 기다릴 때.
  DockSettle: "partial",

  // ── 마커 도킹 이식 (2026-07-30) ───────────────────────────────────────────
  //
  // ④ ArucoApproach 의 `partial` 은 **유지한다.** 구현이 붙었지만(robot_agent 의
  // `marker_dock.py`, arte_aurcomaker_move 커밋 5002e60 에서 판단 로직 무수정 이식)
  // **이 체인 전체가 실기에서 한 번도 안 돌았다.** 조각은 검증됐다 — approach.py 는
  // 같은 로봇에서 실주행했고 단위시험 106개가 있다. 이음매가 처음이다.
  //   robot_agent/app/core/marker_dock.py · app/marker/
  //   해제 조건: 실기에서 복귀 한 사이클이 끝까지 성공한 뒤.

  // 도킹 자세에서 빠져나온다. 코드는 완결이고 시험도 있다(test_undock.py).
  // partial 인 이유: **거리(6cm)가 실측 전이다.** 이 값이 모자라면 nav2 가 시작 격자를
  // 통행불가로 보고 경로를 못 낸다 — 증상이 도킹과 멀리 떨어져 나타난다.
  //   근거·수치: libi_modes/common/undock.py 머리말
  //   nav2_params.yaml: inscribed 0.088 < inflation_radius 0.09
  //   해제 조건: 도킹 후 실제로 나가서 nav2 가 경로를 내는 것을 확인한 뒤.
  Undock: "partial",

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
