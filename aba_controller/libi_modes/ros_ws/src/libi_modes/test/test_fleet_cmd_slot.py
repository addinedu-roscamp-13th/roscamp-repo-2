"""`/fleet_cmd` 는 FSM 과 실행층(robot_agent)이 **같이 쓰는 토픽**이다.

실행층 몫의 액션이 FSM 의 **단일 명령 슬롯**을 덮으면 아직 소비되지 않은 전이 트리거가
사라지고, 그 전이는 로그도 없이 유실된다.

실측 2026-07-30 — 주문 취소가 두 명령을 마이크로초 간격으로 쏜다
(`fleet_orchestrator.py:361-362` → `task_failed` 직후 `mission_stop`).
BT tick 은 100ms 라 둘이 같은 tick 에 들어오고, 예전 코드에서는 `mission_stop` 이 슬롯을
덮어 **WORKING → PATROL 복귀가 유실됐다.** 증상은 "가끔 씹힌다"로 보인다 — 두 명령이
같은 tick 에 걸리는지가 타이밍에 달려 있기 때문이다.
"""
import importlib
import json
import pkgutil
from types import SimpleNamespace

import py_trees
from py_trees.common import Status

import libi_modes.branches as branches_pkg
from libi_modes.blackboard import Keys
from libi_modes.branches import working
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.request_transition import RequestTransition
from libi_modes.registry import TRANSITION_TRIGGERS
from libi_modes.ros.providers import RosProviders


def _providers():
    """`__init__` 우회 — ROS 노드 없이 콜백만 검증한다(test_providers_touch.py 와 같은 방식)."""
    p = RosProviders.__new__(RosProviders)
    p._command_received_at = 0.0
    p._nav_actions = {"navigate"}
    p._guide_actions = {"guide"}
    p._mission_actions = {"goal", "goto", "home", "mission_start"}
    p._arm_actions = {"perform_action"}
    p._follow_actions = {"follow_admin"}
    p._fsm_triggers = set(TRANSITION_TRIGGERS)
    p._active_command = None
    p._last_command = None
    p._nav_target = None
    p._log = SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None)
    return p


def _send(p, action, **args):
    RosProviders._on_cmd(p, SimpleNamespace(
        data=json.dumps({"action": action, "args": args})))


def test_trigger_set_matches_the_branch_maps():
    """⚠️ 화이트리스트가 브랜치 맵과 어긋나면 그 명령은 FSM 에 **영영 도달하지 않는다.**

    새 트리거를 브랜치에 넣고 `registry.TRANSITION_TRIGGERS` 를 안 고치면 여기서 걸린다.
    """
    union = set()
    for m in pkgutil.iter_modules(branches_pkg.__path__):
        mod = importlib.import_module(f"libi_modes.branches.{m.name}")
        union |= set(getattr(mod, "_COMMAND_MAP", {}))
    assert union == set(TRANSITION_TRIGGERS)


def test_execution_layer_action_does_not_touch_the_slot():
    p = _providers()
    _send(p, "task_failed")
    _send(p, "mission_stop")        # robot_agent 몫 — FSM 슬롯을 건드리면 안 된다
    assert p._last_command == "task_failed"


def test_unknown_action_does_not_touch_the_slot():
    """실행층 액션은 계속 늘어난다(dock·slam_*·waypoint_*…). 모르는 이름은 무시가 기본이다."""
    p = _providers()
    _send(p, "stop_request")
    for noise in ("slam_save_map", "waypoint_goto", "dock", "loc_set", "schedule_stop"):
        _send(p, noise)
    assert p._last_command == "stop_request"


def test_real_triggers_still_reach_the_slot():
    p = _providers()
    for trigger in sorted(TRANSITION_TRIGGERS):
        p._last_command = None
        _send(p, trigger)
        assert p._last_command == trigger, trigger


def test_cancel_pair_still_reaches_patrol(seed, tick, read):
    """증상 그대로 — 취소 두 명령을 한 tick 안에 받고도 WORKING -> PATROL 이 일어난다."""
    p = _providers()
    _send(p, "task_failed")
    _send(p, "mission_stop")
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: p._last_command})
    root = py_trees.composites.Sequence(name="WorkingExit", memory=False, children=[
        CommandListener(working._COMMAND_MAP),   # 실제 브랜치 매핑을 그대로 쓴다
        RequestTransition(),
    ])
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"
