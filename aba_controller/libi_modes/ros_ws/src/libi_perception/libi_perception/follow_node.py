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


#: 회복 BT 스냅샷을 실어 보내는 토픽. libi_modes 의 StateIO 가 이걸 받아
#: 자기 트리의 `FollowExec` **밑에 접붙여** `/libi/bt_snapshot` 하나로 내보낸다.
#: 새 파이프를 만들지 않는 이유: 관제 화면은 이미 그 토픽 하나만 본다.
FOLLOW_SNAPSHOT_TOPIC = '/libi/follow_bt_snapshot'

#: py_trees Status → 스냅샷 문자열. state_io._STATUS_NAME 과 같은 표기를 쓴다.
_STATUS_NAME = {'SUCCESS': 'SUCCESS', 'FAILURE': 'FAILURE',
                'RUNNING': 'RUNNING', 'INVALID': 'INVALID'}


def _kind(node) -> str:
    """노드의 성격. libi_modes 의 state_io._kind 와 **같은 표기**여야 한다 —
    두 트리가 한 화면에 이어 붙으므로 표기가 다르면 읽는 사람이 헷갈린다."""
    cls = type(node).__name__
    if cls == 'Parallel':
        return f"Parallel/{type(getattr(node, 'policy', None)).__name__}"
    if cls in ('Sequence', 'Selector'):
        return f'{cls}*' if getattr(node, 'memory', False) else cls
    return cls


def snapshot_dict(node):
    """py_trees 노드 → `{name, kind, status, children}`. state_io._to_dict 와 같은 모양."""
    st = getattr(node, 'status', None)
    return {
        'name': node.name,
        'kind': _kind(node),
        'status': _STATUS_NAME.get(getattr(st, 'name', str(st)), 'INVALID'),
        'children': [snapshot_dict(c) for c in getattr(node, 'children', []) or []],
    }


class RemoteControl:
    """`/fleet_cmd` 왕복으로 세션을 켜고 끈다. 미션 BT(libi_modes)가 이걸로 부른다.

    ## 새 프로토콜을 만들지 않는다
    로봇에는 이미 `/fleet_cmd`(JSON) → 실행 → `/fleet_cmd_result` 왕복이 있고,
    libi_modes 의 모든 액션 leaf 가 그 위에서 돈다(`FleetCmdDriver`). 추종만 별도
    채널을 두면 id 대조·타임아웃·취소를 한 벌 더 만들게 된다. 같은 통로를 쓴다.

    ## 결과를 언제 돌려주나
    추종 세션은 **스스로 끝나지 않는다.** 관리자가 멈추거나(success) 회복이 소진돼
    사람을 놓치면(failure) 끝난다. 그래서 `start` 직후에 결과를 내지 않고, 세션이
    실제로 끝난 tick 에 낸다 — 그래야 BT 의 `poll()` 이 "추종 중"을 running 으로 본다.

    ## 취소
    `FleetCmdDriver.stop()` 은 액션 이름이 아니라 `"stop"` 을 보낸다(id 는
    `stop-<원래id>`). 그래서 여기서도 `stop` 을 받아 세션을 끊는다.
    """

    #: 이 노드가 반응하는 액션. libi_modes 의 FollowExec.handles 와 같은 이름이어야 한다.
    START_ACTION = 'follow_admin'
    STOP_ACTIONS = ('stop', 'follow_stop')

    def __init__(self, node, session, cmd_topic='fleet_cmd',
                 result_topic='fleet_cmd_result'):
        from std_msgs.msg import String
        self._node = node
        self._session = session
        self._log = node.get_logger()
        self._active_id = None
        self._result_pub = node.create_publisher(String, result_topic, 10)
        self._snap_pub = node.create_publisher(String, FOLLOW_SNAPSHOT_TOPIC, 10)
        node.create_subscription(String, cmd_topic, self._on_cmd, 10)

    def _on_cmd(self, msg):
        import json
        try:
            cmd = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        action = str(cmd.get('action', '')).strip()
        if action == self.START_ACTION:
            # 이미 돌고 있으면 이전 세션을 실패로 닫고 새 id 로 갈아탄다 — 두 세션이
            # 같은 cmd_vel 을 동시에 밀면 로봇이 떨린다.
            if self._active_id is not None:
                self._reply(self._active_id, False, '새 추종 요청으로 대체됨')
            self._active_id = cmd.get('id')
            self._session.start()
            self._log.info(f'추종 시작 (id={self._active_id})')
        elif action in self.STOP_ACTIONS and self._active_id is not None:
            self._session.stop()

    def publish_snapshot(self):
        """추종 상태를 `FollowExec` 밑에 붙일 서브트리로 내보낸다.

        SEARCHING 이면 회복 BT 를 그대로, TRACKING 이면 잎 하나로 요약한다 —
        추종 중엔 트리가 존재하지 않지만(ControlLoop 이 SEARCHING 에서만 만든다)
        화면에서 "지금 따라가는 중"이 보여야 한다.
        """
        import json

        from std_msgs.msg import String
        loop = getattr(self._session, '_loop', None)
        if self._active_id is None or loop is None:
            payload = None
        else:
            tree = getattr(loop, 'search_tree', None)
            if tree is not None:
                payload = snapshot_dict(tree)
            else:
                payload = {'name': f'Following[{loop.state}]',
                           'kind': 'FollowSwitch', 'status': 'RUNNING',
                           'children': []}
        out = String()
        out.data = json.dumps({'tree': payload}, ensure_ascii=False)
        self._snap_pub.publish(out)

    def tick(self):
        """세션 tick 뒤에 부른다. 끝났으면 결과를 돌려준다."""
        self.publish_snapshot()
        if self._active_id is None:
            return
        state = self._session.poll()
        if state == 'running':
            return
        self._reply(self._active_id, state == 'success',
                    '중단됨' if state == 'success' else '추종 실패 — 대상을 놓쳤습니다')
        self._log.info(f'추종 종료 ({state}, id={self._active_id})')
        self._active_id = None

    def _reply(self, cmd_id, ok, msg):
        import json

        from std_msgs.msg import String
        out = String()
        out.data = json.dumps({'id': cmd_id, 'ok': bool(ok), 'msg': msg},
                              ensure_ascii=False)
        self._result_pub.publish(out)


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
            # ⚠️ **기본값을 False 로 바꿨다.** 예전엔 True 라 노드를 띄우는 순간 사람을
            #    따라가기 시작했다. 이제 미션 BT 가 /fleet_cmd 로 켜고 끄므로, 자동 시작은
            #    "관제가 시키지도 않았는데 로봇이 움직인다" 가 된다.
            #    단독으로 시험할 때만 -p autostart:=true 로 켠다.
            self.declare_parameter('autostart', False)
            self.declare_parameter('cmd_topic', 'fleet_cmd')
            self.declare_parameter('result_topic', 'fleet_cmd_result')

            scan_topic = self.get_parameter('scan_topic').value
            cmd_topic = self.get_parameter('cmd_vel_topic').value
            host = self.get_parameter('detection_host').value
            port = int(self.get_parameter('detection_port').value)

            self._scan = ScanProvider(self, scan_topic)
            self._cmd = CmdPublisher(self, cmd_topic)
            self._receiver = DetectionReceiver(TcpDetectionSource(host, port))
            self.session = FollowSession(self._make_loop, publish=self._cmd.publish)
            self.remote = RemoteControl(
                self, self.session,
                cmd_topic=self.get_parameter('cmd_topic').value,
                result_topic=self.get_parameter('result_topic').value,
            )
            if self.get_parameter('autostart').value:
                self.session.start()
            self.create_timer(1.0 / config.TICK_HZ, self._tick)
            self.get_logger().info(
                f'libi_perception up — scan={scan_topic} cmd_vel={cmd_topic} '
                f'detections={host}:{port} · 원격 제어 {self.get_parameter("cmd_topic").value}'
                f'(follow_admin/stop)')

        def _tick(self):
            # 순서가 중요하다: 세션을 먼저 굴리고 그 결과를 본다. 반대로 하면 이번 tick 에
            # 끝난 세션의 결과가 한 tick 늦게 나간다.
            self.session.tick()
            self.remote.tick()

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
