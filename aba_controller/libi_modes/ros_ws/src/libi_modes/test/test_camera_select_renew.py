"""앞캠 유지 잎 — PersonBlockGuard 를 뺀 자리를 메운다.

이게 없으면 camera_select 가 만료돼 프레임이 아예 안 오고, 야간 기능 전체가
에러 한 줄 없이 죽는다.
"""
from py_trees.common import Status

from libi_modes.common.intruder_chase import CameraSelectRenew


class Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t


def test_첫_tick_에_앞캠을_요청한다():
    calls = []
    leaf = CameraSelectRenew(camera_driver=calls.append, now_fn=Clock())
    leaf.setup()
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING
    assert calls == ["front"]


def test_짧은_간격에는_다시_안_보낸다():
    calls = []
    clock = Clock()
    leaf = CameraSelectRenew(camera_driver=calls.append, renew_sec=2.0, now_fn=clock)
    leaf.setup()
    leaf.tick_once()
    clock.t += 1.0
    leaf.tick_once()
    assert calls == ["front"]


def test_갱신_주기가_지나면_다시_보낸다():
    """값이 안 바뀌어도 주기 재발행해야 한다 — 송출기 쪽에서 만료된다."""
    calls = []
    clock = Clock()
    leaf = CameraSelectRenew(camera_driver=calls.append, renew_sec=2.0, now_fn=clock)
    leaf.setup()
    leaf.tick_once()
    clock.t += 2.5
    leaf.tick_once()
    assert calls == ["front", "front"]


def test_드라이버가_없어도_죽지_않는다():
    leaf = CameraSelectRenew(camera_driver=None, now_fn=Clock())
    leaf.setup()
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING


def test_브랜치를_다시_들어오면_즉시_다시_보낸다():
    calls = []
    clock = Clock()
    leaf = CameraSelectRenew(camera_driver=calls.append, renew_sec=2.0, now_fn=clock)
    leaf.setup()
    leaf.tick_once()
    leaf.initialise()
    leaf.tick_once()
    assert calls == ["front", "front"]
