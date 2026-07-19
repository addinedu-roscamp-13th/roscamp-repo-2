#!/usr/bin/env python3
"""가벼운 라이다 스캔 필터 노드 (range + speckle).

/scan (raw LaserScan) 을 받아 아래 두 필터를 적용해 /scan_filtered 로 발행한다.
목적: 라이다 잡음이 costmap 에 유령 장애물로 찍히는 것을 방지.

  1) range 필터  : min_range~max_range 밖의 점을 제거(inf)
  2) speckle 필터: 주변에 이웃이 거의 없는 "외딴 점"(전형적 잡음)을 제거(inf)
                   → 방 안(범위 내)에 튀는 단발성 잡음 점을 걸러낸다.

laser_filters 패키지가 diagnostic_updater ABI 문제로 못 뜨는 환경을 위해
rclpy 로 직접 구현. 외부 의존성 없음(rclpy, sensor_msgs 만).

파라미터:
  input_topic   (str,  /scan)          입력 raw 스캔
  output_topic  (str,  scan_filtered)  출력 필터 스캔
  min_range     (float, 0.05) [m]      이 미만(로봇 몸체/근접 잡음) 제거
  max_range     (float, 3.0)  [m]      이 초과(방 밖 잡음) 제거
  speckle_enabled        (bool, True)  외딴 점 제거 on/off
  speckle_window         (int,  2)     좌우로 몇 칸까지 이웃으로 볼지
  speckle_max_range_diff (float, 0.10) [m] 이웃과 거리차가 이 이내면 "같은 물체"로 간주
  speckle_min_neighbors  (int,  1)     이웃(같은 물체)이 이 수 미만이면 잡음으로 제거
"""
import math

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanFilter(Node):
    def __init__(self):
        super().__init__('scan_filter_node')
        self.declare_parameter('input_topic', '/scan')
        self.declare_parameter('output_topic', 'scan_filtered')
        self.declare_parameter('min_range', 0.05)
        self.declare_parameter('max_range', 3.0)
        self.declare_parameter('speckle_enabled', True)
        self.declare_parameter('speckle_window', 2)
        self.declare_parameter('speckle_max_range_diff', 0.10)
        self.declare_parameter('speckle_min_neighbors', 1)

        self.min_range = self.get_parameter('min_range').value
        self.max_range = self.get_parameter('max_range').value
        self.sp_on = self.get_parameter('speckle_enabled').value
        self.sp_win = int(self.get_parameter('speckle_window').value)
        self.sp_diff = self.get_parameter('speckle_max_range_diff').value
        self.sp_min = int(self.get_parameter('speckle_min_neighbors').value)
        in_topic = self.get_parameter('input_topic').value
        out_topic = self.get_parameter('output_topic').value

        # 센서 데이터용 QoS (best effort) 로 라이다와 맞춘다.
        self.pub = self.create_publisher(LaserScan, out_topic, qos_profile_sensor_data)
        self.sub = self.create_subscription(
            LaserScan, in_topic, self.cb, qos_profile_sensor_data)
        self.get_logger().info(
            f'scan_filter: {in_topic} -> {out_topic} | '
            f'range {self.min_range}~{self.max_range}m | '
            f'speckle={"on" if self.sp_on else "off"} '
            f'(win{self.sp_win}, diff{self.sp_diff}, min{self.sp_min})')

    def cb(self, msg: LaserScan):
        lo, hi = self.min_range, self.max_range
        r = list(msg.ranges)
        n = len(r)

        # 1) range 필터: 범위 밖은 inf 로
        valid = [False] * n
        for i in range(n):
            v = r[i]
            if v < lo or v > hi or math.isnan(v) or math.isinf(v):
                r[i] = math.inf
            else:
                valid[i] = True

        # 2) speckle 필터: 이웃(같은 물체)이 너무 적은 외딴 점 제거
        if self.sp_on:
            out = list(r)
            w, diff, need = self.sp_win, self.sp_diff, self.sp_min
            for i in range(n):
                if not valid[i]:
                    continue
                neighbors = 0
                for j in range(i - w, i + w + 1):
                    if j == i or j < 0 or j >= n or not valid[j]:
                        continue
                    if abs(r[i] - r[j]) <= diff:   # 거리차 작으면 같은 물체
                        neighbors += 1
                if neighbors < need:               # 외딴 점 = 잡음
                    out[i] = math.inf
            r = out

        msg.ranges = r
        self.pub.publish(msg)


def main():
    rclpy.init()
    node = ScanFilter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
