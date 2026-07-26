"""FollowSession is the seam libi_modes' FollowExec drives, so its start/poll/stop
contract is what has to be exact."""
from libi_perception.follow_node import FollowSession


class _FakeLoop:
    def __init__(self):
        self.state = 'TRACKING'
        self.ticks = 0

    def tick(self):
        self.ticks += 1


def test_session_is_idle_before_start():
    assert FollowSession(lambda: _FakeLoop()).poll() == 'failure'


def test_started_session_reports_running_and_ticks():
    loops = []

    def factory():
        loop = _FakeLoop()
        loops.append(loop)
        return loop

    session = FollowSession(factory)
    session.start()
    assert session.poll() == 'running'
    session.tick()
    assert loops[0].ticks == 1


def test_searching_still_counts_as_running():
    """Recovery is part of following — it must not look like the command finished."""
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    loop.state = 'SEARCHING'
    assert session.poll() == 'running'


def test_session_fails_when_recovery_gives_up():
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    loop.state = 'ENDED'
    assert session.poll() == 'failure'


def test_stop_reports_success_and_halts_ticking():
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    session.stop()
    assert session.poll() == 'success'
    session.tick()
    assert loop.ticks == 0, 'a stopped session must not keep driving the robot'


def test_restart_after_stop():
    session = FollowSession(lambda: _FakeLoop())
    session.start()
    session.stop()
    session.start()
    assert session.poll() == 'running'


def test_restart_builds_a_fresh_loop():
    """Restarting must not resume the old loop's search timer or miss counter."""
    loops = []

    def factory():
        loop = _FakeLoop()
        loops.append(loop)
        return loop

    session = FollowSession(factory)
    session.start()
    session.stop()
    session.start()
    assert len(loops) == 2
    assert loops[0] is not loops[1]


def test_stop_publishes_zero_velocity():
    """관리자가 중단시키면 로봇이 실제로 멈춰야 한다.

    예전엔 _loop 만 버려서 마지막 cmd_vel 이 그대로 살아남았다.
    베이스의 cmd_vel 타임아웃에 기대면 안 된다 — 있는지 보장되지 않는다.
    """
    calls = []
    s = FollowSession(lambda: _FakeLoop(), publish=lambda lin, ang: calls.append((lin, ang)))
    s.start()
    s.stop()
    assert calls[-1] == (0.0, 0.0)


def test_stop_without_publisher_is_safe():
    """publish 를 안 준 경우(테스트/헤드리스)에도 예외 없이 동작한다."""
    s = FollowSession(lambda: _FakeLoop())
    s.start()
    s.stop()
    assert s.poll() == 'success'
