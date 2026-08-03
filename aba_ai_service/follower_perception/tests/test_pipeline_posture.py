"""파이프라인에 얹은 자세 게이트·소실방향 게이트·카메라 epoch.

기존 `test_pipeline.py` 는 검출·재식별 경로를 본다. 여기는 그 위에 새로 얹은
"가도 되나" 판단만 본다.
"""
from follower_perception.constants import FRAME_DT, REGISTRATION_LEARN_SEC
from follower_perception.detection import TrackedBox
from follower_perception.pipeline import FollowerPerception

W, H = 640, 480


class _Frame:
    """`frame.shape[:2]` 와 **crop 슬라이싱**만 쓰인다.

    ⚠️ [2026-08-02] 예전에는 `shape` 만 있으면 됐다. 갤러리 온라인 학습이
    `CALIBRATION_INTERVAL`(15프레임)마다만 돌아 이 스텁이 crop 경로를 안 탔기 때문이다.
    등록 직후 **매 프레임** 학습하도록 바뀌면서(`REGISTRATION_LEARN_SEC`) 그 경로를
    타게 됐고, `TypeError: '_Frame' object is not subscriptable` 로 드러났다.
    슬라이싱을 받아 자기 자신을 돌려준다 — 크기만 맞으면 되는 스텁이라 내용은 무의미하다.
    """
    shape = (H, W, 3)

    def __getitem__(self, _sl):
        return self


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


#: 프레임당 값 → velocity 단위(px/초). 임계는 프레임당인데 velocity 는 초당이라
#  그냥 넣으면 20배 예민한 옛 버그를 그대로 못박게 된다(2026-08-02 단위 수정).
def _pf(v):
    from follower_perception.constants import FRAME_DT
    return v / FRAME_DT


def test_side_exit_still_coasts():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(0, 100, 40, 400), vel=[-50.0, 0.0, 0.0])
    assert p.get_latest() is not None


def test_down_exit_stops_coasting_immediately():
    """COAST_LIMIT 이 남아 있어도 아래로 사라졌으면 따라가지 않는다."""
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, _pf(15.0), 0.0])
    assert p.get_latest() is None


def test_up_exit_stops_coasting():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 0, 400, 200), vel=[0.0, _pf(-15.0), 0.0])
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
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, _pf(15.0), 0.0])
    for _ in range(5):
        p.run(FRAME)
    assert p.get_latest() is None       # 계속 막혀 있어야 한다


def test_reacquire_clears_exit_direction():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(200, 200, 400, 479), vel=[0.0, _pf(15.0), 0.0])
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


# ── 코스팅은 마지막 실제 판정을 물려받는다 (2026-08-02) ──────────────────────
# 사용자 규칙: "마지막이 정상이었으면 가고, 아니면 말고."
# 코스팅이 권한을 **올려주면** 안 된다 — 안 보이는 동안 새로 판단할 근거가 없다.

def test_coasting_inherits_a_normal_posture_as_movable():
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(280, 200, 360, 300), vel=[2.0, 1.0, 0.0])
    d = p.get_latest()
    assert d is not None and d.is_predicted is True
    assert d.motion_ok is True, "마지막이 Standing 인데 못 가게 했다"


def test_coasting_inherits_a_side_posture_as_blocked():
    """`Side` 는 `_STOP_NOW` 라 allowed=False — 로봇은 전진만 막고 방위는 살린다."""
    p = _pipeline(pose=FakePose(["Side"]))
    _lose_after(p, bbox=(280, 200, 360, 300), vel=[2.0, 1.0, 0.0])
    d = p.get_latest()
    assert d is not None, "Side 는 코스팅 자체는 허용된다(may_coast 는 Lying 만 막는다)"
    assert d.motion_ok is False, "코스팅이 권한을 올려 줬다"
    assert d.posture == "Side", "로봇이 '전진만 차단'을 고르려면 자세가 실려야 한다"


def test_coasting_does_not_upgrade_an_unknown_stop():
    """Unknown 이 쌓여 이미 정지 판정이면 코스팅 중에도 정지다."""
    from follower_perception.constants import UNKNOWN_STOP_FRAMES
    p = _pipeline(pose=FakePose(["Standing"]))
    _lose_after(p, bbox=(280, 200, 360, 300), vel=[2.0, 1.0, 0.0])
    # 게이트를 공개 API 로 정지 상태까지 몬다. 프레임을 25장 흘리면 주기적
    # 캘리브레이션(`CALIBRATION_INTERVAL`)이 진짜 이미지를 요구해 여기서는 못 쓴다.
    for _ in range(UNKNOWN_STOP_FRAMES):
        p.posture_gate.update("Unknown")
    assert p.posture_gate.allowed is False
    d = p.get_latest()
    assert d is not None and d.is_predicted is True
    assert d.motion_ok is False, "정지 판정이 코스팅에서 뒤집혔다"


# ── 등록 직후·카메라 전환 직후 집중 학습 (2026-08-02) ────────────────────────
#
# `register()` 는 한 장으로 템플릿을 만든다. 그 뒤 갤러리는 CALIBRATION_INTERVAL
# (15프레임 ≈ 0.9초)마다만 자라서, 등록하자마자 카메라가 뒤로 바뀌면 유사도가
# REID_THRESHOLD 를 못 넘어 owner 가 안 잡힌다 → 코스팅(주황 박스) → `requester_visible`
# 이 예측을 걸러 **길잡이가 영영 출발하지 못한다**(실측 2026-08-02: 뒷캠에 노란 박스만
# 나와 테스트 자체가 안 됐다).

def test_camera_switch_reopens_the_learning_window():
    """전환 직후에는 그 시점을 새로 배워야 한다 — 안 그러면 뒷캠에서 못 알아본다."""
    p = _pipeline()
    p._learn_frames = 0
    p.set_camera("back")
    assert p._learn_frames == int(REGISTRATION_LEARN_SEC / FRAME_DT), \
        "카메라를 바꿨는데 학습 창이 안 열렸다 — 뒷캠 시점을 영영 못 배운다"


def test_learning_window_is_seconds_not_one_frame():
    n = int(REGISTRATION_LEARN_SEC / FRAME_DT)
    assert n >= 30, f"학습 창이 {n}프레임뿐 — 한 장짜리 템플릿과 다를 게 없다"


def test_learning_window_calibrates_every_frame():
    """창이 열려 있는 동안은 **매 프레임** 채운다. 15프레임 주기로는 너무 느리다."""
    p = _pipeline()
    calls = []
    p.matcher.calibrate = lambda roi: calls.append(roi)
    p._learn_frames = 5
    for _ in range(5):
        p.run(FRAME)
    assert len(calls) == 5, f"학습 창인데 {len(calls)}회만 채웠다"
    assert p._learn_frames == 0, "창이 안 닫혔다"


def test_after_the_window_falls_back_to_the_normal_interval():
    p = _pipeline()
    calls = []
    p.matcher.calibrate = lambda roi: calls.append(roi)
    p._learn_frames = 0
    p._frame_count = 1                      # 15의 배수를 피한다
    for _ in range(5):
        p.run(FRAME)
    assert len(calls) <= 1, f"창이 닫혔는데 매 프레임 채웠다({len(calls)}회)"


# ── 역할 게이트: 자세는 **추종 전용** ────────────────────────────────────────
#
# 실기 증상(2026-08-02): 길잡이 중인데 영상에 pose 골격이 그려졌다.
# 골격은 AI 서버가 JPEG 에 굽고 패널은 끌 수 없으므로, 안 재는 것이 유일한 방법이다.
# 로봇이 `/libi/perception_role` 로 알려주는 역할이 그 근거다.

def test_guide_role_skips_pose_estimation():
    pose = FakePose(["Standing"])
    p = _pipeline(pose=pose)
    p.set_role("guide")
    p.run(FRAME)
    assert pose.calls == 0, "길잡이인데 자세를 쟀다"
    assert p.pose_active is False
    det = p.get_latest()
    assert det.posture is None
    assert det.motion_ok is True, "안 재는 값으로 주행을 막으면 안 된다"


def test_follow_role_keeps_pose_estimation():
    pose = FakePose(["Standing"])
    p = _pipeline(pose=pose)
    p.set_role("follow")
    p.run(FRAME)
    assert pose.calls == 1
    assert p.pose_active is True
    assert p.get_latest().posture == "Standing"


def test_unknown_role_keeps_pose_estimation():
    """역할을 **한 번도 못 받은** 배포(ROS 옵트인 없음)에서는 예전대로 켜져 있어야 한다.
    모름을 '길잡이 아님' 이 아니라 '끄지 않음' 으로 다룬다."""
    pose = FakePose(["Standing"])
    p = _pipeline(pose=pose)          # set_role 을 아예 안 부른다
    p.run(FRAME)
    assert pose.calls == 1 and p.pose_active is True


def test_switching_to_guide_releases_a_blocked_posture_gate():
    """추종에서 자세로 막힌 채 길잡이로 넘어가면 그 정지가 굳으면 안 된다.

    카메라가 안 바뀌는 전환(watch→guide, 둘 다 뒷캠)에서는 `set_camera` 의 reset 이
    안 돌아 아무도 안 풀어 준다.
    """
    p = _pipeline(pose=FakePose(["Standing", "Lying"]))
    p.set_role("follow")
    p.run(FRAME); p.run(FRAME)
    assert p.get_latest().motion_ok is False
    p.set_role("guide")
    p.run(FRAME)
    assert p.get_latest().motion_ok is True


def test_guide_does_not_recalibrate_pose_on_registration():
    """길잡이 등록에서 기준을 다시 재면 'Calibrating' 이 몇 초 뜨고 골격이 노랗게 나온다."""
    pose = FakePose(["Standing"])
    p = _pipeline(pose=pose)
    p.set_role("guide")
    p._on_registered()
    assert pose.recalibrated == 0
    p.set_role("follow")
    p._on_registered()
    assert pose.recalibrated == 1
