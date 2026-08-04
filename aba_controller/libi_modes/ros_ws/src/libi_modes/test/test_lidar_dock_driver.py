"""라이다 도킹 ROS 드라이버.

여기서만 잡히는 것 셋:

  · 끝나고 0 을 안 내면 twist_mux(0.5s)·모터 워치독(0.5s)까지 굴러간다.
    0.05m/s 면 2.5cm — **마지막 한 걸음(5mm)의 5배다**
  · 시작 전·끝난 뒤에 발행하면 다른 주체의 주행을 덮어쓴다
  · 부호가 뒤집히면 도크를 들이받는다

`geometry_msgs`/`sensor_msgs` 가 없는 노트북에서는 건너뛴다 — 로봇에서는 돈다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("geometry_msgs", reason="ROS 2 메시지 없이 Twist 를 만들 수 없다")
pytest.importorskip("sensor_msgs", reason="ROS 2 메시지 없이 LaserScan 을 만들 수 없다")

import libi_modes.ros.lidar_dock_driver as lidar_dock_driver  # noqa: E402
from libi_modes.lidar.config import LidarDockConfig  # noqa: E402
from libi_modes.ros.lidar_dock_driver import LidarDockDriver, NoopDriver  # noqa: E402


class _FakePub:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append((msg.linear.x, msg.angular.z))


class _FakeNode:
    """드라이버가 노드에서 쓰는 것만 흉내낸다."""

    def __init__(self):
        self.pub = _FakePub()
        self.timer_cb = None
        self.sub_cb = None
        self.logs = []

    def create_publisher(self, _type, _topic, _depth):
        return self.pub

    def create_subscription(self, _type, _topic, cb, _qos):
        self.sub_cb = cb
        return cb

    def create_timer(self, _period, cb):
        self.timer_cb = cb
        return cb

    def get_logger(self):
        node = self

        class _Log:
            def info(self, m):
                node.logs.append(m)

            def warning(self, m):
                node.logs.append(m)

            def error(self, m):
                node.logs.append(m)
        return _Log()


def _driver(**cfg_kw):
    node = _FakeNode()
    clock = {"t": 0.0}
    drv = LidarDockDriver(node, "/scan", "cmd_vel_dock",
                          LidarDockConfig(**cfg_kw), now=lambda: clock["t"])
    return node, drv, clock


def test_publishes_nothing_before_start():
    node, drv, _ = _driver()
    node.timer_cb()
    node.timer_cb()
    assert node.pub.sent == []


def test_missing_scan_fails_and_publishes_zero():
    node, drv, clock = _driver(scan_timeout_s=0.5)
    drv.start()
    clock["t"] = 5.0
    node.timer_cb()
    assert drv.poll() == "failure"
    assert node.pub.sent, "실패로 끝나도 0 을 내야 한다"
    assert all(v == (0.0, 0.0) for v in node.pub.sent)


def test_first_tick_right_after_start_does_not_fail_before_grace_period():
    """실측(2026-08-03, pinky-3): 재시작 직후 3연속 26~52ms 만에 scan_timeout —
    `start()` 가 `_msg_at` 을 지운 직후 콜백보다 먼저 tick 이 오는 경합이었다.
    그레이스 구간(< scan_timeout_s) 안에서는 콜백이 아직 안 왔어도 실패시키지 않는다."""
    node, drv, clock = _driver(scan_timeout_s=0.5)
    drv.start()
    clock["t"] = 0.05
    node.timer_cb()
    assert drv.poll() == "running"
    assert node.pub.sent == [], "그레이스 구간엔 아무것도 발행하지 않는다 — 입력 없음"


def test_stop_publishes_zero_several_times():
    """발행만 멈추면 워치독이 세울 때까지 굴러간다 — 이동량보다 큰 거리다."""
    node, drv, _ = _driver()
    drv.start()
    drv.stop()
    zeros = [v for v in node.pub.sent if v == (0.0, 0.0)]
    assert len(zeros) >= 5


def test_poll_is_running_between_start_and_finish():
    node, drv, _ = _driver()
    assert drv.poll() == "running"
    drv.start()
    assert drv.poll() == "running"


def test_forward_commands_are_blocked_at_the_publish_boundary():
    """상태기계가 틀려도 여기서 막는다. 후진 도킹에서 전진은 언제나 버그다."""
    node, drv, _ = _driver()
    drv.start()
    drv._publish(0.5, 0.0)          # 전진을 억지로 낸다
    assert node.pub.sent[-1][0] <= 0.0


def test_noop_driver_never_finishes_and_never_publishes():
    """`BackCamOn` 은 SUCCESS 도 FAILURE 도 내면 안 된다 — Parallel(SuccessOnOne)
    안이라 SUCCESS 면 복귀가 통째로 끝나고 FAILURE 는 Parallel 을 죽인다."""
    d = NoopDriver()
    d.start()
    assert d.poll() == "running"
    d.stop()
    assert d.poll() == "running"


# ── 위 6개가 안 건드리는 자리 (직접 발견, 검증 완료) ──────────────────────────
#
# 위 6개 시험 중 `_msg` 를 채운 채로(`node.sub_cb(...)`) `node.timer_cb()` 를 부르는
# 것이 하나도 없다 — `test_missing_scan_fails_and_publishes_zero` 조차 `_msg_at`
# 이 `None` 인 채로(스캔을 한 번도 못 받은 경우) 끝난다. 그 결과 `_tick` 의 try
# 블록(진짜 detect/step 호출부, 그리고 그 예외 방어)과, "한 번은 살아 있다가 멎은"
# 스캔의 나이 판정이 브리프의 시험만으로는 전혀 실행되지 않는다.
#
# 확인 방법: try/except 를 통째로 지우거나 `_msg_at is None` 만 보게 줄여 위 6개를
# 다시 돌려 봤다 — 둘 다 6개 전부 그대로 통과했다(안 걸린다). 이 두 시험이 그 자리를
# 메운다.

def test_tick_exception_stops_the_robot_instead_of_leaving_the_last_command(monkeypatch):
    """`_tick` 안에서 `detect()` 가 예외를 내면, 이 콜백만 죽고 마지막 명령이 그대로
    남을 수 있다 — twist_mux(0.5s)·모터 워치독(0.5s)까지 굴러간다. try/except 를
    지우면 여기서 `node.timer_cb()` 자체가 예외를 던져 이 시험이 죽는다(검증함)."""
    node, drv, _ = _driver()
    drv.start()
    msg = SimpleNamespace(ranges=[1.0] * 10, angle_min=0.0, angle_increment=0.01,
                          range_min=0.05, range_max=2.0)
    node.sub_cb(msg)                        # 스캔 도착, 안 낡았다

    def _boom(*_a, **_k):
        raise RuntimeError("센서 파싱 실패")
    monkeypatch.setattr(lidar_dock_driver, "detect", _boom)

    node.timer_cb()                         # 여기서 예외가 새 나가면 이 줄이 죽는다

    assert drv.poll() == "failure"
    assert node.pub.sent, "예외로 끝나도 0 을 내야 한다"
    assert all(v == (0.0, 0.0) for v in node.pub.sent)


def test_detect_near_is_never_called_while_acquiring(monkeypatch):
    """ACQUIRE 는 NEAR 관측을 받으면 안 된다(직선 피팅이라는 오검출 방어가 없다) —
    그러니 이 국면에서는 `detect_near` 를 아예 부르지도 않아야 한다. 상태기계 쪽
    가드(`test_lidar_approach.py`)와는 별개로 드라이버 쪽 가드도 이 시험으로 고정한다."""
    node, drv, _ = _driver()
    drv.start()
    drv._machine.phase = "ACQUIRE"          # SETTLE 을 건너뛰고 바로 확인 국면으로
    msg = SimpleNamespace(ranges=[1.0] * 10, angle_min=0.0, angle_increment=0.01,
                          range_min=0.05, range_max=2.0)
    node.sub_cb(msg)

    monkeypatch.setattr(lidar_dock_driver, "detect", lambda *a, **k: None)
    near_calls = []
    monkeypatch.setattr(lidar_dock_driver, "detect_near",
                        lambda *a, **k: near_calls.append(1))

    node.timer_cb()

    assert near_calls == [], "ACQUIRE 에서 detect_near 를 부르면 안 된다"


def test_search_phase_detects_with_the_widened_cfg_not_the_narrow_one(monkeypatch):
    """SEARCH 국면에서는 좁은 기본 창(±60도)이 아니라 넓힌 탐색 창으로 `detect()`
    를 불러야 한다 — 사전 회전이 크게 어긋나도 벽을 찾을 수 있어야 한다(실측
    2026-08-03, 벽 yaw -40.7도로 넘어온 사례)."""
    node, drv, _ = _driver(sector_half_deg=60.0, search_half_deg=175.0)
    drv.start()
    drv._machine.phase = "SEARCH"
    msg = SimpleNamespace(ranges=[1.0] * 10, angle_min=0.0, angle_increment=0.01,
                          range_min=0.05, range_max=2.0)
    node.sub_cb(msg)

    seen_cfgs = []
    monkeypatch.setattr(lidar_dock_driver, "detect",
                        lambda *a, **k: seen_cfgs.append(a[-1]) or None)

    node.timer_cb()

    assert seen_cfgs, "detect 가 호출돼야 한다"
    assert seen_cfgs[0].sector_half_deg == 175.0, "SEARCH 는 넓힌 cfg 로 검출해야 한다"


def test_acquire_phase_still_detects_with_the_narrow_cfg(monkeypatch):
    """SEARCH 를 벗어나면 다시 좁은 기본 창을 써야 한다 — 안 그러면 노치 아닌
    다른 벽·물체를 오검출로 잡을 여지가 넓힌 창만큼 커진다."""
    node, drv, _ = _driver(sector_half_deg=60.0, search_half_deg=175.0)
    drv.start()
    drv._machine.phase = "ACQUIRE"
    msg = SimpleNamespace(ranges=[1.0] * 10, angle_min=0.0, angle_increment=0.01,
                          range_min=0.05, range_max=2.0)
    node.sub_cb(msg)

    seen_cfgs = []
    monkeypatch.setattr(lidar_dock_driver, "detect",
                        lambda *a, **k: seen_cfgs.append(a[-1]) or None)

    node.timer_cb()

    assert seen_cfgs and seen_cfgs[0].sector_half_deg == 60.0


def test_scan_gone_stale_after_being_live_is_treated_as_stale(monkeypatch):
    """스캔이 한 번 들어온 뒤(즉 `_msg_at` 이 `None` 이 아닌 채로) 뚝 끊기는 경우 —
    센서 고장·드라이버 죽음이 이거다. `_msg_at is None` 만 보면 이 경우를 놓치고
    몇 초 전 프레임으로 상태기계를 계속 돌린다."""
    node, drv, clock = _driver(scan_timeout_s=0.2)
    drv.start()
    msg = SimpleNamespace(ranges=[1.0] * 10, angle_min=0.0, angle_increment=0.01,
                          range_min=0.05, range_max=2.0)
    node.sub_cb(msg)                        # t=0 에 스캔 한 번 도착
    clock["t"] = 5.0                        # 그 뒤로 끊겼다 — 나이 4.8s > 0.2s

    called = []
    monkeypatch.setattr(lidar_dock_driver, "detect",
                        lambda *a, **k: called.append(1))
    node.timer_cb()

    assert not called, "낡은 스캔으로 검출을 다시 돌리면 안 된다"
    assert drv.poll() == "failure"
    assert node.pub.sent and all(v == (0.0, 0.0) for v in node.pub.sent)
