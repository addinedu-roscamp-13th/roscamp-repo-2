"""침입 판정·클립 상태기계 — 실물(카메라·ffmpeg·HTTP) 없이 전부 검증한다.

시계와 sink 를 주입받는 구조라 여기서 타임라인을 자유롭게 조작할 수 있다.
"""
import pytest

from scripts.security_recorder import SecurityRecorder, SecurityParams


class FakeSink:
    """열림/쓰기/닫힘과 보고를 기록만 하는 대역."""

    def __init__(self):
        self.opened, self.closed = [], []
        self.written = []
        self.reports, self.attached = [], []
        self._next_id = 1

    def open_clip(self, name):
        self.opened.append(name)
        self.written.append([])

    def write(self, jpeg):
        if self.written:
            self.written[-1].append(jpeg)

    def close_clip(self):
        self.closed.append(len(self.written[-1]) if self.written else 0)
        return True

    def report(self, clip_name, note):
        self.reports.append((clip_name, note))

    def attach_clip(self, clip_name, clip_path):
        self.attached.append((clip_name, clip_path))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture()
def rec(tmp_path):
    clock, sink = Clock(), FakeSink()
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path,
                         sink=sink, now_fn=clock, params=SecurityParams(fps=10))
    r.arm(True)
    return r, clock, sink


def _see(rec, clock, seconds, size=140.0, step=0.1):
    """`seconds` 동안 사람이 보이는 프레임을 흘린다."""
    n = int(seconds / step)
    for _ in range(n):
        rec.feed(b"J", size)
        clock.t += step


def _empty(rec, clock, seconds, step=0.1):
    n = int(seconds / step)
    for _ in range(n):
        rec.feed(b"J", 0.0)
        clock.t += step


def test_연속_1초_미만이면_트리거하지_않는다(rec):
    r, clock, sink = rec
    _see(r, clock, 0.9)
    assert sink.opened == []
    assert sink.reports == []


def test_연속_1초를_넘으면_클립이_열리고_보고된다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    assert len(sink.opened) == 1
    assert [note for _name, note in sink.reports] == ["야간 순찰 중 인기척 감지"]
    assert sink.reports[0][0] == sink.opened[0]      # 같은 clip_name 을 키로 쓴다


def test_트리거_이전_프레임이_클립에_들어간다_프리롤(rec):
    """확정 시점부터 찍으면 '들어오는 순간'이 안 담긴다."""
    r, clock, sink = rec
    _see(r, clock, 1.1)                       # 11프레임(10fps × 1.1초)
    # 프리롤 3초 = 30프레임까지 들고 있다가 먼저 흘려넣는다.
    # 여기서는 11프레임뿐이므로 그 11개가 전부 들어가야 한다.
    assert len(sink.written[-1]) >= 11


def test_사람이_계속_보이면_종료_시계가_갱신된다_슬라이딩(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    _see(r, clock, 30.0)                      # postroll(20초)보다 길게 계속 보임
    assert sink.closed == []                  # 아직 안 닫혔다


def test_사람이_사라지고_postroll_이_지나면_닫히고_경로가_붙는다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    _empty(r, clock, 19.0)
    assert sink.closed == []                  # 19초는 아직
    _empty(r, clock, 2.0)
    assert len(sink.closed) == 1
    assert len(sink.attached) == 1
    assert sink.attached[0][0] == sink.opened[0]      # 열 때 쓴 clip_name 으로 붙인다


def test_상한을_넘으면_강제로_닫는다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    _see(r, clock, 130.0)                     # 계속 보이지만 120초 상한
    assert len(sink.closed) >= 1


def test_강제_종료는_등록을_해제하지_않는다(tmp_path):
    """로봇이 아직 쫓는 중인데 owner 가 증발하면 추종이 이유 없이 끊긴다."""
    clock, sink = Clock(), FakeSink()
    resets = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: resets.append(clock.t),
                         params=SecurityParams(fps=10))
    r.arm(True)
    _see(r, clock, 1.1)
    _see(r, clock, 130.0)
    assert resets == []                       # 강제 종료로는 안 지운다


def test_강제_종료_뒤_사람이_사라지면_그때_등록을_해제한다(tmp_path):
    """⚠️ 강제 종료로 녹화가 끝나면 상태기계가 '녹화 중'을 벗어난다. 그 뒤를 안 지켜보면
    `reset_fn` 이 **영영 안 불려** 다음 밤 침입자가 어제 갤러리에 매칭된다.
    """
    clock, sink = Clock(), FakeSink()
    resets = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: resets.append(clock.t),
                         params=SecurityParams(fps=10))
    r.arm(True)
    _see(r, clock, 1.1)
    _see(r, clock, 130.0)                     # 상한으로 강제 종료. 사람은 아직 보인다
    assert resets == []
    _empty(r, clock, 21.0)                    # 이제 사라졌다
    assert len(resets) == 1


def test_강제_종료_뒤_대기_중_주간_전환되면_등록을_해제한다(tmp_path):
    """⚠️ 상한 종료로 `_pending_release` 가 켜진 채(대상이 여전히 보여 아직 안
    풀린 채) 사서가 「주간」을 누르면, `_recording` 은 이미 False 라
    `if self._recording: self._stop(...)` 경로를 안 타 등록이 안 풀리고 그대로
    새 지 않으면 다음 밤 침입자가 어제 갤러리에 매칭된다.
    """
    clock, sink = Clock(), FakeSink()
    resets = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: resets.append(clock.t),
                         params=SecurityParams(fps=10))
    r.arm(True)
    _see(r, clock, 1.1)
    _see(r, clock, 130.0)                     # 상한으로 강제 종료. 사람은 아직 보인다
    assert resets == []                       # 아직 대기 중 — 아직 안 풀렸다
    r.arm(False)
    r.feed(None, 0.0)                         # 다음 tick에서 주간 전환이 적용된다
    assert len(resets) == 1                   # 대기 중이던 등록이 여기서 풀려야 한다


def test_등록_해제는_한_번만_불린다(tmp_path):
    clock, sink = Clock(), FakeSink()
    resets = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: resets.append(clock.t),
                         params=SecurityParams(fps=10))
    r.arm(True)
    _see(r, clock, 1.1)
    _see(r, clock, 130.0)
    _empty(r, clock, 60.0)                    # 한참 더 비어 있어도
    assert len(resets) == 1


def test_정상_만료는_등록을_해제한다(tmp_path):
    clock, sink = Clock(), FakeSink()
    resets = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: resets.append(clock.t),
                         params=SecurityParams(fps=10))
    r.arm(True)
    _see(r, clock, 1.1)
    _empty(r, clock, 21.0)
    assert len(resets) == 1


def test_쿨다운_중_재검출은_새_이벤트를_안_만든다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    _empty(r, clock, 21.0)                    # 클립 종료
    _see(r, clock, 2.0)                       # 쿨다운(30초) 안에 다시 보임
    assert len(sink.reports) == 1


def test_쿨다운이_지나면_새_이벤트를_만든다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.1)
    _empty(r, clock, 21.0)
    _empty(r, clock, 31.0)                    # 쿨다운 경과
    _see(r, clock, 1.1)
    assert len(sink.reports) == 2


def test_등록_함수가_트리거_시점에_불린다(tmp_path):
    clock, sink = Clock(), FakeSink()
    calls = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock,
                         register_fn=lambda f, c: calls.append((f, c)),
                         params=SecurityParams(fps=10))
    r.arm(True)
    for _ in range(11):
        r.feed(b"J", 140.0, frame="FRAME", cands=["CAND"])
        clock.t += 0.1
    # 이미 뽑아 둔 후보가 그대로 넘어가야 한다 — 안 넘기면 YOLO 가 두 번 돈다
    assert calls == [("FRAME", ["CAND"])]


def test_무장하지_않으면_아무_일도_안_한다(tmp_path):
    clock, sink = Clock(), FakeSink()
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, params=SecurityParams(fps=10))
    _see(r, clock, 5.0)                       # arm(True) 를 안 불렀다
    assert sink.opened == [] and sink.reports == []


def test_무장_해제되면_다음_tick에서_진행_중_클립을_닫는다(rec):
    """사서가 「주간」을 누르면 미완성 mp4 가 남으면 안 된다.

    arm() 은 _want_armed 플래그만 세우고 실제 전이는 다음 _tick()(= 다음
    feed()) 에서 일어난다 — ModePoller 워커 스레드와 메인 프레임 루프가
    동시에 상태를 건드리는 경합을 피하려는 의도적 설계다. 시험 쪽을 고쳐
    실제 동작(다음 프레임에서 닫힘 — 운영 17fps 라면 ~59ms 이내)을 반영한다.
    """
    r, clock, sink = rec
    _see(r, clock, 1.1)
    r.arm(False)
    r.feed(None, 0.0)                      # 다음 tick에서 전이가 적용된다
    assert len(sink.closed) == 1
    assert len(sink.attached) == 1


def test_프레임이_끊겨도_심장박동으로_클립이_닫힌다(rec):
    """카메라가 꺼지면 feed(None) 만 온다. 시계는 그래도 돌아야 한다."""
    r, clock, sink = rec
    _see(r, clock, 1.1)
    for _ in range(210):                      # 21초분 심장박동(0.1초 간격)
        r.feed(None, 0.0)
        clock.t += 0.1
    assert len(sink.closed) == 1
    assert len(sink.attached) == 1


def test_sink_이_예외를_던져도_feed_는_예외를_안_올린다(tmp_path):
    """메인 루프가 죽으면 추종 제어가 같이 죽는다."""
    class Boom:
        def open_clip(self, name):
            raise RuntimeError("ffmpeg 없음")
        def write(self, jpeg):
            raise RuntimeError("broken pipe")
        def close_clip(self):
            raise RuntimeError("nope")
        def report(self, clip_name, note):
            raise RuntimeError("http 불통")
        def attach_clip(self, clip_name, clip_path):
            raise RuntimeError("http 불통")

    clock = Clock()
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=Boom(),
                         now_fn=clock, params=SecurityParams(fps=10))
    r.arm(True)
    for _ in range(300):
        r.feed(b"J", 140.0)                   # 예외가 새어 나오면 여기서 죽는다
        clock.t += 0.1
