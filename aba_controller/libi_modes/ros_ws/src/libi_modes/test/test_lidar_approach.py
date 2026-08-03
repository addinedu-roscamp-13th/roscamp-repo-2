"""라이다 도킹 국면 상태기계.

여기서 잡히는 것들은 실물에서만 드러나는 종류가 아니다:

  · 부호를 잃으면 후진이어야 할 것이 **전진**이 된다 — 도크를 들이받는다
  · 벽이 사라진 뒤에도 yaw 를 계속 믿으면 죽은 값으로 조향한다
  · 펄스가 수렴하지 않으면 목표를 지나쳐 계속 민다
"""
import pytest

from libi_modes.lidar.approach import Cmd, LidarApproach
from libi_modes.lidar.config import LidarDockConfig
from libi_modes.lidar.detect import NotchObs


def obs(d=0.40, y=0.0, yaw=0.0, depth=0.025, wall_m=None):
    return NotchObs(d=d, y=y, yaw=yaw, depth=depth,
                    wall_m=(d - depth) if wall_m is None else wall_m)


def near_obs(d=0.06, y=0.0):
    """NEAR 검출기가 낸 관측 — yaw·depth·wall_m 은 못 잰다(`near=True` 일 때 nan)."""
    return NotchObs(d=d, y=y, yaw=0.0, depth=float("nan"), wall_m=float("nan"), near=True)


def run(machine, frames, t0=0.0, dt=0.1):
    """`frames` 를 순서대로 먹인다. 마지막 Cmd 를 돌려준다."""
    cmd = None
    t = t0
    for frame in frames:
        cmd = machine.step(frame, t)
        t += dt
    return cmd


def raw_cfg(**kw):
    """필터를 끈 설정 — **국면 로직만** 시험하려고.

    EMA(0.3)와 튐 게이트(d 5cm)를 켜 두면 `d` 를 0.30 → 0.09 로 한 번에 바꾸는 시험이
    게이트에 걸려 그 프레임을 '상실'로 센다. 그러면 국면 전이가 아니라 필터를 시험하는
    꼴이 된다. 필터는 아래 전용 시험에서 따로 본다.
    """
    kw.setdefault("ema_coef", 1.0)
    kw.setdefault("jump_speed_max_mps", 10.0)
    kw.setdefault("jump_gate_y_m", 10.0)
    kw.setdefault("jump_gate_yaw_rad", 10.0)
    return LidarDockConfig(**kw)


def settled(cfg=None):
    """SETTLE 을 통과시킨 상태기계를 돌려준다."""
    cfg = cfg or LidarDockConfig()
    m = LidarApproach(cfg)
    run(m, [None] * int(cfg.settle_sec / 0.1 + 2))
    return m


def ready(cfg=None, d=0.50):
    """SETTLE·ACQUIRE 를 통과시켜 주행 국면에 들어간 상태기계를 돌려준다."""
    cfg = cfg or raw_cfg()
    m = settled(cfg)
    run(m, [obs(d=d)] * cfg.confirm_frames, t0=2.0)
    return m


def test_settle_publishes_zero_and_does_not_move():
    m = LidarApproach(LidarDockConfig())
    cmd = m.step(obs(), 0.0)
    assert cmd.phase == "SETTLE"
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_acquire_needs_consecutive_confirmations():
    cfg = LidarDockConfig(confirm_frames=3)
    m = settled(cfg)
    for _ in range(2):
        cmd = m.step(obs(), 2.0)
        assert cmd.phase == "ACQUIRE"
    cmd = m.step(obs(), 2.1)
    assert cmd.phase == "ALIGN"


def test_acquire_aborts_without_rotating_when_nothing_is_found():
    """라이다는 360도를 본다. 못 찾는 이유가 시야가 아니므로 스윕하지 않는다.

    `t0` 은 `cfg.settle_sec` 에 여유를 더해 잡는다 — 고정값(예: 2.0)을 쓰면
    `settle_sec` 기본값이 바뀔 때(1.0 → 3.0) `settled()` 가 이미 SETTLE 을 지나
    ACQUIRE 로 넘어간 시각보다 이 `t0` 가 더 과거가 되어, 20 프레임을 다 먹여도
    `acquire_timeout_s` 문턱을 못 넘겨 이 시험이 거짓으로 통과(또는 실패)한다.
    """
    cfg = LidarDockConfig(acquire_timeout_s=1.0)
    m = settled(cfg)
    cmd = run(m, [None] * 20, t0=cfg.settle_sec + 2.0)
    assert cmd.phase == "ABORT"
    assert cmd.done is True
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_near_observations_never_confirm_or_advance_out_of_acquire():
    """NEAR 에는 오검출 방어(직선 피팅)가 없다 — 실측 노치 없는 벽에서도 100% '검출'한다.
    ACQUIRE 가 NEAR 로 확정되면 오검출로 도킹이 **시작**된다. `confirm_frames` 를
    훌쩍 넘겨 NEAR 를 먹여도 ACQUIRE 를 벗어나면 안 되고, 결국 확정 실패로 ABORT 해야
    한다 — ALIGN·APPROACH·FINAL 어느 쪽으로도 넘어가지 않는다."""
    cfg = LidarDockConfig(acquire_timeout_s=1.0, confirm_frames=3)
    m = settled(cfg)
    t = 2.0
    last = None
    for _ in range(30):        # confirm_frames(3) 를 훌쩍 넘긴다
        last = m.step(near_obs(), t)
        assert last.phase in ("ACQUIRE", "ABORT"), (
            f"NEAR 관측으로 ACQUIRE 를 벗어났다: {last.phase}")
        if last.phase == "ABORT":
            break
        t += 0.1
    assert last.phase == "ABORT" and last.done is True


def test_never_commands_forward_motion():
    """후진 도킹에서 전진 명령은 언제나 버그다. 어떤 입력에도 나오면 안 된다."""
    cfg = LidarDockConfig()
    m = LidarApproach(cfg)
    t = 0.0
    for d in [2.0, 1.0, 0.5, 0.3, 0.2, 0.12, 0.08, 0.07, 0.066, 0.064, 0.05, 0.0]:
        for y in (-0.2, 0.0, 0.2):
            for yaw in (-0.5, 0.0, 0.5):
                cmd = m.step(obs(d=d, y=y, yaw=yaw), t)
                assert cmd.linear <= 0.0, f"전진 명령 {cmd.linear} (d={d})"
                t += 0.1


def test_never_commands_forward_motion_while_actually_driving():
    """`test_never_commands_forward_motion` 을 되짚어 보면 실제로는 ACQUIRE 안에서만
    돈다 — 기본 게이트(`jump_gate_y_m`, `jump_gate_yaw_rad`)가 그 시험의 요동치는
    y·yaw 를 매 프레임 걷어내 3연속 확정에 못 미치고 `acquire_timeout_s` 로
    ABORT 해 버린다. 그러면 부호가 실리는 "driving" 반환문을 한 번도 안 거치므로
    `Cmd(-lin_mag, ...)` 를 `Cmd(lin_mag, ...)` 로 되돌려도 그 시험은 초록으로 남는다
    (실측: 되돌리고 돌려서 확인함).

    여기서는 게이트를 끈 `raw_cfg()` 로 ACQUIRE 를 확실히 통과시킨 뒤 같은 격자를
    먹인다 — 그래야 실제로 후진 중에 부호가 실리는 프레임을 지나가고, 부호가
    뒤집히면 이 시험이 진짜로 빨개진다.
    """
    cfg = raw_cfg()
    m = ready(cfg, d=2.0)
    t = 3.0
    for d in [2.0, 1.0, 0.5, 0.3, 0.2, 0.12, 0.08, 0.07, 0.066, 0.064, 0.05, 0.0]:
        for y in (-0.2, 0.0, 0.2):
            for yaw in (-0.5, 0.0, 0.5):
                cmd = m.step(obs(d=d, y=y, yaw=yaw), t)
                assert cmd.linear <= 0.0, f"전진 명령 {cmd.linear} (d={d})"
                t += 0.1


def test_speed_steps_down_as_it_gets_closer():
    cfg = raw_cfg()
    m = ready(cfg, d=0.50)
    far = m.step(obs(d=0.50), 3.0)
    near = m.step(obs(d=0.20), 3.1)
    assert abs(far.linear) == pytest.approx(cfg.v_far_mps)
    assert abs(near.linear) == pytest.approx(cfg.v_near_mps)
    assert abs(near.linear) < abs(far.linear)


def test_final_phase_ignores_yaw_because_the_wall_is_gone():
    """최종 6~7cm 에서 벽면은 4cm — C1 min range 5cm 아래라 점이 사라진다.
    마지막 yaw 를 얼려서 쓰지 않는다. 죽은 값을 믿는 건 안 보는 것보다 나쁘다."""
    m = ready(raw_cfg(), d=0.30)
    cmd = m.step(obs(d=0.09, y=0.0, yaw=0.5, wall_m=0.065), 5.0)
    assert cmd.phase == "FINAL"
    assert cmd.angular == 0.0, "yaw 가 커도 최종 국면에서는 조향에 안 쓴다"


def test_align_phase_does_use_yaw():
    """FINAL 의 0 이 의미를 가지려면, 벽이 살아 있을 때는 실제로 써야 한다."""
    m = ready(raw_cfg(), d=0.50)
    cmd = m.step(obs(d=0.50, y=0.0, yaw=0.5), 3.0)
    assert cmd.phase == "ALIGN"
    assert cmd.angular != 0.0


def test_final_aborts_when_lateral_error_is_too_large():
    m = ready(raw_cfg(y_max_final_m=0.03), d=0.30)
    cmd = m.step(obs(d=0.09, y=0.06, wall_m=0.065), 5.0)
    assert cmd.phase == "ABORT" and cmd.done is True


def test_reaching_stop_distance_finishes():
    cfg = raw_cfg(stop_m=0.065)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.064, wall_m=0.04), 5.0)
    assert cmd.phase == "DONE" and cmd.done is True
    assert cmd.linear == 0.0


def test_overshoot_aborts():
    m = ready(raw_cfg(stop_m=0.065, overshoot_margin_m=0.01), d=0.30)
    cmd = m.step(obs(d=0.050, wall_m=0.03), 5.0)
    assert cmd.phase == "ABORT"


def test_losing_the_notch_for_too_long_aborts():
    m = ready(raw_cfg(lost_frames_max=5), d=0.30)
    cmd = run(m, [None] * 10, t0=3.0)
    assert cmd.phase == "ABORT"


def test_a_jumped_frame_is_rejected_not_steered_on():
    """10Hz 에 5cm 이동은 0.5m/s 다 — 물리적으로 불가능하므로 검출 오류다.
    그 프레임을 그대로 조향에 실으면 로봇이 홱 튼다."""
    cfg = LidarDockConfig(jump_gate_y_m=0.03, ema_coef=1.0)
    m = ready(cfg, d=0.50)
    m.step(obs(d=0.50, y=0.0), 3.0)
    cmd = m.step(obs(d=0.50, y=0.5), 3.1)      # 좌우가 50cm 튀었다
    assert cmd.reason == "lost"
    assert cmd.angular == 0.0


def drive(machine, cfg, d0=0.20, cmd_delay=0.05, spinup=0.03, coast=0.02,
          noise=0.005, seed=0, dt=0.1, steps=3000):
    """**지연을 넣은** 로봇 모형으로 도킹 한 판을 돌린다.

    ⚠️ 지연 없는 모형은 아무것도 검증하지 못한다(codex P0 #4). 초안 시험은 같은 tick 에
       `d += linear*dt` 로 관측을 즉시 갱신해서 오차가 0.0mm 로 나왔다. 실제로는
       스캔 주기·명령 지연·정지마찰·관성이 다 끼어들고, 그것을 넣으면 초안 정지 조건이
       목표를 10~14mm 지나쳤다.
    """
    import random
    rnd = random.Random(seed)
    d_true, t = d0, 0.0
    pending, v_applied = [], 0.0
    cmd = None
    for _ in range(steps):
        meas = d_true + rnd.gauss(0.0, noise)
        cmd = machine.step(obs(d=meas, wall_m=max(meas - 0.025, 0.0)), t)
        if cmd.done:
            d_true = max(d_true - coast * cfg.v_near_mps * 2, 0.0)
            return cmd, d_true
        pending.append((t + cmd_delay, -cmd.linear))     # 후진 크기
        while pending and pending[0][0] <= t:
            _, v = pending.pop(0)
            v_applied = v
        step = (max(0.0, v_applied * dt - spinup * cfg.v_near_mps) if v_applied > 0
                else coast * cfg.v_near_mps)
        d_true = max(d_true - step, 0.0)
        t += dt
    return cmd, d_true


def test_pulse_converges_without_overshoot_under_latency():
    """지연을 넣어도 목표를 크게 지나치지 않는가.

    한 걸음(5mm)보다 적게 남으면 밟지 않는 규칙이 이것을 만든다 — 밟는 순간
    지나치기 때문이다. 모자란 쪽으로 치우치는 것은 정상이고, 그 편향은 `stop_m`
    실측이 흡수한다.
    """
    cfg = raw_cfg(stop_m=0.065, ema_coef=0.5)
    for seed in range(20):
        m = ready(cfg, d=0.20)
        cmd, d_final = drive(m, cfg, seed=seed)
        assert cmd.phase == "DONE", f"seed={seed}: 수렴 실패 — {cmd.phase}"
        # 과주행 중단선을 넘지 않아야 한다. 여기가 초안이 40/60 으로 실패하던 자리다.
        assert d_final >= cfg.stop_m - cfg.overshoot_margin_m, (
            f"seed={seed}: 목표를 {(cfg.stop_m - d_final)*1000:.1f}mm 지나쳤다")


def test_stopping_short_is_what_prevents_overshoot():
    """되돌림 시험 — 정지 조건을 `d <= stop_m` 으로 바꾸면 이 시험이 빨개져야 한다.

    (구현 시 `_linear`/`step` 의 `min_step` 분기를 잠시 `filtered.d <= c.stop_m` 로
     바꿔 이 시험이 실제로 실패하는지 확인할 것. 통과만 보면 안 된다.)
    """
    cfg = raw_cfg(stop_m=0.065, ema_coef=0.5)
    overshot = 0
    for seed in range(20):
        m = ready(cfg, d=0.20)
        _, d_final = drive(m, cfg, seed=seed)
        if d_final < cfg.stop_m - cfg.overshoot_margin_m:
            overshot += 1
    assert overshot == 0, f"{overshot}/20 이 과주행 중단선을 넘었다"


def test_one_min_step_short_of_stop_finishes_without_a_final_pulse():
    """정지선 코앞(한 스텝 미만)에서 한 번 더 밀지 않는지 결정론적으로 확인한다.

    `test_stopping_short_is_what_prevents_overshoot` 는 지연 시뮬레이션(무작위 20
    시드)으로 이 조건을 간접 검증하지만, 이 브리프의 기본 파라미터 조합
    (stop_m=0.065, overshoot_margin_m=0.01, v_near_mps=0.05, pulse_min_s=0.10)
    에서는 되돌려도(=`filtered.d <= c.stop_m` 로 바꿔도) 20/20 이 여전히 마진 안에
    든다(실측) — 별도의 과주행 ABORT 문턱이 우연히 손상을 가려서다. 통계적 시험이
    이 결함을 항상 잡는다고 보장할 수 없으므로, 경계 지점(정지선에서 1mm 남음, 한
    스텝 5mm 보다 적음)을 직접 찍어 결정론적으로 잡는다.

    `_linear` 의 펄스 길이는 `pulse_min_s` 로 **아래가 클램프**된다 — 남은 거리가
    1mm 라도 펄스는 최소 5mm 어치를 명령한다. 그래서 `d <= stop_m` 조건에서는 이
    경계에서 실제로 한 번 더(5mm) 미는 펄스가 나간다.
    """
    cfg = raw_cfg(stop_m=0.065, pulse_min_s=0.10, v_near_mps=0.05)
    m = ready(cfg, d=0.30)
    # 정지선에서 1mm 남았다 — 한 스텝(5mm)보다 적게 남았다.
    cmd = m.step(obs(d=0.065 + 0.001, wall_m=0.04), 5.0)
    assert cmd.phase == "DONE", (
        f"한 스텝(5mm)도 안 남았는데 국면이 {cmd.phase} — 마지막 펄스를 또 밀면 지나친다")
    assert cmd.linear == 0.0


def test_total_timeout_aborts():
    m = ready(raw_cfg(timeout_s=5.0), d=0.30)
    cmd = m.step(obs(d=0.30), 100.0)
    assert cmd.phase == "ABORT"
