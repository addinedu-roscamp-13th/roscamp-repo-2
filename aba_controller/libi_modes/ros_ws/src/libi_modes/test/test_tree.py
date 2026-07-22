"""Whole-tree behaviour: priority ordering, boot sequence, and the structural rules that
are easy to break later (RequestTransition placement, Selector memory, trailing Running)."""
import py_trees
from py_trees.common import Status

from libi_modes import registry, tree
from libi_modes.blackboard import Keys
from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.request_transition import RequestTransition
from test.fakes import PARAMS, FakeDriver, all_drivers, all_providers


def _walk(node):
    yield node
    for child in getattr(node, "children", []):
        yield from _walk(child)


def _branches():
    return registry.build_branches(PARAMS, all_drivers())


# ── registry ──────────────────────────────────────────────────────────────────

def test_branch_order_is_the_documented_priority():
    assert registry.BRANCH_ORDER == [
        "ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING",
        "SECURITY_PATROL", "PATROL", "IDLE",
    ]


def test_every_state_has_exactly_one_branch():
    branches = _branches()
    assert set(branches) == set(registry.BRANCH_ORDER)
    assert len(branches) == 8


def test_transition_table_covers_all_states():
    """The FMS panel renders the diagram from this table, so a state missing here would
    show up as an unreachable node in the UI."""
    states = set(registry.BRANCH_ORDER)
    pseudo = {registry.START, registry.ANY}
    named = {s for edge in registry.TRANSITIONS for s in (edge[0], edge[1]) if s not in pseudo}
    assert states == named


def test_transition_table_matches_the_18_documented_edges():
    assert len(registry.TRANSITIONS) == 20, "18 written edges, with the 3-state battery_low group expanded to 3"
    targets = {(f, t) for f, t, _ in registry.TRANSITIONS}
    assert (registry.ANY, "ERROR") in targets
    assert (registry.START, "IDLE") in targets
    assert ("ERROR", "IDLE") in targets


def test_start_and_any_are_distinct_pseudo_sources():
    """The two pseudo-sources must never collapse into one wildcard.

    The transition box writes them differently — `[*] -> IDLE` is the boot entry, while
    `(any) -> ERROR` genuinely applies from everywhere. A consumer that read one shared
    wildcard as "from every state" would expand boot into WORKING -> RETURNING and
    INTERACTING -> RETURNING: exactly the two edges the design omits so a task in flight and
    a visitor mid-conversation are never abandoned. This test is what stops that encoding
    from being reintroduced.
    """
    assert registry.START != registry.ANY
    sources = {f for f, _, _ in registry.TRANSITIONS}
    assert registry.START in sources and registry.ANY in sources, (
        "TRANSITIONS spells these out as literals so the table stays ast.literal_eval-able "
        "for the FMS drift test — this is what catches the two copies drifting apart"
    )

    boot = [(f, t, trig) for f, t, trig in registry.TRANSITIONS if f == registry.START]
    # [2026-07-22] RETURNING -> IDLE. 부팅 상태가 **조용히 바뀌면 안 되는 값**이라 여기
    # 고정해 둔다 — 로봇이 켜지자마자 무엇을 하는지가 달라진다.
    # 바꾼 근거는 main.py 의 BOOT_STATE 주석.
    assert boot == [(registry.START, "IDLE", "boot")], "boot is the only start edge"

    group = [(f, t, trig) for f, t, trig in registry.TRANSITIONS if f == registry.ANY]
    assert group == [(registry.ANY, "ERROR", "fault")], "fault is the only any-state edge"


def test_expanding_group_edges_never_opens_a_forbidden_return():
    """Expand ANY across all states the way a diagram renderer would, and confirm the
    battery_low exemptions survive."""
    states = set(registry.BRANCH_ORDER)
    edges = set()
    for source, target, _ in registry.TRANSITIONS:
        if source == registry.ANY:
            edges.update((s, target) for s in states)
        elif source == registry.START:
            continue                      # not reachable from a real state
        else:
            edges.add((source, target))

    for forbidden in (("WORKING", "RETURNING"), ("INTERACTING", "RETURNING"),
                      ("ERROR", "RETURNING"), ("CHARGING", "RETURNING")):
        assert forbidden not in edges, f"{forbidden[0]} must not gain a return edge"


# ── structural invariants ─────────────────────────────────────────────────────

def test_request_transition_is_last_child_of_every_branch_root():
    """Inside a Parallel it would be skipped whenever the action finished first, and the
    robot would silently repeat the same state forever."""
    for state, root in _branches().items():
        assert isinstance(root, py_trees.composites.Sequence), state
        assert isinstance(root.children[-1], RequestTransition), (
            f"{state}: RequestTransition must be the root Sequence's last child"
        )


def test_no_request_transition_nested_inside_a_parallel():
    for state, root in _branches().items():
        for node in _walk(root):
            if isinstance(node, py_trees.composites.Parallel):
                nested = [n for n in _walk(node) if isinstance(n, RequestTransition)]
                assert not nested, f"{state}: RequestTransition found inside {node.name}"


def test_branch_roots_have_memory_disabled():
    for state, root in _branches().items():
        assert root.memory is False, f"{state}: root Sequence must re-evaluate every tick"


def test_priority_selectors_have_memory_disabled():
    """memory=True would stop higher-priority branches from pre-empting."""
    for state, root in _branches().items():
        for node in _walk(root):
            if isinstance(node, py_trees.composites.Selector):
                assert node.memory is False, f"{state}: {node.name} must re-evaluate"


def test_watchdogs_inside_parallels_end_with_running():
    """A Selector that can return FAILURE inside a Parallel aborts the sibling action."""
    for state, root in _branches().items():
        for node in _walk(root):
            if not isinstance(node, py_trees.composites.Parallel):
                continue
            for child in node.children:
                if isinstance(child, py_trees.composites.Selector):
                    assert isinstance(child.children[-1], py_trees.behaviours.Running), (
                        f"{state}: {child.name} can fail and kill {node.name}"
                    )


def test_every_branch_except_error_checks_for_faults():
    for state, root in _branches().items():
        has_fault_check = any(isinstance(n, FaultDetected) for n in _walk(root))
        if state == "ERROR":
            assert not has_fault_check, "ERROR is already ERROR — self-transition is pointless"
        else:
            assert has_fault_check, f"{state}: missing FaultDetected"


def test_first_child_of_every_branch_is_its_mode_guard():
    from libi_modes.common.is_mode import IsMode
    for state, root in _branches().items():
        guard = root.children[0]
        assert isinstance(guard, IsMode), state
        assert guard.mode == state, f"{state}: guard checks {guard.mode}"


# ── deliberately absent edges ─────────────────────────────────────────────────

def test_working_and_interacting_ignore_battery():
    """Both are excluded from the battery_low group on purpose: a task in flight and a
    visitor mid-conversation must not be abandoned."""
    branches = _branches()
    for state in ("WORKING", "INTERACTING"):
        checks = [n for n in _walk(branches[state]) if isinstance(n, BatteryCheck)]
        assert not checks, f"{state} must not react to battery"


def test_returning_ignores_commands():
    """Stopping a robot below 15% away from the charger strands it flat."""
    listeners = [n for n in _walk(_branches()["RETURNING"]) if isinstance(n, CommandListener)]
    assert not listeners


def test_security_patrol_only_listens_for_stop():
    listeners = [n for n in _walk(_branches()["SECURITY_PATROL"]) if isinstance(n, CommandListener)]
    assert len(listeners) == 1
    assert set(listeners[0].mapping) == {"stop_request"}


def test_charging_only_exits_to_idle_or_error():
    targets = set()
    for node in _walk(_branches()["CHARGING"]):
        if isinstance(node, BatteryCheck):
            targets.add(node.next_mode)
        if isinstance(node, CommandListener):
            targets.update(node.mapping.values())
    assert targets == {"IDLE"}, "charger exit decisions are concentrated in IDLE"


# ── whole tree ────────────────────────────────────────────────────────────────

def test_tree_boots_returning_then_charging_then_idle(seed, read):
    """[*] -> RETURNING -> (docked) CHARGING -> (battery >= 40) IDLE."""
    drivers = all_drivers()
    drivers["return_dock"] = FakeDriver(["success"])
    root = tree.build_root(
        PARAMS, drivers,
        all_providers(is_docked=lambda: True, battery_percent=lambda: 55.0),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "RETURNING"})

    bt.tick()
    assert read(Keys.CURRENT_MODE) == "CHARGING", "dock succeeded"

    bt.tick()
    assert read(Keys.CURRENT_MODE) == "IDLE", "battery already past BATTERY_READY"


def test_tree_stays_alive_when_no_branch_guard_matches(seed):
    """Defensive: an unknown mode must not collapse the root to FAILURE."""
    root = tree.build_root(PARAMS, all_drivers(), all_providers())
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "__UNKNOWN__"})
    bt.tick()
    assert root.status == Status.RUNNING


def test_fault_preempts_a_lower_priority_branch(seed, read):
    """ERROR sits above everything in the Selector, but the transition still comes from
    the running branch's own FaultDetected."""
    root = tree.build_root(
        PARAMS, all_drivers(),
        all_providers(battery_percent=lambda: 60.0, fault=lambda: True),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_stopped_robot_stays_idle_then_leaves_on_resume(seed, read):
    """The documented deadlock guard, end to end: an undocked IDLE robot at 95% must not
    auto-patrol, and resume_request is its only way out."""
    command = {"value": None}
    root = tree.build_root(
        PARAMS, all_drivers(),
        all_providers(battery_percent=lambda: 95.0, is_docked=lambda: False,
                      last_command=lambda: command["value"]),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "IDLE"})

    for _ in range(5):
        bt.tick()
    assert read(Keys.CURRENT_MODE) == "IDLE", "no dock, no auto-patrol"

    command["value"] = "resume_request"
    bt.tick()
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_working_robot_is_not_pulled_away_by_low_battery(seed, read):
    root = tree.build_root(
        PARAMS, all_drivers(),
        all_providers(battery_percent=lambda: 5.0, is_docked=lambda: False),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    for _ in range(3):
        bt.tick()
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_patrol_robot_is_pulled_away_by_low_battery(seed, read):
    """The counterpart: PATROL *is* in the battery_low group."""
    root = tree.build_root(
        PARAMS, all_drivers(),
        all_providers(battery_percent=lambda: 5.0, is_docked=lambda: False),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert read(Keys.CURRENT_MODE) == "RETURNING"


def test_disabled_branch_freezes_its_behavior(seed, read):
    """[디버그] 잠긴 현재-상태 브랜치는 exit 조건도 안 돌아 그 상태에 멈춘다(안전 프리즈).

    위 test 와 동일 상황(PATROL + 저배터리 → 보통 RETURNING)인데, PATROL 을 잠그면
    IsMode 가 FAILURE → Selector 가 건너뛰어 NoBranchMatched(Running) → 전이 안 하고
    트리는 살아있다."""
    root = tree.build_root(
        PARAMS, all_drivers(),
        all_providers(battery_percent=lambda: 5.0, is_docked=lambda: False),
    )
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.DISABLED_BRANCHES: {"PATROL"}})
    for _ in range(3):
        bt.tick()
    assert root.status == Status.RUNNING            # 트리 살아있음
    assert read(Keys.CURRENT_MODE) == "PATROL"      # 전이 안 함(프리즈)
