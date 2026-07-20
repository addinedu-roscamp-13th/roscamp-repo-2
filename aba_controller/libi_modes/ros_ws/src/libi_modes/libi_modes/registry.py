"""State -> branch factory mapping, in Priorities-selector order.

This ordering is the single source of truth for "which state, which subtree". The FMS
panel renders from it too — it must not keep a second copy, or the diagram and the robot
will drift apart silently.
"""

from libi_modes.branches import (
    charging, error, idle, interacting, patrol, returning, security_patrol, working,
)

BRANCH_ORDER = [
    "ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING",
    "SECURITY_PATROL", "PATROL", "IDLE",
]

# The transition box, as data. Each entry: (from_state, to_state, trigger).
#
# Two pseudo-sources, spelled exactly as the transition box spells them:
#   START ("[*]")  the boot entry point — NOT reachable from any real state
#   ANY   ("(any)") a genuine group edge that does apply from every state
#
# Keeping them distinct is load-bearing. Collapsing both to a single wildcard and
# expanding it as "from every state" would open WORKING -> RETURNING and
# INTERACTING -> RETURNING, the two edges the design deliberately omits so a task in
# flight and a visitor mid-conversation are never abandoned on a low battery.
# Written out as literals below rather than as these names, so the table stays readable by
# ast.literal_eval — the FMS drift test parses this file without importing it, to avoid
# dragging py_trees into a service that does not need it.
START = "[*]"
ANY = "(any)"

TRANSITIONS = [
    ("[*]", "RETURNING", "boot"),
    ("RETURNING", "CHARGING", "docked"),
    ("CHARGING", "IDLE", "battery_charged [battery >= 40%]"),
    ("IDLE", "PATROL", "patrol_request (auto [battery >= 80% && is_docked] / manual)"),
    ("IDLE", "WORKING", "task_assigned"),
    ("IDLE", "SECURITY_PATROL", "security_patrol_request"),
    ("PATROL", "WORKING", "task_assigned"),
    ("PATROL", "INTERACTING", "ui_touch"),
    ("PATROL", "IDLE", "stop_request"),
    ("INTERACTING", "PATROL", "ui_idle_timeout / ui_close"),
    ("INTERACTING", "WORKING", "task_assigned"),
    ("INTERACTING", "IDLE", "stop_request"),
    ("WORKING", "PATROL", "task_done / task_failed"),
    ("WORKING", "IDLE", "stop_request"),
    ("SECURITY_PATROL", "IDLE", "security_patrol_complete / stop_request"),
    ("IDLE", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    ("PATROL", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    ("SECURITY_PATROL", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    ("(any)", "ERROR", "fault"),
    ("ERROR", "IDLE", "recovered"),
]


def build_branches(params: dict, drivers: dict) -> dict:
    """Instantiates all 8 branches. `drivers` supplies one hardware client per branch that
    needs one; branches without hardware (IDLE, CHARGING, ERROR, INTERACTING) take none.

    "follow" is optional — a deployment without libi_perception still builds a valid tree,
    and dispatching follow_admin without it raises a named error rather than hanging."""
    return {
        "ERROR": error.create(params),
        "RETURNING": returning.create(params, drivers["return_arm"], drivers["return_dock"]),
        "CHARGING": charging.create(params),
        "WORKING": working.create(params, drivers["nav"], drivers["arm"],
                                  drivers.get("follow")),
        "INTERACTING": interacting.create(params),
        "SECURITY_PATROL": security_patrol.create(params, drivers["security_patrol"]),
        "PATROL": patrol.create(params, drivers["patrol"]),
        "IDLE": idle.create(params),
    }
