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


def requester_visible(det):
    """길잡이·감시 역할의 '등록 요청자가 실제로 보이나'.

    이 신호는 자세·주행 가부와 독립이다. 길잡이는 로봇이 앞서 가고 요청자가
    뒤따르는 구조라, 뒷카메라의 Side/Lying/Unknown/Calibrating 판정은 ``그 사람이
    있는가``의 근거를 부정하지 않는다. 자세 게이트는 추종에서 사람에게 접근하지
    않기 위한 규칙이며, 여기서 쓰면 GuideExec 가 nav2 목표를 취소해 버린다.

    ⚠️ **예측(`is_predicted`)은 거른다.** 파이프라인은 검출이 끊겨도
    `COAST_LIMIT`(30프레임 ≈ 2초) 동안 α-β 예측 위치를 계속 내보낸다. 그건 추종이
    문틀·서가에 잠깐 가려질 때 끊기지 않으려고 둔 장치이고, 추종은 지금도 그대로
    쓴다. 하지만 안내의 요점은 **그 사람이 실제로 따라오는지 확인하는 것**이라
    유령을 보고 전진하면 안 된다. `Side` 검사보다 앞에 두는 이유는, 뒤에 두면
    예측된 `Side` 가 True 로 빠져나가기 때문이다.
    """
    if det is None or not getattr(det, 'is_owner', False):
        return False
    if getattr(det, 'is_predicted', False):
        return False
    return True


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
        #: `guide_watch` 를 마지막으로 받은 시각. **BT 가 살아 있다는 유일한 증거**다
        #: — `_guide_orphaned` 참고. None = 지금 GUIDE 세션이 아니다.
        self._guide_seen_at = None
        #: 길잡이 회복 회전 허가. `GuideExec` 이 `mission_stop` 을 낸 **뒤에**
        #: `guide_watch{allow_rotate:true}` 로 켜 준다 — `_publish_for_role` 참고.
        #: 기본 False = "바퀴는 nav2 것".
        self._rotate_allowed = False
        self._result_pub = node.create_publisher(String, result_topic, 10)
        self._snap_pub = node.create_publisher(String, FOLLOW_SNAPSHOT_TOPIC, 10)
        self._cam_pub = node.create_publisher(String, config.CAMERA_SELECT_TOPIC,
                                              _latched_qos())
        # 역할도 같은 결로 내보낸다(config.PERCEPTION_ROLE_TOPIC 머리말 — AI 서버가
        # 길잡이에서 자세 추정을 끄는 근거다). 카메라와 **같은 주기·같은 발행자**로
        # 묶어 둔다 — 따로 두면 한쪽만 갱신돼 역할과 카메라가 어긋난 순간이 생긴다.
        self._role_pub = node.create_publisher(String, config.PERCEPTION_ROLE_TOPIC,
                                               _latched_qos())
        self._vis_pub = node.create_publisher(Bool, config.REQUESTER_VISIBLE_TOPIC, 10)
        self._area_pub = node.create_publisher(Float32, config.REQUESTER_AREA_TOPIC, 10)
        # 화면 전체에서 가장 큰 사람 — 등록 대상 매칭·세션 역할과 무관하다
        # (config.FRONT_PERSON_SIZE_TOPIC 머리말). `_publish_requester` 와 달리
        # 역할로 거르지 않는다 — 사람 차단 판정은 추종 중에도 필요하다.
        self._front_person_size_pub = node.create_publisher(
            Float32, config.FRONT_PERSON_SIZE_TOPIC, 10)
        #: 회복 BT 가 포기했음을 안내 쪽에 알린다 — 근거는 config 의 그 토픽 주석.
        self._guide_fail_pub = node.create_publisher(Bool, config.GUIDE_SEARCH_FAILED_TOPIC, 10)
        self._Bool, self._Float32, self._String = Bool, Float32, String
        self._last_cam_pub_at = 0.0
        #: 세션이 있어서 `camera_select` 를 지금 이 발행자가 쥐고 있는가.
        #: [2026-08-03] `_publish_camera` 의 유휴 침묵/1회 반납 판정 근거 — 그 메서드
        #: 머리말 참고.
        self._camera_owned = False
        self._get_detection = None             # 감시 역할일 때 쓰는 검출 조회
        #: `DetectionReceiver.front_person_size` 조회기. owner 유무와 무관한 값이라
        #: `_get_detection` 과 **따로** 둔다 — `_get_detection()` 은 owner 가 없으면
        #: None 을 돌려주므로 거기서 크기를 꺼내려 하면 주행 중(owner 없음) 내내 0 이 된다.
        self._get_front_person_size = None
        self._start_session_fn = None          # 역할을 실어 세션을 켜는 콜백
        node.create_subscription(String, cmd_topic, self._on_cmd, 10)

    def bind_session_starter(self, start_session):
        """역할을 실어 세션을 켜는 콜백. 노드가 주입한다(여기는 rclpy 를 모른다)."""
        self._start_session_fn = start_session

    def _start_session(self, role):
        if self._start_session_fn is not None:
            self._start_session_fn(role)
        else:
            self._session.start()

    def bind_detection(self, get_detection):
        """감시(guide/watch) 세션이 볼 검출 조회기. 없으면 가시성을 발행하지 않는다."""
        self._get_detection = get_detection

    def bind_front_person_size(self, get_size):
        """`DetectionReceiver.front_person_size` 조회기. 없으면 0.0 을 낸다."""
        self._get_front_person_size = get_size

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
        # ⚠️ [2026-08-04] `follow_admin` 은 관리자 추종(패널 버튼)과 야간순찰 추종
        # (IntruderChase)이 **같은 액션을 공유한다**(같은 FleetCmdDriver, 우회할
        # 새 채널을 안 만들려고 — 위 클래스 머리말). 근데 자세(옆모습) 정지는 관리자
        # 추종에만 필요하고 야간순찰은 bbox 크기만으로 판단해야 한다(요구사항). role
        # 하나로 AI 서버가 자세 추정 여부를 가르므로(`pipeline.POSE_ROLES`), args 에
        # 실린 태그로 둘을 갈라 다른 role 을 붙인다 — 액션·드라이버·타임아웃은 전부
        # 그대로 공유하고 role 만 다르다.
        if role == sess.FOLLOW and args.get('session_kind') == sess.SECURITY:
            role = sess.SECURITY
        if role is not None:
            # 주행 중인 세션은 감시 요청에 밀리지 않는다.
            #
            # 관리자가 추종으로 로봇을 몰고 있는데 이용자가 패널에서 길잡이 목적지를
            # 고르면 등록 화면이 `watch` 를 연다. 그걸 그대로 받으면 **움직이던 로봇의
            # 제어 루프가 그 자리에서 꺼진다.** 사람을 따라가던 로봇이 이유 없이 서는
            # 것은 조작하던 관리자에게 설명되지 않는다.
            # 거절 이유를 돌려주면 패널이 "관리자 추종 중" 이라고 말할 수 있다.
            if (role == sess.WATCH
                    and (self._sessions.driving
                         or (self._sessions.role == sess.GUIDE
                             and not self._guide_orphaned(now)))):
                self._reply(cmd_id, False, '주행 중이라 등록 화면을 열 수 없습니다')
                return
            # ⚠️ [2026-08-02] **같은 역할의 감시 세션이 이미 돌면 인자만 갱신한다.**
            #
            #   `GuideExec._allow_rotate` 가 회복 회전을 켜고 끌 때 `guide_watch` 를
            #   다시 낸다. 그런데 `FleetCmdDriver.start` 는 **매번 새 id** 를 만들고,
            #   `SessionManager.start` 는 id 가 다르면 새 Session 을 만든다. 그대로 두면
            #   재발행 한 번마다 `_start_session_for` 가 제어 루프를 새로 짓고 —
            #   **회복 트리가 처음부터 다시 돈다.** 회전을 허가하는 바로 그 순간에
            #   탐색이 리셋되므로, 켜자마자 0초로 되감기는 셈이다.
            #
            #   주행 세션(추종)은 해당 없다 — 그쪽은 위에서 갈아타는 것이 맞다.
            if (not self._sessions.driving) and self._sessions.role == role:
                self._set_rotate_allowed(bool(args.get('allow_rotate', False)))
                # 제어 루프는 그대로 둔 채, **새 명령 id** 로 lease 를 갱신한다.
                #
                # FleetCmdDriver 는 재발행마다 새 id 를 만들고, 나중에 stop 할 때도
                # 바로 그 마지막 id 를 `stop-<id>` 로 보낸다. 여기서 옛 id 를 보존하면
                # 회전 허가/해제 뒤 GuideExec._release_watch() 의 stop 이 세션을 못 찾아
                # 감시 루프가 고아가 된다. SessionManager.start 는 세션 메타데이터만
                # 바꾸므로 `_start_session()` 을 부르지 않는 한 제어 루프는 재생성되지 않는다.
                cur = self._sessions.current
                self._sessions.start(cmd_id, role, now,
                                     camera=args.get('camera') or cur.camera)
                self._guide_seen_at = now if role == sess.GUIDE else self._guide_seen_at
                self._reply(cmd_id, True,
                            f'{role} 감시 갱신 (회전 {"허가" if self._rotate_allowed else "금지"})')
                self._publish_camera(force=True)
                return
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
            self._rotate_allowed = bool(args.get('allow_rotate', False))
            self._sessions.start(cmd_id, role, now, camera=args.get('camera'))
            self._guide_seen_at = now if role == sess.GUIDE else None
            # 세션은 **역할과 무관하게** 켠다. 감시 역할도 회복 트리가 돌아야
            # 사람을 놓쳤을 때 반대 캠으로 바꿔 볼 수 있기 때문이다 —
            # 안 켜면 길잡이는 놓친 뒤 아무것도 안 하고 유예만 센다.
            # 대신 감시 역할에서는 속도를 **삼킨다**(아래 _start_session).
            self._start_session(role)
            if role in sess.DRIVING_ROLES:
                self._active_id = cmd_id
            else:
                # 감시 세션은 스스로 끝나지 않고 주행도 안 한다. 붙들고 있으면
                # 보낸 쪽이 타임아웃까지 기다린다.
                self._reply(cmd_id, True, f'{role} 감시 시작')
            self._publish_camera(force=True)
            self._log.info(f'{role} 세션 시작 (id={cmd_id}, cam={self._sessions.camera_for()})')
        elif action in self.STOP_ACTIONS:
            target = sess.target_session_id(cmd_id, args)
            if self._sessions.stop(target):
                # 세션은 역할과 무관하게 켜므로(감시도 회복 트리가 돌아야 한다)
                # **닫을 때도 역할과 무관하게** 멈춘다. `_active_id` 로 감싸면
                # guide/watch 의 루프가 세션이 닫힌 뒤에도 계속 tick 되어,
                # 이미 끝난 안내가 카메라 전환을 계속 요청한다.
                self._session.stop()
                self._active_id = None
                # 세션이 닫히면 허가도 없던 일이다. 안 지우면 다음 감시 세션이
                # **회전이 이미 허가된 채** 시작해 출발 전에 바퀴가 돈다.
                self._rotate_allowed = False
                self._guide_seen_at = None
                self._publish_camera(force=True)
                self._log.info(f'세션 종료 (id={target})')
            else:
                # 안 맞으면 **아무 일도 안 일어난다** — 제어 루프가 계속 20Hz 로
                # `/cmd_vel` 을 미는데 화면과 미션 BT 는 이미 빠져나와 있다.
                # 조용히 흘리면 "종료를 눌렀는데 로봇이 계속 움직인다"로만 드러나고
                # 원인을 찾을 실마리가 남지 않는다. 실제로 그것 때문에 헤맸다
                # (2026-07-28, BT 화면: FollowExec 회색 + Following[TRACKING] 파랑).
                self._log.warning(
                    f'종료 요청이 어느 세션도 안 가리킨다 (요청={target}, '
                    f'현재={self._sessions.session_id or "없음"}) — 세션은 그대로 돈다')

    def _should_snapshot(self) -> bool:
        """스냅샷을 낼 세션인가.

        추종(주행 세션)과 **길잡이**만 낸다. `watch` 는 안 된다.

        관제의 접합점 선택(`libi_modes/ros/state_io._pick_graft_point`)은
        `FollowExec`·`GuideExec` 중 tick 을 쥔 잎을 찾고, 없으면 `FollowExec` 으로
        폴백한다. `watch` 는 패널이 직접 여는 것이라 그 시점 미션 상태가
        `INTERACTING` 이고 두 잎 다 안 돈다. 그때 발행하면 **안 도는 잎 밑에
        서브트리가 붙어** 화면이 거짓말한다 — 이 변경이 고치려던 바로 그 병이다.
        """
        return self._active_id is not None or self._sessions.role == sess.GUIDE

    def publish_snapshot(self):
        """추종·안내 상태를 실행 잎 밑에 붙일 서브트리로 내보낸다.

        SEARCHING 이면 회복 BT 를 그대로, TRACKING 이면 잎 하나로 요약한다 —
        추종 중엔 트리가 존재하지 않지만(ControlLoop 이 SEARCHING 에서만 만든다)
        화면에서 "지금 따라가는 중"이 보여야 한다.

        ⚠️ 예전엔 `_active_id` 로만 막았다. 그 값은 주행 역할에만 설정되므로
        **안내 회복 트리가 관제 화면에 한 번도 안 떴다.**
        """
        import json

        from std_msgs.msg import String
        loop = getattr(self._session, '_loop', None)
        if loop is None or not self._should_snapshot():
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
        """세션이 있는 동안은 카메라 선택을 **주기적으로 다시 낸다.**

        한 번만 내면 송출기의 만료 워치독이 스스로 `none` 으로 떨어뜨려 영상이 끊긴다.
        그 워치독은 발행자가 죽었을 때 카메라가 계속 켜져 있는 것을 막으려고 둔 것이라,
        여기서 갱신해 주는 것이 짝이다.

        ⚠️ [2026-08-03] **세션이 없으면(유휴) 주기 재발행을 멈춘다 — 놓아준 뒤로는
        침묵한다.** `navigate` 다리(등록 대상 없음, 세션도 없음) 중에는 미션 BT
        의 `PersonBlockGuard` 가 **직접** `camera_select` 에 `"front"` 를 발행한다
        (`camera_sender.py` 기본값이 `none` 이라 누군가는 켜야 프레임이 나간다).
        `RemoteControl.tick()` 은 세션 유무와 무관하게 매 tick `_publish_camera()`
        를 부르므로, 예전처럼 유휴 중에도 계속 `none` 을 재발행하면 두 발행자가
        같은 토픽을 밀어 **마지막 도착이 이기는 경합**이 된다(`_publish_for_role`
        머리말과 같은 함정) — 앞캠이 깜빡이고 검출이 끊긴다.

        `force` 는 이 침묵을 덮지 않는다 — 세션이 없는데 명시적으로 부르는
        호출(예: `request_camera` 가 세션 없이 불리는 방어적 경로)은 예전처럼
        `camera_for()`(='none')를 그대로 낸다. 바뀌는 것은 오직 `RemoteControl.tick()`
        이 매 tick 부르는 **무조건·비강제** 호출뿐이다.
        """
        now = self._now()
        has_session = self._sessions.role is not None
        if not has_session and not force:
            if not self._camera_owned:
                return                    # 계속 유휴 — 침묵한다, 토픽은 BT 것
            # 세션이 방금 끝났다 — 한 번만 놓아주고 그 뒤로는 조용해진다.
            self._camera_owned = False
            self._last_cam_pub_at = now
            self._cam_pub.publish(self._String(data='none'))
            self._role_pub.publish(self._String(data='none'))
            return
        self._camera_owned = has_session
        period = 1.0 / config.CAMERA_SELECT_HZ if config.CAMERA_SELECT_HZ > 0 else 0.0
        if not force and (now - self._last_cam_pub_at) < period:
            return
        self._last_cam_pub_at = now
        self._cam_pub.publish(self._String(data=self._sessions.camera_for()))
        self._role_pub.publish(self._String(data=self._sessions.role or 'none'))

    def _guide_orphaned(self, now: float) -> bool:
        """GUIDE 세션이 **주인을 잃었나.**

        ⚠️ [2026-08-02] 이 판정이 없으면 **패널이 영구히 잠긴다.**

          길잡이 감시를 패널의 `watch` 가 10초마다 덮어 회복 트리를 리셋하던 버그를
          막으려고, GUIDE 세션이 있는 동안 `watch` 를 거절하게 했다. 그런데 GUIDE
          세션은 **lease 면제**다(`session.py:80` — "BT 가 열고 BT 가 닫는다").

          그 둘이 겹치면: `fsm_node` 가 중간에 죽으면 세션을 닫을 주체가 사라지고,
          그 세션은 영원히 남아 **패널이 길잡이 등록 화면을 다시는 못 연다.**
          재부팅 말고 길이 없다. 하나를 고치다 더 나쁜 것을 만드는 셈이다.

          그래서 `GuideExec` 이 `WATCH_RENEW_SEC`(15초)마다 `guide_watch` 를 재발행해
          살아 있음을 알린다. 그게 이 시간 넘게 안 오면 주인이 없는 것으로 보고
          패널에 넘겨준다. lease 를 GUIDE 에 거는 것과 다르다 — **갱신 경로가 있는**
          상태에서만 만료를 인정하므로, `session.expired` 주석이 경고한 "갱신 경로가
          없어 정확히 lease_sec 만에 강제 종료" 가 일어나지 않는다.
        """
        if self._guide_seen_at is None:
            return True                     # GUIDE 인데 받은 적이 없다 — 주인 불명
        return (now - self._guide_seen_at) > config.GUIDE_ORPHAN_SEC

    def _set_rotate_allowed(self, allow: bool) -> None:
        """회전 허가를 세우고, **막 열렸으면 회복 탐색을 처음부터 다시 시작한다.**

        ⚠️ 이게 없으면 길잡이 회복 한 라운드가 거의 통째로 낭비된다. 회복 트리는
           소실 즉시 도는데 바퀴는 `GuideExec` 이 `guide_lost_grace_sec`(20초)를
           넘겨야 넘겨주므로(`_publish_for_role`), 앞의 `HoldFront`·`HoldBack` 과
           `SweepFront` 앞부분이 **카메라만 바뀌고 로봇은 안 도는 채로** 흘러간다.
           허가가 열리는 순간 되감으면 그 라운드가 온전히 회전 탐색이 된다.

        ⚠️ **False → True 로 바뀔 때만** 되감는다. `guide_watch` 는 lease 갱신으로
           10초마다 다시 오므로, 값이 그대로인데 되감으면 탐색이 영영 처음으로
           돌아간다 — 위 "같은 역할이면 인자만 갱신" 분기가 막으려던 그 버그다.
        """
        opened = allow and not self._rotate_allowed
        self._rotate_allowed = allow
        if not opened:
            return
        loop = getattr(self._session, '_loop', None)
        grant = getattr(loop, 'rotation_granted', None)
        if grant is not None:
            grant()

    @property
    def rotate_allowed(self) -> bool:
        """길잡이 회복 회전을 허가받았나. 노드의 `_publish_for_role` 이 읽는다.

        ⚠️ 값을 **여기서만** 세운다. 세션 명령(`guide_watch`)이 들어오는 곳이 여기라,
           허가와 그 근거(명령)가 같은 자리에 있어야 나중에 "누가 켰나"를 찾을 수 있다.
        """
        return self._rotate_allowed

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
        visible = requester_visible(det)
        # 안내의 출발·재출발 조건은 **뒷캠** 검출뿐이다. 회복 BT 가 앞캠에서
        # 사용자를 발견한 것은 "로봇 앞으로 왔다"는 신호이지 nav2 를 재개할 근거가
        # 아니다. 이 값을 True 로 내보내면 GuideExec 이 즉시 goal 을 재발행한다.
        if self._sessions.role == sess.GUIDE and self._sessions.camera_for() != 'back':
            visible = False
        self._vis_pub.publish(self._Bool(data=visible))
        if visible:
            self._area_pub.publish(self._Float32(data=float(det.area)))

    def _publish_front_person_size(self):
        """화면에서 가장 큰 사람의 크기 — **역할과 무관하게 매 tick** 낸다.

        `_publish_requester` 와 달리 GUIDE/WATCH 로 거르지 않는다: 이 값은 등록
        대상 매칭과 독립이고(`Detection.front_person_size` 머리말), 사람 차단
        판정은 추종·평시 주행 중에도 필요하다.

        ⚠️ [2026-08-03] **`_get_detection()` 이 아니라 `_get_front_person_size()` 를 쓴다.**
        주행(`navigate`) 중에는 등록된 추종 대상이 아예 없어 `_get_detection()` 이
        영원히 None 이다 — 거기서 크기를 꺼내면 `PersonBlockGuard` 가 주행 내내 0.0
        만 본다. `DetectionReceiver.front_person_size()` 는 owner 유무와 무관한
        별도 슬롯이라 이 문제가 없다(detection_receiver.py 머리말).

        조회기가 없으면(바인딩 안 됨) 0.0 을 낸다 — "모른다" 를 안 내는 게 아니라
        0 을 낸다(구독자가 없음으로 해석).
        """
        size = self._get_front_person_size() if self._get_front_person_size is not None else 0.0
        self._front_person_size_pub.publish(self._Float32(data=float(size or 0.0)))

    def _publish_guide_search_failed(self):
        """길잡이 회복 BT 가 다 훑고도 못 찾았나.

        ⚠️ **매 tick 낸다 — 한 번 쏘고 마는 래치가 아니다.** 받는 쪽(`providers`)이
           가시성과 똑같이 신선도로 "지금" 을 판정하기 때문이다. 한 번만 쏘면 그
           메시지를 놓친 순간 안내가 영영 안 끝난다 — 정확히 예전 구조로 되돌아간다.

        주행 세션(추종)은 해당 없다. 그쪽은 `_active_id` 가 있어 `tick()` 아래쪽이
        `/fleet_cmd_result` 로 제대로 결과를 돌려준다. 이 통로는 **결과를 돌려줄 곳이
        없는 감시 세션**을 위한 것이다.
        """
        if self._sessions.role != sess.GUIDE:
            return
        self._guide_fail_pub.publish(self._Bool(data=self._session.poll() == 'failure'))

    def tick(self):
        """세션 tick 뒤에 부른다. 끝났으면 결과를 돌려준다."""
        now = self._now()
        if self._sessions.sweep(now):
            # 패널이 죽어 stop 이 안 왔다. 카메라를 끄지 않으면 아무도 안 보는데
            # 계속 켜져 있고, 그 사실을 아무도 모른다.
            self._log.info('세션 lease 만료 — 닫습니다')
            self._session.stop()
            self._active_id = None
            self._rotate_allowed = False
            self._guide_seen_at = None
        self._publish_camera()
        self._publish_requester()
        self._publish_front_person_size()
        self._publish_guide_search_failed()
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

            # max_age: 이보다 오래된 스캔은 없는 것으로 본다 — 라이다가 멈춘 채로
            # 옛 그림을 보고 회피 판단을 하지 않도록. 0 이면 검사가 꺼진다(config 주석).
            # flip_180: 라이다가 거꾸로 달려 있다 — 실측으로 뒤를 막았는데 앞이 안 갔다.
            #           `scan_provider.to_degree_indexed` 머리말 참고.
            self._scan = ScanProvider(self, scan_topic,
                                      max_age=config.SCAN_MAX_AGE_SEC,
                                      flip_180=getattr(config, "LIDAR_FLIP_180", False))
            self._cmd = CmdPublisher(self, cmd_topic)
            self._receiver = DetectionReceiver(TcpDetectionSource(host, port))
            #: 지금 세션의 역할. `_make_loop` 과 `_publish_for_role` 이 읽는다.
            self._session_role = sess.FOLLOW
            # 종료 정지도 **역할을 거쳐** 나간다. `_cmd.publish` 를 직접 주면 길잡이·감시
            # 세션이 끝날 때(stop 명령·WATCH lease 만료)도 (0,0) 이 나가서, nav2 가 몰고
            # 있는 주행을 한 번 밟는다 — 연속 경합은 아니지만 그 순간 로봇이 움찔한다.
            self.session = FollowSession(self._make_loop, publish=self._publish_for_role)
            self.remote = RemoteControl(
                self, self.session,
                cmd_topic=self.get_parameter('cmd_topic').value,
                result_topic=self.get_parameter('result_topic').value,
            )
            # 감시(guide/watch) 세션이 요청자 가시성을 발행하려면 검출을 봐야 한다.
            # 이게 없으면 `/libi/requester_visible` 발행자가 여전히 없는 셈이라
            # GuideExec 이 '감시 없음' 으로 읽고 사람을 놓쳐도 계속 간다.
            self.remote.bind_detection(self._get_detection)
            # 크기는 owner 유무와 무관하다 — `_get_detection` 과 따로 바인딩한다
            # (`_publish_front_person_size` 머리말 — 주행 중엔 owner 가 아예 없다).
            self.remote.bind_front_person_size(self._receiver.front_person_size)
            self.remote.bind_session_starter(self._start_session_for)
            if self.get_parameter('autostart').value:
                self.session.start()
            self.create_timer(1.0 / config.TICK_HZ, self._tick)
            self.get_logger().info(
                f'libi_perception up — scan={scan_topic} cmd_vel={cmd_topic} '
                f'detections={host}:{port} · 원격 제어 {self.get_parameter("cmd_topic").value}'
                f'(follow_admin/stop)')

        def _tick(self):
            # ⚠️ [2026-08-03] **세션이 없어도 매 tick 소켓을 비운다.**
            #
            # `front_person_size` 는 등록 대상(owner)과 무관해야 하는데, `_receiver.update()`
            # 는 지금까지 `_get_detection()`(ControlLoop 가 세션 중에만 부른다, 또는
            # GUIDE/WATCH 의 `_publish_requester`)을 통해서만 호출됐다. 세션이 아예 없는
            # 평시 주행(`navigate` 다리 — 등록 대상이 애초에 없다) 중에는 아무도
            # `update()` 를 안 불러 수신 버퍼가 안 비워지고, `front_person_size()` 는
            # `_stamp` 가 영원히 None 이라 늘 0.0 만 낸다 — 바로 이 기능이 고치려던 결함이
            # 배선 한 군데 남아 되살아나는 셈이다. 여기서 무조건 한 번 불러 둔다
            # (`ControlLoop` 이 같은 tick 에 또 부르면 poll() 이 빈 리스트를 주므로 무해하다).
            self._receiver.update()
            # 순서가 중요하다: 세션을 먼저 굴리고 그 결과를 본다. 반대로 하면 이번 tick 에
            # 끝난 세션의 결과가 한 tick 늦게 나간다.
            self.session.tick()
            self.remote.tick()

        def _start_session_for(self, role):
            """역할을 기억해 두고 세션을 켠다. `_make_loop` 이 그 값을 읽는다."""
            self._session_role = role
            self.session.start()

        def _publish_for_role(self, lin, ang):
            """감시 역할(guide/watch)에서는 속도를 **기본적으로 삼킨다.**

            회복 트리는 감시 세션에서도 돌아야 한다 — 사람을 놓쳤을 때 반대 캠으로
            바꿔 보는 일을 그 트리가 하기 때문이다. 하지만 길잡이 주행은 nav2 가
            하므로, 여기서 속도를 내면 **두 주체가 같은 `/cmd_vel` 을 민다.**
            그래서 평소에는 트리는 돌리되 바퀴는 안 돌린다.

            ## 예외 — 회복 회전 허가 (2026-08-02, 사용자 스펙)

            "길잡이도 회복 때 추종처럼 회전한다." 그러려면 nav2 가 **정말 멈춘 뒤에만**
            바퀴를 넘겨야 하는데, `libi_modes` 와 이 노드는 별개 프로세스라 그 사실을
            공유할 길이 없었다. `GuideExec` 이 `mission_stop` 을 낸 **다음에**
            `guide_watch{allow_rotate:true}` 를 보내 알린다(`GuideExec._allow_rotate`).

            ⚠️ **전진은 끝까지 안 넘긴다 — 각속도만 넘긴다.** 회복 탐색은 제자리 회전
               으로 훑는 것이고, 여기서 선속도까지 내보내면 nav2 가 취소된 사이 로봇이
               경로 밖으로 걸어 나간다. 그러면 재개할 때 fleet_node 의 노드 예약과
               실제 위치가 어긋난다(교통관제는 로봇이 예약한 정점에 있다고 믿는다).
               회복 트리의 스윕은 각속도만 쓰므로 실질 손실이 없다.
            """
            role = getattr(self, '_session_role', sess.FOLLOW)
            if role in sess.DRIVING_ROLES:
                self._cmd.publish(lin, ang)
            elif role == sess.GUIDE and getattr(self.remote, 'rotate_allowed', False):
                # ⚠️ **역할을 명시적으로 본다.** 예전에는 `rotate_allowed` 만 봤는데,
                #    그 값이 내려가는 것은 세션 교체·종료 경로에 **암묵적으로** 기대고
                #    있었다. 패널이 고아 GUIDE 세션을 넘겨받는 경로(`_guide_orphaned`)가
                #    생기면서 역할이 GUIDE→WATCH 로 바뀔 수 있는데, 그때 플래그가 한
                #    tick 이라도 남으면 **아무도 안 보는 세션이 바퀴를 돌린다.**
                #    조건을 여기서 못 박으면 그 부류가 원천적으로 안 생긴다.
                self._cmd.publish(0.0, ang)

        def _make_loop(self):
            return ControlLoop(
                get_detection=self._get_detection,
                get_scan=self._scan.get,
                publish=self._publish_for_role,
                cfg=config,
                now=lambda: self.get_clock().now().nanoseconds / 1e9,
                # 회복 BT 가 반대 캠을 보려면 세션의 카메라를 잠시 바꿔야 한다.
                # 발행은 여전히 RemoteControl 한 곳에서만 나간다 — 여기서는 세션
                # 상태만 바꾸고, 다음 발행 주기에 그 값이 실려 나간다.
                select_camera=self.remote.request_camera,
                peek_people=self._peek_people,
                # 정위치 캠이 역할에서 나온다 — 추종은 앞, 길잡이·등록감시는 뒤.
                role=getattr(self, '_session_role', sess.FOLLOW),
                # 코스팅이 실제로 도는지 follow 창에 남긴다 (control_loop.tick 주석).
                log=self.get_logger().info,
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
