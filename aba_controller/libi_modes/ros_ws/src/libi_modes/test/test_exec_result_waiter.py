"""[codex P0] `shelf_dock`/`backup` 은 robot_agent 가 **원래 명령으로 이미 실행한다.**

BT 쪽 드라이버가 같은 액션을 재발행하면 robot_agent 가 같은 물리 동작을 두 번 돈다
(navigate → goal 처럼 BT 명령 이름과 실행 액션 이름이 갈라지지 않기 때문 —
`fleet_link.py` 의 `BT_LAYER_ACTIONS` 에 이 둘이 없다). `ExecResultWaiter` 는 재발행하지
않고 원래 명령의 id 로 온 결과만 기다려야 한다.
"""
from libi_modes.main import ExecResultWaiter


class _FakeLogger:
    def warning(self, msg):
        pass


class _FakeNode:
    def get_logger(self):
        return _FakeLogger()


class _FakeCmdPub:
    def __init__(self):
        self.published = []

    def publish_json(self, payload):
        self.published.append(payload)


def _waiter(cmd_id="fms-shelf_dock-1"):
    cmd_pub = _FakeCmdPub()
    waiter = ExecResultWaiter(_FakeNode(), cmd_pub, id_fn=lambda: cmd_id)
    return waiter, cmd_pub


def test_shelf_dock_is_not_republished():
    """`start()` 가 `/fleet_cmd` 로 shelf_dock 을 다시 내면 안 된다 — robot_agent 가
    이미 실행 중인 걸 또 실행하게 된다."""
    waiter, cmd_pub = _waiter("fms-shelf_dock-1")
    waiter.start()
    assert cmd_pub.published == [], "shelf_dock 을 재발행하면 안 된다"


def test_backup_is_not_republished():
    """같은 클래스를 backup 드라이버로도 쓴다 — 같은 계약이 적용된다."""
    waiter, cmd_pub = _waiter("fms-backup-7")
    waiter.start()
    assert cmd_pub.published == [], "backup 을 재발행하면 안 된다"


def test_result_connects_by_the_original_command_id():
    """원래 명령 id 로 온 결과가 다리를 닫아야 한다 — 안 닫히면 같은 급의 결함이다."""
    waiter, _cmd_pub = _waiter("fms-shelf_dock-1")
    waiter.start()
    assert waiter.poll() == "running"

    waiter.on_result({"id": "some-other-id", "ok": True, "msg": ""})
    assert waiter.poll() == "running", "다른 id 의 결과로 닫히면 안 된다"

    waiter.on_result({"id": "fms-shelf_dock-1", "ok": True, "msg": ""})
    assert waiter.poll() == "success"


def test_failure_result_is_reported():
    waiter, _cmd_pub = _waiter("fms-backup-7")
    waiter.start()
    waiter.on_result({"id": "fms-backup-7", "ok": False, "msg": "마커를 못 찾았다"})
    assert waiter.poll() == "failure"


def test_missing_id_fails_immediately():
    """원래 명령의 id 를 모르면 기다릴 대상이 없다 — 조용히 RUNNING 으로 매달리지 않는다."""
    waiter, cmd_pub = _waiter(cmd_id=None)
    waiter.start()
    assert waiter.poll() == "failure"
    assert cmd_pub.published == []


def test_stop_sends_a_generic_stop_not_the_same_action():
    """취소는 같은 액션을 다시 내지 않는다(그러면 또 실행된다) — 범용 stop 을 낸다."""
    waiter, cmd_pub = _waiter("fms-shelf_dock-1")
    waiter.start()
    waiter.stop()
    assert len(cmd_pub.published) == 1
    assert cmd_pub.published[0]["action"] == "stop"
    assert waiter.poll() == "failure", "취소 뒤에는 더 기다릴 것이 없다"


def test_stop_without_start_is_a_noop():
    waiter, cmd_pub = _waiter()
    waiter.stop()
    assert cmd_pub.published == []
