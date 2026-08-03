#!/usr/bin/env python3
"""Perception socket server (B안): owns the webcam, runs FollowerPerception,
draws boxes, and streams annotated JPEG frames to a Qt viewer over localhost
TCP. Receives newline commands: register / reset.

Run from the follower_perception/ package root:
    python scripts/perception_server.py --camera 0 --port 5007
    python scripts/perception_server.py --test-pattern           # no webcam/torch
"""
import argparse
import json
import os
import select
import socket
import sys
import time

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # follower_perception/
sys.path.insert(0, _PKG)
sys.path.insert(0, os.path.join(os.path.dirname(_PKG), "follower_BT"))   # sibling follower_BT/

import cv2
import numpy as np

from scripts.frame_proto import send_frame
from scripts.cmd_preview import compute_cmd_vel


def _status_line(matcher):
    rs, hs = matcher.last_reid_sim, matcher.last_hsv_sim
    parts = [f"reid={rs:.2f}/{matcher.reid_threshold:.2f}" if rs is not None else "reid=-"]
    if matcher.hsv_threshold is None:
        parts.append("hsv=off")
    else:
        parts.append(f"hsv={hs:.2f}/{matcher.hsv_threshold:.2f}"
                     if hs is not None else "hsv=-")
    parts.append("reg" if matcher.is_registered else "noreg")
    if matcher.is_registered:
        parts.append(f"gal={len(matcher.gallery)}")   # online gallery size (grows)
    if matcher.safe_id is not None:
        parts.append(f"trk#{matcher.safe_id}")   # ByteTrack id — changing is normal
    return "  ".join(parts)


def _hud_text(img, text, org, color, scale=0.6, thick=2):
    """Readable HUD text: thick black outline behind, colored text on top (BGR)."""
    # 외곽선을 `thick + 3` 고정으로 두면 얇은 글자(thick=1, 저해상도)에서 검은 테두리가
    # 글자를 덮는다. 두께에 비례시킨다 — thick=2 에서 5 라 예전과 같은 값이다.
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0),
                thick * 2 + 1, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thick, cv2.LINE_AA)


#: HUD 치수를 맞춘 기준 프레임 폭. 이 폭에서 k=1 이라 예전과 픽셀 단위로 같다.
HUD_REF_WIDTH = 640.0
#: 축소 하한. 완전 비례(320 폭에서 k=0.5)로 줄이면 패널이 확대해 띄우지 않는 경우
#: 글자를 못 읽는다. 0.6 은 "작아지되 읽히는" 선이다 — 필요하면 여기만 만진다.
HUD_MIN_SCALE = 0.6


def _hud_scale(width):
    """프레임 폭에 맞춘 HUD 배율.

    글자·선 두께·여백이 전부 640 폭 기준 픽셀 상수였다. 스트리밍을 320x240 으로
    내리자(`scripts/all/libi_pi.sh` `--width 320`) 같은 글자가 화면의 두 배 비중을
    차지해 영상을 가렸다. 상수를 해상도마다 다시 고르는 대신 배율 하나로 묶는다.
    """
    return max(HUD_MIN_SCALE, float(width) / HUD_REF_WIDTH)


#: 판정에 쓰는 키포인트만 그린다. 예전에는 COCO-17 전부와 간선 16개를 그렸는데,
#: 판정은 4점만 보므로 화면과 판정이 서로 다른 이야기를 했다.
#  5,6 어깨 · 11,12 골반 · 13,14 무릎(shoulder_knee 축일 때만)
L_SH, R_SH, L_HIP, R_HIP, L_KNEE, R_KNEE = 5, 6, 11, 12, 13, 14

_TORSO_POINTS = (L_SH, R_SH, L_HIP, R_HIP)
_TORSO_EDGES = ((L_SH, R_SH), (L_HIP, R_HIP))          # 어깨선 · 골반선

#: 자세 문자열 → 색 (BGR). 판정 결과를 한눈에 구분하려는 것뿐이다.
_POSTURE_COLORS = {
    "Standing": (0, 255, 0),        # 초록 — 따라가도 되는 상태
    "Side": (0, 165, 255),          # 주황 — 옆을 봤다. 주행을 막는다
    "Lying": (0, 0, 255),           # 빨강 — 주행을 막는다
    "Calibrating": (0, 255, 255),   # 노랑 — 기준 비율 측정 중
}
_POSTURE_UNKNOWN_COLOR = (170, 170, 170)


def _midpoint(a, b):
    return None if (a is None or b is None) else ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)


def _draw_skeleton(vis, keypoints, conf_min, color, k=1.0, axis="torso"):
    """crop 좌표 키포인트를 원본 프레임 좌표로 옮겨 그린다.

    `PoseEstimator` 는 owner bbox **crop** 에만 pose 를 돌리므로 좌표가 crop 기준이다
    (`pose_estimator.py` `_keypoints` 주석). 그리려면 bbox 원점을 더해야 한다 — 판정은
    상대 기하만 봐서 되돌릴 필요가 없었지만, 화면은 절대 좌표가 필요하다.

    신뢰도가 낮은 점은 **안 그린다.** 판정이 안 쓰는 점을 화면에만 그리면, 판정이
    Unknown 인데 골격은 멀쩡해 보이는 모순이 생긴다. 같은 이유로 판정이 아예 안 쓰는
    점(팔꿈치 등)은 신뢰도와 무관하게 그리지 않는다 — 4점(+축이 `shoulder_knee` 면
    무릎 2점)과 어깨선·골반선·축선만 그린다.
    """
    xy, conf, (ox, oy) = keypoints

    def pt(i):
        c = float(conf[i]) if conf is not None and i < len(conf) else 0.0
        if i >= len(xy) or c < conf_min:
            return None
        return (int(xy[i][0]) + ox, int(xy[i][1]) + oy)

    use_knees = axis == "shoulder_knee"
    idxs = _TORSO_POINTS + ((L_KNEE, R_KNEE) if use_knees else ())
    pts = {i: pt(i) for i in idxs}

    line_w = max(1, int(round(2 * k)))
    dot_r = max(1, int(round(3 * k)))
    for a, b in _TORSO_EDGES:
        if pts[a] and pts[b]:
            cv2.line(vis, pts[a], pts[b], color, line_w, cv2.LINE_AA)

    # 축선: 어깨중점 → (골반중점 또는 무릎중점). `shoulder_knee` 가 아니면(모르는
    # 축 문자열 포함) 골반 축으로 떨어진다 — 축을 몰라서 안 그리는 것보다는 몸통선이
    # 낫다(요구사항: unknown axis 는 죽지 않고 torso 로 대체).
    sh_mid = _midpoint(pts[L_SH], pts[R_SH])
    tgt_mid = _midpoint(pts[L_KNEE], pts[R_KNEE]) if use_knees \
        else _midpoint(pts[L_HIP], pts[R_HIP])
    if sh_mid and tgt_mid:
        cv2.line(vis, sh_mid, tgt_mid, color, line_w, cv2.LINE_AA)

    for p in pts.values():
        if p:
            cv2.circle(vis, p, dot_r, color, -1, cv2.LINE_AA)


def _calibration_text(pose, fps):
    """캘리브 카운트다운 문자열. 측정 중이 아니면 None.

    남은 초는 **실측 fps** 로 환산한다. 공칭 15 로 나누면 프레임이 밀릴 때
    숫자가 멈춘 것처럼 보여, 보는 사람이 로봇이 고장 났다고 생각한다.
    """
    if pose is None or not getattr(pose, "calibrating", False):
        return None
    got, need = pose.calibration_progress
    return f"자세 측정 중… {pose.calibration_remaining_sec(fps):.1f}초  ({got}/{need})"


def draw_overlay(frame, det, *, cands=None, pick=None, cmd=None, status_extra="",
                 pose=None, fps=None, hud_text=True):
    """`hud_text=False` 면 **구석 글자만** 빼고 그린다.

    bbox·스켈레톤·가이드선은 화면 위 좌표에 붙는 그림이라 프레임에 구워야 하지만,
    STATE·POSTURE·cmd_vel 같은 글자는 좌표와 무관하다. 그걸 JPEG 에 구워 보내면
    패널에서 크기·글꼴·색을 못 고치고, 영상이 축소되면 같이 뭉개져 안 읽힌다.
    패널로 가는 스트림은 글자를 빼고 `POSE` 사이드밴드로 **값을 보낸다**
    (`serve_loop`). 로컬 디버그 창은 기본값 그대로라 예전과 똑같이 보인다.
    """
    vis = frame.copy()
    h, w = vis.shape[:2]
    # 아래 글자 크기·두께·여백은 전부 640 폭 기준 상수였다. 프레임 폭에 맞춰 한 번에
    # 줄인다(`_hud_scale`). 640 에서는 k=1 이라 예전과 완전히 같은 그림이다.
    k = _hud_scale(w)
    thick = max(1, int(round(2 * k)))
    pad = max(2, int(round(8 * k)))
    # thirds guide lines: left / center / right direction zones
    for x in (w // 3, 2 * w // 3):
        cv2.line(vis, (x, 0), (x, h), (70, 70, 70), 1)
    # every YOLO person detection (thin gray) — shows detection runs each frame,
    # even before registration.
    for c in (cands or []):
        x1, y1, x2, y2 = (int(v) for v in c.bbox)
        is_pick = pick is not None and c.track_id == pick.track_id
        col = (0, 255, 255) if is_pick else (170, 170, 170)   # pick=yellow
        cv2.rectangle(vis, (x1, y1), (x2, y2), col, thick if is_pick else 1)
        if is_pick:
            cv2.putText(vis, "register target",
                        (max(0, x1), max(int(34 * k), y1 - pad)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k, col, thick)
    # owner box on top (after registration)
    if det is not None and det.is_owner:
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        color = (0, 165, 255) if det.is_predicted else (0, 255, 0)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, thick)
        label = "OWNER (predicted)" if det.is_predicted else "OWNER"
        cv2.putText(vis, label, (max(0, x1), max(int(20 * k), y1 - pad)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * k, color, thick)

        # 자세 — 스켈레톤과 판정을 같이 그린다.
        #
        # 판정만 글자로 띄우면 "왜 Unknown 인지"를 알 수 없다. 골격을 같이 그리면 점이
        # 몇 개나 잡혔는지가 눈에 보여서, 모델이 못 본 건지 임계가 센 건지 바로 갈린다.
        # 그리는 것은 서버 몫이다 — 패널은 JPEG 를 그대로 띄울 뿐이고 bbox·OWNER 도
        # 이미 여기서 굽는다(FollowScreen.qml 머리말).
        posture = getattr(det, "posture", None)
        pcol = _POSTURE_COLORS.get(posture, _POSTURE_UNKNOWN_COLOR)
        kp = getattr(pose, "last_keypoints", None) if pose is not None else None
        if kp is not None:
            # 판정이 실제로 쓴 축을 그대로 그린다 — 안 읽으면 화면은 항상 torso 축인데
            # 판정은 shoulder_knee 로 나오는 어긋남이 생긴다(이 파일이 고치는 이유).
            # `pose` 가 구버전(속성 없음)이거나 값이 None 이어도 torso 로 떨어진다.
            axis = getattr(pose, "last_axis", "torso") or "torso"
            _draw_skeleton(vis, kp, pose.conf_min, pcol, k, axis=axis)
        if posture and hud_text:
            # 주행 허용 여부까지 같이 낸다. 자세와 주행은 별개다 — PostureGate 는
            # 한 번 Lying 을 보면 Standing 이 몇 프레임 이어질 때까지 계속 막는다.
            gate = "" if getattr(det, "motion_ok", True) else "  [motion blocked]"
            _hud_text(vis, f"POSTURE: {posture}{gate}",
                      (max(0, x1), min(h - pad, y2 + int(24 * k))),
                      pcol, 0.6 * k, thick)
            # 등록 직후 ~4초 동안 로봇이 멈춰 서서 기준 비율을 재는데, 화면에 "Calibrating"
            # 만 떠 있으면 고장으로 보인다. 카운트다운을 자세 글자 바로 아래에 덧그린다.
            cal_text = _calibration_text(pose, fps)
            if cal_text:
                _hud_text(vis, cal_text,
                          (max(0, x1), min(h - pad, y2 + int(48 * k))),
                          pcol, 0.55 * k, thick)
    if cmd is not None and hud_text:
        state = cmd.get("state", "")
        scol = {"IDLE": (200, 200, 200), "FOLLOWING": (0, 255, 0),
                "PEEK": (0, 255, 255), "SEARCHING": (0, 165, 255)}.get(state, (0, 0, 255))
        _hud_text(vis, f"STATE: {state}", (int(12 * k), int(40 * k)),
                  scol, 0.9 * k, thick)                                # state (semantic)
        txt = (f"cmd_vel  lin.x={cmd['linear_x']:+.2f}  ang.z={cmd['angular_z']:+.2f}"
               f"   [{cmd['drive']} | {cmd['turn']}]")
        _hud_text(vis, txt, (int(12 * k), int(72 * k)),
                  (255, 0, 0), 0.62 * k, thick)                        # blue (BGR)
    if status_extra and hud_text:
        _hud_text(vis, status_extra, (int(12 * k), h - int(12 * k)),
                  (0, 0, 255), 0.62 * k, thick)                        # red (BGR)
    return vis


def _cm(m):
    """metres -> integer centimetres for the viewer; -1 means no reading."""
    return -1 if (m is None or not np.isfinite(m)) else int(round(m * 100.0))


def _num(v):
    """화면에 띄울 수 있는 실수만 통과시킨다. None·inf·NaN 은 전부 None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _pose_payload(det, cmd, pose, fps, matcher=None):
    """패널이 글자로 그릴 값들. `POSE ` + JSON 한 줄.

    LIDR 은 정수 8개라 공백으로 갈라도 되지만 여기는 문자열·실수·없음이 섞인다.
    자리수로 맞추면 필드를 하나 더할 때마다 양쪽을 같이 고쳐야 하고, 한쪽만
    고치면 조용히 어긋난다. JSON 은 Qt(QJsonDocument)에 이미 있다.
    """
    body = {
        "posture": getattr(det, "posture", None) or None,
        # 자세와 주행은 별개다 — Standing 이어도 LiDAR 나 게이트가 막을 수 있다.
        "motionOk": bool(getattr(det, "motion_ok", True)) if det is not None else None,
        # 판정이 실제로 쓴 값 셋. ratio 가 trip 을 넘으면 측면이다.
        "ratio": _num(getattr(pose, "last_ratio", None)),
        "refRatio": _num(getattr(pose, "ref_ratio", None)),
        "sideTrip": _num(getattr(pose, "side_trip", None)),
        "axis": getattr(pose, "last_axis", None),
        # α-β 코스팅 중인가 — **검출이 끊겼는데 예측으로 이어 붙이는 중**이라는 뜻이다.
        # 예전에는 JPEG 의 주황색 bbox 로만 알 수 있었는데(draw_overlay 의 is_predicted),
        # 영상이 축소되면 색 구분이 어렵고 "필터가 도는 건지"를 값으로 못 봤다.
        # 화면이 글자로 띄울 수 있게 값으로 내보낸다(FollowScreen).
        "isPredicted": bool(getattr(det, "is_predicted", False)) if det is not None else None,
        # 등록된 **그 사람**인가. 지금 파이프라인은 owner 만 내보내므로(pipeline.py 의
        # 유일한 `Detection(...)` 이 `is_owner=True` 고정) 사실상 `det is not None` 과
        # 같지만, **패널이 그 사실에 기대게 두지 않는다.**
        #
        # 길잡이 회복에서 화면이 "또 같이 뒤로 이동해주세요!" 를 띄우는 근거가 이 값이다
        # (GuideScreen.qml). 언젠가 후보 검출을 같이 실어 보내게 되면 — `requester_visible`
        # 이 이미 `det.is_owner` 를 검사하는 걸 보면 그럴 작정이었다 — 그때 이 문구는
        # **지나가던 사람**을 보고 뜬다. 값을 명시해 두면 그날 여기만 맞으면 된다.
        "isOwner": bool(getattr(det, "is_owner", False)) if det is not None else None,
    }
    # ReID/HSV 매칭 신뢰도 — _status_line 과 같은 소스(matcher.last_reid_sim/last_hsv_sim).
    # 예전엔 hud_text=False 라 패널로 가는 JPEG 에서 이 글자가 빠졌고, POSE 에도 없었다.
    if matcher is not None and matcher.is_registered:
        body["reidSim"] = _num(matcher.last_reid_sim)
        body["reidThreshold"] = _num(matcher.reid_threshold)
        if matcher.hsv_threshold is not None:
            body["hsvSim"] = _num(matcher.last_hsv_sim)
            body["hsvThreshold"] = _num(matcher.hsv_threshold)
    # ⚠️ **등록 전에는 내보내지 않는다.** `pose.calibrating` 은 "표본을 아직 다 못
    # 모았다"는 뜻이라(pose_estimator.py:166 `not self._calibrator.done`) 프로세스가
    # 뜨는 순간부터 참이다. 그런데 표본은 owner 가 잡힌 프레임에서만 쌓이고
    # (pipeline.py:173-180), 등록 전에는 `matcher.match` 가 늘 None 이라 **0/60 에서
    # 영영 안 움직인다.** 남은 시간도 `(need-got)/fps` 라 같은 값에 고정된다.
    # 그대로 내보내면 패널이 「등록」을 누르기도 전에 "기준 측정 중 0/60 · 3.8초 남음"
    # 을 띄우고 그 숫자가 줄지도 않는다(실측 2026-08-01).
    # matcher 를 안 받은 호출은 판단 근거가 없으므로 예전대로 내보낸다.
    if (pose is not None and getattr(pose, "calibrating", False)
            and (matcher is None or matcher.is_registered)):
        got, need = pose.calibration_progress
        body["calibrating"] = {"remainingSec": pose.calibration_remaining_sec(fps),
                               "got": got, "need": need}
    if cmd is not None:
        body["state"] = cmd.get("state") or None
        body["linearX"] = _num(cmd.get("linear_x"))
        body["angularZ"] = _num(cmd.get("angular_z"))
    return b"POSE " + json.dumps(body).encode("utf-8")


#: `poll_cmd` 이 "상대가 연결을 닫았다"를 알리는 표식. `None`(명령 없음)과 **반드시**
#: 구분돼야 한다 — 섞으면 죽은 소켓을 붙들고 도는 아래 사고가 난다.
EOF = object()


def serve_loop(conn, frames, perception, *, poll_cmd=None, jpeg_quality=80,
               cmd_sink=None, policy=None, lidar_source=None, detection_sink=None,
               camera_source=None, role_source=None):
    last_t = time.monotonic()
    for frame in frames:
        # None = 심장박동(영상이 아직 안 옴). 소켓만 확인하고 넘어간다 — 이게 없으면
        # 프레임이 안 오는 동안 뷰어가 끊긴 것을 영영 못 알아챈다(udp_video.frames 주석).
        if frame is None:
            if poll_cmd is not None and poll_cmd(conn) is EOF:
                return
            continue
        _sync_camera(perception, camera_source)
        _sync_role(perception, role_source)
        cmd = poll_cmd(conn) if poll_cmd else None
        if cmd is EOF:
            # 패널이 닫혔다. 여기서 안 빠지면 소켓이 CLOSE-WAIT 로 잔류하고,
            # `listen(1)` + 뷰어 1개 구조라 **다음 패널이 영영 못 붙는다**
            # (SYN-SENT 로 매달린 채 "AI 서버에 연결 중…"). 실측 2026-07-28.
            return
        if cmd == "register":
            perception.register_from_image(frame)
        elif cmd == "reset":
            perception.reset()
        perception.run(frame)
        det = perception.get_latest()
        # 로봇의 libi_perception 으로 주인 검출을 직접 보낸다(선택). 이 채널이 없으면
        # 로봇 쪽 제어 루프는 더미 스텁만 받는다 — 회복 BT 가 진짜 검출을 한 번도
        # 못 본다는 뜻이다. 안 보이는 프레임은 null 을 보내는 것이 계약이다.
        # 프레임을 같이 넘겨 해상도를 실어 보낸다(detection_sink.detection_to_dict).
        if detection_sink is not None:
            detection_sink(det, frame)
        cands = perception.last_cands
        # before registration, highlight which candidate the 등록 button would pick
        pick = None if perception.matcher.is_registered \
            else perception._pick_central(cands, frame)
        now = time.monotonic(); dt = now - last_t; last_t = now
        cmd = policy.step(det, frame.shape[1], dt,
                          registered=perception.matcher.is_registered) \
            if policy is not None else compute_cmd_vel(det, frame.shape[1])
        if cmd_sink is not None:                 # optional drive hook (opt-in)
            cmd_sink(cmd)
        # 실측 fps 로 캘리브 카운트다운을 환산한다 — 공칭 15 로 나누면 프레임이 밀릴 때
        # 숫자가 멈춘 것처럼 보인다(`_calibration_text`). dt<=0(첫 tick·시계 튐)은
        # 1/dt 가 무한대/음수가 되므로 None 으로 막는다.
        fps = 1.0 / dt if dt > 0 else None
        # 패널로 가는 스트림은 **글자를 굽지 않는다** — 아래 POSE 로 값을 보내고
        # 패널이 자기 글꼴·색으로 그린다(draw_overlay 머리말).
        vis = draw_overlay(frame, det, cands=cands, pick=pick, cmd=cmd,
                           status_extra=_status_line(perception.matcher),
                           pose=_pose_or_none(perception), fps=fps, hud_text=False)
        ok, buf = cv2.imencode(".jpg", vis,
                               [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if ok:
            try:
                send_frame(conn, buf.tobytes())
                send_frame(conn, _pose_payload(det, cmd, _pose_or_none(perception), fps, perception.matcher))
                if lidar_source is not None:                 # LiDAR telemetry (display only)
                    s = lidar_source.latest()
                    if s is not None:                        # order: FL F FR L R BL B BR
                        send_frame(conn, b"LIDR %d %d %d %d %d %d %d %d" % (
                            _cm(s["front_left"]), _cm(s["front"]), _cm(s["front_right"]),
                            _cm(s["left"]),                       _cm(s["right"]),
                            _cm(s["back_left"]),  _cm(s["back"]), _cm(s["back_right"])))
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


def make_socket_poller():
    """Return a poll_cmd(conn) that non-blockingly reads newline commands.

    끊김은 `EOF` 로 돌려준다 — `None`(이번 tick 에 명령 없음)과 다른 뜻이다.
    예전에는 둘 다 `None` 이라, 상대가 닫아도 `select` 는 EOF 를 계속 readable 로
    보고하고 `recv` 는 계속 `b""` 를 줘서 `serve_loop` 이 죽은 소켓을 붙들고 돌았다.
    `send_frame` 도 예외를 안 냈다 — 상대가 half-close(FIN)만 하고 RST 를 안 보내면
    송신은 한동안 성공한다.
    """
    state = {"buf": b""}

    def poll(conn):
        r, _, _ = select.select([conn], [], [], 0)
        if not r:
            return None
        try:
            data = conn.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            return EOF
        except OSError:
            return None
        if not data:
            return EOF
        state["buf"] += data
        cmd = None
        while b"\n" in state["buf"]:
            line, state["buf"] = state["buf"].split(b"\n", 1)
            line = line.strip().decode(errors="ignore")
            if line:
                cmd = line   # last command this tick wins
        return cmd

    return poll


class _AlwaysBox:
    """Test-pattern detector: always reports one full-frame track."""
    def detect(self, frame):
        from follower_perception.detection import TrackedBox
        h, w = frame.shape[:2]
        return [TrackedBox(bbox=(w * 0.25, h * 0.2, w * 0.75, h * 0.9),
                           cx=w / 2, cy=h / 2, area=w * h * 0.3,
                           track_id=1, confidence=0.9)]

    def reset(self):
        pass


def test_pattern_frames(n=None):
    """Synthetic BGR frames: a coloured person-ish block on a moving bg.
    Runs with no webcam and no torch."""
    i = 0
    w, h = 640, 480
    while n is None or i < n:
        img = np.full((h, w, 3), 30, dtype=np.uint8)
        cx = int(w * (0.5 + 0.25 * np.sin(i * 0.05)))
        cv2.rectangle(img, (cx - 60, 120), (cx + 60, 400), (60, 40, 200), -1)
        yield img
        i += 1


def _camera_frames(index, width=None, height=None):
    """USB 캠 프레임 제너레이터. `width/height` 를 주면 캡처 해상도를 그걸로 요청한다.

    ## [2026-07-30] 해상도 인자를 붙인 이유

    예전에는 `CAP_PROP_*` 을 하나도 안 걸어서 **드라이버 기본 해상도**로만 돌았다.
    `camera_sender.py` 의 `--width` 는 picamera2(앞캠)에만 전달돼서, 뒷캠은 무엇을 주든
    640x480 이었다 — 그 사실이 코드 어디에도 안 드러나 있었다.

    Pi 부하를 줄이려면 뒷캠도 같이 내려야 한다. 캡처 버퍼 복사·회전·생프레임 탭 기록이
    전부 **픽셀 수에 비례**하기 때문이다(640x480 → 480x360 이면 픽셀이 56%).

    ⚠️ **요청이 곧 적용은 아니다.** UVC 캠은 지원하지 않는 크기를 조용히 무시하고
       가까운 값이나 기본값으로 연다. 그래서 설정 뒤 **실제 값을 읽어 찍는다** —
       안 그러면 "줄였다고 믿는데 안 줄어든" 상태를 CPU 수치로만 뒤늦게 발견한다.
    """
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"[error] cannot open camera {index}"); raise SystemExit(2)
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        note = "" if (got_w, got_h) == (int(width), int(height)) else "  ⚠️ 요청과 다름"
        print(f"[cam{index}] 캡처 해상도 요청 {int(width)}x{int(height)} → 실제 {got_w}x{got_h}{note}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


def _build_pose(args):
    """자세 추정기. 실패해도 서버를 죽이지 않는다 — 자세 판정 없이도 추종은 돌아야 한다.

    검출 가중치(best.pt)는 task=detect 라 키포인트를 못 낸다. 그래서 owner bbox crop 에만
    yolo11n-pose 를 2차로 돌린다(전체 프레임이 아니라 crop 이라 비용이 작다).
    """
    # 기본은 꺼짐 — `--pose` 로만 켠다. 켜면 프레임당 2차 추론이 붙어 추종 주기가 느려진다.
    if not getattr(args, "pose", False) or args.test_pattern:
        return None
    try:
        from follower_perception.pose_estimator import PoseEstimator
        est = PoseEstimator(every_n=args.pose_every_n)
        print(f"[pose] 자세 판정 on — 매 {args.pose_every_n} 프레임")
        return est
    except Exception as e:      # noqa: BLE001 — 자세 없이도 추종은 계속돼야 한다
        print(f"[pose] 자세 판정 off ({type(e).__name__}: {e})")
        return None


def _build_perception(args):
    from follower_perception.reid_engine import ReIDEngine
    from follower_perception.pipeline import FollowerPerception
    pose = _build_pose(args)
    if args.test_pattern:
        reid = ReIDEngine(backend="colour")
        p = FollowerPerception(detector=_AlwaysBox(), reid=reid, pose=pose)
    else:
        try:
            from follower_perception.detector import Detector
        except ImportError as e:  # pragma: no cover
            print(f"[error] ultralytics/torch not installed ({e}). Use "
                  f"--test-pattern or run in your model venv."); raise SystemExit(2)
        reid = ReIDEngine(device=args.device)
        p = FollowerPerception(detector=Detector(device=args.device), reid=reid, pose=pose)
    if args.no_hsv:
        p.matcher.hsv_threshold = None
    elif args.hsv_threshold is not None:
        p.matcher.hsv_threshold = float(args.hsv_threshold)
    if args.reid_threshold is not None:
        p.matcher.reid_threshold = float(args.reid_threshold)
    return p


def _rotate_frames(frames, deg):
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(deg)
    for f in frames:
        if f is None:            # 심장박동은 그대로 통과시킨다(회전할 것이 없다)
            yield None
            continue
        yield cv2.rotate(f, rot) if rot is not None else f


def _sync_camera(perception, camera_source):
    """로봇이 카메라를 바꿨으면 추적 상태를 비운다.

    모르고 지나가면 ByteTrack id 가 시점이 통째로 바뀐 프레임에 그대로 이어져,
    추적기가 엉뚱한 사람을 주인으로 붙들 수 있다. 등록 템플릿은 유지된다 —
    사람이 바뀐 게 아니라 보는 각도가 바뀐 것이다.
    """
    if camera_source is None:
        return
    cam = camera_source.latest()
    if cam in ("front", "back"):
        perception.set_camera(cam)


def _sync_role(perception, role_source):
    """로봇이 알려준 세션 역할을 반영한다. 자세 추정을 켜고 끄는 근거다.

    못 받으면(구독 실패·ROS 미소싱) 아무것도 안 한다 — `pose_active` 가 "모름" 을
    "켬" 으로 다루므로 예전과 똑같이 돈다.
    """
    if role_source is None:
        return
    role = role_source.latest()
    if role is not None:
        perception.set_role(role)


def _pose_or_none(perception):
    """골격을 그리고 자세 값을 실을 것인가 — 아니면 `None`(=자세 없음)으로 넘긴다.

    ⚠️ 골격은 **서버가 JPEG 에 굽는다**(`draw_overlay`). 패널은 그 그림을 끌 수 없으므로
       여기서 안 넘기는 것이 유일한 방법이다(`RobotController.cpp` 가 그렇게 적어 뒀다).
    """
    return perception.pose if getattr(perception, "pose_active", True) else None


def _make_detection_sink(host, port):
    """로봇의 libi_perception 으로 주인 검출을 보내는 콜백.

    링크가 끊겨도 추론 루프를 죽이지 않는다(`RobotDetectionSink.send` 가 예외를 삼키고
    다음 send 에서 재연결한다). 주인이 안 보이는 프레임은 **null 을 보낸다** —
    받는 쪽 `detection_from_dict(None)` 이 None 을 그대로 통과시키는 계약이다.

    ⚠️ 프레임을 같이 넘긴다. 좌표(`cx`/`area`)를 만든 쪽이 자기 해상도를 말해야
       받는 쪽 PID 가 환산할 수 있다 — 이유는 `detection_sink.detection_to_dict` 참고.
    """
    import os
    import sys
    # detection_sink.py 는 aba_ai_service 루트에 있다(이 스크립트의 조부모).
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from detection_sink import RobotDetectionSink, detection_to_dict

    sink = RobotDetectionSink(host, port)
    print(f"[ok] 로봇 검출 채널 → {host}:{port}")

    def send(det, frame=None):
        sink.send(detection_to_dict(det, frame))

    return send


def _run_local_show(frames, perception, cmd_sink=None, policy=None, detection_sink=None,
                    camera_source=None, role_source=None):
    """Local cv2 window (no Qt, no socket). Keys: r=register, x=reset, q/ESC=quit."""
    win = "perception  [r]register [x]reset [q]quit"
    last_t = time.monotonic()
    for frame in frames:
        _sync_camera(perception, camera_source)
        _sync_role(perception, role_source)
        perception.run(frame)
        det = perception.get_latest()
        if detection_sink is not None:
            detection_sink(det, frame)
        cands = perception.last_cands
        pick = None if perception.matcher.is_registered \
            else perception._pick_central(cands, frame)
        now = time.monotonic(); dt = now - last_t; last_t = now
        cmd = policy.step(det, frame.shape[1], dt,
                          registered=perception.matcher.is_registered) \
            if policy is not None else compute_cmd_vel(det, frame.shape[1])
        if cmd_sink is not None:                 # optional drive hook (opt-in)
            cmd_sink(cmd)
        # 실측 fps 로 캘리브 카운트다운을 환산한다 — 공칭 15 로 나누면 프레임이 밀릴 때
        # 숫자가 멈춘 것처럼 보인다(`_calibration_text`). dt<=0(첫 tick·시계 튐)은
        # 1/dt 가 무한대/음수가 되므로 None 으로 막는다.
        fps = 1.0 / dt if dt > 0 else None
        vis = draw_overlay(frame, det, cands=cands, pick=pick, cmd=cmd,
                           status_extra=_status_line(perception.matcher),
                           pose=_pose_or_none(perception), fps=fps)
        cv2.imshow(win, vis)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("r"):
            perception.register_from_image(frame)
        elif k == ord("x"):
            perception.reset()
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="Perception socket server (B안)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int, default=0)
    src.add_argument("--test-pattern", dest="test_pattern", action="store_true")
    src.add_argument("--udp", action="store_true", help="receive video over UDP from robot")
    ap.add_argument("--port", type=int, default=5007)          # Qt viewer port
    ap.add_argument("--bind", default="0.0.0.0",
                    help="뷰어 포트를 열 주소. 기본은 모든 인터페이스 — 로봇 터치패널이 "
                         "다른 머신이라 127.0.0.1 이면 못 붙는다. 같은 머신에서만 쓸 땐 "
                         "127.0.0.1 로 좁힌다.")
    ap.add_argument("--udp-port", dest="udp_port", type=int, default=6001)  # robot video in
    ap.add_argument("--device", default=None)
    ap.add_argument("--hsv-threshold", dest="hsv_threshold", type=float, default=None)
    ap.add_argument("--reid-threshold", dest="reid_threshold", type=float, default=None)
    ap.add_argument("--no-hsv", dest="no_hsv", action="store_true")
    ap.add_argument("--show", action="store_true",
                    help="local cv2 window instead of streaming to a viewer")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate incoming frames by N degrees (e.g. 180 for upside-down camera)")
    ap.add_argument("--drive-host", dest="drive_host", default=None,
                    help="robot IP to send cmd_vel values to (ENABLES driving; omit = preview only)")
    ap.add_argument("--drive-port", dest="drive_port", type=int, default=6002)
    ap.add_argument("--lidar-ros", dest="lidar_ros", action="store_true",
                    help="subscribe to /scan via ROS2 and show front/back/left/right in the viewer")
    ap.add_argument("--scan-topic", dest="scan_topic", default="/scan")
    ap.add_argument("--lidar-flip", dest="lidar_flip", action="store_true",
                    help="LiDAR mounted rotated 180 deg (front<->back, left<->right)")
    ap.add_argument("--robot-host", dest="robot_host", default=None,
                    help="로봇 libi_perception 으로 주인 검출을 직접 보낼 주소. "
                         "생략하면 안 보낸다(뷰어 전용). 이 채널이 없으면 로봇의 회복 BT 가 "
                         "진짜 검출을 한 번도 못 본다.")
    ap.add_argument("--robot-detection-port", dest="robot_detection_port",
                    type=int, default=6000)
    # ⚠️ 이름을 `--camera` 로 하면 안 된다 — 소스 선택용 `--camera <index>` 가 이미 있다
    #    (argparse 가 중복 옵션으로 죽는다).
    ap.add_argument("--camera-label", dest="camera_label", default=None,
                    choices=["front", "back"],
                    help="이 스트림이 어느 캠인지. 검출 payload 에 실려 나가고, 로봇의 "
                         "회복 BT 가 '어느 캠에서 찾았나' 를 판단하는 데 쓴다.")
    ap.add_argument("--camera-topic", dest="camera_topic", default=None,
                    help="로봇의 /libi/camera_select 를 구독해 카메라 전환을 따라간다. "
                         "예: --camera-topic /libi/camera_select. ROS sourced + 같은 "
                         "ROS_DOMAIN_ID 가 필요하다(--lidar-ros 와 같은 전제).")
    ap.add_argument("--role-topic", dest="role_topic", default="/libi/perception_role",
                    help="로봇의 세션 역할 토픽. **--camera-topic 을 준 경우에만** 구독한다"
                         "(같은 ROS 소싱 전제). 역할을 알면 길잡이에서 자세·골격을 끈다 — "
                         "자세 게이트는 추종 전용 안전 규칙이기 때문이다.")
    ap.add_argument("--pose", dest="pose", action="store_true",
                    help="자세 판정을 켠다. **기본은 꺼짐** — 켜면 누워 있는 사람에게는 "
                         "로봇이 다가가지 않는다(대신 프레임 예산을 더 쓴다).")
    ap.add_argument("--pose-every-n", dest="pose_every_n", type=int, default=None,
                    help="자세 추론 주기(프레임). 프레임 예산을 넘기면 3 정도로 올린다.")
    args = ap.parse_args()
    if args.pose_every_n is None:
        from follower_perception.constants import POSE_EVERY_N_FRAMES
        args.pose_every_n = POSE_EVERY_N_FRAMES

    _recv = None
    if args.udp:
        from scripts.udp_video import UdpVideoReceiver
        UdpVideoReceiver  # imported lazily so non-UDP runs need no extra deps
        _recv = UdpVideoReceiver(args.udp_port)
        print(f"[ok] receiving UDP video on :{args.udp_port} (from robot)")

    def new_frames():
        """프레임 공급원을 **처음 고른 것과 같은 종류로** 다시 만든다.

        뷰어가 끊기면 제너레이터를 새로 만들어야 하는데(카메라 제너레이터는 닫히면
        소진된다), 예전엔 그 자리에서 무조건 `_camera_frames()` 를 불렀다. UDP 모드로
        떠 있어도 두 번째 뷰어가 붙는 순간 **없는 로컬 카메라를 열려다 서버가 죽었다.**

            [ok] receiving UDP video on :6001 (from robot)
            [ok] viewer connected → disconnected
            [ok] viewer connected            ← 두 번째
            [error] cannot open camera 0     ← 여기서 프로세스 종료

        증상은 "관리자 추종이 두 번째부터 안 되고 바퀴도 안 움직인다"로 나타났다 —
        AI 서버가 죽었으니 검출이 끊기고, 추종 제어 루프가 속도를 만들 수 없었다.
        (실측 2026-07-28)
        """
        if _recv is not None:
            # idle_yield: 로봇이 camera_select=none 이라 영상을 안 보낼 때도 루프가 돌아야
            # 뷰어 끊김을 알아챈다. 안 그러면 다음 패널이 영영 못 붙는다.
            f = _recv.frames(idle_yield=True)
        elif args.test_pattern:
            f = test_pattern_frames()
        else:
            f = _camera_frames(args.camera)
        return _rotate_frames(f, args.rotate) if args.rotate else f

    frames = new_frames()
    perception = _build_perception(args)

    cmd_sink = None
    if args.drive_host:
        from scripts.cmd_channel import CmdSender
        _cmd_sender = CmdSender(args.drive_host, args.drive_port)
        cmd_sink = lambda c: _cmd_sender.send(c["linear_x"], c["angular_z"])
        print(f"[ok] DRIVE ON -> cmd_vel to {args.drive_host}:{args.drive_port} "
              f"(robot must run cmd_bridge)")

    from follower_BT.recovery import DrivePolicy   # IDLE/FOLLOWING/SEARCHING state machine
    policy = DrivePolicy(compute_cmd_vel)

    lidar_source = None
    if args.lidar_ros:
        try:
            from scripts.scan_ros_source import ScanRosSource
            lidar_source = ScanRosSource(topic=args.scan_topic, flip_180=args.lidar_flip)
            print(f"[ok] LiDAR view ON -> {args.scan_topic} via ROS2 "
                  f"(needs ROS sourced + matching ROS_DOMAIN_ID)")
        except Exception as e:
            print(f"[warn] --lidar-ros failed ({e}); continuing WITHOUT LiDAR display")

    if args.camera_label:
        perception.set_camera(args.camera_label)

    detection_sink = None
    if args.robot_host:
        detection_sink = _make_detection_sink(args.robot_host, args.robot_detection_port)

    # 로봇이 회복 중 앞↔뒤를 바꾸면 같은 포트로 다른 시점의 영상이 온다.
    # 그 전환을 알아야 추적 상태를 비울 수 있다.
    camera_source = None
    if args.camera_topic:
        try:
            from scripts.camera_select_source import CameraSelectSource
            camera_source = CameraSelectSource(topic=args.camera_topic)
            print(f"[ok] camera_select 구독 → {args.camera_topic} "
                  f"(ROS sourced + 같은 ROS_DOMAIN_ID 필요)")
        except Exception as e:      # noqa: BLE001 — 구독 못 해도 추론은 돌아야 한다
            print(f"[warn] camera_select 구독 실패({e}) — 카메라 전환을 모른 채 돕니다")

    # 자세(pose)는 **추종 전용**이다. 길잡이에서 끄려면 역할을 알아야 한다
    # (scripts/role_source.py 머리말). camera_select 와 같은 ROS 옵트인에 얹는다.
    role_source = None
    if args.camera_topic:
        try:
            from scripts.role_source import RoleSource
            role_source = RoleSource(topic=args.role_topic)
            print(f"[ok] perception_role 구독 → {args.role_topic} "
                  f"(길잡이에서 자세·골격 끔)")
        except Exception as e:      # noqa: BLE001 — 구독 못 해도 추론은 돌아야 한다
            print(f"[warn] perception_role 구독 실패({e}) — 길잡이에서도 자세를 잽니다")

    if args.show:
        _run_local_show(frames, perception, cmd_sink=cmd_sink, policy=policy,
                        detection_sink=detection_sink, camera_source=camera_source,
                        role_source=role_source)
        return

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 127.0.0.1 로 묶으면 같은 머신의 뷰어만 붙을 수 있다. 로봇 터치패널(libi_gui)은 다른
    # 머신이라 그러면 아예 연결이 안 된다. 필요하면 --bind 로 좁힐 수 있게 열어둔다.
    srv.bind((args.bind, args.port))
    srv.listen(1)
    print(f"[ok] perception server on {args.bind}:{args.port} "
          f"({'test-pattern' if args.test_pattern else f'camera {args.camera}'}); "
          f"waiting for viewer…")
    while True:
        conn, addr = srv.accept()
        print(f"[ok] viewer connected: {addr}")
        try:
            serve_loop(conn, frames, perception, poll_cmd=make_socket_poller(),
                       cmd_sink=cmd_sink, policy=policy, lidar_source=lidar_source,
                       detection_sink=detection_sink, camera_source=camera_source,
                       role_source=role_source)
        finally:
            conn.close()
            print("[..] viewer disconnected; waiting again")
        if not args.test_pattern:
            # 제너레이터는 닫히면 소진된다 — 다음 뷰어를 위해 **같은 종류로** 다시 만든다.
            # `_camera_frames()` 를 직접 부르면 UDP 모드가 무시된다(new_frames 주석).
            frames = new_frames()


if __name__ == "__main__":
    main()
