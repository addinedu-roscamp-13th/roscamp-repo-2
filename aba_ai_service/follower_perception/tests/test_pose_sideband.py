"""자세·비율 값을 **글자가 아니라 데이터로** 패널에 보낸다.

예전에는 STATE·POSTURE·cmd_vel 을 `draw_overlay` 가 JPEG 에 구워 보냈다. 그러면
패널에서 글꼴·색·자리를 못 고치고, 영상이 축소되면 글자가 같이 뭉개져 안 읽힌다.
지금은 같은 값을 `POSE <JSON>` 사이드밴드로 보내고 패널이 자기 스타일로 그린다.

여기서 지키는 계약은 둘이다:

  1. 패널로 가는 프레임에는 **구석 글자가 없다** — 대신 bbox·스켈레톤 같은
     좌표에 붙는 그림은 **그대로 있어야 한다**. 둘을 같이 지워버리는 회귀가
     제일 잦다.
  2. 값이 없으면 **없음(null)** 으로 간다. 0 으로 채우면 "아직 기준을 못 쟀다"와
     "비율이 0 이다"가 화면에서 구분되지 않는다.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.perception_server import _pose_payload, draw_overlay  # noqa: E402


class _Det:
    #: 왼쪽 위 구석(글자 자리)과 겹치지 않게 화면 가운데에 둔다 — 겹치면 "글자가
    #  빠졌나"를 재는 검사가 OWNER 라벨을 글자로 세어 헛집는다.
    def __init__(self, posture="Standing", motion_ok=True):
        self.bbox = (300, 200, 400, 400)
        self.posture = posture
        self.motion_ok = motion_ok
        self.is_owner = True
        self.is_predicted = False


class _Pose:
    """PoseEstimator 흉내. 화면이 읽는 속성만 갖춘다."""

    def __init__(self, ratio=2.0, ref=1.9, trip=3.04, calibrating=False):
        self.last_ratio = ratio
        self.ref_ratio = ref
        self.side_trip = trip
        self.last_axis = "torso"
        self.last_keypoints = None
        self.conf_min = 0.5
        self.calibrating = calibrating
        self.calibration_progress = (23, 60)

    def calibration_remaining_sec(self, fps):
        return 2.4


def _decode(payload):
    assert payload.startswith(b"POSE "), payload[:16]
    return json.loads(payload[len(b"POSE "):].decode("utf-8"))


# ── 1) 사이드밴드 내용 ──────────────────────────────────────────────────────

def test_payload_carries_the_three_ratio_numbers():
    """지금 값 / 기준 / 측면 판정선. 셋이 다 있어야 사람이 암산을 안 한다."""
    body = _decode(_pose_payload(_Det(), None, _Pose(), fps=15.0))
    assert body["ratio"] == 2.0
    assert body["refRatio"] == 1.9
    assert body["sideTrip"] == 3.04


def test_payload_names_the_axis_the_verdict_used():
    """비율만 보내면 "무엇 대비 비율인지"를 모른다 — 축이 바뀌면 값의 뜻도 바뀐다."""
    pose = _Pose()
    pose.last_axis = "head_hip"
    assert _decode(_pose_payload(_Det(), None, pose, 15.0))["axis"] == "head_hip"


def test_payload_reports_posture_and_the_drive_gate_separately():
    """자세와 주행은 별개다 — 직립이어도 게이트가 막을 수 있다."""
    body = _decode(_pose_payload(_Det("Side", motion_ok=False), None, _Pose(), 15.0))
    assert body["posture"] == "Side"
    assert body["motionOk"] is False


def test_missing_values_go_out_as_null_not_zero():
    """기준을 아직 못 쟀는데 0 으로 보내면 화면이 "비율 0.00" 이라고 거짓말한다."""
    body = _decode(_pose_payload(_Det(), None, _Pose(ratio=None, ref=None, trip=None), 15.0))
    assert body["ratio"] is None
    assert body["refRatio"] is None
    assert body["sideTrip"] is None


def test_non_finite_ratio_is_dropped():
    """폭이 0 이면 비율이 inf 가 된다. JSON 으로 그대로 내보내면 파서가 깨진다."""
    body = _decode(_pose_payload(_Det(), None, _Pose(ratio=float("inf")), 15.0))
    assert body["ratio"] is None
    json.dumps(body)        # 다시 직렬화해도 문제없어야 한다


def test_calibration_block_appears_only_while_measuring():
    measuring = _decode(_pose_payload(_Det(), None, _Pose(calibrating=True), 15.0))
    assert measuring["calibrating"] == {"remainingSec": 2.4, "got": 23, "need": 60}

    done = _decode(_pose_payload(_Det(), None, _Pose(calibrating=False), 15.0))
    assert "calibrating" not in done


def test_payload_survives_no_detection_and_no_pose():
    """등록 전에는 둘 다 없다. 그때도 패널은 프레임마다 값을 받아야 한다."""
    body = _decode(_pose_payload(None, None, None, fps=None))
    assert body["posture"] is None
    assert body["ratio"] is None


def test_cmd_fields_ride_along():
    cmd = {"state": "FOLLOWING", "linear_x": 0.08, "angular_z": -0.2,
           "drive": "fwd", "turn": "left"}
    body = _decode(_pose_payload(_Det(), cmd, _Pose(), 15.0))
    assert body["state"] == "FOLLOWING"
    assert body["linearX"] == 0.08
    assert body["angularZ"] == -0.2


# ── 2) 프레임에서 글자만 빠졌는지 ───────────────────────────────────────────

def _frame():
    return np.full((480, 640, 3), 40, dtype=np.uint8)


def _corner(img):
    """STATE·cmd_vel 이 찍히는 왼쪽 위. 가이드선(x=213)보다 왼쪽이라 안 겹친다."""
    return img[0:90, 0:150]


_CMD = {"state": "FOLLOWING", "linear_x": 0.08, "angular_z": 0.0,
        "drive": "fwd", "turn": "straight"}


def test_hud_text_off_leaves_the_corner_untouched():
    off = draw_overlay(_frame(), _Det(), cmd=_CMD, status_extra="등록됨",
                       hud_text=False)
    assert np.array_equal(_corner(off), _corner(_frame()))


def test_hud_text_on_still_writes_the_corner():
    """로컬 디버그 창은 기본값 그대로다 — 글자를 빼는 건 패널 쪽뿐이다."""
    on = draw_overlay(_frame(), _Det(), cmd=_CMD, status_extra="등록됨")
    assert not np.array_equal(_corner(on), _corner(_frame()))


def test_hud_text_off_still_draws_the_bbox():
    """글자와 같이 그림까지 지우면 패널에서 대상이 안 보인다."""
    off = draw_overlay(_frame(), _Det(), cmd=_CMD, hud_text=False)
    # bbox 는 (300,200)-(400,400). 위쪽 테두리가 원본과 달라야 한다.
    assert not np.array_equal(off[200, 300:400], _frame()[200, 300:400])
