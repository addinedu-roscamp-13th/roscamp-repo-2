import json
import time
from types import SimpleNamespace


def test_ui_touch_stamps_receive_monotonic():
    # rclpy 없이 콜백만 테스트 — RosProviders.__init__ 은 node.create_subscription 을 부르므로
    # 콜백 메서드만 떼어 검증한다.
    from libi_modes.ros.providers import RosProviders
    p = RosProviders.__new__(RosProviders)          # __init__ 우회 (ROS 노드 불필요)
    p._ui_last_touch_at = 0.0

    before = time.monotonic()
    RosProviders._on_ui_touch(p, SimpleNamespace(data=999999.0))   # payload 는 엉뚱한 값
    after = time.monotonic()

    assert before <= p._ui_last_touch_at <= after   # 수신 시점 monotonic, payload 아님
    assert p._ui_last_touch_at != 999999.0


def test_command_received_at_is_monotonic_not_epoch():
    """`command_received_at` 은 **monotonic** 이어야 한다.

    이 값을 읽는 곳은 CommandTimeout 하나뿐이고, 그쪽은 `time.monotonic()` 으로 현재
    시각을 잰다. 여기에 ROS 시계(=epoch 초)를 찍으면
    `since = max(last_activity, received_at)` 이 항상 epoch 을 고르고
    `now - since` 가 -17억이 되어 **120초 타임아웃이 영원히 성립하지 않는다.**
    (2026-07-26 실측: monotonic 24,328 vs epoch 1,785,051,205)
    """
    from libi_modes.ros.providers import RosProviders
    p = RosProviders.__new__(RosProviders)          # __init__ 우회 (ROS 노드 불필요)
    p._command_received_at = 0.0
    p._nav_actions = set()
    p._guide_actions = set()
    p._mission_actions = set()
    p._arm_actions = set()
    p._follow_actions = set()
    # 분류 안 된 액션은 이제 슬롯을 건드리지 않는다 — FSM 트리거만 통과한다
    # (registry.TRANSITION_TRIGGERS / test_fleet_cmd_slot.py 참고).
    p._fsm_triggers = {"task_assigned"}
    p._active_command = None
    p._last_command = None

    before = time.monotonic()
    RosProviders._on_cmd(p, SimpleNamespace(data=json.dumps({"action": "task_assigned"})))
    after = time.monotonic()

    assert before <= p._command_received_at <= after
    # epoch(초) 는 monotonic 보다 자릿수가 훨씬 크다. 시계가 섞이면 여기서 걸린다.
    assert p._command_received_at < time.time() / 2, (
        "epoch 시계로 찍혔다 — CommandTimeout 이 영원히 안 걸리는 회귀"
    )


# ── 요청자 가시성 신선도 ─────────────────────────────────────────────────────
# AI 서버나 follow_node 가 죽어 발행이 끊기면, 마지막 True 가 영원히 남아
# 로봇이 "요청자가 계속 보인다"고 믿고 nav2 를 계속 몬다.

class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _providers(ttl=2.0):
    """__init__ 을 우회해 콜백·조회만 검증한다(ROS 노드 불필요)."""
    from libi_modes.ros.providers import RosProviders
    p = RosProviders.__new__(RosProviders)
    p._requester_visible = None
    p._requester_seen_at = 0.0
    p._requester_stamp = None
    p._requester_area = None
    p._requester_area_stamp = None
    p._requester_ttl = ttl
    p._now = _Clock()
    return p


def test_requester_visible_none_when_never_published():
    """한 번도 안 왔으면 None — '감시 없음'이라 길잡이는 그냥 주행한다(기존 계약)."""
    from libi_modes.ros.providers import RosProviders
    p = _providers()
    assert RosProviders._fresh_requester_visible(p) is None


def test_requester_visible_true_while_fresh():
    from libi_modes.ros.providers import RosProviders
    p = _providers()
    RosProviders._on_requester(p, SimpleNamespace(data=True))
    p._now.t = 1.0
    assert RosProviders._fresh_requester_visible(p) is True


def test_requester_visible_goes_false_when_stale():
    """stale 을 None 으로 내리면 '감시 없음 → 그냥 주행'이 되어 정반대로 간다."""
    from libi_modes.ros.providers import RosProviders
    p = _providers(ttl=2.0)
    RosProviders._on_requester(p, SimpleNamespace(data=True))
    p._now.t = 10.0
    assert RosProviders._fresh_requester_visible(p) is False


def test_requester_seen_at_updates_only_when_visible():
    """안 보이는 동안에도 갱신하면 '얼마나 오래 안 보였나'가 항상 0 이 된다."""
    from libi_modes.ros.providers import RosProviders
    p = _providers()
    RosProviders._on_requester(p, SimpleNamespace(data=True))
    seen = p._requester_seen_at
    p._now.t = 1.0
    RosProviders._on_requester(p, SimpleNamespace(data=False))
    assert p._requester_seen_at == seen


def test_requester_area_expires():
    from libi_modes.ros.providers import RosProviders
    p = _providers(ttl=2.0)
    RosProviders._on_requester_area(p, SimpleNamespace(data=900.0))
    p._now.t = 0.5
    assert RosProviders._fresh_requester_area(p) == 900.0
    p._now.t = 10.0
    assert RosProviders._fresh_requester_area(p) is None


def test_requester_area_none_when_never_published():
    from libi_modes.ros.providers import RosProviders
    assert RosProviders._fresh_requester_area(_providers()) is None


def test_ttl_zero_disables_expiry():
    from libi_modes.ros.providers import RosProviders
    p = _providers(ttl=0.0)
    RosProviders._on_requester(p, SimpleNamespace(data=True))
    p._now.t = 10_000.0
    assert RosProviders._fresh_requester_visible(p) is True
