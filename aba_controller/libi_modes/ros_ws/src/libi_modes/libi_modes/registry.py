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

#: FSM 전이를 일으키는 명령 이름 **전부**. 브랜치들의 `_COMMAND_MAP` 키 합집합이다.
#
# ⚠️ **왜 화이트리스트가 필요한가** — `/fleet_cmd` 는 FSM 과 실행층(robot_agent)이 **같이 쓰는
#    토픽**이다. 그래서 `mission_stop`·`slam_save_map`·`dock` 처럼 FSM 과 무관한 액션도 같은
#    토픽으로 흐른다. 예전에는 분류 안 된 액션이 전부 FSM 의 **단일 명령 슬롯**
#    (`ros/providers.py` `_last_command`)에 들어갔고, 그러면 아직 소비되지 않은 전이 트리거를
#    덮어써 그 전이가 **조용히 사라진다.**
#
#    실측 2026-07-30: 주문 취소가 `task_failed` 직후(마이크로초 간격) `mission_stop` 을 쏜다
#    (`fleet_orchestrator.py:361-362`). BT tick 은 100ms 라 둘이 같은 tick 에 들어오고,
#    `mission_stop` 이 슬롯을 덮어 **WORKING→PATROL 복귀가 유실됐다.**
#
# ⚠️ 새 트리거를 브랜치에 추가하면 **여기도 넣어야 한다.** 안 넣으면 그 명령은 FSM 에 영영
#    도달하지 않는다. `test/test_fleet_cmd_slot.py` 가 이 집합과 브랜치 맵의 일치를 지킨다.
TRANSITION_TRIGGERS = frozenset({
    "task_assigned", "task_done", "task_failed", "stop_request",
    "ui_touch", "ui_close", "resume_request", "security_patrol_request",
    "recovered",
})

TRANSITIONS = [
    # [2026-07-22] RETURNING -> IDLE. 부팅 상태로 RETURNING 을 쓰면 켜자마자 AMCL 수렴
    # 전에 주행을 시작하고, 충전소에 이미 있어도 왕복한다. 팔 자세 문제는 부팅 시
    # 팔 홈 복귀를 한 번 보내는 것으로 따로 푼다 (main.py `_boot_arm_home`).
    ("[*]", "IDLE", "boot"),
    ("RETURNING", "CHARGING", "docked"),
    ("CHARGING", "IDLE", "battery_charged [battery >= 40%]"),
    ("IDLE", "PATROL", "resume_request (auto [battery >= 80% && is_docked] / manual)"),
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
    ("SECURITY_PATROL", "IDLE", "stop_request"),
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
        "RETURNING": returning.create(
            params,
            entrance_driver=drivers["return_entrance"],
            dock_driver=drivers["return_dock"],
            rotate_driver=drivers["return_rotate"],
            entrance_xy=drivers["return_entrance_xy"],
            parking_xy=drivers["return_parking_xy"]),
        "CHARGING": charging.create(params),
        "WORKING": working.create(params, drivers["nav"], drivers["arm"],
                                  drivers.get("follow"),
                                  drivers.get("guide"), drivers.get("guide_stop"),
                                  drivers.get("guide_watch"),
                                  junctions=drivers.get("junctions")),
        "INTERACTING": interacting.create(params),
        "SECURITY_PATROL": security_patrol.create(params, drivers["security_patrol"]),
        "PATROL": patrol.create(params, drivers["patrol"]),
        "IDLE": idle.create(params),
    }
