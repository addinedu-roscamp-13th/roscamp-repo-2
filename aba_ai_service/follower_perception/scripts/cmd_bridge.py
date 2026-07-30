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
    # ⚠️ `/cmd_vel` 직접 발행 금지 — 바퀴로 가는 문은 twist_mux 하나뿐이다
    #    (pinky_bringup/config/twist_mux.yaml). 이 다리는 은퇴했지만
    #    scripts/drive-pi/follow-drive.sh 로 **아직 실행 가능**하다.
    #
    # [2026-07-28] `/cmd_vel_follow` → `/cmd_vel_ai`. **같은 입력을 쓰면 안 된다.**
    #   `/cmd_vel_follow` 는 로봇 로컬 PID(libi_perception follow_node)가 쓰는 입력이다.
    #   거기에 이 다리까지 발행하면 **한 토픽에 제어기가 둘**이 되는데, twist_mux 는
    #   토픽 단위로 중재하므로 그 둘은 구별도 우선순위 부여도 못 한다 —
    #   마지막에 도착한 게 이긴다. 둘 다 20Hz 라 서로 덮어써서 추종이 흔들렸다.
    #   이 노드는 명령이 끊겨도 워치독으로 **zero Twist 를 계속 낸다**(아래 루프).
    #   가만히 있는 게 아니라 적극적으로 방해한다는 뜻이다.
    #
    #   `cmd_vel_ai` 는 twist_mux 에서 priority **40** — 입력 중 맨 아래다.
    #   (2026-07-28 에 80 으로 뒀다가 내렸다: 80 이면 navigation(50)·recovery(60)를 이겨서,
    #    이 다리가 떠 있기만 해도 워치독 zero 가 nav2 를 계속 이겨 주행이 통째로 막힌다.
    #    twist_mux 는 메시지의 **값을 안 본다** — 최신·고우선순위면 그대로 통과시킨다.)
    #   그래서 둘이 같이 떠도 로컬 PID(follow 100)가 언제나 이기고, 이 다리만 쓰는 옛
    #   구성에서는 경쟁자가 없어 그대로 동작한다.
    # [2026-07-29] twist_mux 에서 `cmd_vel_ai` 입력을 **삭제했다.** 이 토픽은 이제
    #   아무도 안 듣는다 — 이 스크립트를 띄워도 바퀴는 안 돈다(그게 의도다).
    #   런처(scripts/drive-pi/follow-drive.sh)도 같이 지웠다. 추종 제어는 로봇 로컬
    #   PID(libi_perception → /cmd_vel_follow)가 유일한 경로다.
    ap.add_argument("--topic", default="/cmd_vel_ai")
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
