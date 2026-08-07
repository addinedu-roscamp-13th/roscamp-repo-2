"""인지 서버 배선 — 플래그가 없으면 예전 경로 그대로, 있으면 녹화기가 붙는다."""
import numpy as np
import pytest

perception_server = pytest.importorskip(
    "scripts.perception_server", reason="cv2/ultralytics 없는 환경에서는 건너뛴다")


class RecorderSpy:
    def __init__(self):
        self.calls = []

    def feed(self, jpeg, size, frame=None, cands=None):
        self.calls.append((jpeg is None, size))

    def close(self):
        pass


class _Perception:
    class _M:
        is_registered = False
        gallery = []
        safe_id = None
        last_reid_sim = None
        last_hsv_sim = None
        reid_threshold = 0.0
        hsv_threshold = None
    matcher = _M()
    last_cands = []
    pose = None

    def run(self, _f):
        pass

    def get_latest(self):
        return None

    def _pick_central(self, _c, _f):
        return None

    def register_nearest(self, _frame, _cands=None):
        pass

    def reset(self):
        pass


def _frames(seq):
    for f in seq:
        yield f


def test_심장박동에서도_녹화기가_불린다():
    """카메라가 꺼져 영상이 안 와도 시계는 돌아야 클립이 닫힌다."""
    spy = RecorderSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy)
    assert spy.calls == [(True, 0.0), (True, 0.0)]


def test_프레임이_오면_jpeg_와_크기가_넘어간다():
    spy = RecorderSpy()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class _Conn:
        def sendall(self, _b):
            pass

    perception_server.serve_loop(
        conn=_Conn(), frames=_frames([frame]), perception=_Perception(),
        poll_cmd=None, recorder=spy)
    assert spy.calls and spy.calls[0][0] is False


def test_녹화기가_없으면_아무_일도_안_일어난다():
    """--security 없이 띄우면 코드 경로가 예전과 같아야 한다."""
    perception_server.serve_loop(
        conn=None, frames=_frames([None]), perception=_Perception(),
        poll_cmd=None, recorder=None)


def test_플래그가_없으면_보안_모듈을_import_조차_안_한다():
    """⚠️ `recorder=None` 만 검사하면 `main()` 의 조건부 import·스레드 생성이 전혀
    실행되지 않아, 옵트인이 기본 경로를 건드려도 초록이다. `build_security` 를 직접
    불러 **아무것도 안 만드는지**를 본다.
    """
    import sys

    class _Args:
        security = False

    before = set(sys.modules)
    got = perception_server.build_security(_Args(), _Perception())
    assert got is None
    assert "scripts.security_recorder" not in (set(sys.modules) - before)


def test_플래그를_주면_녹화기와_종료훅을_만든다(tmp_path):
    class _Args:
        security = True
        security_robot = "pinky-3"
        security_ops = "http://127.0.0.1:8000"
        security_media = str(tmp_path)

    got = perception_server.build_security(_Args(), _Perception())
    assert got is not None
    recorder, shutdown, media = got
    assert media == str(tmp_path)    # live.jpg 가 여기 쓰인다(main 의 live_snapshot_path)
    assert callable(shutdown)
    shutdown()                       # 종료 경로가 예외 없이 돈다


# ── 추격 종료 신호 배선 (2026-08-07) ────────────────────────────────────────

class _RoleSource:
    """`/libi/perception_role` 대역 — 부를 때마다 다음 값을 내놓는다."""

    def __init__(self, seq):
        self._seq = list(seq)

    def latest(self):
        return self._seq.pop(0) if self._seq else None


class _EndSpy(RecorderSpy):
    def __init__(self):
        super().__init__()
        self.ended = 0
        self.wants_armed = True

    def end_chase(self):
        self.ended += 1


def test_역할이_security_에서_빠지면_클립을_즉시_닫는다():
    """`postroll` 을 안 기다린다 — 로봇이 이미 순찰로 돌아갔다."""
    spy = _EndSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy,
        role_source=_RoleSource(["security", "security", "none"]))
    assert spy.ended == 1


def test_추격_중에는_안_닫는다():
    spy = _EndSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy,
        role_source=_RoleSource(["security", "security", "security"]))
    assert spy.ended == 0


def test_계속_none_이면_매_프레임_닫자고_하지_않는다():
    """⚠️ 전이에서만 부른다. 유휴에서 매번 부르면 쿨다운이 계속 밀린다."""
    spy = _EndSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy, role_source=_RoleSource(["none"] * 3))
    assert spy.ended == 0


def test_역할을_못_받으면_예전대로_시계에_맡긴다():
    """ROS 옵트인이 꺼졌거나 도메인이 안 맞으면 `role_source` 가 아예 없다."""
    spy = _EndSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy, role_source=None)
    assert spy.ended == 0
    assert spy.calls == [(True, 0.0), (True, 0.0)], "심장박동은 계속 가야 한다"


class _StateSource:
    def __init__(self, seq):
        self._seq = list(seq)

    def in_security_patrol(self):
        return self._seq.pop(0) if self._seq else True


class _PatrolSpy(RecorderSpy):
    def __init__(self):
        super().__init__()
        self.patrol = []
        self.wants_armed = True

    def set_patrol(self, on):
        self.patrol.append(bool(on))

    def end_chase(self):
        pass


def test_상태원이_있으면_매_프레임_순찰여부를_넘긴다():
    spy = _PatrolSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy,
        state_source=_StateSource([True, False, True]))
    assert spy.patrol == [True, False, True]


def test_상태원이_없으면_순찰여부를_안_건드린다():
    """ROS 옵트인이 없는 배포 — 녹화기 기본값(순찰 중)으로 예전처럼 돈다."""
    spy = _PatrolSpy()
    perception_server.serve_loop(
        conn=None, frames=_frames([None, None]), perception=_Perception(),
        poll_cmd=None, recorder=spy, state_source=None)
    assert spy.patrol == []
    assert spy.calls == [(True, 0.0), (True, 0.0)]
