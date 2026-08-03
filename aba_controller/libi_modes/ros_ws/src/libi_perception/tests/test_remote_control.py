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


class RewindableLoop:
    """`rotation_granted()` 가 실제로 불렸는지만 세는 최소 대역."""
    def __init__(self):
        self.state = 'SEARCHING'
        self.search_tree = None
        self.rewinds = 0

    def rotation_granted(self):
        self.rewinds += 1


class FakeLoop:
    """`publish_snapshot` 이 보는 최소 표면 — 트리는 없고 상태·회복 트리만 있다.

    `search_tree` 를 인자로 받는다 — TRACKING 뿐 아니라 **SEARCHING 상태에서
    실제로 회복 서브트리가 실려 나가는지**까지 시험하려면 발행 여부만으로는
    부족하다(2026-08-01 codex 지적).
    """
    def __init__(self, state='TRACKING', search_tree=None):
        self.state = state
        self.search_tree = search_tree


class FakeSession:
    def __init__(self, loop=None):
        self.started = 0
        self.stopped = 0
        self.state = 'running'
        self._loop = loop if loop is not None else FakeLoop()

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
    # `is_predicted` 가 있어야 한다 — `requester_visible` 이 예측 bbox 를 거르는지
    # 시험하려면 대역이 그 필드를 가져야 한다(2026-08-02).
    def __init__(self, area=1234.0, is_owner=True, motion_ok=True, is_predicted=False):
        self.area, self.is_owner, self.motion_ok = area, is_owner, motion_ok
        self.is_predicted = is_predicted


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
                                 gfail=lambda: node.pubs[
                                     config.GUIDE_SEARCH_FAILED_TOPIC].sent,
                                 result=lambda: node.pubs['fleet_cmd_result'].sent,
                                 snap=lambda: node.pubs[
                                     '/libi/follow_bt_snapshot'].sent)


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


def test_panel_watch_cannot_replace_guide_watch_or_reset_recovery(rc):
    """패널 lease는 안내의 GUIDE 세션보다 약하다."""
    rc.send(action='guide_watch', id='guide-watch-1')
    rc.send(action='watch', id='panel-1', args={'camera': 'back'})
    assert rc.ctl._sessions.role == 'guide'
    assert rc.ctl._sessions.session_id == 'guide-watch-1'
    assert rc.session.started == 1, 'watch가 회복 제어 루프를 새로 만들었다'
    reply = json.loads(rc.result()[-1])
    assert reply['id'] == 'panel-1' and reply['ok'] is False


def test_watch_replies_immediately(rc):
    """감시 세션은 스스로 안 끝난다. 결과를 붙들면 보낸 쪽이 타임아웃까지 기다린다."""
    rc.send(action='watch', id='w1', args={'camera': 'front'})
    assert json.loads(rc.result()[-1])['id'] == 'w1'
    assert json.loads(rc.result()[-1])['ok'] is True


def test_follow_does_not_reply_until_it_ends(rc):
    rc.send(action='follow_admin', id='f1')
    assert rc.result() == []


# ── 2026-08-04 요구사항 — 야간순찰 추종은 관리자 추종과 같은 follow_admin
#    액션을 쓰지만, session_kind 태그로 role 을 갈라 자세 게이트를 뺀다 ───────

def test_session_kind_security_태그가_role을_security로_바꾼다(rc):
    """AI 서버의 pipeline.POSE_ROLES 가 role 문자열 하나로 자세 게이트 여부를
    가른다 — 태그 없이는 관리자 추종과 구분이 안 돼 같이 게이트가 걸린다."""
    rc.send(action='follow_admin', id='f1', args={'session_kind': 'security'})
    assert rc.ctl._sessions.role == 'security'
    assert rc.cam()[-1] == 'front'


def test_태그_없는_관리자_추종은_그대로_follow다(rc):
    """회귀 방지 — 패널 버튼(관리자 추종)은 이 태그를 안 실어 보내므로 자세
    게이트가 계속 걸려야 한다."""
    rc.send(action='follow_admin', id='f1')
    assert rc.ctl._sessions.role == 'follow'


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


# ── [2026-08-03] 유휴 중 카메라 침묵 ──────────────────────────────────────────
#
# `navigate` 다리(세션 없음)에는 미션 BT 의 `PersonBlockGuard` 가 **직접**
# `camera_select` 에 `front` 를 발행한다. `RemoteControl.tick()` 은 세션 유무와
# 무관하게 매 tick `_publish_camera()` 를 부르므로, 예전처럼 유휴 중에도 계속
# `none` 을 재발행하면 두 발행자가 같은 토픽을 밀어 마지막 도착이 이기는 경합이
# 된다 — 앞캠이 깜빡이고 검출이 끊긴다. 세션이 있는 동안의 주기 재발행(위 두 시험)
# 은 그대로다.

def test_camera_is_published_while_a_session_lives(rc):
    """세션 중에는 지금까지와 동일하게 주기 발행된다 — 동작 변경 없음."""
    rc.send(action='follow_admin', id='f1')
    n0 = len(rc.cam())
    rc.clock.t += 10.0
    rc.ctl.tick()
    assert len(rc.cam()) == n0 + 1
    assert rc.cam()[-1] == 'front'


def test_camera_is_released_once_when_the_session_ends(rc):
    """세션이 끝나면 `none` 이 **한 번만** 나가고, 그 뒤 tick 은 더 안 늘린다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='stop', id='stop-f1')
    n_at_stop = len(rc.cam())
    assert rc.cam()[-1] == 'none', "끝나는 순간 none 을 놓아줘야 한다"

    # 그 뒤로 여러 tick 이 지나도 카메라 발행이 더는 늘지 않는다 — 침묵.
    for _ in range(5):
        rc.clock.t += 10.0
        rc.ctl.tick()
    assert len(rc.cam()) == n_at_stop, "세션 종료 뒤에도 계속 발행하면 BT 와 경합한다"


def test_camera_is_silent_while_idle(rc):
    """세션을 아예 연 적이 없으면 tick 을 아무리 돌려도 발행이 0 번이어야 한다.

    BT(`PersonBlockGuard`)가 이 토픽의 유일한 주인이 되는 경로다. follow_node 가
    유휴 중에도 `none` 을 계속 밀면, BT 가 낸 `front` 와 마지막 도착 경쟁을 벌인다.
    """
    for _ in range(5):
        rc.clock.t += 1.0
        rc.ctl.tick()
    assert rc.cam() == []


def test_a_new_session_takes_the_camera_back(rc):
    """침묵 상태에서도 새 세션이 열리면 다시 발행된다 — 영구 잠금이 아니다."""
    rc.send(action='follow_admin', id='f1')
    rc.send(action='stop', id='stop-f1')
    for _ in range(3):                       # 침묵 확인
        rc.clock.t += 1.0
        rc.ctl.tick()
    n_idle = len(rc.cam())

    rc.send(action='guide_watch', id='g1')
    assert len(rc.cam()) == n_idle + 1
    assert rc.cam()[-1] == 'back'


# ── 요청자 가시성 ────────────────────────────────────────────────────────────

def test_visibility_published_for_guide(rc):
    rc.ctl.bind_detection(lambda: Det(area=900.0))
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is True
    assert rc.area()[-1] == 900.0


def test_front_camera_detection_does_not_reacquire_a_guide(rc):
    """회복 중 앞캠 사람은 '뒤로 이동' 안내용이며 nav2 재개 조건이 아니다."""
    rc.ctl.bind_detection(lambda: Det(area=900.0))
    rc.send(action='guide_watch', id='g1')
    rc.ctl.request_camera('front')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is False
    assert rc.area() == []


def test_visibility_not_published_for_follow(rc):
    """추종은 제어 루프가 직접 검출을 본다 — 토픽으로 낼 이유가 없다."""
    rc.ctl.bind_detection(lambda: Det())
    rc.send(action='follow_admin', id='f1')
    rc.ctl._publish_requester()
    assert rc.vis() == []


# ── 회전 허가 → 탐색 되감기 ────────────────────────────────────────────────
#
# ⚠️ 이게 없으면 길잡이 회복 한 라운드(32.8초) 중 앞의 20초가 **바퀴가 묶인 채**
#    지나간다 — `GuideExec` 이 `guide_lost_grace_sec` 를 넘겨야 허가를 켜기 때문이다.

def test_rotation_grant_rewinds_the_search():
    node, clock = FakeNode(), Clock()
    loop = RewindableLoop()
    ctl = RemoteControl(node, FakeSession(loop=loop),
                        sessions=SessionManager(lease_sec=60), now=clock)
    send = lambda **kw: node.subs['fleet_cmd'](
        types.SimpleNamespace(data=json.dumps(kw)))

    send(action='guide_watch', id='g1')                       # 허가 없이 시작
    assert loop.rewinds == 0
    send(action='guide_watch', id='g2', args={'allow_rotate': True})
    assert loop.rewinds == 1, "회전이 열렸는데 탐색을 안 되감았다"


def test_repeated_grant_does_not_rewind_again():
    """`guide_watch` 는 lease 갱신으로 10초마다 다시 온다. 값이 그대로인데 되감으면
    탐색이 **영영 처음으로 돌아간다** — 정확히 예전에 막았던 그 버그다."""
    node, clock = FakeNode(), Clock()
    loop = RewindableLoop()
    ctl = RemoteControl(node, FakeSession(loop=loop),
                        sessions=SessionManager(lease_sec=60), now=clock)
    send = lambda **kw: node.subs['fleet_cmd'](
        types.SimpleNamespace(data=json.dumps(kw)))

    send(action='guide_watch', id='g1')
    send(action='guide_watch', id='g2', args={'allow_rotate': True})
    send(action='guide_watch', id='g3', args={'allow_rotate': True})
    send(action='guide_watch', id='g4', args={'allow_rotate': True})
    assert loop.rewinds == 1, "갱신마다 되감으면 탐색이 영영 안 끝난다"


# ── 회복 종료 신호 ──────────────────────────────────────────────────────────
#
# ⚠️ 이 통로가 없으면 `GuideExec` 은 회복이 끝난 것을 **영영 모른다.** 감시 세션은
#    `_active_id` 를 안 세워서 `tick()` 아래쪽의 `/fleet_cmd_result` 경로에 안 걸린다.
#    예전에는 그 자리를 `guide_lost_timeout_sec`(시계)가 대신했다.

def test_guide_search_failure_is_reported(rc):
    """⚠️ **`tick()` 을 통해서 본다.** 발행 함수를 직접 부르면 그 함수가 tick 배선에서
    빠져도 초록이다 — 그러면 실기에서는 아무 신호도 안 나간다."""
    rc.session.state = 'failure'          # 회복 트리가 다 훑고 포기했다
    rc.send(action='guide_watch', id='g1')
    rc.ctl.tick()
    assert rc.gfail()[-1] is True, "회복이 끝났는데 아무도 안 알려준다"


def test_guide_search_running_reports_false(rc):
    """아직 도는 중이면 False 다 — 안 내면 받는 쪽이 stale 로 읽는다."""
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_guide_search_failed()
    assert rc.gfail()[-1] is False


def test_guide_search_failure_not_reported_for_follow(rc):
    """추종은 `_active_id` 로 `/fleet_cmd_result` 를 제대로 돌려준다 — 이 통로 대상이 아니다."""
    rc.session.state = 'failure'
    rc.send(action='follow_admin', id='f1')
    rc.ctl._publish_guide_search_failed()
    assert rc.gfail() == []


def test_area_not_published_when_invisible(rc):
    """안 보일 때 0 을 내면 받는 쪽이 '아주 멀다' 로 읽어 소실과 구별되지 않는다."""
    rc.ctl.bind_detection(lambda: None)
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is False
    assert rc.area() == []


def test_motion_blocked_still_counts_as_visible(rc):
    """⚠️ [2026-08-02] **계약이 뒤집혔다 — 자세는 '보이나'와 무관하다.**

    예전에는 `motion_ok=False`(누움·측면·기준측정)를 "안 보인다"로 쳤다. 그런데
    `requester_visible` 은 **GuideExec 가 nav2 목표를 취소할지**를 정하는 신호다.
    길잡이는 로봇이 앞서 가고 요청자가 뒤따르는 구조라, 뒷카메라가 옆모습을
    잡았다는 이유로 안내를 통째로 중단하면 안 된다 — 사용자 확정 스펙:
    "길잡이는 pose 상관없이 정면 사진을 찍어서 그 사람을 계속 인식해서 간다."

    자세 게이트는 **추종**(로봇이 사람에게 다가감)의 안전 규칙이고, 그건
    `control_loop` 이 역할을 보고 따로 적용한다. 여기는 "그 사람이 거기 있나"만 답한다.
    """
    rc.ctl.bind_detection(lambda: Det(motion_ok=False))
    rc.send(action='guide_watch', id='g1')
    rc.ctl._publish_requester()
    assert rc.vis()[-1] is True, "자세를 이유로 '안 보인다'고 했다 — 안내가 중단된다"


def test_predicted_detection_still_counts_as_not_visible(rc):
    """⚠️ 안전 방향은 그대로다. 예측 bbox 는 '실제로 따라온다'의 근거가 아니다.

    코스팅은 추종의 연속성을 위한 장치다. 안내에서 유령을 보고 계속 전진하면
    요청자를 두고 가 버린다.
    """
    rc.ctl.bind_detection(lambda: Det(is_predicted=True))
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


# ── 스냅샷은 안내에서도 나가야 한다 ──────────────────────────────────────────
# 관제 BT 화면의 접합점은 이미 FollowExec·GuideExec 둘 다 안다
# (libi_modes/ros/state_io.py `_GRAFT_POINTS`). 빠진 건 발행자쪽뿐이었다.

def _tree_of(payload):
    return json.loads(payload)['tree']


def test_snapshot_published_for_guide(rc):
    rc.send(action='guide_watch', id='g1')
    rc.ctl.tick()
    assert _tree_of(rc.snap()[-1]) is not None


def test_snapshot_carries_the_recovery_subtree_for_guide(rc):
    """발행 여부가 아니라 **회복 서브트리 자체가 실려 나가는지**를 잰다.

    이전 시험은 TRACKING 고정 FakeLoop 로 "뭔가 나가는가"만 쟀다 — SEARCHING
    상태에서 search_tree 가 실제로 페이로드에 들어가는지는 안 쟀다
    (2026-08-01 codex 지적).
    """
    class FakeNode:
        """`snapshot_dict()`가 읽는 최소 표면 — kind는 `type(node).__name__`으로
        계산되므로(follow_node._kind) 여기 별도 속성을 안 둔다."""
        name = 'BT_Searching'
        status = types.SimpleNamespace(name='RUNNING')
        children = []

    rc.send(action='guide_watch', id='g1')
    rc.session._loop = FakeLoop(state='SEARCHING', search_tree=FakeNode())
    rc.ctl.tick()
    tree = _tree_of(rc.snap()[-1])
    assert tree is not None
    assert tree['name'] == 'BT_Searching'


def test_snapshot_still_published_for_follow(rc):
    """추종은 예전 그대로."""
    rc.send(action='follow_admin', id='f1')
    rc.ctl.tick()
    assert _tree_of(rc.snap()[-1]) is not None


def test_snapshot_not_published_for_watch(rc):
    """등록감시에서는 두 잎 다 안 돈다 — 내면 안 도는 잎 밑에 붙어 화면이 거짓말한다."""
    rc.send(action='watch', id='w1')
    rc.ctl.tick()
    assert _tree_of(rc.snap()[-1]) is None


# ── 길잡이 회복 회전 허가 (2026-08-02) ───────────────────────────────────────
#
# 길잡이 주행은 nav2 가 한다. 그래서 `FollowNode._publish_for_role` 이 감시 역할의
# `/cmd_vel` 을 평소에 **삼킨다** — 두 주체가 같은 토픽을 밀면 중재자가 없다.
#
# 사용자 스펙(2026-08-02): "길잡이도 회복 때 추종처럼 회전한다." 그러려면 nav2 가
# 정말 멈춘 뒤에만 바퀴를 넘겨야 하는데 두 프로세스라 그 사실을 공유할 길이 없었다.
# `GuideExec` 이 `mission_stop` 을 낸 **다음에** `guide_watch{allow_rotate:true}` 로
# 알린다. 여기서는 그 값이 실제로 들어와 유지되는지, 세션이 재시작되지 않는지를 본다.

def test_guide_watch_defaults_to_no_rotation(rc):
    """기본은 금지다 — 안 실어 보내면 바퀴는 nav2 것."""
    rc.send(action='guide_watch', id='g1')
    assert rc.ctl.rotate_allowed is False


def test_allow_rotate_flag_is_taken(rc):
    rc.send(action='guide_watch', id='g1', args={'camera': 'back', 'allow_rotate': True})
    assert rc.ctl.rotate_allowed is True


def test_toggling_rotation_does_not_restart_the_recovery_tree(rc):
    """⚠️ 이게 핵심이다.

    `FleetCmdDriver.start` 는 **매번 새 id** 를 만들고 `SessionManager.start` 는 id 가
    다르면 새 Session 을 만든다. 그대로 두면 회전을 허가하는 재발행 한 번마다
    제어 루프가 새로 지어져 **회복 트리가 처음부터 다시 돈다** — 켜자마자 0초로
    되감기는 셈이다. 같은 역할의 감시 세션이 살아 있으면 인자만 갱신해야 한다.
    """
    rc.send(action='guide_watch', id='g1')
    assert rc.session.started == 1

    rc.send(action='guide_watch', id='g2', args={'camera': 'back', 'allow_rotate': True})
    assert rc.ctl.rotate_allowed is True
    assert rc.session.started == 1, "회전 허가가 제어 루프를 재시작시켰다"

    rc.send(action='guide_watch', id='g3', args={'camera': 'back', 'allow_rotate': False})
    assert rc.ctl.rotate_allowed is False
    assert rc.session.started == 1, "회전 해제가 제어 루프를 재시작시켰다"


def test_latest_guide_watch_id_can_stop_the_preserved_session(rc):
    """인자 갱신은 루프를 보존하되 마지막 드라이버 id 로 닫혀야 한다."""
    rc.send(action='guide_watch', id='g1')
    rc.send(action='guide_watch', id='g2', args={'camera': 'back', 'allow_rotate': True})
    assert rc.ctl._sessions.session_id == 'g2'
    rc.send(action='follow_stop', id='stop-g2')
    assert rc.session.stopped == 1


def test_session_stop_revokes_rotation(rc):
    """세션이 닫히면 허가도 없던 일이다.

    안 지우면 다음 감시 세션이 **회전이 이미 허가된 채** 시작해, 출발 전에 바퀴가 돈다.
    """
    rc.send(action='guide_watch', id='g1', args={'camera': 'back', 'allow_rotate': True})
    assert rc.ctl.rotate_allowed is True
    rc.send(action='stop', id='g1')
    assert rc.ctl.rotate_allowed is False


def test_follow_session_is_unaffected_by_the_flag(rc):
    """추종은 원래 바퀴를 쓰므로 이 플래그와 무관하다 — 회귀 방지."""
    rc.send(action='follow_admin', id='f1', args={'allow_rotate': True})
    assert rc.session.started == 1
    assert rc.cam()[-1] == 'front'


# ── 고아 GUIDE 세션이 패널을 영구히 잠그면 안 된다 (2026-08-02) ──────────────
#
# GUIDE 세션 중 패널의 `watch` 를 거절하는 보호(위 test_panel_watch_cannot_replace_...)는
# 맞다. 그런데 GUIDE 세션은 **lease 면제**다(session.py `expired` — BT 가 열고 BT 가
# 닫는다). 둘이 겹치면 `fsm_node` 가 죽는 순간 세션을 닫을 주체가 사라지고, 그 세션이
# 영원히 남아 **패널이 길잡이 등록 화면을 다시는 못 연다.** 재부팅 말고 길이 없다.
#
# 그래서 GuideExec 이 WATCH_RENEW_SEC(15초)마다 guide_watch 를 재발행하고,
# follow_node 는 GUIDE_ORPHAN_SEC(45초) 넘게 소식이 없으면 주인 없는 것으로 본다.

def test_live_guide_still_blocks_the_panel(rc):
    """갱신이 계속 오는 동안에는 패널이 못 뺏는다 — 보호가 살아 있어야 한다."""
    rc.send(action='guide_watch', id='g1', args={'camera': 'back'})
    assert rc.ctl._sessions.role == 'guide'
    rc.clock.t += config.GUIDE_ORPHAN_SEC - 5      # 아직 고아 아님
    rc.send(action='watch', id='panel-1', args={'camera': 'front'})
    assert rc.ctl._sessions.role == 'guide', "살아 있는 안내를 패널이 뺏었다"


def test_renewal_keeps_the_guide_alive_indefinitely(rc):
    """GuideExec 의 주기적 재발행이 고아 판정을 계속 미룬다."""
    rc.send(action='guide_watch', id='g1', args={'camera': 'back'})
    for i in range(6):                              # 15초 간격으로 6번 = 90초
        rc.clock.t += 15.0
        rc.send(action='guide_watch', id=f'g-renew-{i}',
                args={'camera': 'back', 'allow_rotate': False})
    rc.clock.t += 5.0
    rc.send(action='watch', id='panel-1', args={'camera': 'front'})
    assert rc.ctl._sessions.role == 'guide', "갱신 중인데 고아로 잡혔다"


def test_orphaned_guide_can_be_taken_over_by_the_panel(rc):
    """⚠️ 이게 잠금 방지의 핵심.

    BT 가 죽어 갱신이 끊기면, 그 세션은 아무도 못 닫는다(lease 면제).
    그 상태로 거절만 하면 패널이 영영 등록을 못 연다.
    """
    rc.send(action='guide_watch', id='g1', args={'camera': 'back'})
    assert rc.ctl._sessions.role == 'guide'

    rc.clock.t += config.GUIDE_ORPHAN_SEC + 1       # BT 가 죽어 갱신이 끊겼다
    rc.send(action='watch', id='panel-1', args={'camera': 'front'})
    assert rc.ctl._sessions.role == 'watch', \
        "고아 GUIDE 세션이 패널을 영구히 잠갔다 — 재부팅 말고 길이 없다"
    assert rc.cam()[-1] == 'front'


def test_renewal_does_not_restart_the_control_loop(rc):
    """재발행이 제어 루프를 새로 지으면 **회복 트리가 15초마다 리셋된다** —
    막으려던 바로 그 버그가 갱신 때문에 되살아난다."""
    rc.send(action='guide_watch', id='g1', args={'camera': 'back'})
    assert rc.session.started == 1
    for i in range(4):
        rc.clock.t += 15.0
        rc.send(action='guide_watch', id=f'g-renew-{i}', args={'camera': 'back'})
    assert rc.session.started == 1, "갱신이 제어 루프를 재시작시켰다"
