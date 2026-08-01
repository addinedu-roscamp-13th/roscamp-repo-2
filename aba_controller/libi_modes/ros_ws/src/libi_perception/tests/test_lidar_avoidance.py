from types import SimpleNamespace
from libi_perception.lidar_avoidance import apply_avoidance


def _cfg(**over):
    base = dict(MIN_DIST=0.20, AVOID_DIST=0.40, AVOID_KP=0.50,
                FRONT_ARC_DEG=15, SIDE_ARC=(16, 71), ANGULAR_Z_MAX=0.60,
                FRONT_MIN_SAMPLES=5)
    base.update(over)
    return SimpleNamespace(**base)


def _clear_scan():
    return [10.0] * 360


# ── 라이다를 못 볼 때 (fail-safe) ────────────────────────────────────────────


def test_no_scan_blocks_forward():
    """예전엔 그대로 통과시켰다 — 라이다가 죽으면 회피 없이 전속 전진이었다."""
    lin, ang = apply_avoidance(0.1, 0.2, [], _cfg())
    assert lin == 0.0, "앞을 못 보는데 전진했다"
    assert ang == 0.2, "회전까지 막을 이유는 없다"


def test_no_scan_leaves_reverse_alone():
    """막는 방향은 앞뿐이다. 후진까지 끊으면 붙었을 때 빠져나올 수단이 사라진다."""
    lin, _ = apply_avoidance(-0.05, 0.0, [], _cfg())
    assert lin == -0.05


def test_front_arc_without_returns_blocks_forward():
    """스캔은 오는데 **전방만** 비었다 — 유리·역광이면 실제로 그렇다.

    `arc` 는 표본이 없으면 10.0(=멀다)을 주므로, 개수를 따로 안 세면
    "앞이 뻥 뚫렸다"와 구별되지 않는다.
    """
    scan = _clear_scan()
    for d in range(-15, 16):
        scan[d % 360] = 0.0          # 0.0 = 측정 실패(scan_provider 의 표기)
    lin, _ = apply_avoidance(0.12, 0.0, scan, _cfg())
    assert lin == 0.0


def test_front_coverage_check_off_when_zero():
    lin, _ = apply_avoidance(0.12, 0.0, [0.0] * 360, _cfg(FRONT_MIN_SAMPLES=0))
    assert lin == 0.12


# ── 아크 경계 ────────────────────────────────────────────────────────────────


def test_no_gap_between_front_and_side_arcs():
    """[2026-07-31] 16~19° 와 341~344° 가 어느 아크에도 안 들어갔다.

    그 방향 장애물은 전방 감속에도 측면 조향에도 안 잡혀 조용히 무시됐다.
    """
    for deg in (16, 19, 341, 344):
        scan = _clear_scan()
        scan[deg] = 0.20
        lin, ang = apply_avoidance(0.12, 0.0, scan, _cfg())
        assert lin != 0.12 or ang != 0.0, f"{deg}° 장애물이 아무 영향도 못 줬다"


def test_clear_path_unchanged():
    lin, ang = apply_avoidance(0.1, 0.2, _clear_scan(), _cfg())
    assert abs(lin - 0.1) < 1e-9
    assert abs(ang - 0.2) < 1e-9


def test_front_obstacle_slows_down():
    scan = _clear_scan()
    scan[0] = 0.10               # 0.10 < MIN_DIST 0.20 -> factor 0.5
    lin, _ = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert abs(lin - 0.05) < 1e-6


def test_left_obstacle_steers_right():
    scan = _clear_scan()
    scan[45] = 0.20              # inside SIDE_ARC (20..71), < AVOID_DIST
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang < 0              # steer right (negative) away from left wall


def test_right_obstacle_steers_left():
    scan = _clear_scan()
    scan[315] = 0.20            # mirror side (360-45), < AVOID_DIST
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang > 0


def test_narrow_corridor_steers_away_from_the_nearer_wall():
    """⚠️ **좁은 길에서 회피가 꺼지던 회귀.**

    예전에는 좌우 기여를 **합산**했다. 복도처럼 양쪽이 다 임계 안이면 두 항이
    상쇄돼 `steer ≈ 0` 이 됐다 — 벽 사이에 끼어 회피가 가장 필요한 순간에 회피가
    사라지는 것이다. 지금은 **더 가까운 쪽 하나만** 보고 그 반대로 민다.
    """
    scan = _clear_scan()
    scan[45] = 0.35             # 왼쪽 벽 (멀다)
    scan[315] = 0.13            # 오른쪽 벽 (가깝다)
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang > 0, f"가까운 오른쪽 벽에서 멀어져야 한다: {ang}"

    # 좌우를 뒤집으면 방향도 뒤집힌다.
    scan = _clear_scan()
    scan[45] = 0.13
    scan[315] = 0.35
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang < 0, f"가까운 왼쪽 벽에서 멀어져야 한다: {ang}"


def test_symmetric_corridor_still_picks_a_side():
    """양쪽이 **똑같이** 가까워도 0 을 내면 안 된다 — 그대로 끼인 채 못 빠져나온다."""
    scan = _clear_scan()
    scan[45] = scan[315] = 0.20
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang != 0.0, "대칭이라고 회피를 포기하면 복도에 갇힌다"


# ── 하드 스톱 ────────────────────────────────────────────────────────────────


def _front_at(dist):
    """전방 아크만 `dist`, 나머지는 멀리."""
    scan = [10.0] * 360
    for d in range(-15, 16):
        scan[d % 360] = dist
    return scan


def test_hard_stop_kills_forward_inside_stop_dist():
    """비례 감속만으로는 안 선다 — 계수가 0 이 되는 건 거리 0 에서다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.25))
    assert lin == 0.0


def test_hard_stop_leaves_reverse_alone():
    """후진까지 **0 으로 끊으면** 너무 붙었을 때 빠져나올 수단이 사라진다.

    ⚠️ 기존 비례 감속은 후진에도 걸린다(전방 장애물 기준으로 부호와 무관하게 곱한다).
    그건 이 변경 이전부터의 동작이라 그대로 둔다 — 여기서 보장하는 건
    "하드 스톱이 후진을 죽이지 않는다" 하나다.
    """
    lin, _ = apply_avoidance(-0.05, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.25))
    assert lin < 0.0, "후진이 살아 있어야 한다"


def test_hard_stop_off_when_zero():
    """STOP_DIST=0 이면 기존 동작(비례 감속만) 그대로다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.0))
    assert lin > 0.0


def test_outside_stop_dist_only_scales():
    """정지선 밖에서는 끊지 않고 줄이기만 한다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.30), _cfg(STOP_DIST=0.25))
    assert lin == 0.12          # MIN_DIST(0.20) 밖이라 감속도 없다


# ── 후방 아크 (BACK_ARC_DEG) ────────────────────────────────────────────────
# 예전에는 전방 ±15° 와 측면 16~70° 만 봐서 **71~289° 가 통째로 사각**이었다.
# 그래서 후진을 막을 근거 자체가 없었다. 라이다는 처음부터 360° 다.

def _back_cfg(**over):
    base = dict(BACK_ARC_DEG=15, STOP_DIST=0.25)
    base.update(over)
    return _cfg(**base)


def test_obstacle_behind_blocks_reverse():
    scan = _clear_scan()
    scan[180] = 0.10                      # 바로 뒤 10cm
    lin, _ = apply_avoidance(-0.05, 0.0, scan, _back_cfg())
    assert lin == 0.0, "뒤에 벽이 있는데 후진했다"


def test_obstacle_behind_does_not_block_forward():
    """뒤에 있는 것 때문에 앞으로 가는 것까지 막으면 빠져나올 수단이 없어진다."""
    scan = _clear_scan()
    scan[180] = 0.10
    lin, _ = apply_avoidance(0.08, 0.0, scan, _back_cfg())
    assert lin == 0.08


def test_obstacle_in_front_still_allows_reverse():
    """반대 방향도 마찬가지 — 앞이 막혔으면 후진이 유일한 탈출구다."""
    scan = _clear_scan()
    scan[0] = 0.10
    lin, _ = apply_avoidance(-0.05, 0.0, scan, _back_cfg())
    assert lin == -0.05


def test_back_arc_covers_both_edges_not_just_dead_astern():
    """정후방 한 점만 보면 비스듬히 뒤에 있는 것을 놓친다."""
    for deg in (180 - 15, 180 + 15):
        scan = _clear_scan()
        scan[deg % 360] = 0.10
        lin, _ = apply_avoidance(-0.05, 0.0, scan, _back_cfg())
        assert lin == 0.0, f"{deg}도의 장애물을 놓쳤다"


def test_back_arc_ignores_what_is_outside_it():
    scan = _clear_scan()
    scan[(180 + 40) % 360] = 0.10         # 아크 밖
    lin, _ = apply_avoidance(-0.05, 0.0, scan, _back_cfg())
    assert lin == -0.05


def test_back_arc_off_restores_the_old_behaviour():
    """0 이면 끈다 — 예전(후방 무감시) 동작으로 정확히 돌아가야 한다."""
    scan = _clear_scan()
    scan[180] = 0.05
    lin, _ = apply_avoidance(-0.05, 0.0, scan, _back_cfg(BACK_ARC_DEG=0))
    assert lin == -0.05


def test_missing_scan_still_only_blocks_forward():
    """스캔이 **없는 것**과 뒤가 **보이는데 가까운 것**은 다르다.

    없을 때까지 후진을 막으면, 앞에 붙어 있는데 빠져나올 방법이 사라진다.
    """
    lin, _ = apply_avoidance(-0.05, 0.0, [], _back_cfg())
    assert lin == -0.05


def test_shipped_config_watches_the_rear():
    from libi_perception import config
    assert config.BACK_ARC_DEG > 0 and config.STOP_DIST > 0
