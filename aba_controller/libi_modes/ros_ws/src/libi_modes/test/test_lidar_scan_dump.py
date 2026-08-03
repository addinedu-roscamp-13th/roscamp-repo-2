"""후방 스캔 덤프의 순수 변환부.

실기에 나가기 전에 확인해야 하는 것은 하나다: **0도가 로봇의 뒤이고, 각도가 도 단위로
오름차순이며, 못 잰 광선이 조용히 사라지지 않는가.** 셋 중 하나만 틀려도 덤프를 보고
"노치가 없다"는 잘못된 결론을 내리게 된다.
"""
import math
import sys
from pathlib import Path

# scripts/ 는 파이썬 패키지가 아니라 실행 스크립트 디렉토리다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scan_dump  # noqa: E402


def _ranges(n=500, value=1.0):
    return [value] * n


def test_rows_cover_only_the_requested_sector():
    n = 500
    rows = scan_dump.rows_from_scan(
        _ranges(n), angle_min=-math.pi, angle_increment=2 * math.pi / n,
        sector_half_deg=60.0)
    assert rows, "섹터 안 광선이 하나도 안 나왔다"
    assert all(-60.0 <= deg <= 60.0 for deg, _ in rows)


def test_rows_are_sorted_by_angle():
    n = 500
    rows = scan_dump.rows_from_scan(
        _ranges(n), angle_min=-math.pi, angle_increment=2 * math.pi / n,
        sector_half_deg=60.0)
    degs = [deg for deg, _ in rows]
    assert degs == sorted(degs)


def test_zero_degree_is_included():
    """0도 = 로봇의 물리적 뒤. 후진 진행 방향이라 반드시 있어야 한다."""
    n = 500
    rows = scan_dump.rows_from_scan(
        _ranges(n), angle_min=-math.pi, angle_increment=2 * math.pi / n,
        sector_half_deg=60.0)
    assert any(abs(deg) < 1.0 for deg, _ in rows)


def test_unmeasurable_rays_are_kept_as_zero_not_dropped():
    """못 잰 광선을 빼 버리면 덤프에서 '노치가 반사를 안 준다'를 못 본다."""
    n = 500
    ranges = _ranges(n)
    ranges[0] = float("inf")
    ranges[1] = float("nan")
    ranges[2] = -1.0
    rows = scan_dump.rows_from_scan(
        ranges, angle_min=-math.pi, angle_increment=2 * math.pi / n,
        sector_half_deg=180.0)
    assert len(rows) == n
    assert sum(1 for _, dist in rows if dist == 0.0) == 3
