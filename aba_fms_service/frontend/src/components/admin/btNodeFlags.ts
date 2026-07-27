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
 * ## 2026-07-26 감사 (codex 1차 → 직접 재확인)
 * codex 가 낸 6건 중, 코드로 확인되고 **이 트리의 노드에 해당하는** 것만 넣었다.
 * 나머지는 노드 표시가 아니라 결함 목록으로 다뤄야 할 것들이라 뺐다:
 *   · ReturnNavigation 이 도킹 수락 전 무한 대기 → 결함이지 미구현은 아니다
 *   · 주행 타임아웃이 드라이버를 확실히 stop 하지 않음 → 같음
 *   · DOCK_RETRY_COUNT 가 복귀 회차마다 초기화되지 않음 → 같음
 *   · DriverAction 이 죽은 코드 → 트리에 아예 안 실려서 화면에 나오지 않는다
 */
export const BT_NODE_FLAGS: Record<string, BtNodeFlag> = {
  // ⚠️ **추종 기능이 없다는 뜻이 아니다.** libi_perception 에 전부 있다:
  //      FollowSession   start()/poll()/stop() — 이 leaf 가 요구하는 계약 그대로
  //      FollowSwitch    TRACKING / SEARCHING / ENDED
  //      recovery_bt.py  놓쳤을 때 도는 **별도 회복 BT**
  //                      (CheckReacquired | Hold → Scan1 → Turn180 → Scan2 → GiveUp)
  //
  // 끊긴 건 **통로**다. 두 군데가 비어 있다:
  //   1) libi_perception 이 `autostart: True` 로 자기 프로세스에서 알아서 돈다.
  //      밖에서 켜고 끌 토픽·서비스가 없다. fsm_node 가 FollowSession 을 직접 만들면
  //      cmd_vel 발행자와 검출 TCP 연결이 이중이 된다.
  //   2) providers._on_cmd 가 `follow_admin` 을 active_command 로 **분류조차 안 한다** —
  //      드라이버를 꽂아도 이 leaf 까지 명령이 도달하지 않는다.
  // 그래서 main.py 에 "follow" 키가 없고 `UnwiredDriver` 로 떨어져 이름 붙은 에러로 실패한다.
  //
  // 지금 관제의 추종 시작/정지(/api/robot/admin-follow)는 FSM 상태만 WORKING 으로 옮기는
  // **별개 경로**다. BT 를 거치지 않는다.
  //   working_actions.py:194,245 · main.py:126-158 · providers.py:118-146
  //   follow_node.py:19,90 · recovery_bt.py:1-23 · switch.py:19
  // [2026-07-26] 배선 완료 → 플래그 해제 대기 중.
  //   libi_perception 에 RemoteControl(/fleet_cmd 왕복) 추가, autostart 기본 False 로 변경
  //   providers 에 follow_actions=("follow_admin",) 추가 → active_command 로 분류
  //   main.py 에 "follow": FleetCmdDriver("follow_admin") 주입
  // 실기에서 한 번 돌려보고 확인되면 이 줄을 지운다. 아직 sim 에서 검증 불가
  // (추종은 real 전용 — libi_perception + image_sender 가 있어야 한다).
  FollowExec: "unwired",

  // ⚠️ **길잡이 주행은 동작한다.** 흐린 건 "요청자를 보고 있는가" 한 가지다.
  //
  // 도는 것:
  //   providers 가 `guide` 를 active_command 로 분류하고 목적지를 싣는다 (providers.py:147,161)
  //   GuideExec 이 목적지로 몰고 실좌표로 도착을 판정한다 (working_actions.py:GuideExec)
  //   요청자를 놓치면 mission_stop → ros_bridge.cancel_nav() 로 **실제로 멈춘다**
  //     (main.py "guide_stop" · fleet_link.py:125 · mission.py:128)
  //   test_guide_exec.py 가 유예·정지·재개·포기·도착을 전부 붙들고 있다
  //
  // 안 도는 것 ① 정지가 실제로 안 먹는다.
  //   `mission_stop` → `mission.stop_mission()` → `ros_bridge.cancel_nav()` 까지는 가는데,
  //   `cancel_nav()` 가 취소하는 `_active_goal_handle` 은 **채워지지 않는다** —
  //   `send_nav_goal()` 이 `send_goal_async()` 결과에 `_on_goal_response` 콜백을 안 달기
  //   때문이다(ros_bridge.py:486 vs :494,512). 그래서 요청자를 놓쳐 정지를 보내도 nav2 는
  //   계속 달린다. ⚠️ 이건 길잡이만의 문제가 아니다 — `goal` 로 나가는 **모든 주행**
  //   (배달·순회·복귀)이 취소되지 않는다. robot_agent 소유라 실기 확인 없이 손대지 않았다.
  //
  // 안 도는 것 ② `/libi/requester_visible` 을 **아무도 발행하지 않는다**.
  //   providers 는 구독하고 있고(providers.py:89), 발행자가 없으면 값이 None 이다.
  //   GuideExec 은 None 을 "감시 없음"으로 읽고 **그냥 주행한다**(멈추지 않는다) —
  //   즉 지금 배포에서 길잡이는 사람을 놓쳐도 계속 간다. 이게 partial 인 이유다.
  //   발행할 쪽은 libi_perception 이다(DetectionReceiver 가 이미 "지금 보이나"를 안다:
  //   detection_receiver.py:latest). 붙이면 이 플래그를 지운다.
  GuideExec: "partial",

  // ── 2026-07-26 수정 완료 (플래그 해제) ────────────────────────────────────
  // UiSessionTimer[20s] : "인터록을 소유한다"는 주석이 거짓이었다. 락을 읽는 production
  //   코드가 없고, BT 안에 검사를 넣어도 죽은 코드다(Priorities 가 Selector 라 락이 켜진
  //   동안 다른 브랜치 액션은 tick 되지 않는다). 주석을 사실대로 고쳐 결함을 없앴다.
  //   → ui_session_timer.py 상단 docstring
  // CommandTimeout[120s] : providers 가 command_received_at 을 ROS 시계(epoch)로 찍어
  //   monotonic 과 섞였고, 그래서 120초 조건이 영원히 성립하지 않았다. monotonic 으로
  //   통일하고 회귀 테스트를 붙였다.
  //   → providers.py `_on_cmd` · test_providers_touch.py
};
