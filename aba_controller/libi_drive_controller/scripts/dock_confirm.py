#!/usr/bin/env python3
"""주차장에 도착했는지 위치로 판정해 `/is_docked` 를 발행한다. **로봇 도메인에서 실행한다.**

## 왜 필요한가

`libi_modes` 의 ReturningBranch > ReturnNavigation 은 도킹 성공 판정에
**`blackboard.is_docked` 를 요구한다.** 그 주석이 이유를 이렇게 적어 두었다:

    `dock_driver.poll() == "success"` 는 **명령이 접수됐다**는 뜻일 뿐이다
    (`send_nav_goal` 은 도착을 안 기다린다). 로봇이 실제로 도착했는지는 아무 말도
    안 한다. 그래서 진짜 확인 신호를 추가로 요구한다 — "명령 접수됨"을 "도착함"으로
    믿지 않으려고.

그런데 **아무도 `/is_docked` 를 발행하지 않았다.** 그래서 게이트가 영영 안 열리고
`RETURNING -> CHARGING` 전이가 일어나지 않았다. 부팅은 무조건 `RETURNING` 으로
시작하므로, **로봇이 첫 상태에서 한 발짝도 못 나갔다.**

## sim 전용이 아니다 — 실물에서도 이걸 쓴다 (2026-07-22)

원래는 sim 전용으로 만들었다. 실물은 정밀 주차(테이프 추종) 폐루프가 성공 신호를
낼 거라고 봤기 때문이다. 그런데 **그 경로가 배선돼 있지 않다** — BT 의 복귀 드라이버는
`goal`(주차장 좌표)을 보낼 뿐이고 `park_dock` 을 부르는 곳이 없다
(자세한 것은 `scripts/drive-pi/dock/README.md` 의 미결 1~4).

그래서 지금은 실물도 sim 과 같은 판정을 쓴다: **주차장 정점 반경 안에 들어오면 도착.**
정밀 주차가 붙으면 그때 신호 주체만 바꾸면 된다 — **BT 는 한 줄도 안 바뀐다.**

    지금      주차장 정점 반경 안        ->  /is_docked   (이 파일, sim·실물 공통)
    나중에    테이프 추종 폐루프 성공    ->  /is_docked

## 판정

로봇 위치(`/amcl_pose`)가 주차장 정점에서 `--radius` 안에 있으면 도착으로 본다.
벗어나면 즉시 false 로 되돌린다 — 한 번 true 로 굳으면 다음 복귀가 "이미 도킹됨"으로
잘못 판정된다.

`is_docked` 는 상태이지 사건이므로 **주기적으로 계속 발행**한다. TRANSIENT_LOCAL 로 두어
`libi_modes` 가 늦게 떠도 마지막 값을 바로 받는다.

## 실행

    ROS_DOMAIN_ID=90 python3 dock_confirm.py --dock 주차장
    ROS_DOMAIN_ID=119 python3 dock_confirm.py --x -0.001 --y -0.033
"""

from __future__ import annotations

import argparse
import math

import rclpy
import yaml
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool

#: 도킹으로 볼 거리(m). arte2 는 1.26x2.16m 축소맵이라 넉넉히 잡아도 이 정도다.
#: 정밀 도킹이 없는 sim 에서는 nav2 가 데려다 준 자리가 곧 도킹 위치다.
DEFAULT_RADIUS_M = 0.12
#: 발행 주기(초). 상태이므로 계속 낸다.
PUBLISH_PERIOD_SEC = 0.5

#: amcl_pose 는 TRANSIENT_LOCAL 로 발행된다 — 기본 QoS(VOLATILE)로 구독하면 아무것도 못 받는다.
LATCHED = QoSProfile(depth=1)
LATCHED.durability = DurabilityPolicy.TRANSIENT_LOCAL
LATCHED.reliability = ReliabilityPolicy.RELIABLE


def dock_xy_from_navgraph(navgraph: str, name: str) -> tuple[float, float]:
    """navgraph 에서 정점 이름으로 좌표를 찾는다."""
    with open(navgraph) as f:
        data = yaml.safe_load(f)
    for vertex in data["levels"]["L1"]["vertices"]:
        meta = vertex[2] if len(vertex) > 2 and isinstance(vertex[2], dict) else {}
        if str(meta.get("name", "")) == name:
            return float(vertex[0]), float(vertex[1])
    raise SystemExit(f"navgraph 에 '{name}' 정점이 없습니다: {navgraph}")


class SimDockConfirm(Node):
    def __init__(self, dock_x: float, dock_y: float, radius: float, topic: str) -> None:
        super().__init__("dock_confirm")
        self._dock = (dock_x, dock_y)
        self._radius = radius
        self._pose: tuple[float, float] | None = None
        self._docked: bool | None = None

        self._pub = self.create_publisher(Bool, topic, LATCHED)
        self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._on_pose, LATCHED
        )
        self.create_timer(PUBLISH_PERIOD_SEC, self._tick)
        self.get_logger().info(
            f"[sim-dock] 주차장 ({dock_x:.3f}, {dock_y:.3f}) 반경 {radius:.2f}m → {topic}"
        )

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        self._pose = (p.x, p.y)

    def _tick(self) -> None:
        if self._pose is None:
            return                       # 아직 위치를 모른다 — 아무 주장도 하지 않는다
        dist = math.hypot(self._pose[0] - self._dock[0], self._pose[1] - self._dock[1])
        docked = dist <= self._radius
        if docked != self._docked:       # 바뀔 때만 로그 (매 0.5초 도배 방지)
            self.get_logger().info(
                f"[sim-dock] {'도킹' if docked else '이탈'} — 주차장까지 {dist:.3f}m"
            )
            self._docked = docked
        self._pub.publish(Bool(data=docked))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dock", default="주차장", help="navgraph 정점 이름")
    ap.add_argument("--x", type=float, help="정점 대신 좌표를 직접 줄 때")
    ap.add_argument("--y", type=float)
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M)
    ap.add_argument("--topic", default="/is_docked")
    ap.add_argument(
        "--navgraph",
        default="aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml",
        help="저장소 루트 기준 상대경로 또는 절대경로",
    )
    args = ap.parse_args()

    if args.x is not None and args.y is not None:
        dock_x, dock_y = args.x, args.y
    else:
        dock_x, dock_y = dock_xy_from_navgraph(args.navgraph, args.dock)

    rclpy.init()
    node = SimDockConfirm(dock_x, dock_y, args.radius, args.topic)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
