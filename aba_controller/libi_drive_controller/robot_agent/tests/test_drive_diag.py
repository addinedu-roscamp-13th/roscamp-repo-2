"""왜 바퀴가 안 도는가 — 판정.

`nav_phase` 가 목표 쪽 원인을 갈랐고, 여기는 **twist_mux 쪽 원인**을 가른다.
둘을 섞으면 "명령을 줬는데 안 움직인다"가 다시 하나로 뭉개진다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.drive_diag import (  # noqa: E402
    LOCK_PRIORITY, MUX_INPUTS, MUX_TIMEOUT_SEC, blocked_reason, snapshot, winner,
)


def test_우선순위가_높은_입력이_이긴다():
    assert winner(["navigation", "dock"]) == "dock"
    assert winner(["navigation"]) == "navigation"
    assert winner([]) is None


def test_잠금이_자율제어를_막는다():
    """이게 이 파일의 존재 이유 — nav2 는 정상인데 /cmd_vel 만 침묵하던 경우."""
    assert blocked_reason(["navigation"], motion_lock=True) == "motion_lock"
    assert blocked_reason(["navigation"], motion_lock=False) is None


def test_잠금은_비상정지를_안_막는다():
    """잠긴 상태에서도 정지는 언제나 통해야 한다(twist_mux 의 stop=255 > lock=150)."""
    assert blocked_reason(["stop"], motion_lock=True, subject="stop") is None


def test_더_높은_입력에_밀리면_이름을_말한다():
    assert blocked_reason(["navigation", "dock"], motion_lock=False) == "outranked:dock"
    assert blocked_reason(["navigation", "follow"], motion_lock=False) == "outranked:follow"


def test_아무도_안_내면_막힌_게_아니다():
    """낸 사람이 없는 것은 **목표 쪽 문제**다 — nav_phase 가 답한다. 여기서 섞지 않는다."""
    assert blocked_reason([], motion_lock=False) == "no_input"
    assert blocked_reason([], motion_lock=True) == "no_input", "잠금보다 무입력이 먼저다"


def test_대상이_안_내면_남이_이겨도_막힘이_아니다():
    """nav2 가 애초에 아무것도 안 내는데 dock 이 도는 것은 정상이다."""
    assert blocked_reason(["dock"], motion_lock=False) is None


def test_신선하지_않은_입력은_무시된다():
    """twist_mux 는 timeout(0.5초) 넘은 입력을 없는 것으로 친다 — 판정도 같아야 한다."""
    s = snapshot({"navigation": MUX_TIMEOUT_SEC + 0.1, "dock": 0.1},
                 motion_lock=False, cmd_vel_age=0.05, cmd_vel_moving_age=0.05)
    assert s["winner"] == "dock"
    assert "navigation" not in s["fresh"]


def test_스냅샷이_이유를_한_필드로_말한다():
    s = snapshot({"navigation": 0.1}, motion_lock=True,
                 cmd_vel_age=0.05, cmd_vel_moving_age=None)
    assert s["blocked"] == "motion_lock"
    assert s["motion_lock"] is True
    assert s["cmd_vel_moving_age"] is None, "한 번도 안 움직였다"


def test_우선순위표가_twist_mux_와_같은_순서다():
    """⚠️ 두 곳에 적힌 값이다. 한쪽만 고치면 화면이 조용히 거짓말을 한다."""
    prios = [p for _n, _t, p in MUX_INPUTS]
    assert prios == sorted(prios, reverse=True), "표가 우선순위 내림차순이 아니다"
    names = {n for n, _t, _p in MUX_INPUTS}
    assert names == {"stop", "hold", "dock", "follow", "recovery", "navigation"}
    # 잠금(150)은 hold(160)보다 아래, dock(120)보다 위여야 한다 — 자기가 건 잠금에
    # 자기가 막히면 안 되고, 도킹 미세이동은 잠기는 게 맞다.
    by = {n: p for n, _t, p in MUX_INPUTS}
    assert by["hold"] > LOCK_PRIORITY > by["dock"]
