"""가장 큰 사람 크기 — 등록 대상 매칭과 독립이어야 한다."""
from libi_perception.detection import Detection, detection_from_dict
from libi_perception.detection_receiver import DetectionReceiver


def _d(**over):
    kw = dict(cx=0.0, cy=0.0, area=100.0, bbox=(0, 0, 10, 10), track_id=1,
              is_owner=True, confidence=0.9, is_predicted=False)
    kw.update(over)
    return Detection(**kw)


def test_default_is_zero_meaning_unknown():
    assert _d().front_person_size == 0.0


def test_field_survives_the_dict_round_trip():
    d = detection_from_dict({
        "cx": 1.0, "cy": 2.0, "area": 30.0, "bbox": [0, 0, 3, 10],
        "track_id": 5, "is_owner": False, "confidence": 0.5,
        "is_predicted": False, "front_person_size": 214.5,
    })
    assert d.front_person_size == 214.5


def test_old_payload_without_the_field_is_zero():
    d = detection_from_dict({
        "cx": 1.0, "cy": 2.0, "area": 30.0, "bbox": [0, 0, 3, 10],
        "track_id": 5, "is_owner": False, "confidence": 0.5,
        "is_predicted": False,
    })
    assert d.front_person_size == 0.0


def test_none_payload_is_none():
    assert detection_from_dict(None) is None


# ── [2026-08-03] fix round 1 — owner 없어도 크기는 흘러야 한다 ─────────────────
#
# 배달 주행(`navigate` 다리) 중에는 등록된 추종 대상이 애초에 없다. owner 가 없다고
# `detection_to_dict` 가 통째로 `None`(JSON null)을 돌려주면 `front_person_size` 가
# 실릴 자리가 없어 `PersonBlockGuard` 가 주행 내내 0.0 만 본다 — 이 기능을 통째로
# 죽이는 결함이었다. `cx` 가 없는 dict(`{"front_person_size": ...}`)로 크기만 태운다.

def test_owner_less_payload_is_not_a_detection():
    """`cx` 가 없으면 owner 검출이 아니다 — KeyError 로 안 죽고 None."""
    assert detection_from_dict({"front_person_size": 214.5}) is None


class _Source:
    """`DetectionReceiver` 가 기대하는 최소 표면 — 배치 목록을 하나씩 poll() 로 낸다."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.i = 0

    def poll(self):
        out = self.batches[self.i] if self.i < len(self.batches) else []
        self.i += 1
        return out


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_receiver_keeps_the_size_when_there_is_no_owner():
    """owner 없는 payload(`{"front_person_size": ...}` 만)여도 크기는 산다."""
    r = DetectionReceiver(_Source([[{"front_person_size": 214.5}]]))
    r.update()
    assert r.latest() is None
    assert r.front_person_size() == 214.5


def test_receiver_reports_zero_for_a_null_payload():
    r = DetectionReceiver(_Source([[None]]))
    r.update()
    assert r.latest() is None
    assert r.front_person_size() == 0.0


def test_size_goes_stale_with_the_same_ttl():
    """`latest()` 와 같은 TTL 규칙 — 소스가 끊기면 모르는 것을 안다고 하지 않는다."""
    clock = _Clock()
    r = DetectionReceiver(_Source([[{"front_person_size": 214.5}]]), ttl_sec=1.0, now=clock)
    r.update()
    assert r.front_person_size() == 214.5

    clock.t = 1.1
    assert r.front_person_size() == 0.0, "TTL 초과 — 유령 크기를 쫓지 않는다"


def test_owner_payload_still_carries_the_size():
    """owner 가 있을 때도 같은 슬롯에서 같은 값이 읽혀야 한다."""
    payload = {"cx": 1.0, "cy": 2.0, "area": 30.0, "bbox": [0, 0, 3, 10],
              "track_id": 5, "is_owner": True, "confidence": 0.5,
              "is_predicted": False, "front_person_size": 300.0}
    r = DetectionReceiver(_Source([[payload]]))
    r.update()
    assert r.latest().front_person_size == 300.0
    assert r.front_person_size() == 300.0
