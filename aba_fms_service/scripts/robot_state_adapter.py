#!/usr/bin/env python3
"""로봇 위치 → fleet_node 가 읽는 `RobotState` 로 변환하는 서버측 어댑터.

## 왜 필요한가
`fleet_node` 는 `/robot_state`(`rmf_fleet_msgs/RobotState`)를 구독해서 "어떤 로봇이 어디에
있는가"를 안다. 그런데 로봇(및 sim)은 그 타입을 발행하지 않는다 — `amcl_pose`,
`battery/percent`, `fleet_status` 만 낸다. 그래서 **로봇→fleet_node 고리가 비어 있고,
fleet_node 가 로봇을 0대로 본다 → 배차가 아예 불가능**했다.

이 어댑터가 그 틈을 메운다. 이미 domain_bridge 가 서버 도메인(86)으로 옮겨 놓은
`/pinky3/amcl_pose` 와 `/pinky3/battery/percent` 를 읽어 `/robot_state` 로 재발행한다.
**로봇은 무수정** — 서버에서만 돈다.

## 이름 규칙
`RobotState.name` 은 `FsmState.robot_id` 와 같은 키여야 fleet_node 안에서 상태가 매칭된다
(fleet_node.cpp 주석 참고). 스크립트들이 로봇을 `pinky3` 로 부르므로 그 값을 기본으로 쓴다.

## 실행 (서버, 도메인 86)
    ROS_DOMAIN_ID=86 python3 aba_fms_service/scripts/robot_state_adapter.py --robot pinky3
"""

from __future__ import annotations

import argparse
import math

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from rmf_fleet_msgs.msg import Location, RobotMode, RobotState
from std_msgs.msg import Float32

PUBLISH_HZ = 2.0

# ⚠️ amcl_pose 는 TRANSIENT_LOCAL 로 발행된다(브릿지가 그대로 넘긴다). 기본 QoS(VOLATILE)로
# 구독하면 **매칭 자체가 안 돼** 마지막 위치조차 못 받는다. AMCL 은 주기 발행이 아니라
# 갱신 때만 내보내므로, latch 된 값을 받으려면 반드시 durability 를 맞춰야 한다.
LATCHED_QOS = QoSProfile(
    depth=10,
    history=HistoryPolicy.KEEP_LAST,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def yaw_from_quat(z: float, w: float) -> float:
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


def node_safe(name: str) -> str:
    """ROS 노드 이름으로 쓸 수 있게 정리한다.

    노드 이름은 영숫자와 `_` 만 허용된다 — DB 의 로봇 이름은 `Pinky-3` 처럼 `-` 를 포함할
    수 있어서 그대로 쓰면 `InvalidNodeNameException` 으로 죽는다.
    (발행하는 `RobotState.name` 은 원래 이름을 그대로 쓴다 — fleet_node 매칭 키이므로.)
    """
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return safe or "robot"


class RobotStateAdapter(Node):
    def __init__(self, robot: str, prefix: str) -> None:
        super().__init__(f"robot_state_adapter_{node_safe(robot)}")
        self._robot = robot
        self._pose: tuple[float, float, float] | None = None
        self._battery = 100.0
        self._seq = 0

        self.create_subscription(
            PoseWithCovarianceStamped, f"{prefix}/amcl_pose", self._on_pose, LATCHED_QOS
        )
        self.create_subscription(
            Float32, f"{prefix}/battery/percent", self._on_battery, 10
        )
        self._pub = self.create_publisher(RobotState, "/robot_state", 10)
        self.create_timer(1.0 / PUBLISH_HZ, self._tick)

        self.get_logger().info(
            f"어댑터 시작: {prefix}/amcl_pose → /robot_state (name={robot})"
        )

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation.z, p.orientation.w))

    def _on_battery(self, msg: Float32) -> None:
        self._battery = float(msg.data)

    def _tick(self) -> None:
        # 위치를 아직 못 받았으면 발행하지 않는다 — 좌표 0,0 인 유령 로봇이 생기면 안 된다.
        if self._pose is None:
            return
        x, y, yaw = self._pose

        loc = Location()
        loc.t = self.get_clock().now().to_msg()
        loc.x = float(x)
        loc.y = float(y)
        loc.yaw = float(yaw)
        loc.level_name = "L1"

        msg = RobotState()
        msg.name = self._robot
        msg.model = "pinky"
        msg.task_id = ""
        msg.seq = self._seq
        self._seq += 1
        # 모드는 fleet_node 가 FsmState 로 따로 갱신한다. 여기서는 중립값만 채운다.
        msg.mode = RobotMode(mode=RobotMode.MODE_IDLE)
        msg.battery_percent = float(self._battery)
        msg.location = loc
        msg.path = []
        self._pub.publish(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", default="pinky3", help="RobotState.name (FsmState.robot_id 와 같아야 함)")
    ap.add_argument("--prefix", default=None, help="브릿지 토픽 접두사 (기본 /<robot>)")
    args = ap.parse_args()
    prefix = args.prefix or f"/{args.robot}"

    rclpy.init()
    node = RobotStateAdapter(args.robot, prefix)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
