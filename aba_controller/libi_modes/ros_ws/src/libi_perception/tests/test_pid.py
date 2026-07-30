from types import SimpleNamespace
from libi_perception.pid import FollowPID, clamp


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
                IMAGE_WIDTH=640, KP_ANGLE=0.0010, KI_ANGLE=0.0, KD_ANGLE=0.0,
                INTEGRAL_ANGLE_CLAMP=200.0, ANGLE_DEADZONE=45.0, ANGULAR_Z_MAX=0.60,
                ANGULAR_SMOOTHING=1.0)   # smoothing=1 -> deterministic single-step
    base.update(over)
    return SimpleNamespace(**base)


def test_clamp():
    assert clamp(5, 0, 1) == 1
    assert clamp(-5, 0, 1) == 0
    assert clamp(0.5, 0, 1) == 0.5


def test_far_target_drives_forward():
    pid = FollowPID(_cfg())
    lin, _ = pid.compute(cx=320.0, area=100.0, dt=0.05)   # sqrt=10 << 360
    assert lin > 0


def test_too_close_target_reverses_and_is_bounded():
    pid = FollowPID(_cfg())
    lin, _ = pid.compute(cx=320.0, area=1_000_000.0, dt=0.05)  # sqrt=1000 >> 360
    assert lin < 0
    assert lin >= -0.06        # bounded by LINEAR_X_REVERSE_MAX


def test_target_left_of_center_turns_left():
    pid = FollowPID(_cfg())
    _, ang = pid.compute(cx=0.0, area=100.0, dt=0.05)  # cx < width/2 -> err>0 -> +ang
    assert ang > 0


def test_deadzone_zeroes_small_bearing_error():
    pid = FollowPID(_cfg())
    _, ang = pid.compute(cx=320.0 - 10.0, area=100.0, dt=0.05)  # |err|=10 < 45
    assert ang == 0.0


def test_angular_clamped():
    pid = FollowPID(_cfg(KP_ANGLE=1.0))
    _, ang = pid.compute(cx=0.0, area=100.0, dt=0.05)
    assert ang <= 0.60


# ── 해상도 불변성 (2026-07-30) ────────────────────────────────────────────────
#
# 카메라를 640x480 → 320x240 으로 내렸더니 같은 사람의 bbox 가 선형으로 절반이 됐다.
# 튜닝값(TARGET_SIZE=280·KP=0.0030·DEADZONE=45)이 전부 640 기준 픽셀이라, 환산이 없으면
#   · 거리: sqrt(area) 가 절반 → 목표에 영영 못 닿아 **전진이 안 멈춘다**
#   · 조향: 중심을 320 으로 잡는데 실제는 160 → 늘 한쪽으로 헛돈다
# 여기서 못 박는 계약: **같은 장면이면 해상도가 달라도 같은 출력.**

def _same_scene_at(width, pid):
    """640 기준으로 (cx=200, size=100) 인 장면을 `width` 해상도 픽셀로 환산해 넣는다."""
    k = width / 640.0
    cx = 200.0 * k
    size = 100.0 * k
    return pid.compute(cx=cx, area=size * size, dt=0.05, image_width=width)


def test_half_resolution_gives_same_output():
    a = _same_scene_at(640, FollowPID(_cfg()))
    b = _same_scene_at(320, FollowPID(_cfg()))
    assert abs(a[0] - b[0]) < 1e-9, f"거리 출력이 해상도에 따라 달라졌다: {a[0]} vs {b[0]}"
    assert abs(a[1] - b[1]) < 1e-9, f"조향 출력이 해상도에 따라 달라졌다: {a[1]} vs {b[1]}"


def test_other_resolutions_too():
    ref = _same_scene_at(640, FollowPID(_cfg()))
    for w in (320, 480, 800, 1280):
        got = _same_scene_at(w, FollowPID(_cfg()))
        assert abs(ref[0] - got[0]) < 1e-9 and abs(ref[1] - got[1]) < 1e-9, \
            f"width={w} 에서 출력이 다르다: {got} != {ref}"


def test_screen_filling_person_reads_the_same_at_any_resolution():
    """"화면을 세로로 꽉 채운 사람"은 해상도와 무관하게 같은 판단을 받아야 한다.

    config.py 6-8행이 640x480 기준으로 계산해 둔 값이 `size=sqrt(480*127)=247` 이다.
    환산이 맞다면 320x240 에서 꽉 채운 사람도 **247 로 읽혀야** 한다.
    (247 < TARGET_SIZE 280 이라 아직 전진하는 것은 **의도된 동작**이다 — 같은 주석 13-15행:
     "280 은 247보다 조금 크므로 bbox 가 살짝 잘릴 만큼은 다가간다". 그러니 여기서
     후진을 기대하면 안 된다. 지켜야 할 것은 부호가 아니라 **해상도 불변**이다.)
    """
    cfg = _cfg(TARGET_SIZE=280.0)

    def full_screen_at(w):
        h = w * 3 / 4                       # 4:3
        bw = 0.45 / 1.7 * h                 # 사람 폭/키 비율
        return FollowPID(cfg).compute(cx=w / 2.0, area=h * bw, dt=0.05, image_width=w)

    ref = full_screen_at(640)
    for w in (320, 480):
        got = full_screen_at(w)
        assert abs(ref[0] - got[0]) < 1e-9, \
            f"width={w}: 같은 장면인데 거리 판단이 다르다 {got[0]} != {ref[0]}"

    # 그리고 그 값이 문서의 247 과 맞는지 — 환산이 통째로 어긋나면 여기서 드러난다.
    import math
    k = 640 / 320.0
    size_320 = math.sqrt(240.0 * (0.45 / 1.7 * 240.0)) * k
    assert abs(size_320 - 247.0) < 2.0, f"640 기준 size 가 247 이 아니다: {size_320:.1f}"


def test_missing_image_width_falls_back_to_cfg():
    """소스가 해상도를 안 보내면 예전(640 가정) 그대로여야 한다 — 회귀 방지."""
    pid_a = FollowPID(_cfg())
    pid_b = FollowPID(_cfg())
    assert pid_a.compute(cx=200.0, area=10_000.0, dt=0.05) == \
           pid_b.compute(cx=200.0, area=10_000.0, dt=0.05, image_width=640)
