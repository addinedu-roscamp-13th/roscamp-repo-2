import os

import numpy as np
import pytest
from follower_perception.detection import TrackedBox
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from follower_perception import constants


def _frame(color_bgr, w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


def _perception(script):
    return FollowerPerception(detector=MockDetector(script),
                              reid=ReIDEngine(backend='colour'))


def test_register_requires_stable_frames():
    script = [[_full_box(1)]] * constants.REGISTRATION_STABLE_FRAMES
    p = _perception(script)
    red = _frame((0, 0, 255))
    results = [p.register(red) for _ in range(constants.REGISTRATION_STABLE_FRAMES)]
    assert results[-1] is True            # confirmed on the last stable frame
    assert results[0] is False            # not yet stable on frame 1


def test_run_then_get_latest_returns_owner():
    n = constants.REGISTRATION_STABLE_FRAMES
    script = [[_full_box(1)]] * (n + 1)
    p = _perception(script)
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.run(red)
    det = p.get_latest()
    assert det is not None
    assert det.is_owner is True
    assert det.is_predicted is False
    assert det.track_id == 1


def test_coasting_then_none():
    n = constants.REGISTRATION_STABLE_FRAMES
    # n stable frames to register, 1 run with owner, then misses (empty lists)
    script = [[_full_box(1)]] * (n + 1) + [[]] * (constants.COAST_LIMIT + 2)
    p = _perception(script)
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.run(red)                            # owner seen
    assert p.get_latest().is_predicted is False
    p.run(red)                            # first miss
    assert p.get_latest().is_predicted is True   # coasting
    for _ in range(constants.COAST_LIMIT + 1):
        p.run(red)
    assert p.get_latest() is None         # beyond coast limit


def test_reset_clears_everything():
    n = constants.REGISTRATION_STABLE_FRAMES
    p = _perception([[_full_box(1)]] * (n + 1))
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.reset()
    assert p.get_latest() is None


def test_register_from_image_registers_central_person():
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    box = p.register_from_image(_frame((0, 0, 255)))
    assert box is not None
    assert box.track_id == 1
    assert p.matcher.is_registered is True


def test_register_from_image_no_person_returns_none():
    p = _perception([])
    p.detector = MockDetector([[]])
    assert p.register_from_image(_frame((0, 0, 255))) is None
    assert p.matcher.is_registered is False


def test_save_profile_writes_folder(tmp_path):
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    p.register_from_image(_frame((0, 0, 255)))
    d = str(tmp_path / "v1")
    p.save_profile(d, name="v1", source_image="x.jpg",
                   registered_at="2026-07-10T00:00:00")
    assert os.path.exists(os.path.join(d, "crop.jpg"))
    assert os.path.exists(os.path.join(d, "meta.json"))


def test_save_then_load_profile_round_trip(tmp_path):
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    p.register_from_image(_frame((0, 0, 255)))
    d = str(tmp_path / "v1")
    p.save_profile(d, name="v1")

    p2 = _perception([])
    p2.load_profile(d)
    assert p2.matcher.is_registered is True


# ── 예측 bbox 가 실제로 움직이는가 (2026-08-02) ──────────────────────────────
# 화면은 `det.bbox` 를 그린다(perception_server.draw_overlay). 예전엔 예측일 때도
# 마지막 실검출 bbox 를 그대로 실어 보내서 **주황 박스가 얼어붙어** 있었다.
# 사용자 보고: "UI 에서 알파베타 필터면 주황색으로 되잖아, 그게 잘 안 되었어."

#: 프레임 안에 머무는 사람 상자. 프레임 밖으로 나가면 crop 이 비어 matcher 가
#  못 잡고 `_last_owner` 가 없어져 예측 자체가 안 나온다(테스트가 조용히 무의미해진다).
def _moving_box(tid, cx, w=40, h=80):
    return TrackedBox(bbox=(cx - w / 2, 120 - h / 2, cx + w / 2, 120 + h / 2),
                      cx=float(cx), cy=120.0, area=float(w * h),
                      track_id=tid, confidence=0.9)


def test_coasting_extrapolates_position_but_never_area():
    """코스팅은 **위치만** 민다. 면적은 마지막 실측을 든다.

    ⚠️ [2026-08-06] 이 시험은 뒤집혔다. 2026-08-02 에는 "얼린다"를 검사했다
       (`test_coasting_holds_the_last_real_box`). 사용자 요청으로 외삽을 되돌리되,
       그때 외삽을 껐던 이유 두 개는 각각 막았다 — 면적 고정과 이동 상한.
       상한·대각선·Lying 차단은 `test_coast_extrapolation.py` 가 따로 본다.
    """
    n = constants.REGISTRATION_STABLE_FRAMES
    # 오른쪽으로 꾸준히 이동하다 사라진다.
    moving = [[_moving_box(1, 80 + i * 6)] for i in range(n + 6)]
    script = [[_full_box(1, 320, 240)]] * n + moving + [[]] * 5
    p = _perception(script)
    red = _frame((0, 0, 255), 320, 240)
    for _ in range(n):
        p.register(red)
    for _ in range(n + 6):
        p.run(red)
    last_real = p.get_latest()
    assert last_real.is_predicted is False

    boxes = []
    for _ in range(5):
        p.run(red)
        d = p.get_latest()
        assert d.is_predicted is True
        boxes.append((d.cx, d.cy, d.area, tuple(d.bbox)))

    # ① 위치는 가던 쪽(오른쪽)으로 계속 밀린다 — 안 밀면 주황 박스가 제자리에 선다.
    assert boxes[-1][0] > boxes[0][0], "코스팅이 위치를 안 민다(외삽이 꺼져 있다)"
    # ② 면적은 한 톨도 안 변한다 — √area 가 거리 PID 의 입력이라 여기가 무너지면
    #    "아주 멀다"로 읽혀 전속 전진한다(실측 2026-08-02).
    assert all(b[2] == pytest.approx(last_real.area) for b in boxes)
    # ③ 화면 박스도 같은 만큼 밀리고 크기는 그대로다 — cx 만 밀면 눈에는 안 보인다.
    w0 = boxes[0][3][2] - boxes[0][3][0]
    assert all(b[3][2] - b[3][0] == pytest.approx(w0) for b in boxes)
    assert boxes[-1][3][0] > boxes[0][3][0]
    # ④ cx 는 여전히 bbox 중심이다 — 화면과 제어가 같은 곳을 가리켜야 한다.
    for b in boxes:
        assert b[0] == pytest.approx((b[3][0] + b[3][2]) / 2.0)


def test_predicted_bbox_keeps_the_last_aspect_ratio():
    """예측은 중심·면적만 안다 — 모양은 마지막 실검출 것을 유지한다."""
    n = constants.REGISTRATION_STABLE_FRAMES
    script = [[_full_box(1, 320, 240)]] * n + [[_moving_box(1, 160)]] * 3 + [[]] * 3
    p = _perception(script)
    red = _frame((0, 0, 255), 320, 240)
    for _ in range(n):
        p.register(red)
    for _ in range(3):
        p.run(red)
    real = p.get_latest()
    ar_real = (real.bbox[2] - real.bbox[0]) / (real.bbox[3] - real.bbox[1])
    p.run(red)
    d = p.get_latest()
    ar_pred = (d.bbox[2] - d.bbox[0]) / (d.bbox[3] - d.bbox[1])
    assert abs(ar_pred - ar_real) < 1e-6, f"종횡비가 바뀌었다: {ar_real} → {ar_pred}"


# ── 가려짐이 예측 면적을 무너뜨리던 회귀 (2026-08-02) ────────────────────────
# 서가·문틀 뒤로 들어가면 사라지기 직전 bbox 가 잘려 면적이 급감한다. α-β 가 그
# 급감을 속도로 학습해 밀고 나가면 3프레임 만에 area 가 0 이 됐다:
#   ① 주황 예측 박스가 점으로 쪼그라든다  ② √area=0 → 거리 PID 가 전속 전진
#   ③ 예측 bbox 가 0 크기

def _shrinking_box(tid, area, cx=160.0):
    import math as _m
    ar = 0.5
    w = _m.sqrt(area * ar); h = w / ar
    return TrackedBox(bbox=(cx - w / 2, 120 - h / 2, cx + w / 2, 120 + h / 2),
                      cx=cx, cy=120.0, area=float(area), track_id=tid, confidence=0.9)


def _occluded_perception():
    n = constants.REGISTRATION_STABLE_FRAMES
    shrink = [3200.0] * 6 + [2900.0, 2400.0, 1800.0, 1200.0, 700.0]
    script = ([[_full_box(1, 320, 240)]] * n
              + [[_shrinking_box(1, a)] for a in shrink]
              + [[]] * (constants.COAST_LIMIT + 2))
    p = _perception(script)
    red = _frame((0, 0, 255), 320, 240)
    for _ in range(n):
        p.register(red)
    for _ in range(len(shrink)):
        p.run(red)
    return p, red


def test_occlusion_does_not_collapse_the_predicted_area():
    p, red = _occluded_perception()
    for miss in range(1, constants.COAST_LIMIT + 1):
        p.run(red)
        d = p.get_latest()
        assert d is not None, f"miss {miss} 에서 코스팅이 끊겼다"
        assert d.area > 0.0, (
            f"miss {miss} 에서 예측 면적이 0 이 됐다 — 가려짐 급감을 외삽했다")


def test_predicted_area_is_held_not_extrapolated():
    """면적은 마지막 필터 추정값에서 **멈춘다** — 코스팅 내내 같은 값이어야 한다."""
    p, red = _occluded_perception()
    p.run(red)
    first = p.get_latest().area
    for _ in range(5):
        p.run(red)
    later = p.get_latest().area
    assert first == later, f"면적이 코스팅 중에 변했다: {first} → {later}"
    assert first > 0.0


def test_predicted_bbox_stays_a_real_box_under_occlusion():
    """박스가 0 크기로 쪼그라들면 화면에서 안 보인다 — 그게 증상이었다."""
    p, red = _occluded_perception()
    for _ in range(6):
        p.run(red)
    d = p.get_latest()
    w = d.bbox[2] - d.bbox[0]
    h = d.bbox[3] - d.bbox[1]
    assert w > 1.0 and h > 1.0, f"예측 박스가 사라졌다: {w:.2f} x {h:.2f}"


def test_register_nearest_는_화면_가운데가_아니라_가장_큰_후보를_고른다():
    """`register_from_image`(가운데)와 갈라진 이유 자체를 고정한다.

    가운데에 작은 사람, 가장자리에 큰 사람이 있을 때 둘이 **다른** 후보를 골라야 한다.
    """
    import numpy as np
    from follower_perception.pipeline import FollowerPerception

    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class _Box:
        def __init__(self, cx, area):
            self.cx, self.cy, self.area = cx, 120, area
            half = int(area ** 0.5) // 2
            self.bbox = (max(0, cx - half), 120 - half, min(319, cx + half), 120 + half)
            self.track_id, self.confidence, self.is_predicted = 1, 0.9, False

    small_center = _Box(cx=160, area=900)     # 화면 정중앙, 작다(멀다)
    big_edge = _Box(cx=40, area=19600)        # 왼쪽 끝, 크다(가깝다)

    class _Detector:
        def detect(self, _frame):
            return [small_center, big_edge]

    perception = FollowerPerception.__new__(FollowerPerception)
    perception.detector = _Detector()

    assert perception._pick_central([small_center, big_edge], frame) is small_center
    picked = perception._pick_nearest([small_center, big_edge], frame)
    assert picked is big_edge


def test_register_nearest_는_너무_작은_후보를_거른다():
    """`REGISTRATION_MIN_AREA_RATIO` 필터를 `_pick_central` 과 똑같이 적용한다."""
    import numpy as np
    from follower_perception.pipeline import FollowerPerception

    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class _Tiny:
        cx, cy, area = 160, 120, 100          # 320*240 의 0.13% — 1% 미만
        bbox = (155, 115, 165, 125)
        track_id, confidence, is_predicted = 1, 0.9, False

    perception = FollowerPerception.__new__(FollowerPerception)
    assert perception._pick_nearest([_Tiny()], frame) is None


def test_register_nearest_가_matcher_에_큰_후보의_crop_을_넘긴다():
    """등록 경로 전체(crop → matcher.register → 상태 초기화)가 도는지."""
    import numpy as np
    from follower_perception.pipeline import FollowerPerception

    frame = np.full((240, 320, 3), 128, dtype=np.uint8)

    class _Box:
        def __init__(self, cx, area):
            self.cx, self.cy, self.area = cx, 120, area
            half = int(area ** 0.5) // 2
            self.bbox = (max(0, cx - half), 120 - half, min(319, cx + half), 120 + half)
            self.track_id, self.confidence, self.is_predicted = 1, 0.9, False

    big = _Box(cx=40, area=19600)

    class _Detector:
        def detect(self, _f):
            return [_Box(cx=160, area=900), big]

    class _Matcher:
        def __init__(self):
            self.registered_with = None
        def register(self, roi):
            self.registered_with = roi

    class _Smoother:
        def reset(self):
            pass

    p = FollowerPerception.__new__(FollowerPerception)
    p.detector, p.matcher, p.smoother = _Detector(), _Matcher(), _Smoother()
    p._on_registered = lambda: None

    got = p.register_nearest(frame)

    assert got is big
    assert p.matcher.registered_with is not None       # crop 이 넘어갔다
    assert p._last_bbox == list(big.bbox)
