"""Long-run mission cycle against a simulated world.

The unit tests each pin one edge with the rest of the world held still. This one lets the
world move — battery drains and charges, docking succeeds, commands arrive — and checks the
tree survives it. That is what caught CommandTimeout firing on entry to WORKING: every
single-tick test passed while a running robot went straight to ERROR.
"""
import py_trees
from py_trees.common import Access, Status

from libi_modes import tree
from libi_modes.blackboard import Keys
from test.fakes import PARAMS, FakeArmDriver, FakeDriver

TICKS = 2000
SCRIPT = {
    400: "task_assigned", 401: None,
    450: "task_done", 451: None,
    600: "ui_touch", 601: None,
    650: "ui_close", 651: None,
    700: "stop_request", 701: None,
    750: "resume_request", 751: None,
}


def _run(fault_at=None):
    world = {"battery": 12.0, "docked": False, "cmd": None, "fault": False}
    drivers = {
        "patrol": FakeDriver(), "security_patrol": FakeDriver(),
        "nav": FakeDriver(), "arm": FakeDriver(),
        "return_arm": FakeArmDriver(), "return_dock": FakeDriver(),
    }
    root = tree.build_root(PARAMS, drivers, {
        "battery_percent": lambda: world["battery"],
        "is_docked": lambda: world["docked"],
        "fault": lambda: world["fault"],
        "last_command": lambda: world["cmd"],
        "ui_last_touch_at": lambda: 0.0,
    })
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)

    writer = py_trees.blackboard.Client(name="soak-writer")
    writer.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
    writer.set(Keys.CURRENT_MODE, "RETURNING")     # boot
    reader = py_trees.blackboard.Client(name="soak-reader")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)

    seen, transitions, prev = set(), [], None
    for i in range(TICKS):
        mode = reader.get(Keys.CURRENT_MODE)
        if mode == "RETURNING":
            drivers["return_dock"]._poll_sequence = ["success"]
            world["docked"] = True   # sim world: pose reaches the charger as soon as it tries
        if mode == "CHARGING":
            world["battery"] = min(100.0, world["battery"] + 2.0)
        if mode in ("PATROL", "SECURITY_PATROL", "WORKING"):
            world["docked"] = False
            world["battery"] = max(0.0, world["battery"] - 0.35)
        if i in SCRIPT:
            world["cmd"] = SCRIPT[i]
        if fault_at is not None and i == fault_at:
            world["fault"] = True

        bt.tick()
        assert root.status != Status.FAILURE, f"tick {i}: root FAILED while in {mode}"

        mode = reader.get(Keys.CURRENT_MODE)
        seen.add(mode)
        if mode != prev:
            transitions.append((i, prev, mode))
            prev = mode

    return seen, transitions, prev


def test_mission_cycle_runs_without_wedging():
    seen, transitions, final = _run()
    assert "ERROR" not in seen, "no fault injected — ERROR means something fired spuriously"
    assert {"RETURNING", "CHARGING", "IDLE", "WORKING", "PATROL"} <= seen
    assert len(transitions) > 5, "robot should cycle, not sit in one state"


def test_charge_discharge_loop_closes():
    """PATROL drains to BATTERY_LOW, returns, charges past BATTERY_READY, patrols again —
    the loop must close rather than stall on either side."""
    _, transitions, _ = _run()
    pairs = [(a, b) for _, a, b in transitions]
    assert ("PATROL", "RETURNING") in pairs
    assert ("RETURNING", "CHARGING") in pairs
    assert ("CHARGING", "IDLE") in pairs


def test_working_survives_a_full_task_without_timing_out():
    """400 -> task_assigned, 450 -> task_done. 50 ticks in WORKING with no command in
    flight must NOT trip CommandTimeout."""
    _, transitions, _ = _run()
    pairs = [(a, b) for _, a, b in transitions]
    assert ("IDLE", "WORKING") in pairs
    assert ("WORKING", "PATROL") in pairs, "task_done should return it to patrol"
    assert ("WORKING", "ERROR") not in pairs


def test_injected_fault_reaches_error_and_stays():
    seen, transitions, final = _run(fault_at=300)
    assert final == "ERROR", "a latched fault has no automatic way out"
    assert ("ERROR", "IDLE") not in [(a, b) for _, a, b in transitions]
