import pytest
from types import SimpleNamespace
from libi_perception.pid import FollowPID, clamp


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, DIST_DEADZONE=0.0,
                LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
                IMAGE_WIDTH=640, KP_ANGLE=0.0010, KI_ANGLE=0.0, KD_ANGLE=0.0,
                INTEGRAL_ANGLE_CLAMP=200.0, ANGLE_DEADZONE=45.0, ANGULAR_Z_MAX=0.60,
                ANGLE_RESUME_RATIO=1.3,  # 운영값과 같게 — 빼면 히스테리시스가 꺼진다
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


# ── 원본 픽셀 그대로 (2026-08-02) ─────────────────────────────────────────────
#
# 해상도 환산(`k = IMAGE_WIDTH / image_width`)을 걷어냈다. 이제 검출이 보낸 픽셀이
# 그대로 PID 에 들어간다 — 화면에서 잰 값이 곧 config 값이다.
#
# ⚠️ 대가: 픽셀 상수가 카메라 해상도에 묶였다. 여기서 못 박는 계약은
#    **"환산이 없다"** 와 **"지금 카메라(320×240) 에서 값들이 실제로 도달 가능하다"** 둘이다.

def test_no_resolution_scaling():
    """compute 는 해상도를 안 받는다. 넣으면 TypeError 여야 한다 — 환산 부활 방지."""
    import pytest
    pid = FollowPID(_cfg())
    with pytest.raises(TypeError):
        pid.compute(cx=200.0, area=10_000.0, dt=0.05, image_width=320)


def test_pixels_go_in_unscaled():
    """들어온 픽셀이 그대로 쓰인다 — 어떤 배율도 안 걸린다.

    `cfg.IMAGE_WIDTH` 를 320 으로 두고 그 화면의 중심·목표크기를 넣는다. 환산이
    살아 있으면 640 기준으로 끌어올려져 중심과 크기가 둘 다 어긋난다.
    """
    cfg = _cfg(IMAGE_WIDTH=320, TARGET_SIZE=140.0, DIST_DEADZONE=0.0)
    pid = FollowPID(cfg)
    _, ang = pid.compute(cx=160.0, area=10_000.0, dt=0.05)
    assert ang == 0.0, f"화면 중심(160)인데 각속도가 났다: {ang}"
    lin, _ = pid.compute(cx=160.0, area=140.0 ** 2, dt=0.05)
    assert lin == 0.0, f"목표 크기(140)인데 선속도가 났다: {lin}"


def test_real_config_center_is_the_real_camera_center():
    """운영 config 의 중심이 실제 카메라 중심과 같아야 한다.

    환산이 있던 시절엔 IMAGE_WIDTH 가 640(기준 폭)이라 실제 320 카메라의 중심
    160 과 달랐고, 그 차이를 `k` 가 메웠다. 환산을 걷어낸 지금은 **IMAGE_WIDTH 가
    곧 카메라 폭**이어야 한다 — 아니면 사람이 정중앙에 있어도 한쪽으로 계속 돈다.
    """
    from libi_perception import config
    pid = FollowPID(config)
    _, ang = pid.compute(cx=config.IMAGE_WIDTH / 2.0, area=10_000.0, dt=0.05)
    assert ang == 0.0, (
        f"IMAGE_WIDTH={config.IMAGE_WIDTH} 의 중심인데 각속도 {ang} — "
        f"카메라 실제 폭(320)과 어긋났는지 확인할 것")


def test_target_size_is_reachable_on_the_real_camera():
    """`TARGET_SIZE` 가 지금 카메라에서 **실제로 나올 수 있는** 크기여야 한다.

    2026-07-28·07-30 사고가 둘 다 이것이었다 — 목표가 화면에서 안 나오는 크기라
    오차가 계속 양수고, 전진이 영영 안 멈춰 로봇이 들이박았다.

    ⚠️ 상한을 "안 잘린 사람이 화면 높이를 꽉 채움"으로 잡으면 **너무 빡빡하다.**
       320×240 에서 그 값은 √(240 × 63.5) = 123 인데, 사람이 더 가까워지면 bbox 가
       위아래로 **잘리면서 폭이 넓어져** 그보다 훨씬 커진다(높이 240 · 폭 147 이면 188).
       2026-08-02 실측 188 이 정확히 그 구간이다. 그러니 기하 모형이 아니라
       **프레임 전체**를 상한으로 본다.

    프레임을 통째로 채우면 √(W×H) 다. 목표가 거기 가까우면 사람이 화면을 다 덮어야
    멈춘다는 뜻이라 사실상 도달 불가다. 85% 를 선으로 둔다.
    """
    import math
    from libi_perception import config
    h = config.IMAGE_WIDTH * 3 / 4                  # 4:3
    ceiling = math.sqrt(config.IMAGE_WIDTH * h)     # 프레임을 통째로 채운 bbox
    assert config.TARGET_SIZE <= ceiling * 0.85, (
        f"TARGET_SIZE={config.TARGET_SIZE} 가 프레임 전체({ceiling:.0f})에 너무 가깝다 "
        f"— 사람이 화면을 다 덮어야 멈춘다는 뜻이라 전진이 안 멈춘다")
    assert config.TARGET_SIZE > 0


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


def test_distance_deadzone_stays_inside_the_measured_stop_band():
    """정지 구간이 패널 실측으로 잡은 바깥 경계를 **넘지 않는다.**

    실측(2026-08-02, 측정기로 화면에 직접 친 박스): "이 정도면 전진 √area≈158 /
    이 정도면 후진 √area≈218" (320 원본 픽셀 기준).

    ⚠️ 그 뒤 실주행에서 **너무 바짝 붙는다**는 판단이 나와 `TARGET_SIZE` 를 188 →
    150 으로 내렸다. 즉 이제 정지 구간 전체가 실측 구간보다 **앞쪽(더 먼 거리)** 에
    있다. 그래서 여기서 못 박는 것은 "실측 구간 안"이 아니라 **후진 경계를 넘지
    않는 것** 하나다 — 218 보다 위로 올라가면 실측상 "이미 후진해야 할 거리"인데
    로봇이 서 있게 되고, 그건 사람에게 닿는 방향의 실패다.

    더 멀리 서는 쪽(lo 를 낮추는 쪽)은 안전한 방향이라 막지 않는다.
    """
    from libi_perception import config
    hi = config.TARGET_SIZE + config.DIST_DEADZONE
    assert hi <= 218, f"후진해야 할 구간(√area {hi:.0f} 이상)까지 정지가 먹었다"
    assert config.DIST_DEADZONE > 0, "구간이 꺼지면 바퀴가 목표 근처에서 깨작인다"


def test_bearing_deadzone_fallback_matches_the_fraction():
    """폴백 픽셀값(`ANGLE_DEADZONE`)과 비율(`ANGLE_DEADZONE_FRAC`)이 같은 곳을 가리켜야 한다.

    실제로 쓰이는 것은 비율 쪽이다(`pid.py`). 픽셀값은 비율이 없는 설정에서만
    쓰이는데, 둘이 어긋나 있으면 그 경로로 떨어지는 순간 **경계가 조용히 달라진다.**

    ⚠️ [2026-08-02] 예전엔 이 값이 화면 3등분 가운데 칸(±w/6)과 같은지를 봤다.
       지금은 사용자 지시로 정지 구간을 그 1/3(±w/18)로 좁혔으므로 **더 이상
       화면 가이드선 = 제어 경계가 아니다.** 화각이 좁아 3등분 칸이 너무 넓다는
       실측 판단이다. 화면 선을 맞추려면 `perception_server.draw_overlay` 를
       같이 고쳐야 한다(노트북 쪽).
    """
    from libi_perception import config
    expected = config.IMAGE_WIDTH * config.ANGLE_DEADZONE_FRAC
    assert abs(config.ANGLE_DEADZONE - expected) < 1.0, (
        f"폴백 {config.ANGLE_DEADZONE} 와 비율 환산 {expected:.2f} 이 어긋난다")


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


def test_bearing_turns_as_soon_as_it_leaves_the_zone():
    """⚠️ [2026-08-02] **정지 구간을 벗어나면 곧바로 돈다 — 히스테리시스 없다.**

    예전엔 `ANGLE_RESUME_RATIO=1.3` 이라 칸을 30% 더 벗어나야 돌기 시작했다.
    그 사이 구간은 **사람이 화면상 명백히 칸 밖인데도 로봇이 안 도는** 구간이라
    화면이 거짓말을 했다(실측 2026-08-02). 경계 하나로 되돌린 것을 못 박는다.
    """
    dz = 106.67
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640, ANGULAR_Z_MIN=0.0))
    _, ang = pid.compute(cx=640 / 2.0 - (dz + 5.0), area=280.0 ** 2, dt=0.05)
    assert ang > 0.0, f"칸을 벗어났는데 안 돈다: {ang}"


def test_bearing_error_is_subtracted_so_the_edge_does_not_jump():
    """경계 바로 밖에서 명령이 **0 부터 이어져야** 한다 — 거리축과 같은 규칙.

    오차를 통째로 쓰면 경계에서 `KP × dz` 가 한꺼번에 튀어나오고, 그 반동으로
    칸 안팎을 오가며 좌우로 깨작인다("도리도리"). 빼내면 그 튐이 없다.
    """
    dz, kp = 106.67, 0.0010
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640, KP_ANGLE=kp,
                         ANGULAR_Z_MIN=0.0))
    _, ang = pid.compute(cx=640 / 2.0 - (dz + 1.0), area=280.0 ** 2, dt=0.05)
    assert 0.0 < ang < kp * dz * 0.1, f"경계에서 튀었다(오차를 안 뺐다): {ang}"


def test_bearing_min_speed_overcomes_stiction_but_not_inside_the_zone():
    """`ANGULAR_Z_MIN` — 돌기로 정했으면 실제로 도는 크기를 보장한다.

    오차를 빼내면 경계 근처 명령이 너무 작아 바퀴가 안 돈다(실측: -0.009 rad/s 에
    odom 이 ±0.013 으로 떨기만 했다). 그래서 0 이 아닌 명령엔 하한을 씌운다.

    ⚠️ 하지만 **정지 구간 안에서는 절대 걸리면 안 된다** — 걸리면 가운데 칸에서
       영원히 도는 로봇이 된다. 그 경계를 여기서 못 박는다.
    """
    dz, floor = 106.67, 0.12
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640, KP_ANGLE=0.0010,
                         ANGULAR_Z_MIN=floor))
    # 칸 바로 밖 — 비례항만으론 0.001 도 안 되지만 하한이 받쳐 준다.
    _, ang = pid.compute(cx=640 / 2.0 - (dz + 1.0), area=280.0 ** 2, dt=0.05)
    assert abs(ang) >= floor, f"칸 밖인데 못 도는 크기다: {ang}"
    # 칸 안 — 하한이 걸리면 안 된다.
    pid2 = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640, ANGULAR_Z_MIN=floor))
    for off in (0.0, dz / 2.0, dz - 1.0):
        _, ang = pid2.compute(cx=640 / 2.0 - off, area=280.0 ** 2, dt=0.05)
        assert ang == 0.0, f"가운데 칸 안({off}px)인데 하한이 걸렸다: {ang}"


def test_bearing_min_speed_does_not_latch_after_returning_to_center():
    """칸 안으로 돌아오면 각속도가 **0 까지 내려가야** 한다 — 하한에 물리면 안 된다.

    저역통과(`ANGULAR_SMOOTHING`)라 즉시 0 은 아니고 몇 tick 에 걸쳐 줄어든다.
    문제는 그 감쇠 도중 값이 `ANGULAR_Z_MIN` 밑으로 내려갈 때다. 하한을 원오차로
    판정하면 거기서 **0.12 로 도로 부풀어** 사람이 가운데 있는데도 로봇이 영원히
    돈다. `pid.py` 가 **빼낸 뒤 오차**로 가르는 이유가 이것이다.

    두 가지를 못 박는다: 단조 감소일 것, 그리고 끝에 0 일 것.
    """
    dz = 106.67
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640, ANGULAR_Z_MIN=0.12,
                         ANGULAR_SMOOTHING=0.3))
    for _ in range(5):                                   # 칸 밖에서 충분히 돈다
        pid.compute(cx=640 / 2.0 - 300.0, area=280.0 ** 2, dt=0.05)
    prev = None
    for _ in range(60):                                  # 정중앙 유지
        _, ang = pid.compute(cx=640 / 2.0, area=280.0 ** 2, dt=0.05)
        if prev is not None:
            assert ang <= prev + 1e-12, f"감쇠 중에 하한이 물려 다시 커졌다: {prev} → {ang}"
        prev = ang
    assert prev == pytest.approx(0.0, abs=1e-6), f"가운데인데 각속도가 안 죽는다: {prev}"


def test_bearing_hysteresis_keeps_turning_until_back_in_the_zone():
    """일단 돌기 시작하면 **가운데 칸에 들어올 때까지** 계속 돈다.

    복귀 문턱으로 멈추면 칸에 못 들어온 채 서 버린다 — 그러면 사람이 칸 밖에
    있는데 로봇이 가만히 있는 상태가 된다.
    """
    dz, ratio = 106.67, 1.3
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, ANGLE_RESUME_RATIO=ratio, IMAGE_WIDTH=640))
    pid.compute(cx=640 / 2.0 - 250.0, area=280.0 ** 2, dt=0.05)      # 돌기 시작
    # 복귀 문턱(138.7)과 칸(106.67) 사이 — 아직 칸 밖이므로 계속 돌아야 한다.
    _, ang = pid.compute(cx=640 / 2.0 - 120.0, area=280.0 ** 2, dt=0.05)
    assert ang > 0.0, f"칸에 못 들어왔는데 멈췄다: {ang}"
    # 칸 안으로 들어오면 멈춘다.
    _, ang = pid.compute(cx=640 / 2.0 - 50.0, area=280.0 ** 2, dt=0.05)
    assert ang == 0.0, f"가운데 칸 안인데 돈다: {ang}"


def test_bearing_deadzone_is_silent_inside_the_center_third():
    """가운데 칸 안이면 각속도 0 — 이게 사용자가 요구한 규칙이다."""
    dz = 106.67
    pid = FollowPID(_cfg(ANGLE_DEADZONE=dz, IMAGE_WIDTH=640))
    for off in (0.0, dz / 2.0, dz - 1.0):
        _, ang = pid.compute(cx=640 / 2.0 - off, area=280.0 ** 2, dt=0.05)
        assert ang == 0.0, f"가운데 칸 안({off}px)인데 돌았다: {ang}"


def test_bearing_deadzone_uses_screen_fraction_not_fixed_pixels():
    """비율(w/6)로 잡아야 해상도가 달라져도 **화면에 보이는 그 칸**과 같다."""
    from types import SimpleNamespace
    base = _cfg(IMAGE_WIDTH=640, ANGLE_DEADZONE=9999.0)   # 픽셀값은 일부러 틀리게
    cfg = SimpleNamespace(**{**vars(base), "ANGLE_DEADZONE_FRAC": 1.0 / 6.0})
    pid = FollowPID(cfg)
    # 640/6 = 106.67 안 → 0. 픽셀값(9999)이 쓰였다면 이것도 0 이라 구분이 안 되므로
    # 칸 밖도 같이 본다.
    _, inside = pid.compute(cx=640 / 2.0 - 50.0, area=280.0 ** 2, dt=0.05)
    assert inside == 0.0
    _, outside = pid.compute(cx=640 / 2.0 - 300.0, area=280.0 ** 2, dt=0.05)
    assert outside > 0.0, "비율이 아니라 고정 픽셀(9999)이 쓰였다"
