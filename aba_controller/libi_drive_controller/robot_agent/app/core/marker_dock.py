"""뒷캠 ArUco 정밀 도킹 — `/fleet_cmd{aruco_dock}` 한 번으로 끝까지 돌고 결과를 돌려준다.

판단 로직은 `app/marker/` 에 그대로 있다(`arte_aurcomaker_move` 커밋 5002e60 에서 이식,
**한 줄도 안 고쳤다**). 이 파일은 원본의 CLI(`marker/drive.py`)를 대체하는 **실행 껍데기**다:
프레임을 어디서 받고, 명령을 어디로 내고, 언제 멈추는가만 다르다.

    원본 drive.py                        여기
    ─────────────────────────────────    ────────────────────────────────────────
    open_camera() 로 장치 직접 열기      /dev/shm 프레임 탭에서 읽기
    /cmd_vel 직행                        cmd_vel_dock (twist_mux priority 120)
    while rclpy.ok() + return 0/1        한 번 돌고 (ok, status, data, msg)
    자기 노드 + spin_once                전용 노드 + spin_once (구조 보존)
    cv2.setNumThreads(1)                 **안 한다** (아래 참고)

## 왜 BT 가 아니라 여기인가

BT(`libi_modes`)의 `ArucoApproach` 는 이미 `/fleet_cmd{aruco_dock}` 를 내고 결과를 기다리는
위임 leaf 다. 이 절차는 12Hz 로 수십 초 도는 블로킹 루프인데, BT tick 은 5Hz 이고 leaf 는
블로킹하면 안 된다. 반면 fleet_link 워커는 **명령 하나를 끝까지 수행하는 구조**다
(`dock_runner.py` 선례). 그래서 여기가 맞는 자리다.

## ⚠️ `cv2.setNumThreads(1)` 을 옮기지 않았다

원본 `drive.py:158` 주석이 스스로 "프로세스 전역이라 detect.py 가 아니라 여기 둔다 —
detect.py 에 두면 그 모듈을 import 하는 다른 프로세스까지 조용히 묶인다"고 적었다.
robot_agent 는 `park_dock`·`aruco_dock` 라우터로 **다른 cv2 작업을 돈다.** 여기서 전역
설정을 걸면 그쪽까지 1스레드가 된다. 도킹 하나 때문에 프로세스 전체를 묶지 않는다.

## ⚠️ 취소 — 큐를 우회해야 한다

`fleet_link` 워커는 이 명령을 **블로킹으로** 수행한다. 그래서 BT 가 뒤이어 낸 `stop` 이
큐에 들어가도 **워커가 그걸 꺼낼 수 없다** — 이 루프가 끝나야 꺼낸다. 즉 BT 가 포기한
뒤에도 여기가 계속 바퀴를 돌릴 수 있다.

그래서 `fleet_link` 의 **구독 콜백**(워커가 아니라)이 `request_cancel()` 을 직접 부르고,
이 루프는 매 tick 그 이벤트를 본다. `mission_stop` 이 이미 같은 이유로 인라인 실행되는
선례가 있다(`fleet_link.py` `on_cmd`).
"""

from __future__ import annotations

import os
import threading
import time

#: 프레임 탭 경로. **쓰는 쪽은 `aba_ai_service/follower_perception/scripts/frame_tap.py`** 다.
#
# 그 모듈을 import 하지 않고 파일 포맷만 읽는 이유: robot_agent 는 단독 실행이라
# (`scripts/run_fleet_link-tunning.py` 가 sys.path 에 robot_agent 루트만 넣는다)
# `from scripts import frame_tap` 이 `ModuleNotFoundError` 로 죽는다. 두 서비스는 별도
# 배포 단위이므로 경로를 억지로 잇지 않고 **문서화된 파일 포맷의 소비자**가 된다.
#
# ⚠️ 그쪽 계약이 바뀌면 여기도 같이 바꿔야 한다. 계약(그 파일 머리말):
#     · 슬롯은 front/back 두 개. `camera_select` 와 무관하게 **둘 다 항상 기록**한다
#     · JPEG 이 아니라 **생 BGR**
#     · 임시파일 → os.replace 라 원자적. 읽는 쪽에 락이 필요 없다
#     · **stale 판정은 소비자 몫이다** — 여기서 한다(FRAME_STALE_SEC)
_TAP_DIR_ENV = "LIBI_CAM_TAP_DIR"
_TAP_DEFAULT_DIR = "/dev/shm"

#: 프레임이 이보다 오래됐으면 못 믿는다.
#
# ⚠️ `camera_sender` 는 **선택되지 않은 캠을 8틱에 한 번만** 잡는다
#    (`STANDBY_EVERY=8`, 15fps → 0.53초 = 1.9Hz). 그 상태로 12Hz 시각 서보를 돌리면
#    HOMING(0.10m/s)에서 프레임 사이 5.3cm 를 간다 — 못 쓴다.
#    그래서 도킹 동안에는 BT 가 `guide_watch{camera:back}` 로 **뒷캠을 선택 상태로**
#    만든다(`libi_modes` 의 `BackCamOn` leaf). 그러면 매 프레임 15fps 다.
#    이 임계는 그 선택이 실제로 걸렸는지를 드러내는 감시이기도 하다 — 1.9Hz 로 떨어지면
#    여기서 실패한다. 조용히 느려지는 것보다 낫다.
FRAME_STALE_SEC = 0.4

#: 새 프레임이 없는 tick 에 **직전 명령을 다시 낼** 최대 시간(초).
#
#  카메라 15fps / 이 루프 12Hz 라 주기가 안 맞는다 — 매 tick 중 20% 가량이 직전과 같은
#  프레임을 본다. 예전에는 그때마다 0 을 냈고, 그게 "조금 조금 움직인다"의 절반이었다.
#  67ms 전 기하는 여전히 유효하므로 유지하는 편이 맞다.
#
#  ⚠️ 이 값은 `FRAME_STALE_SEC` 보다 **작아야 한다.** 같거나 크면 카메라가 죽은 구간에서도
#     비영 명령이 stale 판정 시각까지 유지된다. 지금 값이면 최악이 0.05m/s × 0.2s = 1cm 다.
HOLD_LAST_CMD_SEC = 0.2

#: 진입 전 정지 게이트. nav2 취소는 요청일 뿐이고 감속은 컨트롤러 몫이라 잔여 속도가 남는다.
#: 그 상태로 SEARCH 스윕이 시작되면 둘이 싸운다.
SETTLE_DELTA_M = 0.005      # 이만큼도 안 움직이면 멈춘 것으로 본다
SETTLE_HOLD_S = 0.5         # 그 상태가 이만큼 연속돼야 진입
SETTLE_MAX_S = 3.0          # 그래도 안 열리면 포기하고 진입 (아래 주석)

#: 이 프레임이 아니면 `ScanWatch` 의 각도 가정이 틀린 것이다.
EXPECTED_SCAN_FRAME = "rplidar_link"

#: 후진 진행 방향이 생 `/scan` 좌표에서 몇 도인가.
#
# 원본 `drive.py:168` 은 `scan_forward_deg + 180`(뒷캠=후진이니 감시도 뒤를 봐야)이고,
# 그건 **라이다 0 rad = 물리적 앞**을 전제한다. 이 로봇은 아니다:
#
#   URDF   pinky.urdf.xacro:201  `<origin xyz="0 0 0.030" rpy="0 0 ${pi}"/>`
#   실측   2026-07-30, 모터 무발행. 로봇 뒤 20cm 에 손을 대고 /scan 을 봤더니
#          30cm 이내 반사가 **0도 부근(-30~+45)** 에서 0.15~0.24m 로 잡혔다.
#
# 즉 `rplidar_link` 0 rad = **물리적 뒤**. 후진 진행 방향이 곧 0도다.
# 원본의 +180 을 그대로 썼으면 **근접 감시가 앞을 보면서 뒤로 박았다.**
#
# ⚠️ nav2·AMCL 은 영향 없다 — TF 로 변환해 쓴다. 생 `/scan` 각도를 직접 읽는
#    `ScanWatch` 만 이 보정이 필요하다.
SCAN_FORWARD_DEG_BACKWARD = 0.0

#: 뒷캠은 로봇 뒤를 본다 — 마커로 가려면 후진이다.
#: 뒤집히는 것은 **직진 축뿐**이다(원본 `_drive_sign` 주석: 뒷캠은 앞캠을 수직축으로
#: 180° 돌린 것이고, 수직축 회전은 z 회전량을 보존한다).
DRIVE_SIGN = -1.0

#: 이 로봇·이 마커 전용 현장값. 다른 마커를 쓰면 `marker_len_m` 부터 다시 잰다
#: (공칭 7cm 인쇄물의 검은 사각형 실측이 6.3cm 다).
FIELD_DEFAULTS = dict(
    marker_id=0,
    marker_len_m=0.063,
    dict_name="DICT_5X5_100",
    stop_m=0.06,
    front_offset_m=0.067,
    steer_sign=-1.0,
    yaw_offset_deg=4.0,
    axis_gate_m=0.6,
    lin_pulse=0.05,          # 실측 모터 데드밴드. 그 아래로는 안 돈다
    lin_homing=0.10,
    steer_ang_max=0.08,
    # ── [2026-07-31] AXIS_ALIGN 을 **연속 주행**으로 바꿨다 (사용자: "smooth 하게") ──
    #
    # 원본 기본값은 `move_pulse_s=0.10 / move_pause_s=0.90` — **듀티 10%** 다.
    # 0.1초 가고 0.9초 서기를 반복하니 "조금 조금 움직인다"로 보였다. 여기서 0.4 로
    # 늘려 놨어도 듀티는 31% 라 여전히 끊긴다.
    #
    # 멈춤을 없애도 되는 이유: 조향(`ang`)은 원래부터 쉬는 구간에도 매 tick 나갔다.
    # 즉 펄스는 "돌면서 가끔 전진"이었고, 멈춤을 빼면 **돌면서 계속 전진**이 된다 —
    # 시각 서보의 보통 모습이다. 속도는 데드밴드값 0.05 m/s 그대로라 여전히 느리다
    # (평균 15mm/s → 50mm/s).
    #
    # ⚠️ 되돌리려면 `move_pause_s` 만 0.9 로 올리면 된다. 정렬이 근거리에서 흔들리면
    #    그게 첫 번째 의심 지점이다 — 멈춰 보는 리듬이 자세 추정을 안정시켰을 수 있다.
    move_pulse_s=0.4,
    move_pause_s=0.0,
    timeout_s=157.0,
    # ── 마커 탐색(SEARCH) — "좌우로 천천히 도리도리" ─────────────────────────
    #
    # ②가 뒷캠을 충전소 쪽으로 돌려 놓지만, 정점 오차·AMCL 오차로 마커가 화각을
    # 벗어날 수 있다. 그때 이 스윕이 찾는다: **좌 45° → 중앙 → 우 45°** 를
    # 15° 씩 끊어 가며, 매 걸음 멈춰서 본다.
    #
    # ⚠️ **회전 속도는 못 낮춘다.** 0.16 rad/s 가 실측 데드밴드다(현장 튜닝의
    #    `ANG_SEARCH` — "이 아래로는 안 돈다"). 더 느리게 적으면 명령은 나가는데
    #    바퀴가 안 돈다. 그래서 "천천히"를 **속도가 아니라 걸음으로** 만든다:
    #      · 걸음을 20° → 15° 로 잘게 (한 걸음 2.6초 → 1.6초)
    #      · 걸음마다 멈춰 보는 시간을 0.4 → 0.7초로
    #    회전 중에는 모션블러로 자세가 안 풀리므로, 실제 검출은 **멈춘 프레임**에서
    #    일어난다. 멈추는 횟수를 늘리는 게 곧 "천천히 훑는" 것이다.
    ang_search=0.16,
    search_span_deg=45.0,       # 중앙 기준 좌우 각각 (좌 45 · 중앙 · 우 45 = 7걸음)
    search_step_deg=15.0,
    turn_pause_s=0.7,
)

#: 취소 신호. **구독 콜백이 세우고 루프가 읽는다** (머리말 참고).
#
# ## ⚠️ Event 가 아니라 세대 번호다 (codex 리뷰 2026-07-30)
#
# 처음엔 `threading.Event` 였고, `request_cancel()` 이 락 안에서 `_running` 만 보고
# 락 **밖에서** `set()` 했다. 그 사이가 벌어지면 취소가 **다음 도킹**을 죽인다:
#
#     이전 도킹의 취소 콜백이 `_running=True` 를 확인
#       → 콜백 스레드가 밀린다
#       → 이전 도킹이 끝난다
#       → 새 도킹이 시작한다 (Event 를 clear 하고)
#       → 밀렸던 콜백이 이제서야 set() → **새 도킹이 첫 tick 에 499 로 죽는다**
#
# `set()` 을 락 안으로 옮겨도 안 된다. 락을 늦게 잡으면 그때 `_running` 은 이미
# **새 도킹**의 True 라서, 여전히 엉뚱한 것을 죽인다. 무엇을 취소하려던 것인지를
# 기억해야 한다 — 그래서 세대 번호다.
_lock = threading.Lock()
_running = False
_gen = 0                #: 도킹을 시작할 때마다 오른다
_cancel_gen = -1        #: 취소를 요청받은 세대


def request_cancel() -> bool:
    """진행 중인 도킹을 끊는다. 돌고 있지 않으면 아무 일도 없다.

    `fleet_link` 의 **구독 콜백**에서 부른다 — 워커에서 부르면 이미 늦다(그 워커가
    바로 이 루프를 돌고 있다).
    """
    global _cancel_gen
    with _lock:
        if not _running:
            return False
        _cancel_gen = _gen          # **지금 도는 그 세대**를 지목한다
        return True


def _cancelled(my_gen: int) -> bool:
    with _lock:
        return _cancel_gen == my_gen


def is_running() -> bool:
    with _lock:
        return _running


# ── 프레임 탭 ────────────────────────────────────────────────────────────────

def _tap_path(slot: str) -> str:
    return os.path.join(os.environ.get(_TAP_DIR_ENV, _TAP_DEFAULT_DIR),
                        f"libi_cam_{slot}.npz")


def read_tap(slot: str = "back"):
    """`(frame, seq, stamp)` 또는 None. 아직 없거나 교체 중이면 None."""
    import numpy as np

    p = _tap_path(slot)
    if not os.path.exists(p):
        return None
    try:
        with np.load(p) as z:
            return z["frame"], int(z["seq"]), float(z["stamp"])
    except (OSError, ValueError, KeyError, EOFError):
        # 원자적 교체 직전을 잡았을 수 있다. 다음 tick 에 다시 읽으면 된다 —
        # 여기서 예외를 올리면 제어 루프가 죽는다.
        return None


def _check_resolution(frame, expect_wh) -> str:
    """캘리브 해상도와 다르면 사유를 돌려준다(같으면 빈 문자열).

    ⚠️ 이걸 빼면 **조용히** 틀린다. K 는 320x240 전용이라 640x480 을 주면 fx·cx 가
       두 배 틀려 거리가 절반으로 읽힌다 — 예외도, 로그도 없다. 그리고 이 USB 캠은
       요청 해상도를 실제로 바꿔치기한 전력이 있다(2026-07-30 실측: 480x360 요청 →
       640x360 반환. `libi_pi.sh` 주석에도 기록돼 있다).
    """
    h, w = frame.shape[:2]
    if (w, h) == tuple(expect_wh):
        return ""
    return (f"프레임 {w}x{h} 인데 캘리브는 {expect_wh[0]}x{expect_wh[1]} 다 — "
            f"거리가 {expect_wh[0] / w:.2f}배 틀린다. --back-width 확인")


# ── 본체 ─────────────────────────────────────────────────────────────────────

def run_dock(**overrides) -> tuple[bool, int, dict, str]:
    """도킹을 끝까지 돌린다. `(ok, http_status, data, msg)` — fleet_link `_dispatch` 계약."""
    global _running

    global _gen
    with _lock:
        if _running:
            return False, 409, {"docked": False}, "도킹이 이미 진행 중이다"
        _gen += 1
        my_gen = _gen               # 이 판을 가리키는 번호. 남의 취소에 안 죽는다
        _running = True
    try:
        return _run(overrides, my_gen)
    except Exception as exc:                                   # noqa: BLE001
        # ⚠️ `_run` 이 예외로 빠지면 `finish()` 를 못 거친다 — 마지막 비영 명령이 남고
        #    노드가 샌다. 여기서 갚는다. 0.08m/s 면 twist_mux timeout(0.5s)까지
        #    4cm 를 더 가는데, 그건 이동량(3cm)보다 크다.
        _emergency_stop()
        return False, 500, {"docked": False}, f"도킹 예외: {exc}"
    finally:
        with _lock:
            _running = False


#: `_run` 이 만든 (node, publisher, executor). 예외로 빠졌을 때 정리하려고 들고 있는다.
_live = None


def _emergency_stop() -> None:
    """예외 경로에서 0 을 내고 노드를 정리한다. 여기서 또 예외를 내지 않는다."""
    global _live
    pair, _live = _live, None
    if pair is None:
        return
    node, pub, executor = pair
    try:
        from geometry_msgs.msg import Twist
        for _ in range(5):
            pub.publish(Twist())
            time.sleep(0.02)
    except Exception:                                          # noqa: BLE001
        pass
    for step in (lambda: executor.remove_node(node), node.destroy_node):
        try:
            step()
        except Exception:                                      # noqa: BLE001
            pass


def _run(overrides: dict, my_gen: int) -> tuple[bool, int, dict, str]:
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.executors import SingleThreadedExecutor

    from app.core import fleet_link
    from app.marker.approach import MarkerApproach
    from app.marker.calib import load_calib
    from app.marker.config import MarkerDriveConfig
    from app.marker.detect import detect_marker
    from app.marker.odom import OdomTracker
    from app.marker.scan import ScanWatch

    cfg_kw = dict(FIELD_DEFAULTS)
    cfg_kw.update({k: v for k, v in overrides.items() if v is not None})
    cfg = MarkerDriveConfig(**cfg_kw).clamped()

    K, dist, wh = load_calib("back", rotate=0)

    # 전용 노드를 만들고 **이 루프가 직접 spin_once 한다.**
    #
    # ros_bridge 의 BridgeNode 에 구독을 붙이지 않는 이유: 그 노드는 다른 스레드가
    # `rclpy.spin(node)` 로 이미 돌리고 있고(ros_bridge.py), 스핀 중인 노드에 다른
    # 스레드가 구독을 붙이는 것이 안전하다는 보장이 없다. 그리고 그 노드의 /odom·/scan
    # 구독은 `FLEET_LINK_LITE=1` 이 **일부러 끈 것**이다("Pi 에서 초당 수십 건
    # 역직렬화로 CPU 를 크게 잡아먹는다" — ros_bridge.py:294). 되살리면 평소 CPU 가 는다.
    #
    # fleet_link 의 context 를 쓴다 — 도메인이 같고, 노드 하나를 두 executor 가 돌리는
    # 경우가 아니라서(우리 노드는 우리만 spin_once) 안전하다. 새 context 를 init 하면
    # shutdown·오류복구·도메인 일치 책임이 하나 더 생긴다.
    ctx = fleet_link.get_context()
    if ctx is None:
        return False, 503, {"docked": False}, "fleet_link ROS context 가 아직 없다"
    node = rclpy.create_node("marker_dock", context=ctx)
    # ⚠️ **전용 executor 를 만든다.** `rclpy.spin_once(node)` 를 쓰면 안 된다 —
    #    그건 인자가 없을 때 **전역 executor**(`get_global_executor()`)를 집는데,
    #    fleet_link 가 같은 context 에서 이미 그걸 돌리고 있다. 그러면 첫 tick 에
    #    `RuntimeError: Executor is already spinning` 으로 도킹이 통째로 죽는다.
    #    실측(2026-07-30 실기): `aruco_dock 실패: 도킹 예외: Executor is already spinning`
    #    이 세 번 반복되고 RETURNING → ERROR 로 갔다.
    #
    #    노드 하나를 두 executor 가 돌리는 게 아니라(이 노드는 우리만) 안전하다.
    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    pub = node.create_publisher(Twist, "cmd_vel_dock", 10)
    global _live
    _live = (node, pub, executor)   # 예외로 빠져도 정리되게 (위 `_emergency_stop`)

    def publish(lin: float, ang: float) -> None:
        t = Twist()
        t.linear.x = float(lin)
        t.angular.z = float(ang)
        pub.publish(t)

    def spin() -> None:
        # 원본 drive.py:190-192 그대로: 콜백을 한 번만 돌리면 20Hz 센서가 12Hz 루프보다
        # 빨라 큐가 밀린다. 밀리면 age() 판정까지 왜곡된다.
        for _ in range(4):
            executor.spin_once(timeout_sec=0.0)

    odom = OdomTracker(node)
    watch = ScanWatch(node, forward_deg=SCAN_FORWARD_DEG_BACKWARD)
    machine = MarkerApproach(cfg)
    period = 1.0 / cfg.loop_hz
    log = node.get_logger()
    log.info(f"[도킹] id={cfg.marker_id} {cfg.dict_name} marker={cfg.marker_len_m}m "
             f"stop={cfg.stop_m}(+{cfg.front_offset_m}) gate={cfg.axis_gate_m} "
             f"sign={cfg.steer_sign:+.0f} 후진 scan_fwd={SCAN_FORWARD_DEG_BACKWARD:.0f}deg")

    def finish(ok: bool, status: int, phase: str, reason: str, msg: str):
        # 끝나는 모든 길에서 0 을 여러 번 낸다. 마지막 명령이 남으면 twist_mux
        # timeout(0.5s)·모터 워치독(0.5s)까지 계속 밀린다.
        # ⚠️ `_live` 를 **0 을 다 낸 뒤에** 비운다. 먼저 비우면 첫 publish 가 예외를
        #    낼 때(context/DDS 이상) `_emergency_stop` 이 다시 시도할 대상이 없어져,
        #    마지막 비영 명령이 mux timeout 까지 남는다 — 0.08m/s 면 4cm 다.
        global _live
        for _ in range(5):
            publish(0.0, 0.0)
            time.sleep(0.02)
        _live = None
        executor.remove_node(node)
        node.destroy_node()
        return ok, status, {"docked": ok, "phase": phase, "reason": reason}, msg

    # ── 센서 준비 ────────────────────────────────────────────────────────────
    deadline = time.monotonic() + 5.0
    while not (odom.ready and watch.ready) and time.monotonic() < deadline:
        spin()
        time.sleep(0.02)
    if not (odom.ready and watch.ready):
        missing = [n for n, ok in (("/odom", odom.ready), ("/scan", watch.ready)) if not ok]
        return finish(False, 503, "INIT", "sensor_wait",
                      f"센서가 안 들어온다: {', '.join(missing)}")
    if watch.frame_id and watch.frame_id != EXPECTED_SCAN_FRAME:
        # 각도 보정이 이 프레임 기준으로 맞춰져 있다. 다르면 감시가 엉뚱한 쪽을 본다.
        return finish(False, 500, "INIT", "scan_frame",
                      f"/scan frame_id 가 {watch.frame_id} 다 "
                      f"({EXPECTED_SCAN_FRAME} 기준으로 각도를 맞췄다)")

    # ── 진입 전 정지 게이트 ──────────────────────────────────────────────────
    #
    # ⚠️ 타임아웃이면 **실패로 빼지 않고 진입**한다. 오래 서 있는 로봇의 odom 노이즈로
    #    게이트가 안 열릴 수 있고, 그때 도킹을 포기하는 편이 더 나쁘다. 대신 그 동안
    #    0 을 계속 내서 우리가 미는 일은 없게 한다.
    settle_t0 = time.monotonic()
    still_since = None
    ref = odom.forward_m
    while time.monotonic() - settle_t0 < SETTLE_MAX_S:
        spin()
        publish(0.0, 0.0)
        now = time.monotonic()
        if abs(odom.forward_m - ref) < SETTLE_DELTA_M:
            if still_since is None:
                still_since = now
            elif now - still_since >= SETTLE_HOLD_S:
                break
        else:
            still_since = None
            ref = odom.forward_m
        time.sleep(0.05)
    else:
        log.warning(f"[도킹] 정지 게이트 {SETTLE_MAX_S:.0f}초 미달 — 그대로 진입한다")

    # ── 제어 루프 ────────────────────────────────────────────────────────────
    last_seq = None
    last_new_seq_at = time.monotonic()
    #: 중복 프레임 tick 에 다시 낼 직전 명령 (아래 `seq == last_seq` 분기 참고).
    last_cmd = (0.0, 0.0)
    phase, reason = "SEARCH", ""
    while True:
        spin()
        if _cancelled(my_gen):
            return finish(False, 499, phase, "cancelled", "취소됨")

        stale = [n for n, age in (("/odom", odom.age()), ("/scan", watch.age()))
                 if age > cfg.sensor_timeout_s]
        if stale:
            return finish(False, 503, phase, "sensor_timeout",
                          f"센서가 {cfg.sensor_timeout_s:.2f}초 넘게 끊겼다: {', '.join(stale)}")
        if not watch.valid:
            # 스캔은 제때 오는데 쓸 만한 값이 하나도 없다 = 라이다 고장.
            # front_m 은 그때도 None 이라 '전방이 트여 있음'과 구분이 안 된다.
            return finish(False, 503, phase, "scan_invalid",
                          "/scan 에 유효한 거리값이 하나도 없다")

        got = read_tap("back")
        now = time.monotonic()
        if got is None:
            if now - last_new_seq_at > FRAME_STALE_SEC:
                return finish(False, 503, phase, "frame_missing", "뒷캠 프레임 탭이 비어 있다")
            publish(0.0, 0.0)
            time.sleep(period)
            continue
        frame, seq, stamp = got
        # ⚠️ **`seq` 만 보면 안 된다.** stamp 는 camera_sender 가 찍은 캡처 시각이다
        #    (`time.monotonic` — 리눅스에서 CLOCK_MONOTONIC 은 시스템 전역이라 프로세스가
        #    달라도 비교된다). seq 만 보면 다음이 통과한다:
        #      · `/dev/shm` 에 남은 **이전 실행**의 프레임을 첫 tick 에 그대로 쓴다
        #      · 파이프라인이 밀려 과거 프레임을 순서대로 쓰는 동안 seq 만 올라간다
        #    둘 다 **과거의 마커 기하를 보고 조향·후진**한다 — 조용히 틀어지는 종류다.
        if now - stamp > FRAME_STALE_SEC:
            return finish(False, 503, phase, "frame_stale",
                          f"뒷캠 프레임이 {now - stamp:.2f}초 전 것이다 "
                          f"(camera_select 가 back 인지 확인 — 대기 캠은 1.9Hz 다)")
        fresh = seq != last_seq
        if not fresh:
            # **같은 프레임이다.** 다시 검출하면 `aligned_frames_needed=3`(연속 정렬 3프레임)이
            # 같은 한 장을 세 번 세서 무시각 전진에 들어간다. 그렇다고 시각 국면에서
            # obs=None 으로 부르면 정지 화상을 '마커 상실'로 세어 lost_grace 를 태운다.
            if now - last_new_seq_at > FRAME_STALE_SEC:
                return finish(False, 503, phase, "frame_stale",
                              f"뒷캠 프레임이 {now - last_new_seq_at:.2f}초째 안 바뀐다 "
                              f"(camera_select 가 back 인지 확인 — 대기 캠은 1.9Hz 다)")
            # ⚠️ [2026-07-31] **BLIND_PUSH 는 예외다 — 여기서 건너뛰면 안 된다.**
            #    그 국면은 애초에 시각을 안 쓴다. 마커는 이미 화각을 벗어났고 거리 판정은
            #    odom(`_do_blind_push`)이 한다. 그런데 프레임이 같다는 이유로 step 을
            #    건너뛰면 **목표 도달·scan_guard·timeout 을 그동안 아무도 안 본다** —
            #    직전 명령을 최대 0.2초 유지하면 0.05m/s × 0.2s = **1cm** 를 눈감고 더 간다.
            #    6cm 목표의 16.7% 다(codex 지적, 2026-07-31). 그래서 obs=None 으로 그대로
            #    태운다: approach.py 는 그 국면의 None 을 상실로 세지 않고 곧장 odom
            #    판정으로 보낸다(`approach.py` step() 의 `if self.phase == "BLIND_PUSH"`).
            if machine.phase != "BLIND_PUSH":
                # 시각 국면 — 새 프레임이 없다고 직전 판단이 무효가 된 것은 아니다.
                # 카메라 15fps / 루프 12Hz 라 주기가 어긋나 같은 프레임을 보는 tick 이
                # 생기는데, 예전에는 그때마다 0 을 쏘아 주행이 톱니처럼 끊겼다.
                if now - last_new_seq_at <= HOLD_LAST_CMD_SEC:
                    publish(*last_cmd)
                else:
                    publish(0.0, 0.0)
                time.sleep(period)
                continue

        obs = None
        if fresh:
            last_seq, last_new_seq_at = seq, now
            bad = _check_resolution(frame, wh)
            if bad:
                return finish(False, 500, phase, "resolution", bad)
            obs = detect_marker(frame, K, dist, marker_len_m=cfg.marker_len_m,
                                target_id=cfg.marker_id, dict_name=cfg.dict_name)
        cmd = machine.step(obs, yaw_deg=odom.yaw_deg,
                           forward_m=odom.forward_m * DRIVE_SIGN,
                           front_m=watch.front_m, now_s=now)
        phase, reason = cmd.phase, cmd.reason
        last_cmd = (cmd.linear * DRIVE_SIGN, cmd.angular)
        publish(*last_cmd)
        if cmd.done:
            ok = cmd.phase == "DONE"
            return finish(ok, 200 if ok else 502, cmd.phase, cmd.reason,
                          "" if ok else f"도킹 중단: {cmd.reason}")
        time.sleep(period)
