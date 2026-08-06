"""소실 방향 분류. 이미지 좌표(y 는 아래로 증가)."""
from follower_perception.exit_direction import classify_exit, may_coast
from follower_perception.constants import FRAME_DT, EXIT_AREA_SURGE


def per_frame(v):
    """프레임당 값 → `BBoxSmoother.velocity` 단위(px/초).

    ⚠️ [2026-08-02] 임계(`_DOWN_VY_FRAC`·`EXIT_AREA_SURGE`)는 **프레임당**인데
    velocity 는 **초당**이다. 예전 시험들은 초당 값을 프레임당인 것처럼 넣어
    **20배 예민한 버그를 그대로 못박고** 있었다. 의도를 눈에 보이게 이 헬퍼를 쓴다.
    """
    return v / FRAME_DT

W, H = 640, 480     # 마진 8% → x 51.2px, y 38.4px


def test_left_edge_is_side():
    assert classify_exit((0, 100, 40, 400), [-50.0, 0.0, 0.0], W, H) == "side"


def test_right_edge_is_side():
    assert classify_exit((600, 100, 639, 400), [50.0, 0.0, 0.0], W, H) == "side"


def test_bottom_edge_is_down():
    assert classify_exit((200, 200, 400, 479), [0.0, per_frame(15.0), 0.0], W, H) == "down"


def test_top_edge_is_up():
    assert classify_exit((200, 0, 400, 200), [0.0, per_frame(-15.0), 0.0], W, H) == "up"


def test_area_surge_is_down_without_touching_any_edge():
    """코앞으로 다가와 시야를 덮는 경우 — 가장자리 검사로는 못 잡는다."""
    assert classify_exit((150, 150, 500, 400), [0.0, 0.0, per_frame(EXIT_AREA_SURGE * 1.2)], W, H) == "down"


def test_middle_is_center():
    assert classify_exit((280, 200, 360, 300), [2.0, 1.0, 0.0], W, H) == "center"


def test_bottom_and_side_together_prefers_down():
    """모서리로 빠질 때 '옆'으로 읽으면 코앞의 대상을 향해 계속 전진한다."""
    assert classify_exit((0, 300, 60, 479), [per_frame(-15.0), per_frame(15.0), 0.0], W, H) == "down"


def test_top_and_side_together_prefers_up():
    assert classify_exit((0, 0, 60, 180), [per_frame(-15.0), per_frame(-15.0), 0.0], W, H) == "up"


def test_bottom_edge_moving_up_is_not_down():
    """바닥에 닿아 있어도 **올라오는 중**이면 사라지는 방향이 아래가 아니다."""
    assert classify_exit((200, 200, 400, 479), [0.0, per_frame(-15.0), 0.0], W, H) != "down"


def test_side_edge_but_vertical_motion_is_center():
    """가장자리에 있어도 주된 이동이 세로면 옆으로 빠진 게 아니다."""
    assert classify_exit((0, 200, 40, 300), [per_frame(1.0), per_frame(8.0), 0.0], W, H) == "center"


def test_missing_bbox_falls_back_to_center():
    """근거가 없으면 기존 동작(예측 추종)을 유지한다."""
    assert classify_exit(None, [0.0, 0.0, 0.0], W, H) == "center"


def test_zero_frame_size_falls_back_to_center():
    assert classify_exit((0, 0, 1, 1), [0.0, 0.0, 0.0], 0, 0) == "center"


def test_missing_velocity_is_tolerated():
    assert classify_exit((280, 200, 360, 300), None, W, H) == "center"


# ── coast 허용 규칙 ──────────────────────────────────────────────────────────

def test_coast_allowed_for_side_and_center():
    assert may_coast("side", "Standing") is True
    assert may_coast("center", "Standing") is True


def test_coast_blocked_for_down_and_up():
    assert may_coast("down", "Standing") is False
    assert may_coast("up", "Standing") is False


def test_lying_blocks_regardless_of_direction():
    """쓰러지는 중이던 대상을 예측 위치로 쫓아가는 것이 바로 피하려던 상황이다."""
    assert may_coast("side", "Lying") is False
    assert may_coast("center", "Lying") is False


def test_calibrating_no_longer_blocks_coasting():
    """⚠️ [2026-08-06] `Calibrating` 을 차단목록에서 **뺐다.**

    넣었던 이유는 "기준을 재는 중이라 판정을 못 믿는다" 였는데, 같은 "모른다"인
    `Unknown` 은 이미 허용하고 있어 일관되지 않았다. 막으면 등록 직후 캘리브 구간
    (정상 조건에서도 60프레임 ≈ 3.5초, 골격이 안 잡히면 더 길다) 안에 놓칠 때마다
    주황 박스가 통째로 사라진다 — 사용자 보고 2026-08-06 "pose 켜면 안 보인다".

    빼도 안전한 근거는 **로봇이 어차피 안 움직인다**는 것이다. `PostureGate` 가
    `Calibrating` 을 즉시정지로 보고, 코스팅은 `motion_ok` 를 마지막 실제 판정에서
    물려받으므로 전진은 0 이다(`test_pose_does_not_kill_coasting.py` 가 종단으로 확인).
    """
    assert may_coast("side", "Calibrating") is True
    assert may_coast("center", "Calibrating") is True
    # 방향 규칙은 그대로다 — 코앞(down)·낙하(up)는 자세와 무관하게 막힌다.
    assert may_coast("down", "Calibrating") is False


def test_side_and_unknown_do_not_block_coasting():
    """⚠️ [2026-08-02] **정상 추종의 대부분이 여기 걸려 코스팅이 꺼져 있었다.**

    따라가는 사람은 대부분 옆이나 등을 보인다 — 그러면 자세가 `Side` 이거나,
    어깨·골반 신뢰도가 모자라 `Unknown` 이 된다. 예전 규칙(`!= "Standing"` 이면
    차단)은 그 구간 전체에서 α-β 예측을 통째로 껐다.
    사용자 보고: "bbox 사라지면 알파베타 필터가 적용이 잘 안 되네".

    막아야 할 것은 `Lying` 이지 `Side` 가 아니다 — `control_loop` 도 2026-08-01 에
    "측면은 놓친 게 아니라 거리만 못 믿는 것"으로 이미 되돌렸다.
    """
    assert may_coast("side", "Side") is True
    assert may_coast("center", "Side") is True
    assert may_coast("center", "Unknown") is True


def test_unrecognised_posture_defaults_to_allowed():
    """차단목록 방식이라 모르는 자세 이름은 허용된다 — 그게 의도다.

    안전 근거는 `_NO_COAST_POSTURES` 주석에 있다(코스팅 상한 1.4초 · 최대 8cm ·
    DOWN 방향 차단 · 라이다 하드 스톱). 새 자세가 위험하면 그 목록에 넣는다.
    """
    assert may_coast("center", "Crouching") is True


def test_unknown_posture_source_does_not_block():
    assert may_coast("side", None) is True


# ── 바닥에 발이 닿은 대상이 코스팅을 잃던 회귀 (2026-08-01) ──────────────────

def test_feet_at_bottom_with_jitter_still_coasts():
    """⚠️ **따라가는 사람은 가까우면 발이 늘 화면 아래 가장자리에 있다.**

    예전에는 `at_bottom and vy > 0` 이라, 검출이 1~2px 흔들려 vy 가 조금만 양수여도
    DOWN 으로 떨어졌다. DOWN 은 `_COASTABLE` 이 아니라 **α-β 코스팅이 통째로
    건너뛰어진다** — 잠깐 가려진 것도 즉시 소실이 되어 회복 탐색이 바로 돌았다
    (실측: "사라지면 바로 peek 된다"). 임계를 둬서 흔들림은 걸러낸다.
    """
    bbox = (250, 100, 390, H - 5)            # 발이 바닥에 닿음
    d = classify_exit(bbox, [0.0, 2.0, 0.0], W, H)   # 2px/frame — 검출 흔들림 수준
    assert d != "down", f"흔들림을 아래로 빠진 것으로 읽었다: {d}"
    assert may_coast(d, None), "코스팅이 막히면 안 된다"


def test_real_fall_at_bottom_still_blocks_coasting():
    """⚠️ 안전 방향. 진짜로 아래로 빠지는 것은 여전히 막아야 한다 —
    코앞에 쓰러진 사람을 예측 위치로 밀고 들어가면 들이받는다."""
    bbox = (250, 100, 390, H - 5)
    d = classify_exit(bbox, [0.0, per_frame(H * 0.05), 0.0], W, H)   # 임계(0.02)의 2배 이상
    assert d == "down"
    assert not may_coast(d, None)


def test_area_surge_still_blocks_coasting():
    """면적 급증(코앞으로 다가와 시야를 덮음)은 가장자리와 무관하게 정지다."""
    d = classify_exit((100, 100, 500, 400), [0.0, 0.0, per_frame(EXIT_AREA_SURGE * 1.2)], W, H)
    assert d == "down"
    assert not may_coast(d, None)


# ── 임계 단위가 20배 어긋나던 회귀 (2026-08-02) ──────────────────────────────
# `BBoxSmoother.velocity` 는 px/**초**인데 `_DOWN_VY_FRAC`·`EXIT_AREA_SURGE` 는
# 주석대로 **프레임당** 값이다. 그대로 비교하면 실효 임계가 20배 예민해져
# 정상적으로 다가오는 사람도 DOWN 으로 찍히고 코스팅이 통째로 꺼진다.


def _bottom_bbox(h=240):
    return (100.0, 20.0, 200.0, float(h) - 1)


def test_ordinary_downward_jitter_is_not_a_fall():
    """발이 화면 바닥에 닿은 채 조금 떠는 것은 DOWN 이 아니다."""
    # 프레임당 1px 하강 = 초당 20px. 의도한 임계(4.8 px/frame)의 1/5 이다.
    vy_per_sec = 1.0 / FRAME_DT
    assert classify_exit(_bottom_bbox(), [0.0, vy_per_sec, 0.0], 320, 240) != "down"


def test_a_real_fall_is_still_down():
    """진짜로 빠르게 아래로 빠지면 DOWN 이어야 한다 — 게이트를 죽이면 안 된다."""
    vy_per_sec = 8.0 / FRAME_DT          # 8 px/frame — 임계(4.8) 초과
    assert classify_exit(_bottom_bbox(), [0.0, vy_per_sec, 0.0], 320, 240) == "down"


def test_ordinary_area_growth_is_not_a_surge():
    """가까워지며 면적이 조금 커지는 것은 '코앞'이 아니다."""
    varea_per_sec = (EXIT_AREA_SURGE * 0.2) / FRAME_DT   # 임계의 20%
    assert classify_exit((100.0, 60.0, 200.0, 180.0),
                         [0.0, 0.0, varea_per_sec], 320, 240) != "down"


def test_a_real_area_surge_is_still_down():
    varea_per_sec = (EXIT_AREA_SURGE * 1.5) / FRAME_DT
    assert classify_exit((100.0, 60.0, 200.0, 180.0),
                         [0.0, 0.0, varea_per_sec], 320, 240) == "down"
