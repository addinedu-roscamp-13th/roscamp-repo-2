"""길잡이 가시성은 '이상 상황' 만 봐야 한다.

측면(Side)은 이상이 아니다 — 요청자가 서가를 보며 따라오는 것은 정상이고
흔하다. 주행 가부(motion_ok)를 그대로 쓰면 옆을 볼 때마다 "놓쳤다" 가 발행돼
미션 BT 가 멈추거나 회복에 들어간다.
"""
from libi_perception.detection import Detection


def _det(posture, motion_ok, is_predicted=False):
    return Detection(cx=100.0, cy=100.0, area=1000.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9,
                     is_predicted=is_predicted, posture=posture, motion_ok=motion_ok)


def _visible(det):
    from libi_perception.follow_node import requester_visible
    return requester_visible(det)


def test_side_is_still_visible():
    """옆을 봐도 따라오고 있는 것이다 — 이것만 예전 동작에서 도려낸다."""
    assert _visible(_det("Side", motion_ok=False)) is True


def test_lying_is_not_visible():
    """쓰러졌으면 따라오고 있지 않다."""
    assert _visible(_det("Lying", motion_ok=False)) is False


def test_standing_is_visible():
    assert _visible(_det("Standing", motion_ok=True)) is True


def test_calibrating_follows_the_gate():
    """`Side` 외에는 예전 그대로 — 게이트가 막으면 안 보이는 것이다.

    ⚠️ 초판 계획은 `posture != "Lying"` 이라 Calibrating·Unknown·predicted 까지
    전부 visible 로 냈다. 특히 `Unknown` 은 지금 25프레임 뒤 invisible 이 되는데
    그 동작이 통째로 사라진다. 저 conf 로 누움이 `Unknown` 으로 나간 요청자를
    길잡이가 영영 정상으로 읽게 된다.
    """
    assert _visible(_det("Calibrating", motion_ok=False)) is False


def test_unknown_still_follows_the_gate():
    """게이트가 아직 허용 중이면 보이고, 25프레임을 넘겨 막으면 안 보인다."""
    assert _visible(_det("Unknown", motion_ok=True)) is True
    assert _visible(_det("Unknown", motion_ok=False)) is False


def test_no_posture_source_is_visible():
    """자세 모델 없는 배포 — 예전과 같이 동작한다."""
    assert _visible(_det(None, motion_ok=True)) is True


def test_unknown_posture_string_follows_the_gate():
    """모르는 문자열이 들어와도 게이트 판단을 따른다 — 허용으로 새지 않는다."""
    assert _visible(_det("Sideways", motion_ok=False)) is False


def test_none_detection_is_not_visible():
    assert _visible(None) is False


def test_non_owner_is_not_visible():
    d = _det("Standing", motion_ok=True)
    d.is_owner = False
    assert _visible(d) is False


def test_predicted_detection_is_not_visible():
    """α-β 예측 bbox 는 '보인다' 가 아니다.

    파이프라인은 검출이 끊겨도 COAST_LIMIT(30프레임 ≈ 2초) 동안 예측 위치를
    내보낸다. 안내의 요점은 **그 사람이 실제로 따라오는지 확인하는 것**이므로
    유령을 보고 전진하면 안 된다. 추종은 이 함수를 안 쓰므로 영향받지 않는다.
    """
    assert _visible(_det("Standing", motion_ok=True, is_predicted=True)) is False


def test_predicted_beats_every_other_signal():
    """자세가 완벽해도 예측이면 안 보이는 것이다 — 순서가 뒤바뀌면 안 된다."""
    assert _visible(_det("Side", motion_ok=False, is_predicted=True)) is False
