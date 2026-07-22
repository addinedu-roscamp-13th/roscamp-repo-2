"""주행은 **도착**에서 끝난다 — 명령 접수에서 끝나지 않는다.

## 왜 이 파일이 생겼나

`NavigationExec` 은 드라이버 결과(`/fleet_cmd_result`)로 끝났다. 그런데 그 결과는
`ros_bridge.send_nav_goal()` 이 **완료를 안 기다리고** 돌려주는 접수 응답이다.
20초짜리 주행이 BT 상으로는 0.2초 만에 끝난 일이 됐고, 그래서:

- 관제 화면이 주행 내내 `AwaitingCommand` 를 보여줬다 (실측)
- `CommandTimeout` 이 실제 주행이 아니라 접수 왕복만 지켰다
- **도착 없이 끝난 주행을 아무도 못 알아챘다** — 로봇이 6분 40초를 서 있었다 (실측)

`ReturnNavigation` 이 도킹에서 이미 푼 문제와 같다: "명령 접수됨"을 "도착함"으로
믿지 않는다. 도킹은 `is_docked`, 주행은 `robot_pose` 를 근거로 삼는다.
"""
import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.working_actions import NavigationExec

from .fakes import FakeDriver

TOLERANCE = 0.05
RESEND = 10.0
TIMEOUT = 60.0


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def leaf(seed):
    def _make(poll_sequence=("running",), *, clock=None, **blackboard):
        client = seed(**blackboard)
        node = NavigationExec(FakeDriver(poll_sequence), TOLERANCE, RESEND, TIMEOUT,
                              now_fn=clock or _Clock())
        node.setup()
        node.initialise()
        node.test_bb = client       # 리프는 목적지·위치를 READ 로만 잡는다
        return node

    return _make


def _at(x, y):
    return {"x": x, "y": y}


def _to(x, y):
    return {"x": x, "y": y, "yaw": 0.0}


def test_stays_running_while_the_robot_is_still_driving(leaf):
    """접수 응답이 와도 도착 전이면 RUNNING 이다 — 이게 이 클래스의 존재 이유다."""
    node = leaf(["success"], **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    for _ in range(20):
        assert node.update() == Status.RUNNING


def test_succeeds_when_the_robot_reaches_the_target(leaf):
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate",
                   Keys.NAV_TARGET: _to(1.0, 0.0),
                   Keys.ROBOT_POSE: _at(0.0, 0.0)})
    assert node.update() == Status.RUNNING

    node.test_bb.set(Keys.ROBOT_POSE, _at(0.97, 0.0))       # 0.03m — 허용 안
    assert node.update() == Status.SUCCESS
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None, "다음 명령을 받으려면 슬롯을 비운다"


def test_unknown_position_is_not_arrival(leaf):
    """위치를 모르면 도착이 아니다. 모르는 걸 도착으로 치면 로봇을 두고 다음으로 넘어간다."""
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate", Keys.NAV_TARGET: _to(0.0, 0.0)})
    assert node.update() == Status.RUNNING


def test_new_target_flows_through_without_stopping(leaf):
    """목적지가 바뀌면 그대로 이어서 보낸다 — 노드마다 멈추지 않게 하는 지점이다.

    관제는 로봇이 arrive_radius 안에 들어오면 **nav2 가 마지막 몇 cm 를 좁히는 중에**
    다음 노드를 허가한다. 그러니 목적지 변경은 예외가 아니라 정상 흐름이다.
    """
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate",
                   Keys.NAV_TARGET: _to(1.0, 0.0),
                   Keys.ROBOT_POSE: _at(0.0, 0.0)})
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 1

    node.test_bb.set(Keys.NAV_TARGET, _to(2.0, 0.0))        # 다음 노드
    assert node.update() == Status.RUNNING, "목적지가 바뀌었다고 주행이 끊기면 안 된다"
    assert node.driver.start_count == 2


def test_same_target_is_re_driven_after_the_resend_window(leaf):
    """도착 못 한 채 시간이 지나면 같은 목적지로 다시 몬다.

    nav2 주행은 도착 없이 끝날 수 있다(ABORTED, 또는 선점 순간 직전 목표의 완료가
    새 목표의 완료로 보고되는 경우). 재전송을 **BT 가** 하는 이유는, 도착했는지
    아는 게 BT 뿐이기 때문이다 — FMS 가 시간만 보고 보내면 정상 주행에도 끼어든다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    assert node.driver.start_count == 1

    clock.t = RESEND - 0.1
    node.update()
    assert node.driver.start_count == 1, "정상 주행 중에 끼어들면 nav2 목표가 선점된다"

    clock.t = RESEND
    node.update()
    assert node.driver.start_count == 2


def test_gives_up_after_the_arrive_timeout(leaf):
    """영영 못 가는 것과 아직 가는 중인 것을 구별한다.

    FAILURE 를 내야 dispatch Selector 가 흘러가고, 새 명령이 안 오면
    CommandTimeout 이 로봇을 ERROR 로 데려간다. 조용히 서 있는 게 제일 나쁘다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    clock.t = TIMEOUT
    assert node.update() == Status.FAILURE
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None


def test_arrive_timeout_restarts_on_a_new_target(leaf):
    """긴 주행이 중간 노드를 지날 때마다 시간이 다시 시작돼야 한다.

    안 그러면 노드가 많은 다리는 도착도 못 했는데 타임아웃에 걸린다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    clock.t = TIMEOUT - 1
    node.test_bb.set(Keys.NAV_TARGET, _to(2.0, 0.0))
    node.update()

    clock.t = TIMEOUT + 1
    assert node.update() == Status.RUNNING, "새 목적지부터 다시 센다"


def test_target_without_coordinates_releases_the_slot(leaf):
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate"})
    assert node.update() == Status.FAILURE
    assert node.driver.start_count == 0
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None


def test_other_commands_fall_through(leaf):
    """자기 명령이 아니면 즉시 FAILURE — Selector 가 다음 처리기로 넘어가는 방식이다."""
    node = leaf(**{Keys.ACTIVE_COMMAND: "perform_action"})
    assert node.update() == Status.FAILURE
    assert node.driver.start_count == 0
