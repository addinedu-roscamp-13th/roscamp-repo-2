"""Mirrors upstream test_state_machine.py case-for-case, plus a guard that the
`transitions` dependency really is gone."""
import pytest

from libi_perception.switch import FollowSwitch, InvalidTransition


def test_initial_state_tracking():
    assert FollowSwitch().state == 'TRACKING'


def test_lost_then_reacquired():
    s = FollowSwitch()
    s.lost()
    assert s.state == 'SEARCHING'
    s.reacquired()
    assert s.state == 'TRACKING'


def test_search_failed_ends():
    s = FollowSwitch()
    s.lost()
    s.search_failed()
    assert s.state == 'ENDED'


def test_restart_from_ended():
    s = FollowSwitch()
    s.lost()
    s.search_failed()
    s.restart()
    assert s.state == 'TRACKING'


def test_invalid_transition_raises():
    s = FollowSwitch()
    with pytest.raises(InvalidTransition):
        s.reacquired()           # not valid from TRACKING


def test_invalid_transition_leaves_state_untouched():
    s = FollowSwitch()
    with pytest.raises(InvalidTransition):
        s.restart()
    assert s.state == 'TRACKING'


def test_no_transitions_library_dependency():
    """INSTRUCTION.md: 별도 FSM 라이브러리를 사용하지 않는다.

    Checks the import statements rather than the source text — the docstring names
    `transitions` on purpose, to record what this module replaced.
    """
    import ast
    import inspect

    import libi_perception.switch as mod

    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Import):
            imported.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])

    assert 'transitions' not in imported
    assert imported == set(), f'the switch should need no imports at all, got {imported}'
