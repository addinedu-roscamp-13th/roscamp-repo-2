#!/usr/bin/env python3
r"""fleet_node 의 경로 명령 → nav2 주행. **로봇 도메인에서 실행한다.**

## 왜 필요한가 (끊겨 있던 고리)
`fleet_node` 는 교통(예약)까지 풀어 경로를 정한 뒤 `rmf_fleet_msgs/PathRequest` 를
`/robot_path_requests` 로 발행한다. 그런데 **로봇 쪽에 이걸 구독하는 코드가 하나도 없었다.**
그래서 배차는 성공하는데(로봇에 goal_vertex 가 찍힘) 로봇이 움직이지 않았다.
이 노드가 그 고리를 잇는다 — `robot_state_adapter.py`(로봇 위치 → fleet_node)의 짝이다.

## 한 노드씩 간다 (교통 협상 때문에)
fleet_node 의 traffic 플러그인은 **다음 한 노드만** 예약한다. 전체 경로를 한 번에 받아
달리면 예약하지 않은 노드로 들어가 **교통 협상이 무력화된다**(로봇이 여러 대인 순간
충돌). 그래서 관제가 노드를 하나씩 알려주고, 이 노드는 그 한 구간만 `NavigateToPose`
로 주행한다.

정점은 **사람이 통로 중심에 맞춰 직접 찍은 것**이라(`waypoint.yaml`), 정점을 이어 달리면
벽에서 떨어진 경로가 된다. 구간 사이 경로는 nav2 전역 플래너가 만든다.

## 전역 플래너는 Theta* 를 쓴다
arte2 는 1.26x2.16m 축소맵이라 정점 간격이 6~20cm 뿐이다. 기본 플래너 NavFn(전위장)은
그 짧은 구간을 계획하지 못하고 `"Failed to create a plan from potential..."` 로 실패했다.
그러면 BT 가 복구동작(spin/backup)을 돌려 로봇이 79초간 엉뚱한 곳을 0.58m 배회하다
ABORTED 로 끝났다(실측). Theta* 는 전위장을 쓰지 않아 그 실패 모드가 없고, any-angle
이라 격자 계단이 아닌 직선 경로를 낸다.
→ `pinky_navigation/params/nav2_params_sim.yaml` 의 planner_server 참고.

## 실행 (로봇 도메인 = rc_robots.domain_id, sim 은 셸 값)
    ROS_DOMAIN_ID=119 python3 path_request_driver.py --robot pinky3

⚠️ `/robot_path_requests` 는 서버 도메인(86)에서 발행되므로 **domain_bridge 에 역방향
(reversed) 항목이 있어야** 이 노드까지 온다 — `config/domain_bridge.template.yaml` 참고.
"""

from __future__ import annotations

import argparse
import math

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rmf_fleet_msgs.msg import PathRequest

#: nav2 액션 서버가 뜰 때까지 기다리는 한 번의 대기 시간.
SERVER_WAIT_SEC = 5.0
#: 주행 실패 후 같은 경유점을 다시 시도하기까지 기다리는 시간(초). 재시도 폭주 방지.
RETRY_COOLDOWN_SEC = 3.0
#: 재시도 여부를 확인하는 주기(초).
RETRY_TICK_SEC = 1.0


def quat_z_w(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def node_safe(name: str) -> str:
    """ROS 노드 이름으로 쓸 수 있게 정리한다.

    노드 이름은 영숫자와 `_` 만 허용된다 — 로봇 이름이 `Pinky-3` 처럼 `-` 를 포함하면
    그대로 쓸 수 없다. 필터에 쓰는 `robot_name` 은 원래 이름을 그대로 둔다.
    """
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return safe or "robot"


class PathRequestDriver(Node):
    def __init__(self, robot: str, topic: str, frame: str) -> None:
        super().__init__(f"path_request_driver_{node_safe(robot)}")
        self._robot = robot
        self._frame = frame

        #: 주행할 경유점 (x, y, yaw). 노드 단위라 보통 1개다.
        self._points: list[tuple[float, float, float]] = []
        #: _points 안에서 지금 향하는 점.
        self._idx = 0
        self._task_id = ""
        self._goal_handle = None
        self._busy = False
        #: 현재 향하는 최종 목적지. 같은 목적지면 재발행을 무시한다(끊김 방지).
        self._dest: tuple[float, float] | None = None
        #: 실패 후 재시도 가능 시각(초).
        self._retry_after = 0.0
        #: 목표 일련번호 — 취소된 목표의 뒤늦은 콜백을 걸러낸다.
        #  cancel_goal_async() 는 즉시 끝나지 않고, 이미 등록된 결과 콜백이 나중에 한 번 더
        #  불린다. 그걸 현재 주행의 결과로 착각하면 상태가 엉킨다(실측: 옛 목표의 SUCCEEDED
        #  가 "완주" 로 둔갑해, 로봇은 목적지에서 10cm 떨어진 채 주문이 멈춰 있었다).
        self._goal_seq = 0

        self._nav = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.create_subscription(PathRequest, topic, self._on_path, 10)
        self.create_timer(RETRY_TICK_SEC, self._retry_tick)
        self.get_logger().info(
            f"경로 드라이버 시작: {topic} → nav2 navigate_to_pose (robot={robot})"
        )

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ── PathRequest 수신 ────────────────────────────────────────────────────
    def _on_path(self, msg: PathRequest) -> None:
        # 다른 로봇 앞으로 온 명령은 무시한다(한 토픽에 여러 로봇이 섞인다).
        if msg.robot_name and msg.robot_name != self._robot:
            return
        pts = [(float(p.x), float(p.y), float(p.yaw)) for p in msg.path]
        if not pts:
            self.get_logger().info("빈 경로 수신 → 현재 목표 취소")
            self._cancel()
            self._points, self._idx, self._dest = [], 0, None
            return

        # ⚠️ path[0] 은 목적지가 아니라 **출발점(로봇의 현재 위치)** 이다.
        # 이걸 주행 목표로 쓰면 "지금 서 있는 자리로 가라"가 되어, nav2 가 거리 0 목표에
        # 경로를 못 만들고 즉시 ABORTED 를 낸다. 그러면 주문이 그 자리에서 영구 정지했다.
        if len(pts) >= 2:
            pts = pts[1:]

        dest = (round(pts[-1][0], 3), round(pts[-1][1], 3))

        # 같은 목적지로 이미 달리는 중이면 건드리지 않는다.
        # 그때마다 취소·재전송하면 로봇이 출발하기도 전에 목표가 덮여 제자리에서 버벅인다.
        if dest == self._dest and self._busy:
            return
        # 실패 직후 쿨다운 중이면 같은 목적지 재시도를 미룬다.
        if dest == self._dest and self._now() < self._retry_after:
            return

        self.get_logger().info(
            f"경로 수신 task={msg.task_id} 경유점 {len(pts)}개 → "
            f"{dest[0]:.2f},{dest[1]:.2f}"
        )
        self._cancel()
        self._points, self._idx, self._task_id, self._dest = pts, 0, msg.task_id, dest
        self._send_next()

    def _retry_tick(self) -> None:
        """실패해서 멈춰 있으면 쿨다운 뒤 같은 경유점을 다시 보낸다.

        fleet_node 는 노드가 바뀔 때만 발행하므로, 한 번 실패하면 아무도 다시
        깨워주지 않는다. 이 타이머가 유일한 복구 경로다.
        """
        if self._busy or self._idx >= len(self._points):
            return
        if self._now() < self._retry_after:
            return
        self.get_logger().info(f"경유점 {self._idx + 1} 재시도")
        self._send_next()

    # ── nav2 목표 전송 ──────────────────────────────────────────────────────
    def _send_next(self) -> None:
        if self._idx >= len(self._points):
            self.get_logger().info(f"경로 완주 task={self._task_id}")
            self._busy = False
            self._points, self._idx = [], 0
            return
        if not self._nav.wait_for_server(timeout_sec=SERVER_WAIT_SEC):
            self.get_logger().error("nav2 navigate_to_pose 액션 서버 없음 — 주행 불가")
            self._fail_later()
            return

        x, y, yaw = self._points[self._idx]
        ps = PoseStamped()
        ps.header.frame_id = self._frame
        ps.header.stamp = self.get_clock().now().to_msg()
        ps.pose.position.x = x
        ps.pose.position.y = y
        z, w = quat_z_w(yaw)
        ps.pose.orientation.z = z
        ps.pose.orientation.w = w
        goal = NavigateToPose.Goal()
        goal.pose = ps

        self._busy = True
        self._goal_seq += 1
        seq = self._goal_seq
        self.get_logger().info(
            f"[{self._idx + 1}/{len(self._points)}] → ({x:.2f}, {y:.2f})"
        )
        self._nav.send_goal_async(goal).add_done_callback(
            lambda fut: self._on_accepted(fut, seq)
        )

    def _on_accepted(self, future, seq: int) -> None:
        if seq != self._goal_seq:
            return  # 이미 갈아탄 목표의 응답 — 버린다
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"목표 전송 실패: {exc}")
            self._fail_later()
            return
        if not handle.accepted:
            self.get_logger().error("nav2 가 목표를 거절함")
            self._fail_later()
            return
        self._goal_handle = handle
        handle.get_result_async().add_done_callback(
            lambda fut: self._on_result(fut, seq)
        )

    def _on_result(self, future, seq: int) -> None:
        # 취소했거나 이미 다음 목표로 넘어간 뒤 도착한 결과는 현재 주행의 결과가 아니다.
        if seq != self._goal_seq:
            return
        self._goal_handle = None
        try:
            result = future.result()
            status = getattr(result, "status", GoalStatus.STATUS_SUCCEEDED)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"주행 결과 오류: {exc}")
            self._fail_later()
            return

        if status != GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().warn(
                f"경유점 {self._idx + 1} 주행 실패(status={status}) — 재시도 대기"
            )
            self._fail_later()
            return

        self._idx += 1
        self._busy = False
        self._send_next()

    def _fail_later(self) -> None:
        """실패 표시 + 쿨다운. 쿨다운이 지나면 _retry_tick 이 다시 건다."""
        self._busy = False
        self._retry_after = self._now() + RETRY_COOLDOWN_SEC

    def _cancel(self) -> None:
        # seq 를 올려서 **취소한 목표의 뒤늦은 콜백을 전부 무효화**한다.
        self._goal_seq += 1
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None
        self._busy = False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", default="pinky3", help="RobotState.name 과 같은 이름")
    ap.add_argument("--topic", default="/robot_path_requests")
    ap.add_argument("--frame", default="map")
    args = ap.parse_args()

    rclpy.init()
    node = PathRequestDriver(args.robot, args.topic, args.frame)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
