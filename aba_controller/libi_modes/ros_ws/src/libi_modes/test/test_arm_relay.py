"""팔 명령 중계 — `/fleet_cmd{perform_action}` → `arm_task` 액션.

여기서 지키는 것:
  ① `/fleet_cmd` args 로부터 goal 필드가 유도된다 (FMS 가 아직 새 필드를 안 보내므로)
  ② **FMS 가 보낸 값이 유도값을 이긴다** (정본이 둘이 되면 안 된다)
  ③ 모르는 액션·장소는 지어내지 않고 실패한다
  ④ providers 가 팔 args 를 **보관한다** (예전엔 버려서 어느 책인지가 사라졌다)
  ⑤ 드라이버 상태기계: 서버 없음 / goal 거절 / 성공 / 실패 / 타임아웃
"""
import json
from types import SimpleNamespace

import pytest

from libi_modes.arm_task_map import (BIN, DESK, FLOOR, LIBI, SHELF, TABLE,
                                     place_of, to_goal_fields)
from libi_modes.ros.handy_action_driver import HandyActionDriver


# ── ① 유도 ───────────────────────────────────────────────────────────────────

def test_pick_is_derived_from_action_name():
    g = to_goal_fields({"action": "pick", "book": "코스모스", "at": "과학-인문학서가"})
    assert g["object"] == "book"
    assert (g["from_place"], g["to_place"]) == (SHELF, LIBI)
    assert g["book"] == "코스모스"
    # 좌표는 도서 DB 값이라 유도할 수 없다 — FMS 가 보내기 전까지 0 이다
    assert (g["tier"], g["row"], g["slot"]) == (0, 0, 0)


def test_place_destination_comes_from_waypoint():
    assert to_goal_fields({"action": "place", "at": "1번테이블"})["to_place"] == TABLE
    assert to_goal_fields({"action": "place", "at": "2번테이블"})["to_place"] == TABLE
    assert to_goal_fields({"action": "place", "at": "안내데스크"})["to_place"] == DESK


def test_map_typo_is_normalised():
    """지도 데이터의 실제 표기는 `안네데스크`(오타)다 — 고치면 주행 정점이 깨진다.

    그래서 중계에서 정규화하고, 팔에는 올바른 표기만 나간다.
    """
    assert place_of("안네데스크") == DESK
    assert to_goal_fields({"action": "place", "at": "안네데스크"})["to_place"] == DESK


@pytest.mark.parametrize("action,src,dst", [
    ("unload_to_floor", LIBI, FLOOR),
    ("load_from_box", BIN, LIBI),
    ("refill_box", FLOOR, BIN),
])
def test_basket_moves_are_fixed_regardless_of_waypoint(action, src, dst):
    g = to_goal_fields({"action": action, "book": "바구니", "at": "수거함"})
    assert (g["object"], g["from_place"], g["to_place"]) == ("basket", src, dst)
    # `book:"바구니"` 는 대상 종류를 도서명 필드에 위장해 넣은 값이다 — 팔에 넘기지 않는다
    assert g["book"] == ""
    assert (g["tier"], g["row"], g["slot"]) == (0, 0, 0)


# ── ② FMS 우선 ───────────────────────────────────────────────────────────────

def test_explicit_fields_win_over_derivation():
    g = to_goal_fields({"action": "place", "at": "1번테이블",
                        "to_place": DESK, "object": "book", "book": "x"})
    assert g["to_place"] == DESK, "FMS 가 명시한 값이 유도값을 이겨야 한다"


def test_coordinates_pass_through_when_fms_sends_them():
    g = to_goal_fields({"action": "pick", "book": "x", "at": "문학서가",
                        "tier": 3, "row": 2, "slot": 1})
    assert (g["tier"], g["row"], g["slot"]) == (3, 2, 1)


# ── ③ 지어내지 않는다 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [
    {"action": "throw", "at": "문학서가"},          # 모르는 액션
    {"action": "place", "at": "미정"},              # 장소로 못 읽는 정점
    {"action": "place", "at": ""},
    {"action": "pick", "at": "문학서가", "from_place": "창고"},   # 모르는 장소 명시
    {"action": "pick", "at": "문학서가", "object": "사람"},       # 모르는 대상 명시
    {},
    None,
])
def test_unknown_input_fails_instead_of_guessing(args):
    assert to_goal_fields(args) is None


# ── ④ providers 가 팔 args 를 보관한다 ───────────────────────────────────────

def test_providers_keep_arm_args():
    """예전엔 팔 갈래가 `active_command` 만 세우고 args 를 버렸다 — 어느 책을 어디서
    집으라는 정보가 통째로 사라졌다(팔이 스텁이라 안 드러났다)."""
    from libi_modes.ros.providers import RosProviders
    p = RosProviders.__new__(RosProviders)          # __init__ 우회 (ROS 노드 불필요)
    p._command_received_at = 0.0
    p._nav_actions, p._guide_actions = set(), set()
    p._mission_actions, p._follow_actions = set(), set()
    p._arm_actions = {"perform_action"}
    p._fsm_triggers = set()
    p._active_command = p._last_command = p._nav_target = None
    p._arm_args = p._arm_cmd_id = None
    p._log = SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None)

    args = {"action": "pick", "book": "코스모스", "at": "문학서가"}
    RosProviders._on_cmd(p, SimpleNamespace(
        data=json.dumps({"id": "c-42", "action": "perform_action", "args": args})))

    assert p._active_command == "perform_action"
    assert p._arm_args == args
    # id 도 보관해야 한다 — 완료를 이 id 로 올려야 FMS 가 다리를 닫는다(다른 값이면 무시된다).
    assert p._arm_cmd_id == "c-42"


# ── ⑤ 드라이버 상태기계 ──────────────────────────────────────────────────────

class _Fut:
    def __init__(self, value=None, done=True):
        self._v, self._done = value, done
        self._cbs = []

    def done(self):
        return self._done

    def result(self):
        return self._v

    def add_done_callback(self, cb):
        """rclpy future 의 계약. 이미 끝났으면 즉시, 아니면 `resolve()` 때 부른다."""
        if self._done:
            cb(self)
        else:
            self._cbs.append(cb)

    def resolve(self, value):
        """수락이 **늦게** 오는 상황을 재현한다 (고아 goal 검증용)."""
        self._v, self._done = value, True
        for cb in self._cbs:
            cb(self)


class _Handle:
    def __init__(self, accepted=True, result=None):
        self.accepted = accepted
        self._result = result
        self.cancelled = False

    def get_result_async(self):
        return _Fut(SimpleNamespace(result=self._result))

    def cancel_goal_async(self):
        self.cancelled = True
        return _Fut(None)


class _Client:
    def __init__(self, ready=True, handle=None, send_done=True):
        self._ready, self._handle, self._send_done = ready, handle, send_done
        self.sent = []

    def server_is_ready(self):
        return self._ready

    def send_goal_async(self, goal, feedback_callback=None):
        self.sent.append(goal)
        self.last_future = _Fut(self._handle, done=self._send_done)
        return self.last_future


def _driver(client, args, now=0.0, reports=None):
    from libi_interfaces.action import ArmTask
    d = HandyActionDriver.__new__(HandyActionDriver)   # __init__ 우회 (ROS 노드 불필요)
    d._node = None
    d._args_fn = lambda: args
    d._timeout_sec = 120.0
    d._result_fn = (lambda ok, msg: reports.append((ok, msg))) if reports is not None else None
    d._log = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                             debug=lambda *a: None)
    d._ArmTask = ArmTask
    d._client = client
    d._now = lambda: now
    d._reset()
    return d


def test_unmappable_args_fail_without_sending():
    c = _Client()
    d = _driver(c, {"action": "throw"})
    d.start()
    assert d.poll() == "failure"
    assert c.sent == [], "goal 을 보내면 안 된다"


def test_missing_server_fails_immediately():
    c = _Client(ready=False)
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    assert d.poll() == "failure"
    assert c.sent == []


def test_rejected_goal_is_failure():
    c = _Client(handle=_Handle(accepted=False))
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    assert d.poll() == "failure"


def test_success_result():
    c = _Client(handle=_Handle(result=SimpleNamespace(ok=True, msg="")))
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    assert len(c.sent) == 1
    g = c.sent[0]
    assert (g.action, g.object, g.from_place, g.to_place) == ("pick", "book", SHELF, LIBI)
    assert d.poll() == "success"


def test_failed_result():
    c = _Client(handle=_Handle(result=SimpleNamespace(ok=False, msg="그립 실패")))
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    assert d.poll() == "failure"


def test_running_while_goal_not_accepted_yet():
    c = _Client(handle=None, send_done=False)
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    assert d.poll() == "running"


def test_timeout_cancels_and_fails():
    """응답이 안 오면 취소를 보내고 실패시킨다 — 고아 goal 을 남기지 않는다."""
    h = _Handle(result=None)
    c = _Client(handle=h)
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    d._result_future = _Fut(None, done=False)     # 결과가 아직 안 왔다
    d._handle = h
    d._now = lambda: 999.0                        # 타임아웃 경과
    assert d.poll() == "failure"
    assert h.cancelled, "타임아웃 시 취소를 보내야 한다"


def test_stop_cancels_the_goal():
    h = _Handle(result=SimpleNamespace(ok=True, msg=""))
    c = _Client(handle=h)
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    d.poll()                                       # handle 확보
    d._handle = h                                  # 성공으로 비워졌으니 되돌려 stop 검증
    d.stop()
    assert h.cancelled


# ── ⑥ 고아 goal — 수락보다 취소가 먼저 온 경우 ────────────────────────────────

def test_goal_accepted_after_stop_is_cancelled():
    """정지가 수락보다 먼저 오면 취소할 handle 이 아직 없다.

    그대로 잊으면 서버는 뒤늦게 수락하고 **아무도 안 보는 팔이 계속 움직인다** — 그 사이
    주행이 시작되면 팔을 뻗은 채 로봇이 간다. 취소 의사를 남겨 콜백이 끊어야 한다.
    """
    h = _Handle()
    c = _Client(handle=h, send_done=False)          # 수락이 아직 안 왔다
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    d.stop()                                        # handle 없이 정지
    assert not h.cancelled                          # 아직 취소할 대상이 없다
    c.last_future.resolve(h)                        # 서버가 뒤늦게 수락
    assert h.cancelled, "취소한 뒤 수락된 goal 을 끊지 않았다 (고아 goal)"


def test_goal_accepted_after_timeout_is_cancelled():
    h = _Handle()
    c = _Client(handle=h, send_done=False)
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    d._now = lambda: 999.0
    assert d.poll() == "failure"
    c.last_future.resolve(h)
    assert h.cancelled


def test_send_future_exception_is_a_failure():
    """링크가 끊겨 future 가 예외로 끝나도 트리는 죽지 않고 실패로 내려간다."""
    class _Boom(_Fut):
        def result(self):
            raise RuntimeError("링크 끊김")

    c = _Client(handle=_Handle())
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"})
    d.start()
    d._send_future = _Boom(None)
    assert d.poll() == "failure"


# ── ⑦ 완료 보고 — 관제 다리를 닫는 유일한 통로 ────────────────────────────────

def test_success_is_reported_once():
    reports = []
    c = _Client(handle=_Handle(result=SimpleNamespace(ok=True, msg="")))
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"}, reports=reports)
    d.start()
    assert d.poll() == "success"
    assert reports == [(True, "")]


def test_failure_is_reported_with_reason():
    reports = []
    c = _Client(handle=_Handle(result=SimpleNamespace(ok=False, msg="그립 실패")))
    d = _driver(c, {"action": "pick", "book": "x", "at": "문학서가"}, reports=reports)
    d.start()
    assert d.poll() == "failure"
    assert reports == [(False, "그립 실패")]


def test_unmappable_args_are_reported_too():
    """goal 을 못 만들어도 **답은 해야 한다** — 안 하면 관제 주문이 영원히 안 닫힌다."""
    reports = []
    d = _driver(_Client(), {"action": "throw"}, reports=reports)
    d.start()
    assert d.poll() == "failure"
    assert len(reports) == 1 and reports[0][0] is False


def test_missing_server_is_reported():
    reports = []
    d = _driver(_Client(ready=False), {"action": "pick", "book": "x", "at": "문학서가"},
                reports=reports)
    d.start()
    assert d.poll() == "failure"
    assert len(reports) == 1 and reports[0][0] is False
