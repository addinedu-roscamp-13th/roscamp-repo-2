"""소실 방향 분류. 이미지 좌표(y 는 아래로 증가)."""
from follower_perception.exit_direction import classify_exit, may_coast

W, H = 640, 480     # 마진 8% → x 51.2px, y 38.4px


def test_left_edge_is_side():
    assert classify_exit((0, 100, 40, 400), [-50.0, 0.0, 0.0], W, H) == "side"


def test_right_edge_is_side():
    assert classify_exit((600, 100, 639, 400), [50.0, 0.0, 0.0], W, H) == "side"


def test_bottom_edge_is_down():
    assert classify_exit((200, 200, 400, 479), [0.0, 60.0, 0.0], W, H) == "down"


def test_top_edge_is_up():
    assert classify_exit((200, 0, 400, 200), [0.0, -60.0, 0.0], W, H) == "up"


def test_area_surge_is_down_without_touching_any_edge():
    """코앞으로 다가와 시야를 덮는 경우 — 가장자리 검사로는 못 잡는다."""
    assert classify_exit((150, 150, 500, 400), [0.0, 5.0, 9000.0], W, H) == "down"


def test_middle_is_center():
    assert classify_exit((280, 200, 360, 300), [2.0, 1.0, 0.0], W, H) == "center"


def test_bottom_and_side_together_prefers_down():
    """모서리로 빠질 때 '옆'으로 읽으면 코앞의 대상을 향해 계속 전진한다."""
    assert classify_exit((0, 300, 60, 479), [-40.0, 40.0, 0.0], W, H) == "down"


def test_top_and_side_together_prefers_up():
    assert classify_exit((0, 0, 60, 180), [-40.0, -40.0, 0.0], W, H) == "up"


def test_bottom_edge_moving_up_is_not_down():
    """바닥에 닿아 있어도 **올라오는 중**이면 사라지는 방향이 아래가 아니다."""
    assert classify_exit((200, 200, 400, 479), [0.0, -60.0, 0.0], W, H) != "down"


def test_side_edge_but_vertical_motion_is_center():
    """가장자리에 있어도 주된 이동이 세로면 옆으로 빠진 게 아니다."""
    assert classify_exit((0, 200, 40, 300), [5.0, 40.0, 0.0], W, H) == "center"


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


def test_non_standing_blocks_regardless_of_direction():
    """쓰러지는 중이던 대상을 예측 위치로 쫓아가는 것이 바로 피하려던 상황이다."""
    assert may_coast("side", "Lying") is False
    assert may_coast("center", "Unknown") is False


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
    d = classify_exit(bbox, [0.0, H * 0.05, 0.0], W, H)   # 임계(0.02)의 2배 이상
    assert d == "down"
    assert not may_coast(d, None)


def test_area_surge_still_blocks_coasting():
    """면적 급증(코앞으로 다가와 시야를 덮음)은 가장자리와 무관하게 정지다."""
    d = classify_exit((100, 100, 500, 400), [0.0, 0.0, 9000.0], W, H)
    assert d == "down"
    assert not may_coast(d, None)
