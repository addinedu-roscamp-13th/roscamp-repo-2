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


# ── 잠금 상태의 정지 명령 (cmd_vel_hold) ────────────────────────────────────
#
# [2026-07-29] 잠금은 아래 입력을 **막을 뿐 0 을 만들지 않는다.** 그래서 잠긴 순간
# /cmd_vel 은 침묵이고, 실제 정지는 모터 워치독(0.5초)이 했다 — **최대 0.5초는 마지막
# 속도로 굴러간다.** 잠근 주체가 0 도 같이 내도록 바꿨고, 여기서 그 계약을 지킨다.

def test_hold_priority_sits_between_lock_and_estop():
    """`cmd_vel_hold` 는 잠금(150)보다 위, 비상정지(255)보다 아래여야 한다.

    150 이하면 **자기가 건 잠금에 자기가 막혀** 0 이 안 나간다(정지가 조용히 사라진다).
    255 이상이면 비상정지를 이겨서, 누른 버튼이 FSM 에 밀린다.
    """
    import pathlib
    import yaml

    cfg = (pathlib.Path(__file__).resolve().parents[5]
           / "libi_drive_controller/ros_ws/src/pinky_pro/pinky_bringup/config/twist_mux.yaml")
    if not cfg.exists():          # 로봇 워크스페이스가 없는 체크아웃(서버 전용)에서는 건너뛴다
        pytest.skip(f"twist_mux.yaml 없음: {cfg}")

    params = yaml.safe_load(cfg.read_text())["twist_mux"]["ros__parameters"]
    hold = params["topics"]["hold"]["priority"]
    lock = params["locks"]["fsm_motion_lock"]["priority"]
    stop = params["topics"]["stop"]["priority"]

    assert lock < hold < stop, f"hold={hold} 가 lock={lock}~stop={stop} 사이가 아니다"
    assert params["topics"]["hold"]["topic"] == "cmd_vel_hold"


def test_hold_is_published_only_while_locked():
    """안 잠겼으면 **아무것도 안 낸다** — 계속 0 을 내면 추종·nav2 를 영영 막는다.

    twist_mux 는 값을 안 보고 우선순위만 본다. hold(160)가 계속 나가면 follow(100)도
    navigation(50)도 통과하지 못한다 — 로봇이 아무 데도 못 간다.
    """
    class _FakePub:
        def __init__(self): self.count = 0
        def publish(self, _msg): self.count += 1

    from libi_modes.ros.state_io import StateIO

    io = StateIO.__new__(StateIO)          # ROS 노드 없이 콜백만 시험한다
    io._hold_pub = _FakePub()

    io._locked = False
    StateIO._publish_hold(io)
    assert io._hold_pub.count == 0, "안 잠겼는데 정지 명령이 나갔다"

    io._locked = True
    StateIO._publish_hold(io)
    StateIO._publish_hold(io)
    assert io._hold_pub.count == 2
