"""도킹·복귀 이동 분해. 차동구동이라 옆으로 못 간다 — 회전과 직진뿐이다."""
import math

import pytest

from app.shelf.geometry import Move, approach_moves, axis_aligned_moves, return_moves, wrap_pi


def test_wrap_keeps_angles_inside_pi():
    assert math.isclose(wrap_pi(3 * math.pi), math.pi) or \
           math.isclose(wrap_pi(3 * math.pi), -math.pi)
    assert math.isclose(wrap_pi(-3 * math.pi / 2), math.pi / 2)
    assert math.isclose(wrap_pi(0.5), 0.5)


def test_straight_ahead_target_needs_no_first_turn():
    mv = approach_moves(0.0, 0.0, 0.0, 1.0, 0.0, clearance=0.0, final_yaw=0.0)
    assert mv[0] == Move("turn", 0.0)
    assert mv[1] == Move("drive", 1.0)
    assert mv[2] == Move("turn", 0.0)


def test_clearance_shortens_the_drive():
    mv = approach_moves(0.0, 0.0, 0.0, 1.0, 0.0, clearance=0.01, final_yaw=0.0)
    assert math.isclose(mv[1].value, 0.99)


def test_target_to_the_left_turns_positive():
    mv = approach_moves(0.0, 0.0, 0.0, 0.0, 1.0, clearance=0.0, final_yaw=0.0)
    assert math.isclose(mv[0].value, math.pi / 2)
    assert math.isclose(mv[1].value, 1.0)


def test_final_turn_lands_on_the_requested_yaw():
    mv = approach_moves(0.0, 0.0, 0.0, 0.0, 1.0, clearance=0.0,
                        final_yaw=math.pi)
    # 접근 방향은 +pi/2. 거기서 pi 로 가려면 +pi/2 더 돈다.
    assert math.isclose(mv[2].value, math.pi / 2)


def test_total_rotation_matches_final_yaw():
    mv = approach_moves(0.0, 0.0, 0.3, 0.2, -0.5, clearance=0.01,
                        final_yaw=3.1416)
    turned = 0.3 + mv[0].value + mv[2].value
    assert math.isclose(wrap_pi(turned - 3.1416), 0.0, abs_tol=1e-9)


def test_clearance_never_makes_the_drive_negative():
    mv = approach_moves(0.0, 0.0, 0.0, 0.005, 0.0, clearance=0.01,
                        final_yaw=0.0)
    assert mv[1].value == 0.0


def test_axis_aligned_moves_drive_x_then_y():
    moves = axis_aligned_moves(0.0, 0.0, 0.0, 0.3, -0.4, final_yaw=math.pi)
    assert [m.kind for m in moves] == ["turn", "drive", "turn", "drive", "turn"]
    assert math.isclose(moves[1].value, 0.3)
    assert math.isclose(moves[3].value, 0.4)
    assert math.isclose(moves[2].value, -math.pi / 2)


def test_return_turns_around_and_drives_the_same_distance():
    out = [Move("turn", 0.4), Move("drive", 0.25), Move("turn", -1.2)]
    back = return_moves(out, heading=0.7, final_yaw=0.7)
    assert back[0].kind == "turn"
    assert back[1] == Move("drive", 0.25)
    assert len(back) == 2


def test_return_faces_opposite_of_the_outbound_heading():
    """나갈 때 몸이 향한 최종 방향에서 180도 돌아 같은 거리를 간다.

    ⚠️ 이건 `heading == final_yaw` 인 특수 케이스에서만 참이다 — 일반적으로는
    아니다(아래 `test_out_and_back_returns_to_the_origin` 참고).
    """
    out = [Move("turn", 0.0), Move("drive", 1.0), Move("turn", 0.0)]
    back = return_moves(out, heading=0.0, final_yaw=0.0)
    assert math.isclose(abs(back[0].value), math.pi)


def test_return_of_nothing_is_nothing():
    assert return_moves([Move("turn", 0.5)], heading=1.0, final_yaw=2.0) == []


# ── 왕복 적분 시험 (R8-4, 2026-08-03 리뷰 Critical 이 요구) ──────────────────────
#
# `math.pi` 를 그냥 도는 구버전은 `final_yaw == heading` 일 때만 우연히 맞았다.
# 그래서 아래는 **그 특수 케이스 하나 + `final_yaw != heading` 인 조합 셋**을 같이
# 돌린다 — 특수 케이스만 있었다면 이 결함이 59개 시험이 전부 초록인 채로 살아
# 있었다(리뷰어 실측: 문학서가 왕복 오차 0.2448m).

@pytest.mark.parametrize("tx, ty, final_yaw", [
    (1.0, 0.0, 0.0),        # heading == final_yaw (특수 케이스 — 구버전도 통과)
    (0.2, -0.5, 3.1416),    # 문학서가 도킹과 같은 조합 — heading != final_yaw
    (-0.8, 0.6, 1.5708),
    (0.05, 0.9, -1.5708),
])
def test_out_and_back_returns_to_the_origin(tx, ty, final_yaw):
    """approach 로 나간 뒤 return 을 그대로 적분하면 출발점으로 돌아온다."""
    rx, ry, ryaw = 0.0, 0.0, 0.3
    out = approach_moves(rx, ry, ryaw, tx, ty, clearance=0.0, final_yaw=final_yaw)
    heading = math.atan2(ty - ry, tx - rx)
    back = return_moves(out, heading, final_yaw)

    x, y, yaw = rx, ry, ryaw
    for m in out + back:
        if m.kind == "turn":
            yaw = wrap_pi(yaw + m.value)
        else:
            x += m.value * math.cos(yaw)
            y += m.value * math.sin(yaw)

    assert math.isclose(x, rx, abs_tol=1e-9)
    assert math.isclose(y, ry, abs_tol=1e-9)
