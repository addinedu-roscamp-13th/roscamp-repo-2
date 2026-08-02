"""Blackboard key name constants shared by every common node and branch."""


class Keys:
    CURRENT_MODE = "current_mode"
    NEXT_MODE = "next_mode"
    FAULT = "fault"
    BATTERY_PERCENT = "battery_percent"
    IS_DOCKED = "is_docked"
    LAST_COMMAND = "last_command"
    UI_LAST_TOUCH_AT = "ui_last_touch_at"
    # INTERACTING 남은초(초, float). UiSessionTimer 가 매 tick 쓴다. state_io 가 읽어
    # /libi/fsm_state JSON 의 remaining_sec 으로 내보낸다(패널 카운트다운용). 비-INTERACTING 시 0.0.
    INTERACTING_REMAINING = "interacting_remaining"
    ACTIVE_COMMAND = "active_command"
    # 주행 명령의 목적지 {x, y, yaw}. FMS 가 /fleet_cmd{navigate} 로 내려보낸 args 다.
    # 예전엔 이게 없어서 nav 드라이버가 home 좌표로 하드코딩돼 있었고, 그래서
    # NavigationExec 은 "어디로 갈지"를 알 방법이 아예 없었다(죽은 코드였던 이유).
    NAV_TARGET = "nav_target"
    # 팔 명령의 args 원본(`/fleet_cmd` 의 `args` dict). `HandyActionDriver` 가 이걸 읽어
    # `ArmTask` goal 을 만든다. 좌표(`tier`/`row`/`slot`)는 FMS 가 실어 보내야 채워진다.
    ARM_ARGS = "arm_args"
    # 그 팔 명령의 `/fleet_cmd` **id**. 완료를 `/fleet_cmd_result` 로 올릴 때 이 id 를 그대로
    # 써야 FMS 가 어느 다리가 끝났는지 안다(FMS 는 자기가 보낸 id 로만 대조한다).
    # ⚠️ 이게 없으면 팔이 다 움직인 뒤에도 관제는 모른다 — robot_agent 의 즉시 성공 응답이
    #    다리를 먼저 닫고, 로봇은 **팔을 뻗은 채 다음 주행을 시작한다.**
    ARM_CMD_ID = "arm_cmd_id"
    # 로봇의 현재 위치 {x, y}. `/amcl_pose` 에서 온다.
    # NavigationExec 이 **도착**을 판정하는 근거다 — 명령 접수 응답(`/fleet_cmd_result`)은
    # "주문 받았다"는 뜻일 뿐 도착과 아무 상관이 없다. 위치를 모르면 None 이고,
    # 그때는 도착하지 않은 것으로 본다 (모르는 걸 도착으로 치지 않는다).
    ROBOT_POSE = "robot_pose"
    # 길잡이(GuideExec)가 "안내받는 사람이 아직 따라오는가"를 판단하는 근거.
    #   REQUESTER_VISIBLE  : 지금 보이나 (True/False). 감시 자체가 안 돌면 None.
    #   REQUESTER_SEEN_AT  : 마지막으로 보인 monotonic 시각. 한 번도 못 봤으면 0.0.
    # 둘 다 `/libi/requester_visible`(Bool) 에서 온다 — libi_perception 이 발행한다.
    # ⚠️ VISIBLE 만으로는 "잠깐 가렸다"와 "가버렸다"를 못 가른다. 얼마나 오래 안 보였는지가
    #    정지/포기를 가르는 값이라 시각을 함께 둔다(추종 회복 BT 가 Hold 를 먼저 두는 것과 같은 이유).
    REQUESTER_VISIBLE = "requester_visible"
    REQUESTER_SEEN_AT = "requester_seen_at"
    # 요청자 bbox 면적(px^2). `/libi/requester_area` 에서 온다. 감시가 없거나 안 보이면 None.
    # ⚠️ VISIBLE 만으로는 "보이지만 10m 뒤"를 못 가른다 — 보이기만 하면 계속 가버린다.
    #    안 보일 때 0 을 싣지 않는 것이 계약이다(0 을 실으면 소실과 원거리가 같아진다).
    REQUESTER_AREA = "requester_area"
    # 길잡이 회복 BT 가 다 훑고도 요청자를 못 찾았다. `/libi/guide_search_failed`(Bool).
    # ⚠️ **안내를 끝내는 신호다.** 예전에는 `guide_lost_timeout_sec` 시계가 그 일을 했는데,
    #    회복 트리 타임라인과 그 숫자가 계속 어긋났다(트리를 고칠 때마다 손으로 맞춰야 했다).
    #    이제 회복이 스스로 "끝났다"고 말한다 — 근거는 libi_perception config 의 그 토픽 주석.
    #    감시가 안 돌거나 발행이 끊기면 None 이고, 그때는 끝난 것으로 치지 않는다.
    GUIDE_SEARCH_FAILED = "guide_search_failed"
    COMMAND_RECEIVED_AT = "command_received_at"
    DOCK_RETRY_COUNT = "dock_retry_count"
    #: 이번 도킹에 대한 undock 을 이미 했나. **원샷 래치**다.
    #
    # `is_docked` 는 주차장 정점 반경 0.12m 판정(dock_confirm.py)이라, 6cm 나온 뒤에도
    # 여전히 참이다. 브랜치 루트가 `memory=False` 라 그대로 두면 **매 tick 다시 6cm 를
    # 민다.** 반대로 반경을 벗어나는 것으로 판정하면 충전소를 충분히 못 벗어난 채
    # 건너뛴다. 그래서 "나왔다"를 위치가 아니라 사실로 기억한다.
    # 지우는 곳은 **`DockSettle` 하나뿐**이다(`common/return_steps.py`) — 도킹이 실제로
    # 끝나는 자리다. 게이트가 `is_docked` 전이를 보고 지우면 안 된다: 게이트는 주행
    # 브랜치마다 **별개 인스턴스**라(py_trees 노드는 부모를 하나만 갖는다) PATROL→WORKING
    # 으로 옮길 때 그쪽 인스턴스가 남의 래치를 지우고 또 민다.
    UNDOCK_DONE = "undock_done"
    #: BT 가 **스스로 선언한** 도킹 여부. `state_io` 가 이 값을 `/is_docked`(Bool, latched)로
    #  내보내고, 그 토픽을 `providers` 가 되받아 `IS_DOCKED` 를 채운다.
    #
    # ⚠️ **`IS_DOCKED` 에 직접 쓰면 안 된다.** `providers.as_dict()` 가 매 tick 그 키를
    #    구독값으로 덮어쓴다 — 써 봐야 다음 tick 에 지워진다. 그래서 선언용 키를 따로 둔다.
    #
    # 왜 필요한가 (2026-07-31): `/is_docked` 를 내던 `dock_confirm.py` 가 `pi.sh` 에서
    # 빠져 **아무도 발행하지 않는다**(pi.sh:203). 게다가 그 기본 정점 이름 `주차장` 은
    # navgraph 에서 없어졌다. 그 결과 `is_docked` 가 영영 None 이고,
    #   · `AlreadyDocked` 가 항상 실패해 충전소에 놓인 채 켜도 입구까지 나갔다 온다
    #   · `UndockNotNeeded` 가 None 을 "도킹 아님"으로 읽어 **탈출을 건너뛴다** →
    #     벽에서 7cm 지점에서 nav2 가 경로를 못 낸다
    # 위치로 추정하는 대신 **개루프 후진이 끝난 사실**로 선언한다(사용자 결정).
    #   세우는 곳: `DockSettle`      (common/return_steps.py — 도킹이 끝나는 자리)
    #   내리는 곳: `Undock` 성공 시  (common/undock.py — 실제로 빠져나온 자리)
    # ⚠️ 내리는 걸 빼먹으면 다음 복귀에서 `AlreadyDocked` 가 낡은 True 를 보고 주행을
    #    통째로 건너뛴다 — 도서관 한복판에서 CHARGING 을 선언한다.
    DOCK_DECLARED = "dock_declared"
    ERROR_CODE = "error_code"
    # [디버그] 잠긴 상태 브랜치 집합. IsMode 가 여기 든 상태면 FAILURE → Selector 가 건너뜀.
    # main.py 가 param/env 로 1회 seed. 비어있으면(기본) 동작 변화 없음.
    DISABLED_BRANCHES = "disabled_branches"
    # 이 시각(monotonic)까지는 BT 가 스스로 상태를 못 바꾼다. 관제 패널이 전이를 시킨
    # 직후에만 state_io 가 채운다 — 누른 상태가 곧바로 자동 이탈로 사라지지 않게 한다.
    # 비어 있으면(평상시) 아무 영향 없다. 자세한 배경은 request_transition.py 참고.
    HOLD_UNTIL = "hold_until"
    # 이 tick 의 next_mode 가 **명령에서 왔다**는 표시. CommandListener 만 쓴다.
    # HOLD_UNTIL 은 **BT 자율 전이(배터리·타이머·액션 완료)만** 미뤄야 한다 — 관제·패널이
    # 보낸 명령(task_assigned·ui_touch·task_done…)은 그 사람의 뜻이라 억제 대상이 아니다.
    # ⚠️ **한 tick 만 산다.** main.py `_tick()` 이 tick 끝에 무조건 지운다. 남겨 두면
    #    낡은 표시가 우연히 같은 목표를 노린 자율 전이까지 유지 시간을 뚫게 만든다.
    COMMANDED_MODE = "commanded_mode"


#: 등록 누락 경고를 키마다 한 번만 낸다 (tick 마다 도배하지 않게).
_WARNED: set = set()


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
    except AttributeError:
        # **등록 안 된 키다.** py_trees 는 KeyError 가 아니라 AttributeError 를 낸다:
        #   client 'fsm_node' does not have read/write access to '/robot_pose'
        #
        # 여기서 안 잡으면 **FSM 노드가 통째로 죽는다** — 그 순간 로봇은 판단 주체를
        # 잃는다. 등록 누락은 프로그래밍 실수지 "아직 값이 없음"이 아니므로 조용히
        # None 을 주는 것도 옳지 않다. 그래서 죽이지 않되 **한 번은 크게 남긴다.**
        #
        # ⚠️ 이 경고가 보이면 그 클라이언트에 `register_key` 를 추가해라. 안 하면 그
        #    값을 쓰는 기능이 조용히 안 도는 상태로 남는다.
        #    (2026-07-30 실측: `return_rotate` 의 pose 읽기가 이걸로 노드를 죽였다)
        if key not in _WARNED:
            _WARNED.add(key)
            print(f"[blackboard] ⚠️ 등록 안 된 키를 읽었다: {key!r} — "
                  f"register_key 를 빠뜨렸다. 값은 {default!r} 로 대체한다", flush=True)
        return default
