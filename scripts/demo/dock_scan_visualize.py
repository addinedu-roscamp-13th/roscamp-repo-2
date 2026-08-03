#!/usr/bin/env python3
"""라이다 도킹 검출 시각화 — 실제 `detect()` 가 스캔에서 무엇을 보고 어떻게
판단했는지 그림 한 장(또는 여러 장 → gif)으로 보여준다.

## 쓰는 법

    python3 scripts/demo/dock_scan_visualize.py SCAN.csv -o out.png
    python3 scripts/demo/dock_scan_visualize.py scan_dir/*.csv -o approach.gif   # 여러 장 → 애니메이션

CSV 형식은 `scan_dump.py` 가 찍는 것과 같다: `angle_deg,range_m` 두 열, 0 도 =
로봇 물리적 뒤(`pinky.urdf.xacro:201`, z축 π 회전 장착). 거리 0.0 은 "측정 실패".

## 왜 판정 로직을 다시 안 짜나

여기서 검출을 다시 구현하면 실제 도킹 코드와 갈라질 위험이 생긴다. 이 그림이 보여주는
판단은 로봇이 실제로 내리는 판단과 **같은 코드**(`libi_modes.lidar.detect`)로 만들어야
신뢰할 수 있다 — 그래서 `sector_points`/`fit_wall`/`detect` 를 그대로 불러 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
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

_LIBI_MODES_SRC = (Path(__file__).resolve().parents[2]
                    / "aba_controller/libi_modes/ros_ws/src/libi_modes")
sys.path.insert(0, str(_LIBI_MODES_SRC))

from libi_modes.lidar.config import LidarDockConfig  # noqa: E402
from libi_modes.lidar.detect import detect, fit_wall, sector_points  # noqa: E402

#: RPLIDAR C1 최소 거리(m). CSV 는 range_min/range_max 를 안 들고 있어 여기서 고정한다
#: (config.py 의 stop_m 실측 근거와 같은 값).
ASSUMED_RANGE_MIN_M = 0.05

#: `--robot` 라이브 캡처용. **여기 값은 실측 확인된 것만 적는다** — 추측 금지
#: (CLAUDE.md: "도메인은 실행 명령을 보고 판단하고, 추측으로 쓰지 않는다").
#: pinky-1·pinky-2 는 도메인이 문서마다 달라 실측 전엔 못 채운다 — 그 로봇에서
#: `pgrep -af fsm_node`(또는 bringup) 로 실행 명령의 `--domain-id`/env 를 직접
#: 확인한 뒤 `--domain-id`·`--cyclonedds-uri` 로 넘겨서 쓴다.
ROBOT_DEFAULTS = {
    "pinky-3": {
        "ssh_host": "pinky3_ip",
        "domain_id": 119,
        "cyclonedds_uri": ("file:///home/leekt/aba_controller/"
                           "libi_drive_controller/ros_ws/cyclonedds.xml"),
    },
}


def load_csv(path: str) -> list[tuple[float, float]]:
    with open(path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)  # 머리글
        return [(float(deg), float(dist)) for deg, dist in r]


def to_detect_args(rows: list[tuple[float, float]]):
    """(각도_도, 거리) 목록 → `detect()` 가 받는 (ranges, angle_min, angle_increment).

    ⚠️ 정렬·균등 간격을 전제한다 — `scan_dump.py` 출력이 그 전제를 만족한다.
    """
    rows = sorted(rows, key=lambda row: row[0])
    angle_min = math.radians(rows[0][0])
    angle_increment = (math.radians(rows[1][0] - rows[0][0])
                       if len(rows) > 1 else math.radians(1.0))
    ranges = [d if d > 0.0 else float("inf") for _, d in rows]
    return ranges, angle_min, angle_increment


def render_frame(ax, rows: list[tuple[float, float]], cfg: LidarDockConfig,
                 title: str = "") -> bool:
    """스캔 한 장을 그린다. 노치를 찾았으면 `True`."""
    ax.clear()
    ranges, angle_min, angle_increment = to_detect_args(rows)
    pts = sector_points(ranges, angle_min, angle_increment,
                        ASSUMED_RANGE_MIN_M, cfg.range_max_m, cfg)
    wall = fit_wall(pts, cfg) if len(pts) else None
    obs = detect(ranges, angle_min, angle_increment,
                ASSUMED_RANGE_MIN_M, cfg.range_max_m, cfg)

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


def capture_live(robot: str, count: int, sector_deg: float, domain_id: int,
                 cyclonedds_uri: str, ssh_host: str, out_dir: Path) -> list[str]:
    """`--robot` 로 실제 라이다에서 스캔을 받아 로컬 CSV 로 가져온다.

    ⚠️ 읽기만 한다 — `/scan` 구독뿐이라 로봇을 안 움직인다. 로봇의 fsm_node·
    bringup 등 다른 프로세스는 건드리지 않는다(다른 세션이 그 로봇을 쓰고 있어도
    안전한 이유).
    """
    import subprocess

    remote_script = "/tmp/aba_dock_scan_dump.py"
    remote_prefix = "/tmp/aba_dockviz"
    local_script = str(Path(__file__).resolve().parent / "scan_dump.py")

    print(f"[{robot}] scan_dump.py 전송 중...")
    subprocess.run(["scp", "-q", local_script, f"{ssh_host}:{remote_script}"], check=True)

    env = (f"source /opt/ros/jazzy/setup.bash && "
          f"export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && "
          f"export CYCLONEDDS_URI='{cyclonedds_uri}' && "
          f"export ROS_DOMAIN_ID={domain_id}")
    cmd = (f"{env} && timeout {count * 3 + 15} python3 {remote_script} "
          f"--count {count} --sector {sector_deg} --out {remote_prefix}")
    print(f"[{robot}] {count}장 캡처 중(±{sector_deg}도)...")
    try:
        subprocess.run(["ssh", ssh_host, cmd], check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"[{robot}] 캡처 실패(ssh 종료 코드 {exc.returncode}) — "
            "가장 흔한 원인은 /scan 이 아직 안 뜬 것이다(라이다·bringup 확인). "
            f"직접 확인: ssh {ssh_host} \"source /opt/ros/jazzy/setup.bash && "
            f"export ROS_DOMAIN_ID={domain_id} && ros2 topic hz /scan\"") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["scp", "-q", f"{ssh_host}:{remote_prefix}_*.csv", str(out_dir)], check=True)
    files = sorted(str(p) for p in out_dir.glob("aba_dockviz_*.csv"))
    if not files:
        raise RuntimeError(f"[{robot}] 캡처된 스캔이 없다 — /scan 이 살아있는지 확인할 것")
    print(f"[{robot}] {len(files)}장 받음 → {out_dir}")
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scans", nargs="*", help="scan CSV 파일(들) — 여러 장이면 순서대로 애니메이션. "
                    "--robot 을 쓰면 생략한다")
    ap.add_argument("-o", "--out", default="dock_scan.png",
                    help="출력 파일. 확장자가 .gif 면 애니메이션, 아니면 첫 장만 정지 이미지")
    ap.add_argument("--robot", choices=sorted(ROBOT_DEFAULTS), default=None,
                    help="CSV 대신 이 로봇에서 실시간으로 캡처한다(예: pinky-3)")
    ap.add_argument("--count", type=int, default=5, help="--robot 캡처 장수")
    ap.add_argument("--sector", type=float, default=90.0,
                    help="--robot 캡처 반각(도) — detect() 자체 창(기본 60도)보다 "
                    "넓게 잡아야 그림에서 잘려 보이지 않는다")
    ap.add_argument("--domain-id", type=int, default=None,
                    help="--robot 전용 오버라이드. 표에 없는 로봇이면 필수")
    ap.add_argument("--cyclonedds-uri", default=None, help="--robot 전용 오버라이드")
    ap.add_argument("--ssh-host", default=None, help="--robot 전용 오버라이드(기본: ~/.ssh/config 별칭)")
    args = ap.parse_args()

    if args.robot:
        defaults = ROBOT_DEFAULTS.get(args.robot, {})
        domain_id = args.domain_id if args.domain_id is not None else defaults.get("domain_id")
        cyclonedds_uri = args.cyclonedds_uri or defaults.get("cyclonedds_uri")
        ssh_host = args.ssh_host or defaults.get("ssh_host")
        if domain_id is None or cyclonedds_uri is None or ssh_host is None:
            ap.error(f"{args.robot} 은 실측 기본값이 없다 — "
                    "--domain-id/--cyclonedds-uri/--ssh-host 를 직접 넘길 것")
        tmp = Path(f"/tmp/aba_dockviz_{args.robot}")
        files = capture_live(args.robot, args.count, args.sector, domain_id,
                            cyclonedds_uri, ssh_host, tmp)
    elif args.scans:
        files = sorted(args.scans)
    else:
        ap.error("scan CSV 파일을 주거나 --robot 을 지정할 것")

    cfg = LidarDockConfig()

    fig, ax = plt.subplots(figsize=(7, 7))
    if len(files) == 1 or not args.out.lower().endswith(".gif"):
        rows = load_csv(files[0])
        found = render_frame(ax, rows, cfg, title=Path(files[0]).name)
        fig.tight_layout()
        fig.savefig(args.out, dpi=120)
        print(f"저장: {args.out} ({'검출 성공' if found else '검출 실패'})")
        return

    from matplotlib.animation import FuncAnimation, PillowWriter
    frames_rows = [load_csv(f) for f in files]

    def update(i):
        render_frame(ax, frames_rows[i], cfg, title=Path(files[i]).name)

    anim = FuncAnimation(fig, update, frames=len(files), interval=500)
    fig.tight_layout()
    anim.save(args.out, writer=PillowWriter(fps=2))
    print(f"저장: {args.out} ({len(files)}프레임)")


if __name__ == "__main__":
    main()
