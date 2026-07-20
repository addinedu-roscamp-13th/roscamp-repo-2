#!/usr/bin/env python3
"""Robot (Pi) side: receive (linear_x, angular_z) over UDP from the AI server and
publish geometry_msgs/Twist to /cmd_vel at a fixed rate, with a SAFETY WATCHDOG
(publish zero if no fresh command) and speed clamps.

This is the ONLY ROS piece of the simple drive path. It is intentionally a thin,
standalone node so it can be SWAPPED for follower_control later (same /cmd_vel
output). Run on the Pi with ROS 2 sourced:

    python3 scripts/cmd_bridge.py --port 6002
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.cmd_channel import CmdReceiver
from scripts.lidar_avoid import sectors4, avoid_cmd


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def main():
    ap = argparse.ArgumentParser(description="UDP cmd -> /cmd_vel bridge (Pi side)")
    ap.add_argument("--port", type=int, default=6002, help="UDP cmd port from AI server")
    ap.add_argument("--topic", default="/cmd_vel")
    ap.add_argument("--rate", type=float, default=20.0, help="publish Hz")
    ap.add_argument("--timeout", type=float, default=0.5,
                    help="STOP if no cmd received within this many seconds (watchdog)")
    ap.add_argument("--max-linear", dest="max_linear", type=float, default=0.15,
                    help="m/s clamp (safety, start low)")
    ap.add_argument("--max-angular", dest="max_angular", type=float, default=0.8,
                    help="rad/s clamp (safety)")
    ap.add_argument("--scan-topic", dest="scan_topic", default="/scan")
    ap.add_argument("--no-avoid", dest="no_avoid", action="store_true",
                    help="disable LiDAR obstacle avoidance")
    ap.add_argument("--scan-timeout", dest="scan_timeout", type=float, default=0.5,
                    help="fail-safe: no forward if no fresh /scan within this (avoidance on)")
    ap.add_argument("--flip-180", dest="flip_180", action="store_true",
                    help="LiDAR mounted rotated 180 deg: swap front<->back and left<->right")
    ap.add_argument("--debug-scan", dest="debug_scan", action="store_true",
                    help="log front/left/right distances ~1Hz + avoidance actions")
    args = ap.parse_args()

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from geometry_msgs.msg import Twist
    from sensor_msgs.msg import LaserScan

    rclpy.init()
    node = Node("cmd_bridge")
    pub = node.create_publisher(Twist, args.topic, 10)
    recv = CmdReceiver(args.port)

    scan = {"data": None, "t": 0.0}             # (ranges, angle_min, angle_inc) + recv time
    if not args.no_avoid:
        def on_scan(msg):
            scan["data"] = (list(msg.ranges), msg.angle_min, msg.angle_increment)
            scan["t"] = time.monotonic()
        node.create_subscription(LaserScan, args.scan_topic, on_scan, qos_profile_sensor_data)

    node.get_logger().info(
        f"cmd_bridge: UDP :{args.port} -> {args.topic} "
        f"(watchdog {args.timeout}s, max_lin {args.max_linear}, max_ang {args.max_angular}, "
        f"avoid={'OFF' if args.no_avoid else args.scan_topic}, "
        f"flip180={'ON' if args.flip_180 else 'off'})")

    def tick():
        t = Twist()
        v = recv.latest()
        if v is not None and v[2] <= args.timeout:          # fresh command
            lin, ang = float(v[0]), float(v[1])
            if not args.no_avoid:
                fresh = (scan["data"] is not None
                         and (time.monotonic() - scan["t"]) < args.scan_timeout)
                if fresh:
                    front, back, left, right = sectors4(*scan["data"], flip_180=args.flip_180)
                    if args.debug_scan:
                        node.get_logger().info(
                            f"scan F={front:.2f} B={back:.2f} L={left:.2f} R={right:.2f}",
                            throttle_duration_sec=1.0)
                    nlin, nang, reason = avoid_cmd(lin, ang, front, back, left, right)
                    if reason != "clear":
                        node.get_logger().info(
                            f"avoid[{reason}] F={front:.2f} B={back:.2f} L={left:.2f} R={right:.2f} "
                            f"lin {lin:.2f}->{nlin:.2f} ang {ang:+.2f}->{nang:+.2f}",
                            throttle_duration_sec=0.5)
                    lin, ang = nlin, nang
                elif lin > 0.0:
                    lin = 0.0     # fail-safe: no fresh LiDAR -> don't drive forward blind
            t.linear.x = _clamp(lin, -args.max_linear, args.max_linear)
            t.angular.z = _clamp(ang, -args.max_angular, args.max_angular)
        # else: stale / none cmd -> zero Twist (STOP)
        pub.publish(t)

    node.create_timer(1.0 / args.rate, tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        pub.publish(Twist())        # final stop
        recv.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
