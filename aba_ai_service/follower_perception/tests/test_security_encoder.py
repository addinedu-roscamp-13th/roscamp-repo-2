"""ffmpeg 인코더 sink — 메인 스레드를 절대 막지 않는지, 실제로 H.264 가 나오는지."""
import shutil
import subprocess
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
