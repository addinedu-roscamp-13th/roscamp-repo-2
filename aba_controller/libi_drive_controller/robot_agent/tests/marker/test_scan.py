"""ScanWatch — 근접 보호가 보는 유일한 값이라 규약을 지켜야 한다.

ROS 없이 돌리려고 최소 스텁을 끼운다. 노드는 구독 등록만 받으면 되므로 가짜로 둔다.
"""
import math
import sys
import types

import pytest


@pytest.fixture
def scan_module(monkeypatch):
    for name in ("rclpy", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["rclpy.qos"].qos_profile_sensor_data = None
    sys.modules["sensor_msgs.msg"].LaserScan = object
    monkeypatch.delitem(sys.modules, "app.marker.scan", raising=False)
    import app.marker.scan as scan
    return scan


class FakeNode:
    def __init__(self):
        self.cb = None

    def create_subscription(self, _msg, _topic, cb, _qos):
        self.cb = cb


def make_scan(ranges, range_min=0.05, range_max=12.0):
    msg = types.SimpleNamespace()
    msg.ranges = ranges
    msg.range_min, msg.range_max = range_min, range_max
    msg.angle_min = -math.pi
    msg.angle_increment = 2 * math.pi / len(ranges)
    return msg


def feed(scan_module, ranges, **kw):
    node = FakeNode()
    watch = scan_module.ScanWatch(node, half_angle_deg=15.0)
    node.cb(make_scan(ranges, **kw))
    return watch


def test_front_sector_minimum_is_taken(scan_module):
    ranges = [5.0] * 360
    ranges[0] = 0.30          # angle_min 기준 0번은 뒤쪽(-pi)
    ranges[180] = 0.40        # 정면(0 rad)
    watch = feed(scan_module, ranges)
    assert watch.front_m == pytest.approx(0.40)


def test_values_below_range_min_are_rejected(scan_module):
    """LaserScan 규약상 range_min 미만은 무효다.

    그대로 믿으면 없는 장애물 앞에서 즉시 정지한다.
    """
    ranges = [5.0] * 360
    ranges[180] = 0.01        # range_min(0.05) 미만 = 무효
    watch = feed(scan_module, ranges)
    assert watch.front_m == pytest.approx(5.0)


def test_values_above_range_max_are_rejected(scan_module):
    ranges = [5.0] * 360
    ranges[180] = 99.0
    watch = feed(scan_module, ranges)
    assert watch.front_m == pytest.approx(5.0)


def test_non_finite_values_are_rejected(scan_module):
    ranges = [float("inf")] * 360
    ranges[180] = float("nan")
    watch = feed(scan_module, ranges)
    assert watch.front_m is None      # 볼 수 있는 값이 하나도 없다


def test_age_is_infinite_before_any_scan(scan_module):
    watch = scan_module.ScanWatch(FakeNode())
    assert watch.age() == float("inf")
    assert watch.ready is False


def test_age_resets_on_each_scan(scan_module):
    watch = feed(scan_module, [1.0] * 360)
    assert watch.ready is True
    assert watch.age() < 0.5


# ------------------------------------------------- 고장난 라이다 구분

def test_all_nan_scan_is_marked_invalid(scan_module):
    """스캔은 제때 오는데 값이 전부 NaN = 라이다 고장.

    front_m 은 그때도 None 이라 '전방이 트여 있음'과 구분이 안 된다.
    이 구분이 없으면 고장난 심장박동을 건강으로 세고 전진한다.
    """
    watch = feed(scan_module, [float("nan")] * 360)
    assert watch.ready is True          # 메시지는 왔다
    assert watch.valid is False         # 그런데 쓸 값이 없다
    assert watch.front_m is None


def test_open_space_scan_is_valid(scan_module):
    """전부 inf 는 '그 범위 안에 아무것도 없음'이라는 정상 관측이다."""
    watch = feed(scan_module, [float("inf")] * 360)
    assert watch.valid is True
    assert watch.front_m is None        # 전방에 잡히는 게 없을 뿐


def test_forward_offset_moves_the_watched_sector(scan_module):
    """라이다가 180° 돌아 달렸으면 감시 섹터도 같이 돌아야 한다."""
    ranges = [5.0] * 360
    ranges[0] = 0.30                    # angle_min(-pi) 위치 = 오프셋 180°일 때의 전방
    node = FakeNode()
    watch = scan_module.ScanWatch(node, half_angle_deg=15.0, forward_deg=180.0)
    node.cb(make_scan(ranges))
    assert watch.front_m == pytest.approx(0.30)
