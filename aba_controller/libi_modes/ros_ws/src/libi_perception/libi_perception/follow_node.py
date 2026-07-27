"""ROS2 entry point for LIBI person-following.

FollowSession is ROS-free and testable on its own; the rclpy node below only wires
transports to it and ticks it. libi_modes' FollowExec sees nothing but FollowSession's
start()/poll()/stop() contract, so the follower's internals stay replaceable.

Topic names and the detection port are ROS parameters, not constants. Where this node
runs is still an open question — it needs /scan and /cmd_vel, which belong to the driving
Pi, so it may end up deployed there rather than on the mission PC. Parameterising the
transports keeps that a launch-time decision instead of a code change.
"""
import time

from . import config
from . import session as sess
from .control_loop import ControlLoop
from .detection_receiver import DetectionReceiver
from .tcp_detection_source import TcpDetectionSource


def _latched_qos():
    """발행자는 반드시 TRANSIENT_LOCAL 이어야 한다.

    ROS2 호환 규칙상 발행자 VOLATILE + 구독자 TRANSIENT_LOCAL 조합은 **연결 자체가
    안 된다.** 늦게 뜬 송출기가 현재 선택을 곧바로 받게 하려면 이쪽이 durable 이어야 한다.
    """
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    return QoSProfile(depth=1,
                      reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


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
    """`/fleet_cmd` 왕복으로 세션을 켜고 끈다. 미션 BT(libi_modes)와 패널이 이걸로 부른다.

    ## 새 프로토콜을 만들지 않는다
    로봇에는 이미 `/fleet_cmd`(JSON) → 실행 → `/fleet_cmd_result` 왕복이 있고,
    libi_modes 의 모든 액션 leaf 가 그 위에서 돈다(`FleetCmdDriver`). 추종만 별도
    채널을 두면 id 대조·타임아웃·취소를 한 벌 더 만들게 된다. 같은 통로를 쓴다.

    ## 세 가지 역할
        follow_admin  관리자 추종 — 앞캠, 제어 루프가 /cmd_vel 을 만든다
        guide_watch   길잡이 감시 — 뒷캠, 주행은 nav2 가 하고 여기는 눈만 된다
        watch         등록 화면 감시 — 패널이 직접 연다. 주행 없음

    `watch` 는 **등록 데드락**을 푼다: 등록하려면 카메라가 필요한데 카메라는 세션이
    켜고 세션은 등록 후 시작된다. 게다가 등록 시점의 미션 상태는 INTERACTING 이라
    WORKING 브랜치의 GuideExec 은 tick 되지도 않는다.

    ## 결과를 언제 돌려주나
    추종 세션은 **스스로 끝나지 않는다.** 관리자가 멈추거나(success) 회복이 소진돼
    사람을 놓치면(failure) 끝난다. 그래서 `start` 직후에 결과를 내지 않고, 세션이
    실제로 끝난 tick 에 낸다 — 그래야 BT 의 `poll()` 이 "추종 중"을 running 으로 본다.
    감시 전용 세션(guide/watch)은 주행을 안 하므로 시작 즉시 수락 결과를 낸다.

    ## 카메라 선택은 여기가 **유일한** 발행자다
    미션 BT 도 회복 BT 도 직접 발행하지 않는다. 발행자가 둘이면 회복 중 서로 덮어쓴다.
    """

    #: 세션을 여는 액션 → 역할. libi_modes 의 leaf handles 와 같은 이름이어야 한다.
    START_ACTIONS = {
        'follow_admin': sess.FOLLOW,
        'guide_watch': sess.GUIDE,
        'watch': sess.WATCH,
    }
    STOP_ACTIONS = ('stop', 'follow_stop')

    def __init__(self, node, session, cmd_topic='fleet_cmd',
                 result_topic='fleet_cmd_result', sessions=None, now=time.monotonic):
        from std_msgs.msg import Bool, Float32, String
        self._node = node
        self._session = session
        self._log = node.get_logger()
        self._now = now
        self._sessions = sessions or sess.SessionManager(config.SESSION_LEASE_SEC)
        self._active_id = None                 # 결과를 돌려줘야 하는 주행 세션 id
        self._result_pub = node.create_publisher(String, result_topic, 10)
        self._snap_pub = node.create_publisher(String, FOLLOW_SNAPSHOT_TOPIC, 10)
        self._cam_pub = node.create_publisher(String, config.CAMERA_SELECT_TOPIC,
                                              _latched_qos())
        self._vis_pub = node.create_publisher(Bool, config.REQUESTER_VISIBLE_TOPIC, 10)
        self._area_pub = node.create_publisher(Float32, config.REQUESTER_AREA_TOPIC, 10)
        self._Bool, self._Float32, self._String = Bool, Float32, String
        self._last_cam_pub_at = 0.0
        self._get_detection = None             # 감시 역할일 때 쓰는 검출 조회
        node.create_subscription(String, cmd_topic, self._on_cmd, 10)

    def bind_detection(self, get_detection):
        """감시(guide/watch) 세션이 볼 검출 조회기. 없으면 가시성을 발행하지 않는다."""
        self._get_detection = get_detection

    def request_camera(self, name):
        """회복 BT 가 탐색 중 반대 캠을 보고 싶을 때 부른다.

        발행자가 하나라는 규칙(`camera_select` 는 이 클래스만 낸다)을 지키려고, 회복
        BT 는 여기에 **요청만** 한다 — 직접 내면 발행자가 둘이 되어 서로 덮어쓴다.

        ⚠️ 탐색 구간 노드는 **매 tick** 이걸 부른다(20Hz). 그때마다 발행하면 latched
        토픽에 초당 20개가 쌓인다. 값이 실제로 바뀔 때만 즉시 내고, 나머지는 평소
        주기(`CAMERA_SELECT_HZ`)에 맡긴다.
        """
        if name == self._sessions.camera_for():
            return
        self._sessions.override_camera(name)
        self._publish_camera(force=True)

    # ── 명령 수신 ────────────────────────────────────────────────────────
    def _on_cmd(self, msg):
        import json
        try:
            cmd = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        action = str(cmd.get('action', '')).strip()
        cmd_id = cmd.get('id')
        args = cmd.get('args') or {}
        now = self._now()

        role = self.START_ACTIONS.get(action)
        if role is not None:
            # 이미 주행 세션이 돌고 있으면 **실제로 멈추고** 갈아탄다.
            #
            # ⚠️ 결과만 실패로 돌려주고 제어 루프를 안 세우면, 그 루프가 계속 20Hz 로
            #    PID 속도를 발행한다. 새 세션이 길잡이면 nav2 와 옛 추종 PID 가 동시에
            #    `/cmd_vel` 을 밀어 로봇이 떨거나 엉뚱하게 움직인다 — 중재자가 없어
            #    마지막에 도착한 메시지가 이긴다.
            if self._active_id is not None:
                self._session.stop()
                self._reply(self._active_id, False, '새 세션 요청으로 대체됨')
                self._active_id = None
            self._sessions.start(cmd_id, role, now, camera=args.get('camera'))
            if role in sess.DRIVING_ROLES:
                self._active_id = cmd_id
                self._session.start()
            else:
                # 감시 세션은 스스로 끝나지 않고 주행도 안 한다. 붙들고 있으면
                # 보낸 쪽이 타임아웃까지 기다린다.
                self._reply(cmd_id, True, f'{role} 감시 시작')
            self._publish_camera(force=True)
            self._log.info(f'{role} 세션 시작 (id={cmd_id}, cam={self._sessions.camera_for()})')
        elif action in self.STOP_ACTIONS:
            target = sess.target_session_id(cmd_id, args)
            if self._sessions.stop(target):
                if self._active_id is not None:
                    self._session.stop()
                self._publish_camera(force=True)
                self._log.info(f'세션 종료 (id={target})')

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

    # ── 발행 ────────────────────────────────────────────────────────────
    def _publish_camera(self, force=False):
        """카메라 선택을 **주기적으로 다시 낸다.**

        한 번만 내면 송출기의 만료 워치독이 스스로 `none` 으로 떨어뜨려 영상이 끊긴다.
        그 워치독은 발행자가 죽었을 때 카메라가 계속 켜져 있는 것을 막으려고 둔 것이라,
        여기서 갱신해 주는 것이 짝이다.
        """
        now = self._now()
        period = 1.0 / config.CAMERA_SELECT_HZ if config.CAMERA_SELECT_HZ > 0 else 0.0
        if not force and (now - self._last_cam_pub_at) < period:
            return
        self._last_cam_pub_at = now
        self._cam_pub.publish(self._String(data=self._sessions.camera_for()))

    def _publish_requester(self):
        """감시 역할일 때만 요청자 가시성·크기를 낸다.

        면적은 **보일 때만** 낸다. 안 보일 때 0 을 내면 받는 쪽이 '아주 멀다' 로 읽어
        소실과 원거리가 구별되지 않는다.
        """
        if self._sessions.role not in (sess.GUIDE, sess.WATCH):
            return
        if self._get_detection is None:
            return
        det = self._get_detection()
        visible = bool(det is not None and getattr(det, 'is_owner', False)
                       and getattr(det, 'motion_ok', True))
        self._vis_pub.publish(self._Bool(data=visible))
        if visible:
            self._area_pub.publish(self._Float32(data=float(det.area)))

    def tick(self):
        """세션 tick 뒤에 부른다. 끝났으면 결과를 돌려준다."""
        now = self._now()
        if self._sessions.sweep(now):
            # 패널이 죽어 stop 이 안 왔다. 카메라를 끄지 않으면 아무도 안 보는데
            # 계속 켜져 있고, 그 사실을 아무도 모른다.
            self._log.info('세션 lease 만료 — 닫습니다')
            if self._active_id is not None:
                self._session.stop()
        self._publish_camera()
        self._publish_requester()
        self.publish_snapshot()
        if self._active_id is None:
            return
        state = self._session.poll()
        if state == 'running':
            return
        self._reply(self._active_id, state == 'success',
                    '중단됨' if state == 'success' else '추종 실패 — 대상을 놓쳤습니다')
        self._log.info(f'추종 종료 ({state}, id={self._active_id})')
        self._sessions.stop(self._active_id)
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
            # 감시(guide/watch) 세션이 요청자 가시성을 발행하려면 검출을 봐야 한다.
            # 이게 없으면 `/libi/requester_visible` 발행자가 여전히 없는 셈이라
            # GuideExec 이 '감시 없음' 으로 읽고 사람을 놓쳐도 계속 간다.
            self.remote.bind_detection(self._get_detection)
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
                # 회복 BT 가 반대 캠을 보려면 세션의 카메라를 잠시 바꿔야 한다.
                # 발행은 여전히 RemoteControl 한 곳에서만 나간다 — 여기서는 세션
                # 상태만 바꾸고, 다음 발행 주기에 그 값이 실려 나간다.
                select_camera=self.remote.request_camera,
                peek_people=self._peek_people,
                role='follow',
            )

        def _peek_people(self):
            """반대 캠에 사람이 몇 명 보이나.

            지금은 검출 채널이 owner 하나만 실어 보내므로 "보이면 1, 아니면 0"이다.
            여러 명을 세려면 AI 서버가 후보 수를 같이 보내야 한다 — 그때 여기만 고친다.
            (한 명일 때만 반응한다는 규칙은 이미 트리 쪽에 있으므로, 이 근사는
             '여럿이면 무반응'을 못 지킬 뿐 위험한 쪽으로 틀리지는 않는다.)
            """
            det = self._get_detection()
            return 1 if det is not None else 0

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
