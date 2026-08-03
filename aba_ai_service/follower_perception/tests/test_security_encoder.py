"""ffmpeg 인코더 sink — 메인 스레드를 절대 막지 않는지, 실제로 H.264 가 나오는지."""
import shutil
import subprocess
import threading
import time

import pytest

from scripts.security_recorder import FfmpegClipWriter, resolve_ffmpeg


def test_ffmpeg_이_없으면_None_을_돌려준다(monkeypatch):
    monkeypatch.delenv("SECURITY_FFMPEG", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert resolve_ffmpeg() is None


def test_환경변수가_which_보다_우선한다(monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SECURITY_FFMPEG", str(fake))
    assert resolve_ffmpeg() == str(fake)


def test_인코더가_없어도_write_가_예외를_안_올린다(tmp_path):
    """ffmpeg 이 없으면 녹화만 비활성 — 이벤트·모달·추종은 계속 돌아야 한다."""
    w = FfmpegClipWriter(tmp_path, fps=10, ffmpeg_path=None)
    w.open_clip("x.mp4")
    for _ in range(100):
        w.write(b"not-a-jpeg")
    assert w.close_clip() is False


def test_큐가_가득_차면_프레임을_버리고_막지_않는다(tmp_path, monkeypatch):
    """녹화 품질 < 제어 안전. write() 가 블록되면 추종이 죽는다.

    ⚠️ `ffmpeg_path=None` 으로는 이 시험이 **아무것도 검증하지 않는다** —
    `open_clip` 이 즉시 돌아와 `_proc` 가 없고 `write()` 도 즉시 반환해 `dropped`
    가 영원히 0 이다. 그래서 **가짜 프로세스를 실제로 주입**한다.
    """
    import subprocess as sp

    class _SlowStdin:
        def write(self, _b):
            time.sleep(10)                 # writer 스레드를 붙잡아 큐를 채운다
        def close(self):
            pass

    class _FakeProc:
        returncode = 0
        stdin = _SlowStdin()
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(sp, "Popen", lambda *a, **k: _FakeProc())

    w = FfmpegClipWriter(tmp_path, fps=10, ffmpeg_path="/bin/true", queue_max=4)
    w.open_clip("y.mp4")
    started = time.monotonic()
    for _ in range(200):
        w.write(b"x")                      # 여기서 블록되면 아래 단언이 깨진다
    assert time.monotonic() - started < 1.0
    assert w.dropped > 0


def test_close_clip_도_큐가_차_있으면_안_막힌다(tmp_path, monkeypatch):
    """종료 sentinel 을 put() 으로 넣으면 큐가 찼을 때 메인 스레드가 멈춘다."""
    import subprocess as sp

    class _SlowStdin:
        def write(self, _b):
            time.sleep(10)
        def close(self):
            pass

    class _FakeProc:
        returncode = 0
        stdin = _SlowStdin()
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(sp, "Popen", lambda *a, **k: _FakeProc())

    w = FfmpegClipWriter(tmp_path, fps=10, ffmpeg_path="/bin/true", queue_max=4)
    w.open_clip("z.mp4")
    for _ in range(50):
        w.write(b"x")
    started = time.monotonic()
    w.close_clip()
    assert time.monotonic() - started < 6.0   # join(5) 은 감수, put 블록은 안 된다


def test_옛_pump_스레드가_다음_클립의_큐로_안_샌다(tmp_path, monkeypatch):
    """`_pump` 가 `self._proc`/`self._q` 를 매번 다시 읽으면, `close_clip()` 이
    큐에 넣어 둔 종료 sentinel(`None`)을 아직 못 받은 채(느린 `stdin.write()`
    안에 갇혀 있느라) 다음 `open_clip()` 이 `self._q` 를 새 클립 큐로 갈아
    끼우는 순간 그 스레드는 **자기 몫의 sentinel 을 영영 못 보고 새 클립의
    큐로 옮겨가 좀비로 남는다** — 그 사이 새 클립의 정상 소비자와 경합하고,
    바로 녹화가 가장 필요한 디스크 스트레스 상황에서 새 클립이 조용히 비거나
    망가질 수 있다. `_pump` 가 시작 시점에 넘겨받은 `proc`/`q` 인자만 쓰면
    큐가 바뀌어도 자기 sentinel 을 정상적으로 받고 바로 끝난다.

    판정 기준은 "누가 어떤 바이트를 썼는가"가 아니라 **스레드 A 가 실제로
    종료하는가** 다 — `self._proc` 도 함께 새 클립 것으로 바뀌므로, 옛
    스레드가 새 큐에서 프레임을 훔쳐도 어차피 "현재" proc(=새 proc)의
    stdin 으로 쓰여 내용만 봐서는 구분이 안 된다. 반면 좀비 여부는 버그가
    있으면 100% 재현되는 결정적 신호다."""
    import subprocess as sp

    class _FakeStdin:
        def __init__(self, sink, hold=None):
            self._sink = sink
            self._hold = hold

        def write(self, b):
            if self._hold is not None:
                self._hold.wait(timeout=5)
            self._sink.append(b)

        def close(self):
            pass

    class _FakeProc:
        returncode = 0

        def __init__(self, stdin):
            self.stdin = stdin

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    writes_a, writes_b = [], []
    hold_a = threading.Event()      # 느린 디스크 흉내 — 시험이 놓아줄 때까지 write() 안에 붙잡는다
    proc_a = _FakeProc(_FakeStdin(writes_a, hold=hold_a))
    proc_b = _FakeProc(_FakeStdin(writes_b))
    procs = [proc_a, proc_b]
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: procs.pop(0))

    w = FfmpegClipWriter(tmp_path, fps=10, ffmpeg_path="/bin/true", queue_max=4)
    w.open_clip("a.mp4")
    thread_a, q_a = w._thread, w._q     # 나중에 좀비인지 확인하려고 내부를 붙잡아 둔다
    w.write(b"1")
    time.sleep(0.1)                     # 스레드 A 가 "1" 을 집어 write() 안에 들어설 시간

    # close_clip("a.mp4") 이 join(timeout=5) 만료 직전까지 간 상태를 그대로 흉내낸다 —
    # 종료 sentinel 은 이미 q_a 에 들어갔지만, 스레드 A 는 여전히 write() 안이다.
    q_a.put_nowait(None)

    # 다음 클립을 연다 — self._q/self._proc 가 클립 B 것으로 갈아 끼워진다.
    w.open_clip("b.mp4")
    w.write(b"2")
    time.sleep(0.1)
    assert writes_b == [b"2"]           # 클립 B 자체는 정상 동작한다

    hold_a.set()                        # 스레드 A 를 풀어준다 — 루프를 한 바퀴 더 돈다
    thread_a.join(timeout=2)
    assert not thread_a.is_alive(), (
        "스레드 A 가 q_a 에 이미 들어가 있던 자기 sentinel 을 못 받고 좀비로 "
        "남았다 — _pump 가 self._q 를 다시 읽어 클립 B 의 큐로 옮겨간 것이다")


@pytest.mark.skipif(resolve_ffmpeg() is None, reason="ffmpeg 없는 환경에서는 건너뛴다")
def test_실제로_브라우저가_여는_H264_가_나온다(tmp_path):
    """mp4v fourcc 는 크롬이 못 여는 고전 함정 — 코덱을 실측으로 못박는다."""
    import cv2
    import numpy as np

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    jpeg = buf.tobytes()

    w = FfmpegClipWriter(tmp_path, fps=10)
    w.open_clip("z.mp4")
    for _ in range(20):
        w.write(jpeg)
    assert w.close_clip() is True

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0",
         str(tmp_path / "z.mp4")],
        capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "h264"
