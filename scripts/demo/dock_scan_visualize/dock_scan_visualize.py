#!/usr/bin/env python3
"""라이다 도킹 검출 실시간 시각화 — 실제 `detect()` 가 지금 이 순간 무엇을 보고
어떻게 판단하는지 창 하나로 계속 보여준다.

## 쓰는 법

    python3 scripts/demo/dock_scan_visualize/dock_scan_visualize.py --robot pinky-3

창 안에서 `s` 키 — 지금 프레임을 `/tmp/aba_dockviz_<시각>.{csv,png}` 로 저장한다
(이상해 보이는 순간을 잡아 나중에 원본 숫자로 확인할 때 쓴다).
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
import shlex
import time
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

#: `domain_id` **만** 로봇마다 다르다(도메인 = 로봇). `CYCLONEDDS_URI` 는 여기
#: 안 둔다 — 그건 로봇 몫이 아니라 **이 노트북**의 피어 목록이라 어느 로봇을
#: 고르든 항상 같다. 노트북 `~/.bashrc` 가 이미 export 해 둔 값을 그대로 쓴다
#: (`os.environ.setdefault` — 이미 있으면 안 건드린다).
#: ⚠️ **여기 값은 실측 확인된 것만 적는다** — 추측 금지
#: (CLAUDE.md: "도메인은 실행 명령을 보고 판단하고, 추측으로 쓰지 않는다").
#: pinky-1·pinky-2 는 도메인이 문서마다 달라 실측 전엔 못 채운다 — 그 로봇에서
#: `pgrep -af fsm_node`(또는 bringup) 로 실행 명령의 `--domain-id` 를 직접 확인한
#: 뒤 `--domain-id` 로 넘겨서 쓴다.
ROBOT_DEFAULTS = {
    "pinky-3": {"domain_id": 119},
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
        ax.scatter(pts[:, 0], pts[:, 1], s=6, c="#999", label=f"라이다 점 {len(pts)}개", zorder=2)
    ax.scatter([0], [0], marker="s", s=40, c="black", zorder=5, label="로봇(라이다 원점)")

    if wall is not None:
        inl = pts[wall.inliers]
        ax.scatter(inl[:, 0], inl[:, 1], s=8, c="tab:blue",
                  label=f"벽 inlier {len(inl)}/{len(pts)}", zorder=3)
        wvec = np.array([-wall.normal[1], wall.normal[0]])
        centre = wall.normal * wall.offset
        p1, p2 = centre - wvec * 0.4, centre + wvec * 0.4
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], c="tab:blue", lw=1,
               label=f"벽 fit (yaw={math.degrees(wall.yaw):+.1f}°, rms={wall.rms*1000:.1f}mm)")

    # ── 목표 지점(정지선) — 로직이 "여기로 가야 한다"고 보는 자리 ─────────────
    #   y=0(벽에 정렬), x=stop_m(그 이상 못 들어간다, C1 min range 바닥). 검출
    #   여부와 무관하게 항상 찍는다 — 지금 얼마나 남았는지 눈으로 바로 비교되게.
    ax.scatter([cfg.stop_m], [0], marker="X", s=60, c="tab:green", zorder=7,
              label=f"정지 목표(stop_m={cfg.stop_m*1000:.0f}mm, y=0)")
    ax.axvline(cfg.stop_m, color="tab:green", lw=0.6, ls=":", alpha=0.5)

    if obs is not None:
        ax.scatter([obs.d], [obs.y], marker="*", s=90, c="tab:red", zorder=6,
                  label=f"노치 검출 d={obs.d*1000:.0f}mm y={obs.y*1000:+.0f}mm")
        # 검출 지점 → 목표 지점. 이 화살표 길이가 "얼마나 더 가야 하는지"다.
        ax.annotate("", xy=(cfg.stop_m, 0.0), xytext=(obs.d, obs.y),
                   arrowprops=dict(arrowstyle="->", color="tab:orange", lw=1, alpha=0.8))
        remain_d = (obs.d - cfg.stop_m) * 1000
        ax.annotate(f"{remain_d:.0f}mm · {obs.y*1000:+.0f}mm",
                   ((obs.d + cfg.stop_m) / 2, obs.y / 2), fontsize=6, color="tab:orange",
                   ha="center", va="bottom",
                   xytext=(0, 4), textcoords="offset points")

    if obs is not None:
        status = (f"[성공] 검출 성공 — d={obs.d*1000:.0f}mm  y={obs.y*1000:+.0f}mm  "
                 f"yaw={math.degrees(obs.yaw):+.1f}°  depth={obs.depth*1000:.0f}mm")
    elif wall is None:
        status = "[실패] 검출 실패 — 벽 fit 조차 안 됨(점 부족·RANSAC 실패·yaw 허용치 초과)"
    else:
        status = (f"[실패] 검출 실패 — 벽은 찾음(yaw={math.degrees(wall.yaw):+.1f}°)이지만 "
                 f"노치 신호(깊이·폭)가 기준에 안 맞음")

    ax.set_title(f"{title}\n{status}" if title else status, fontsize=10)
    ax.set_xlabel("x (m) — 로봇 뒤(도킹 진행축). 작을수록 벽에 가깝다, 초록 X 가 정지선")
    ax.set_ylabel("y (m) — 좌우(벽 방향). 0 이 정렬된 상태")
    ax.set_xlim(-0.05, cfg.range_max_m)
    ax.set_ylim(-cfg.range_max_m * 0.7, cfg.range_max_m * 0.7)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=7)
    return obs is not None


#: `source /opt/ros/jazzy/setup.bash` 를 손으로 안 쳐도 되게 — 이미 소싱돼 있으면
#: (`AMENT_PREFIX_PATH` 존재로 판단) 아무것도 안 하고, 아니면 서브셸에서 소싱한
#: 뒤 그 결과 환경변수를 그대로 가져온다.
ROS_SETUP_BASH = "/opt/ros/jazzy/setup.bash"


def _ensure_ros_env(setup_bash: str = ROS_SETUP_BASH) -> None:
    """ROS2 환경이 없으면(`AMENT_PREFIX_PATH` 기준) `source` 한 뒤 **이 프로세스를
    통째로 다시 실행한다.**

    ⚠️ `os.environ` 만 고쳐 쓰는 방식은 실기로 안 된다는 것을 확인했다 —
    `PYTHONPATH` 는 인터프리터가 **시작할 때만** `sys.path` 로 읽으므로 이미 뜬
    프로세스엔 안 먹고(따로 `sys.path` 에 넣어 해결 가능), `LD_LIBRARY_PATH` 는
    동적 링커가 얽혀 있어 그렇게 해도 `librcl_action.so` 를 못 찾았다(실측
    `ImportError`). 두 캐시를 다 우회하는 유일한 방법은 process 를 새로 켜는
    것 — `exec` 로 통째로 갈아 끼운다(성공하면 이 함수는 돌아오지 않는다).
    """
    if "AMENT_PREFIX_PATH" in os.environ:
        return
    if not os.path.isfile(setup_bash):
        print(f"경고: {setup_bash} 이 없다 — ROS2 환경이 이미 갖춰졌다고 가정한다",
             file=sys.stderr)
        return
    print(f"{setup_bash} 소싱 중... (재실행)")
    cmd = f"source {setup_bash} && exec python3 " + " ".join(shlex.quote(a) for a in sys.argv)
    os.execvp("bash", ["bash", "-c", cmd])


def _save_snapshot(msg, fig) -> None:
    """지금 프레임을 CSV(`angle_deg,range_m`)+PNG 로 `/tmp` 에 남긴다.

    검출이 이상해 보이는 순간을 잡아 나중에(또는 다른 사람에게) 원본 숫자로
    확인시키기 위한 것 — 화면만으로는 RANSAC 이 어느 점을 골랐는지 정확히
    안 보인다.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    rows = []
    for i, r in enumerate(msg.ranges):
        a = math.atan2(math.sin(msg.angle_min + i * msg.angle_increment),
                       math.cos(msg.angle_min + i * msg.angle_increment))
        dist = r if (isinstance(r, float) and math.isfinite(r) and r > 0.0) else 0.0
        rows.append((math.degrees(a), dist))
    rows.sort(key=lambda row: row[0])

    csv_path = f"/tmp/aba_dockviz_{ts}.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("angle_deg,range_m\n")
        for deg, dist in rows:
            f.write(f"{deg:.3f},{dist:.4f}\n")

    png_path = f"/tmp/aba_dockviz_{ts}.png"
    fig.savefig(png_path, dpi=120)
    print(f"저장: {csv_path} , {png_path}")


def live_view(robot: str, domain_id: int, cyclonedds_uri: str, cfg: LidarDockConfig) -> None:
    """`/scan` 을 직접 구독해 창 하나에 실시간으로 그린다.

    SSH 를 안 쓴다 — 이 노트북 자체가 CycloneDDS 정적 피어 목록에 있어 DDS 로
    바로 구독된다. 로봇에 새로 뭘 얹지도, 계산을 시키지도 않는다 — RANSAC·노치
    검출은 전부 이 프로세스 안에서 돈다.
    """
    _ensure_ros_env()
    # ⚠️ RMW_IMPLEMENTATION 은 rclpy import **전에** 정해져야 한다.
    # ⚠️ `setdefault` 다 — 이미 셸(`~/.bashrc`)이 export 해 뒀으면 그 값을 쓴다.
    #    특히 `CYCLONEDDS_URI` 는 여기서 값을 지어내지 않는다 — 로봇 쪽 file://
    #    경로를 노트북에 강제로 씌운 적이 있었다(2026-08-04 실기: "can't open
    #    configuration file file:///home/leekt/..." 로 죽었다). 셸에 없으면
    #    `--cyclonedds-uri` 로 명시하게 한다.
    os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    os.environ.setdefault("ROS_DOMAIN_ID", str(domain_id))
    if cyclonedds_uri is not None:
        os.environ.setdefault("CYCLONEDDS_URI", cyclonedds_uri)
    elif "CYCLONEDDS_URI" not in os.environ:
        print("경고: CYCLONEDDS_URI 가 셸에도 없고 --cyclonedds-uri 도 안 줬다 — "
             "디스커버리가 안 될 수 있다", file=sys.stderr)

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
    print(f"[{robot}] /scan 구독 중 — 's' 로 지금 프레임 저장, 창을 닫으면 끝난다(Ctrl+C 도 됨)")

    fig, ax = plt.subplots(figsize=(7, 7))

    def on_key(event) -> None:
        if event.key != "s":
            return
        if latest["msg"] is None:
            print("아직 스캔을 못 받았다 — 저장할 게 없다")
            return
        _save_snapshot(latest["msg"], fig)

    fig.canvas.mpl_connect("key_press_event", on_key)

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
    ap.add_argument("--cyclonedds-uri", default=None,
                    help="셸에 이미 있으면 안 줘도 된다(그 값을 그대로 쓴다)")
    args = ap.parse_args()

    defaults = ROBOT_DEFAULTS.get(args.robot, {})
    domain_id = args.domain_id if args.domain_id is not None else defaults.get("domain_id")
    if domain_id is None:
        ap.error(f"{args.robot} 은 실측 도메인이 없다 — --domain-id 를 직접 넘길 것")

    live_view(args.robot, domain_id, args.cyclonedds_uri, LidarDockConfig())


if __name__ == "__main__":
    main()
