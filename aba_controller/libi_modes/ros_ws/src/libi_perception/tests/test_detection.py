from libi_perception.detection import Detection, detection_from_dict


def _dict(**over):
    base = dict(cx=320.0, cy=240.0, area=400.0, bbox=[0, 0, 20, 20],
                track_id=1, is_owner=True, confidence=0.9, is_predicted=False)
    base.update(over)
    return base


def test_from_dict_none():
    assert detection_from_dict(None) is None


def test_from_dict_maps_fields():
    d = detection_from_dict(_dict())
    assert isinstance(d, Detection)
    assert d.cx == 320.0
    assert d.area == 400.0
    assert d.bbox == (0, 0, 20, 20)
    assert d.is_owner is True
    assert d.is_predicted is False


# ── 신규 필드 (자세·주행가부·카메라·epoch) ────────────────────────────────────
# 전부 optional 이다. 옛 payload 를 보내는 소스가 남아 있어도 계약이 깨지면 안 된다.

def test_new_fields_default_when_absent():
    d = detection_from_dict(_dict())
    assert d.posture is None
    assert d.motion_ok is True        # 모른다 != 가지 마라
    assert d.camera is None
    assert d.camera_epoch == 0


def test_new_fields_read_when_present():
    d = detection_from_dict(_dict(posture="Lying", motion_ok=False,
                                  camera="front", camera_epoch=3))
    assert d.posture == "Lying"
    assert d.motion_ok is False
    assert d.camera == "front"
    assert d.camera_epoch == 3


def test_motion_ok_false_is_not_swallowed_by_default():
    """False 를 'falsy 니까 기본값' 으로 처리하면 정지 신호가 사라진다."""
    assert detection_from_dict(_dict(motion_ok=False)).motion_ok is False


def test_camera_epoch_none_becomes_zero():
    assert detection_from_dict(_dict(camera_epoch=None)).camera_epoch == 0
