"""`RemoteControl` — 세션 라우팅과 발행. rclpy 없이 대역 노드로 돈다.

여기서 잡는 것: 감시 세션이 켜지는가, stop 이 **자기 세션만** 닫는가, 카메라 선택이
주기적으로 다시 나가는가, 요청자 가시성이 감시 역할일 때만 나가는가.
"""
import json
import sys
import types

import pytest

from libi_perception import config


# ── rclpy 대역 ───────────────────────────────────────────────────────────────
# follow_node 는 std_msgs 와 rclpy.qos 를 import 한다. 로봇 밖에서도 시험하려고
# 최소한의 모듈을 꽂는다. 실제 ROS 가 있으면 그쪽이 이긴다.

def _install_ros_stubs():
    if 'std_msgs.msg' not in sys.modules:
        msg = types.ModuleType('std_msgs.msg')

        def _mk(name):
            def init(self, data=None):
                self.data = data
            return type(name, (), {'__init__': init})

        msg.String, msg.Bool, msg.Float32 = _mk('String'), _mk('Bool'), _mk('Float32')
        std = types.ModuleType('std_msgs')
        std.msg = msg
        sys.modules['std_msgs'] = std
        sys.modules['std_msgs.msg'] = msg
    if 'rclpy.qos' not in sys.modules:
        qos = types.ModuleType('rclpy.qos')
        qos.QoSProfile = lambda **kw: kw
        qos.ReliabilityPolicy = types.SimpleNamespace(RELIABLE='RELIABLE')
        qos.DurabilityPolicy = types.SimpleNamespace(TRANSIENT_LOCAL='TRANSIENT_LOCAL')
        rclpy = sys.modules.get('rclpy') or types.ModuleType('rclpy')
        rclpy.qos = qos
        sys.modules['rclpy'] = rclpy
        sys.modules['rclpy.qos'] = qos


_install_ros_stubs()

from libi_perception.follow_node import RemoteControl          # noqa: E402
from libi_perception.session import SessionManager             # noqa: E402


class FakePub:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg.data)


class FakeNode:
    def __init__(self):
        self.pubs = {}
        self.subs = {}

    def create_publisher(self, _type, topic, _qos):
        p = FakePub()
        self.pubs[topic] = p
        return p

    def create_subscription(self, _type, topic, cb, _qos):
        self.subs[topic] = cb

    def get_logger(self):
        return types.SimpleNamespace(info=lambda *_a, **_k: None,
                                     warning=lambda *_a, **_k: None,
                                     error=lambda *_a, **_k: None)


class FakeSession:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.state = 'running'

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def poll(self):
        return self.state


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Det:
    def __init__(self, area=1234.0, is_owner=True, motion_ok=True):
        self.area, self.is_owner, self.motion_ok = area, is_owner, motion_ok


@pytest.fixture
def rc():
    node, session, clock = FakeNode(), FakeSession(), Clock()
    ctl = RemoteControl(node, session, sessions=SessionManager(lease_sec=60), now=clock)
    return types.SimpleNamespace(ctl=ctl, node=node, session=session, clock=clock,
                                 send=lambda **kw: node.subs['fleet_cmd'](
                                     types.SimpleNamespace(data=json.dumps(kw))),
                                 cam=lambda: node.pubs[config.CAMERA_SELECT_TOPIC].sent,
                                 vis=lambda: node.pubs[config.REQUESTER_VISIBLE_TOPIC].sent,
                                 area=lambda: node.pubs[config.REQUESTER_AREA_TOPIC].sent,
                                 result=lambda: node.pubs['fleet_cmd_result'].sent)


# ── 세션 라우팅 ─────────────────────────────────────────────────────────────

def test_follow_admin_starts_control_loop(rc):
    rc.send(action='follow_admin', id='f1')
    assert rc.session.started == 1
    assert rc.cam()[-1] == 'front'


def test_guide_watch_starts_a_motion_free_session(rc):
    """감시 세션도 회복 트리는 돌아야 한다 — 사람을 놓쳤을 때 반대 캠으로 바꿔 보는
    일을 그 트리가 하기 때문이다. 안 켜면 길잡이는 놓친 뒤 유예만 세고 아무것도 안 한다.

    /cmd_vel 경합은 세션을 안 켜서가 아니라 **속도를 삼켜서** 막는다
    (FollowNode._publish_for_role). 여기서는 세션이 켜지는지만 본다."""
    rc.send(action='guide_watch', id='g1')
    assert rc.session.started == 1
    assert rc.cam()[-1] == 'back'


def test_watch_uses_camera_from_args(rc):
    """등록 화면은 앞캠이다 — 이용자가 패널 앞에 서 있다."""
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    assert rc.cam()[-1] == 'front'


def test_watch_replies_immediately(rc):
    """감시 세션은 스스로 안 끝난다. 결과를 붙들면 보낸 쪽이 타임아웃까지 기다린다."""
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    assert json.loads(rc.result()[-1])['id'] == 'w1'
    assert json.loads(rc.result()[-1])['ok'] is True


def test_follow_does_not_reply_until_it_ends(rc):
    rc.send(action='follow_admin', id='f1')
    assert rc.result() == []


# ── stop id 규약 ─────────────────────────────────────────────────────────────

def test_stop_with_prefixed_id_closes_bt_session(rc):
    """FleetCmdDriver.stop() 은 `stop-<원래id>` 를 보낸다.
    원 id 로만 비교하면 BT 가 연 세션이 영영 안 닫힌다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='stop', id='stop-f1')
    assert rc.session.stopped == 1
    assert rc.cam()[-1] == 'none'


def test_stop_with_other_id_does_not_close(rc):
    """패널의 watch 종료가 관리자 추종을 끊으면 안 된다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='stop', id='stop-w9')
    assert rc.session.stopped == 0
    assert rc.cam()[-1] == 'front'


def test_panel_can_target_its_own_session_explicitly(rc):
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    rc.send(action='stop', id='whatever', args={'session_id': 'w1'})
    assert rc.cam()[-1] == 'none'


# ── 카메라 재발행 ────────────────────────────────────────────────────────────

def test_camera_republished_periodically(rc):
    """한 번만 내면 송출기의 만료 워치독이 스스로 none 으로 떨어뜨려 영상이 끊긴다."""
    rc.send(action='follow_admin', id='f1')
    n0 = len(rc.cam())
    rc.clock.t += 10.0
    rc.ctl._publish_camera()
    assert len(rc.cam()) == n0 + 1


def test_camera_not_republished_faster_than_period(rc):
    rc.send(action='follow_admin', id='f1')
    n0 = len(rc.cam())
    rc.ctl._publish_camera()
    rc.ctl._publish_camera()
    assert len(rc.cam()) == n0


# ── 요청자 가시성 ────────────────────────────────────────────────────────────

def test_visibility_published_for_guide(rc):
    rc.ctl.bind_detection(lambda: Det(area=900.0))
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is True
    assert rc.area()[-1] == 900.0


def test_visibility_not_published_for_follow(rc):
    """추종은 제어 루프가 직접 검출을 본다 — 토픽으로 낼 이유가 없다."""
    rc.ctl.bind_detection(lambda: Det())
    rc.send(action='follow_admin', id='f1')
    rc.ctl._publish_requester()
    assert rc.vis() == []


def test_area_not_published_when_invisible(rc):
    """안 보일 때 0 을 내면 받는 쪽이 '아주 멀다' 로 읽어 소실과 구별되지 않는다."""
    rc.ctl.bind_detection(lambda: None)
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is False
    assert rc.area() == []


def test_motion_blocked_counts_as_not_visible(rc):
    """누워 있으면 '보이지만 가면 안 된다' — 길잡이는 멈춰야 한다."""
    rc.ctl.bind_detection(lambda: Det(motion_ok=False))
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is False


# ── lease 만료 ──────────────────────────────────────────────────────────────

def test_lease_expiry_turns_camera_off(rc):
    """패널이 죽어 stop 이 안 와도 카메라가 꺼져야 한다."""
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    rc.clock.t += 61.0
    rc.ctl.tick()
    assert rc.cam()[-1] == 'none'


# ── 회복 BT 의 카메라 요청 ───────────────────────────────────────────────────

def test_recovery_camera_request_goes_through_the_single_publisher(rc):
    """회복 BT 가 직접 발행하면 발행자가 둘이 되어 서로 덮어쓴다.
    세션 상태만 바꾸고 발행은 여기 한 곳에서 나가야 한다."""
    rc.send(action='follow_admin', id='f1')
    assert rc.cam()[-1] == 'front'
    rc.ctl.request_camera('back')             # 탐색 중 반대 캠 관찰
    assert rc.cam()[-1] == 'back'


def test_camera_request_without_session_is_ignored(rc):
    """세션이 없는데 카메라가 켜지면 아무도 안 보는 영상이 나간다."""
    rc.ctl.request_camera('back')
    assert rc.cam()[-1] == 'none'


# ── codex 리뷰(2026-07-27) ───────────────────────────────────────────────────

def test_new_session_actually_stops_the_running_follow(rc):
    """결과만 실패로 돌려주고 제어 루프를 안 세우면, 그 루프가 계속 20Hz 로 PID
    속도를 발행한다 — 새 세션이 길잡이면 nav2 와 동시에 /cmd_vel 을 민다."""
    rc.send(action='follow_admin', id='f1')
    assert rc.session.stopped == 0
    rc.send(action='guide_watch', id='g1')
    assert rc.session.stopped == 1


def test_follow_session_is_not_swept_by_lease(rc):
    """추종은 수십 분씩 이어진다. lease 로 끊으면 멀쩡한 추종이 죽는다."""
    rc.send(action='follow_admin', id='f1')
    rc.clock.t += 10_000.0
    rc.ctl.tick()
    assert rc.session.stopped == 0
    assert rc.cam()[-1] == 'front'


def test_watch_cannot_preempt_a_running_follow(rc):
    """움직이던 로봇의 제어 루프가 이용자 터치 한 번에 꺼지면 안 된다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    assert rc.session.stopped == 0
    assert rc.cam()[-1] == 'front'            # 추종이 그대로 살아 있다
    assert json.loads(rc.result()[-1])['ok'] is False   # 패널에 이유가 간다


def test_guide_watch_may_replace_a_follow(rc):
    """BT 가 보내는 길잡이 감시는 미션 전이의 결과라 정당하게 대체한다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='guide_watch', id='g1')
    assert rc.session.stopped == 1


def test_stopping_a_watch_session_also_stops_the_loop(rc):
    """세션을 역할과 무관하게 켜므로 닫을 때도 그래야 한다 — 안 그러면 이미 끝난
    안내의 회복 트리가 계속 tick 되어 카메라 전환을 요청한다."""
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    assert rc.session.started == 1
    rc.send(action='stop', id='stop-w1')
    assert rc.session.stopped == 1
    assert rc.cam()[-1] == 'none'


def test_stopping_a_guide_watch_session_also_stops_the_loop(rc):
    rc.send(action='guide_watch', id='g1')
    rc.send(action='stop', id='stop-g1')
    assert rc.session.stopped == 1
