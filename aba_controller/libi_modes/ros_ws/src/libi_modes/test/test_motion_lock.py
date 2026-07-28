"""자율주행 잠금 — 상태 자체로 바퀴로 가는 문을 닫는다.

## 왜 있나

2026-07-28 하루 동안 "화면 상태와 바퀴가 다르다"는 신고가 다섯 번 나왔다:
응대중인데 굴러가고, 대기인데 굴러가고, 추종을 껐는데 제어 루프가 계속 밀었다.

원인은 매번 달랐지만 **구조는 하나**였다. `/cmd_vel` 에 발행자가 10개인데 중재자가
없었고(마지막 메시지가 이긴다), 정지는 전적으로 "각 BT leaf 가 자기 목표를 끊는 것을
기억하는가"에 달려 있었다. leaf 가 이미 끝난 뒤에는 끊을 주체 자체가 없다.

그래서 twist_mux 를 넣고, FSM 이 **상태로** 문을 닫게 했다. 여기서 지키는 것은
"어느 상태에서 잠기는가" 하나다 — 이 집합이 틀리면 로봇이 못 움직이거나(과잉),
대기 중에 굴러간다(과소).
"""
import pytest

from libi_modes.ros.state_io import MOTION_LOCKED_STATES


@pytest.mark.parametrize("state", ["IDLE", "INTERACTING", "ERROR", "CHARGING"])
def test_states_that_must_not_drive_are_locked(state):
    """로봇이 서 있어야 하는 상태.

    IDLE·INTERACTING 은 실제로 굴러간 신고가 있었고, ERROR·CHARGING 은 굴러가면
    각각 고장 확대와 충전 단자 손상으로 이어진다.
    """
    assert state in MOTION_LOCKED_STATES


@pytest.mark.parametrize("state", ["PATROL", "WORKING", "RETURNING", "SECURITY_PATROL"])
def test_states_that_must_drive_are_not_locked(state):
    """주행이 본업인 상태. 여기에 잠금이 걸리면 로봇이 아무것도 못 한다."""
    assert state not in MOTION_LOCKED_STATES


def test_the_lock_set_matches_the_transition_table():
    """전이표에 없는 상태 이름이 섞이면 **영원히 안 잠긴다** — 오타가 조용히 통과한다."""
    from libi_modes.registry import BRANCH_ORDER
    unknown = MOTION_LOCKED_STATES - set(BRANCH_ORDER)
    assert not unknown, f"전이표에 없는 상태: {unknown}"
