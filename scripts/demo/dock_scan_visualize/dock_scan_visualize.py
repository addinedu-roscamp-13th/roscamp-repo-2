#!/usr/bin/env python3
"""라이다 도킹 검출 실시간 시각화 — 실제 `detect()` 가 지금 이 순간 무엇을 보고
어떻게 판단하는지 창 하나로 계속 보여준다.

## 쓰는 법

    python3 scripts/demo/dock_scan_visualize/dock_scan_visualize.py --robot pinky-3

창을 닫거나 Ctrl+C 로 끝낸다.

## Pi 에 부담을 안 주는 이유

`/scan` 구독은 참조만 받는다 — 계산(RANSAC·노치 검출)은 전부 **이 노트북에서** 돈다.
Pi 쪽은 이미 하고 있던 발행 그대로다(구독자가 하나 더 붙는 것뿐). SSH 로 로봇에
들어가지도 않는다 — 노트북이 같은 CycloneDDS 정적 피어 목록에 있어 DDS 로 바로
구독한다(`.env` 의 `LAPTOP_IP`, `~/.bashrc` 의 `CYCLONEDDS_URI`).

## 왜 판정 로직을 다시 안 짜나

여기서 검출을 다시 구현하면 실제 도킹 코드와 갈라질 위험이 생긴다. 이 그림이 보여주는
판단은 로봇이 실제로 내리는 판단과 **같은 코드**(`libi_modes.lidar.detect`)로 만들어야
신뢰할 수 있다 — 그래서 `sector_points`/`fit_wall`/`detect` 를 그대로 불러 쓴다.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# 한글이 네모로 깨지는 것 방지. ⚠️ Noto Sans CJK 는 한 .ttc 파일에 JP/KR/SC/TC/HK
# 여러 서체가 들어있는데, matplotlib 폰트매니저는 파일당 이름을 하나만 잡는다 —
# 이 시스템에서는 그게 우연히 "KR" 이 아니라 "JP" 였다(둘 다 한글 완성형을 담고
# 있는 같은 프로젝트 폰트라 글자는 똑같이 나온다. `fc-list :lang=ko` 로 실제
# 설치 이름을 먼저 확인했다 — 짐작으로 이름을 적지 않는다).
_KO_FONT_CANDIDATES = ("Noto Sans CJK JP", "Noto Sans CJK KR", "NanumGothic", "Malgun Gothic")
_available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _font in _KO_FONT_CANDIDATES:
    if _font in _available:
        matplotlib.rcParams["font.family"] = _font
        break
else:
    print(f"경고: 한글 폰트를 못 찾았다({_KO_FONT_CANDIDATES}) — 글자가 깨질 수 있다",
          file=sys.stderr)
matplotlib.rcParams["axes.unicode_minus"] = False

_LIBI_MODES_SRC = (Path(__file__).resolve().parents[3]
                    / "aba_controller/libi_modes/ros_ws/src/libi_modes")
sys.path.insert(0, str(_LIBI_MODES_SRC))

from libi_modes.lidar.config import LidarDockConfig  # noqa: E402
from libi_modes.lidar.detect import detect, fit_wall, sector_points  # noqa: E402

#: **여기 값은 실측 확인된 것만 적는다** — 추측 금지
#: (CLAUDE.md: "도메인은 실행 명령을 보고 판단하고, 추측으로 쓰지 않는다").
#: pinky-1·pinky-2 는 도메인이 문서마다 달라 실측 전엔 못 채운다 — 그 로봇에서
#: `pgrep -af fsm_node`(또는 bringup) 로 실행 명령의 `--domain-id`/env 를 직접
#: 확인한 뒤 `--domain-id`·`--cyclonedds-uri` 로 넘겨서 쓴다.
ROBOT_DEFAULTS = {
    "pinky-3": {
        "domain_id": 119,
        "cyclonedds_uri": ("file:///home/leekt/aba_controller/"
                           "libi_drive_controller/ros_ws/cyclonedds.xml"),
    },
}


def render_scan(ax, ranges, angle_min: float, angle_increment: float,
                range_min: float, range_max: float,
                cfg: LidarDockConfig, title: str = "") -> bool:
    """스캔 한 장(원시 `LaserScan` 필드 그대로)을 그린다. 노치를 찾았으면 `True`."""
    ax.clear()
    pts = sector_points(ranges, angle_min, angle_increment, range_min, cfg.range_max_m, cfg)
    wall = fit_wall(pts, cfg) if len(pts) else None
    obs = detect(ranges, angle_min, angle_increment, range_min, range_max, cfg)

    if len(pts):
        ax.scatter(pts[:, 0], pts[:, 1], s=8, c="#999", label=f"라이다 점 {len(pts)}개", zorder=2)
    ax.scatter([0], [0], marker="s", s=140, c="black", zorder=5, label="로봇(라이다 원점)")
    ax.annotate("뒤(0°, 도킹 진행 방향) →", (0, 0), xytext=(0.03, -0.05),
               fontsize=8, color="#555")

    if wall is not None:
        inl = pts[wall.inliers]
        ax.scatter(inl[:, 0], inl[:, 1], s=12, c="tab:blue",
                  label=f"벽 inlier {len(inl)}/{len(pts)}", zorder=3)
        wvec = np.array([-wall.normal[1], wall.normal[0]])
        centre = wall.normal * wall.offset
        p1, p2 = centre - wvec * 0.4, centre + wvec * 0.4
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], c="tab:blue", lw=2,
               label=f"벽 fit (yaw={math.degrees(wall.yaw):+.1f}°, rms={wall.rms*1000:.1f}mm)")

    if obs is not None:
        ax.scatter([obs.d], [obs.y], marker="*", s=350, c="tab:red", zorder=6,
                  label=f"노치 검출 d={obs.d*1000:.0f}mm y={obs.y*1000:+.0f}mm")

    if obs is not None:
        status = (f"[성공] 검출 성공 — d={obs.d*1000:.0f}mm  y={obs.y*1000:+.0f}mm  "
                 f"yaw={math.degrees(obs.yaw):+.1f}°  depth={obs.depth*1000:.0f}mm")
    elif wall is None:
        status = "[실패] 검출 실패 — 벽 fit 조차 안 됨(점 부족·RANSAC 실패·yaw 허용치 초과)"
    else:
        status = (f"[실패] 검출 실패 — 벽은 찾음(yaw={math.degrees(wall.yaw):+.1f}°)이지만 "
                 f"노치 신호(깊이·폭)가 기준에 안 맞음")

    ax.set_title(f"{title}\n{status}" if title else status, fontsize=10)
    ax.set_xlabel("x (m) — 로봇 뒤 방향(도킹 진행축)")
    ax.set_ylabel("y (m) — 좌우")
    ax.set_xlim(-0.05, cfg.range_max_m)
    ax.set_ylim(-cfg.range_max_m * 0.7, cfg.range_max_m * 0.7)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=7)
    return obs is not None


def live_view(robot: str, domain_id: int, cyclonedds_uri: str, cfg: LidarDockConfig) -> None:
    """`/scan` 을 직접 구독해 창 하나에 실시간으로 그린다.

    SSH 를 안 쓴다 — 이 노트북 자체가 CycloneDDS 정적 피어 목록에 있어 DDS 로
    바로 구독된다. 로봇에 새로 뭘 얹지도, 계산을 시키지도 않는다 — RANSAC·노치
    검출은 전부 이 프로세스 안에서 돈다.
    """
    # ⚠️ RMW_IMPLEMENTATION 은 rclpy import **전에** 정해져야 한다.
    os.environ["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
    os.environ["CYCLONEDDS_URI"] = cyclonedds_uri
    os.environ["ROS_DOMAIN_ID"] = str(domain_id)

    import rclpy
    from matplotlib.animation import FuncAnimation
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import LaserScan

    rclpy.init()
    node = rclpy.create_node("dock_scan_visualize_live")
    latest = {"msg": None, "n": 0}

    def on_scan(msg: LaserScan) -> None:
        latest["msg"] = msg
        latest["n"] += 1

    node.create_subscription(LaserScan, "/scan", on_scan, qos_profile_sensor_data)
    print(f"[{robot}] /scan 구독 중 — 창을 닫으면 끝난다 (Ctrl+C 도 됨)")

    fig, ax = plt.subplots(figsize=(7, 7))

    def update(_frame):
        rclpy.spin_once(node, timeout_sec=0.05)
        msg = latest["msg"]
        if msg is None:
            ax.clear()
            ax.set_title(f"[{robot}] /scan 대기 중... (라이다·bringup 켜져 있나?)")
            return
        render_scan(ax, msg.ranges, msg.angle_min, msg.angle_increment,
                   msg.range_min, msg.range_max, cfg,
                   title=f"[{robot}] 스캔 #{latest['n']}")

    anim = FuncAnimation(fig, update, interval=100, cache_frame_data=False)
    try:
        plt.show()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--robot", required=True, choices=sorted(ROBOT_DEFAULTS),
                    help="실시간으로 구독할 로봇(예: pinky-3)")
    ap.add_argument("--domain-id", type=int, default=None,
                    help="표에 없는 로봇이면 필수(추측 금지 — 실측값만)")
    ap.add_argument("--cyclonedds-uri", default=None, help="표에 없는 로봇이면 필수")
    args = ap.parse_args()

    defaults = ROBOT_DEFAULTS.get(args.robot, {})
    domain_id = args.domain_id if args.domain_id is not None else defaults.get("domain_id")
    cyclonedds_uri = args.cyclonedds_uri or defaults.get("cyclonedds_uri")
    if domain_id is None or cyclonedds_uri is None:
        ap.error(f"{args.robot} 은 실측 기본값이 없다 — "
                "--domain-id/--cyclonedds-uri 를 직접 넘길 것")

    live_view(args.robot, domain_id, cyclonedds_uri, LidarDockConfig())


if __name__ == "__main__":
    main()
