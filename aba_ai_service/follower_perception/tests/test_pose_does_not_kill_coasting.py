"""`--pose` 를 켜도 주황 박스(코스팅)가 나온다 — 종단 확인.

## 이 파일이 막는 것

사용자 보고: "골격 없이 했을 땐 잘 되었는데, pose 를 켜니 주황 박스가 안 보인다."

사슬은 이랬다:

    --pose 켬
      → 등록 직후 자세 = "Calibrating"
      → `exit_direction._NO_COAST_POSTURES` 에 "Calibrating" 이 있다
      → `may_coast()` 가 False
      → `get_latest()` 가 코스팅 분기를 못 타고 **None** 을 낸다
      → 주황 박스가 아예 안 나오고, 놓치는 즉시 소실 처리된다

그리고 `Calibrating` 은 **몸통 키포인트 4점이 전부 conf ≥ 0.5 인 프레임 60장**이
모여야 풀린다. 역광·측면·먼 거리처럼 골격이 잘 안 잡히면 **영영 안 풀린다** —
빠져나갈 시간 제한이 없었다. `--pose` 를 끄면 자세가 None 이라 둘 다 안 걸린다.

여기서는 위 사슬을 **진짜 부품으로** 태운다 — 파이프라인·PoseEstimator·may_coast 를
전부 실물로 쓰고 YOLO 모델만 가짜다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from follower_perception import constants  # noqa: E402
from follower_perception.detection import TrackedBox  # noqa: E402
from follower_perception.mocks import MockDetector  # noqa: E402
from follower_perception.pipeline import FollowerPerception  # noqa: E402
from follower_perception.reid_engine import ReIDEngine  # noqa: E402

pose_estimator = pytest.importorskip(
    "follower_perception.pose_estimator",
    reason="ultralytics/yolo_pose 없는 환경에서는 건너뛴다")


def _frame(colour, w=320, h=240):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = colour
    return img


def _box(tid, cx, w=40, h=80):
    return TrackedBox(bbox=(cx - w / 2, 120 - h / 2, cx + w / 2, 120 + h / 2),
                      cx=float(cx), cy=120.0, area=float(w * h),
                      track_id=tid, confidence=0.9)


class _LowConfPose:
    """키포인트는 내지만 **신뢰도가 미달**인 모델.

    캘리브 표본에 한 장도 안 들어가는 조건이다 — 실기에서 역광·측면·먼 거리가
    이 상태다. (키포인트를 아예 못 내는 경우는 `_guard_only` 가 Unknown 을 내므로
    원래부터 코스팅을 안 막았다. 여기가 진짜로 막히던 경로다.)
    """

    class _KP:
        def __init__(self, xy, conf):
            self.xy = [np.asarray(xy, dtype=float)]
            self.conf = [np.asarray(conf, dtype=float)]

    class _Res:
        def __init__(self, kp):
            self.keypoints = kp

    def __call__(self, crop, verbose=False):
        xy = np.tile(np.array([20.0, 30.0]), (17, 1))
        return [self._Res(self._KP(xy, np.full(17, 0.1)))]


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(pose_estimator.time, "monotonic", c.monotonic)
    return c


def _pose_pipeline():
    """사람이 오른쪽으로 걸어가다 사라지는 대본 + **진짜** PoseEstimator."""
    n = constants.REGISTRATION_STABLE_FRAMES
    moving = [[_box(1, 80 + i * 6)] for i in range(n + 6)]
    script = [[_box(1, 160, w=200, h=200)]] * n + moving + [[]] * 30
    pose = pose_estimator.PoseEstimator(model=_LowConfPose(), every_n=1)
    p = FollowerPerception(detector=MockDetector(script),
                           reid=ReIDEngine(backend='colour'), pose=pose)
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    for _ in range(n + 6):
        p.run(red)
    return p, red, pose


def test_캘리브_중에_놓쳐도_주황_박스가_나온다(clock):
    """이 파일의 존재 이유.

    ⚠️ 제한 시간(`POSE_CALIBRATION_TIMEOUT_SEC`)만으로는 **안 풀린다.**
       `_last_posture` 는 owner 를 실제로 잡은 프레임에서만 갱신되므로, 사람을 놓친
       뒤에는 `classify` 가 아예 안 불리고 마지막 값 "Calibrating" 이 얼어붙는다.
       그래서 `Calibrating` 을 코스팅 차단목록에서 뺐다.
    """
    p, red, pose = _pose_pipeline()
    assert p.pose_active is True, "이 시험의 전제 — 자세 판정이 켜져 있다"

    p.run(red)                                  # 놓친 첫 프레임
    assert p._last_posture == "Calibrating", "이 시험의 전제 — 캘리브 중에 놓쳤다"

    det = p.get_latest()
    assert det is not None, "캘리브 중에 놓쳤다고 주황 박스가 사라진다(사용자 보고)"
    assert det.is_predicted is True

    # ⚠️ 그래도 **로봇은 안 간다** — 이게 차단목록에서 빼도 안전한 근거다.
    assert det.motion_ok is False, "코스팅이 주행 권한을 올려주면 안 된다"


def test_캘리브가_영영_안_끝나면_제한시간이_풀어준다(clock):
    """골격이 안 잡히면 `Calibrating` 이 무한히 이어져 **주행까지** 막힌다.

    코스팅은 위에서 살렸지만, `PostureGate` 는 `Calibrating` 인 동안 계속 정지시킨다.
    제한 시간이 그걸 `Unknown` 으로 풀어 준다 — 기준을 억지로 세우지는 않는다.
    """
    p, red, pose = _pose_pipeline()
    assert p._last_posture == "Calibrating"
    assert p.posture_gate.allowed is False, "캘리브 중에는 정지가 맞다"

    clock.t += constants.POSE_CALIBRATION_TIMEOUT_SEC + 1.0
    # 사람이 다시 보이는 동안 판정이 돌아야 제한 시간이 평가된다.
    # ⚠️ 대본 **끝**에 붙이면 안 된다 — 그 앞에 빈 프레임 30장이 있어 여기까지 안 온다.
    i = p.detector.i
    p.detector.script[i:i] = [[_box(1, 200)]] * 5   # 지금 자리에 끼워 넣는다
    for _ in range(5):
        p.run(red)

    assert p._last_posture != "Calibrating", "영영 Calibrating 이면 로봇이 영영 안 간다"
    assert pose.calibrating is False, "포기했으면 화면 카운트다운도 멈춰야 한다"


def test_pose_없이는_처음부터_나온다(clock):
    """대조군 — 골격을 끄면 예전처럼 바로 나온다. 원인이 pose 쪽임을 못 박는다."""
    n = constants.REGISTRATION_STABLE_FRAMES
    moving = [[_box(1, 80 + i * 6)] for i in range(n + 6)]
    script = [[_box(1, 160, w=200, h=200)]] * n + moving + [[]] * 30
    p = FollowerPerception(detector=MockDetector(script),
                           reid=ReIDEngine(backend='colour'))       # pose 없음
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    for _ in range(n + 6):
        p.run(red)
    p.run(red)
    det = p.get_latest()
    assert det is not None and det.is_predicted is True


def test_주황_박스가_사람_가던_쪽으로_밀린다(clock):
    """코스팅이 살아나도 얼어 있으면 사람 눈에는 그대로다 — 실제로 밀려야 한다."""
    p, red, _pose = _pose_pipeline()
    clock.t += constants.POSE_CALIBRATION_TIMEOUT_SEC + 1.0
    p.run(red)
    first = p.get_latest()
    for _ in range(4):
        p.run(red)
    later = p.get_latest()
    assert later.cx > first.cx, "오른쪽으로 가다 사라졌는데 박스가 제자리다"
    assert later.area == pytest.approx(first.area), "면적은 외삽하면 안 된다"
