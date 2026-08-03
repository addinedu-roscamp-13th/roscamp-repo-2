"""관제 보고 sink 와 모드 폴링 — 메인 스레드를 막지 않고, 어떤 예외로도 안 죽는다."""
import time

from scripts.security_recorder import ModePoller, OpsSink


class FakeWriter:
    def __init__(self, ok=True):
        self.ok, self.opened, self.frames = ok, [], 0
    def open_clip(self, name):
        self.opened.append(name)
    def write(self, jpeg):
        self.frames += 1
    def close_clip(self):
        return self.ok


def test_보고와_영상붙이기가_clip_name_으로_이어진다():
    """워커가 POST 응답의 id 를 clip_name 에 저장해 뒤이은 PATCH 가 꺼내 쓴다."""
    posts, patches = [], []
    def post(url, json, timeout):
        posts.append(json)
        return {"id": 7}
    def patch(url, json, timeout):
        patches.append((url, json))
    sink = OpsSink("http://x", "pinky-3", FakeWriter(),
                   post_fn=post, patch_fn=patch)
    sink.report("abc.mp4", "야간 순찰 중 인기척 감지")
    sink.attach_clip("abc.mp4", "/api/admin/ops/security/clips/abc.mp4")
    sink.shutdown()

    assert posts[0]["source"] == "pinky-3"
    assert "zone" not in posts[0]             # 위치는 관제가 채운다
    assert patches[0][0].endswith("/events/7/clip")


def test_보고가_실패하면_영상을_안_붙인다():
    """붙일 이벤트가 없다. 조용히 넘어가고 죽지 않는다."""
    patches = []
    def post(url, json, timeout):
        raise OSError("연결 거부")
    sink = OpsSink("http://x", "pinky-3", FakeWriter(),
                   post_fn=post, patch_fn=lambda *a, **k: patches.append(a))
    sink.report("abc.mp4", "...")
    sink.attach_clip("abc.mp4", "/x")
    sink.shutdown()
    assert patches == []


def test_영상붙이기가_실패하면_재시도한다():
    """관제가 잠깐 재시작하면 영상이 영원히 안 붙는다."""
    tries = {"n": 0}
    def patch(url, json, timeout):
        tries["n"] += 1
        raise OSError("일시 장애")
    sink = OpsSink("http://x", "pinky-3", FakeWriter(),
                   post_fn=lambda *a, **k: {"id": 1}, patch_fn=patch)
    sink.report("abc.mp4", "...")
    sink.attach_clip("abc.mp4", "/x")
    sink.shutdown()
    assert tries["n"] == 1 + OpsSink.MAX_ATTACH_RETRY


def test_클립이_실패하면_경로를_안_붙인다():
    """close_clip 이 False 면 파일이 없다 — 상태기계가 attach 를 안 부른다."""
    sink = OpsSink("http://x", "pinky-3", FakeWriter(ok=False))
    assert sink.close_clip() is False


def test_HTTP_는_워커_스레드로_나가_호출이_즉시_돌아온다():
    """메인 루프를 막으면 추종 반응이 그만큼 늦는다. report 도 마찬가지다."""
    def slow(url, json, timeout):
        time.sleep(0.5)
        return {"id": 1}
    sink = OpsSink("http://x", "pinky-3", FakeWriter(),
                   post_fn=slow, patch_fn=slow)
    started = time.monotonic()
    sink.report("abc.mp4", "...")             # ← 여기가 3초 블록되면 로봇이 사람을 놓친다
    sink.attach_clip("abc.mp4", "/x")
    assert time.monotonic() - started < 0.2   # 둘 다 안 기다렸다
    sink.shutdown()


def test_모드_폴링이_night_를_읽으면_콜백이_불린다():
    seen = []
    p = ModePoller("http://x", on_change=seen.append,
                   get_fn=lambda url, timeout: {"mode": "night"}, interval=0.01)
    p.start()
    time.sleep(0.1)
    p.stop()
    assert True in seen


def test_모드_폴링은_어떤_예외로도_안_끝난다():
    """죽으면 마지막 모드에 영원히 고정된다 — 주간인데 계속 녹화하거나 그 반대."""
    n = {"i": 0}
    def boom(url, timeout):
        n["i"] += 1
        raise RuntimeError("서버 불통")
    p = ModePoller("http://x", on_change=lambda _v: None,
                   get_fn=boom, interval=0.01)
    p.start()
    time.sleep(0.1)
    p.stop()
    assert n["i"] > 2                          # 계속 재시도했다
