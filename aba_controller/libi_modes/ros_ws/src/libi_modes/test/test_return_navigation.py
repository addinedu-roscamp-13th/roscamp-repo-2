"""도킹의 두 관문 — 명령 접수(driver)와 실제 도착(is_docked).

## 왜 이 파일이 생겼나

sim 에서 로봇이 **부팅 상태 RETURNING 에서 한 발짝도 못 나갔다.** `/is_docked` 를
`true` 로 발행하고 있는데도 `RETURNING → CHARGING` 이 안 됐다.

원인은 `FleetCmdDriver.poll()` 이 결과를 **한 번만** 준다는 것(`_results.pop`)이었다.
예전 `update()` 는 매 tick 다시 poll 하면서, `success` 를 받았는데 `is_docked` 가 아직
없으면 그냥 `RUNNING` 을 돌려줬다 — 그 순간 접수 사실이 사라진다. 다음 tick 부터
`poll()` 은 영원히 `"running"` 이라 게이트가 다시는 안 열린다.

타이밍상 이건 예외가 아니라 **기본값**이었다. 도킹 명령은 부팅 1초 안에 접수되는데
AMCL 이 수렴해 위치를 낼 때까지는 수십 초가 걸린다.
"""
import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.return_navigation import ReturnNavigation

from .fakes import FakeArmDriver, FakeDriver


class _Clock:
    """주입 가능한 시계 — 확인 대기 한도를 실제로 기다리지 않고 시험한다."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def leaf(seed):
    """설정을 마친 ReturnNavigation 을 만든다. `poll_sequence` 로 드라이버를 각본화한다."""

    def _make(poll_sequence, *, retry_max=3, clock=None, **blackboard):
        client = seed(**blackboard)
        node = ReturnNavigation(FakeArmDriver(), FakeDriver(poll_sequence), retry_max,
                                now_fn=clock or _Clock())
        node.setup()
        node.initialise()
        # 리프는 is_docked 를 READ 로만 잡는다. 시험 도중 신호가 늦게 오는 상황을
        # 만들려면 쓰기 권한이 있는 클라이언트가 따로 필요하다.
        node.test_bb = client
        return node

    return _make


def test_success_survives_a_late_dock_confirmation(leaf, read):
    """접수가 먼저 오고 도착 확인이 한참 뒤에 와도 SUCCESS 로 간다 — 회귀 방지.

    이게 깨지면 로봇이 RETURNING 에 영구히 갇힌다. sim 에서 실제로 그랬다.
    """
    node = leaf(["success"], **{Keys.IS_DOCKED: False})

    for _ in range(50):                       # AMCL 수렴 대기 흉내
        assert node.update() == Status.RUNNING

    node.test_bb.set(Keys.IS_DOCKED, True)
    assert node.update() == Status.SUCCESS
    assert node.dock_driver.start_count == 1, "기다리는 동안 명령을 다시 내면 안 된다"


def test_confirmation_first_then_acceptance(leaf):
    """반대 순서(확인이 먼저)도 통해야 한다."""
    node = leaf(["running", "running", "success"], **{Keys.IS_DOCKED: True})

    assert node.update() == Status.RUNNING
    assert node.update() == Status.RUNNING
    assert node.update() == Status.SUCCESS


def test_accepted_but_never_arrives_retries(leaf, read):
    """접수만 되고 도착 확인이 영영 안 오면 재시도한다.

    이게 없으면 nav2 가 충전소에 못 갔을 때와 아직 가는 중일 때가 구별되지 않아
    로봇이 조용히 서 있는다 — 진단할 단서 하나 없이.
    """
    clock = _Clock()
    node = leaf(["success", "success"], retry_max=3, clock=clock, **{Keys.IS_DOCKED: False})

    assert node.update() == Status.RUNNING
    assert node.dock_driver.start_count == 1

    clock.t = 1000.0                          # 확인 대기 한도를 넘긴다
    assert node.update() == Status.RUNNING    # 이 tick 이 실패로 판정한다
    assert read(Keys.DOCK_RETRY_COUNT) == 1

    assert node.update() == Status.RUNNING    # 다음 tick 이 새 시도를 시작한다
    assert node.dock_driver.start_count == 2, "재시도는 도킹 명령을 다시 내야 한다"


def test_retries_exhausted_raises_fault_not_failure(leaf, read):
    """재시도를 다 쓰면 fault 를 올리되 FAILURE 는 내지 않는다.

    FAILURE 를 내면 Parallel 이 즉시 무너져 형제 FaultDetected 가 그 fault 를
    전이로 바꿀 기회를 잃는다.
    """
    node = leaf(["failure", "failure"], retry_max=2)

    assert node.update() == Status.RUNNING
    assert read(Keys.FAULT) is False
    assert node.update() == Status.RUNNING
    assert read(Keys.FAULT) is True


def test_dock_failure_before_acceptance_retries(leaf, read):
    node = leaf(["failure", "success"], retry_max=3, **{Keys.IS_DOCKED: True})

    assert node.update() == Status.RUNNING
    assert read(Keys.DOCK_RETRY_COUNT) == 1
    assert node.update() == Status.SUCCESS


def test_arm_goes_home_before_the_base_moves(seed):
    """부팅 직후엔 팔 자세를 모른다 — 편 채로 달리면 책장을 친다."""
    seed()
    node = ReturnNavigation(FakeArmDriver(), FakeDriver(["running"]), 3, now_fn=_Clock())
    node.setup()

    node.initialise()
    assert node.arm_driver.went_home
    assert node.dock_driver.start_count == 0, "팔 홈이 주행보다 먼저다"

    node.update()
    assert node.dock_driver.start_count == 1


def test_terminate_clears_the_latch(leaf):
    """브랜치를 나갔다 들어오면 접수 상태가 남아 있으면 안 된다.

    남으면 다음 복귀가 도킹 명령을 아예 안 내고 곧장 확인만 기다린다 — 로봇은
    충전소 근처에도 안 갔는데.
    """
    node = leaf(["success"], **{Keys.IS_DOCKED: False})
    node.update()

    node.terminate(Status.INVALID)
    assert node._dock_accepted is False
    assert node._dock_started is False
