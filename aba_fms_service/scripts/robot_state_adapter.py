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
import os
import time
import traceback

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from rmf_fleet_msgs.msg import Location, RobotMode, RobotState
from std_msgs.msg import Float32

PUBLISH_HZ = 2.0

#: amcl_pose 를 못 받는 상태를 몇 초마다 알릴지. 발행 주기(2 Hz)보다 훨씬 커야 로그를 안 덮는다.
POSE_WAIT_WARN_SEC = 15.0

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

        # 진단용 — "프로세스가 살아 있다"와 "일하고 있다"를 구별하기 위한 최소 상태.
        self._prefix = prefix
        self._started_at = time.monotonic()
        self._last_wait_warn_at = 0.0

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
        first = self._pose is None
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation.z, p.orientation.w))
        if first:
            # "언제부터 정상이었나"를 로그에 남긴다. 이 줄이 없으면 어댑터가 일을 시작한
            # 시각을 알 방법이 없고, 시작조차 못 한 경우와 구별되지 않는다.
            self.get_logger().info(
                f"첫 위치 수신 — /robot_state 발행을 시작합니다 "
                f"(대기 {time.monotonic() - self._started_at:.1f}s)"
            )

    def _on_battery(self, msg: Float32) -> None:
        self._battery = float(msg.data)

    def _tick(self) -> None:
        # 위치를 아직 못 받았으면 발행하지 않는다 — 좌표 0,0 인 유령 로봇이 생기면 안 된다.
        if self._pose is None:
            # ⚠️ 예전엔 여기서 조용히 return 했다. 그래서 이 상태가 **완전히 무증상**이었다:
            #    프로세스는 살아 있고(pgrep 으로 보이고) 로그도 안 나오는데
            #    /robot_state 는 한 번도 안 나가고, fleet_node 는 로봇을 0대로 본다.
            #    → 배차·순회가 시작조차 안 되는데 관제 패널에는 로봇이 정상으로 보인다
            #      (패널은 amcl_pose 를 직접 읽는다). 2026-07-26 순찰 정지의 유력 상류.
            now = time.monotonic()
            if now - self._last_wait_warn_at >= POSE_WAIT_WARN_SEC:
                self._last_wait_warn_at = now
                self.get_logger().warn(
                    f"{self._prefix}/amcl_pose 대기 중 ({now - self._started_at:.0f}s) — "
                    "/robot_state 를 발행하지 못하고 있습니다. fleet_node 는 이 로봇을 "
                    "인식하지 못하며 배차·순회가 시작되지 않습니다. "
                    "도메인 브릿지(domain_bridge)와 AMCL 초기 위치를 확인하세요."
                )
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

    def log_shutdown(exc: BaseException | None) -> None:
        reason = type(exc).__name__ if exc is not None else "spin 정상 반환"
        node.get_logger().info(
            f"종료 신호 수신({reason}) — "
            f"pid={os.getpid()} ppid={os.getppid()} "
            f"가동 {time.monotonic() - node._started_at:.0f}s. 어댑터를 정리합니다"
        )

    try:
        rclpy.spin(node)
    except KeyboardInterrupt as exc:
        log_shutdown(exc)
    except Exception as exc:
        # rclpy 의 SIGINT/SIGTERM 처리 자체가 레이스다: 시그널 핸들러가 C 레벨에서
        # 컨텍스트/전역 executor 를 비동기로 정리하는데, 그 타이밍이 spin() 내부의 어느
        # 지점과 겹치느냐에 따라 던지는 예외 타입이 달라진다 — 실측 확인됨:
        #   - ExternalShutdownException (rclpy 자신의 정상 종료 체크, context.ok())
        #   - RCLError: failed to initialize wait set ... (wait set 생성이 shutdown 과 겹침)
        #   - AttributeError: 'NoneType' object has no attribute 'add_node'
        #     (rclpy.spin() 이 get_global_executor() 를 부르는 순간 전역 executor 가 이미
        #     None 으로 정리된 상태 — 30회 반복 재현 중 2회 관측)
        # 예외 타입을 나열해 잡는 건 두더지잡기라 끝이 없다. 대신 rclpy.ok() 로 "컨텍스트가
        # 이미 죽었는가"만 본다 — 죽었으면 무슨 예외든 정상 종료의 부산물이니 삼키고, 안
        # 죽었으면 진짜 내부 오류이므로 다시 던져 트레이스백을 남긴다.
        if rclpy.ok():
            raise
        # 컨텍스트는 이미 죽었다 = 종료 경로다. 하지만 **모든** 예외를 종료 잡음으로
        # 취급하면, 종료와 우연히 겹친 진짜 결함이 "정상 종료" 한 줄로 둔갑해 사라진다.
        # 그래서 종료 자체는 정상으로 끝내되(재던지면 트레이스백이 사인을 덮는다),
        # 알려진 종료 예외가 아니면 트레이스백을 남긴다 — 조용히 삼키지 않는다.
        if not isinstance(exc, ExternalShutdownException):
            node.get_logger().warning(
                "종료 중 예상치 못한 예외 — 종료와 겹친 진짜 결함일 수 있습니다:\n"
                + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )
        log_shutdown(exc)
    else:
        # spin() 이 예외 없이 정상 반환하는 경우도 있다. rclpy.spin() 은
        # `while executor.context.ok(): executor.spin_once()` 라서, while 조건 검사와 다음
        # spin_once() 호출 사이에 컨텍스트가 shutdown 되면 예외 하나 없이 루프가 끝난다.
        # 여기서 기록을 안 남기면 사인 불명 상태가 그대로 재발한다.
        log_shutdown(None)
    finally:
        node.destroy_node()
        # 이미 종료된 컨텍스트에 다시 shutdown 을 부르면
        # `RCLError: rcl_shutdown already called` 로 죽는다. 그래서 상태를 먼저 본다.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
