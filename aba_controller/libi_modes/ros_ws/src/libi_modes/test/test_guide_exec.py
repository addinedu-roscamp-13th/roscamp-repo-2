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
import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.working_actions import GuideExec, NavigationExec

from .fakes import FakeDriver

TOLERANCE = 0.1
RESEND = 10.0
TIMEOUT = 60.0
GRACE = 3.0
LOST = 45.0


class _Clock:
    def __init__(self):
        self.t = 100.0        # 0 이 아니어야 "한 번도 못 봄(0.0)"과 구분된다

    def __call__(self):
        return self.t


@pytest.fixture
def leaf(seed):
    def _make(*, clock=None, stop=None, watch=None, far_area_min=0.0,
              near_area_max=0.0, junctions=None, junction_hold_sec=0.0,
              **blackboard):
        clock = clock or _Clock()
        seed(**blackboard)
        node = GuideExec(FakeDriver(), TOLERANCE, RESEND, TIMEOUT, GRACE, LOST,
                         stop_driver=stop, watch_driver=watch,
                         far_area_min=far_area_min, near_area_max=near_area_max,
                         junctions=junctions, junction_hold_sec=junction_hold_sec,
                         now_fn=clock)
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

    p._on_cmd(_Msg(json.dumps({"action": "navigate", "args": {"x": 3.0, "y": 4.0}})))
    assert p.as_dict()["active_command"]() == "navigate"


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
                   Keys.REQUESTER_SEEN_AT: 99.0})      # 1초 전에 보였다 (< GRACE)
    assert node.update() == Status.RUNNING
    assert stop.start_count == 0                 # 아직 안 멈춘다


def test_lost_past_grace_actually_cancels_navigation(leaf):
    """표시만 하고 안 멈추면 로봇은 사람을 두고 간다 — 실제 취소를 내야 한다."""
    stop = FakeDriver()
    node = leaf(stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 95.0})      # 5초 전 (> GRACE)
    assert node.update() == Status.RUNNING       # 포기는 아니다 — 기다린다
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


def test_gives_up_when_the_requester_never_comes_back(leaf, read):
    node = leaf(**{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: False,
                   Keys.REQUESTER_SEEN_AT: 50.0})      # 50초 전 (> LOST)
    assert node.update() == Status.FAILURE
    assert read(Keys.ACTIVE_COMMAND) is None            # 슬롯을 비워야 다음 명령을 받는다


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


def test_losing_the_requester_for_good_also_exits_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(5.0, 5.0), Keys.ROBOT_POSE: _at(0.0, 0.0),
            Keys.REQUESTER_VISIBLE: False, Keys.REQUESTER_SEEN_AT: 10.0})  # 90초 전
    tick(_working())
    assert read(Keys.NEXT_MODE) == "PATROL", "포기했으면 WORKING 에 갇히면 안 된다"


def test_guide_command_reaches_guide_exec_not_navigation(seed, tick):
    """dispatch Selector 에서 앞의 NavigationExec 이 가로채면 안 된다."""
    nav, guide = FakeDriver(), FakeDriver()
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "guide",
            Keys.NAV_TARGET: _to(5.0, 5.0), Keys.ROBOT_POSE: _at(0.0, 0.0),
            Keys.REQUESTER_VISIBLE: True, Keys.REQUESTER_SEEN_AT: 100.0})
    tick(_working(nav=nav, guide=guide))
    assert guide.started and not nav.started


def test_no_watcher_means_drive_anyway(leaf):
    """libi_perception 이 없는 로봇에서 길잡이가 통째로 죽으면 안 된다."""
    stop = FakeDriver()
    node = leaf(stop=stop,
                **{Keys.ACTIVE_COMMAND: "guide", Keys.NAV_TARGET: _to(5, 5),
                   Keys.ROBOT_POSE: _at(0, 0), Keys.REQUESTER_VISIBLE: None,
                   Keys.REQUESTER_SEEN_AT: 0.0})
    assert node.update() == Status.RUNNING
    assert stop.start_count == 0


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
