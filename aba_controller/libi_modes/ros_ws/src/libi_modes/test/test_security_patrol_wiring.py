"""야간 순찰 브랜치 배선 — 잎이 제자리에 있고, 기존 호출 방식이 그대로 통하는지."""
import py_trees

from libi_modes.branches import security_patrol
from test.fakes import PARAMS, FakeDriver


def _names(root):
    return {n.name for n in root.iterate()}


def _gate():
    return py_trees.behaviours.Success(name="undock_gate")


def test_기존_인자_조합_그대로_만들_수_있다():
    """registry.py:146 이 부르는 형태를 깨면 안 된다."""
    root = security_patrol.create(
        PARAMS, FakeDriver(), undock_gate=_gate(),
        person_stop_driver=FakeDriver(), block_fn=lambda *_a: None,
        camera_driver=lambda _v: None)
    assert root is not None


def test_침입_추종_잎과_앞캠_유지_잎이_들어간다():
    root = security_patrol.create(
        PARAMS, FakeDriver(), undock_gate=_gate(),
        camera_driver=lambda _v: None, follow_driver=FakeDriver())
    names = _names(root)
    assert "IntruderChase" in names
    assert "CameraSelectRenew" in names


def test_사람에_의한_정지는_야간에_없다():
    """추종이 그 자리를 대신한다. 둘을 같이 두면 /cmd_vel 을 두고 싸운다."""
    root = security_patrol.create(
        PARAMS, FakeDriver(), undock_gate=_gate(),
        person_stop_driver=FakeDriver(), camera_driver=lambda _v: None,
        follow_driver=FakeDriver())
    assert "PersonBlockGuard" not in _names(root)


def test_추종_잎이_순찰_주행보다_앞에_있다():
    """Selector 의 첫 자식이어야 침입 시 순찰을 선점한다."""
    root = security_patrol.create(
        PARAMS, FakeDriver(), undock_gate=_gate(),
        camera_driver=lambda _v: None, follow_driver=FakeDriver())
    sel = next(n for n in root.iterate() if n.name == "PatrolOrChase")
    assert sel.children[0].name == "IntruderChase"


def test_설정에_security_patrol_블록이_없어도_안_죽는다():
    """params.yaml 을 안 고친 로봇에서 KeyError 가 나면 fsm_node 가 통째로 죽는다."""
    params = {k: v for k, v in PARAMS.items()}
    assert "security_patrol" not in params
    root = security_patrol.create(
        params, FakeDriver(), undock_gate=_gate(),
        camera_driver=lambda _v: None, follow_driver=FakeDriver())
    assert root is not None
