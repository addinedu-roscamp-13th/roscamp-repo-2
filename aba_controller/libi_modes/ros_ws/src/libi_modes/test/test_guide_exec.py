"""안내는 **혼자 도착하면 실패다** — GuideExec 이 요청자를 보고 모는지.

## 계약

    FMS  →  /fleet_cmd {action:"guide", args:{x,y,yaw}}     ← BT 층 (GuideExec 소유)
              providers: active_command="guide", nav_target={x,y,yaw}
    BT   →  /fleet_cmd {action:"goal", args:{x,y,yaw}}      ← 실행 층 (기존과 동일)
    감시  →  /libi/requester_visible (Bool)                  ← libi_perception 발행
              providers: requester_visible / requester_seen_at

`guide` 를 `navigate` 로 뭉뚱그리면 Selector 앞에 있는 `NavigationExec` 이 먼저 집어가
GuideExec 은 **한 번도 안 돈다** — 요청자를 놓쳐도 아무도 안 멈춘다는 뜻이다.
그래서 여기 첫 두 테스트는 "분류가 갈리는가"를 붙든다.

멈추는 건 표시가 아니라 **실제 취소**여야 한다. `stop_driver`(mission_stop →
ros_bridge.cancel_nav) 를 안 부르고 RUNNING 만 돌려주면 화면은 "기다리는 중"인데
nav2 는 계속 달려서 로봇이 사람을 두고 가버린다.
"""
import time

import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.working_actions import GuideExec, NavigationExec

from .fakes import FakeDriver

TOLERANCE = 0.1
RESEND = 10.0
TIMEOUT = 60.0
COAST = 1.0
WAIT = 2.0
RECOVER_AT = COAST + WAIT   # 회복 BT 에 바퀴를 넘기는 시각


class _Clock:
    def __init__(self):
        self.t = 100.0        # 0 이 아니어야 "한 번도 못 봄(0.0)"과 구분된다

    def __call__(self):
        return self.t


@pytest.fixture
def leaf(seed):
    def _make(*, clock=None, stop=None, watch=None, far_area_min=0.0,
              near_area_max=0.0, junctions=None, junction_hold_sec=0.0,
              result_fn=None, **blackboard):
        clock = clock or _Clock()
        seed(**blackboard)
        node = GuideExec(FakeDriver(), TOLERANCE, RESEND, TIMEOUT, COAST, WAIT,
                         stop_driver=stop, watch_driver=watch,
                         far_area_min=far_area_min, near_area_max=near_area_max,
                         junctions=junctions, junction_hold_sec=junction_hold_sec,
                         result_fn=result_fn, now_fn=clock)
        node.setup()
        node.initialise()
        node.test_clock = clock
        return node

    return _make


def _at(x, y):
    return {"x": x, "y": y}


def _to(x, y):
    return {"x": x, "y": y, "yaw": 0.0}


# ── 분류 (Selector 에서 서로 안 잡아먹는가) ──────────────────────────────────

def _gate():
    """도킹 탈출 게이트 대역 — `working.create` 가 필수로 받는다(test_branches 와 같은 이유)."""
    from libi_modes.common import undock
    return undock.create(FakeDriver(), distance_m=0.06, timeout_sec=8.0,
                         retry_max=3, now_fn=lambda: 0.0)

# ── 포기는 **SUCCESS 로 닫는다** (2026-08-02 실기) ──────────────────────────
#
# 실측(Pi, 1785641373.92): 20.3초에 give_up 은 정확히 발동했는데 전이가 유실됐다.
#     [WARN] 전이 요청이 적용되지 않았다: WORKING -> PATROL
#            (패널 유지 시간 중이거나, 브랜치가 RequestTransition 에 닿지 못했다)
# FAILURE 를 내면 dispatch Selector 가 `Running("AwaitingCommand")` 까지 흘러가
# Parallel(SuccessOnOne)이 RUNNING 을 유지 → Sequence 가 `RequestTransition` 에
# **영영 못 닿는다.** 그래서 이제 성공·실패 모두 SUCCESS 로 닫는다.
# 여기서 SUCCESS 는 "잘 마쳤다" 가 아니라 **"이 leaf 의 일이 끝났다"** 다.
#
# 시험이 봐야 할 것은 반환값이 아니라 **WORKING 을 빠져나가는가**(NEXT_MODE=PATROL)다.


def test_navigation_exec_does_not_claim_guide(seed):
    """`navigate` 담당은 `guide` 를 건드리지 않는다 — 건드리면 GuideExec 이 죽는다."""
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(1, 1)})
    node = NavigationExec(FakeDriver(), TOLERANCE, RESEND, TIMEOUT, now_fn=_Clock())
    node.setup()
    node.initialise()
    assert node.update() == Status.FAILURE      # 내 명령이 아니다 → 다음 처리기로


def test_guide_exec_does_not_claim_navigate(leaf):
    """반대도 같다 — 배달 주행을 길잡이가 가로채면 요청자 없다고 멈춰 선다."""
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate", Keys.NAV_TARGET: _to(1, 1)})
    assert node.update() == Status.FAILURE


def test_guide_survives_its_own_goal_echo():
    """**자기 메아리** — 이게 안 막히면 길잡이가 첫 goal 직후 죽는다.

    BT 드라이버도 같은 `/fleet_cmd` 로 실행 층 `goal` 을 발행하고, 이 노드가 그걸 도로
    구독한다. 예전엔 mission_actions 갈래가 무조건 `active_command="navigate"` 로 덮어서:

        guide 수신 → GuideExec 이 goal 발행 → 그 goal 을 되받아 navigate 로 덮임
        → 다음 tick 부터 앞에 있는 NavigationExec 이 가져감
        → 요청자 감시가 사라지고, 사람을 놓쳐도 아무도 안 멈춘다

    leaf 단위 시험으로는 안 잡힌다(명령이 어떻게 바뀌는지는 provider 쪽 일이라).
    """
    import json

    from libi_modes.ros.providers import RosProviders

    class _FakeNode:
        def create_subscription(self, *a, **k):
            return None

        def get_logger(self):
            class _L:
                def warning(self, *a): pass
                def info(self, *a): pass
            return _L()

    class _Msg:
        def __init__(self, data):
            self.data = data

    p = RosProviders(_FakeNode())
    p._on_cmd(_Msg(json.dumps({"action": "guide", "args": {"x": 1.0, "y": 2.0}})))
    assert p.as_dict()["active_command"]() == "guide"

    # GuideExec 이 실행 층으로 내려보낸 goal 이 되돌아온다
    p._on_cmd(_Msg(json.dumps({"action": "goal", "args": {"x": 1.0, "y": 2.0}})))
    assert p.as_dict()["active_command"]() == "guide", "실행 층 메아리가 guide 를 덮으면 안 된다"

    # navigate 가 돌고 있을 때의 메아리는 같은 값이라 무해해야 한다(기존 동작 유지)
    p._active_command = "navigate"
    p._on_cmd(_Msg(json.dumps({"action": "goal", "args": {}})))
    assert p.as_dict()["active_command"]() == "navigate"

    # 아무것도 안 돌 때 FMS 가 준 goto 는 여전히 navigate 로 잡혀야 한다
    p._active_command = None
    p._on_cmd(_Msg(json.dumps({"action": "goto", "args": {"name": "화장실"}})))
    assert p.as_dict()["active_command"]() == "navigate"


def test_providers_classifies_guide_separately():
    """provider 가 `guide` 를 navigate 로 바꿔치기하면 위 분류가 통째로 무의미해진다."""
    import json

    from libi_modes.ros.providers import RosProviders

    class _FakeNode:
        def create_subscription(self, *a, **k):
            return None

        def get_logger(self):
            class _L:
                def warning(self, *a): pass
                def info(self, *a): pass
            return _L()

    class _Msg:
        def __init__(self, data):
            self.data = data

    p = RosProviders(_FakeNode())
    p._on_cmd(_Msg(json.dumps({"action": "guide", "args": {"x": 1.0, "y": 2.0}})))
    d = p.as_dict()
    assert d["active_command"]() == "guide"
    assert d["nav_target"]() == {"x": 1.0, "y": 2.0, "yaw": 0.0}

    # fleet_node의 순회/배차 경로가 안내 중에 도착해도 guide 및 그 목적지를 뺏으면 안 된다.
    # 이 방어가 없으면 GuideExec 이 다음 tick 에 감시를 닫고 NavigationExec 으로 강등된다.
    p._on_cmd(_Msg(json.dumps({"action": "navigate", "args": {"x": 3.0, "y": 4.0}})))
    assert p.as_dict()["active_command"]() == "guide"
    assert p.as_dict()["nav_target"]() == {"x": 1.0, "y": 2.0, "yaw": 0.0}

    # 비어 있을 때는 기존처럼 fleet_node 주행을 받아야 한다.
    p._active_command = None
    p._on_cmd(_Msg(json.dumps({"action": "navigate", "args": {"x": 3.0, "y": 4.0}})))
    assert p.as_dict()["active_command"]() == "navigate"
    assert p.as_dict()["nav_target"]() == {"x": 3.0, "y": 4.0, "yaw": 0.0}


# ── 요청자를 보고 모는가 ────────────────────────────────────────────────────

def test_drives_while_the_requester_is_visible(leaf):
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: 100.0})
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 1          # goal 을 냈다


def test_brief_occlusion_does_not_stop_the_robot(leaf):
    """서가 뒤로 한 발 들어간 정도로 멈추면 안내가 계속 끊긴다."""
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(clock=clock, stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 99.5})      # 0.5초 전 (< COAST)
    assert node.update() == Status.RUNNING
    assert stop.start_count == 0                 # 코스팅 중 — 아직 안 멈춘다


def test_lost_past_coast_actually_cancels_navigation(leaf):
    """코스팅이 끝나면 **바로** 멈춘다.

    ⚠️ [2026-08-02] 예전에는 `lost_grace_sec`(20초)를 다 지나야 멈췄다 — 즉 사람을
       놓친 채 20초를 더 갔다. 이제 코스팅(1.4초)만 지나면 nav2 를 끊고 서서
       기다린다(사용자 스펙 "20초 정지 후에 회복 BT" 의 그 정지다).
    """
    stop = FakeDriver()
    node = leaf(stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 98.0})      # 2초 전 (> COAST, < 대기 끝)
    assert node.update() == Status.RUNNING       # 포기는 아니다 — 서서 기다린다
    assert stop.start_count == 1
    node.update()
    assert stop.start_count == 1                 # 취소는 한 번만


def test_resumes_when_the_requester_comes_back(leaf, seed):
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(clock=clock, stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 95.0})
    node.update()
    sent_before = node.driver.start_count

    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
            Keys.REQUESTER_SEEN_AT: clock.t})
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == sent_before + 1   # 취소된 주행을 다시 냈다


def test_gives_up_when_recovery_reports_it_searched_and_failed(leaf, read):
    """회복 BT 가 **다 훑고도 못 찾았다**고 말하면 그때 끝낸다.

    ⚠️ 예전에는 `guide_lost_timeout_sec`(시계)가 이 자리에 있었다. 시계는 회복
    트리 타임라인과 계속 어긋났다 — 이제 회복이 스스로 말한다
    (`follow_node._publish_guide_search_failed` → `/libi/guide_search_failed`).
    """
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 99.0,        # 1초 전 — 유예 안쪽이다
                   Keys.GUIDE_SEARCH_FAILED: True})
    # 포기도 SUCCESS 로 닫는다(위 ── 주석). 봐야 할 것은 **끝났고 순찰을 예약했는가**.
    assert node.update() == Status.SUCCESS
    assert read(Keys.NEXT_MODE) == "PATROL", "포기했는데 순찰 예약이 없다"
    assert read(Keys.ACTIVE_COMMAND) is None            # 슬롯을 비워야 다음 명령을 받는다


def test_lost_alone_never_gives_up_without_the_recovery_signal(leaf, read):
    """**시계만으로는 절대 안 끝난다.** 회복 신호가 유일한 종결자다.

    되돌림 감지용: `lost >= <어떤 시간>` 조건을 다시 넣으면 이 시험이 빨개진다.
    """
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 1.0,         # 99초 전 — 아무리 오래돼도
                   Keys.GUIDE_SEARCH_FAILED: False})
    assert node.update() == Status.RUNNING, "회복이 아직 도는데 안내를 끝냈다"
    assert read(Keys.NEXT_MODE) is None


def test_stale_recovery_signal_does_not_end_the_guide(leaf, read):
    """발행이 끊겨 `None` 이면 **끝난 것으로 치지 않는다.**

    `providers._fresh_guide_search_failed` 가 stale 을 None 으로 내린다. 그걸
    True 로 읽으면 멀쩡한 안내가 링크 끊김만으로 끝난다.
    """
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 99.0,
                   Keys.GUIDE_SEARCH_FAILED: None})
    assert node.update() == Status.RUNNING
    assert read(Keys.NEXT_MODE) is None


def test_arrival_still_ends_the_guide(leaf, read):
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(1, 1),
                   Keys.ROBOT_POSE: _at(1, 1), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: 100.0})
    assert node.update() == Status.SUCCESS
    assert read(Keys.ACTIVE_COMMAND) is None


# ── 브랜치 통합 (안내가 끝나면 WORKING 을 빠져나가는가) ──────────────────────

def _working(nav=None, guide=None, stop=None):
    from libi_modes.branches import working
    from .fakes import PARAMS
    return working.create(PARAMS, nav or FakeDriver(), FakeDriver(), None,
                          guide or FakeDriver(), stop or FakeDriver(),
                          clock=lambda: 100.0, undock_gate=_gate())


def test_arrival_takes_the_robot_out_of_working(seed, read, tick):
    """도착해도 WORKING 에 남으면 120초 뒤 CommandTimeout 이 ERROR 로 보낸다.

    배달은 FMS 가 다리 완료를 알고 task_done 을 주지만, 길잡이를 시킨 건 패널이라
    FMS 는 로봇이 도착했는지 모른다. GuideExec 이 스스로 알려야 한다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(1.0, 1.0), Keys.ROBOT_POSE: _at(1.0, 1.0),
            Keys.REQUESTER_VISIBLE: True, Keys.REQUESTER_SEEN_AT: 100.0})
    tick(_working())
    # 성공은 Sequence 가 끝까지 가므로 RequestTransition 이 **같은 tick 에 적용**하고
    # NEXT_MODE 를 지운다 — 그래서 결과는 CURRENT_MODE 에서 본다.
    assert read(Keys.CURRENT_MODE) == "PATROL", "도착했으면 WORKING 을 빠져나가야 한다"


def test_guide_completion_bypasses_the_panel_manual_hold(seed, read, tick):
    """패널이 직전에 전이시켜도 안내 종료의 WORKING -> PATROL 은 유실되면 안 된다."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(1.0, 1.0), Keys.ROBOT_POSE: _at(1.0, 1.0),
            Keys.REQUESTER_VISIBLE: True, Keys.REQUESTER_SEEN_AT: 100.0,
            Keys.HOLD_UNTIL: time.monotonic() + 300.0})
    tick(_working())
    assert read(Keys.CURRENT_MODE) == "PATROL", \
        "manual_hold_sec 중에도 GuideExec 종료는 같은 tick 에 PATROL 이어야 한다"


def test_losing_the_requester_for_good_also_exits_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(5.0, 5.0), Keys.ROBOT_POSE: _at(0.0, 0.0),
            Keys.REQUESTER_VISIBLE: False, Keys.REQUESTER_SEEN_AT: 10.0,
            # 회복 BT 가 다 훑고 포기했다 — 이게 안내를 끝내는 유일한 신호다.
            Keys.GUIDE_SEARCH_FAILED: True})
    tick(_working())
    # ⚠️ [2026-08-02] 이제 **전이가 그 tick 에 실제로 적용된다.** 예전에는 GuideExec 이
    #    FAILURE 를 내 Selector 가 `AwaitingCommand`(RUNNING) 까지 흘렀고, Parallel 이
    #    RUNNING 이라 Sequence 가 `RequestTransition` 에 못 닿아 `NEXT_MODE` 만 남았다
    #    (실기에서 로봇이 WORKING 에 갇힌 원인). SUCCESS 로 닫으면서 같은 tick 에
    #    적용되므로, 확인할 것은 예약(NEXT_MODE)이 아니라 **결과(CURRENT_MODE)** 다.
    assert read(Keys.CURRENT_MODE) == "PATROL", "포기했으면 WORKING 에 갇히면 안 된다"
    assert read(Keys.NEXT_MODE) is None, "예약이 소비되지 않았다 — 전이가 안 적용됐다"


def test_guide_command_reaches_guide_exec_not_navigation(seed, tick):
    """dispatch Selector 에서 앞의 NavigationExec 이 가로채면 안 된다."""
    nav, guide = FakeDriver(), FakeDriver()
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(5.0, 5.0), Keys.ROBOT_POSE: _at(0.0, 0.0),
            Keys.REQUESTER_VISIBLE: True, Keys.REQUESTER_SEEN_AT: 100.0})
    tick(_working(nav=nav, guide=guide))
    assert guide.started and not nav.started


def test_no_watcher_fails_safe_after_grace(leaf):
    """감시가 없으면 등록자가 확인되지 않았으므로 nav2 를 멈춘다."""
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(stop=stop,
                clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: None,
                   Keys.REQUESTER_SEEN_AT: 0.0,
                   Keys.COMMAND_RECEIVED_AT: 95.0})
    assert node.update() == Status.RUNNING
    assert stop.start_count == 1


# ── 감시 세션 (2026-07-27) ───────────────────────────────────────────────────
# 이게 없으면 `/libi/requester_visible` 발행자가 아예 없어, 아래 판단이 전부
# "감시 없음 → 그냥 주행" 으로 흘러간다.

def test_starts_watch_session_on_first_tick(leaf):
    watch = FakeDriver()
    node = leaf(watch=watch, **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                                Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    assert watch.started is True


def test_stops_watch_session_on_arrival(leaf):
    watch = FakeDriver()
    node = leaf(watch=watch, **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(0, 0),
                                Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    assert watch.stopped is True


def test_watch_started_only_once(leaf):
    watch = FakeDriver()
    node = leaf(watch=watch, **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                                Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    node.update()
    assert watch.start_count == 1


# ── 거리 게이트 ─────────────────────────────────────────────────────────────

def test_far_requester_halts(leaf):
    """보이지만 너무 멀다 — VISIBLE 만 보면 10m 뒤에 있어도 계속 간다."""
    stop = FakeDriver()
    node = leaf(stop=stop, far_area_min=500.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 100.0})
    assert node.update() == Status.RUNNING
    assert stop.started is True


def test_near_requester_drives(leaf):
    stop = FakeDriver()
    node = leaf(stop=stop, far_area_min=500.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 900.0})
    node.update()
    assert stop.started is False


def test_far_gate_off_by_default(leaf):
    """실측 전에는 꺼져 있어야 한다 — 근거 없는 임계로 멈추면 원인을 못 찾는다."""
    stop = FakeDriver()
    node = leaf(stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 1.0})
    node.update()
    assert stop.started is False


def test_unknown_area_does_not_halt(leaf):
    """면적을 모르면(옛 payload / 감시 없음) 거리 게이트를 걸지 않는다."""
    stop = FakeDriver()
    node = leaf(stop=stop, far_area_min=500.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    assert stop.started is False


def test_near_obstacle_halts(leaf):
    """앞을 막을 만큼 가까우면 멈춘다(기본 꺼짐)."""
    stop = FakeDriver()
    node = leaf(stop=stop, near_area_max=5000.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 9000.0})
    assert node.update() == Status.RUNNING
    assert stop.started is True


def test_near_gate_off_by_default(leaf):
    stop = FakeDriver()
    node = leaf(stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 999999.0})
    node.update()
    assert stop.started is False


def test_resumes_after_requester_comes_closer(leaf):
    stop, clock = FakeDriver(), _Clock()
    node = leaf(clock=clock, stop=stop, far_area_min=500.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_AREA: 100.0})
    import py_trees
    node.update()
    before = node.driver.start_count
    # provider 가 다음 tick 에 새 값을 올린 것과 같다(GuideExec 은 READ 전용이다).
    py_trees.blackboard.Blackboard.set(Keys.REQUESTER_AREA, 900.0)
    assert node.update() == Status.RUNNING
    assert node.driver.start_count > before   # goal 을 다시 낸다


# ── 갈림길 확인 ─────────────────────────────────────────────────────────────

class _Junctions:
    def __init__(self, pts):
        self.pts = pts

    def __len__(self):
        return len(self.pts)

    def contains(self, t):
        return t is not None and (round(t["x"], 3), round(t["y"], 3)) in self.pts


def test_junction_holds_briefly(leaf):
    """갈림길에서만 선다. 모든 노드에서 서면 arte2 는 1~5초마다 멈춘다."""
    stop, clock = FakeDriver(), _Clock()
    node = leaf(clock=clock, stop=stop, junctions=_Junctions({(5.0, 5.0)}),
                junction_hold_sec=1.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    assert node.update() == Status.RUNNING
    assert stop.started is True
    clock.t += 2.0
    node.update()
    assert node.driver.started is True         # 유지 시간이 지나면 다시 간다


def test_non_junction_does_not_hold(leaf):
    stop = FakeDriver()
    node = leaf(stop=stop, junctions=_Junctions({(9.0, 9.0)}), junction_hold_sec=1.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    assert stop.started is False


def test_same_junction_does_not_hold_twice(leaf):
    """goal 을 다시 내도 목적지는 그대로다 — 제한이 없으면 그 자리에서 영원히 선다."""
    stop, clock = FakeDriver(), _Clock()
    node = leaf(clock=clock, stop=stop, junctions=_Junctions({(5.0, 5.0)}),
                junction_hold_sec=1.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    clock.t += 2.0
    node.update()
    before = stop.start_count
    clock.t += 2.0
    node.update()
    assert stop.start_count == before


def test_junction_hold_off_when_no_navgraph(leaf):
    """navgraph 를 못 읽으면 확인 동작이 그냥 꺼진다 — 안내는 계속된다."""
    stop = FakeDriver()
    node = leaf(stop=stop, junctions=None, junction_hold_sec=1.0,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True})
    node.update()
    assert stop.started is False


# ── 명령 메아리가 페일세이프를 무력화하던 것 (2026-08-02) ──────────────────────
#
# `providers._on_cmd` 는 `/fleet_cmd` 로 **무엇이 오든** 맨 앞에서
# `_command_received_at = monotonic()` 을 찍는다(providers.py:207). 그런데 GuideExec
# 자신이 같은 토픽으로 `goal`(재전송) · `guide_watch` · `mission_stop` 을 낸다.
# 그 메아리가 도로 구독되어 기준 시각이 **매 tick 지금으로 갱신**되므로, 예전
# `_lost_for()` 처럼 그 값을 기준으로 유예를 재면 요청자를 한 번도 못 본 안내에서
# `lost` 가 영영 유예를 못 넘긴다 — 아무도 안 멈추고 로봇이 혼자 목적지로 간다.
#
# 위 `test_no_watcher_fails_safe_after_grace` 가 이걸 못 잡은 이유는 그 시험이
# COMMAND_RECEIVED_AT 을 95.0 으로 **고정**하기 때문이다. 실기에서는 움직이는 과녁이다.

def test_moving_command_stamp_cannot_unblock_an_unconfirmed_guide(leaf, seed, read):
    """명령 접수 시각이 계속 갱신돼도, 확인 안 된 요청자로는 출발하지 않는다."""
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(stop=stop, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: None,
                   Keys.REQUESTER_SEEN_AT: 0.0,
                   Keys.COMMAND_RECEIVED_AT: clock.t})

    # 로봇 자신의 명령 메아리를 흉내낸다 — 매 tick 기준 시각이 "지금" 으로 밀린다.
    #
    # ⚠️ [2026-08-02] 계약이 바뀌었다: 뒷캠으로 **한 번도 확인 못 한** 안내는
    #    회복을 기다리지 않고 **대기가 끝나는 시점(`_recover_at`)에서 포기**한다
    #    (사용자 스펙 "처음에 안내 시작하고 안 보이면 알아서 순찰 모드").
    #    그래서 그 직전까지만 굴리고, 그 안에서 출발하지 않는 것을 본다.
    ticks = int(RECOVER_AT) - 1
    for _ in range(ticks):
        clock.t += 1.0
        seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.COMMAND_RECEIVED_AT: clock.t})
        assert node.update() == Status.RUNNING, "대기 안인데 포기했다"

    # 대기 안에서 주행 명령은 한 번도 안 나갔고, 정지는 처음 한 번 나갔다.
    assert node.driver.start_count == 0, "확인 안 된 요청자로 출발했다"
    assert stop.start_count == 1

    # 대기를 넘기면 **회복을 기다리지 않고** 바로 포기한다 → PATROL.
    clock.t += 2.0
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.COMMAND_RECEIVED_AT: clock.t})
    st = node.update()
    assert st == Status.SUCCESS, \
        "확인 못 한 안내가 대기를 넘겼는데 안 포기했다 — 이용자는 왜 안 가는지 모른다"
    assert read(Keys.NEXT_MODE) == "PATROL", "포기했는데 순찰 예약이 없다"


def test_unconfirmed_guide_still_gives_up_on_its_own_clock(leaf, seed, read):
    """확인 전 포기(`_recover_at`)는 메아리에 밀리지 않는다 — 우리 시각으로 잰다."""
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(stop=stop, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: None,
                   Keys.REQUESTER_SEEN_AT: 0.0,
                   Keys.COMMAND_RECEIVED_AT: clock.t})
    assert node.update() == Status.RUNNING          # 여기서 기준 시각이 찍힌다

    clock.t += RECOVER_AT + 1.0
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.COMMAND_RECEIVED_AT: clock.t})   # 메아리
    st = node.update()
    assert st == Status.SUCCESS, "메아리 때문에 영영 안 포기한다"
    assert read(Keys.NEXT_MODE) == "PATROL", "포기했는데 순찰 예약이 없다"


def test_confirmed_then_lost_still_gets_the_grace(leaf, seed):
    """한 번 확인된 뒤의 소실은 **평소대로** 유예를 받는다(회귀 방지).

    `_never_confirmed` 가 과하게 걸리면 잠깐 가려질 때마다 로봇이 서 버린다.
    """
    stop = FakeDriver()
    clock = _Clock()
    node = leaf(stop=stop, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t,
                   Keys.COMMAND_RECEIVED_AT: clock.t})
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 1             # 보이니 출발했다

    # 잠깐 가려진다 — 코스팅 안이라 아직 안 멈춘다.
    clock.t += COAST - 0.5
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: False})
    assert node.update() == Status.RUNNING
    assert stop.start_count == 0, "코스팅 안인데 멈췄다"

    # 코스팅을 넘기면 멈춘다.
    clock.t += 1.0
    assert node.update() == Status.RUNNING
    assert stop.start_count == 1


def test_wait_holds_the_wheels_until_it_is_over(leaf, seed):
    """정지 → **대기** → 회복. 대기가 끝나기 전에는 회전을 안 넘긴다.

    ⚠️ 넘기면 nav2 를 막 끊은 자리에서 로봇이 곧바로 돌기 시작한다. 사용자 스펙은
       "박스가 사라지면 20초 정지 **후에** 회복 BT" 다.
    """
    stop, watch = FakeDriver(poll_sequence=("success",) * 20), _RotWatch()
    clock = _Clock()
    node = leaf(stop=stop, watch=watch, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t})
    node.update()                                    # 뒷캠 확인 완료, 출발

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: False,
            Keys.NAV_TARGET: _to(5, 5), Keys.ROBOT_POSE: _at(0, 0),
            Keys.REQUESTER_SEEN_AT: lost_at})

    clock.t = lost_at + COAST + 0.1                  # 정지 구간
    node.update()
    assert stop.start_count == 1, "코스팅이 끝났는데 nav2 를 안 끊었다"
    assert True not in watch.rotate_calls, "대기 중인데 회전을 허가했다"

    clock.t = lost_at + RECOVER_AT - 0.1             # 대기 끝나기 직전
    node.update()
    assert True not in watch.rotate_calls, "대기가 아직 안 끝났는데 회전을 허가했다"

    clock.t = lost_at + RECOVER_AT + 0.1             # 대기 끝
    node.update()
    assert True in watch.rotate_calls, "대기가 끝났는데 회복 회전을 안 넘겼다"


# ── 요청자를 놓치면 그 홉을 FMS 에 실패로 닫는다 (교통관제 연동 2026-08-02) ──────
#
# 길잡이가 fleet_node 의 홉을 받아 가게 되면서, 놓친 순간 그 홉은 더 못 간다.
# 아무 말도 안 하면 FMS 는 "가는 중" 으로 알고 그 로봇 몫 노드·간선을 안 놓는다.

class _Results:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd_id, ok, msg):
        self.calls.append((cmd_id, ok, msg))


def test_lost_requester_closes_the_hop(leaf, seed):
    res = _Results()
    stop, clock = FakeDriver(), _Clock()
    node = leaf(stop=stop, clock=clock, result_fn=res,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t, Keys.GUIDE_CMD_ID: "guide-7-123"})
    node.update()                                   # 뒷캠 확인 완료, 출발
    assert res.calls == []

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
            Keys.REQUESTER_SEEN_AT: lost_at, Keys.GUIDE_CMD_ID: "guide-7-123"})
    clock.t = lost_at + COAST + 0.1
    node.update()
    assert res.calls == [("guide-7-123", False, "requester_lost")], \
        "놓쳤는데 FMS 에 안 알렸다 — 예약이 계속 잡혀 있다"

    # 20Hz 로 같은 id 를 쏟으면 FMS 가 같은 홉을 반복해서 닫는다.
    clock.t += 0.5
    node.update()
    clock.t += 0.5
    node.update()
    assert len(res.calls) == 1, "소실 한 번에 한 번만 보고해야 한다"


def test_reacquire_rearms_the_report(leaf, seed):
    """다시 보였다가 또 놓치면 **새 사건**이라 다시 알려야 한다."""
    res = _Results()
    stop, clock = FakeDriver(), _Clock()
    node = leaf(stop=stop, clock=clock, result_fn=res,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t, Keys.GUIDE_CMD_ID: "hop-1"})
    node.update()

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
            Keys.REQUESTER_SEEN_AT: lost_at, Keys.GUIDE_CMD_ID: "hop-1"})
    clock.t = lost_at + COAST + 0.1
    node.update()
    assert len(res.calls) == 1

    # 다시 찾았다 — FMS 가 새 홉을 내려준다(새 id).
    clock.t += 1.0
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
            Keys.REQUESTER_SEEN_AT: clock.t, Keys.GUIDE_CMD_ID: "hop-2"})
    node.update()

    # 또 놓쳤다
    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
            Keys.REQUESTER_SEEN_AT: lost_at, Keys.GUIDE_CMD_ID: "hop-2"})
    clock.t = lost_at + COAST + 0.1
    node.update()
    assert res.calls[-1] == ("hop-2", False, "requester_lost"), \
        "재획득 뒤 래치가 안 풀렸다 — 두 번째 소실을 FMS 가 영영 모른다"


def test_new_hop_during_a_loss_can_still_be_closed(leaf, seed):
    """소실 중에 FMS 가 **늦게 도착한 홉**을 내리면 그것도 닫아야 한다.

    ⚠️ 래치가 bool 이면 그 홉은 영영 안 닫혀 fleet 예약이 남는다
       (codex 검토 2026-08-02 P1). id 가 바뀌면 새 사건으로 본다.
    """
    res = _Results()
    stop, clock = FakeDriver(), _Clock()
    node = leaf(stop=stop, clock=clock, result_fn=res,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t, Keys.GUIDE_CMD_ID: "hop-1"})
    node.update()

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
            Keys.REQUESTER_SEEN_AT: lost_at, Keys.GUIDE_CMD_ID: "hop-1"})
    clock.t = lost_at + COAST + 0.1
    node.update()
    assert res.calls == [("hop-1", False, "requester_lost")]

    # 아직 안 보이는데 새 홉이 도착했다(FMS 가 소실을 알기 전에 낸 명령).
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
            Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
            Keys.REQUESTER_SEEN_AT: lost_at, Keys.GUIDE_CMD_ID: "hop-2"})
    clock.t += 0.5
    node.update()
    assert res.calls[-1] == ("hop-2", False, "requester_lost"), \
        "늦게 온 홉이 안 닫힌다 — fleet 예약이 그대로 남는다"


def test_no_result_fn_is_harmless(leaf, seed):
    """주입 안 된 배포(시험대·팔 없는 구성)에서도 안내는 그대로 돈다."""
    clock = _Clock()
    node = leaf(clock=clock, stop=FakeDriver(),
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 99.0})
    assert node.update() == Status.RUNNING


# ── 확인 전에는 회복 회전을 절대 허가하지 않는다 (2026-08-02, 리뷰 지적) ──────
#
# `_lost_for()` 는 뒷캠 목격이 없으면 `_guard_since`(안내를 맡은 시각)부터 잰다.
# 그래서 **출발 전에도** 10초만 지나면 `lost >= grace` 가 성립한다. 그 조건만 보고
# 회전을 허가하면, 등록만 하고 로봇 앞에 서 있는 사람 앞에서 로봇이 돌기 시작한다.
# 주석은 "확인 전엔 안 준다" 였는데 코드가 안 지키고 있었다.

class _RotWatch(FakeDriver):
    """`start(args)` 로 넘어온 회전 허가를 기록한다."""
    def __init__(self):
        super().__init__()
        self.rotate_calls = []

    def start(self, args=None):
        super().start()
        if args is not None:
            self.rotate_calls.append(bool(args.get("allow_rotate")))


def test_rotation_is_never_granted_before_the_back_cam_confirms(leaf, seed):
    # ⚠️ 정지 ack 를 **성공**으로 만든다. 기본 FakeDriver 는 항상 "running" 이라
    #    `_stop_settled()` 가 False 로 남고, 그러면 `_never_confirmed` 와 무관하게
    #    회전이 안 열려 **시험이 아무것도 검증하지 못한다**(첫 판이 그랬다).
    #    여기서 통과시켜야 남는 조건이 `_never_confirmed` 하나가 된다.
    stop, watch = FakeDriver(poll_sequence=("success",) * 20), _RotWatch()
    clock = _Clock()
    node = leaf(stop=stop, watch=watch, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: None,
                   Keys.REQUESTER_SEEN_AT: 0.0,
                   Keys.COMMAND_RECEIVED_AT: clock.t})
    node.update()                                   # 감시 시작, _guard_since 찍힘

    # 사람이 아직 뒤로 안 왔다. 대기를 훌쩍 넘겨도 **회전은 안 된다.**
    for _ in range(6):
        clock.t += RECOVER_AT
        seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: None,
                Keys.REQUESTER_SEEN_AT: 0.0, Keys.COMMAND_RECEIVED_AT: clock.t})
        if node.update() != Status.RUNNING:
            break

    assert True not in watch.rotate_calls, \
        "뒷캠 확인 전인데 회전을 허가했다 — 앞에 서 있는 사람 앞에서 로봇이 돈다"
    assert node.driver.start_count == 0, "확인 전인데 출발했다"


# ── 정지가 성공으로 안 끝나면 **회전 대신 안내를 끝낸다** (2026-08-02) ────────
#
# 두 가지가 동시에 참이라 어느 한쪽만 고르면 안 된다.
#
#   ① `failure` 를 정지로 인정하면 안 된다. 재전달 방어에 걸린 `mission_stop` 은
#      **실행되지 않은 채** 408 로 답하고(robot_agent `fleet_link.py` stale 분기),
#      `poll()` 의 타임아웃 failure 는 오히려 "상대가 아직 돌고 있을 수 있다" 는
#      뜻이라 `_abandoned_id` 를 남긴다(`fleet_cmd_driver.py`). 그 상태로 회전을
#      넘기면 nav2 와 회복 트리가 같은 `/cmd_vel` 을 민다.
#   ② 그렇다고 계속 기다리면 안내가 **WORKING 에 영영 갇힌다.** 길잡이의 회복
#      트리는 회전 허가가 열려야 비로소 만들어지므로(`control_loop._start_search()`
#      는 길잡이일 때 `_search_tree = None`), 허가가 안 열리면 `GiveUp` 도
#      `guide_search_failed` 도 안 나온다. 패널도 WORKING 이탈만 보므로 안내
#      화면에 남는다(`RobotController.finishGuideIfLeftWorking`).
#
# 답: 회전은 안 넘기고 **안내를 끝낸다**(PATROL 로 나간다).

def test_stop_failure_ends_the_guide_instead_of_rotating(leaf, seed, read):
    """정지가 실패로 끝났다 — 회전은 안 넘기고 WORKING 을 벗어난다.

    `NEXT_MODE=PATROL` 까지 확인한다. 그게 실제 상태 전이이고, 패널이 홈으로
    돌아가는 근거이기도 하다(`RobotController.finishGuideIfLeftWorking` 은
    로봇이 WORKING 을 벗어난 것만 본다).
    """
    stop, watch = FakeDriver(poll_sequence=("failure",) * 20), _RotWatch()
    clock = _Clock()
    node = leaf(stop=stop, watch=watch, clock=clock, result_fn=_Results(),
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t,
                   Keys.GUIDE_CMD_ID: "guide-9-999"})
    node.update()                                    # 뒷캠 확인 완료, 출발

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: False,
            Keys.NAV_TARGET: _to(5, 5), Keys.ROBOT_POSE: _at(0, 0),
            Keys.REQUESTER_SEEN_AT: lost_at})

    clock.t = lost_at + COAST + 0.1                  # 정지 구간 — 아직 대기 중
    node.update()
    assert True not in watch.rotate_calls, "대기 중인데 회전을 허가했다"
    assert read(Keys.ACTIVE_COMMAND) == "guide", "대기 중인데 안내를 끝냈다"

    clock.t = lost_at + RECOVER_AT + 0.1             # 대기 끝 — 정지 결과가 실패다
    node.update()
    assert True not in watch.rotate_calls, \
        "정지를 못 믿는데 회전을 허가했다 — nav2 와 /cmd_vel 을 다툰다"
    assert read(Keys.ACTIVE_COMMAND) is None, \
        "정지가 실패로 끝났는데 안내를 안 끝냈다 — WORKING 에 갇힌다"
    assert read(Keys.NEXT_MODE) == "PATROL", \
        "안내를 끝냈는데 상태 전이를 예약 안 했다 — 로봇이 WORKING 에 남는다"
    assert read(Keys.COMMANDED_MODE) == "PATROL", \
        "패널 유지 시간(HOLD_UNTIL)에 막혀 전이가 유실된다 — `_release` 주석 참고"


def test_stop_success_still_opens_the_rotation(leaf, seed, read):
    """정상 경로는 그대로다 — 정지가 성공하면 회전을 넘기고 안내는 계속된다."""
    stop, watch = FakeDriver(poll_sequence=("success",) * 20), _RotWatch()
    clock = _Clock()
    node = leaf(stop=stop, watch=watch, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t})
    node.update()

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: False,
            Keys.NAV_TARGET: _to(5, 5), Keys.ROBOT_POSE: _at(0, 0),
            Keys.REQUESTER_SEEN_AT: lost_at})
    clock.t = lost_at + RECOVER_AT + 0.1
    node.update()
    assert True in watch.rotate_calls, "정지가 성공했는데 회복을 안 열었다"
    assert read(Keys.ACTIVE_COMMAND) == "guide", "정상 회복인데 안내를 끝냈다"


def test_rotation_stays_shut_while_the_stop_is_still_in_flight(leaf, seed, read):
    """결과가 **아직 안 온** 동안에는 회전도 종료도 없다 — 그냥 기다린다.

    그 창에서 회전을 넘기면 nav2 와 겹치고, 끝내 버리면 멀쩡한 안내가 죽는다.
    되돌림 방지: `_stop_settled()` 를 무조건 True 로 만들면 첫 단언이 빨개지고,
    `"running"` 까지 실패로 세면 둘째 단언이 빨개진다.
    """
    stop, watch = FakeDriver(), _RotWatch()          # 기본 FakeDriver = 계속 "running"
    clock = _Clock()
    node = leaf(stop=stop, watch=watch, clock=clock,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: True,
                   Keys.REQUESTER_SEEN_AT: clock.t})
    node.update()

    lost_at = clock.t
    seed(**{Keys.ACTIVE_COMMAND: "guide", Keys.REQUESTER_VISIBLE: False,
            Keys.NAV_TARGET: _to(5, 5), Keys.ROBOT_POSE: _at(0, 0),
            Keys.REQUESTER_SEEN_AT: lost_at})
    clock.t = lost_at + RECOVER_AT + 0.1
    node.update()
    assert True not in watch.rotate_calls, \
        "정지가 아직 날아다니는데 회전을 허가했다 — nav2 와 /cmd_vel 을 다툰다"
    assert read(Keys.ACTIVE_COMMAND) == "guide", \
        "정지 결과를 기다리는 중인데 안내를 끝냈다"


# ── ROS 로거를 여러 인자로 부르면 노드가 죽는다 (2026-08-02 실기) ─────────────
#
# `RcutilsLogger.warning()` 은 인자가 **하나뿐**이다. `("...%s", x)` 로 부르면
#     TypeError: warning() takes 2 positional arguments but 3 were given
# 이 나고 **fsm_node 가 통째로 죽는다.** 실측: 안내 중 노드가 죽어 관제에서
# state 가 None 이 됐고, 로봇이 응답을 멈췄다. 파이썬 표준 logging 은 이 형태를
# 받아들이므로 코드 리뷰로는 잘 안 걸린다 — 시험으로 붙든다.

def test_ros_loggers_are_called_with_a_single_argument():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "libi_modes"
    bad = []
    for f in root.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            if n.func.attr not in ("info", "warning", "warn", "error", "debug"):
                continue
            owner = ast.unparse(n.func.value)
            if "_log" not in owner and "get_logger" not in owner:
                continue
            if len(n.args) > 1:
                bad.append(f"{f.name}:{n.lineno} {ast.unparse(n)[:70]}")
    assert not bad, (
        "ROS 로거를 여러 인자로 불렀다 — 그 줄이 실행되는 순간 노드가 죽는다. "
        "f-string 을 쓸 것:\n  " + "\n  ".join(bad))
