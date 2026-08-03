"""OdomTracker — 옆으로 밀린 거리를 전진으로 세지 않는지 여기서 확인한다.

상태기계는 이미 투영된 forward_m 만 받으므로, 그쪽 테스트로는 이 성질을 못 본다.
"""
import math
import sys
import types

import pytest


@pytest.fixture
def odom_module(monkeypatch):
    for name in ("rclpy", "rclpy.qos", "nav_msgs", "nav_msgs.msg"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["rclpy.qos"].qos_profile_sensor_data = None
    sys.modules["nav_msgs.msg"].Odometry = object
    monkeypatch.delitem(sys.modules, "app.marker.odom", raising=False)
    import app.marker.odom as odom
    return odom


class FakeNode:
    def __init__(self):
        self.cb = None

    def create_subscription(self, _msg, _topic, cb, _qos):
        self.cb = cb


def pose(x, y, yaw_rad):
    msg = types.SimpleNamespace()
    msg.pose = types.SimpleNamespace()
    msg.pose.pose = types.SimpleNamespace()
    msg.pose.pose.position = types.SimpleNamespace(x=x, y=y, z=0.0)
    msg.pose.pose.orientation = types.SimpleNamespace(
        x=0.0, y=0.0, z=math.sin(yaw_rad / 2), w=math.cos(yaw_rad / 2))
    return msg


def track(odom_module, poses):
    node = FakeNode()
    tracker = odom_module.OdomTracker(node)
    for p in poses:
        node.cb(p)
    return tracker


def test_forward_motion_accumulates(odom_module):
    t = track(odom_module, [pose(0, 0, 0), pose(0.05, 0, 0), pose(0.10, 0, 0)])
    assert t.forward_m == pytest.approx(0.10, abs=1e-6)


def test_sideways_motion_is_not_forward(odom_module):
    """헤딩이 +x 인데 +y 로 밀렸다 — 벽까지 남은 거리는 그대로다."""
    t = track(odom_module, [pose(0, 0, 0), pose(0, 0.10, 0)])
    assert t.forward_m == pytest.approx(0.0, abs=1e-6)


def test_backward_motion_subtracts(odom_module):
    t = track(odom_module, [pose(0, 0, 0), pose(0.10, 0, 0), pose(0.04, 0, 0)])
    assert t.forward_m == pytest.approx(0.04, abs=1e-6)


def test_forward_follows_heading(odom_module):
    """90° 돌아선 뒤 +y 로 간 것은 전진이다."""
    t = track(odom_module, [pose(0, 0, math.pi / 2), pose(0, 0.08, math.pi / 2)])
    assert t.forward_m == pytest.approx(0.08, abs=1e-6)


def test_yaw_unwraps_across_pi(odom_module):
    t = track(odom_module, [pose(0, 0, 3.0), pose(0, 0, -3.0)])
    assert t.yaw_deg == pytest.approx(math.degrees(2 * math.pi - 6.0), abs=0.5)


def test_age_and_ready(odom_module):
    node = FakeNode()
    t = odom_module.OdomTracker(node)
    assert t.ready is False and t.age() == float("inf")
    node.cb(pose(0, 0, 0))
    assert t.ready is True and t.age() < 0.5
