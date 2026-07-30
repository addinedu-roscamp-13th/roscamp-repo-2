"""복귀 ④ `DockNudge` 가 쓰는 개루프 미세 이동 드라이버.

## 왜 이걸 시험하나

이 드라이버는 **거리를 시간으로 판다.** 눈으로 확인할 수단이 없는 구간이라
(마커가 화각을 벗어난다) 틀려도 로그에 안 나오고, 틀린 만큼 로봇이 충전 단자를
비껴 서거나 들이받는다. 그런데 실물에서만 드러나는 결함이 아니다 — 여기서 잡히는
것들이 있다:

  · 정지 구간을 빼먹으면 명령을 끊은 뒤 twist_mux timeout(0.5s) 동안 계속 굴러간다
  · 부호를 잃으면 후진이어야 할 것이 전진이 된다
  · 시간이 다 되기 전에 `poll()` 이 성공을 내면 다음 단계가 겹친다

`geometry_msgs` 가 없는 환경(개발 노트북)에서는 건너뛴다 — 로봇/CI 에서는 돈다.
"""
import pytest

pytest.importorskip("geometry_msgs", reason="ROS 2 메시지 없이 Twist 를 만들 수 없다")

from libi_modes.ros.fleet_cmd_driver import NudgeDriver  # noqa: E402


class _FakePub:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg.linear.x)


class _FakeNode:
    """`NudgeDriver` 가 노드에서 쓰는 네 가지만 흉내낸다."""

    def __init__(self):
        self.pub = _FakePub()
        self.t = 0.0
        self.timer_cb = None
        self.logs = []

    # -- rclpy Node 흉내 --------------------------------------------------
    def create_publisher(self, _msg_type, _topic, _depth):
        return self.pub

    def create_timer(self, _period, cb):
        self.timer_cb = cb
        return cb

    def get_clock(self):
        node = self

        class _Clock:
            def now(self):
                class _T:
                    nanoseconds = node.t * 1e9
                return _T()
        return _Clock()

    def get_logger(self):
        node = self

        class _Log:
            def info(self, msg):
                node.logs.append(msg)
        return _Log()

    # -- 시험 편의 --------------------------------------------------------
    def advance(self, dt, step=0.05):
        """`dt` 초를 20Hz 타이머로 굴린다."""
        end = self.t + dt
        while self.t < end - 1e-9:
            self.t = min(self.t + step, end)
            self.timer_cb()

    def jump(self, dt):
        """callback 하나가 `dt` 초 밀려서 도착한다 — CPU 부하로 실제로 일어난다."""
        self.t += dt
        self.timer_cb()


def _driver(**over):
    node = _FakeNode()
    # config/params.yaml 의 배포값 그대로다 — 3cm 를 0.08m/s 로, 즉 0.375초.
    cfg = dict(distance_m=0.03, speed_mps=-0.08, zero_sec=0.3)
    cfg.update(over)
    return node, NudgeDriver(node, "/cmd_vel_dock", **cfg)


def test_silent_until_started():
    """안 도는 동안 발행하면 twist_mux 가 이 입력을 계속 살아 있는 것으로 본다 —
    priority 120 이라 그동안 nav2 를 막는다."""
    node, _ = _driver()
    node.advance(1.0)
    assert node.pub.sent == []


def test_drives_for_distance_over_speed():
    """3cm ÷ 0.08m/s = 0.375초. 그 동안만 속도를 낸다."""
    node, drv = _driver()
    drv.start()
    node.advance(0.3)
    assert all(v == pytest.approx(-0.08) for v in node.pub.sent)
    assert len(node.pub.sent) == pytest.approx(6, abs=1)       # 0.3s × 20Hz


def test_sign_is_the_direction():
    """음수 = 후진. 부호를 잃으면 충전소 반대로 간다."""
    node, drv = _driver(speed_mps=-0.08)
    drv.start()
    node.advance(0.3)
    assert node.pub.sent[0] < 0

    node2, drv2 = _driver(speed_mps=0.08)
    drv2.start()
    node2.advance(0.3)
    assert node2.pub.sent[0] > 0


def test_publishes_real_zeros_at_the_end():
    """발행만 멈추면 twist_mux timeout(0.5s)·모터 워치독(0.5s)이 세울 때까지 굴러간다.
    0.08m/s 면 그동안 4cm 를 더 간다 — 3cm 를 재는 판에 이동량보다 크다."""
    node, drv = _driver()
    drv.start()
    node.advance(0.6)                       # 주행 0.375s + 0 구간
    assert node.pub.sent[-1] == 0.0
    assert 0.0 in node.pub.sent


def test_running_until_the_time_is_up():
    """시간이 남았는데 성공을 내면 ⑤가 먼저 시작돼 관성이 남은 채 CHARGING 이 뜬다."""
    node, drv = _driver()
    drv.start()
    assert drv.poll() == "running"
    node.advance(0.3)
    assert drv.poll() == "running"
    node.advance(0.5)                       # 주행 + 0 구간을 다 지난다
    assert drv.poll() == "success"


def test_stop_still_emits_zeros():
    """중단도 **0 을 내고** 멈춘다. 조용히 손을 떼면 위 timeout 만큼 굴러간다."""
    node, drv = _driver()
    drv.start()
    node.advance(0.2)
    drv.stop()
    node.advance(0.1)
    assert node.pub.sent[-1] == 0.0
    node.advance(0.4)
    assert drv.poll() == "success"


# ── 지터 (codex 리뷰 2026-07-30) ────────────────────────────────────────────
#
# 이 Pi 는 CPU 부하로 executor 가 밀린 이력이 있다(nav2 가 제어 주기를 놓칠 정도).
# 아래 둘은 균일한 50ms tick 만 흉내내면 절대 안 걸리는 결함이다.

def test_drive_window_starts_at_the_first_publish_not_at_start():
    """`start()` 는 아무것도 발행하지 않는다. 종료 시각을 `start()` 기준으로 박으면
    첫 callback 이 늦은 만큼 **덜 간다.**"""
    node, drv = _driver()
    drv.start()
    node.jump(0.30)                         # 첫 callback 이 0.30초 늦게 왔다
    node.advance(0.6)                       # 0.375초 창이 여기서 끝나야 한다
    driving = [v for v in node.pub.sent if v != 0.0]
    # 실제로 민 시간 = 발행 횟수 ÷ 20Hz. 지연과 무관하게 0.375초여야 한다.
    assert len(driving) == pytest.approx(8, abs=1)


def test_zeros_are_counted_not_timed():
    """0 구간을 시각으로 재면, 주행 종료 직후 callback 이 0.3초 넘게 밀렸을 때 0 을
    **한 번도 안 낸다.** 그러면 twist_mux(0.5s)·모터 워치독(0.5s)이 세울 때까지
    최대 2.5cm 를 더 간다 — 막으려던 바로 그 결함이 되살아난다."""
    node, drv = _driver()
    drv.start()
    node.advance(0.35)                      # 주행 구간을 다 쓴다 (아직 0 은 안 냈다)
    node.jump(2.0)                          # 그 뒤 callback 이 2초 밀려서 도착
    assert node.pub.sent[-1] == 0.0, "지연된 tick 에서 0 을 안 냈다"


def test_stop_before_the_first_tick_does_not_start_a_fresh_run():
    """`_deadline` 을 None 으로 둔 채 멈추면, 다음 tick 이 자기를 '첫 발행'으로 보고
    **거기서부터 3cm 를 새로 간다.**"""
    node, drv = _driver()
    drv.start()
    drv.stop()                              # 타이머가 한 번도 안 돌았다
    node.advance(1.0)
    assert all(v == 0.0 for v in node.pub.sent), "끊었는데 주행 명령이 나갔다"


def test_restart_resets_the_clock():
    """`AbsorbFailure` 가 재시도하면 처음부터 다시 3cm 다."""
    node, drv = _driver()
    drv.start()
    node.advance(0.8)
    assert drv.poll() == "success"
    drv.start()
    node.advance(0.2)
    assert drv.poll() == "running"
    assert node.pub.sent[-1] == pytest.approx(-0.08)
