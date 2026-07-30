"""통행 금지 마스크 정책. ROS·nav2 없이 규칙만 본다."""
import math

from libi_perception.keepout_mask import DRIVE, HALT, MASK, KeepoutPolicy

POSE = {"x": 0.0, "y": 0.0, "yaw": 0.0}


def _p(**over):
    kw = dict(near_area_max=5000, wait_sec=10, ttl_sec=20,
              fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    kw.update(over)
    return KeepoutPolicy(**kw)


# ── 기본 OFF ────────────────────────────────────────────────────────────────

def test_disabled_when_threshold_is_zero():
    """실측 전에는 꺼져 있어야 한다. 켜고 끄기가 쉬워야 문제가 났을 때 되돌린다."""
    p = _p(near_area_max=0)
    assert p.enabled is False
    assert p.update(area=999999, pose=POSE, now=0)[0] == DRIVE


# ── 정지 → 대기 → 마스크 ────────────────────────────────────────────────────

def test_far_obstacle_drives():
    assert _p().update(area=100, pose=POSE, now=0)[0] == DRIVE


def test_near_obstacle_halts_immediately():
    assert _p().update(area=9000, pose=POSE, now=0)[0] == HALT


def test_mask_only_after_the_wait():
    p = _p()
    p.update(area=9000, pose=POSE, now=0)
    assert p.update(area=9000, pose=POSE, now=5)[0] == HALT
    assert p.update(area=9000, pose=POSE, now=11)[0] == MASK


def test_passerby_never_creates_a_mask():
    """지나가는 사람 때문에 지도가 더러워지면 안 된다."""
    p = _p()
    p.update(area=9000, pose=POSE, now=0)
    p.update(area=100, pose=POSE, now=3)          # 비켰다 — 타이머 되돌림
    assert p.update(area=9000, pose=POSE, now=11)[0] == HALT


# ── 마스크 모양 ─────────────────────────────────────────────────────────────

def test_mask_excludes_the_robot_footprint():
    """로봇이 마스크 안에 갇히면 컨트롤러가 탈출 궤적까지 막는다."""
    _, mask = _p(wait_sec=0, footprint_radius=0.10).update(area=9000, pose=POSE, now=1)
    assert mask.contains(0.0, 0.0) is False
    assert mask.contains(0.05, 0.0) is False       # footprint 반경 안


def test_mask_covers_straight_ahead():
    _, mask = _p(wait_sec=0).update(area=9000, pose=POSE, now=1)
    assert mask.contains(0.3, 0.0) is True


def test_mask_does_not_cover_behind():
    _, mask = _p(wait_sec=0).update(area=9000, pose=POSE, now=1)
    assert mask.contains(-0.3, 0.0) is False


def test_mask_respects_the_fan_angle():
    _, mask = _p(wait_sec=0, fan_deg=60).update(area=9000, pose=POSE, now=1)
    assert mask.contains(0.3 * math.cos(math.radians(20)),
                         0.3 * math.sin(math.radians(20))) is True
    assert mask.contains(0.3 * math.cos(math.radians(50)),
                         0.3 * math.sin(math.radians(50))) is False


def test_mask_respects_range():
    _, mask = _p(wait_sec=0, fan_range_m=0.5).update(area=9000, pose=POSE, now=1)
    assert mask.contains(0.9, 0.0) is False


def test_mask_follows_robot_heading():
    _, mask = _p(wait_sec=0).update(
        area=9000, pose={"x": 0.0, "y": 0.0, "yaw": math.pi}, now=1)
    assert mask.contains(-0.3, 0.0) is True
    assert mask.contains(0.3, 0.0) is False


# ── 수명 ────────────────────────────────────────────────────────────────────

def test_mask_expires():
    """만료가 없으면 지도가 점점 막혀 결국 아무 데도 못 간다."""
    p = _p(wait_sec=0, ttl_sec=20)
    p.update(area=9000, pose=POSE, now=1)
    assert p.active_mask(now=10) is not None
    assert p.active_mask(now=25) is None


def test_expired_mask_is_dropped_from_state():
    p = _p(wait_sec=0, ttl_sec=5)
    p.update(area=9000, pose=POSE, now=1)
    p.active_mask(now=100)
    assert p.active_mask(now=101) is None


def test_mask_survives_while_obstacle_clears():
    """비켜도 마스크는 수명까지 남는다 — 만료 즉시 원래 사람에게 다시 다가가지 않게."""
    p = _p(wait_sec=0, ttl_sec=20)
    p.update(area=9000, pose=POSE, now=1)
    _, mask = p.update(area=100, pose=POSE, now=5)
    assert mask is not None


def test_pose_none_does_not_crash():
    _, mask = _p(wait_sec=0).update(area=9000, pose=None, now=1)
    assert mask is not None
