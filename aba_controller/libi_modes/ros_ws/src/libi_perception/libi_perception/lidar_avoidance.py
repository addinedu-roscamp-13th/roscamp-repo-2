from .pid import clamp


def _block_forward(linear_x):
    """전진만 0 으로 만든다. 후진·회전은 그대로.

    앞을 못 보는 상황에서 쓰는 것이라 막는 방향도 앞뿐이다. 후진까지 끊으면 이미
    붙어 있을 때 빠져나올 수단이 사라진다(STOP_DIST 와 같은 규칙).
    """
    return min(linear_x, 0.0)


def apply_avoidance(linear_x, angular_z, scan, cfg):
    """Post-process PID output with LiDAR: front slowdown + side shy-away.

    ## [2026-07-31] 스캔이 없을 때 fail-open 이었다

    예전에는 `if not scan: return linear_x, angular_z` 로 **그대로 통과**시켰다.
    라이다가 아직 안 올라왔거나 죽으면 회피가 통째로 꺼진 채 PID 가 요구하는 속도가
    그대로 바퀴로 갔고, 그 사실이 로그에도 화면에도 안 나타났다.
    지금은 전진만 막는다 — 앞을 못 보면 앞으로 가지 않는다.
    """
    if not scan:
        # 스캔 자체가 없다. `ScanProvider.get()` 은 첫 스캔 전과 **stale 판정** 때
        # 빈 리스트를 준다(그쪽에서 경고를 찍는다).
        return _block_forward(linear_x), angular_z
    n = len(scan)
    step = n / 360.0

    def arc(deg_iter):
        """(그 아크의 최소 거리, 유효 표본 수). 표본이 없으면 (10.0, 0)."""
        idx = [int(i * step) % n for i in deg_iter]
        vals = [scan[i] for i in idx if scan[i] and scan[i] > 0.05]
        return (min(vals) if vals else 10.0), len(vals)

    # front arc: proportional slowdown, then a hard stop
    front, front_n = arc(range(-cfg.FRONT_ARC_DEG, cfg.FRONT_ARC_DEG + 1))
    # ⚠️ **전진일 때만 줄인다.** 예전에는 부호를 안 봐서 앞에 벽이 있으면 **후진
    #    속도까지 반감**됐다(front 0.10 / MIN_DIST 0.20 → 계수 0.5). 앞이 막혀서
    #    빠져나오려는 참에 탈출 속도를 깎는 셈이라 정확히 반대로 걸렸다.
    #    원본 `lidar_avoid.avoid_cmd` 도 `linear_x > 0.0` 으로 가른다.
    if linear_x > 0 and front < cfg.MIN_DIST:
        linear_x *= max(0.0, front / cfg.MIN_DIST)
    # 비례 감속만으로는 안 선다 — 계수가 0 이 되는 건 거리 0 에서다.
    # 그 아래에 끊는 선을 둔다. **전진만** 막는다: 후진까지 막으면 너무 붙었을 때
    # 빠져나올 수단이 사라진다.
    stop_dist = getattr(cfg, "STOP_DIST", 0.0)
    if stop_dist > 0 and front < stop_dist and linear_x > 0:
        linear_x = 0.0
    # 스캔은 왔는데 **전방만 비어 있는** 경우. 위 `arc` 는 표본이 없으면 10.0(=멀다)을
    # 돌려주므로, 개수를 따로 안 보면 "앞이 뻥 뚫렸다"와 구별되지 않는다.
    # 유리·거울·역광처럼 특정 방향만 리턴이 사라지는 것은 실제로 흔하다.
    min_samples = getattr(cfg, "FRONT_MIN_SAMPLES", 0)
    if min_samples > 0 and front_n < min_samples:
        linear_x = _block_forward(linear_x)

    # back arc: 후진에도 같은 하드 스톱을 건다.
    #
    # ⚠️ 예전에는 후방을 **아무도 안 봤다** — 전방 ±15° 와 측면 16~70° 뿐이라
    #    71~289° 가 통째로 사각이었다. 그래서 "후진은 살려 둔다"가 정책이 아니라
    #    그냥 막을 근거가 없었던 것이다. 라이다는 처음부터 360° 이고, 서버 쪽
    #    `lidar_avoid.sectors8` 은 8방향을 이미 다 계산해 패널에 보여주고 있었다.
    #
    # ⚠️ 스캔이 아예 없을 때(위 `if not scan`)는 여전히 전진만 막는다. 못 보는
    #    것과 **보이는데 가까운 것**은 다르다 — 앞에 붙어 있을 때 빠져나올
    #    수단을 없애면 안 된다.
    back_deg = getattr(cfg, "BACK_ARC_DEG", 0)
    if back_deg > 0 and stop_dist > 0 and linear_x < 0:
        back, _ = arc(range(180 - back_deg, 180 + back_deg + 1))
        if back < stop_dist:
            linear_x = 0.0

    # side arcs: shy away
    lo, hi = cfg.SIDE_ARC
    left, _ = arc(range(lo, hi))
    right, _ = arc(range(360 - hi + 1, 360 - lo + 1))
    steer = 0.0
    if left < cfg.AVOID_DIST:
        steer -= (cfg.AVOID_DIST - left) * cfg.AVOID_KP    # wall on left -> steer right
    if right < cfg.AVOID_DIST:
        steer += (cfg.AVOID_DIST - right) * cfg.AVOID_KP   # wall on right -> steer left
    angular_z = clamp(angular_z + steer, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

    return linear_x, angular_z
