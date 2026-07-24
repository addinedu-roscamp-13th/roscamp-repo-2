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
