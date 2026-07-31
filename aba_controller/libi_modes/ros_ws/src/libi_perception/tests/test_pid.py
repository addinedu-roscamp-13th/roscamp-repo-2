from types import SimpleNamespace
from libi_perception.pid import FollowPID, clamp


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, DIST_DEADZONE=0.0,
                LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
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


# ── 거리 정지 구간 (DIST_DEADZONE) ──────────────────────────────────────────
# 목표 크기 근처에서 바퀴가 계속 깨작이던 문제. 방위각의 ANGLE_DEADZONE 과 같은 규칙.

def test_distance_deadzone_stops_near_target():
    pid = FollowPID(_cfg(DIST_DEADZONE=20.0))
    # sqrt(area)=350, 목표 360 -> 오차 10 < 20 -> 전진 명령이 없어야 한다.
    lin, _ = pid.compute(cx=320.0, area=350.0 ** 2, dt=0.05)
    assert lin == 0.0


def test_distance_deadzone_does_not_swallow_real_error():
    pid = FollowPID(_cfg(DIST_DEADZONE=20.0))
    # 오차 30 > 20 -> 구간 밖이므로 예전과 똑같이 움직인다.
    lin, _ = pid.compute(cx=320.0, area=330.0 ** 2, dt=0.05)
    assert lin > 0


def test_distance_deadzone_clears_the_integral():
    """정지 구간에 들어가면 적분항도 턴다.

    오차만 0 으로 만들면 `KI × I` 가 남아 명령이 완전히 0 이 되지 않는다. 게다가
    구간을 드나들 때 예전에 쌓인 적분이 되살아나 사람 쪽으로 튄다.
    """
    pid = FollowPID(_cfg(DIST_DEADZONE=20.0, KI_DIST=0.01))
    for _ in range(20):                      # 멀리서 적분을 쌓는다
        pid.compute(cx=320.0, area=100.0, dt=0.05)
    assert pid._i_size > 0
    lin, _ = pid.compute(cx=320.0, area=350.0 ** 2, dt=0.05)   # 구간 안으로
    assert pid._i_size == 0.0
    assert lin == 0.0


def test_shipped_config_enables_the_deadzone():
    """기본값이 0 이면 기능 전체가 조용히 꺼진다 — 그러라고 만든 게 아니다."""
    from libi_perception import config
    assert config.DIST_DEADZONE > 0


def test_distance_deadzone_is_continuous_at_the_edge():
    """구간 경계에서 명령이 튀면 안 된다.

    0 으로 죽이는 방식이면 경계 바로 밖에서 `KP × DEADZONE` 이 통째로 나온다 —
    서 있던 로봇이 사람이 한 걸음 물러난 순간 그 속도로 튄다. 빼내는 방식이라
    0 부터 이어져야 한다.
    """
    dz = 28.0
    pid = FollowPID(_cfg(DIST_DEADZONE=dz))
    # 목표 360, 구간 밖으로 1px 만 나간 지점.
    lin, _ = pid.compute(cx=320.0, area=(360.0 - dz - 1.0) ** 2, dt=0.05)
    assert 0.0 < lin < 0.0030 * 2, f"경계에서 튀었다: {lin}"


def test_distance_deadzone_matches_the_original_ten_percent_band():
    """원본 `cmd_preview.SIZE_DEADBAND` 는 30/300 = 목표의 10% 였다.

    제어가 bang-bang 에서 PID 로 옮겨가며 사라졌던 값이다. 비율이 어긋나면
    "예전 거리감"이 재현되지 않는다.
    """
    from libi_perception import config
    assert abs(config.DIST_DEADZONE / config.TARGET_SIZE - 0.10) < 0.005


def test_deadzone_does_not_suppress_reverse_when_too_close():
    """⚠️ 안전 방향. 사람에게 너무 가까우면 **후진**이 나와야 한다.

    구간 검사를 부호 없이 짜면(예: `e < DEADZONE`) 오차가 음수인 쪽 — 즉
    "너무 가깝다" — 이 전부 구간 안으로 삼켜져 후진이 영영 안 나온다.
    앞쪽만 검사하는 테스트로는 그 회귀가 안 잡힌다.
    """
    dz = 20.0
    pid = FollowPID(_cfg(DIST_DEADZONE=dz))
    # sqrt(area)=420, 목표 360 -> e = -60, 구간(20) 밖 -> 후진.
    lin, _ = pid.compute(cx=320.0, area=420.0 ** 2, dt=0.05)
    assert lin < 0, f"너무 가까운데 후진이 안 나온다: {lin}"


def test_deadzone_is_symmetric_at_both_edges():
    """가까운 쪽 경계도 먼 쪽과 똑같이 이어져야 한다 — 크기만 같고 부호만 반대."""
    dz = 20.0
    far = FollowPID(_cfg(DIST_DEADZONE=dz)).compute(
        cx=320.0, area=(360.0 - dz - 5.0) ** 2, dt=0.05)[0]
    near = FollowPID(_cfg(DIST_DEADZONE=dz)).compute(
        cx=320.0, area=(360.0 + dz + 5.0) ** 2, dt=0.05)[0]
    assert far > 0 > near
    assert abs(far + near) < 1e-9, f"양쪽이 비대칭이다: {far} vs {near}"


def test_entering_the_band_does_not_kick_the_derivative():
    """구간에 **들어오는 첫 프레임**에 D 항이 튀면 안 된다.

    `_prev_size` 를 안 지우면 `d = (0 - 직전오차)/dt` 가 한 번 나온다. 지금은
    출하 `KD_DIST = 0` 이라 안 보이지만, D 를 켜는 순간 "멈춰야 할 때 한 번
    튀는" 버그가 조용히 살아난다. D 를 켜고 재서 못 박는다.
    """
    dz = 20.0
    for approach_area in ((360.0 - dz - 30.0) ** 2,      # 멀리서 다가오며 진입
                          (360.0 + dz + 30.0) ** 2):     # 가까이서 물러나며 진입
        pid = FollowPID(_cfg(DIST_DEADZONE=dz, KD_DIST=0.01))
        pid.compute(cx=320.0, area=approach_area, dt=0.05)   # 구간 밖 한 프레임
        lin, _ = pid.compute(cx=320.0, area=360.0 ** 2, dt=0.05)  # 구간 안으로
        assert lin == 0.0, f"구간 진입에서 D 가 튀었다: {lin}"
