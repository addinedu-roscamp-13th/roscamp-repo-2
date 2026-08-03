"""libi_modes 실행 노드 — 미션 FSM 트리를 로봇 도메인에서 tick 한다.

    ros2 run libi_modes fsm_node --ros-args -p robot_id:=pinkySim

`ROS_DOMAIN_ID` 는 건드리지 않는다 — `sim.sh` / `laptop.sh` 가 이미 로봇과 같은 도메인으로
export 해준다(sim=90, 실기는 로봇별 88/89/…). 중앙(86)으로는 domain_bridge 가 올려준다.

## 로봇 1대 = 프로세스 1개

py_trees 의 blackboard 는 프로세스 전역이라 한 프로세스가 두 로봇의 트리를 굴릴 수 없다
(테스트가 매 케이스마다 blackboard 를 비우는 이유도 같다). 로봇이 여러 대면 프로세스를
여러 개 띄운다 — 어차피 각 로봇이 자기 도메인에 있으므로 자연스럽다.

## tick 한 번의 순서

    1. 대기 중인 수동 전이 적용 (서비스로 들어온 것)
    2. 트리 tick — Topics2BB 가 구독값을 blackboard 에 넣고, 브랜치가 판단·행동
    3. blackboard 를 되읽어 provider 와 대조 (leaf 가 소비한 명령을 provider 도 비움)
    4. 상태·스냅샷 발행

3번이 빠지면 Topics2BB 가 다음 tick 에 소비된 명령을 되살려 같은 전이가 반복된다.
"""
import json
import os

import py_trees
import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from py_trees.common import Access
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from std_msgs.msg import String

from libi_modes import blackboard as bb
from libi_modes import tree as tree_mod
from libi_modes.blackboard import Keys
from libi_modes.common.junctions import JunctionSet, load_junctions
from libi_modes.registry import BRANCH_ORDER
from libi_modes.ros.fleet_cmd_driver import FleetCmdDriver, NudgeDriver, YawGoalDriver
from libi_modes.ros.handy_action_driver import ArmStubDriver, HandyActionDriver
from libi_modes.ros.providers import RosProviders
from libi_modes.ros.state_io import StateIO

#: 부팅 상태. 전이 박스의 `[*] -> IDLE`.
#
# ## 왜 RETURNING 이 아닌가 (2026-07-22 변경)
#
# 원래는 `RETURNING` 이었다. 이유는 "켜진 순간 로봇은 자기 위치도 팔 자세도 모르니
# 일단 팔을 접고 충전소로 가서 도킹부터 하라"였다. 팔 자세 걱정은 맞는데, 상태를
# `RETURNING` 으로 두는 건 그 문제를 푸는 방법이 아니었다:
#
#   - **켜자마자 주행을 시작한다.** `ReturnNavigation` 이 부팅 1초 안에 도킹 명령을
#     내는데, AMCL 은 아직 수렴 전이다. 위치를 모르는 채로 목표를 찍는 셈이다.
#   - **이미 충전소에 있어도 왕복한다.** 로봇은 보통 충전 중에 켜지므로, 제자리에서
#     "복귀"를 하는 헛수고가 매번 일어난다.
#   - **모든 시험이 이 구간을 먼저 지나야 했다.** 배차도 순회도 `RETURNING` 을
#     빠져나온 뒤에야 볼 수 있어서, 여기가 막히면 그 위의 모든 것이 안 보였다.
#
# `IDLE` 은 아무 데도 가지 않고 기다린다. 팔 자세 문제는 **부팅 시 팔 홈 복귀를 한 번
# 보내는 것**으로 그대로 해결한다(아래 `_boot_arm_home`) — 상태를 바꿔서 우회하는 것보다
# 직접적이다.
#
# ⚠️ 바꿀 때 `libi_modes/registry.py` 와 FMS 의 `app/fsm_model.py` 를 **함께** 고쳐야 한다.
#    둘이 어긋나면 `test_fsm_registry_drift.py` 가 잡는다.
BOOT_STATE = "IDLE"

#: 팔 다리를 **실제 팔 보드**로 중계할까 (`arm_task` 액션). 기본은 아니다.
#
# ⚠️ **답하는 쪽이 하나여야 한다.** `/fleet_cmd{perform_action}` 은 두 프로세스가 듣는다 —
#    이 BT 와 robot_agent 의 `fleet_link`. FMS 는 `/fleet_cmd_result` 를 **처음 받은 것**으로
#    다리를 닫으므로, 둘이 다 답하면 팔이 아직 움직이는 중에 다음 주행이 시작된다
#    (팔을 뻗은 채 로봇이 간다). 그래서 같은 env 한 개로 갈라 놓는다:
#
#      0 (기본) 팔 보드 없음. robot_agent 가 즉시 성공으로 답하고, 여기선 스텁이 통과시킨다.
#      1        팔 보드 있음. 여기서 액션으로 중계하고 **완료를 여기서 답한다**.
#               robot_agent 는 침묵한다(`fleet_link.ARM_ACTIONS`).
#
#    ⚠️ 켤 때 **robot_agent 프로세스에도 같은 env 를 주어야 한다.** 한쪽만 켜면
#       아무도 답하지 않아(0/1 반대면 둘이 답해) 주문이 조용히 멈춘다.
ARM_VIA_BT = os.getenv("LIBI_ARM_VIA_BT", "0") == "1"

#: 요청자를 놓쳤을 때 내는 `mission_stop` 의 응답 타임아웃(초).
#
# 기본값(120초)을 쓰면 **답이 안 올 때 안내가 2분을 통째로 멈춘다** —
# `GuideExec._stop_settled()` 가 이 결과를 기다리는 동안 회복 회전이 안 열리고,
# 회전이 안 열리면 회복 트리 자체가 안 만들어진다(`control_loop._start_search`).
# 타임아웃이 나면 `GuideExec` 은 회전 대신 **안내를 끝낸다**(정지를 못 믿으므로).
#
# ⚠️ 너무 짧게 잡으면 멀쩡한 정지를 실패로 읽어 안내가 헛되이 끝난다. 실행 층의
#    `mission_stop` 은 인라인이지만 `mission.stop_mission()` 이 살아 있는 미션
#    스레드를 **최대 2초 join** 한다(robot_agent `app/core/mission.py`). 거기에
#    DDS 왕복·콜백 지연 여유를 더해 8초로 둔다.
GUIDE_STOP_TIMEOUT_SEC = 8.0


class _CmdPublisher:
    """드라이버들이 공유하는 /fleet_cmd 발행자."""

    def __init__(self, node, topic):
        self._pub = node.create_publisher(String, topic, 10)

    def publish_json(self, payload):
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self._pub.publish(msg)


class FsmNode(Node):
    def __init__(self):
        super().__init__("libi_modes")

        robot_id = self.declare_parameter("robot_id", "libi").value
        # dynamic_typing — 안 주면 `-p tick_hz:=4` 가 INTEGER 로 파싱돼 DOUBLE 기대와
        # 충돌하며 노드가 뜨지 않는다. 정수로 주는 게 자연스러운 값이라 둘 다 받는다.
        tick_hz = float(self.declare_parameter(
            "tick_hz", 10.0, ParameterDescriptor(dynamic_typing=True)).value)
        params_file = self.declare_parameter("params_file", "").value
        cmd_topic = self.declare_parameter("cmd_topic", "fleet_cmd").value
        result_topic = self.declare_parameter("result_topic", "fleet_cmd_result").value
        home_location = self.declare_parameter("home_location", "charger").value
        # 도킹 미세 이동이 쓰는 twist_mux 입력. **`/cmd_vel` 이 아니다** —
        # 직접 발행 금지는 twist_mux.yaml 이 정한 것이고, 그 문이 있어서 비상정지가 이긴다.
        dock_nudge_topic = self.declare_parameter("dock_nudge_topic", "/cmd_vel_dock").value

        # 복귀 목적지 — **주차장 정점의 좌표와 자세**.
        #
        # 예전엔 `home` 액션을 보냈고, 그건 robot_agent 의 `mission.go_home()` 으로 가서
        # "로봇에 HOME 이 등록돼 있으면 거기, 없으면 맵 원점(0,0,0)" 이었다. 로봇마다
        # 등록 상태가 다르면 **같은 명령이 로봇마다 다른 곳으로 간다.** 그래서 좌표를
        # 여기서 명시한다 — 주행 명령과 똑같이 `goal` 로 나간다.
        #
        # yaw 0 = +x 방향 = 입구·안내데스크 쪽(관제 화면에서 위쪽). waypoint.yaml 의
        # `주차장` 자세와 같은 값이다.
        #
        # [2026-07-30] 복귀 단계 재편 — 뒷캠 ArUco 정밀 주차.
        #
        #   ① 주차장 입구 이동   nav2 주행                    GoToParkingEntrance
        #   ② 접근 자세 회전     절대각(approach_yaw_rad)      FaceApproachYaw
        #   ③ nav2 목표 해제     바퀴를 외부에 넘기기 전       ReleaseNav
        #   ④ 정밀 도킹 6cm      **`dock_sensor` 가 정한다**    DockApproach
        #   ⑤ 개루프 후진        cmd_vel_dock 정속 발행        DockNudge
        #   ⑥ 안정화 대기        시간으로 넘긴다               DockSettle
        #
        # 없앤 것: `GoToParking`(nav2 로 주차장 정점까지) · `TurnAround`(180°).
        # ②가 한 번에 접근 자세로 돌리므로 둘 다 필요 없어졌고, AMCL 오차가 충전 단자
        # 폭보다 큰 마지막 구간을 nav2 에게 맡기지 않는 것이 이 재편의 요점이다.
        #
        # ⚠️ `return_x/y/yaw` 는 이제 **트리가 안 쓴다**(`GoToParking` 이 없어졌다).
        #    선언만 남겨 둔다 — 이 값을 넘기는 런치 파일이 있고, 선언을 지우면 그쪽이
        #    "undeclared parameter" 로 죽는다. 주차장 좌표 판정은 `dock_confirm.py` 몫이다.
        # ⚠️ 이 좌표는 다시 **쓰인다.** ②가 "등을 충전소로 향한다"를
        #    `atan2(충전소 − 현재) + 180°` 로 내기 때문이다(return_steps `_approach_yaw`).
        #    arte2 navgraph 의 `충전소` 정점.
        return_x = float(self.declare_parameter("return_x", -0.0007).value)
        return_y = float(self.declare_parameter("return_y", -0.0333).value)
        self.declare_parameter("return_yaw", 3.1415)
        # 도킹 접근 정점 — arte2 navgraph 의 **`충전소통로`**.
        #
        # [2026-07-30] `충전소입구`(0.6005, 옛 `주차장입구`) → `충전소통로`(0.384) 로 옮겼다.
        #   충전소통로 → 충전소 = **0.385m**. `axis_gate_m`(0.6) **안**이라 도착하자마자
        #   자세를 신뢰하는 AXIS_ALIGN 구간으로 들어간다. 충전소입구(0.60m)는 딱 그 경계라
        #   HOMING/AXIS_ALIGN 을 오갈 여지가 있었다.
        #   좌표 출처: aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml (19번 정점)
        #
        # ⚠️ 노드 이름은 `GoToParkingEntrance` 그대로다 — 관제 화면 라벨이라 개명하면
        #    `btNodeFlags.ts`·README 를 같이 고쳐야 한다(CLAUDE.md). 목적지가 바뀐 것은
        #    이 주석과 params 로 남긴다.
        entrance_x = float(self.declare_parameter("entrance_x", 0.384).value)
        entrance_y = float(self.declare_parameter("entrance_y", -0.03).value)
        entrance_yaw = float(self.declare_parameter("entrance_yaw", 3.1415).value)

        # [디버그] 잠글 상태 브랜치 — 콤마구분. ROS param `disabled_branches` 또는
        # env `LIBI_DISABLED_BRANCHES` (fsm-bt.sh --disable 가 env 로 넘긴다). 기본 빈 값.
        disabled_branches = self._resolve_disabled_branches()

        params = self._load_params(params_file)
        self._apply_battery_auto(params)

        cmd_pub = _CmdPublisher(self, cmd_topic)
        # 팔 완료를 FMS 에 올리는 통로. 팔을 중계할 때만 만든다 — 안 쓰는 발행자를
        # 띄우면 "누가 답하는가"가 코드만 봐선 흐려진다(위 ARM_VIA_BT 주석).
        self._result_pub = _CmdPublisher(self, result_topic) if ARM_VIA_BT else None
        self._providers = RosProviders(
            self, cmd_topic=cmd_topic,
            # 요청자 가시성이 이 시간 갱신되지 않으면 False(정지)로 본다.
            # 감시하던 쪽이 죽었을 때 마지막 True 가 영원히 남아 로봇이 사람 없이
            # 계속 몰고 가는 것을 막는다.
            requester_ttl_sec=float(
                params.get("working", {}).get("requester_ttl_sec", 2.0)))

        # 액션별 드라이버. 전부 같은 /fleet_cmd 통로를 쓰고 결과는 id 로 갈린다.
        self._drivers = {
            # 순회도 배달과 **같은 실행 층 액션**을 쓴다. 예전엔 mission_start 라
            # 로봇이 자기 waypoint 목록을 혼자 돌았고, 그 경로는 fleet_node 의 교통
            # 예약을 아예 안 거쳤다 — 순회가 두 벌 동시에 돌고 있었다.
            "patrol": FleetCmdDriver(self, "goal",
                                     args_fn=lambda: self._nav_args(home_location)).bind(cmd_pub),
            # 야간 순찰도 순회(patrol)와 **같은 실행 경로**(goal, fleet_node 교통 경유)로 돈다.
            # 예전엔 mission_start 라 로봇이 waypoint 를 혼자 한 바퀴 돌고 IDLE 로 나갔는데,
            # 야간엔 계속 순찰해야 하므로 patrol 과 같은 goal 드라이버를 써서
            # security_patrol 브랜치가 PatrolNavigation 으로 지속 순찰하게 한다(1회로 안 끝남).
            "security_patrol": FleetCmdDriver(self, "goal",
                                              args_fn=lambda: self._nav_args(home_location)).bind(cmd_pub),
            # 주행: FMS 가 /fleet_cmd{navigate, x,y,yaw} 로 준 목적지를 blackboard 에서 읽어
            # 실행 층(goal)으로 내려보낸다.
            #
            # ⚠️ 예전엔 `args_fn=lambda: {"name": home_location}` 으로 **하드코딩**돼 있었다.
            # 목적지를 받을 통로가 아예 없어서 NavigationExec 은 항상 home 으로만 갈 수 있었고,
            # 그래서 이 브랜치는 사실상 죽은 코드였다. FMS 는 그동안 BT 를 우회해
            # fleet_node → path_request_driver → nav2 로 직접 몰았다.
            #
            # 목적지가 아직 없으면(주행 명령 없이 tick 이 돈 경우) home 으로 폴백한다 —
            # 좌표 없는 goal 을 보내면 fleet_link 가 KeyError 로 죽는다.
            "nav": FleetCmdDriver(self, "goal",
                                  args_fn=lambda: self._nav_args(home_location)).bind(cmd_pub),
            # 팔 — **액션 중계**다. `/fleet_cmd{perform_action}` 을 받아 팔 보드의
            # `arm_task` 액션 서버를 부른다(2026-07-30 확정).
            #
            # ⚠️ 예전에는 `FleetCmdDriver(self, "perform_action")` 이었다. 그건 두 가지가
            #    틀렸다: ① `args_fn` 이 없어 **`{}` 를 발행했다** — 어느 책을 어디서 집으라는
            #    정보가 통째로 사라졌다. ② `/fleet_cmd` 로 되돌려 발행하니 그걸 받는 건
            #    robot_agent 의 스텁이었고, 팔 보드까지 가는 통로가 아예 없었다.
            #    (팔이 스텁이라 둘 다 드러나지 않았다)
            #
            # 취소·중복방어·진행보고를 직접 구현하지 않으려고 액션을 쓴다 —
            # 옵시디언 `presen/final/14 로봇팔 통합 - 토픽 대신 액션.md`.
            #
            # ⚠️ **팔 보드가 붙기 전에는 스텁이다**(`LIBI_ARM_VIA_BT` 주석 참고).
            "arm": (HandyActionDriver(self, lambda: self._read(Keys.ARM_ARGS),
                                      result_fn=self._publish_arm_result)
                    if ARM_VIA_BT else ArmStubDriver(self)),
            # 길잡이 주행 — nav 와 같은 실행 층 `goal` 이다. 다른 건 BT 층에서 누가 받느냐뿐.
            "guide": FleetCmdDriver(self, "goal",
                                    args_fn=lambda: self._nav_args(home_location)).bind(cmd_pub),
            # 요청자를 놓쳤을 때 **실제로 멈추는** 수단. mission_stop → ros_bridge.cancel_nav().
            # 이게 없으면 GuideExec 은 "기다리는 중"만 표시하고 로봇은 계속 달린다.
            #
            # ⚠️ 응답 타임아웃을 기본(120초)에서 줄인다. `GuideExec._stop_settled()` 가
            #    이 결과를 기다리는 동안 회복 회전이 안 열리고, 회전이 안 열리면
            #    회복 트리 자체가 안 만들어진다(`control_loop._start_search`) —
            #    즉 답이 안 오면 그만큼 안내가 통째로 멈춘다. `mission_stop` 은 실행
            #    층이 큐를 우회해 인라인 처리하므로 몇 초 안에 안 오면 안 오는 것이다.
            "guide_stop": FleetCmdDriver(self, "mission_stop",
                                         timeout_sec=GUIDE_STOP_TIMEOUT_SEC).bind(cmd_pub),
            # 요청자를 놓쳤다고 **FMS 에 알리는** 통로. 그 홉을 실패로 닫아야 FMS 가
            # 예약을 풀고 로봇을 붙잡는다 — `GuideExec._report_lost` 주석.
            "guide_result": self._publish_cmd_result,
            # 뒷캠 감시 세션. 이게 없으면 `/libi/requester_visible` 발행자가 없어
            # GuideExec 이 "감시 없음 → 그냥 주행"으로 읽고, 사람을 놓쳐도 계속 간다.
            # stop_action: 세션만 닫고 **주행은 안 끊는다** (FleetCmdDriver 주석 참고).
            "guide_watch": FleetCmdDriver(self, "guide_watch",
                                          args_fn=lambda: {"camera": "back"},
                                          timeout_sec=3600.0,
                                          stop_action="follow_stop").bind(cmd_pub),
            # 사람 추종 — libi_perception 의 RemoteControl 이 같은 /fleet_cmd 를 구독해
            # FollowSession 을 켜고 끈다. 세션은 스스로 안 끝나므로(관리자가 멈추거나
            # 회복이 소진될 때까지) 응답 타임아웃을 길게 준다 — 기본 120초면 추종 중에
            # "응답 없음"으로 실패 처리된다.
            #
            # ⚠️ libi_perception 이 안 떠 있으면 이 명령에 아무도 응답하지 않고
            #    timeout_sec 뒤 failure 가 된다. 예전처럼 조용히 안 도는 게 아니라
            #    "추종 실패"로 관제에 뜬다.
            "follow": FleetCmdDriver(self, "follow_admin", timeout_sec=3600.0,
                                     stop_action="follow_stop").bind(cmd_pub),
        }
        # 복귀 5단계가 쓰는 것들.
        #   ⚠️ 팔 홈복귀(return_arm)는 뺐다 — 이 로봇에 팔이 없다(사용자 결정 2026-07-27).
        #      ArmHomeDriver 클래스는 남겨 둔다. 팔 로봇이 붙는 날 되살릴 자리다.
        ret = params.get("returning", {})
        self._drivers["return_entrance"] = FleetCmdDriver(
            self, "goal",
            args_fn=lambda: {"x": entrance_x, "y": entrance_y, "yaw": entrance_yaw},
        ).bind(cmd_pub)
        self._drivers["return_rotate"] = YawGoalDriver(
            FleetCmdDriver(self, "goal").bind(cmd_pub),
            pose_fn=lambda: bb.get(self._bb, Keys.ROBOT_POSE))
        self._drivers["return_entrance_xy"] = (entrance_x, entrance_y)
        self._drivers["return_entrance_yaw"] = entrance_yaw
        self._drivers["return_parking_xy"] = (return_x, return_y)
        # ③ 바퀴를 외부에 넘기기 전에 nav2 목표를 끊는다.
        #
        # ①은 x·y 만 보고 성공하고, 성공한 단계는 stop 을 안 보낸다 — 그래서 입구 goal 이
        # 살아 있는 채로 ④가 시작될 수 있다. 예전엔 `GoToParking` 이 새 goal 로 선점해
        # 줬는데 그 단계를 없애면서 선점 주체가 사라졌다(codex 리뷰, 2026-07-30).
        # `stop` 은 fleet_link 에서 `ros_bridge.cancel_nav()` 로 간다 — 취소할 목표가
        # 없으면 아무 일도 안 일어난다.
        self._drivers["return_nav_release"] = FleetCmdDriver(self, "stop").bind(cmd_pub)
        # ④ 정밀 도킹 — **센서를 파라미터로 고른다.** 되돌리기가 배포 없이 되게 하려고
        #    ArUco 경로 코드를 한 줄도 지우지 않았다.
        #
        #      aruco : `/fleet_cmd{aruco_dock}` 로 robot_agent 의 marker_dock 에 위임
        #              (2026-07-31 실기 성공). 이 저장소에는 구현이 없다
        #      lidar : 이 저장소의 `LidarDockDriver` 가 /scan 을 보고 직접 몬다
        #
        # ⚠️ `aruco` 로 되돌릴 때 `returning.nudge_distance_m` 도 0.03 으로 같이
        #    되돌려야 한다. 라이다는 마지막까지 보므로 ⑤가 0 이다.
        dock_sensor = str(self.declare_parameter(
            "dock_sensor", str(ret.get("dock_sensor", "aruco"))).value).lower()
        if dock_sensor == "lidar":
            from libi_modes.lidar.config import LidarDockConfig
            from libi_modes.ros.lidar_dock_driver import LidarDockDriver, NoopDriver

            lidar_cfg = LidarDockConfig.from_params(
                ret.get("lidar_dock"),
                on_unknown=lambda keys: self.get_logger().warning(
                    f"params.yaml lidar_dock 에 모르는 키가 있다(오타?): {keys}"))
            scan_topic = self.declare_parameter("dock_scan_topic", "/scan").value
            self._drivers["return_dock"] = LidarDockDriver(
                self, str(scan_topic), dock_nudge_topic.lstrip("/"),
                lidar_cfg, rate_hz=lidar_cfg.loop_hz)
            # 라이다 도킹에 뒷캠은 필요 없다. 그대로 두면 camera_sender 가 뒷캠을
            # 1.9Hz → 15fps 로 올려 놓는다 — 쓰지도 않는 캠에 Pi CPU 를 태운다.
            # 노드는 남기고 드라이버만 무해화한다(⑤와 같은 수법, BT 모양 보존).
            self._drivers["return_back_cam"] = NoopDriver()
            self.get_logger().info(
                f"정밀 도킹: 라이다 (stop={lidar_cfg.stop_m:.3f}m "
                f"steer_sign={lidar_cfg.steer_sign:+.0f})")
        else:
            # ⚠️ **답하는 쪽이 하나여야 한다.** 둘이 답하면 **먼저 온 결과로 ⑤가 시작된다** —
            #    즉 로봇이 아직 접근 중인데 후진이 겹친다. 팔에서 이미 밟은 함정이다
            #    (CLAUDE.md `LIBI_ARM_VIA_BT`).
            #
            # ⚠️ 기본값이 `"dock"` 이 **아닌** 이유: 그 이름은 fleet_link 가 이미 잡아
            #    `park_dock`(라인 트레이싱, **실물 미검증**)을 실제로 돌린다. 즉 외부 노드가
            #    없어도 로봇이 움직이고 성공 결과까지 낸다 — 뒷캠 ArUco 인 줄 알고 있는데
            #    다른 알고리즘이 도는, 가장 나쁜 종류의 조용한 실패다.
            #    `aruco_dock` 은 fleet_link 가 모르는 이름이라 `400 알 수 없는 action` 이
            #    **즉시** 돌아온다 → 재시도 소진 → fault → ERROR. 시끄럽게 실패한다.
            #    외부 노드를 붙이는 날: 그 노드가 이 이름을 듣게 하면 끝이다.
            #    (`park_dock` 을 쓰고 싶으면 `-p dock_action:=dock` 로 되돌린다)
            dock_action = self.declare_parameter("dock_action", "aruco_dock").value
            self._drivers["return_dock"] = FleetCmdDriver(
                self, str(dock_action),
                timeout_sec=float(ret.get("aruco_timeout_sec", 180.0)),
            ).bind(cmd_pub)
            # 도킹 동안 **뒷캠을 선택 상태로** 만든다. 안 하면 대기 캠이라 8틱에 한 번
            # (STANDBY_EVERY=8, 15fps → 1.9Hz)만 갱신돼 시각 서보가 못 돈다.
            # `camera_select` 발행자는 follow_node 하나라는 규칙이 있어(follow_node.py) 그쪽에
            # 요청하는 통로를 쓴다 — GuideExec 이 이미 쓰는 액션이다.
            # stop_action 은 `follow_stop`: 세션만 닫고 주행은 안 끊는다.
            self._drivers["return_back_cam"] = FleetCmdDriver(
                self, "guide_watch", args_fn=lambda: {"camera": "back"},
                timeout_sec=3600.0, stop_action="follow_stop").bind(cmd_pub)
            self.get_logger().info(f"정밀 도킹: ArUco ({dock_action})")
        # ⑤ 마지막 몇 cm — 마커가 화각을 벗어나는 구간이라 시간으로 민다.
        #
        # ⚠️ **거리를 `dock_sensor` 에서 유도한다.** params.yaml 에 두 값을 두고
        #    "같이 바꿔라"라고 하면 한쪽만 바꾼 상태가 조용히 만들어진다:
        #      · aruco 인데 0     → 마지막 3cm 를 아무도 안 민다 (접촉 실패)
        #      · lidar 인데 0.03  → 정밀 도킹이 끝난 뒤 또 밀어 도크를 과주행
        #    둘 다 로그도 예외도 없다. 그래서 되돌리기를 파라미터 **하나**로 만든다.
        #    라이다는 마지막까지 노치를 보므로 눈 감고 미는 구간이 없다.
        nudge_distance = (0.0 if dock_sensor == "lidar"
                          else float(ret.get("nudge_distance_m", 0.03)))
        self._drivers["return_nudge"] = NudgeDriver(
            self, dock_nudge_topic,
            distance_m=nudge_distance,
            speed_mps=float(ret.get("nudge_speed_mps", -0.08)))

        # 도킹 탈출 — 나갈 때 **앞으로** 민다(부호가 양수인 것 말고는 ⑤와 같은 드라이버).
        # 벽에서 9cm 안쪽은 costmap 통행불가라 nav2 가 시작 격자에서 경로를 못 낸다.
        #   근거·수치: common/undock.py 머리말
        # ⚠️ 드라이버가 **미는 거리**와 `Undock` 이 **요구하는 거리**는 다르다.
        #    개루프 실이동은 명령값보다 항상 조금 적다(가속 램프·슬립·데드밴드). 같은 값을
        #    쓰면 여유가 0 이라 한 번에 성공하지 못하고 재시도가 붙는다 — 재시도는
        #    기준점을 새로 잡으므로 로봇이 앞으로 세 번 나간다(실측 2026-07-31).
        #    여기서 여유만큼 더 밀고, 판정은 `registry._undock` 이 원래 값으로 한다.
        self._drivers["undock"] = NudgeDriver(
            self, dock_nudge_topic,
            distance_m=(float(ret.get("undock_distance_m", 0.06))
                        + float(ret.get("undock_slip_margin_m", 0.05))),
            speed_mps=abs(float(ret.get("nudge_speed_mps", -0.08))))

        # 갈림길 정점 — 길잡이가 여기서만 잠깐 서서 사람을 확인한다. 모든 노드에서 서면
        # arte2 레인이 0.151~0.601m 라 1~5초마다 멈춰 안내가 계속 끊긴다.
        # 파일이 없으면 빈 목록이고 확인 동작이 그냥 꺼진다.
        navgraph_file = self.declare_parameter("navgraph_file", "").value
        junction_pts = load_junctions(navgraph_file) if navgraph_file else []
        self._drivers["junctions"] = JunctionSet(
            junction_pts, tolerance=params["working"]["arrive_tolerance_m"])
        self.get_logger().info(
            f"갈림길 확인: {len(junction_pts)}개 정점"
            if junction_pts else
            "갈림길 확인 off — navgraph_file 파라미터가 없거나 읽지 못했습니다")

        self.create_subscription(String, result_topic, self._on_result, 10)

        root = tree_mod.build_root(params, self._drivers, self._providers.as_dict())
        self._tree = py_trees.trees.BehaviourTree(root=root)
        self._tree.setup(timeout=15)
        self._root = root

        self._bb = py_trees.blackboard.Client(name="fsm_node")
        for key in (Keys.CURRENT_MODE, Keys.LAST_COMMAND, Keys.ACTIVE_COMMAND,
                    Keys.DISABLED_BRANCHES, Keys.NEXT_MODE, Keys.COMMANDED_MODE):
            self._bb.register_key(key=key, access=Access.WRITE)
        # 주행 목적지는 Topics2BB 가 쓰고 여기선 읽기만 한다 (_nav_args).
        self._bb.register_key(key=Keys.NAV_TARGET, access=Access.READ)
        # 팔 args·id 도 같은 이유로 읽기만 한다. **등록을 빼먹으면 `_read` 가 KeyError 를
        # 삼켜 조용히 None 이 되고**, 드라이버는 "goal 로 옮길 수 없다"로 매번 실패한다.
        self._bb.register_key(key=Keys.ARM_ARGS, access=Access.READ)
        self._bb.register_key(key=Keys.ARM_CMD_ID, access=Access.READ)
        # [2026-07-30] `return_rotate`(YawGoalDriver)가 **이 클라이언트로** pose 를 읽는다
        # (아래 `pose_fn`). 등록이 빠져 있어서 복귀 ②단계가 처음 실제로 도는 순간
        # `AttributeError: client 'fsm_node' does not have read/write access to
        # '/robot_pose'` 로 **FSM 노드가 통째로 죽었다.** 실측 로그로 잡았다.
        #   왜 여태 안 드러났나: 그 경로는 `_YawStep` 이 goal 을 실제로 낼 때만 지난다.
        #   RETURNING 이 ②까지 간 적이 오늘이 처음이다.
        self._bb.register_key(key=Keys.ROBOT_POSE, access=Access.READ)
        self._bb.set(Keys.CURRENT_MODE, BOOT_STATE)
        self._bb.set(Keys.DISABLED_BRANCHES, disabled_branches)
        #: 마지막으로 경고한 "적용 안 된 전이 목표". 같은 요청으로 로그를 도배하지 않는다.
        self._stranded = None

        # 패널 전이를 이 시간만큼은 붙잡는다 — 안 그러면 BT 가 다음 tick 에 되돌려서
        # 관제 화면·LED 에 아무 일도 안 일어난 것처럼 보인다 (state_io.apply_pending 참고).
        # BT 스냅샷 발행 주기(초). 0 이면 매 tick — 예전 동작이다. 기본 0.5s(2Hz)로 낮춘 이유는
        # state_io 의 `_snap_period` 주석에 있다(75노드 JSON 직렬화가 tick 마다 돌아 CPU 34%).
        self._io = StateIO(self, robot_id,
                           manual_hold_sec=float(params.get("manual_hold_sec", 0.0)),
                           snapshot_period_sec=float(self.declare_parameter(
                               "bt_snapshot_period_sec", 0.5).value))
        self._boot_arm_home()
        self.create_timer(1.0 / tick_hz, self._tick)
        self.get_logger().info(
            f"libi_modes up — robot_id={robot_id} tick={tick_hz}Hz boot={BOOT_STATE}")

    def _boot_arm_home(self) -> None:
        """부팅 시 팔을 홈 자세로 한 번 보낸다.

        기동 직후에는 팔이 어떤 자세인지 알 수 없다. 편 채로 주행하면 책장을 친다.
        예전엔 부팅 상태가 `RETURNING` 이라 `ReturnNavigation.initialise()` 가 이걸
        대신 해 줬는데, 부팅을 `IDLE` 로 바꾸면서 그 자리가 없어졌다. **그 안전 장치까지
        같이 없애면 안 되므로** 여기서 명시적으로 한 번 보낸다.

        결과를 기다리지 않는다 — 기다리면 노드 기동이 막힌다. 팔이 없으면(지금이 그렇다)
        실행기가 400 을 돌려주고 끝이며, 그건 정상이다.

        ⚠️ [2026-07-27] `return_arm` 드라이버를 **의도적으로 안 꽂았다**(사용자 결정 —
        이 로봇에 팔이 없다). 그래서 지금 이 함수는 아무 일도 안 한다. **팔이 달린
        로봇을 붙일 때 여기 드라이버를 되살려야 한다** — 함수를 지우지 않고 남겨 둔 것은
        그 자리를 잃지 않기 위해서다.
        """
        driver = self._drivers.get("return_arm")
        if driver is None:
            return
        try:
            driver.go_home()
        except Exception:  # noqa: BLE001 — 팔 때문에 노드가 안 뜨면 안 된다
            self.get_logger().warning("부팅 팔 홈 복귀 명령 전송 실패 — 계속 진행합니다")

    def _nav_args(self, home_location):
        """주행 드라이버가 실행 층(goal)으로 내려보낼 인자.

        FMS 가 `/fleet_cmd{navigate, x,y,yaw}` 로 준 목적지를 blackboard(`nav_target`)에서
        읽는다. 아직 없으면 home 으로 폴백한다 — 좌표 없이 goal 을 보내면 로봇 쪽
        `fleet_link._dispatch` 가 `args["x"]` 에서 KeyError 로 죽는다.
        """
        target = bb.get(self._bb, Keys.NAV_TARGET) if getattr(self, "_bb", None) else None
        if not target:
            self.get_logger().warn(
                f"주행 목적지가 없어 home({home_location}) 으로 폴백합니다")
            return {"name": home_location}
        return {"x": target["x"], "y": target["y"], "yaw": target.get("yaw", 0.0)}

    def _load_params(self, path):
        if not path:
            path = f"{get_package_share_directory('libi_modes')}/config/params.yaml"
        with open(path) as f:
            return yaml.safe_load(f)["libi_modes"]

    def _resolve_disabled_branches(self) -> frozenset:
        """param(disabled_branches) + env(LIBI_DISABLED_BRANCHES) 합집합. 콤마구분.

        모르는 상태 이름은 경고 후 무시. 안전 브랜치(ERROR/RETURNING) 잠금은 경고만 하고
        허용한다 — 디버그 전용 기능이며, 막으면 오히려 그 브랜치를 디버깅할 수 없다.
        """
        raw = str(self.declare_parameter("disabled_branches", "").value or "")
        raw += "," + os.environ.get("LIBI_DISABLED_BRANCHES", "")
        disabled = {s.strip() for s in raw.split(",") if s.strip()}

        unknown = disabled - set(BRANCH_ORDER)
        if unknown:
            self.get_logger().warning(f"disabled_branches 에 모르는 상태(무시): {sorted(unknown)}")
            disabled -= unknown
        if disabled:
            safety = disabled & {"ERROR", "RETURNING"}
            self.get_logger().warning(
                f"[디버그] 잠긴 브랜치: {sorted(disabled)}"
                + (f"  ⚠️ 안전 브랜치 포함: {sorted(safety)}" if safety else ""))
        return frozenset(disabled)

    def _apply_battery_auto(self, params) -> None:
        """[디버그] 배터리로 인한 **자동 상태 전이를 끈다.**
        param(`battery_auto:=false`) 또는 env(`LIBI_BATTERY_AUTO=0`).

        배터리 값이 못 믿을 상태(센서 이상·미배선)일 때 쓴다. 값이 튀면 로봇이 순회
        도중 제멋대로 RETURNING 으로 빠지고, 그러면 다른 어떤 기능도 검증할 수 없다.
        끄면 복귀·순회 시작을 관제 UI 에서 직접 눌러 돌린다.

        임계를 지우지 않고 **닿지 않는 값으로 바꾼다** — `BatteryCheck` 는 비교만 하므로
        트리 구조도 화면(BT 뷰)도 그대로다. 노드가 사라지면 관제 화면이 다른 그림이 된다.

          low(<=)     -1     : 배터리가 음수일 수 없으니 → RETURNING 안 뜸
          charged(>=) 1e9    : 도달 불가 → IDLE 에서 자동 PATROL 안 나감
          ready(>=)   -1     : **항상 참** → CHARGING 에 갇히지 않고 바로 IDLE 로 나온다
                               (여기까지 막으면 도킹하는 순간 아무것도 못 하게 된다)
        """
        raw = self.declare_parameter("battery_auto", True).value
        env = os.environ.get("LIBI_BATTERY_AUTO")
        if env is not None:
            raw = env.strip().lower() not in ("0", "false", "no", "off", "")
        if raw:
            return
        params.setdefault("battery", {}).update(low=-1.0, charged=1e9, ready=-1.0)
        self.get_logger().warning(
            "[디버그] 배터리 자동 전이 OFF — 저전력 복귀·자동 순회 시작이 뜨지 않는다. "
            "복귀는 관제 UI 에서 직접 명령할 것.")

    def _publish_cmd_result(self, cmd_id: str, ok: bool, msg: str) -> None:
        """`/fleet_cmd_result` 로 임의의 명령 결과를 올린다.

        `_publish_arm_result` 와 같은 모양이다 — 다른 건 id 를 **인자로 받는다**는 점뿐.
        팔은 blackboard 에 하나뿐인 `ARM_CMD_ID` 를 쓰지만, 길잡이는 홉마다 id 가 달라서
        부르는 쪽이 자기 id 를 알고 있다.

        `status` 200/500 은 robot_agent 의 `publish_result` 와 같은 모양을 유지하려는
        것이다 — FMS 쪽 소비자(`on_cmd_result`)가 한 형태만 알면 되게.
        """
        if not cmd_id or self._result_pub is None:
            self.get_logger().warning(
                f"명령 결과를 올릴 수 없다 (id={cmd_id!r}) — 관제가 그 홉을 못 닫는다")
            return
        self._result_pub.publish_json({"id": str(cmd_id), "ok": bool(ok),
                                       "status": 200 if ok else 500,
                                       "data": None, "msg": str(msg or "")})
        self.get_logger().info(f"명령 결과 보고 cmd={cmd_id} ok={ok} {msg}")

    def _publish_arm_result(self, ok: bool, msg: str) -> None:
        """팔 동작이 끝났다고 `/fleet_cmd_result` 로 올린다 — **완료를 아는 쪽이 답한다.**

        id 는 `/fleet_cmd` 로 받은 그 명령의 id 다(FMS 는 자기가 보낸 id 로만 대조하므로
        다른 값을 쓰면 조용히 무시되고 주문이 영원히 안 닫힌다).

        `status` 를 200/500 으로 채우는 이유: robot_agent 의 `publish_result` 와 같은 모양을
        유지해야 FMS 쪽 소비자(`on_cmd_result`)가 한 가지 형태만 알면 된다.
        """
        cmd_id = self._read(Keys.ARM_CMD_ID)
        if not cmd_id or self._result_pub is None:
            self.get_logger().warning(
                f"팔 결과를 올릴 수 없다 (id={cmd_id!r}) — 관제 다리가 안 닫힌다")
            return
        self._result_pub.publish_json({"id": cmd_id, "ok": bool(ok),
                                       "status": 200 if ok else 500,
                                       "data": None, "msg": str(msg or "")})
        self.get_logger().info(f"팔 결과 보고 cmd={cmd_id} ok={ok} {msg}")

    def _on_result(self, msg):
        try:
            payload = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        for driver in self._drivers.values():
            handler = getattr(driver, "on_result", None)
            if handler:
                handler(payload)

    def _read(self, key):
        try:
            return self._bb.get(key)
        except KeyError:
            return None

    def _tick(self):
        if self._io.apply_pending():
            self.get_logger().info(f"수동 전이 적용 → {self._read(Keys.CURRENT_MODE)}")

        before = self._read(Keys.CURRENT_MODE)
        self._tree.tick()
        after = self._read(Keys.CURRENT_MODE)
        if after != before:
            self.get_logger().info(f"{before} -> {after}")

        # leaf 가 소비한 명령을 provider 쪽에도 반영 (안 하면 다음 tick 에 되살아난다)
        self._providers.sync_consumed(
            self._read(Keys.LAST_COMMAND), self._read(Keys.ACTIVE_COMMAND))

        # 명령 유래 표시는 **이 tick 안에서만** 유효하다. 남겨 두면 낡은 표시가 우연히 같은
        # 목표를 노린 자율 전이까지 유지 시간을 뚫게 만든다 (blackboard.COMMANDED_MODE 참고).
        self._bb.set(Keys.COMMANDED_MODE, None)

        # ⚠️ 적용 안 된 전이 요청을 드러낸다. next_mode 가 남았는데 상태가 그대로면
        #    **아무도 그 요청을 적용하지 않았다.** PATROL·WORKING 은 다음 tick 에 이 요청을
        #    재시도할 통로가 없어(request_transition.py 클래스 주석) 그대로 유실된다.
        #    이 경고가 없던 동안 배차·터치가 조용히 사라져 원인을 찾는 데 오래 걸렸다.
        pending = self._read(Keys.NEXT_MODE)
        if pending and after == before:
            if pending != self._stranded:
                self.get_logger().warning(
                    f"전이 요청이 적용되지 않았다: {after} -> {pending} "
                    f"(패널 유지 시간 중이거나, 브랜치가 RequestTransition 에 닿지 못했다)")
                self._stranded = pending
        else:
            self._stranded = None

        self._io.publish(self._root)


def main(args=None):
    rclpy.init(args=args)
    node = FsmNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Ctrl+C — 미션 FSM 을 정리합니다")
    except Exception as exc:      # noqa: BLE001 — 아래에서 진짜 오류만 다시 던진다
        # rclpy 는 SIGINT/SIGTERM 에 기본 핸들러를 걸어 전역 컨텍스트를 비동기로 종료한다.
        # 그 타이밍에 따라 spin() 이 던지는 예외 타입이 달라진다(ExternalShutdownException,
        # RCLError, get_global_executor() 가 None 이 된 뒤의 AttributeError …).
        # 타입을 나열해 잡으면 두더지잡기라, **컨텍스트가 이미 죽었는가**만 본다.
        #   죽었다 → 정상 종료의 부산물이니 삼킨다. 안 그러면 트레이스백이 로그를 덮어
        #            "왜 죽었나"(예: 누가 SIGTERM 을 보냈나)가 가려진다.
        #   살아있다 → 진짜 내부 오류다. 다시 던진다.
        if rclpy.ok():
            raise
        node.get_logger().info(f"종료 신호 수신({type(exc).__name__}) — 미션 FSM 을 정리합니다")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
