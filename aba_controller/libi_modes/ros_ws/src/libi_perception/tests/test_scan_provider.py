"""ScanProvider 의 게으른 변환 — 추종을 안 할 때 정말 공짜인가.

[2026-07-30] 실측에서 순회만 도는데 `follow_node` 가 코어의 14% 를 먹었다. 원인은
`_cb` 가 스캔마다 `to_degree_indexed`(광선 전부를 파이썬 루프로 도는 함수)를 즉시
돌린 것이었다. 소비자는 세션이 있을 때만 존재하는데도.

여기서 지키는 계약은 하나다: **`get()` 을 안 부르면 변환이 한 번도 안 일어난다.**
성능 최적화는 "안 도는 것"을 세지 않으면 조용히 되돌아간다 — 그래서 횟수를 센다.
"""
import math
from types import SimpleNamespace

import libi_perception.scan_provider as sp
from libi_perception.scan_provider import ScanProvider, to_degree_indexed


class _FakeNode:
    """create_subscription 만 받아 콜백을 붙잡아 둔다."""

    def __init__(self):
        self.cb = None

    def create_subscription(self, msg_type, topic, cb, qos):
        self.cb = cb
        return SimpleNamespace()


def _scan(n=720, dist=1.0):
    """angle_min=-pi 인 실제 sllidar 배치 그대로."""
    return SimpleNamespace(ranges=[dist] * n,
                           angle_min=-math.pi,
                           angle_increment=2 * math.pi / n)


def _counting(monkeypatch):
    """`to_degree_indexed` 호출 횟수를 센다."""
    calls = []
    real = sp.to_degree_indexed

    def spy(msg):
        calls.append(1)
        return real(msg)

    monkeypatch.setattr(sp, "to_degree_indexed", spy)
    return calls


def test_no_consumer_means_no_conversion(monkeypatch):
    """세션이 없으면(=get 을 아무도 안 부르면) 변환은 0회다. 이게 14% 를 없앤 계약이다."""
    calls = _counting(monkeypatch)
    node = _FakeNode()
    ScanProvider(node, "/scan")
    for _ in range(50):
        node.cb(_scan())
    assert calls == [], f"소비자가 없는데 {len(calls)}번 변환했다"


def test_conversion_is_cached_per_message(monkeypatch):
    """제어 루프(20Hz)가 스캔(10Hz)보다 자주 물어도 변환은 메시지당 1회다."""
    calls = _counting(monkeypatch)
    node = _FakeNode()
    p = ScanProvider(node, "/scan")

    node.cb(_scan())
    for _ in range(5):
        p.get()
    assert len(calls) == 1, f"같은 스캔을 {len(calls)}번 변환했다"

    node.cb(_scan())            # 새 스캔 → 캐시 무효
    p.get()
    assert len(calls) == 2


def test_get_before_any_scan_is_empty():
    """스캔이 오기 전에는 빈 리스트. None 을 돌려주면 소비자가 터진다."""
    node = _FakeNode()
    p = ScanProvider(node, "/scan")
    assert p.get() == []


def test_value_matches_eager_conversion():
    """게을러졌어도 **값은 예전과 같아야** 한다 — 회피 판단이 바뀌면 안 된다."""
    node = _FakeNode()
    p = ScanProvider(node, "/scan")
    msg = _scan(n=720, dist=1.5)
    node.cb(msg)
    assert p.get() == to_degree_indexed(msg)


# ── 신선도 ───────────────────────────────────────────────────────────────────
#
# [2026-07-31] 예전에는 마지막으로 받은 스캔을 **영원히** 최신처럼 돌려줬다.
# 라이다가 멈춰도 소비자는 알 방법이 없어 몇 분 전 그림으로 회피를 판단했다.


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_stale_scan_reads_as_no_scan():
    clock = _Clock()
    node = _FakeNode()
    p = ScanProvider(node, "/scan", max_age=1.0, now=clock)
    node.cb(_scan())
    assert p.get(), "방금 받은 스캔인데 비었다"

    clock.t = 1.5                      # max_age 를 넘겼다
    assert p.get() == [], "오래된 스캔을 최신처럼 돌려줬다"


def test_fresh_scan_clears_the_stale_state():
    """라이다가 돌아오면 다시 쓴다 — 한 번 stale 이었다고 영영 막으면 안 된다."""
    clock = _Clock()
    node = _FakeNode()
    p = ScanProvider(node, "/scan", max_age=1.0, now=clock)
    node.cb(_scan())
    clock.t = 1.5
    assert p.get() == []

    clock.t = 1.6
    node.cb(_scan())                   # 새 스캔 도착
    assert p.get(), "스캔이 돌아왔는데 계속 막고 있다"


def test_max_age_zero_disables_the_check():
    """라이다 없이 굴리는 구성을 위해 끌 수 있어야 한다(config 주석)."""
    clock = _Clock()
    node = _FakeNode()
    p = ScanProvider(node, "/scan", max_age=0.0, now=clock)
    node.cb(_scan())
    clock.t = 10_000.0
    assert p.get(), "검사가 꺼져 있는데 비웠다"
