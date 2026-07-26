"""ROS2 entry point for LIBI person-following.

FollowSession is ROS-free and testable on its own; the rclpy node below only wires
transports to it and ticks it. libi_modes' FollowExec sees nothing but FollowSession's
start()/poll()/stop() contract, so the follower's internals stay replaceable.

Topic names and the detection port are ROS parameters, not constants. Where this node
runs is still an open question — it needs /scan and /cmd_vel, which belong to the driving
Pi, so it may end up deployed there rather than on the mission PC. Parameterising the
transports keeps that a launch-time decision instead of a code change.
"""
from . import config
from .control_loop import ControlLoop
from .detection_receiver import DetectionReceiver
from .tcp_detection_source import TcpDetectionSource


class FollowSession:
    """start()/poll()/stop() wrapper around a ControlLoop, matching the driver contract
    libi_modes' CommandDrivenAction expects.

    poll() mapping:
      not started / recovery gave up -> 'failure'
      TRACKING or SEARCHING          -> 'running'
      stopped on request             -> 'success'

    An admin-follow session never finishes by itself. It ends because an admin stopped it
    (success) or because recovery exhausted and the person is gone (failure) — so 'success'
    deliberately means "told to stop", not "arrived".
    """

    def __init__(self, loop_factory, publish=None):
        self._loop_factory = loop_factory
        #: 세션 종료 시 정지 명령을 낼 발행자. 없으면(테스트) 아무것도 안 한다.
        #: `_loop.publish` 를 몰래 꺼내 쓰지 않고 명시적으로 받는다 — 세션의 책임이
        #: "루프를 갈아끼우는 것"과 "멈추는 것" 둘 다이므로, 멈출 수단은 세션이 가져야 한다.
        self._publish = publish
        self._loop = None
        self._stopped = False

    def start(self):
        self._loop = self._loop_factory()
        self._stopped = False

    def poll(self):
        if self._stopped:
            return 'success'
        if self._loop is None:
            return 'failure'
        return 'failure' if self._loop.state == 'ENDED' else 'running'

    def stop(self):
        # 루프를 버리기 전에 정지 명령을 낸다. 이게 없으면 마지막으로 발행한 속도가
        # 그대로 살아남는다 — 관리자가 "중단"을 눌렀는데 로봇이 계속 굴러간다.
        # (베이스의 cmd_vel 타임아웃에 기대면 안 된다. 있는지 보장되지 않는다.)
        if self._publish is not None:
            self._publish(0.0, 0.0)
        self._loop = None
        self._stopped = True

    def tick(self):
        if self._loop is not None:
            self._loop.tick()


def main(args=None):
    import rclpy
    from rclpy.node import Node

    from .cmd_publisher import CmdPublisher
    from .scan_provider import ScanProvider

    class FollowNode(Node):
        def __init__(self):
            super().__init__('libi_perception')
            self.declare_parameter('scan_topic', config.SCAN_TOPIC)
            self.declare_parameter('cmd_vel_topic', config.CMD_VEL_TOPIC)
            self.declare_parameter('detection_host', config.DETECTION_TCP_HOST)
            self.declare_parameter('detection_port', config.DETECTION_TCP_PORT)
            self.declare_parameter('autostart', True)

            scan_topic = self.get_parameter('scan_topic').value
            cmd_topic = self.get_parameter('cmd_vel_topic').value
            host = self.get_parameter('detection_host').value
            port = int(self.get_parameter('detection_port').value)

            self._scan = ScanProvider(self, scan_topic)
            self._cmd = CmdPublisher(self, cmd_topic)
            self._receiver = DetectionReceiver(TcpDetectionSource(host, port))
            self.session = FollowSession(self._make_loop, publish=self._cmd.publish)
            if self.get_parameter('autostart').value:
                self.session.start()
            self.create_timer(1.0 / config.TICK_HZ, self.session.tick)
            self.get_logger().info(
                f'libi_perception up — scan={scan_topic} cmd_vel={cmd_topic} '
                f'detections={host}:{port}')

        def _make_loop(self):
            return ControlLoop(
                get_detection=self._get_detection,
                get_scan=self._scan.get,
                publish=self._cmd.publish,
                cfg=config,
                now=lambda: self.get_clock().now().nanoseconds / 1e9,
            )

        def _get_detection(self):
            self._receiver.update()
            return self._receiver.latest()

    rclpy.init(args=args)
    node = FollowNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
