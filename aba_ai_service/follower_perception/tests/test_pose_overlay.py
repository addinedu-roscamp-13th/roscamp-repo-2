"""자세를 화면에 그린다 — 스켈레톤 + 서있음/누워있음 판정.

판정만 글자로 띄우면 "왜 Unknown 인지"를 알 수 없다. 골격을 같이 그리면 점이 몇 개나
잡혔는지 눈에 보여, 모델이 못 본 건지 임계가 센 건지 화면에서 갈린다.

여기서 지키는 것은 **좌표계 하나**다. `PoseEstimator` 는 owner bbox crop 에만 pose 를
돌리므로 키포인트가 crop 기준이다(판정은 상대 기하만 봐서 되돌릴 필요가 없었다).
그리려면 bbox 원점을 더해야 하는데, 안 더해도 **에러가 안 난다** — 골격이 화면 왼쪽 위에
얌전히 그려질 뿐이다. 그 조용한 어긋남을 잡으려고 이 파일이 있다.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.perception_server import _draw_skeleton  # noqa: E402

L_SH, R_SH = 5, 6


def _canvas():
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _two_shoulders(conf_l=1.0, conf_r=1.0):
    """crop 좌표로 어깨 두 점만. (10,10) 과 (60,10)."""
    xy = np.zeros((17, 2), dtype=float)
    xy[L_SH] = (10.0, 10.0)
    xy[R_SH] = (60.0, 10.0)
    conf = np.zeros(17, dtype=float)
    conf[L_SH], conf[R_SH] = conf_l, conf_r
    return xy, conf


GREEN = (0, 255, 0)


def _painted(img, x, y):
    return tuple(int(v) for v in img[y, x]) != (0, 0, 0)


def test_keypoints_land_at_the_bbox_origin_not_the_frame_origin():
    """이 파일의 존재 이유. 원점을 안 더하면 골격이 화면 왼쪽 위에 그려진다."""
    vis = _canvas()
    xy, conf = _two_shoulders()
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    assert _painted(vis, 210, 110), "왼쪽 어깨가 bbox 원점만큼 안 옮겨졌다"
    assert _painted(vis, 260, 110), "오른쪽 어깨가 bbox 원점만큼 안 옮겨졌다"
    assert not _painted(vis, 10, 10), "crop 좌표 그대로 그렸다 — 원점을 안 더했다"


def test_low_confidence_points_are_not_drawn():
    """판정이 안 쓰는 점을 화면에만 그리면, Unknown 인데 골격은 멀쩡해 보인다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=1.0, conf_r=0.1)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    assert _painted(vis, 210, 110), "믿을 만한 점은 그려야 한다"
    assert not _painted(vis, 260, 110), "신뢰도가 낮은 점을 그렸다"


def test_an_edge_needs_both_ends():
    """한쪽 끝이 없는 선을 그리면 없는 관절이 있는 것처럼 보인다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=1.0, conf_r=0.1)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    # 두 어깨의 중간점 — 선을 그렸다면 여기가 칠해진다.
    assert not _painted(vis, 235, 110), "끝점 하나가 없는데 어깨선을 그렸다"


def test_drawing_nothing_is_not_an_error():
    """전부 신뢰도 미달이어도 죽지 않아야 한다 — 영상이 통째로 멈추는 것보다 낫다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=0.0, conf_r=0.0)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)
    assert vis.sum() == 0


L_HIP, R_HIP, L_EL, L_KNEE, R_KNEE = 11, 12, 7, 13, 14


def _torso4(conf=1.0):
    xy = np.zeros((17, 2), dtype=float)
    confs = np.zeros(17, dtype=float)
    for i, p in ((L_SH, (10.0, 10.0)), (R_SH, (60.0, 10.0)),
                 (L_HIP, (10.0, 110.0)), (R_HIP, (60.0, 110.0)),
                 (L_EL, (0.0, 60.0)), (L_KNEE, (10.0, 210.0)), (R_KNEE, (60.0, 210.0))):
        xy[i] = p
        confs[i] = conf
    return xy, confs


def test_limbs_are_not_drawn():
    """판정이 안 쓰는 점을 그리면 화면과 판정이 다른 이야기를 한다."""
    vis = _canvas()
    xy, conf = _torso4()
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)
    assert not _painted(vis, 200, 160), "팔꿈치가 그려졌다"


def test_torso_four_points_are_drawn():
    vis = _canvas()
    xy, conf = _torso4()
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)
    for x, y in ((210, 110), (260, 110), (210, 210), (260, 210)):
        assert _painted(vis, x, y), f"({x},{y}) 가 안 그려졌다"


def test_knees_are_drawn_only_for_the_shoulder_knee_axis():
    vis = _canvas()
    xy, conf = _torso4()
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN, axis="torso")
    assert not _painted(vis, 210, 310), "torso 축인데 무릎이 그려졌다"

    vis2 = _canvas()
    _draw_skeleton(vis2, (xy, conf, (200, 100)), 0.5, GREEN, axis="shoulder_knee")
    assert _painted(vis2, 210, 310), "shoulder_knee 축인데 무릎이 안 그려졌다"


def test_side_has_its_own_colour():
    from scripts.perception_server import _POSTURE_COLORS
    assert "Side" in _POSTURE_COLORS
    assert _POSTURE_COLORS["Side"] != _POSTURE_COLORS["Lying"]
    assert _POSTURE_COLORS["Side"] != _POSTURE_COLORS["Standing"]


def test_calibration_countdown_is_drawn():
    from scripts.perception_server import draw_overlay

    class _Pose:
        last_keypoints = None
        conf_min = 0.5
        calibrating = True
        calibration_progress = (30, 60)

        def calibration_remaining_sec(self, fps):
            return (60 - 30) / fps

    class _Det:
        bbox = (10, 10, 60, 200)
        is_owner = True
        is_predicted = False
        posture = "Calibrating"
        motion_ok = False

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    out = draw_overlay(frame, _Det(), pose=_Pose(), fps=15.0)
    assert out.shape == frame.shape
    assert out.any(), "아무것도 안 그려졌다"


def test_countdown_is_skipped_when_calibration_is_done():
    """끝났는데 0.0초가 계속 떠 있으면 안 된다."""
    from scripts.perception_server import _calibration_text

    class _Pose:
        calibrating = False
        calibration_progress = (60, 60)

        def calibration_remaining_sec(self, fps):
            return 0.0

    assert _calibration_text(_Pose(), 15.0) is None


def test_countdown_text_uses_measured_fps():
    from scripts.perception_server import _calibration_text

    class _Pose:
        calibrating = True
        calibration_progress = (30, 60)

        def calibration_remaining_sec(self, fps):
            return (60 - 30) / fps

    assert "2.0" in _calibration_text(_Pose(), 15.0)
    assert "4.0" in _calibration_text(_Pose(), 7.5)


# ── draw_overlay 가 실제로 쓰인 축을 그리는가 ──────────────────────────────────
#
# `_draw_skeleton` 자체는 axis= 를 받으면 맞게 그린다(위 test_knees_are_drawn_only_for_
# the_shoulder_knee_axis 가 이미 증명했다). 그런데 `draw_overlay`(런타임 경로)가
# `pose.last_axis` 를 안 읽고 넘기면, 판정은 shoulder_knee 인데 화면은 항상 torso 축을
# 그려 화면과 판정이 다른 이야기를 한다 — 이 구멍을 잡는다.
#
# owner bbox 를 (10,10)~(60,200) 에, 키포인트 원점을 (400,50) 에 둔다 — 무릎이 찍힐
# (410,260)/(460,260) 이 owner 사각형 테두리·HUD 글자·삼분할 안내선(x=213/426) 과
# 전혀 안 겹쳐야, "그 자리가 칠해졌다"가 곧 "무릎이 그려졌다"를 뜻한다.

_OVERLAY_ORIGIN = (400, 50)
_KNEE_PROBE = (410, 260)   # L_KNEE(크롭 10,210) + 원점(400,50)


class _OverlayDet:
    bbox = (10, 10, 60, 200)
    is_owner = True
    is_predicted = False
    posture = "Standing"
    motion_ok = True


def test_draw_overlay_draws_knees_when_last_axis_is_shoulder_knee():
    from scripts.perception_server import draw_overlay

    xy, conf = _torso4()

    class _Pose:
        last_keypoints = (xy, conf, _OVERLAY_ORIGIN)
        conf_min = 0.5
        calibrating = False
        last_axis = "shoulder_knee"

    out = draw_overlay(_canvas(), _OverlayDet(), pose=_Pose())
    assert _painted(out, *_KNEE_PROBE), \
        "판정이 shoulder_knee 축인데 draw_overlay 가 무릎을 안 그렸다"


def test_draw_overlay_does_not_draw_knees_when_last_axis_is_torso():
    from scripts.perception_server import draw_overlay

    xy, conf = _torso4()

    class _Pose:
        last_keypoints = (xy, conf, _OVERLAY_ORIGIN)
        conf_min = 0.5
        calibrating = False
        last_axis = "torso"

    out = draw_overlay(_canvas(), _OverlayDet(), pose=_Pose())
    assert not _painted(out, *_KNEE_PROBE), \
        "판정이 torso 축인데 draw_overlay 가 무릎을 그렸다"


def test_draw_overlay_falls_back_to_torso_when_pose_has_no_last_axis():
    """구버전 `pose`(아직 `last_axis` 를 안 가짐)를 줘도 죽지 않고 torso 로 대체돼야 한다."""
    from scripts.perception_server import draw_overlay

    xy, conf = _torso4()

    class _Pose:
        last_keypoints = (xy, conf, _OVERLAY_ORIGIN)
        conf_min = 0.5
        calibrating = False
        # last_axis 없음 — 일부러(구버전 pose 객체 흉내)

    out = draw_overlay(_canvas(), _OverlayDet(), pose=_Pose())   # 예외 없이 통과해야 한다
    assert not _painted(out, *_KNEE_PROBE), "축을 모르면 torso 로 대체돼(무릎 없이) 그려야 한다"
