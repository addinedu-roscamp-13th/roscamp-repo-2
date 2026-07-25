#!/usr/bin/env python3
"""[sim 전용] 배터리 잔량을 흉내내 `/battery/percent` 를 발행한다. **로봇 도메인에서 실행한다.**

## 왜 필요한가

`libi_modes` 의 FSM 은 배터리로 두 전이를 판단한다:

    CHARGING → IDLE                     battery >= ready (40%)
    {IDLE, PATROL, SECURITY} → RETURNING  battery <= low   (15%)

sim 월드에는 배터리가 없다. 실물은 `pinky_bringup` 의 battery_publisher 가 INA219 를
읽어 발행하지만 그건 하드웨어가 있어야 돈다. 발행자가 없으면 `battery_percent` 가
None 으로 남고 `BatteryCheck` 가 영영 통과하지 않아 **로봇이 CHARGING 에서 안 나온다** —
`RETURNING → CHARGING` 을 고쳐 놓고도 배차 대상(IDLE/PATROL)이 되지 못한다.

## 모델

일부러 단순하게 둔다. 목적은 배터리를 정밀하게 모사하는 게 아니라 **FSM 의 배터리
전이를 sim 에서 실제로 밟아 보는 것**이다.

    도킹 중(`/is_docked` true)   →  `--charge` %/분 만큼 오른다
    그 외                        →  `--drain`  %/분 만큼 내린다

`--drain` 기본값은 0 이다. 시험이 예고 없이 배터리 저하 복귀로 끊기면 원인을 찾느라
시간을 버린다 — 배터리 시험을 할 때만 켜서 쓴다.

## 배터리 저하 시나리오 시험

`/sim_battery/set` 에 값을 밀어 넣으면 즉시 그 값이 된다. 배달 도중 15% 를 만들어
복귀 선점이 실제로 일어나는지 볼 때 쓴다:

    ros2 topic pub --once /sim_battery/set std_msgs/msg/Float32 '{data: 10.0}'

## 실행

    ROS_DOMAIN_ID=90 python3 sim_battery.py                 # 60% 고정(기본)
    ROS_DOMAIN_ID=90 python3 sim_battery.py --start 30       # 충전부터 보고 싶을 때
    ROS_DOMAIN_ID=90 python3 sim_battery.py --drain 5        # 5%/분씩 소모
"""

from __future__ import annotations

import argparse

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, Float32

#: 발행 주기(초). 실물 battery_publisher 와 같은 정도면 충분하다.
PUBLISH_PERIOD_SEC = 1.0

#: is_docked 는 TRANSIENT_LOCAL 로 발행된다(dock_confirm.py) — 기본 QoS 로 구독해도
#: 받긴 하지만, 늦게 떠도 마지막 값을 바로 받도록 맞춰 둔다.
LATCHED = QoSProfile(depth=1)
LATCHED.durability = DurabilityPolicy.TRANSIENT_LOCAL
LATCHED.reliability = ReliabilityPolicy.RELIABLE


def step_percent(percent: float, docked: bool, charge_per_min: float,
                 drain_per_min: float, dt_sec: float) -> float:
    """다음 잔량. 0~100 으로 자른다.

    순수 함수로 빼 두어 노드 없이도 시험할 수 있다.
    """
    rate = charge_per_min if docked else -drain_per_min
    return min(100.0, max(0.0, percent + rate * dt_sec / 60.0))


class SimBattery(Node):
    def __init__(self, start: float, charge: float, drain: float, topic: str) -> None:
        super().__init__("sim_battery")
        self._percent = start
        self._charge = charge
        self._drain = drain
        self._docked = False

        self._pub = self.create_publisher(Float32, topic, 10)
        self.create_subscription(Bool, "is_docked", self._on_docked, LATCHED)
        self.create_subscription(Float32, "sim_battery/set", self._on_set, 10)
        self.create_timer(PUBLISH_PERIOD_SEC, self._tick)
        self.get_logger().info(
            f"[sim-batt] {start:.0f}% 에서 시작 — 충전 {charge:.1f}%/분 · 소모 {drain:.1f}%/분 → {topic}"
        )

    def _on_docked(self, msg: Bool) -> None:
        if bool(msg.data) != self._docked:
            self._docked = bool(msg.data)
            self.get_logger().info(
                f"[sim-batt] {'충전 시작' if self._docked else '방전 시작'} ({self._percent:.1f}%)"
            )

    def _on_set(self, msg: Float32) -> None:
        self._percent = min(100.0, max(0.0, float(msg.data)))
        self.get_logger().info(f"[sim-batt] 잔량을 {self._percent:.1f}% 로 강제 설정")

    def _tick(self) -> None:
        self._percent = step_percent(self._percent, self._docked, self._charge,
                                     self._drain, PUBLISH_PERIOD_SEC)
        self._pub.publish(Float32(data=float(self._percent)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=60.0, help="시작 잔량 %% (기본 60 — 즉시 자동순회 방지)")
    ap.add_argument("--charge", type=float, default=30.0, help="도킹 중 회복 %%/분")
    ap.add_argument("--drain", type=float, default=0.0,
                    help="비도킹 시 소모 %%/분 (기본 0 — 시험이 배터리로 끊기지 않게)")
    ap.add_argument("--topic", default="battery/percent")
    args = ap.parse_args()

    rclpy.init()
    node = SimBattery(args.start, args.charge, args.drain, args.topic)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
