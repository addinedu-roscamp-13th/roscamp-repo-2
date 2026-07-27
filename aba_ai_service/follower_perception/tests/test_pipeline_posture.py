"""파이프라인에 얹은 자세 게이트·소실방향 게이트·카메라 epoch.

기존 `test_pipeline.py` 는 검출·재식별 경로를 본다. 여기는 그 위에 새로 얹은
"가도 되나" 판단만 본다.
"""
from follower_perception.detection import TrackedBox
from follower_perception.pipeline import FollowerPerception

W, H = 640, 480


class _Frame:
    """`frame.shape[:2]` 만 쓰이므로 진짜 이미지가 필요 없다."""
    shape = (H, W, 3)


FRAME = _Frame()


def _box(track_id=1, cx=320.0, cy=240.0, area=10000.0, bbox=(280, 140, 360, 340)):
    return TrackedBox(bbox=bbox, cx=cx, cy=cy, area=area,
                      track_id=track_id, confidence=0.9)


class FakeDetector:
    def __init__(self):
        self.next = [_box()]

    def detect(self, frame):
        return list(self.next)


class FakeMatcher:
    """owner 를 항상 첫 후보로 잡는 대역. 재식별 로직은 여기 관심사가 아니다."""

    def __init__(self):
        self.safe_id = None
        self.registered = 0

    def match(self, cands, frame):
        return cands[0].track_id if cands else None

    def calibrate(self, crop):
        pass

    def register(self, roi):
        self.registered += 1

    def reset(self):
        self.safe_id = None


class FakePose:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0
        self.recalibrated = 0

    def classify(self, frame, bbox):
        v = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        return v

    def recalibrate(self):
        self.recalibrated += 1
        self.calls = 0


def _pipeline(pose=None):
    p = FollowerPerception(detector=FakeDetector(), reid=object(), pose=pose)
    p.matcher = FakeMatcher()
    return p


# ── 자세 게이트 ──────────────────────────────────────────────────────────────

def test_posture_reaches_detection():
    p = _pipeline(pose=FakePose(["Standing"]))
    p.run(FRAME)
    det = p.get_latest()
    assert det.posture == "Standing" and det.motion_ok is True


def test_lying_stops_motion_but_keeps_detection():
    """누워 있어도 '보이는' 것은 맞다 — 검출은 유지하고 주행만 막는다.
    검출을 없애면 회복 BT 가 '놓쳤다' 로 오해해 탐색 회전을 시작한다."""
    p = _pipeline(pose=FakePose(["Standing", "Lying"]))
    p.run(FRAME)
    p.run(FRAME)
    det = p.get_latest()
    assert det is not None
    assert det.motion_ok is False


def test_no_pose_estimator_leaves_motion_allowed():
    """자세 모델 없는 배포에서 추종이 통째로 죽으면 안 된다."""
    p = _pipeline(pose=None)
    p.run(FRAME)
    det = p.get_latest()
    assert det.posture is None and det.motion_ok is True


def test_registration_recalibrates_pose():
    """등록 시 기준 비율을 다시 잰다 — 카메라 높이·거리가 매번 다르기 때문이다."""
    import numpy as np
    pose = FakePose(["Standing"])
    p = _pipeline(pose=pose)
    p.register_from_image(np.zeros((H, W, 3), dtype=np.uint8))   # _crop 이 진짜 배열을 쓴다
    assert pose.recalibrated == 1


def test_registration_resets_posture_gate():
    """직전 대상이 Lying 이라 막혀 있던 상태가 새 등록으로 넘어오면 안 된다."""
    import numpy as np
    pose = FakePose(["Lying"])
    p = _pipeline(pose=pose)
    p.run(FRAME)
    assert p.get_latest().motion_ok is False
    p.register_from_image(np.zeros((H, W, 3), dtype=np.uint8))
    assert p.posture_gate.allowed is True


# ── 소실 방향 게이트 ────────────────────────────────────────────────────────

def _lose_after(p, bbox, vel):
    """bbox 자리에서 보이다가 사라지게 만든다. 스무더 속도를 직접 세팅한다."""
    p.detector.next = [_box(bbox=bbox)]
    p.run(FRAME)
    p.smoother.velocity[:] = vel
    p.detector.next = []
    p.run(FRAME)


def test_side_exit_still_coasts():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(0, 100, 40, 400), vel=[-50.0, 0.0, 0.0])
    assert p.get_latest() is not None


def test_down_exit_stops_coasting_immediately():
    """COAST_LIMIT 이 남아 있어도 아래로 사라졌으면 따라가지 않는다."""
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, 60.0, 0.0])
    assert p.get_latest() is None


def test_up_exit_stops_coasting():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 0, 400, 200), vel=[0.0, -60.0, 0.0])
    assert p.get_latest() is None


def test_center_exit_coasts():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(280, 200, 360, 300), vel=[2.0, 1.0, 0.0])
    assert p.get_latest() is not None


def test_lying_before_loss_blocks_coast_regardless_of_direction():
    p = _pipeline(pose=FakePose(["Lying"]))
    _lose_after(p, bbox=(0, 100, 40, 400), vel=[-50.0, 0.0, 0.0])
    assert p.get_latest() is None


def test_exit_direction_latched_at_first_miss():
    """이후 프레임에서 다시 분류하면 예측이 흘러가며 판정이 뒤집힌다."""
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, 60.0, 0.0])
    for _ in range(5):
        p.run(FRAME)
    assert p.get_latest() is None       # 계속 막혀 있어야 한다


def test_reacquire_clears_exit_direction():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, 60.0, 0.0])
    p.detector.next = [_box()]
    p.run(FRAME)
    assert p.get_latest() is not None


# ── 카메라 전환 ──────────────────────────────────────────────────────────────

def test_camera_epoch_increments_on_switch():
    p = _pipeline(pose=FakePose(["Standing"]))
    p.set_camera("front")
    p.run(FRAME)
    first = p.get_latest().camera_epoch
    p.set_camera("back")
    p.run(FRAME)
    det = p.get_latest()
    assert det.camera == "back"
    assert det.camera_epoch > first


def test_same_camera_does_not_bump_epoch():
    p = _pipeline(pose=FakePose(["Standing"]))
    p.set_camera("front")
    p.run(FRAME)
    e = p.get_latest().camera_epoch
    p.set_camera("front")
    p.run(FRAME)
    assert p.get_latest().camera_epoch == e


def test_switch_clears_track_lock_but_keeps_template():
    """전환 때 matcher.reset() 을 부르면 등록한 사람을 잊는다 — 그러면 안 된다."""
    p = _pipeline(pose=FakePose(["Standing"]))
    p.matcher.safe_id = 7
    p.matcher.registered = 1
    p.set_camera("back")
    assert p.matcher.safe_id is None
    assert p.matcher.registered == 1        # 템플릿은 그대로
