from geometry_msgs.msg import Twist


class CmdPublisher:
    """Publishes (linear_x, angular_z) as geometry_msgs/Twist."""

    def __init__(self, node, topic):
        self._pub = node.create_publisher(Twist, topic, 10)

    def publish(self, linear_x, angular_z):
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        self._pub.publish(msg)
