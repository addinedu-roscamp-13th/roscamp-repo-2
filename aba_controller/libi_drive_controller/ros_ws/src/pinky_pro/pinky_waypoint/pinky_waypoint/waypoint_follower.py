#!/usr/bin/env python3
"""
RViz 클릭으로 경유지/목적지를 받아 Nav2로 주행시키는 노드.

사용법(RViz):
  1) "Publish Point" 도구로 지나갈 지점(경유지)들을 순서대로 클릭한다.
       -> /clicked_point 로 들어오며, 클릭할 때마다 경유지 목록에 추가된다.
  2) "2D Goal Pose" 도구로 최종 목적지를 클릭한다.
       -> /goal_pose 로 들어오며, 이 순간
          goThroughPoses([경유지들..., 최종 목적지]) 가 실행된다.
          (경유지에 "충분히 가까워지면" Nav2가 알아서 다음 점으로 넘어감)
  3) 주행이 끝나면 경유지 목록은 자동으로 비워진다.

경유지 없이 "2D Goal Pose" 만 찍으면 곧장 목적지로 간다.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PointStamped, PoseStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult


class WaypointFollower(Node):
    def __init__(self, navigator: BasicNavigator):
        super().__init__('waypoint_follower')
        self.navigator = navigator

        self.waypoints = []        # 누적된 경유지(PoseStamped) 목록
        self.pending_goal = None   # 막 들어온 최종 목적지(PoseStamped)
        self.navigating = False    # 현재 주행 중인지

        self.create_subscription(
            PointStamped, '/clicked_point', self.on_clicked_point, 10)
        self.create_subscription(
            PoseStamped, '/goal_pose', self.on_goal_pose, 10)

        self.get_logger().info(
            "준비 완료. RViz 'Publish Point'로 경유지, '2D Goal Pose'로 목적지를 찍으세요.")

    # ---------------- 경유지 추가 ----------------
    def on_clicked_point(self, msg: PointStamped):
        if self.navigating:
            self.get_logger().warn("주행 중에는 경유지를 추가할 수 없습니다. 끝나고 다시 찍으세요.")
            return

        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.navigator.get_clock().now().to_msg()
        pose.pose.position.x = msg.point.x
        pose.pose.position.y = msg.point.y
        pose.pose.orientation.w = 1.0   # 경유지는 방향이 없으므로 기본값
        self.waypoints.append(pose)
        self.get_logger().info(
            f"경유지 {len(self.waypoints)} 추가: "
            f"({msg.point.x:.2f}, {msg.point.y:.2f})")

    # ---------------- 최종 목적지 -> 주행 트리거 ----------------
    def on_goal_pose(self, msg: PoseStamped):
        if self.navigating:
            self.get_logger().warn("이미 주행 중입니다. 무시합니다.")
            return
        self.pending_goal = msg
        self.get_logger().info(
            f"목적지 수신: ({msg.pose.position.x:.2f}, {msg.pose.position.y:.2f}) "
            f"-> 경유지 {len(self.waypoints)}개 거쳐 출발")

    # ---------------- 메인 루프에서 호출 ----------------
    def tick(self):
        # 새 목적지가 들어왔고 아직 주행 전이면 출발
        if self.pending_goal is not None and not self.navigating:
            poses = self.waypoints + [self.pending_goal]
            self.navigator.goThroughPoses(poses)
            self.navigating = True
            self.pending_goal = None

        # 주행 중이면 완료 여부 확인
        if self.navigating and self.navigator.isTaskComplete():
            result = self.navigator.getResult()
            if result == TaskResult.SUCCEEDED:
                self.get_logger().info("목적지 도착 완료!")
            elif result == TaskResult.CANCELED:
                self.get_logger().warn("주행이 취소되었습니다.")
            else:
                self.get_logger().warn("주행 실패. 위치추정(2D Pose Estimate)을 확인하세요.")
            self.navigating = False
            self.waypoints = []   # 다음 주행을 위해 초기화


def main():
    rclpy.init()

    navigator = BasicNavigator()
    navigator.get_logger().info("Nav2 활성화 대기 중...")
    navigator.waitUntilNav2Active()
    navigator.get_logger().info("Nav2 활성화됨.")

    node = WaypointFollower(navigator)

    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            node.tick()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        navigator.lifecycleShutdown()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
