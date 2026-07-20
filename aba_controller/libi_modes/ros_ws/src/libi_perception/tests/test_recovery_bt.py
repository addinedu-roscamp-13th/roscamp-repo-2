"""The recovery ORDER must live in tree structure, and must not have drifted from the
timeline that was running before the decomposition."""
from types import SimpleNamespace

import py_trees

from libi_perception.recovery_bt import (
    SearchContext, create_searching_tree, tick_tree,
)
from libi_perception.search_planner import search_command


def _cfg(**over):
    base = dict(SEARCH_HOLD_SEC=10.0, SEARCH_SCAN_SEC=4.0,
                ANGULAR_Z_SEARCH=0.35, SEARCH_TURN_ANGLE=3.14159)
    base.update(over)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Pub:
    def __init__(self):
        self.calls = []

    def __call__(self, lin, ang):
        self.calls.append((lin, ang))


# ── behaviour preserved from the pre-decomposition BT_Searching ───────────────

def test_reacquire_returns_success():
    ctx = SearchContext(get_detection=lambda: object(), publish=_Pub(),
                        cfg=_cfg(), now=_Clock())
    assert tick_tree(create_searching_tree(ctx)) == py_trees.common.Status.SUCCESS


def test_scanning_publishes_rotation_and_runs():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)                  # establishes start time
    clock.t = 12.0                   # into the first scan
    assert tick_tree(root) == py_trees.common.Status.RUNNING
    assert pub.calls[-1][1] != 0.0


def test_exhausted_returns_failure():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 10_000.0
    assert tick_tree(root) == py_trees.common.Status.FAILURE


# ── the order is really in the tree, not hidden inside one leaf ───────────────

def test_recovery_order_is_tree_structure():
    ctx = SearchContext(get_detection=lambda: None, publish=_Pub(),
                        cfg=_cfg(), now=_Clock())
    root = create_searching_tree(ctx)
    phases = [c for c in root.children if c.name == 'SearchPhases'][0]
    assert [c.name for c in phases.children] == [
        'Hold', 'Scan1', 'Turn180', 'Scan2', 'GiveUp',
    ]
    assert phases.memory is True, 'a finished phase must not restart'
    assert root.memory is False, 'reacquire must be re-checked every tick'


def test_reacquire_interrupts_from_any_phase():
    clock, pub = _Clock(), _Pub()
    visible = {'v': False}
    ctx = SearchContext(get_detection=lambda: object() if visible['v'] else None,
                        publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    for t in (0.0, 12.0, 20.0):      # hold, scan1, turn180
        clock.t = t
        assert tick_tree(root) == py_trees.common.Status.RUNNING
        visible['v'] = True
        assert tick_tree(root) == py_trees.common.Status.SUCCESS, f'no interrupt at t={t}'
        visible['v'] = False


def test_giveup_stops_the_robot():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 10_000.0
    tick_tree(root)
    assert pub.calls[-1] == (0.0, 0.0), 'giving up must halt, not leave it spinning'


# ── equivalence with the reference oracle ─────────────────────────────────────

def test_angular_output_matches_search_command_over_timeline():
    """Sweeps the whole recovery at the real 20 Hz tick and asserts the decomposed tree
    publishes bit-identical angular_z to the pre-decomposition timeline function."""
    for lkd in (1.0, -1.0):
        cfg, clock, pub = _cfg(), _Clock(), _Pub()
        ctx = SearchContext(lambda: None, pub, cfg, clock, lkd=lkd)
        root = create_searching_tree(ctx)
        mismatches = []
        t = 0.0
        while t < 30.0:
            clock.t = t
            before = len(pub.calls)
            status = tick_tree(root)
            exp_ang, exp_done = search_command(t, cfg, lkd)
            if exp_done:
                assert status == py_trees.common.Status.FAILURE, f'lkd={lkd} t={t}'
            else:
                got = pub.calls[-1][1] if len(pub.calls) > before else None
                if got is None or abs(got - exp_ang) > 1e-9:
                    mismatches.append((round(t, 2), exp_ang, got))
            t += 0.05
        assert not mismatches, f'lkd={lkd} mismatches: {mismatches[:5]}'
