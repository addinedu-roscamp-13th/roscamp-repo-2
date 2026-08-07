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
    실제 동작(다음 프레임에서 닫힘 — 운영 15fps 라면 ~67ms 이내)을 반영한다.
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


# ── 추격 종료 신호로 즉시 닫기 (2026-08-07) ─────────────────────────────────
#
# 예전엔 `postroll_sec`(20초) 시계만 클립을 닫았다. 로봇은 `lose_sec`(5초)에 이미
# 추격을 접고 순찰로 돌아가는데 클립은 15초를 더 돌았고, 그 사이 같은 사람이 다시
# 잡히면 **두 침입이 한 파일로 이어져** 화면에 1건으로 보였다.
#
# 로봇이 "끝났다"고 말해 준다 — `/libi/perception_role` 이 `security` 에서 빠진다
# (`IntruderChase._release` → `follow_stop` → `follow_node._publish_camera`).
# 시계를 두 프로세스에 복제하지 않고 그 말을 듣는다.

def test_추격이_끝나면_postroll_을_안_기다리고_닫힌다(rec):
    r, clock, sink = rec
    _see(r, clock, 1.5)
    assert len(sink.opened) == 1 and not sink.closed      # 녹화 중

    _empty(r, clock, 2.0)                                 # postroll(20초) 한참 전
    assert not sink.closed, "아직 시계로는 안 닫힌다 — 전제 확인"

    r.end_chase()
    assert len(sink.closed) == 1
    assert sink.attached and sink.attached[0][0] == sink.opened[0]


def test_유휴에서_end_chase_는_쿨다운을_밀지_않는다(rec):
    """⚠️ 되돌림 주의: `end_chase` 의 녹화중 가드를 빼면 여기가 빨개진다.

    유휴 상태에서 `_stop()` 이 불리면 쿨다운(30초)이 매번 새로 걸려 **새 클립이
    영영 안 열린다.** 호출자가 매 프레임 부르는 구조라 실제로 그렇게 된다.
    """
    r, clock, sink = rec
    for _ in range(50):
        r.end_chase()
        clock.t += 0.1
    _see(r, clock, 1.5)
    assert len(sink.opened) == 1, "쿨다운이 밀려 트리거가 막혔다"


def test_추격_종료는_등록도_해제한다(tmp_path):
    clock, sink = Clock(), FakeSink()
    released = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, params=SecurityParams(fps=10),
                         reset_fn=lambda: released.append(True))
    r.arm(True)
    _see(r, clock, 1.5)
    r.end_chase()
    assert released == [True]


def test_상한_종료_뒤_추격이_끝나면_등록만_해제한다(tmp_path):
    """파일은 이미 닫혔다 — `_stop` 을 또 부르면 닫힌 sink 를 다시 닫는다."""
    clock, sink = Clock(), FakeSink()
    released = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, reset_fn=lambda: released.append(True),
                         params=SecurityParams(fps=10, max_clip_sec=3.0))
    r.arm(True)
    _see(r, clock, 5.0)                       # 상한 초과 — 사람은 아직 보인다
    assert len(sink.closed) == 1 and released == []

    r.end_chase()
    assert len(sink.closed) == 1, "닫힌 sink 를 또 닫으면 안 된다"
    assert released == [True]


# ── 야간 순찰일 때만 녹화 (2026-08-07) ──────────────────────────────────────
#
# 무장은 관제 운영 모드(day/night) 하나로만 정해졌다. 그건 "지금이 밤이다"이지
# **"이 로봇이 순찰 중이다"가 아니다.** 밤에 충전 중이든 배달을 돌든 사서가 패널로
# 추종을 걸든 무장이 그대로라, 사람만 보이면 클립이 열렸다("자꾸 녹화하네").
#
# `/libi/fsm_state` 의 `current_state == "SECURITY_PATROL"` 을 AND 로 묶는다.

def test_순찰이_아니면_야간이어도_녹화하지_않는다(rec):
    r, clock, sink = rec
    r.set_patrol(False)
    _see(r, clock, 3.0)
    assert not sink.opened


def test_순찰로_들어오면_그때부터_녹화한다(rec):
    r, clock, sink = rec
    r.set_patrol(False)
    _see(r, clock, 3.0)
    assert not sink.opened

    r.set_patrol(True)
    _see(r, clock, 1.5)
    assert len(sink.opened) == 1


def test_순찰에서_빠지면_진행_중_클립을_닫는다(rec):
    """기존 「주간」 해제 경로를 그대로 탄다 — 새 상태기계를 안 만든 이유다."""
    r, clock, sink = rec
    _see(r, clock, 1.5)
    assert len(sink.opened) == 1 and not sink.closed

    r.set_patrol(False)
    r.feed(b"J", 0.0)                      # 전이는 tick 안에서만 난다
    assert len(sink.closed) == 1
    assert sink.attached and sink.attached[0][0] == sink.opened[0]


def test_순찰_이탈은_등록도_해제한다(tmp_path):
    clock, sink = Clock(), FakeSink()
    released = []
    r = SecurityRecorder(robot_name="pinky-3", media_dir=tmp_path, sink=sink,
                         now_fn=clock, params=SecurityParams(fps=10),
                         reset_fn=lambda: released.append(True))
    r.arm(True)
    _see(r, clock, 1.5)
    r.set_patrol(False)
    r.feed(b"J", 0.0)
    assert released == [True]


def test_상태를_모르면_예전대로_야간_모드만_본다(rec):
    """⚠️ 기본값이 True 인 이유. ROS 옵트인이 없는 배포에서 야간 녹화가 조용히
    사라지면 안 된다 — `set_patrol` 을 아무도 안 불러도 돌아야 한다."""
    r, clock, sink = rec
    _see(r, clock, 1.5)
    assert len(sink.opened) == 1


def test_주간이면_순찰_중이어도_녹화하지_않는다(rec):
    """운영 모드는 사람이 쥔 차단기다 — AND 라 한쪽만 꺼도 안 돈다."""
    r, clock, sink = rec
    r.arm(False)
    r.set_patrol(True)
    _see(r, clock, 3.0)
    assert not sink.opened


def test_wants_armed_는_둘_다_참일_때만_참이다(rec):
    r, _clock, _sink = rec
    assert r.wants_armed is True
    r.set_patrol(False)
    assert r.wants_armed is False
    r.arm(False); r.set_patrol(True)
    assert r.wants_armed is False
