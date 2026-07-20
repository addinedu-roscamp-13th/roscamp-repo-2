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

from libi_modes import tree as tree_mod
from libi_modes.blackboard import Keys
from libi_modes.registry import BRANCH_ORDER
from libi_modes.ros.fleet_cmd_driver import ArmHomeDriver, FleetCmdDriver
from libi_modes.ros.providers import RosProviders
from libi_modes.ros.state_io import StateIO

BOOT_STATE = "RETURNING"     # 전이 박스의 [*] -> RETURNING


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

        # [디버그] 잠글 상태 브랜치 — 콤마구분. ROS param `disabled_branches` 또는
        # env `LIBI_DISABLED_BRANCHES` (fsm-bt.sh --disable 가 env 로 넘긴다). 기본 빈 값.
        disabled_branches = self._resolve_disabled_branches()

        params = self._load_params(params_file)

        cmd_pub = _CmdPublisher(self, cmd_topic)
        self._providers = RosProviders(self, cmd_topic=cmd_topic)

        # 액션별 드라이버. 전부 같은 /fleet_cmd 통로를 쓰고 결과는 id 로 갈린다.
        self._drivers = {
            "patrol": FleetCmdDriver(self, "mission_start").bind(cmd_pub),
            "security_patrol": FleetCmdDriver(self, "mission_start").bind(cmd_pub),
            "nav": FleetCmdDriver(self, "goto",
                                  args_fn=lambda: {"name": home_location}).bind(cmd_pub),
            "arm": FleetCmdDriver(self, "perform_action").bind(cmd_pub),
            "return_dock": FleetCmdDriver(self, "home").bind(cmd_pub),
        }
        self._drivers["return_arm"] = ArmHomeDriver(
            FleetCmdDriver(self, "arm_home").bind(cmd_pub))

        self.create_subscription(String, result_topic, self._on_result, 10)

        root = tree_mod.build_root(params, self._drivers, self._providers.as_dict())
        self._tree = py_trees.trees.BehaviourTree(root=root)
        self._tree.setup(timeout=15)
        self._root = root

        self._bb = py_trees.blackboard.Client(name="fsm_node")
        for key in (Keys.CURRENT_MODE, Keys.LAST_COMMAND, Keys.ACTIVE_COMMAND,
                    Keys.DISABLED_BRANCHES):
            self._bb.register_key(key=key, access=Access.WRITE)
        self._bb.set(Keys.CURRENT_MODE, BOOT_STATE)
        self._bb.set(Keys.DISABLED_BRANCHES, disabled_branches)

        self._io = StateIO(self, robot_id)
        self.create_timer(1.0 / tick_hz, self._tick)
        self.get_logger().info(
            f"libi_modes up — robot_id={robot_id} tick={tick_hz}Hz boot={BOOT_STATE}")

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

        self._io.publish(self._root)


def main(args=None):
    rclpy.init(args=args)
    node = FsmNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
