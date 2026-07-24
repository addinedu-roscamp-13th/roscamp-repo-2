"""fsm_link 의 순수 로직 테스트 — rclpy 없이 돈다.

fsm_link 는 (fleet_telemetry.py 와 같은 규칙으로) 모듈 최상위에 rclpy 를 import 하지
않으므로, ROS2 를 source 하지 않은 환경에서도 import 되어야 한다. 이 테스트가 그 계약을 지킨다.
"""
import time

from app import fsm_link


def test_module_imports_without_ros2():
    """최상위에 rclpy import 가 없어야 한다 (ROS 미설치 환경 보호)."""
    import inspect

    source = inspect.getsource(fsm_link)
    header = source.split("def _empty_entry")[0]
    assert "import rclpy" not in header
    assert "from rclpy" not in header
    assert "from std_msgs" not in header


def test_apply_state_msg_folds_into_cache():
    cache = {}
    fsm_link._apply_state_msg(cache, {
        "robot_id": "pinky1",
        "current_state": "PATROL",
        "active_branch": "PATROL",
        "error_code": "",
        "battery_percent": 71.5,
        "is_docked": False,
    })
    entry = cache["pinky1"]
    assert entry["current_state"] == "PATROL"
    assert entry["active_branch"] == "PATROL"
    assert entry["battery_percent"] == 71.5
    assert entry["is_docked"] is False
    assert entry["_last_ros_at"] > 0


def test_apply_state_msg_rejects_unknown_state():
    """정의되지 않은 상태가 캐시에 들어가면 UI 가 못 그린다 — 방어한다."""
    cache = {}
    assert fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "BOGUS"}) is None
    assert "pinky1" not in cache


def test_apply_state_msg_rejects_missing_robot_id():
    cache = {}
    assert fsm_link._apply_state_msg(cache, {"current_state": "IDLE"}) is None
    assert cache == {}


def test_apply_state_msg_records_previous_state_for_edge_highlight():
    """INSTRUCTION.md: '직전에 발생한 전이의 간선을 일시 강조' — 이전 상태가 필요하다."""
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    entry = cache["pinky1"]
    assert entry["previous_state"] == "IDLE"
    assert entry["current_state"] == "PATROL"
    assert entry["transitioned_at"] > 0


def test_apply_state_msg_does_not_move_previous_on_repeat():
    """같은 상태가 주기 재발행되는 건 전이가 아니다 — 강조가 매 tick 사라지면 안 된다."""
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    assert cache["pinky1"]["previous_state"] == "IDLE"


def test_first_state_has_no_previous():
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    assert cache["pinky1"]["previous_state"] is None


def test_apply_tree_msg_stores_snapshot():
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    fsm_link._apply_tree_msg(cache, {
        "robot_id": "pinky1",
        "tree": {
            "name": "PatrolBranch",
            "status": "RUNNING",
            "children": [
                {"name": "IsMode[PATROL]", "status": "SUCCESS", "children": []},
                {"name": "PatrolNavigation", "status": "RUNNING", "children": []},
            ],
        },
    })
    tree = cache["pinky1"]["tree"]
    assert tree["name"] == "PatrolBranch"
    assert tree["children"][1]["status"] == "RUNNING"


def test_apply_tree_msg_before_any_state_still_creates_entry():
    """BT 스냅샷이 상태보다 먼저 도착해도 잃어버리지 않는다."""
    cache = {}
    fsm_link._apply_tree_msg(cache, {"robot_id": "pinky2", "tree": {"name": "X", "status": "RUNNING", "children": []}})
    assert cache["pinky2"]["tree"]["name"] == "X"
    assert cache["pinky2"]["current_state"] is None


def test_is_stale_after_timeout():
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    assert fsm_link.is_stale(cache["pinky1"]) is False
    cache["pinky1"]["_last_ros_at"] = time.time() - (fsm_link.FRESH_SEC + 1)
    assert fsm_link.is_stale(cache["pinky1"]) is True


def test_request_transition_returns_none_without_link():
    """브릿지가 없으면 즉시 None — 호출측이 '링크 없음'을 사용자에게 알릴 수 있어야 한다."""
    assert fsm_link.request_transition("pinky1", "IDLE") is None


def test_snapshot_returns_none_for_unknown_robot():
    assert fsm_link.snapshot("never-seen") is None
