"""실물 ROS 그래프 없이 도킹 설명 화면의 핵심 오버레이를 검증한다."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import json

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[4]
VIEWER = ROOT / "scripts/demo/shelf_dock_lidar_viewer/shelf_dock_lidar_viewer.py"
SPEC = importlib.util.spec_from_file_location("shelf_dock_lidar_viewer", VIEWER)
assert SPEC and SPEC.loader
viewer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = viewer
SPEC.loader.exec_module(viewer)


def test_render_draws_pgm_ray_stop_point_and_live_lidar() -> None:
    cells = np.zeros((20, 20), dtype=np.int16)
    cells[:, 14:] = 100
    fake = SimpleNamespace(
        map_data=viewer.MapData(cells=cells, resolution=0.05, origin_x=0.0,
                                origin_y=0.0, received_at=__import__("time").monotonic()),
        pose=(0.20, 0.50, 0.0),
        scan_data=viewer.ScanData(
            ranges=np.array([0.20, 0.35, 0.50], dtype=np.float32),
            angles=np.array([-0.20, 0.0, 0.20], dtype=np.float32),
            range_min=0.02, range_max=5.0, received_at=__import__("time").monotonic()),
        status={"event": "shelf_dock", "phase": "final_progress",
                "pgm_distance_m": 0.50, "clearance_m": 0.02,
                "remaining_to_clearance_m": 0.48, "ray_yaw_rad": 0.0},
        step_index=viewer.step_index_for("final_progress", None),
        step_log={},
    )
    args = SimpleNamespace(robot="pinky-3", range_m=1.20, front_half_angle_deg=15.0)

    image = viewer.render(fake, args)

    assert image.shape == (viewer.CANVAS_H, viewer.CANVAS_W, 3)
    assert np.any(np.all(image == viewer.RAY_CYAN, axis=2))
    assert np.any(np.all(image == viewer.STOP_YELLOW, axis=2))
    assert np.any(np.all(image == viewer.LIDAR_GREEN, axis=2))


def test_age_label_marks_missing_data_as_waiting() -> None:
    assert viewer.age_label(None) == "waiting"


def test_step_index_for_orders_started_before_marker_centering() -> None:
    assert viewer.step_index_for("started", None) == 0
    assert viewer.step_index_for("marker_centering", "초기 중앙정렬") == 1
    assert viewer.step_index_for("marker_centering", "재관측 중앙정렬") == 6
    assert viewer.step_index_for("final_progress", None) == len(viewer.STEPS) - 1


def test_step_index_for_unknown_phase_returns_negative_one() -> None:
    assert viewer.step_index_for("completed", None) == -1
    assert viewer.step_index_for("failed", None) == -1
    assert viewer.step_index_for("not_a_real_phase", None) == -1


def test_failure_reason_strips_korean_prefix_cv2_cannot_render() -> None:
    # cv2 Hershey 폰트가 한글을 못 그리므로 ': ' 뒤 영문 reason 코드만 뽑아야 한다.
    assert viewer.failure_reason("초기 비주얼 서보 실패: marker_not_found") == "marker_not_found"
    assert viewer.failure_reason("옆축 AMCL PID 실패: odom_stale") == "odom_stale"
    assert viewer.failure_reason(None) == ""
    assert viewer.failure_reason("") == ""


def test_on_status_tracks_last_reached_step_through_failure() -> None:
    """failed 리포트는 phase 를 "failed" 로 덮어써서 몇 단계까지 갔는지가
    status 에서 사라진다 — step_index 는 그 전 마지막 진행 단계를 기억해야 한다."""
    import json as _json
    from types import SimpleNamespace as _NS

    node = object.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data, node.front_half_deg = None, 15.0
    node.status = {}
    node.step_index = -1

    def send(phase, **fields):
        payload = {"event": "shelf_dock", "phase": phase, **fields}
        viewer.ShelfDockLidarViewer._on_status(node, _NS(data=_json.dumps(payload)))

    send("started", clearance_m=0.02)
    assert node.step_index == 0
    send("marker_centering", stage="초기 중앙정렬")
    assert node.step_index == 1
    send("initial_marker_centered", frames=10)
    assert node.step_index == 2
    send("lateral_plan_ready", planned_lateral_m=0.1)
    assert node.step_index == 3
    send("failed", status=502, message="옆축 AMCL PID 실패: odom_stale")
    # phase 는 "failed" 로 바뀌었지만 step_index 는 마지막 진행 단계(3)에 멈춰 있다.
    assert node.step_index == 3
    assert node.status["phase"] == "failed"
    assert viewer.failure_reason(node.status["message"]) == "odom_stale"

    # 다음 시도가 시작되면 이전 진행도는 리셋된다.
    send("started", clearance_m=0.02)
    assert node.step_index == 0


def test_render_shows_failure_banner_with_reason_and_red_step_chip() -> None:
    fake = SimpleNamespace(
        map_data=None, pose=None, scan_data=None,
        status={"event": "shelf_dock", "phase": "failed", "status": 502,
                "message": "초기 비주얼 서보 실패: marker_not_found"},
        step_index=1,
        step_log={1: {"phase": "failed", "stage": None,
                      "fields": {"message": "초기 비주얼 서보 실패: marker_not_found"},
                      "summary": "FAILED: marker_not_found"}},
    )
    args = SimpleNamespace(robot="pinky-3", range_m=1.20, front_half_angle_deg=15.0)

    image = viewer.render(fake, args)

    assert np.any(np.all(image == viewer.HIT_RED, axis=2))  # 실패 배너 + 멈춘 칩


def test_format_step_fields_shows_detected_value_and_move_distance() -> None:
    summary = viewer.format_step_fields(
        "lateral_plan_ready",
        {"pgm_distance_m": 0.42, "planned_lateral_m": 0.07, "ray_yaw_rad": 0.1})
    assert "42.0cm" in summary   # 검출된 값(벽까지 거리)
    assert "7.0cm" in summary    # 이동량(옆축으로 얼마나 움직일지)


def test_format_step_fields_unknown_phase_falls_back_to_raw_dump() -> None:
    # 모르는 phase 나 새 필드가 와도 조용히 빠지지 않고 raw key=value 로 나온다.
    summary = viewer.format_step_fields("some_future_phase", {"weird_field": 3.5})
    assert "weird_field=3.5" in summary


def test_on_status_records_per_step_summary_and_raw_korean_message() -> None:
    import json as _json
    from types import SimpleNamespace as _NS

    node = object.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data, node.front_half_deg = None, 15.0
    node.status = {}
    node.step_index = -1
    node.step_log = {}

    def send(phase, **fields):
        payload = {"event": "shelf_dock", "phase": phase, **fields}
        viewer.ShelfDockLidarViewer._on_status(node, _NS(data=_json.dumps(payload)))

    send("started", clearance_m=0.02)
    send("marker_centering", stage="초기 중앙정렬")
    send("initial_marker_centered", frames=12)
    assert node.step_log[2]["summary"] == "marker found, centered in 12 frames"
    send("failed", status=499, message="추가 회전 실패: canceled")
    # 원본(한글 포함) message 는 그대로 fields 에 남아있다 — 로그 덤프용.
    failed_record = node.step_log[node.step_index]
    assert failed_record["phase"] == "failed"
    assert failed_record["fields"]["message"] == "추가 회전 실패: canceled"
    assert failed_record["summary"] == "FAILED: canceled"


def test_format_log_text_includes_raw_korean_message_uncorrupted() -> None:
    node = object.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data, node.front_half_deg = None, 15.0
    node.status = {"phase": "failed"}
    node.step_index = 1
    node.step_log = {
        0: {"phase": "started", "stage": None, "fields": {}, "summary": "clearance=2.0cm"},
        1: {"phase": "failed", "stage": "초기 중앙정렬",
            "fields": {"message": "초기 비주얼 서보 실패: marker_not_found"},
            "summary": "FAILED: marker_not_found"},
    }
    args = SimpleNamespace(robot="pinky-3")

    out = viewer.format_log_text(node, args)

    assert "초기 비주얼 서보 실패: marker_not_found" in out   # 텍스트라 한글이 안 깨진다
    assert "FAIL" in out
    assert "OK" in out


def test_render_surfaces_camera_bind_error_instead_of_silent_lidar_fallback() -> None:
    """실측(2026-08-05): perception_server.py 가 같은 UDP 포트를 이미 물고 있으면
    카메라 bind 가 실패하는데, 화면엔 이유 없이 라이다만 보여서 헷갈렸다 —
    bind 실패 사유가 라이다 패널에 직접 보여야 한다."""
    fake = SimpleNamespace(
        map_data=None, pose=None, scan_data=None,
        status={}, step_index=0, step_log={},
        cam=SimpleNamespace(error="UDP 6021 bind 실패: [Errno 98] Address already in use",
                            latest=lambda: (None, None)),
    )
    args = SimpleNamespace(robot="pinky-3", range_m=1.20, front_half_angle_deg=15.0, cam_port=6021)

    image = viewer.render(fake, args)

    # 작은 폰트라 anti-aliasing 때문에 순수 HIT_RED 픽셀이 없을 수 있다 — 근접색으로 본다.
    r, g, b = viewer.HIT_RED
    near_red = ((np.abs(image[:, :, 0].astype(int) - r) < 40)
                & (np.abs(image[:, :, 1].astype(int) - g) < 40)
                & (np.abs(image[:, :, 2].astype(int) - b) < 40))
    assert near_red.sum() > 20


def test_cam_port_for_matches_common_sh_robot_ports_rule() -> None:
    # scripts/_common.sh 의 robot_ports(): off = (num-1)*10, port = 6001+off.
    assert viewer.cam_port_for("pinky-1") == 6001
    assert viewer.cam_port_for("pinky-2") == 6011
    assert viewer.cam_port_for("pinky-3") == 6021
    assert viewer.cam_port_for("pinky3") == 6021       # 하이픈 없어도 숫자만 보면 됨
    assert viewer.cam_port_for("nodigits") == 6001      # 숫자 없으면 1번 취급


def test_debug_port_offset_matches_libi_pi_sh_dock_debug_rule() -> None:
    # scripts/all/libi_pi.sh 의 --dock-debug: DOCK_DEBUG_PORT=$((VIDEO_PORT + 1000)).
    # 여기 값을 바꾸면 그쪽도 같이 바꿔야 한다 — 이 테스트가 어긋남을 잡는다.
    assert viewer.DEBUG_PORT_OFFSET == 1000
    assert viewer.cam_port_for("pinky-3") + viewer.DEBUG_PORT_OFFSET == 7021


def test_cam_frame_reassembler_drops_stale_and_keeps_newest() -> None:
    reasm = viewer._CamFrameReassembler()
    hdr = viewer._CAM_HDR

    # frame 0: 2청크
    assert reasm.feed(hdr.pack(0, 0, 2) + b"AA") is None
    assert reasm.feed(hdr.pack(0, 1, 2) + b"BB") == b"AABB"

    # frame 0 조각이 뒤늦게 다시 오면(중복/재전송) 버린다 — 이미 latest_done.
    assert reasm.feed(hdr.pack(0, 0, 2) + b"XX") is None

    # frame 1: 1청크, 바로 완성.
    assert reasm.feed(hdr.pack(1, 0, 1) + b"CC") == b"CC"


def test_cam_frame_reassembler_resyncs_on_large_backward_jump() -> None:
    reasm = viewer._CamFrameReassembler()
    hdr = viewer._CAM_HDR
    reasm.feed(hdr.pack(100, 0, 1) + b"AA")
    assert reasm._latest_done == 100
    # frame_id 가 100 에서 5로 크게 뒤로 튐 = sender 재시작 → resync 해야 한다.
    assert reasm.feed(hdr.pack(5, 0, 1) + b"BB") == b"BB"


def test_cam_receiver_round_trip_over_real_udp_socket() -> None:
    """실제 루프백 UDP로 인코드->청크분할->수신->디코드 전체 파이프라인을 검증한다.
    프로토콜이 udp_video.py 원본과 어긋나면 여기서 바로 빨개진다."""
    import socket as _socket
    import time as _time

    receiver = viewer.CamReceiver(port=0, host="127.0.0.1")
    assert receiver.error is None
    port = receiver._sock.getsockname()[1]

    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[:, :] = (10, 20, 30)
    ok, buf = __import__("cv2").imencode(".jpg", frame)
    assert ok
    payload = buf.tobytes()
    chunk_size = 40
    total = (len(payload) + chunk_size - 1) // chunk_size
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        for i in range(total):
            pkt = viewer._CAM_HDR.pack(0, i, total) + payload[i * chunk_size:(i + 1) * chunk_size]
            sock.sendto(pkt, ("127.0.0.1", port))
        deadline = _time.monotonic() + 2.0
        got_frame = None
        while _time.monotonic() < deadline:
            got_frame, _at = receiver.latest()
            if got_frame is not None:
                break
            _time.sleep(0.02)
        assert got_frame is not None
        assert got_frame.shape[:2] == (40, 60)
    finally:
        sock.close()
        receiver.close()


def test_draw_camera_shows_center_dot_and_marker_dot_when_error_present() -> None:
    view = np.full((100, 140, 3), viewer.BG, np.uint8)
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    viewer.draw_camera(view, frame, __import__("time").monotonic(), marker_error_px=12.0)
    assert np.any(np.all(view == viewer.WHITE, axis=2))         # 목표(화면 중앙) 점
    assert np.any(np.all(view == viewer.STOP_YELLOW, axis=2))   # 검출된 마커 위치 점


def test_draw_camera_omits_marker_dot_without_error_field() -> None:
    """marker_error_px 가 없는 phase(APPROACH 이전/이후)에선 노란 점을 안 찍는다 —
    없는 값을 있는 것처럼 그리면 거짓 정보다."""
    view = np.full((100, 140, 3), viewer.BG, np.uint8)
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    viewer.draw_camera(view, frame, __import__("time").monotonic(), marker_error_px=None)
    assert not np.any(np.all(view == viewer.STOP_YELLOW, axis=2))


def test_format_step_fields_marker_centering_shows_error_and_stable_count() -> None:
    summary = viewer.format_step_fields(
        "marker_centering",
        {"marker_error_px": 3.2, "stable_frames": 18, "tol_px": 5.0})
    assert "3.2px" in summary
    assert "18" in summary


def test_draw_camera_places_marker_dot_at_real_vertical_position() -> None:
    """2026-08-05: 예전엔 노란 점을 항상 세로 중앙에 찍었다(제어가 가로만 봐서) —
    "실제로 어딜 보고 있는지" 확인하려면 세로(marker_row_px)도 진짜 위치에 찍어야
    한다. row=0(맨 위)과 row=frame 맨 아래를 주면 점 y좌표가 달라져야 한다."""
    view_top = np.full((100, 140, 3), viewer.BG, np.uint8)
    view_bottom = np.full((100, 140, 3), viewer.BG, np.uint8)
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    at = __import__("time").monotonic()
    viewer.draw_camera(view_top, frame, at, marker_error_px=0.0, marker_row_px=0.0)
    viewer.draw_camera(view_bottom, frame, at, marker_error_px=0.0, marker_row_px=39.0)

    def yellow_rows(v):
        ys, _xs = np.where(np.all(v == viewer.STOP_YELLOW, axis=2))
        return ys

    top_ys, bottom_ys = yellow_rows(view_top), yellow_rows(view_bottom)
    assert len(top_ys) and len(bottom_ys)
    assert top_ys.mean() < bottom_ys.mean()   # row=0 점이 row=39 점보다 위에 찍혀야 한다


def test_centroid_uv_returns_both_axes_and_centroid_u_matches_first():
    """centroid_u()는 이제 centroid_uv()의 얇은 래퍼다 — 기존 호출자 안 깨지는지."""
    import sys as _sys
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "green_marker",
        "/home/ane/personal_repo/aba_project/aba_controller/libi_drive_controller/robot_agent/app/shelf/green_marker.py")
    gm = _ilu.module_from_spec(spec)
    _sys.modules[spec.name] = gm
    spec.loader.exec_module(gm)

    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    frame[20:40, 30:60] = (0, 255, 0)   # BGR 순초록 — HSV h≈60, s=255, v=255, 임계 안

    uv = gm.centroid_uv(frame)
    u = gm.centroid_u(frame)
    assert uv is not None and u is not None
    assert uv[0] == u
    assert 20 <= uv[1] <= 40   # v(세로)도 블록 안에 있어야 한다


def test_on_status_stamps_status_at_for_staleness_display():
    """실측(2026-08-05): "이거 방금 실패한 거 맞아? 옛날 기록 아니야?" 라는 질문이
    나왔다 — status_at 이 있어야 화면에 나이를 찍어 바로 답할 수 있다."""
    import json as _json
    from types import SimpleNamespace as _NS

    node = object.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data, node.front_half_deg = None, 15.0
    node.status = {}
    node.status_at = None
    node.step_index = -1

    before = time.monotonic()
    viewer.ShelfDockLidarViewer._on_status(
        node, _NS(data=_json.dumps({"event": "shelf_dock", "phase": "started"})))
    assert node.status_at is not None
    assert node.status_at >= before


def test_reset_clears_status_and_step_log():
    """'r' 키가 부르는 reset() — 새 "started" 없이도 화면을 손으로 비울 수 있어야 한다."""
    node = object.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data, node.front_half_deg = None, 15.0
    node.status = {"event": "shelf_dock", "phase": "failed"}
    node.status_at = time.monotonic()
    node.step_index = 3
    node.step_log = {0: {"summary": "OK"}}

    node.reset()

    assert node.status == {}
    assert node.status_at is None
    assert node.step_index == -1
    assert node.step_log == {}


def test_render_shows_stale_status_age_in_red():
    """status_at 이 3초보다 오래됐으면 나이 표시가 빨간색이어야 한다 — 옛날 기록임을
    한눈에 알 수 있게."""
    fake = SimpleNamespace(
        map_data=None, pose=None, scan_data=None,
        status={"event": "shelf_dock", "phase": "failed", "message": "실패"},
        status_at=time.monotonic() - 10.0,
        step_index=1, step_log={},
    )
    args = SimpleNamespace(robot="pinky-3", range_m=1.20, front_half_angle_deg=15.0)

    image = viewer.render(fake, args)

    assert np.any(np.all(image == viewer.HIT_RED, axis=2))


# ─── PGM 대 실제 라이다 — "얼마나 다른가" 를 로그에 남기기 위한 계측 ──────────────
# 도킹은 `/map` PGM 광선만 보고 멈추는데 PGM 은 SLAM 당시 스냅샷 + 2cm 격자다.
# CLEARANCE_M(7cm) - 로봇반지름(6cm) = 실여유 1cm 뿐이라 그 차이가 곧 안전 여유인데,
# 여태 화면에만 뜨고 저장 로그엔 PGM 값만 남아서 **차이가 몇 cm 인지 기록이 없었다**
# (2026-08-05: /tmp/shelf_dock_log_*.txt 전부 확인 — 라이다 수치 0건).

def _scan(ranges, angles, range_min=0.02, range_max=5.0):
    return viewer.ScanData(ranges=np.asarray(ranges, dtype=np.float32),
                           angles=np.asarray(angles, dtype=np.float32),
                           range_min=range_min, range_max=range_max,
                           received_at=time.monotonic())


def test_scan_front_min_takes_the_nearest_return_inside_the_cone() -> None:
    # 원뿔(±15°=±0.262rad) 안: 0.30 · 0.25. 밖: 0.10(가장 가깝지만 옆이라 무시).
    scan = _scan([0.30, 0.25, 0.10], [0.0, 0.20, 1.20])
    assert viewer.scan_front_min(scan, 15.0) == pytest.approx(0.25)


def test_scan_front_min_ignores_invalid_returns() -> None:
    """inf/NaN 과 센서 유효범위 밖 값은 빼야 한다 — 안 빼면 0 이나 inf 가 최소값이 된다."""
    scan = _scan([float("inf"), float("nan"), 0.001, 0.40], [0.0, 0.0, 0.0, 0.0],
                 range_min=0.02, range_max=5.0)
    assert viewer.scan_front_min(scan, 15.0) == pytest.approx(0.40)
    assert viewer.scan_front_min(None, 15.0) is None
    assert viewer.scan_front_min(_scan([0.5], [1.2]), 15.0) is None   # 전방에 반사 없음


def test_scan_front_min_does_not_clip_by_view_range_when_logging() -> None:
    """표시 범위로 자르는 건 **화면 전용**이다.

    로그에서까지 자르면 표시 범위 밖의 벽이 "없음" 으로 빠져서 정작 PGM 과 비교가
    안 된다 — 비교하려고 재는 값인데 비교 대상이 사라진다.
    """
    scan = _scan([0.80], [0.0])
    assert viewer.scan_front_min(scan, 15.0) == pytest.approx(0.80)          # 로그용
    assert viewer.scan_front_min(scan, 15.0, max_m=0.5) is None              # 화면용


def test_step_log_line_carries_lidar_distance_and_delta() -> None:
    """저장 로그 한 줄에 PGM·라이다·차이가 **같이** 남아야 한다."""
    summary = viewer.format_step_fields(
        "lateral_plan_ready",
        {"pgm_distance_m": 0.22, "viewer_scan_front_m": 0.194, "planned_lateral_m": 0.108})
    assert "22.0cm" in summary          # PGM
    assert "19.4cm" in summary          # 실제 라이다
    assert "diff-2.6cm" in summary      # 차이 — 음수 = 실제 벽이 더 가깝다(위험한 쪽)
    # 화면(cv2 Hershey)은 ASCII 밖 글자를 '?' 로 그린다 — 실기에서 `Δ` 가 `??` 로 나왔다.
    assert summary.isascii(), f"화면에서 깨질 글자가 있다: {summary!r}"
    assert "10.8cm" in summary          # 기존 이동량은 그대로


def test_final_plan_and_progress_lines_also_carry_the_delta() -> None:
    """마지막 접근이 제일 위험하다(실여유 1cm) — 거기도 반드시 같이 남는다."""
    plan = viewer.format_step_fields(
        "final_plan_ready",
        {"pgm_distance_m": 0.13, "viewer_scan_front_m": 0.11, "planned_forward_m": 0.06})
    assert "13.0cm" in plan and "11.0cm" in plan and "-2.0cm" in plan
    progress = viewer.format_step_fields(
        "final_progress",
        {"pgm_distance_m": 0.09, "viewer_scan_front_m": 0.075,
         "remaining_to_clearance_m": 0.02})
    assert "9.0cm" in progress and "7.5cm" in progress and "-1.5cm" in progress


def test_missing_lidar_is_marked_not_silently_dropped() -> None:
    """/scan 이 없으면 **없다고 적는다.** 조용히 빠지면 "차이 0" 으로 오해된다."""
    summary = viewer.format_step_fields(
        "lateral_plan_ready", {"pgm_distance_m": 0.22, "planned_lateral_m": 0.108})
    assert "22.0cm" in summary
    assert "lidar -" in summary


def test_status_callback_stamps_the_lidar_reading_at_arrival_time() -> None:
    """PGM 값과 **같은 순간**의 라이다 값이어야 비교에 의미가 있다.

    뷰어가 잰 값이므로 `viewer_` 접두사로 원본과 구분된다(덤프에서 출처가 드러나게).
    """
    node = viewer.ShelfDockLidarViewer.__new__(viewer.ShelfDockLidarViewer)
    node.scan_data = _scan([0.194], [0.0])
    node.front_half_deg = 15.0
    node.step_index = -1
    node.step_log = {}
    node.status = {}
    node.status_at = None
    node._on_status(SimpleNamespace(data=json.dumps(
        {"event": "shelf_dock", "phase": "lateral_plan_ready",
         "pgm_distance_m": 0.22, "planned_lateral_m": 0.108})))

    idx = viewer.step_index_for("lateral_plan_ready", None)
    assert node.step_log[idx]["fields"]["viewer_scan_front_m"] == pytest.approx(0.194)
    assert "19.4cm" in node.step_log[idx]["summary"]
