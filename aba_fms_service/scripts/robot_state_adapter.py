#!/usr/bin/env python3
"""로봇 위치 → fleet_node 가 읽는 `RobotState` 로 변환하는 서버측 어댑터.

## 왜 필요한가
`fleet_node` 는 `/robot_state`(`rmf_fleet_msgs/RobotState`)를 구독해서 "어떤 로봇이 어디에
있는가"를 안다. 그런데 로봇(및 sim)은 그 타입을 발행하지 않는다 — `amcl_pose`,
`battery/percent`, `fleet_status` 만 낸다. 그래서 **로봇→fleet_node 고리가 비어 있고,
fleet_node 가 로봇을 0대로 본다 → 배차가 아예 불가능**했다.

이 어댑터가 그 틈을 메운다. 이미 domain_bridge 가 서버 도메인(111)으로 옮겨 놓은
`/pinky3/amcl_pose` 와 `/pinky3/battery/percent` 를 읽어 `/robot_state` 로 재발행한다.
**로봇은 무수정** — 서버에서만 돈다.

## 이름 규칙
`RobotState.name` 은 `FsmState.robot_id` 와 같은 키여야 fleet_node 안에서 상태가 매칭된다
(fleet_node.cpp 주석 참고). 스크립트들이 로봇을 `pinky3` 로 부르므로 그 값을 기본으로 쓴다.

## 실행 (서버, 도메인 111)
    ROS_DOMAIN_ID=111 python3 aba_fms_service/scripts/robot_state_adapter.py --robot pinky3
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import traceback

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Path
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from rmf_fleet_msgs.msg import Location, RobotMode, RobotState
from std_msgs.msg import Float32, String

PUBLISH_HZ = 2.0

#: amcl_pose 를 못 받는 상태를 몇 초마다 알릴지. 발행 주기(2 Hz)보다 훨씬 커야 로그를 안 덮는다.
POSE_WAIT_WARN_SEC = 15.0

# ── 로봇 생존 판정 ───────────────────────────────────────────────────────────
#
# ## ⚠️ [2026-08-02] 로봇이 꺼져도 **유령이 계속 발행되고 있었다** (실측)
#
# pinky-3 의 Pi 를 완전히 내린 뒤에도 이 어댑터가 `/robot_state` 를 계속 냈다.
# `_tick` 이 "위치를 **한 번이라도** 받았나"만 보고 신선도를 안 봤기 때문이다.
# 그 결과가 줄줄이 이어졌다:
#   · fleet_node 가 꺼진 로봇에게 **순회를 배정**했다 ("[P-pinky-3] 순회 시작")
#   · 관제 지도에 없는 로봇이 떠 있었다 — 사람이 그걸 보고 배차를 판단한다
#   · 그 로봇이 노드를 예약해 **살아 있는 로봇의 길을 막는다**
# 프론트에서 "좌표가 있으면 살아 있다"로 거르는 것으로는 못 막는다 — 유령도 좌표가 있다.
#
# ## 위치와 생존은 **다른 종류의 신호**다
#
#   위치(amcl_pose) = **상태**. 로봇이 안 움직이면 안 바뀌는 게 정상이다.
#     AMCL 은 주기 발행이 아니라 **갱신 때만** 내보낸다(아래 LATCHED_QOS 주석).
#     그래서 "pose 가 안 온다"를 죽음으로 읽으면 **주차된 멀쩡한 로봇을 죽었다고 한다.**
#   생존(battery/percent) = **이벤트**. `pinky_bringup/battery_publisher.py` 가
#     5초 타이머로 **무조건** 발행한다(값이 안 변해도 — 그 파일 109-110행).
#
# 그래서 생존은 배터리로 재고, 위치는 마지막 값을 그대로 쓴다.
#
# ## ⚠️ latched pose 함정 — "위치가 있다"는 생존의 증거가 **아니다**
#
# `amcl_pose` 는 TRANSIENT_LOCAL 이라(아래 LATCHED_QOS) 로봇이 몇 시간 전에 꺼졌어도
# **구독하는 순간 마지막 값이 배달된다.** 그래서 "하트비트를 본 적 있는 로봇만
# 검사한다"로 두면, 이미 꺼진 로봇에 어댑터를 새로 띄웠을 때 배터리는 영영 안 오고
# latched pose 만 받아 **영구 유령**이 된다. 실측 pinky-3 이 정확히 그 상태였다.
#
# 그래서 판정은 **둘 중 하나라도 신선하면 살아 있다**로 한다:
#   · 배터리(5초 주기) — 로봇이 주차돼 있어도 온다
#   · 위치(갱신 시에만) — 움직이는 동안 온다
# 둘 다 끊기면 꺼진 것이다. 어느 한쪽 발행기가 없는 배포도 나머지 하나로 버틴다.
#
# ⚠️ **배터리 발행기가 없고 로봇이 주차만 하는 배포**에서는 이 검사가 로봇을 지운다.
#    그런 구성이 있으면 `LIVENESS_TTL_SEC=0` 으로 끄거나 TTL 을 크게 잡아야 한다.
# ## 이 값이 **유령 제거의 시작점**이다 — 하류는 이것 없이는 못 알아챈다
#
#   이 검사가 없으면 어댑터가 2 Hz 로 옛 좌표를 계속 밀기 때문에, 하류의
#   staleness 검사(`fleet_node` kRobotStaleSec=10s, 백엔드 `fleet_link.FRESH_SEC`=10s)가
#   **영원히 발동하지 않는다.** "마지막 수신 시각"이 늘 방금이기 때문이다.
#   그래서 여기가 1차 판정이고, 하류 10초는 그 뒤의 전파 지연이다.
#
#   로봇 전원 차단 → 화면에서 사라지기까지:  이 TTL + 하류 10초
#
# ⚠️ 값 선택: 배터리 5초 주기(sim 은 1초)의 **3배**. 2배(10초)는 I2C 읽기 실패로
#    한 주기를 거르면(battery_publisher.py 의 유효표본 검사) 곧바로 오탐이 난다.
#    4배 이상은 유령이 화면에 남는 시간이 30초를 넘어 사람이 "왜 아직 있지" 를 겪는다.
#: 하트비트가 이 시간 넘게 없으면 로봇이 꺼진 것으로 본다. 0 이면 검사 끔.
LIVENESS_TTL_SEC = 15.0

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
        #: 마지막 하트비트 수신 시각. 배터리와 위치를 각각 따로 잰다 — 근거는
        #: 위 LIVENESS_TTL_SEC 머리말(둘 중 하나라도 신선하면 살아 있다).
        self._battery_at: float | None = None
        self._pose_at: float | None = None
        #: 지금 "꺼졌다"로 보고 있나. 상태가 바뀔 때만 로그를 찍으려고 둔다 —
        #: 2 Hz 로 매번 찍으면 진짜 경고가 묻힌다.
        self._offline = False

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
        # ── nav2 실주행 경로 중계 (2026-08-02) ────────────────────────────
        #
        # 길잡이는 `GuideExec` 이 nav2 로 **직접** 몬다. fleet_node 는 그 목적지를
        # 모르므로 `/fms/routes` 에 안 실린다 — 그래서 관제 지도에 안내 경로가
        # 안 그려지고, 대신 **순찰 경로**만 떠서 화면이 거짓말을 했다
        # (사용자 실측 2026-08-02: 화장실로 가는 중인데 순회 경로만 보였다).
        #
        # nav2 가 내는 `/plan` 은 **이미 도메인 브릿지에 있다**(`/pinky3/plan`,
        # `config/generated/domain_bridge_pinky3.yaml`). 로봇은 손댈 필요가 없다.
        # 다만 백엔드는 로봇별 접두사를 모른다 — 그건 이 어댑터만 안다(`--prefix`).
        # 그래서 여기서 **이름을 붙여** 공용 토픽 하나로 넘긴다(`/robot_state` 와 같은 꼴).
        #
        # ⚠️ 점을 솎는다. nav2 경로는 수백 점이라 그대로 20Hz WS 에 실으면 관제
        #    프론트가 그 하나로 막힌다. 지도에 그리는 데는 5cm 간격이면 충분하다.
        self._nav_path_pub = self.create_publisher(String, "/robot_nav_path", 10)
        self.create_subscription(Path, f"{prefix}/plan", self._on_nav_path, 10)

        self._pub = self.create_publisher(RobotState, "/robot_state", 10)
        self.create_timer(1.0 / PUBLISH_HZ, self._tick)

        self.get_logger().info(
            f"어댑터 시작: {prefix}/amcl_pose → /robot_state (name={robot})"
        )

    #: 경로 점 사이 최소 간격(m). 이보다 촘촘한 점은 버린다.
    NAV_PATH_MIN_STEP_M = 0.05
    #: 한 경로에 실을 최대 점 수. 아주 긴 경로에서도 상한을 둔다.
    NAV_PATH_MAX_POINTS = 400

    def _on_nav_path(self, msg: Path) -> None:
        """nav2 `/plan` → 이름 붙인 JSON 한 줄. 위 ⚠️ 주석 참고."""
        pts: list[list[float]] = []
        last: tuple[float, float] | None = None
        for ps in msg.poses:
            x, y = float(ps.pose.position.x), float(ps.pose.position.y)
            if last is not None:
                dx, dy = x - last[0], y - last[1]
                if (dx * dx + dy * dy) < (self.NAV_PATH_MIN_STEP_M ** 2):
                    continue
            pts.append([round(x, 3), round(y, 3)])
            last = (x, y)
            if len(pts) >= self.NAV_PATH_MAX_POINTS:
                break
        # 마지막 점은 **반드시** 남긴다 — 솎다가 목적지를 버리면 경로가 도중에 끊겨
        # 보인다. 빈 경로(nav2 가 목표를 취소했다)는 그대로 빈 채로 보낸다: 그것이
        # "지금 가는 곳이 없다"는 사실이고, 화면이 옛 선을 계속 그리면 안 된다.
        if msg.poses:
            end = msg.poses[-1].pose.position
            tail = [round(float(end.x), 3), round(float(end.y), 3)]
            if not pts or pts[-1] != tail:
                pts.append(tail)
        self._nav_path_pub.publish(String(data=json.dumps(
            {"name": self._robot, "points": pts}, ensure_ascii=False)))

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        first = self._pose is None
        p = msg.pose.pose
        pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation.z, p.orientation.w))
        # ⚠️ **값이 바뀐 pose 만 생존 신호로 친다 — latched 재배달을 걸러내려는 것이다.**
        #
        #   `amcl_pose` 는 TRANSIENT_LOCAL 이라(아래 LATCHED_QOS) 도메인 브릿지가
        #   재연결되면 **몇 시간 전 pose 가 그대로 다시 배달된다.** 도착 시각만 찍으면
        #   그걸 "방금 살아 있었다"로 읽어, 이미 꺼진 로봇의 유령을 다시 15초 살린다
        #   (codex 지적 2026-08-02).
        #
        #   재배달은 **같은 메시지**라 값이 비트 단위로 같다. 반면 살아서 움직이는
        #   로봇은 값이 바뀐다. 그래서 시계(header.stamp)를 안 보고 값의 변화로 가른다 —
        #   로봇과 서버의 시계가 안 맞아도 성립한다.
        #
        #   ⚠️ 살아 있지만 **주차된** 로봇은 pose 가 안 바뀌어 여기서 신호를 못 얻는다.
        #      그 경우는 배터리 하트비트가 받친다(LIVENESS_TTL_SEC 머리말).
        if pose != self._pose:
            self._pose_at = time.monotonic()
        self._pose = pose
        if first:
            # "언제부터 정상이었나"를 로그에 남긴다. 이 줄이 없으면 어댑터가 일을 시작한
            # 시각을 알 방법이 없고, 시작조차 못 한 경우와 구별되지 않는다.
            self.get_logger().info(
                f"첫 위치 수신 — /robot_state 발행을 시작합니다 "
                f"(대기 {time.monotonic() - self._started_at:.1f}s)"
            )

    def _on_battery(self, msg: Float32) -> None:
        self._battery = float(msg.data)
        self._battery_at = time.monotonic()

    def _is_offline(self) -> bool:
        """로봇이 꺼졌나. **배터리든 위치든 하나라도 신선하면 살아 있다.**

        근거와 latched pose 함정은 위 LIVENESS_TTL_SEC 머리말 참고.
        """
        if LIVENESS_TTL_SEC <= 0:
            return False
        now = time.monotonic()
        for at in (self._battery_at, self._pose_at):
            if at is not None and (now - at) <= LIVENESS_TTL_SEC:
                return False          # 하나라도 신선하다 → 살아 있다
        return True

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
        # 꺼진 로봇을 계속 발행하지 않는다 — "모르는 것을 안다고 하지 않는다".
        # (`libi_perception/detection_receiver.py` 의 TTL 과 같은 원칙이다.)
        offline = self._is_offline()
        if offline != self._offline:
            self._offline = offline
            if offline:
                self.get_logger().warn(
                    f"{self._prefix} 의 하트비트가 {LIVENESS_TTL_SEC:.0f}s 넘게 없습니다 "
                    "(battery/percent 도, 값이 바뀐 amcl_pose 도 안 옵니다) — "
                    "로봇이 꺼진 것으로 보고 /robot_state 발행을 멈춥니다. "
                    "fleet_node 는 이 로봇을 **새 배차·순회 후보에서 제외**합니다"
                    "(fleet_node.cpp state_stale). "
                    "다만 **이미 잡고 있던 예약은 자동으로 안 풉니다** — 통신이 끊긴 것과 "
                    "로봇이 그 자리에서 사라진 것은 다르기 때문입니다. 로봇이 실제로 "
                    "치워졌다면 운영자가 확인 후 정리해야 합니다."
                )
            else:
                self.get_logger().info("배터리 하트비트 복구 — /robot_state 발행을 재개합니다")
        if offline:
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
