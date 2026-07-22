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
    # 주행 명령의 목적지 {x, y, yaw}. FMS 가 /fleet_cmd{navigate} 로 내려보낸 args 다.
    # 예전엔 이게 없어서 nav 드라이버가 home 좌표로 하드코딩돼 있었고, 그래서
    # NavigationExec 은 "어디로 갈지"를 알 방법이 아예 없었다(죽은 코드였던 이유).
    NAV_TARGET = "nav_target"
    # 로봇의 현재 위치 {x, y}. `/amcl_pose` 에서 온다.
    # NavigationExec 이 **도착**을 판정하는 근거다 — 명령 접수 응답(`/fleet_cmd_result`)은
    # "주문 받았다"는 뜻일 뿐 도착과 아무 상관이 없다. 위치를 모르면 None 이고,
    # 그때는 도착하지 않은 것으로 본다 (모르는 걸 도착으로 치지 않는다).
    ROBOT_POSE = "robot_pose"
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
