"""FMS 주행 명령이 BT 까지 목적지를 싣고 오는지 — `/fleet_cmd{navigate}` 계약.

## 배경

`WorkingBranch ▸ NavigationExec` 은 오랫동안 죽은 코드였다. 이유는 "명령이 안 와서"가
아니라 **목적지를 받을 통로가 아예 없었기 때문**이다:

    "nav": FleetCmdDriver(self, "goto", args_fn=lambda: {"name": home_location})
                                                          └─ 하드코딩. 항상 home 으로만

`providers` 도 명령의 액션 이름만 보고 `args`(좌표)는 버렸다. 그래서 FMS 는 BT 를 우회해
`fleet_node → path_request_driver → nav2` 로 직접 몰았고, 로봇 FSM 은 배달 중에도
`IDLE`/`PATROL` 로 남아 관제가 "배차 가능"으로 잘못 표시했다.

## 계약

    FMS  →  /fleet_cmd {action:"navigate", args:{x,y,yaw}}      ← BT 층
              providers: active_command="navigate", nav_target={x,y,yaw}
              NavigationExec → driver.start()
    BT   →  /fleet_cmd {action:"goal", args:{x,y,yaw}}          ← 실행 층
              fleet_link → send_nav_goal → nav2

`navigate` 는 BT 소유라 실행기(`robot_agent fleet_link`)가 실행하면 안 된다 —
그러면 같은 주행이 두 번 나간다.
"""
import json

from libi_modes.ros.providers import RosProviders


class _FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(str(msg))

    def info(self, msg):
        pass


class _FakeNode:
    """RosProviders 가 생성자에서 쓰는 것만 흉내낸다 (구독 등록 · 로거 · 시계)."""

    def __init__(self):
        self._logger = _FakeLogger()
        self.subscriptions = []

    def create_subscription(self, msg_type, topic, cb, qos):
        self.subscriptions.append((topic, cb))
        return object()

    def get_logger(self):
        return self._logger

    def get_clock(self):
        class _C:
            @staticmethod
            def now():
                class _N:
                    nanoseconds = 0
                return _N()
        return _C()


class _Msg:
    def __init__(self, payload):
        self.data = json.dumps(payload)


def _providers():
    node = _FakeNode()
    return RosProviders(node), node


def _send(p, payload):
    p._on_cmd(_Msg(payload))


def test_navigate_carries_the_target():
    """FMS 가 준 좌표가 blackboard 로 갈 값(nav_target)에 담긴다."""
    p, _ = _providers()
    _send(p, {"action": "navigate", "args": {"x": 0.519, "y": -1.356, "yaw": 1.57}})

    d = p.as_dict()
    assert d["active_command"]() == "navigate"
    assert d["nav_target"]() == {"x": 0.519, "y": -1.356, "yaw": 1.57}


def test_navigate_defaults_yaw():
    p, _ = _providers()
    _send(p, {"action": "navigate", "args": {"x": 1.0, "y": 2.0}})
    assert p.as_dict()["nav_target"]()["yaw"] == 0.0


def test_navigate_without_coordinates_is_rejected():
    """좌표 없는 navigate 를 통과시키면 실행 층에서 KeyError 로 죽는다.

    여기서 막고 경고를 남긴다 — 조용히 무시하면 "왜 안 움직이지"가 된다.
    """
    p, node = _providers()
    _send(p, {"action": "navigate", "args": {}})

    d = p.as_dict()
    assert d["nav_target"]() is None
    assert d["active_command"]() is None, "실행할 수 없는 명령을 활성으로 두면 안 된다"
    assert any("좌표" in w for w in node.get_logger().warnings)


def test_execution_layer_actions_still_mark_navigation():
    """실행 층 액션(goal/goto/home/mission_start)도 '주행 중' 표시는 그대로 유지한다.

    BT 가 자기 드라이버로 goal 을 내보낼 때 이게 돌아오는데, 그때 active_command 가
    풀리면 NavigationExec 이 자기 명령을 잃는다.
    """
    for action in ("goal", "goto", "home", "mission_start"):
        p, _ = _providers()
        _send(p, {"action": action, "args": {}})
        assert p.as_dict()["active_command"]() == "navigate", action


def test_execution_layer_does_not_overwrite_the_target():
    """BT 가 goal 을 내보낸 뒤에도 목적지가 남아 있어야 재시도가 가능하다."""
    p, _ = _providers()
    _send(p, {"action": "navigate", "args": {"x": 1.0, "y": 2.0}})
    _send(p, {"action": "goal", "args": {"x": 1.0, "y": 2.0}})
    assert p.as_dict()["nav_target"]() == {"x": 1.0, "y": 2.0, "yaw": 0.0}


def test_arm_command_is_unaffected():
    p, _ = _providers()
    _send(p, {"action": "perform_action", "args": {"action": "pick"}})
    d = p.as_dict()
    assert d["active_command"]() == "perform_action"
    assert d["nav_target"]() is None


def test_transition_commands_still_go_to_last_command():
    """상태를 바꾸는 명령은 실행 커맨드가 아니라 전이 트리거다."""
    for action in ("task_assigned", "task_done", "stop_request", "resume_request"):
        p, _ = _providers()
        _send(p, {"action": action})
        d = p.as_dict()
        assert d["last_command"]() == action, action
        assert d["active_command"]() is None, action
