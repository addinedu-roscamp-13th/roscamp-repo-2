import os

import numpy as np
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


def test_coasting_holds_the_last_real_box():
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

    # ⚠️ [2026-08-02] **외삽하지 않는다 — 마지막 실검출 박스를 그대로 든다.**
    #   사용자 지시: "예측 느낌 말고, 유실된 그 시점의 박스를 유지하기만 하면 된다."
    #   외삽은 가려질 때 면적을 무너뜨리고(3프레임 만에 0) 중심도 끌고 갔다.
    boxes = []
    for _ in range(6):
        p.run(red)
        d = p.get_latest()
        assert d.is_predicted is True
        boxes.append((d.cx, d.cy, d.area, tuple(d.bbox)))
    first = boxes[0]
    assert all(b == first for b in boxes), \
        f"코스팅 중에 박스가 움직였다(외삽이 살아 있다): {boxes[0]} → {boxes[-1]}"
    # 그리고 그 박스는 **마지막으로 실제 본 검출** 그 자체여야 한다.
    #
    # ⚠️ 살아 있을 때의 `det.cx`(133.8)와는 다르다 — 그쪽은 스무더가 한 스텝 앞을
    #    보정한 값이고(`predict(PREDICT_DT)`), 코스팅은 **원본 박스**를 든다.
    #    그래서 코스팅에서는 `cx` 가 bbox 중심과 정확히 일치한다(화면과 제어가 같은 것을
    #    가리킨다). 전환 순간의 한 번뿐인 차이는 방위 데드존(±13.3px)보다 작다.
    assert tuple(last_real.bbox) == first[3]
    assert abs(first[0] - (first[3][0] + first[3][2]) / 2.0) < 1e-6, \
        "코스팅 cx 가 bbox 중심과 다르다"


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
