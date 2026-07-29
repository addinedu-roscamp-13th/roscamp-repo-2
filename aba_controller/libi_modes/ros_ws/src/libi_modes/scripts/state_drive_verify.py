#!/usr/bin/env python3
"""상태별로 **실제로 움직여야 할 때 움직이고, 멈춰야 할 때 멈추는지** 한 번에 본다.

## 왜 있나

2026-07-28 하루에 "화면은 정상인데 바퀴가 안 돈다 / 멈춰야 하는데 돈다"가 여섯 번 났다.
매번 원인이 달랐고(관제 미발행 · 실행층 조기응답 · 관제 재배차 · AI 서버 사망 ·
검출 채널 인자 누락 · 패널 자기해제) **전부 에러 없이 실패했다.** 눈으로 확인하는 한
또 놓친다. 그래서 사슬 전체를 한 번에 훑는 도구를 둔다.

## 쓰는 법 (로봇 위에서)

    ros2 run libi_modes state_drive_verify          # 관측만 — 상태를 안 바꾼다
    ros2 run libi_modes state_drive_verify --watch 30

또는 그냥::

    python3 scripts/state_drive_verify.py

⚠️ **바퀴를 띄워 놓고 돌려라.** 이 도구는 상태를 바꾸지 않지만, 관측 중 로봇이
주행 상태면 실제로 움직인다.

## 무엇을 보나

`/cmd_vel` 의 **non-zero 출력**을 본다. "발행이 없다"가 아니다 — 정지도 zero 를
발행하는 정상 동작이라, 무발행으로 판정하면 거짓 통과가 난다.

    상태            기대
    IDLE            자율주행 non-zero 없음 (motion_lock=true)
    INTERACTING     〃
    ERROR           〃
    CHARGING        〃
    PATROL          navigate 배차 시 non-zero 있음
    WORKING         명령에 따라 non-zero 있음
    RETURNING       non-zero 있음
    SECURITY_PATROL non-zero 있음

## 사전조건 — 이게 깨지면 나머지 판정이 의미 없다

`/cmd_vel` 발행자가 **정확히 `twist_mux` 하나**여야 한다. 다른 노드가 직접 발행하면
그 노드만 중재와 잠금을 우회하므로, "멈춤" 검증이 거짓 통과하거나 거짓 실패한다.
"""
import argparse
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

#: 자율주행이 돌면 안 되는 상태. `libi_modes/ros/state_io.py` 의 MOTION_LOCKED_STATES 와
#: 같아야 한다 — 다르면 이 도구가 거짓말을 한다.
LOCKED_STATES = frozenset({"IDLE", "INTERACTING", "ERROR", "CHARGING"})

#: 이 값보다 큰 성분이 하나라도 있으면 "움직이는 중"으로 친다. 노이즈·반올림 제외용.
MOVING_EPS = 1e-3

OK, BAD, WARN = "\033[92m✅\033[0m", "\033[91m❌\033[0m", "\033[93m⚠️\033[0m"


def _moving(t: Twist) -> bool:
    return (abs(t.linear.x) > MOVING_EPS or abs(t.linear.y) > MOVING_EPS
            or abs(t.angular.z) > MOVING_EPS)


class Verifier(Node):
    def __init__(self):
        super().__init__("state_drive_verify")
        # 로봇 쪽 발행이 best-effort 일 수 있으니 맞춰 둔다 — 안 맞으면 아무것도 안 온다.
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.state = None
        self.lock = None
        #: 상태 → 그 상태에서 non-zero 를 본 횟수 / 전체 프레임 수
        self.seen = {}
        self.create_subscription(String, "fsm_state", self._on_state, 10)
        self.create_subscription(Bool, "/libi/motion_lock", self._on_lock, 10)
        for topic in ("/cmd_vel", "/cmd_vel_follow", "/cmd_vel_nav_out",
                      "/cmd_vel_recovery", "/cmd_vel_stop"):
            # 발행자 QoS 를 모르므로 신뢰성만 맞춰 하나만 건다. 두 벌을 걸면
            # 같은 메시지를 두 번 세어 통계가 거짓이 된다.
            self.create_subscription(Twist, topic, self._make_cb(topic), qos)

    def _on_state(self, msg):
        self.state = msg.data

    def _on_lock(self, msg):
        self.lock = msg.data

    def _make_cb(self, topic):
        def cb(msg):
            key = (self.state or "?", topic)
            n_move, n_all = self.seen.get(key, (0, 0))
            self.seen[key] = (n_move + (1 if _moving(msg) else 0), n_all + 1)
        return cb

    # ── 사전조건 ────────────────────────────────────────────────────────
    def check_single_publisher(self):
        """`/cmd_vel` 발행자가 twist_mux 하나뿐인가."""
        infos = self.get_publishers_info_by_topic("/cmd_vel")
        names = sorted(i.node_name for i in infos if i.node_name != self.get_name())
        return names

    def report(self):
        print("\n" + "=" * 62)
        names = self.check_single_publisher()
        if names == ["twist_mux"]:
            print(f"{OK} /cmd_vel 발행자: twist_mux 하나")
        else:
            print(f"{BAD} /cmd_vel 발행자가 twist_mux 하나가 아니다: {names}")
            print("     → 그 노드는 중재와 잠금을 우회한다. 아래 판정은 믿을 수 없다.")

        print(f"\n마지막 상태: {self.state}   motion_lock: {self.lock}")
        if self.state in LOCKED_STATES and self.lock is False:
            print(f"{BAD} {self.state} 인데 잠금이 꺼져 있다 — 자율주행이 통과한다")
        elif self.state and self.state not in LOCKED_STATES and self.lock is True:
            print(f"{BAD} {self.state} 인데 잠금이 켜져 있다 — 주행이 통째로 막힌다")

        print("\n상태별 non-zero 출력 (움직인 프레임 / 전체):")
        if not self.seen:
            print(f"  {WARN} 아무 속도 메시지도 못 받았다 — DDS 설정이나 토픽 이름을 확인하라")
        for (state, topic), (n_move, n_all) in sorted(self.seen.items()):
            mark = ""
            if topic == "/cmd_vel":
                if state in LOCKED_STATES and n_move:
                    mark = f"  {BAD} 멈춰야 하는 상태인데 움직였다"
                elif state not in LOCKED_STATES and state != "?" and not n_move:
                    mark = f"  {WARN} 이 상태에서 한 번도 안 움직였다 (명령이 없었을 수도)"
                else:
                    mark = f"  {OK}"
            print(f"  {state:<16} {topic:<20} {n_move:>5}/{n_all:<6}{mark}")
        print("=" * 62)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", type=float, default=20.0, help="관측 시간(초)")
    args = ap.parse_args()

    rclpy.init()
    node = Verifier()
    print(f"[verify] {args.watch:.0f}초 관측한다. 그동안 관제/패널에서 상태를 바꿔 보라.")
    print("[verify] ⚠️ 바퀴를 띄워 놓았는지 확인하라 — 주행 상태면 실제로 움직인다.")
    t0 = time.time()
    try:
        while time.time() - t0 < args.watch:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    node.report()
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
