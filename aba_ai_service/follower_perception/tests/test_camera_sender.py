"""단일 송출 프로세스: 생프레임 2슬롯 탭 + camera_select 만료 워치독.

카메라도 ROS 도 없이 돈다 — 그게 이 구조를 나눈 이유다.
"""
import os
import tempfile

import numpy as np
import pytest

from scripts import frame_tap
from scripts.camera_select import CameraSelect
from scripts.camera_sender import run


@pytest.fixture(autouse=True)
def tmp_tap(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("LIBI_CAM_TAP_DIR", d)
        yield d


def _frames(tag, n=5):
    """식별 가능한 프레임 n 장. (0,0,0) 픽셀에 tag 를 심는다."""
    for i in range(n):
        f = np.zeros((4, 4, 3), dtype=np.uint8)
        f[0, 0, 0] = tag
        yield f


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, frame):
        self.sent.append(np.asarray(frame).copy())

    def close(self):
        pass


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.01
        return self.t


def _run(select, front_tag=1, back_tag=2, n=3, back=True):
    sender = FakeSender()
    seq, sent = run(_frames(front_tag, n), _frames(back_tag, n) if back else None,
                    sender, select,
                    orient_front=lambda f: f, orient_back=lambda f: f,
                    fps=0, now=Clock(), max_frames=n)
    return sender, seq, sent


# ── 탭 ──────────────────────────────────────────────────────────────────────

def test_both_slots_written_even_when_selection_is_none():
    """`none` 은 '송출 중단' 이지 '캡처 중단' 이 아니다.
    탭까지 멈추면 복귀 중 도는 마커 도킹이 프레임을 못 얻어 조용히 죽는다."""
    select = CameraSelect(expiry_sec=0)
    select.set("none", 0.0)
    sender, _, sent = _run(select)
    assert sent == 0                       # 아무것도 안 보냈다
    assert frame_tap.read("front") is not None
    assert frame_tap.read("back") is not None


def test_tap_slots_are_independent():
    select = CameraSelect(expiry_sec=0)
    select.set("none", 0.0)
    _run(select, front_tag=11, back_tag=22)
    assert frame_tap.read("front")[0][0, 0, 0] == 11
    assert frame_tap.read("back")[0][0, 0, 0] == 22


def test_tap_records_sequence_and_stamp():
    select = CameraSelect(expiry_sec=0)
    select.set("none", 0.0)
    _run(select, n=3)
    _, seq, stamp = frame_tap.read("front")
    assert seq == 3
    assert stamp > 0


def test_cleanup_removes_slots():
    """프로세스가 끝나면 슬롯을 지운다. 남겨두면 소비자가 죽은 프로세스의 마지막
    프레임을 신선한 것으로 읽어, 정지한 화면을 보고 계속 주행한다."""
    select = CameraSelect(expiry_sec=0)
    select.set("none", 0.0)
    _run(select)
    assert frame_tap.read("front") is not None       # 루프는 지우지 않는다
    frame_tap.cleanup()
    assert frame_tap.read("front") is None
    assert frame_tap.read("back") is None


def test_cleanup_is_idempotent():
    frame_tap.cleanup()
    frame_tap.cleanup()                              # 두 번 불러도 죽지 않는다


def test_read_missing_slot_returns_none():
    assert frame_tap.read("back") is None


def test_unknown_slot_rejected():
    with pytest.raises(ValueError):
        frame_tap.path("side")


# ── 선택에 따른 송출 ────────────────────────────────────────────────────────

def test_front_selection_sends_front_frames():
    select = CameraSelect(expiry_sec=0)
    select.set("front", 0.0)
    sender, _, sent = _run(select, front_tag=1, back_tag=2)
    assert sent == 3
    assert all(f[0, 0, 0] == 1 for f in sender.sent)


def test_back_selection_sends_back_frames():
    select = CameraSelect(expiry_sec=0)
    select.set("back", 0.0)
    sender, _, sent = _run(select, front_tag=1, back_tag=2)
    assert sent == 3
    assert all(f[0, 0, 0] == 2 for f in sender.sent)


def test_back_selected_without_back_camera_sends_nothing():
    """뒷캠 없이 back 을 고르면 아무것도 안 보낸다 — 앞캠을 대신 보내면 안 된다.
    받는 쪽이 뒤를 본다고 믿고 판단하기 때문이다."""
    select = CameraSelect(expiry_sec=0)
    select.set("back", 0.0)
    sender, _, sent = _run(select, back=False)
    assert sent == 0
    assert frame_tap.read("front") is not None


def test_one_dead_camera_does_not_stop_the_other():
    sender = FakeSender()
    select = CameraSelect(expiry_sec=0)
    select.set("front", 0.0)

    def boom():
        raise RuntimeError("USB 캠 빠짐")
        yield  # pragma: no cover

    seq, sent = run(_frames(1, 3), boom(), sender, select,
                    orient_front=lambda f: f, orient_back=lambda f: f,
                    fps=0, now=Clock(), max_frames=3)
    assert sent == 3


# ── 만료 워치독 ────────────────────────────────────────────────────────────

def test_default_is_none():
    assert CameraSelect(expiry_sec=5).current(now=0.0) == "none"


def test_set_then_current():
    cs = CameraSelect(expiry_sec=5)
    cs.set("front", stamp=0.0)
    assert cs.current(now=1.0) == "front"


def test_expires_to_none():
    """발행자가 죽어 갱신이 끊기면 스스로 none 으로 떨어진다.
    latched QoS 는 발행자가 사라지면 캐시도 같이 사라지므로 이쪽이 지켜야 한다."""
    cs = CameraSelect(expiry_sec=5)
    cs.set("front", stamp=0.0)
    assert cs.current(now=6.0) == "none"


def test_refresh_extends():
    cs = CameraSelect(expiry_sec=5)
    cs.set("front", stamp=0.0)
    cs.set("front", stamp=4.0)
    assert cs.current(now=8.0) == "front"


def test_expiry_zero_never_expires():
    cs = CameraSelect(expiry_sec=0)
    cs.set("front", stamp=0.0)
    assert cs.current(now=10_000.0) == "front"


def test_unknown_value_falls_back_to_none():
    """오타 하나로 카메라가 계속 켜지는 것보다, 안 켜지고 로그로 드러나는 편이 낫다."""
    cs = CameraSelect(expiry_sec=5)
    cs.set("frnot", stamp=0.0)
    assert cs.current(now=1.0) == "none"


def test_expired_selection_stops_sending_mid_run():
    """세션이 끝난 뒤에도 영상이 계속 나가는 것을 막는다."""
    sender = FakeSender()
    cs = CameraSelect(expiry_sec=0.02)
    cs.set("front", stamp=0.0)          # 시계는 0.01 씩 흐른다 → 3프레임쯤에 만료
    seq, sent = run(_frames(1, 10), None, sender, cs,
                    orient_front=lambda f: f, orient_back=lambda f: f,
                    fps=0, now=Clock(), max_frames=10)
    assert 0 < sent < 10


# ── 프레임 주기 ─────────────────────────────────────────────────────────────


def test_sleep_subtracts_work_time_so_fps_is_actually_reached(monkeypatch):
    """`--fps` 는 상한이 아니라 **목표값**이어야 한다.

    처리 시간을 빼지 않고 `sleep(1/fps)` 를 걸면 실제 주기가
    `캡처+인코딩 + 1/fps` 가 되어, 15fps 를 시켜도 캡처가 33ms 걸리면 ~10fps 로 떨어진다.
    추종 제어 루프는 20Hz 라 검출이 늦어진 만큼 그대로 반응이 늦는다.
    """
    import scripts.camera_sender as cs

    slept = []
    monkeypatch.setattr(cs.time, "sleep", slept.append)

    # 한 장 처리에 0.04초 걸리는 시계. `run` 은 루프 시작에서 한 번,
    # sleep 계산에서 한 번 호출하므로 그 차이가 처리 시간이 된다.
    ticks = iter([0.0, 0.04, 1.0, 1.04, 2.0, 2.04])
    run(_frames(1, 2), None, FakeSender(), CameraSelect(expiry_sec=5),
        orient_front=lambda f: f, orient_back=lambda f: f,
        fps=10.0, now=lambda: next(ticks), max_frames=2)

    # delay=0.1, 처리 0.04  →  0.06 만 자야 한다 (0.1 이 아니라)
    assert slept == pytest.approx([0.06, 0.06], abs=1e-9)


def test_sleep_never_negative_when_work_exceeds_budget(monkeypatch):
    """처리가 예산보다 오래 걸리면 **안 자고** 바로 다음 장으로 간다."""
    import scripts.camera_sender as cs

    slept = []
    monkeypatch.setattr(cs.time, "sleep", slept.append)

    ticks = iter([0.0, 0.5, 1.0, 1.5])      # 한 장에 0.5초 — 예산 0.1 초과
    run(_frames(1, 2), None, FakeSender(), CameraSelect(expiry_sec=5),
        orient_front=lambda f: f, orient_back=lambda f: f,
        fps=10.0, now=lambda: next(ticks), max_frames=2)

    assert slept == [0.0, 0.0]
