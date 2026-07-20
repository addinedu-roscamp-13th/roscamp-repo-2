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
