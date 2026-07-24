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
