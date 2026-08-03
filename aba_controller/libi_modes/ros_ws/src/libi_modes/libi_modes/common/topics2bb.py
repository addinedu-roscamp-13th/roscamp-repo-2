import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys

_KEY_BY_PROVIDER = {
    "battery_percent": Keys.BATTERY_PERCENT,
    "is_docked": Keys.IS_DOCKED,
    "fault": Keys.FAULT,
    "last_command": Keys.LAST_COMMAND,
    "ui_last_touch_at": Keys.UI_LAST_TOUCH_AT,
    "active_command": Keys.ACTIVE_COMMAND,
    "command_received_at": Keys.COMMAND_RECEIVED_AT,
    "nav_target": Keys.NAV_TARGET,
    "arm_args": Keys.ARM_ARGS,
    "arm_cmd_id": Keys.ARM_CMD_ID,
    # shelf_dock/backup 명령의 args. blackboard.py 의 Keys 에는 없다(그 파일은 이번 작업
    # 범위 밖 — task-10-12-16 브리핑) — 문자열을 그대로 키로 쓴다. `providers.as_dict()`
    # 의 "exec_args" 항목과 main.py 의 `register_key(key="exec_args", ...)` 가 이 문자열로
    # 맞춘다.
    "exec_args": "exec_args",
    # 그 명령의 원래 `/fleet_cmd` id — codex P0 정정. BT 드라이버는 재발행하지 않고
    # 이 id 로 온 `/fleet_cmd_result` 만 기다린다(main.py `ExecResultWaiter`).
    "exec_cmd_id": "exec_cmd_id",
    "robot_pose": Keys.ROBOT_POSE,
    "requester_visible": Keys.REQUESTER_VISIBLE,
    "requester_seen_at": Keys.REQUESTER_SEEN_AT,
    "requester_area": Keys.REQUESTER_AREA,
    "guide_search_failed": Keys.GUIDE_SEARCH_FAILED,
    "guide_cmd_id": Keys.GUIDE_CMD_ID,
    "front_person_size": Keys.FRONT_PERSON_SIZE,
    "moving_straight": Keys.MOVING_STRAIGHT,
    "committed_node": Keys.COMMITTED_NODE,
    "committed_is_destination": Keys.COMMITTED_IS_DESTINATION,
}


class Topics2BB(py_trees.behaviour.Behaviour):
    """Pulls each provider() every tick and writes the result to the blackboard.

    Always RUNNING so it never terminates the root Parallel. Real ROS2 subscriptions are
    injected as `providers` from main.py — keeping them out of this class is what lets the
    whole tree be unit-tested without rclpy.
    """

    def __init__(self, providers: dict, name: str = "Topics2BB"):
        super().__init__(name=name)
        unknown = set(providers) - set(_KEY_BY_PROVIDER)
        if unknown:
            raise ValueError(f"unknown providers: {sorted(unknown)}")
        self.providers = providers

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        for key in _KEY_BY_PROVIDER.values():
            self.blackboard.register_key(key=key, access=Access.WRITE)

    def update(self) -> Status:
        for provider_name, fn in self.providers.items():
            self.blackboard.set(_KEY_BY_PROVIDER[provider_name], fn())
        return Status.RUNNING
