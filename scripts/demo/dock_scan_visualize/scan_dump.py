#!/usr/bin/env python3
"""후방 스캔 덤프 — 라이다가 도크에서 무엇을 보는지 눈으로 본다.

## 왜 제어기보다 먼저인가

도크의 노치는 깊이 2.5cm 이고 RPLIDAR C1 의 거리 분해능은 15mm 다. 특징이 눈금의
1.67배밖에 안 되므로, **실제로 보이는지는 계산이 아니라 측정이 답한다.** 이 도구
없이 제어기를 먼저 만들면 "안 붙는다"의 원인이 검출인지 제어인지 못 가른다.

## 쓰는 법

    source /opt/ros/jazzy/setup.bash
    python3 scripts/scan_dump.py --count 20 --out /tmp/dock_scan

`/tmp/dock_scan_000.csv` ... 이 생긴다. 각 파일은 `angle_deg,range_m` 두 열이다.
거리 `0.0` 은 **측정 실패**를 뜻한다(값이 없는 것과 구분하려고 지우지 않는다).

⚠️ 0도는 로봇의 **물리적 뒤**다. `rplidar_link` 가 z축 π 회전으로 장착돼 있다
   (`pinky.urdf.xacro:201`). 후진 도킹의 진행 방향이 곧 0도다.
"""
import argparse
import math


def rows_from_scan(ranges, angle_min, angle_increment, sector_half_deg):
    """스캔 → `(각도_도, 거리_m)` 목록. 각도 오름차순, 못 잰 광선은 거리 0.0.

    변환을 여기 한 곳에서만 한다 — 각도 메타데이터가 실제로 존재하는 자리이고,
    아래로 내려보내면 모든 소비자가 같은 실수를 반복할 자리가 생긴다.
    """
    rows = []
    for i, r in enumerate(ranges):
        a = angle_min + i * angle_increment
        a = math.atan2(math.sin(a), math.cos(a))    # (-pi, pi] 로 감는다
        deg = math.degrees(a)
        if abs(deg) > sector_half_deg:
            continue
        dist = r if (isinstance(r, float) and math.isfinite(r) and r > 0.0) else 0.0
        rows.append((deg, float(dist)))
    rows.sort(key=lambda row: row[0])
    return rows


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("angle_deg,range_m\n")
        for deg, dist in rows:
            f.write(f"{deg:.3f},{dist:.4f}\n")


def main():
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import LaserScan

    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="/scan")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--sector", type=float, default=60.0, help="±도")
    ap.add_argument("--out", default="/tmp/dock_scan")
    args = ap.parse_args()

    rclpy.init()
    node = rclpy.create_node("scan_dump")
    saved = {"n": 0}

    def on_scan(msg):
        if saved["n"] >= args.count:
            return
        rows = rows_from_scan(msg.ranges, msg.angle_min, msg.angle_increment, args.sector)
        path = f"{args.out}_{saved['n']:03d}.csv"
        _write_csv(path, rows)
        near = min((d for _, d in rows if d > 0.0), default=0.0)
        node.get_logger().info(
            f"[{saved['n']:03d}] {path} · 광선 {len(rows)}개 · frame={msg.header.frame_id} "
            f"· 최근접 {near:.3f}m")
        saved["n"] += 1

    node.create_subscription(LaserScan, args.topic, on_scan, qos_profile_sensor_data)
    node.get_logger().info(f"{args.topic} 에서 {args.count}장을 받는다 (±{args.sector}도)")
    while rclpy.ok() and saved["n"] < args.count:
        rclpy.spin_once(node, timeout_sec=0.5)
    node.get_logger().info(f"완료 — {saved['n']}장 저장")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
