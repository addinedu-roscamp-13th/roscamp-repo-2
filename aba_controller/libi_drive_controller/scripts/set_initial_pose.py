#!/usr/bin/env python3
"""AMCL 이 뜬 뒤 초기 자세를 한 번 넣어 준다. **로봇 도메인에서 실행한다.**

## 왜 필요한가 — 파라미터만으로는 안 잡힌다

`nav2_params_sim.yaml` 에 이미 이렇게 적혀 있다:

    set_initial_pose: true
    initial_pose: {x: 0.0, y: 0.0, yaw: 0.0}

그런데 sim 을 띄우면 **AMCL 이 엉뚱한 곳에 수렴한다.** 실측:

    odom : (+0.693, -0.295)  yaw -98.6°     ← 실제에 가까움
    amcl : (+0.716, -1.142)  yaw -99.1°     ← y 가 0.847 m 어긋남

yaw 는 맞고 **y 만** 틀렸다. arte2 는 평행한 긴 복도가 많아, 스캔만으로는 복도를 따라
미끄러진 자세가 제자리와 거의 같아 보인다. 초기 추정이 조금만 어긋나도 그쪽으로
수렴해 버린다.

`set_initial_pose` 는 AMCL 이 **활성화되는 시점**에 적용된다. 그 순간에 아직
`odom -> base_link` TF 가 없거나 로봇이 이미 움직였다면, 그걸 기준으로 계산한
`map -> odom` 이 틀어진다. 그래서 **AMCL 이 확실히 뜬 뒤에 한 번 더** 넣어 준다.

같은 걸 손으로 하면 이렇다(rviz 의 "2D Pose Estimate" 와 같은 토픽):

    ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped ...

## 언제 실행하나

**로봇이 움직이기 전에.** sim.sh 는 이걸 `sim-dock`·`sim-batt` 보다 **먼저** 띄운다 —
그 둘이 `/is_docked` 와 배터리를 내야 FSM 이 IDLE 을 벗어나므로, 그 전에 끝내면
로봇은 아직 제자리에 있다.

이미 움직인 뒤에 실행하면 **오히려 위치를 망친다** (제자리에 있다고 우기게 된다).

## 실행

    ROS_DOMAIN_ID=90 python3 set_initial_pose.py                 # 스폰 자세 (0,0,0)
    ROS_DOMAIN_ID=90 python3 set_initial_pose.py --dock 주차장    # navgraph 정점 이름으로
    ROS_DOMAIN_ID=90 python3 set_initial_pose.py --x 0.5 --y -1.2 --yaw 1.57
"""

from __future__ import annotations

import argparse
import math
import time

import rclpy
import yaml
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

#: AMCL 이 뜰 때까지 기다리는 상한(초). 넘으면 그냥 보내고 끝낸다 — 여기서 막히면
#: sim 기동 전체가 멈춘 것처럼 보인다.
WAIT_AMCL_SEC = 90.0
#: 초기 추정의 불확실성. 너무 작게 주면 파티클이 한 점에 몰려 실제와 어긋났을 때
#: 회복하지 못하고, 너무 크면 다시 엉뚱한 곳으로 수렴한다.
COV_XY = 0.05
COV_YAW = 0.05

LATCHED = QoSProfile(depth=1)
LATCHED.durability = DurabilityPolicy.TRANSIENT_LOCAL
LATCHED.reliability = ReliabilityPolicy.RELIABLE


def dock_xy_from_navgraph(navgraph: str, name: str) -> tuple[float, float]:
    with open(navgraph) as f:
        data = yaml.safe_load(f)
    for v in data["levels"]["L1"]["vertices"]:
        meta = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
        if str(meta.get("name", "")) == name:
            return float(v[0]), float(v[1])
    raise SystemExit(f"navgraph 에 '{name}' 정점이 없습니다: {navgraph}")


class InitialPoseSetter(Node):
    def __init__(self, x: float, y: float, yaw: float, repeat: int) -> None:
        super().__init__("set_initial_pose")
        self._x, self._y, self._yaw = x, y, yaw
        self._left = repeat
        self._pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self._seen_amcl = False
        # AMCL 이 자기 추정을 내기 시작하면 그때가 "떴다"는 신호다.
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose",
                                 self._on_amcl, LATCHED)
        self._deadline = time.time() + WAIT_AMCL_SEC
        self.create_timer(1.0, self._tick)
        self.get_logger().info(
            f"[init-pose] AMCL 대기 중 — 넣을 자세 ({x:.3f}, {y:.3f}) yaw={math.degrees(yaw):.1f}°")

    def _on_amcl(self, _msg) -> None:
        self._seen_amcl = True

    def _tick(self) -> None:
        if not self._seen_amcl and time.time() < self._deadline:
            return
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = self._x
        msg.pose.pose.position.y = self._y
        msg.pose.pose.orientation.z = math.sin(self._yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(self._yaw / 2.0)
        cov = [0.0] * 36
        cov[0] = cov[7] = COV_XY
        cov[35] = COV_YAW
        msg.pose.covariance = cov
        self._pub.publish(msg)
        self._left -= 1
        self.get_logger().info(
            f"[init-pose] 초기 자세 발행 ({self._x:.3f}, {self._y:.3f}) — 남은 {self._left}회")
        if self._left <= 0:
            self.get_logger().info("[init-pose] 완료")
            raise SystemExit(0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=float)
    ap.add_argument("--y", type=float)
    ap.add_argument("--yaw", type=float, default=0.0, help="라디안")
    ap.add_argument("--dock", help="navgraph 정점 이름 (x/y 대신)")
    ap.add_argument("--navgraph",
                    default="aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml")
    #: 한 번만 보내면 AMCL 이 막 뜬 직후라 놓칠 수 있다. 몇 번 더 보낸다.
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    if args.dock:
        x, y = dock_xy_from_navgraph(args.navgraph, args.dock)
    else:
        x, y = (args.x or 0.0), (args.y or 0.0)

    rclpy.init()
    node = InitialPoseSetter(x, y, args.yaw, args.repeat)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
