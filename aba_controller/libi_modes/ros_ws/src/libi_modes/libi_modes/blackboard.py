"""Blackboard key name constants shared by every common node and branch."""


class Keys:
    CURRENT_MODE = "current_mode"
    NEXT_MODE = "next_mode"
    FAULT = "fault"
    BATTERY_PERCENT = "battery_percent"
    IS_DOCKED = "is_docked"
    LAST_COMMAND = "last_command"
    UI_LAST_TOUCH_AT = "ui_last_touch_at"
    DRIVE_LOCK = "drive_lock"
    ARM_LOCK = "arm_lock"
    ACTIVE_COMMAND = "active_command"
    COMMAND_RECEIVED_AT = "command_received_at"
    DOCK_RETRY_COUNT = "dock_retry_count"
    ERROR_CODE = "error_code"
    # [디버그] 잠긴 상태 브랜치 집합. IsMode 가 여기 든 상태면 FAILURE → Selector 가 건너뜀.
    # main.py 가 param/env 로 1회 seed. 비어있으면(기본) 동작 변화 없음.
    DISABLED_BRANCHES = "disabled_branches"


def get(blackboard, key, default=None):
    """py_trees blackboard.get() raises KeyError for a never-written key.

    Branches tick from boot before Topics2BB has necessarily populated every key,
    so every read in this package goes through here — a missing key means
    "not known yet", never a crash.
    """
    try:
        return blackboard.get(key)
    except KeyError:
        return default
