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
    ang_search=0.16,
    move_pulse_s=0.4,
    timeout_s=157.0,
)

#: 취소 신호. **구독 콜백이 세우고 루프가 읽는다** (머리말 참고).
_cancel = threading.Event()
_lock = threading.Lock()
_running = False


def request_cancel() -> bool:
    """진행 중인 도킹을 끊는다. 돌고 있지 않으면 아무 일도 없다.

    `fleet_link` 의 **구독 콜백**에서 부른다 — 워커에서 부르면 이미 늦다(그 워커가
    바로 이 루프를 돌고 있다).
    """
    with _lock:
        if not _running:
            return False
    _cancel.set()
    return True


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

    with _lock:
        if _running:
            return False, 409, {"docked": False}, "도킹이 이미 진행 중이다"
        # ⚠️ **락 안에서 지운다.** 밖에서 지우면 `_running=True` 와 `clear()` 사이에
        #    들어온 취소가 통째로 사라진다 — BT 가 포기했는데 여기는 계속 미는 창이다.
        #    `request_cancel()` 도 같은 락으로 `_running` 을 보므로, 그 뒤에 온 취소는
        #    반드시 이 clear 뒤에 세워진다.
        _cancel.clear()
        _running = True
    try:
        return _run(overrides)
    except Exception as exc:                                   # noqa: BLE001
        # ⚠️ `_run` 이 예외로 빠지면 `finish()` 를 못 거친다 — 마지막 비영 명령이 남고
        #    노드가 샌다. 여기서 갚는다. 0.08m/s 면 twist_mux timeout(0.5s)까지
        #    4cm 를 더 가는데, 그건 이동량(3cm)보다 크다.
        _emergency_stop()
        return False, 500, {"docked": False}, f"도킹 예외: {exc}"
    finally:
        with _lock:
            _running = False


#: `_run` 이 만든 (node, publisher). 예외로 빠졌을 때 정리하려고 들고 있는다.
_live = None


def _emergency_stop() -> None:
    """예외 경로에서 0 을 내고 노드를 정리한다. 여기서 또 예외를 내지 않는다."""
    global _live
    pair, _live = _live, None
    if pair is None:
        return
    node, pub = pair
    try:
        from geometry_msgs.msg import Twist
        for _ in range(5):
            pub.publish(Twist())
            time.sleep(0.02)
    except Exception:                                          # noqa: BLE001
        pass
    try:
        node.destroy_node()
    except Exception:                                          # noqa: BLE001
        pass


def _run(overrides: dict) -> tuple[bool, int, dict, str]:
    import rclpy
    from geometry_msgs.msg import Twist

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
    pub = node.create_publisher(Twist, "cmd_vel_dock", 10)
    global _live
    _live = (node, pub)          # 예외로 빠져도 정리되게 (위 `_emergency_stop`)

    def publish(lin: float, ang: float) -> None:
        t = Twist()
        t.linear.x = float(lin)
        t.angular.z = float(ang)
        pub.publish(t)

    def spin() -> None:
        # 원본 drive.py:190-192 그대로: 콜백을 한 번만 돌리면 20Hz 센서가 12Hz 루프보다
        # 빨라 큐가 밀린다. 밀리면 age() 판정까지 왜곡된다.
        for _ in range(4):
            rclpy.spin_once(node, timeout_sec=0.0)

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
        global _live
        _live = None             # 정상 종료 — `_emergency_stop` 이 두 번 하지 않게
        for _ in range(5):
            publish(0.0, 0.0)
            time.sleep(0.02)
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
    phase, reason = "SEARCH", ""
    while True:
        spin()
        if _cancel.is_set():
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
        if seq == last_seq:
            # **같은 프레임이다.** 다시 검출하면 `aligned_frames_needed=3`(연속 정렬 3프레임)이
            # 같은 한 장을 세 번 세서 무시각 전진에 들어간다. 그렇다고 obs=None 으로
            # 부르면 정지 화상을 '마커 상실'로 세어 lost_grace 를 태운다.
            # → step 을 아예 건너뛰되, **0 은 계속 낸다**(직전 비영 명령이 twist_mux
            #   timeout 까지 유지되면 안 된다).
            if now - last_new_seq_at > FRAME_STALE_SEC:
                return finish(False, 503, phase, "frame_stale",
                              f"뒷캠 프레임이 {now - last_new_seq_at:.2f}초째 안 바뀐다 "
                              f"(camera_select 가 back 인지 확인 — 대기 캠은 1.9Hz 다)")
            publish(0.0, 0.0)
            time.sleep(period)
            continue
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
        publish(cmd.linear * DRIVE_SIGN, cmd.angular)
        if cmd.done:
            ok = cmd.phase == "DONE"
            return finish(ok, 200 if ok else 502, cmd.phase, cmd.reason,
                          "" if ok else f"도킹 중단: {cmd.reason}")
        time.sleep(period)
