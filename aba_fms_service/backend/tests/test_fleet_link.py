"""fleet_link 의 순수 함수 테스트 — rclpy 없이 돈다.

ROS 콜백은 이 함수들을 감싸기만 하므로, 캐시에 무엇이 어떻게 접히는지는 여기서 다 잡힌다.
"""
import time

from app import fleet_link


def test_task_state_marks_robot_busy_while_running():
    robots, tasks = {}, []
    for state in ("ASSIGNED", "EXECUTING"):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": "t1", "state": state, "robot_id": "pinky1", "progress": 0.4,
        })
        assert robots["pinky1"]["busy"] is True, state


def test_task_state_clears_busy_when_finished():
    """COMPLETED/FAILED/REJECTED 는 끝난 것이므로 다시 배차 후보가 되어야 한다."""
    robots, tasks = {}, []
    fleet_link.apply_task_state(robots, tasks, {
        "task_id": "t1", "state": "EXECUTING", "robot_id": "pinky1", "progress": 0.5,
    })
    for state in ("COMPLETED", "FAILED", "REJECTED"):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": "t1", "state": state, "robot_id": "pinky1", "progress": 1.0,
        })
        assert robots["pinky1"]["busy"] is False, state


def test_task_state_without_robot_is_ignored():
    robots, tasks = {}, []
    assert fleet_link.apply_task_state(robots, tasks, {"task_id": "t", "state": "ASSIGNED"}) is None
    assert robots == {} and tasks == []


def test_task_history_is_capped():
    """이력이 무한히 자라면 스냅샷이 매번 커진다 — WebSocket push 마다 실려 나가므로."""
    robots, tasks = {}, []
    for i in range(fleet_link.TASK_HISTORY_MAX + 20):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": f"t{i}", "state": "ASSIGNED", "robot_id": "pinky1", "progress": 0.0,
        })
    assert len(tasks) == fleet_link.TASK_HISTORY_MAX
    assert tasks[-1]["task_id"] == f"t{fleet_link.TASK_HISTORY_MAX + 19}"


def test_robot_state_records_location():
    robots = {}
    assert fleet_link.apply_robot_state(robots, {
        "name": "pinky2", "location": {"x": 1.5, "y": -2.0},
    }) == "pinky2"
    assert robots["pinky2"]["x"] == 1.5
    assert robots["pinky2"]["y"] == -2.0


def test_robot_state_does_not_import_sim_battery():
    """fleet_node 도 sim 배터리를 무시하고 내부값을 쓴다. 여기서 sim 값을 들이면
    화면 숫자와 배차 판정에 쓰이는 값이 달라진다."""
    robots = {}
    fleet_link.apply_robot_state(robots, {
        "name": "pinky1", "location": {"x": 0, "y": 0}, "battery_percent": 42.0,
    })
    assert "battery" not in robots["pinky1"]


def test_parse_json_map_survives_broken_frames():
    assert fleet_link.parse_json_map('{"3":"pinky1"}') == {"3": "pinky1"}
    assert fleet_link.parse_json_map("not json") == {}
    assert fleet_link.parse_json_map("[1,2]") == {}, "리스트는 맵이 아니다"


def test_rows_mark_unset_state_and_battery_as_unknown():
    """fleet_node 는 상태·배터리를 발행하지 않는다. 설정 전에는 '모른다'로 보여야지,
    기본값을 읽어온 값처럼 보여주면 안 된다."""
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(robots, {}, {}, {})
    assert rows[0]["state"] is None
    assert rows[0]["state_source"] == "unknown"
    assert rows[0]["battery_source"] == "unknown"


def test_rows_label_panel_set_values_as_panel_sourced():
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    echo = {"pinky1": {"state": "PATROL", "battery": 55.0}}
    rows = fleet_link.build_rows(robots, echo, {}, {})
    assert rows[0]["state"] == "PATROL"
    assert rows[0]["state_source"] == "panel"
    assert rows[0]["battery"] == 55.0
    assert rows[0]["battery_source"] == "panel"


def test_rows_prefer_live_fsm_state_over_panel():
    """로봇이 실제로 발행한 상태가 정본이다.

    fleet_node 도 on_fsm_state 에서 robot_mode_ 를 무조건 덮어쓴다. 패널 값을 우선하면
    화면엔 PATROL 인데 fleet_node 는 RETURNING 으로 알고 배차를 안 하는, 추적 불가능한
    불일치가 생긴다.
    """
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    echo = {"Pinkysim": {"state": "PATROL", "battery": 55.0}}
    fsm = {"Pinkysim": {"current_state": "RETURNING", "battery_percent": 88.0}}
    rows = fleet_link.build_rows(robots, echo, {}, {}, fsm)
    assert rows[0]["state"] == "RETURNING"
    assert rows[0]["state_source"] == "fsm"
    assert rows[0]["battery"] == 88.0
    assert rows[0]["battery_source"] == "fsm"


def test_rows_fill_state_from_fsm_without_any_panel_setting():
    """회귀 방지 — 예전엔 패널에서 손으로 설정해야만 state 가 찼다. 그래서 로봇이 멀쩡히
    상태를 발행해도 표는 늘 비었고 '배차 가능 0 대'로 보였다."""
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    fsm = {"Pinkysim": {"current_state": "PATROL", "battery_percent": None}}
    rows = fleet_link.build_rows(robots, {}, {}, {}, fsm)
    assert rows[0]["state"] == "PATROL"
    assert rows[0]["state_source"] == "fsm"
    assert rows[0]["battery_source"] == "unknown", "배터리는 안 왔으면 여전히 모른다"


def test_rows_ignore_stale_fsm_and_fall_back_to_panel():
    """수신이 끊긴 FSM 캐시를 현재 상태처럼 보여주면 안 된다."""
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    echo = {"Pinkysim": {"state": "IDLE"}}
    fsm = {"Pinkysim": {"current_state": "WORKING", "stale": True}}
    rows = fleet_link.build_rows(robots, echo, {}, {}, fsm)
    assert rows[0]["state"] == "IDLE"
    assert rows[0]["state_source"] == "panel"


def test_rows_without_fsm_argument_behave_as_before():
    """fsm 인자는 선택이다 — 안 주면 예전 동작 그대로."""
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(robots, {"pinky1": {"state": "IDLE"}}, {}, {})
    assert rows[0]["state_source"] == "panel"


def test_rows_include_robots_known_only_from_echo():
    """관측 전에 모드를 밀어 넣은 로봇도 표에 나와야 한다 — 안 그러면 '설정했는데 사라짐'."""
    rows = fleet_link.build_rows({}, {"pinky3": {"state": "IDLE"}}, {}, {})
    assert [r["name"] for r in rows] == ["pinky3"]


def test_rows_attach_goal_and_held_nodes():
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(
        robots, {}, {"pinky1": 7}, {"3": "pinky1", "4": "pinky2", "5": "pinky1"},
    )
    assert rows[0]["goal_vertex"] == 7
    assert rows[0]["held_nodes"] == [3, 5], "다른 로봇 점유는 섞이지 않는다"


def test_rows_flag_stale_feed():
    fresh = {"a": dict(fleet_link._empty_robot(), name="a", _last_ros_at=time.time())}
    old = {"b": dict(fleet_link._empty_robot(), name="b", _last_ros_at=1.0)}
    assert fleet_link.build_rows(fresh, {}, {}, {})[0]["stale"] is False
    assert fleet_link.build_rows(old, {}, {}, {})[0]["stale"] is True


def test_state_validation_uses_libi_modes_vocabulary():
    for state in ("IDLE", "PATROL", "WORKING", "ERROR", "RETURNING",
                  "CHARGING", "INTERACTING", "SECURITY_PATROL"):
        assert fleet_link.is_valid_state(state), state
    for bad in ("STOP", "CHARGE", "idle", ""):
        assert not fleet_link.is_valid_state(bad), bad


def test_service_calls_fail_closed_without_a_link():
    """링크가 없을 때 조용히 성공한 척하면 안 된다 — 사유를 달고 실패해야 한다."""
    assert fleet_link.submit_task("3")["accepted"] is False
    assert fleet_link.set_robot_mode("pinky1", "IDLE")["ok"] is False
    assert fleet_link.set_battery("pinky1", 50.0)["ok"] is False
    assert fleet_link.set_plugins("", "")["ok"] is False
    assert fleet_link.reload_navgraph()["ok"] is False


def test_bad_mode_is_rejected_before_reaching_the_fleet():
    assert fleet_link.set_robot_mode("pinky1", "STOP")["reason"] == "bad_mode"
