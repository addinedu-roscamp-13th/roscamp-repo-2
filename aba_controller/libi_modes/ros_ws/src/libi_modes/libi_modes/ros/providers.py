"""구독한 ROS 토픽 → Topics2BB 가 매 tick 읽어가는 콜러블.

구독 콜백은 값을 캐시에 넣기만 하고 즉시 리턴한다. 트리는 캐시를 읽을 뿐이라
`update()` 안에서 블로킹이 일어나지 않는다 — tick 이 멈추면 트리 전체가 멈춘다.

## 지금 실제로 연결된 입력과 아직 발행자가 없는 입력

| provider | 소스 | 상태 |
|---|---|---|
| `battery_percent` | `/battery/percent` (Float32) | 연결됨 — pinky_bringup 의 battery_publisher |
| `last_command` / `active_command` | `/fleet_cmd` (String JSON) | 연결됨 — FMS 가 브릿지로 내려보냄 |
| `is_docked` | `docked_topic` (Bool) | 연결됨 — sim: `sim_dock_confirm.py`(주차장 반경) / 실물: 라인 추종 주차 |
| `robot_pose` | `/amcl_pose` (PoseWithCovarianceStamped) | 연결됨 — nav2 AMCL. `NavigationExec` 의 도착 판정 |
| `fault` | `fault_topic` (Bool) | **발행자 없음** — 기본 False |
| `ui_last_touch_at` | `ui_touch_topic` (Float64) | **발행자 없음** — libi_gui 가 붙으면 채워진다 |

발행자가 없는 값을 지어내지 않는다. `is_docked` 가 None 이면 `BatteryCheck` 의
`require_docked` 가드가 통과하지 않으므로, 도킹 정보가 없는 로봇은 배터리만으로
자동 전이하지 않는다 — 모르는 상태에서 움직이는 것보다 안전한 쪽이다.

## 소비 규약 — Topics2BB 가 매 tick 덮어쓴다는 점이 중요하다

leaf 들은 blackboard 의 `last_command` / `active_command` 를 다 쓰고 나면 None 으로
비운다(안 비우면 같은 전이가 다음 tick 에 재발화). 그런데 Topics2BB 는 매 tick
provider 값을 다시 써넣으므로, provider 가 계속 같은 값을 돌려주면 비운 게 되살아난다.

그래서 provider 는 값을 유지하고, **노드가 매 tick 후 blackboard 를 되읽어 대조한다**
(`sync_consumed()`): leaf 가 비웠으면 provider 도 비운다.

읽자마자 지우는 one-shot 은 안 된다 — `CommandListener` 는 자기 매핑에 없는 명령을
**일부러 남겨둔다**(전이 후 다른 브랜치가 받을 수 있으므로). one-shot 이면 그 명령이
현재 상태에서 조용히 사라진다.
"""
import json
import time

from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, Float32, Float64, String

#: `/amcl_pose` 는 TRANSIENT_LOCAL 로 발행된다 — 기본 QoS(VOLATILE)로 구독하면
#: 아무것도 못 받는다. 조용히 위치가 안 오면 NavigationExec 이 영영 도착 판정을
#: 못 하므로, 여기서 QoS 를 틀리면 증상이 "로봇이 안 멈춘다" 로 나타난다.
_LATCHED = QoSProfile(depth=1)
_LATCHED.durability = DurabilityPolicy.TRANSIENT_LOCAL
_LATCHED.reliability = ReliabilityPolicy.RELIABLE


class RosProviders:
    def __init__(self, node, *, battery_topic="battery/percent", docked_topic="is_docked",
                 fault_topic="fault", cmd_topic="fleet_cmd", ui_touch_topic="ui_last_touch_at",
                 pose_topic="/amcl_pose",
                 mission_actions=("goal", "goto", "home", "mission_start"),
                 nav_actions=("navigate",),
                 arm_actions=("perform_action",),
                 follow_actions=("follow_admin",)):
        self._node = node
        self._log = node.get_logger()
        self._mission_actions = set(mission_actions)
        # BT 층 주행 명령. mission_actions 와 달리 **목적지를 함께 싣고 온다.**
        #   mission_actions : 실행 층 액션(goal/goto/home/…)이 지나가는 걸 보고
        #                     "주행 중이구나" 로만 표시한다 (인자는 안 본다)
        #   nav_actions     : FMS 가 BT 에게 직접 주는 명령 — args 의 목적지를 저장한다
        self._nav_actions = set(nav_actions)
        # 팔 명령은 이름을 그대로 active_command 로 쓴다 — ArmExec 의 handles 와 맞춰야 한다
        # (working_actions.ArmExec: handles={"perform_action"}).
        self._arm_actions = set(arm_actions)
        # 추종도 같은 규칙 — FollowExec.handles={"follow_admin"} 과 이름이 같아야 한다.
        # ⚠️ 이 분류가 없으면 follow_admin 이 last_command 로 새어 나가 **active_command 가
        #    되지 않고**, FollowExec 은 ACTIVE_COMMAND 만 보므로 드라이버를 꽂아도
        #    영영 안 잡힌다 (ArmExec 이 예전에 같은 이유로 죽어 있었다).
        self._follow_actions = set(follow_actions)

        self._battery = None
        self._docked = None
        self._fault = False
        self._last_command = None
        self._active_command = None
        self._nav_target = None
        self._robot_pose = None
        self._command_received_at = 0.0
        self._ui_last_touch_at = 0.0

        node.create_subscription(Float32, battery_topic, self._on_battery, 10)
        node.create_subscription(Bool, docked_topic, self._on_docked, 10)
        node.create_subscription(Bool, fault_topic, self._on_fault, 10)
        node.create_subscription(String, cmd_topic, self._on_cmd, 10)
        node.create_subscription(Float64, ui_touch_topic, self._on_ui_touch, 10)
        node.create_subscription(PoseWithCovarianceStamped, pose_topic, self._on_pose, _LATCHED)

    # ── 콜백 (캐시에 담기만 한다) ──────────────────────────────────────────────

    def _on_battery(self, msg):
        self._battery = float(msg.data)

    def _on_docked(self, msg):
        self._docked = bool(msg.data)

    def _on_fault(self, msg):
        self._fault = bool(msg.data)

    def _on_ui_touch(self, msg):
        # payload 값은 신뢰하지 않는다 — libi_gui 가 다른 머신(노트북/sim)이면 monotonic 시계가
        # 서로 다르다. 수신 시점에 로봇 자기 monotonic 으로 찍어야 UiSessionTimer(time.monotonic)와
        # 같은 기준으로 비교된다. 메시지가 오는 것 자체가 "방금 터치" 신호다.
        self._ui_last_touch_at = time.monotonic()

    def _on_cmd(self, msg):
        """FMS 명령을 전이 트리거와 실행 커맨드 둘로 가른다.

        `goto`/`goal` 같은 주행 액션은 WORKING 브랜치가 실행할 `active_command` 이고,
        `stop_request`/`task_done` 같은 것은 상태를 바꾸는 `last_command` 다.
        한 토픽으로 오므로 여기서 나눈다.
        """
        try:
            cmd = json.loads(msg.data)
        except (ValueError, TypeError):
            self._log.warning(f"fleet_cmd 파싱 실패: {msg.data[:120]!r}")
            return
        action = str(cmd.get("action", "")).strip()
        if not action:
            return
        # ⚠️ **monotonic 이어야 한다.** 이 값을 읽는 곳은 CommandTimeout 하나뿐이고,
        #    그쪽은 time.monotonic() 으로 현재 시각을 잰다. 여기에 ROS 시계(=epoch 초)를
        #    찍으면 `max(last_activity, received_at)` 이 항상 epoch 을 고르고,
        #    `now - since` 가 -17억이 되어 **타임아웃이 영원히 성립하지 않는다**
        #    (실측: monotonic 24,328 vs epoch 1,785,051,205).
        #    바로 위 _on_ui_touch 가 같은 이유로 이미 monotonic 을 쓴다.
        self._command_received_at = time.monotonic()
        if action in self._nav_actions:
            # FMS 가 준 BT 층 주행 명령 — 목적지를 함께 저장한다.
            # NavigationExec 의 드라이버가 이 값을 읽어 실행 층(goal)으로 내려보낸다.
            args = cmd.get("args") or {}
            try:
                self._nav_target = {
                    "x": float(args["x"]),
                    "y": float(args["y"]),
                    "yaw": float(args.get("yaw", 0.0)),
                }
            except (KeyError, TypeError, ValueError):
                self._log.warning(f"navigate 명령에 좌표가 없다: {args!r}")
                return
            self._active_command = "navigate"
        elif action in self._mission_actions:
            self._active_command = "navigate"      # WorkingBranch 의 NavigationExec 이 받는다
        elif action in self._follow_actions:
            self._active_command = action
        elif action in self._arm_actions:
            # 팔 명령도 **실행 커맨드**다 — WorkingBranch 의 ArmExec 이 받는다.
            # 예전엔 이 갈래가 없어서 perform_action 이 last_command 로 새어 나갔고,
            # ArmExec 은 ACTIVE_COMMAND 만 보므로 **영원히 안 잡혔다**.
            # 이름을 그대로 쓴다 — 매핑을 두면 ArmExec.handles 와 어긋날 여지가 생긴다.
            self._active_command = action
        else:
            self._last_command = action

    def _on_pose(self, msg):
        p = msg.pose.pose.position
        self._robot_pose = {"x": float(p.x), "y": float(p.y)}

    # ⚠️ ROS 시계로 시각을 찍는 헬퍼(`_now`)는 삭제했다. 이 클래스가 내보내는 시각 값
    #    (ui_last_touch_at, command_received_at)은 **전부 time.monotonic 기준**이어야 한다 —
    #    읽는 쪽(UiSessionTimer, CommandTimeout)이 monotonic 으로 현재 시각을 재기 때문이다.
    #    둘을 섞을 통로를 아예 없애 같은 실수가 다시 나지 않게 한다.

    # ── Topics2BB 인터페이스 ─────────────────────────────────────────────────

    def sync_consumed(self, bb_last_command, bb_active_command):
        """tick 직후 blackboard 값을 받아 provider 를 맞춘다.

        leaf 가 비웠으면(None) provider 도 비운다. 아직 남아 있으면(매핑 안 된 명령,
        실행 중인 커맨드) 그대로 둔다. 이걸 안 하면 Topics2BB 가 다음 tick 에 값을
        되살려서 같은 전이가 무한 반복된다.
        """
        if bb_last_command is None:
            self._last_command = None
        if bb_active_command is None:
            self._active_command = None

    def as_dict(self):
        return {
            "battery_percent": lambda: self._battery,
            "is_docked": lambda: self._docked,
            "fault": lambda: self._fault,
            "last_command": lambda: self._last_command,
            "ui_last_touch_at": lambda: self._ui_last_touch_at,
            "active_command": lambda: self._active_command,
            "nav_target": lambda: self._nav_target,
            "robot_pose": lambda: self._robot_pose,
            "command_received_at": lambda: self._command_received_at,
        }
