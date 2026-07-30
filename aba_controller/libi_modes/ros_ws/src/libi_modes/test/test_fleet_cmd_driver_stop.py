"""응답이 없어 포기한 명령도 **취소는 보내야** 한다.

codex 감사(2026-07-28)에서 나온 결함:

    poll() 이 타임아웃에서 `_pending_id = None` 으로 지운다
      → 곧이어 terminate() 가 stop() 을 부른다
      → stop() 은 `_pending_id is None` 이라 early return, **아무것도 안 보낸다**
      → 원격 세션(follow_node 의 ControlLoop 등)이 고아로 계속 돈다

추종 드라이버의 타임아웃이 3600초라, BT 는 실패로 접었는데 로봇은 여전히 `/cmd_vel` 을
밀 수 있었다. "응답이 없다"는 "상대가 안 돈다"가 아니다 — 오히려 아직 돌고 있을
가능성이 크다.
"""
import pytest

from libi_modes.ros.fleet_cmd_driver import FleetCmdDriver


class _Clock:
    def __init__(self):
        self.t = 0.0


class _FakeNode:
    def __init__(self, clock):
        self._clock = clock

    def get_clock(self):
        clock = self._clock

        class _C:
            @staticmethod
            def now():
                class _N:
                    nanoseconds = int(clock.t * 1e9)
                return _N()
        return _C()

    def get_logger(self):
        class _L:
            @staticmethod
            def warning(_msg):
                pass
        return _L()


class _Pub:
    def __init__(self):
        self.sent = []

    def publish_json(self, payload):
        self.sent.append(payload)

    @property
    def actions(self):
        return [p["action"] for p in self.sent]

    @property
    def last_id(self):
        return self.sent[-1]["id"]


@pytest.fixture
def driver():
    clock = _Clock()
    pub = _Pub()
    d = FleetCmdDriver(_FakeNode(clock), "follow_admin", timeout_sec=100.0).bind(pub)
    return d, pub, clock


def test_start_publishes_the_action(driver):
    d, pub, _ = driver
    d.start()
    assert pub.actions == ["follow_admin"]


def test_stop_cancels_a_pending_command(driver):
    d, pub, _ = driver
    d.start()
    started = pub.last_id
    d.stop()
    assert pub.actions == ["follow_admin", "stop"]
    assert pub.last_id == f"stop-{started}"


def test_stop_after_timeout_still_cancels(driver):
    """이 파일의 존재 이유 — 예전엔 여기서 아무것도 안 나갔다."""
    d, pub, clock = driver
    d.start()
    started = pub.last_id
    clock.t = 200.0
    assert d.poll() == "failure"
    d.stop()
    assert pub.actions == ["follow_admin", "stop"], "포기한 명령을 취소하지 않았다 — 원격 세션이 고아가 된다"
    assert pub.last_id == f"stop-{started}"


def test_stop_after_a_real_result_sends_nothing(driver):
    """결과가 왔다 = 상대가 끝냈다. 취소할 것이 없다."""
    d, pub, _ = driver
    d.start()
    d.on_result({"id": pub.last_id, "ok": True, "msg": ""})
    assert d.poll() == "success"
    d.stop()
    assert pub.actions == ["follow_admin"]


def test_stop_before_start_sends_nothing(driver):
    d, pub, _ = driver
    d.stop()
    assert pub.sent == []


def test_stop_is_not_repeated(driver):
    """두 번 부르면 두 번 나가면 안 된다 — 다음 명령의 id 를 덮어 취소할 위험."""
    d, pub, clock = driver
    d.start()
    clock.t = 200.0
    d.poll()
    d.stop()
    d.stop()
    assert pub.actions.count("stop") == 1


def test_new_command_clears_the_abandoned_id(driver):
    """포기 기록이 새 명령까지 새어 나가면, 방금 낸 명령이 엉뚱한 id 로 취소된다."""
    d, pub, clock = driver
    d.start()
    clock.t = 200.0
    d.poll()                      # 포기
    clock.t = 300.0
    d.start()                     # 새 명령
    fresh = pub.last_id
    d.stop()
    assert pub.last_id == f"stop-{fresh}"


# ── 세션 취소가 주행을 죽이면 안 된다 ────────────────────────────────────────
#
# `stop` 은 소비자가 둘이고 뜻이 다르다:
#   follow_node : 실린 id 의 세션을 닫는다
#   fleet_link  : id 와 무관하게 nav2 목표를 취소한다
# 그래서 추종 세션을 닫으려고 낸 `stop` 이 형제 leaf 가 방금 낸 주행까지 죽였다.
# 실측 2026-07-28 `/fleet_cmd`: goal-1(t=172.393) → stop-follow_admin-1(t=172.404)
# → 재전송(t=182.489)까지 10초를 서 있었다. 사용자 신고: "갑자기 멈춤".

def test_session_driver_uses_follow_stop(driver):
    """세션 드라이버는 `follow_stop` 을 낸다 — fleet_link 의 nav 취소는 `stop` 정확일치만 잡는다."""
    clock = _Clock()
    pub = _Pub()
    d = FleetCmdDriver(_FakeNode(clock), "follow_admin", timeout_sec=100.0,
                       stop_action="follow_stop").bind(pub)
    d.start()
    d.stop()
    assert pub.actions == ["follow_admin", "follow_stop"], "세션 취소가 주행을 끊는 액션을 썼다"


def test_session_stop_keeps_the_stop_id_prefix(driver):
    """접두어는 액션 이름과 **무관하게** `stop-` 이다.

    세션을 찾는 쪽(session.py target_session_id)이 `"stop-"` 만 벗겨 낸다. 접두어를
    액션에 맞춰 바꾸면 id 가 안 맞아 조용히 아무것도 안 닫힌다.
    """
    clock = _Clock()
    pub = _Pub()
    d = FleetCmdDriver(_FakeNode(clock), "follow_admin", timeout_sec=100.0,
                       stop_action="follow_stop").bind(pub)
    d.start()
    started = pub.last_id
    d.stop()
    assert pub.last_id == f"stop-{started}"


def test_nav_driver_still_uses_plain_stop(driver):
    """주행 드라이버는 그대로 `stop` 이다 — 그게 nav2 목표를 실제로 끊는 유일한 수단이다."""
    d, pub, _ = driver
    d.start()
    d.stop()
    assert pub.actions[-1] == "stop"
