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
    front_deg = cfg.FRONT_ARC_DEG
    back_deg = getattr(cfg, "BACK_ARC_DEG", 0)
    front, front_n = arc(range(-front_deg, front_deg + 1))
    # ⚠️ **전진일 때만 줄인다.** 예전에는 부호를 안 봐서 앞에 벽이 있으면 **후진
    #    속도까지 반감**됐다(front 0.10 / MIN_DIST 0.20 → 계수 0.5). 앞이 막혀서
    #    빠져나오려는 참에 탈출 속도를 깎는 셈이라 정확히 반대로 걸렸다.
    #    원본 `lidar_avoid.avoid_cmd` 도 `linear_x > 0.0` 으로 가른다.
    if linear_x > 0 and front < cfg.MIN_DIST:
        linear_x *= max(0.0, front / cfg.MIN_DIST)
    # 비례 감속만으로는 안 선다 — 계수가 0 이 되는 건 거리 0 에서다.
    # 그 아래에 끊는 선을 둔다. **전진만** 막는다: 후진까지 막으면 너무 붙었을 때
    # 빠져나올 수단이 사라진다.
    # ⚠️ [2026-08-02] **`<` 가 아니라 `<=` 다.** 예전엔 임계와 **정확히 같은** 거리에서
    #    비례 감속만 남아 `LINEAR_X_MAX × front/MIN_DIST` 만큼(0.09m 에서 0.027 m/s)
    #    계속 기어갔다. 사용자 요구는 "9cm 인식되면 **아예 못 가게**" 다. 경계를
    #    포함시켜야 그 문장이 성립한다(codex 지적 2026-08-02).
    stop_dist = getattr(cfg, "STOP_DIST", 0.0)
    if stop_dist > 0 and front <= stop_dist and linear_x > 0:
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
    #
    # ⚠️ [2026-08-02] **후방 임계를 앞과 분리했다**(`BACK_STOP_DIST`). 사용자가 앞 9cm ·
    #    뒤 8cm 로 따로 지정했다. 값이 없으면 예전처럼 `STOP_DIST` 를 그대로 쓴다 —
    #    그래야 이 키를 모르는 옛 설정도 동작이 안 바뀐다.
    back_stop = getattr(cfg, "BACK_STOP_DIST", None)
    if back_stop is None:
        back_stop = stop_dist
    if back_deg > 0 and linear_x < 0:
        back, back_n = arc(range(180 - back_deg, 180 + back_deg + 1))
        if back_stop > 0 and back <= back_stop:      # 경계 포함 — 전방과 같은 규칙
            linear_x = 0.0
        # ⚠️ [2026-08-02] **후방 표본 부족 — 전방에만 있던 검사를 여기도 넣었다.**
        #
        #   `arc` 는 유효 표본이 없으면 10.0(=멀다)을 돌려준다. 그래서 후방이 통째로
        #   안 보여도 "뒤가 뚫렸다"와 구별이 안 됐고, 위 하드 스톱이 **조용히 안
        #   걸렸다.** 유리·검은 옷·라이다 최소거리(5cm) 미만이면 실제로 반사가 사라지는데,
        #   그게 하필 뒤에 바짝 붙었을 때다 — 막아야 할 바로 그 순간에 못 막는다.
        #
        #   여기서 막는 것은 "스캔은 오는데 **뒤만** 안 보이는" 경우다. 스캔이 통째로
        #   없는 경우(위 `if not scan`)는 여전히 후진을 살린다 — 라이다가 죽었다고
        #   탈출 수단까지 뺏으면 앞에 낀 로봇을 못 꺼낸다. 이 경우에도 회전은 살아
        #   있으므로 빠져나갈 길은 남는다.
        back_min = getattr(cfg, "BACK_MIN_SAMPLES", 0)
        if back_min > 0 and back_n < back_min:
            linear_x = 0.0

    # ── 측면 ────────────────────────────────────────────────────────────────
    #
    # ⚠️ [2026-08-02] **좌우를 세 조각(위·옆·아래)의 최솟값으로 잰다.**
    #
    #   참조 구현(`arte_libi_perception/.../lidar_avoid.py` `sectors4`)과 같은 방식이다:
    #     left  = min(front_left, left, back_left)     ← 좌상 · 좌 · 좌하
    #     right = min(front_right, right, back_right)  ← 우상 · 우 · 우하
    #   한 방향을 한 조각으로만 재면 **비스듬히 있는 장애물이 조각 사이로 새어 나간다.**
    #   최솟값을 쓰면 어느 각도로 들어와도 가장 가까운 것이 잡힌다.
    #
    #   전방(±FRONT_ARC_DEG)과 후방(180±BACK_ARC_DEG)을 뺀 나머지가 전부 좌우다 —
    #   360° 에 빈 각도가 없다.
    lo = front_deg + 1
    hi = 180 - back_deg - 1
    left, _ = arc(range(lo, hi + 1))            # 좌상~좌하
    right, _ = arc(range(360 - hi, 360 - lo + 1))   # 우상~우하

    nearest = min(left, right)
    # 왼쪽이 더 가까우면 오른쪽으로(음수), 오른쪽이 더 가까우면 왼쪽으로(양수).
    # 같으면 한쪽을 정해야 한다 — 붙었을 때 0 을 내면 그대로 끼인다.
    away = -1.0 if left <= right else 1.0

    block = getattr(cfg, "SIDE_BLOCK_DIST", 0.0)
    if block > 0 and nearest <= block:
        # ⚠️ **벽에 안 붙는 것이 추종보다 우선이다. 단, 막는 것은 "벽 쪽"뿐이다.**
        #
        #   규칙은 한 문장이다: **벽 쪽으로는 절대 안 돈다. 반대편으로는 얼마든지 돈다.**
        #
        #   [2026-08-02] 처음엔 `angular_z = away * SIDE_DRIFT` 로 통째로 덮어썼는데
        #   그게 틀렸다. 사용자 지적: "왼쪽이 가까울 때 왼쪽으로 못 가는 건 맞다.
        #   그런데 사람이 오른쪽에 있어서 **오른쪽으로 가야 할 때는 갈 수 있어야** 한다."
        #   덮어쓰면 추종이 이미 옳게 벽 반대편으로 0.6 을 요구해도 **0.12 로 깎였다** —
        #   벽에서 빠져나오는 속도를 회피가 스스로 늦춘 셈이다.
        #
        #   그래서 **한쪽 방향으로만 자르는(clamp) 방식**으로 바꾼다:
        #     · 벽 쪽으로 도는 성분은 0 으로 잘린다 → 그쪽으로 도는 일이 없다
        #     · 반대편으로는 최소 `SIDE_DRIFT` 를 보장하되 **상한은 없다** →
        #       추종이 더 세게 돌라고 하면 그대로 통과한다
        #   ⚠️ [2026-08-02 후] **서 있을 때는 회피 회전을 안 낸다.**
        #
        #     `SIDE_DRIFT` 를 무조건 보장했더니 **벽 옆에 가만히 서 있기만 해도 계속
        #     돌았다**(사용자 실측: "반대쪽에 있으면 계속 도니깐"). 정지 중에 도는 것은
        #     회피가 아니라 제자리 뱅뱅이다 — 벽은 로봇이 안 움직이면 안 가까워진다.
        #
        #     그래서 최소값 보장은 **선속도가 있을 때만** 건다. 서 있으면 벽 쪽으로
        #     도는 성분만 잘라 낸다(그건 여전히 막아야 한다 — 제자리 회전으로도
        #     벽에 부딪힐 수 있다).
        drift = away * getattr(cfg, "SIDE_DRIFT", 0.12)
        moving = linear_x != 0.0
        if moving:
            # 움직이는 중 — 반대편으로 최소 `SIDE_DRIFT` 는 보장한다(상한은 없다).
            angular_z = max(angular_z, drift) if away > 0 else min(angular_z, drift)
        else:
            # 서 있는 중 — 벽 쪽으로 도는 성분만 0 으로 자른다. 안 돌면 그대로 0.
            angular_z = max(angular_z, 0.0) if away > 0 else min(angular_z, 0.0)
        #   ⚠️ **전진은 막지 않는다.** 예전엔 `_block_forward` 로 같이 막았는데,
        #      위에서 이미 벽 반대편으로 돌게 만들어 놨으므로 전진은 **벽에서
        #      멀어지는 방향**이다. 막으면 오히려 탈출이 안 된다.
        #      정면으로 다가오는 것은 위의 `STOP_DIST` 가 따로 막는다.
    elif nearest < cfg.AVOID_DIST:
        # 아직 여유가 있는 구간 — 부드럽게 비켜 준다(비례). 벽에서 멀어지면 이 항이
        # 0 으로 수렴해 **원래 추종 각속도로 돌아온다.**
        #
        # ⚠️ [2026-08-01] **양쪽을 더하지 않는다 — 더 가까운 쪽만 본다.**
        #   좁은 복도처럼 양쪽이 다 임계 안이면 두 항이 상쇄돼 회피가 **꺼진다**.
        #   피할 대상은 둘 중 더 가까운 벽 하나이고, 반대쪽 벽은 "그쪽으로 갈 여유가
        #   있다"는 뜻이지 반대 방향으로 밀 근거가 아니다.
        push = (cfg.AVOID_DIST - nearest) * cfg.AVOID_KP
        angular_z = angular_z + away * push

    angular_z = clamp(angular_z, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)
    return linear_x, angular_z
