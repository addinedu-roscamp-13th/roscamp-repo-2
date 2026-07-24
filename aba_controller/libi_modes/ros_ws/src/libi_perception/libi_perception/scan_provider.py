from sensor_msgs.msg import LaserScan
from rclpy.qos import qos_profile_sensor_data


class ScanProvider:
    """Caches the latest /scan ranges as a plain list."""

    def __init__(self, node, topic):
        self._ranges = []
        node.create_subscription(LaserScan, topic, self._cb,
                                 qos_profile_sensor_data)

    def _cb(self, msg):
        self._ranges = list(msg.ranges)

    def get(self):
        return self._ranges
