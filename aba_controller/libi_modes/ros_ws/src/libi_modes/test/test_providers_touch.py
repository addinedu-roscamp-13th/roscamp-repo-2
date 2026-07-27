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
