"""Laptop-side ROS2 /scan subscriber -> 4-sector distances (front/back/left/right).

Kept in its OWN module so perception_server stays ROS-free unless the operator
opts in with --lidar-ros. Importing this requires ROS2 sourced (rclpy on the
PYTHONPATH) and a ROS_DOMAIN_ID matching the robot. Cross-machine discovery
(same domain + LAN multicast) must work for /scan to arrive.

The result is display-only telemetry; it does NOT affect driving (avoidance is
robot-local in cmd_bridge).
"""
import threading
import time

from scripts.lidar_avoid import sectors8


class ScanRosSource:
    """Subscribes to /scan in a background thread; latest() returns the newest
    (front, back, left, right) in metres, or None if stale / nothing yet."""

    def __init__(self, topic="/scan", flip_180=False):
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import LaserScan

        self._flip = flip_180
        self._latest = None                 # (front, back, left, right, monotonic_recv)
        self._lock = threading.Lock()

        if not rclpy.ok():
            rclpy.init()
        self._rclpy = rclpy
        self._node = Node("libi_scan_view")
        self._node.create_subscription(
            LaserScan, topic, self._on_scan, qos_profile_sensor_data)
        self._t = threading.Thread(target=self._spin, daemon=True)
        self._t.start()

    def _on_scan(self, msg):
        s = sectors8(list(msg.ranges), msg.angle_min,
                     msg.angle_increment, flip_180=self._flip)
        with self._lock:
            self._latest = (s, time.monotonic())

    def _spin(self):
        try:
            self._rclpy.spin(self._node)
        except Exception:
            pass

    def latest(self, max_age=1.0):
        """The eight-sector dict (see lidar_avoid.sectors8), or None if stale."""
        with self._lock:
            v = self._latest
        if v is None or (time.monotonic() - v[1]) > max_age:
            return None
        return v[0]

    def close(self):
        try:
            self._node.destroy_node()
        except Exception:
            pass
