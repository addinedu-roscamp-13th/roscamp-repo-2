#!/usr/bin/env python3
"""라이다 도킹 검출 실시간 시각화 — 실제 `detect()` 가 지금 이 순간 무엇을 보고
어떻게 판단하는지 창 하나로 계속 보여준다.

## 쓰는 법

    python3 scripts/demo/dock_scan_visualize/dock_scan_visualize.py --robot pinky-3

창 안에서:
  `s`        지금 프레임을 `/tmp/aba_dockviz_<시각>.{csv,png}` 로 저장
             (이상해 보이는 순간을 원본 숫자로 확인할 때)
  `v`        녹화 시작/정지 — 프레임을 낱장 PNG 로 계속 쌓다가 정지하면
             ffmpeg 로 mp4 까지 만든다(`/tmp/aba_dockviz_rec_<시각>/`, `.mp4`)
  `r`        국면(SETTLE→SEARCH→ACQUIRE→...) 재시작 — 로봇을 새로 놓고 누른다
  스크롤휠    확대/축소, `0` 확대 초기화
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
import shutil
import signal
import subprocess
import time
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import yaml

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

# ⚠️ 실기: 's' 를 누르면 내 저장(`_save_snapshot`)뿐 아니라 matplotlib 기본
#    단축키(`keymap.save`)도 같이 발동해 `Figure_1.png` 를 홈 디렉터리에 또
#    남겼다 — 사용자가 "어디 저장된 거냐" 헷갈린 원인이었다. 같은 문제가
#    'r'(→ keymap.home, 확대 리셋과 겹침)·'v'(→ keymap.forward, 뷰 히스토리
#    앞으로 가기와 겹침)에도 있어서 셋 다 기본 바인딩을 끈다.
for _keymap, _key in (("keymap.save", "s"), ("keymap.home", "r"), ("keymap.forward", "v")):
    if _key in matplotlib.rcParams[_keymap]:
        matplotlib.rcParams[_keymap].remove(_key)

_LIBI_MODES_SRC = (Path(__file__).resolve().parents[3]
                    / "aba_controller/libi_modes/ros_ws/src/libi_modes")
sys.path.insert(0, str(_LIBI_MODES_SRC))

from libi_modes.lidar.approach import LidarApproach  # noqa: E402
from libi_modes.lidar.config import LidarDockConfig  # noqa: E402
from libi_modes.lidar.detect import detect, detect_near, fit_wall, sector_points  # noqa: E402

_PARAMS_YAML = _LIBI_MODES_SRC / "config/params.yaml"


def load_cfg() -> LidarDockConfig:
    """실제 로봇이 쓰는 `config/params.yaml` 값으로 만든다.

    `LidarDockConfig()` 맨 기본값만 쓰면 안 된다 — 클래스 기본값 중 일부는
    아직 현장 실측 전 자리표시자다(실기: `stop_m` 기본 0.065 인데 현장 실측값은
    0.05, 이 도구가 그 차이를 숨기면 신뢰가 깨진다). params.yaml 최상위 키가
    `libi_modes:` 그대로라 `/**`/`ros__parameters` 감싸기가 없다
    (`test_lidar_config.py` 의 같은 경로 탐색 참고).
    """
    if not _PARAMS_YAML.exists():
        print(f"경고: {_PARAMS_YAML} 없음 — LidarDockConfig 기본값을 쓴다"
             "(현장 실측값과 다를 수 있다)", file=sys.stderr)
        return LidarDockConfig()
    doc = yaml.safe_load(_PARAMS_YAML.read_text(encoding="utf-8"))
    node = doc.get("libi_modes", doc)
    node = node.get("/**", node)
    params = node.get("ros__parameters", node)
    lidar_dock = params.get("returning", {}).get("lidar_dock", {})
    seen = []
    cfg = LidarDockConfig.from_params(lidar_dock, on_unknown=seen.append)
    if seen:
        print(f"경고: params.yaml 에 모르는 lidar_dock 키가 있다(오타?): {seen}",
             file=sys.stderr)
    return cfg

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
                cfg: LidarDockConfig, title: str = "",
                xlim: tuple[float, float] | None = None,
                ylim: tuple[float, float] | None = None) -> tuple[bool, bool]:
    """스캔 한 장(원시 `LaserScan` 필드 그대로)을 그린다.

    `xlim`/`ylim` 을 안 주면 `cfg.range_max_m` 기준 기본값을 쓴다. 라이브 뷰가
    확대 상태를 프레임마다 넘겨줄 자리 — `ax.clear()` 를 매 프레임 부르므로
    (아래) 이 함수가 매번 기본 범위로 되돌리면 툴바/스크롤 확대가 무의미해진다.
    """
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

    # ── 도착 판정 — 실제 DockApproach 가 DONE(=정지 트리거)을 내는 바로 그 조건 ──
    #   `approach.py` 의 `step()` 그대로다: `d <= stop_m` 이 아니라 "한 걸음
    #   (min_step)보다 적게 남았나" — 불감시간을 감안해도 못 지나치게 하는 그
    #   조건이다. 여기선 EMA 필터 없는 단일 프레임 판정이라 실제 로봇보다 프레임
    #   마다 살짝 더 튈 수 있다(참고용 — 실제 정지는 로봇 쪽 상태기계가 낸다).
    min_step = cfg.pulse_min_s * cfg.v_near_mps
    arrived = obs is not None and (obs.d - cfg.stop_m) < min_step

    if obs is not None:
        status = (f"[성공] 검출 성공 — d={obs.d*1000:.0f}mm  y={obs.y*1000:+.0f}mm  "
                 f"yaw={math.degrees(obs.yaw):+.1f}°  depth={obs.depth*1000:.0f}mm")
    elif wall is None:
        status = "[실패] 검출 실패 — 벽 fit 조차 안 됨(점 부족·RANSAC 실패·yaw 허용치 초과)"
    else:
        status = (f"[실패] 검출 실패 — 벽은 찾음(yaw={math.degrees(wall.yaw):+.1f}°)이지만 "
                 f"노치 신호(깊이·폭)가 기준에 안 맞음")

    ax.set_title(f"{title}\n{status}" if title else status, fontsize=10)
    if arrived:
        ax.text(0.5, 1.14, "도착 — 정지 트리거 조건 만족 (실제 로봇이라면 여기서 DONE)",
               transform=ax.transAxes, ha="center", fontsize=9, color="white",
               bbox=dict(facecolor="tab:green", edgecolor="none", pad=4))
    ax.set_xlabel("x (m) — 로봇 뒤(도킹 진행축). 작을수록 벽에 가깝다, 초록 X 가 정지선")
    ax.set_ylabel("y (m) — 좌우(벽 방향). 0 이 정렬된 상태")
    ax.set_xlim(xlim if xlim is not None else (-0.05, cfg.range_max_m))
    ax.set_ylim(ylim if ylim is not None else (-cfg.range_max_m * 0.7, cfg.range_max_m * 0.7))
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=7)
    return obs is not None, arrived


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


def _stop_recording(rec_dir: Path, fps: float) -> None:
    """녹화 프레임들을 mp4 로 합친다. `ffmpeg` 없으면 PNG 들만 남긴다.

    프레임을 낱장 PNG 로도 남기는 이유 — 창을 못 보는 쪽(코드로 확인하는 쪽)이
    특정 순간만 골라 열어볼 수 있어야 한다. mp4 는 사람이 훑어보기 편한 결과물,
    PNG 시퀀스는 그 원본이다.
    """
    n = len(list(rec_dir.glob("frame_*.png")))
    if n == 0:
        print(f"녹화 프레임이 없다 — {rec_dir} 지운다")
        rec_dir.rmdir()
        return
    mp4_path = rec_dir.with_suffix(".mp4")
    if shutil.which("ffmpeg") is None:
        print(f"녹화 종료: 프레임 {n}장 → {rec_dir} (ffmpeg 없어서 mp4 변환은 건너뜀)")
        return
    proc = subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(rec_dir / "frame_%05d.png"),
         "-pix_fmt", "yuv420p", str(mp4_path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"녹화 종료: 프레임 {n}장 → {rec_dir} (ffmpeg 변환 실패, 로그: {proc.stderr[-300:]})")
    else:
        print(f"녹화 종료: {mp4_path} ({n}프레임) — 낱장은 {rec_dir} 에 그대로 있다")


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
    print(f"[{robot}] /scan 구독 중 — 's' 한 장 저장, 'v' 녹화 시작/정지, 'r' 국면 재시작, "
         "스크롤휠 확대, '0' 확대 초기화, 창을 닫으면 끝난다(Ctrl+C 도 됨)")

    fig, ax = plt.subplots(figsize=(7, 7))

    # ⚠️ 실기: Ctrl+C 가 안 먹었다 — TkAgg 의 `plt.show()` 는 C 레벨 블로킹
    #    루프라 파이썬 시그널을 즉시 못 받는다. 그 상태에서 `update()` 가
    #    계속 도니, 이미 `rclpy.shutdown()` 이 불린(다른 경로로 살짝 죽다 만)
    #    뒤에도 `spin_once` 가 또 불려 "context is not valid" 를 반복 찍으며
    #    창이 안 닫혔다. SIGINT 핸들러를 직접 걸어 `plt.close()` 로 확실히
    #    깨운다 — 이게 Tk 블로킹 루프를 Ctrl+C 로 끊는 표준 우회다.
    def _on_sigint(_sig, _frame) -> None:
        stopped["v"] = True
        plt.close(fig)

    signal.signal(signal.SIGINT, _on_sigint)

    #: `v` 로 토글하는 녹화 상태. `dir=None` 이면 꺼진 것.
    recording = {"dir": None, "n": 0}
    REC_FPS = 10.0  # update() 주기(interval=100ms)와 맞춘다

    def on_key(event) -> None:
        if event.key == "s":
            if latest["msg"] is None:
                print("아직 스캔을 못 받았다 — 저장할 게 없다")
                return
            _save_snapshot(latest["msg"], fig)
        elif event.key == "r":
            state["machine"] = LidarApproach(cfg)
            print(f"[{robot}] 국면 재시작 — SETTLE 부터 다시(로봇을 새로 놓고 눌렀다면 맞다)")
        elif event.key == "0":
            view["xlim"] = None
            view["ylim"] = None
            print(f"[{robot}] 확대 초기화")
        elif event.key == "v":
            if recording["dir"] is None:
                rec_dir = Path(f"/tmp/aba_dockviz_rec_{time.strftime('%Y%m%d_%H%M%S')}")
                rec_dir.mkdir(parents=True, exist_ok=True)
                recording["dir"] = rec_dir
                recording["n"] = 0
                print(f"[{robot}] 녹화 시작 → {rec_dir}")
            else:
                rec_dir = recording["dir"]
                recording["dir"] = None
                _stop_recording(rec_dir, REC_FPS)

    fig.canvas.mpl_connect("key_press_event", on_key)

    # ── 확대 — 스크롤 휠. `None` 이면 `render_scan` 기본 범위를 쓴다. ──────────
    #   툴바/드래그 확대를 안 쓰는 이유: `render_scan` 이 매 프레임 `ax.clear()`
    #   를 부르므로(위 docstring) 그쪽 확대 상태는 다음 프레임에 그냥 지워진다.
    #   확대 값을 직접 들고 있다가 매 프레임 `render_scan` 에 넘겨야 유지된다.
    view = {"xlim": None, "ylim": None}

    def on_scroll(event) -> None:
        if event.inaxes != ax or event.xdata is None or event.ydata is None:
            return
        xlim0 = view["xlim"] if view["xlim"] is not None else ax.get_xlim()
        ylim0 = view["ylim"] if view["ylim"] is not None else ax.get_ylim()
        scale = 0.85 if event.button == "up" else 1 / 0.85
        xc, yc = event.xdata, event.ydata
        view["xlim"] = (xc - (xc - xlim0[0]) * scale, xc + (xlim0[1] - xc) * scale)
        view["ylim"] = (yc - (yc - ylim0[0]) * scale, yc + (ylim0[1] - yc) * scale)

    fig.canvas.mpl_connect("scroll_event", on_scroll)

    # ── 실제 상태기계까지 같이 돌린다 — "지금 국면에서 얼마로 움직일 것인가" ──
    #   `render_scan` 은 이 프레임 하나의 검출만 보여준다. 실제로 로봇이
    #   낼 명령(선속도·각속도)은 국면(SETTLE→SEARCH→ACQUIRE→...)과 이전
    #   프레임들의 EMA 필터에 달려 있어 `LidarApproach` 를 통째로 안 돌리면
    #   못 보여준다 — 여기서도 그 클래스를 그대로 쓴다.
    search_cfg = replace(cfg, sector_half_deg=cfg.search_half_deg,
                        wall_yaw_max_rad=cfg.search_wall_yaw_max_rad)
    state = {"machine": LidarApproach(cfg)}
    was_arrived = {"v": False}
    stopped = {"v": False}

    def update(_frame):
        if stopped["v"] or not rclpy.ok():
            return
        rclpy.spin_once(node, timeout_sec=0.05)
        msg = latest["msg"]
        if msg is None:
            ax.clear()
            ax.set_title(f"[{robot}] /scan 대기 중... (라이다·bringup 켜져 있나?)")
            return
        _found, arrived = render_scan(ax, msg.ranges, msg.angle_min, msg.angle_increment,
                                      msg.range_min, msg.range_max, cfg,
                                      title=f"[{robot}] 스캔 #{latest['n']}",
                                      xlim=view["xlim"], ylim=view["ylim"])
        # 도착 순간에만 한 번 알린다 — 매 프레임(10Hz) 찍으면 로그가 안 읽힌다.
        if arrived and not was_arrived["v"]:
            print(f"[{robot}] 도착 — 정지 트리거 조건 만족 (스캔 #{latest['n']})")
        was_arrived["v"] = arrived

        # 드라이버(`lidar_dock_driver.py`)와 같은 규칙(2026-08-04 정정):
        # SEARCH 는 `detect()`(노치까지 확정)를 안 부른다 — 각도가 심하게
        # 틀어지면 벽은 잡혀도 노치 자체가 인식이 안 돼 계속 None 이 나와
        # 방향 신호를 영영 못 받는다. 벽만 넓힌 cfg 로 피팅해 그 yaw 로 돈다.
        # FINAL 인데 FAR 가 실패하면 기존대로 NEAR 로 인수인계한다.
        machine = state["machine"]
        if machine.phase == "SEARCH":
            pts = sector_points(msg.ranges, msg.angle_min, msg.angle_increment,
                                msg.range_min, search_cfg.range_max_m, search_cfg)
            wall = fit_wall(pts, search_cfg) if len(pts) else None
            cmd = machine.step(None, time.monotonic(),
                              search_wall_yaw=(wall.yaw if wall else None))
        else:
            mobs = detect(msg.ranges, msg.angle_min, msg.angle_increment,
                          msg.range_min, msg.range_max, cfg)
            if mobs is None and machine.phase == "FINAL":
                mobs = detect_near(msg.ranges, msg.angle_min, msg.angle_increment,
                                  msg.range_min, msg.range_max, cfg)
            cmd = machine.step(mobs, time.monotonic())

        cmd_text = (f"국면 {cmd.phase}\n"
                   f"linear  {cmd.linear:+.3f} m/s\n"
                   f"angular {cmd.angular:+.3f} rad/s\n"
                   f"사유 {cmd.reason}\n"
                   f"('r' 로 재시작)")
        # ⚠️ `family="monospace"` 를 쓰면 안 된다 — 그 폰트엔 한글 글리프가 없어서
        #    "국면"·"사유" 가 네모로 깨진다(실기로 확인). 전역으로 맞춘 한글
        #    폰트(위 rcParams)를 그대로 상속하게 둔다.
        ax.text(0.98, 0.98, cmd_text, transform=ax.transAxes, ha="right", va="top",
               fontsize=8,
               bbox=dict(facecolor="white", edgecolor="#888", alpha=0.85, pad=4))

        if recording["dir"] is not None:
            frame_path = recording["dir"] / f"frame_{recording['n']:05d}.png"
            fig.savefig(frame_path, dpi=100)
            recording["n"] += 1

    anim = FuncAnimation(fig, update, interval=100, cache_frame_data=False)
    try:
        plt.show()
    finally:
        # ⚠️ 순서가 중요하다 — 타이머를 먼저 멈춰야 `rclpy.shutdown()` 뒤에
        #    큐에 남은 `update()` 호출이 죽은 컨텍스트로 `spin_once` 를
        #    부르지 않는다(바로 위 SIGINT 핸들러 주석의 그 재현 경로).
        stopped["v"] = True
        if anim.event_source is not None:
            anim.event_source.stop()
        if recording["dir"] is not None:  # 녹화 중에 창을 그냥 닫은 경우도 mp4 로 마무리
            _stop_recording(recording["dir"], REC_FPS)
        if rclpy.ok():
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

    live_view(args.robot, domain_id, args.cyclonedds_uri, load_cfg())


if __name__ == "__main__":
    main()
