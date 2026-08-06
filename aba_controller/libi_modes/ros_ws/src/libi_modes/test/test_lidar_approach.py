"""라이다 도킹 국면 상태기계.

여기서 잡히는 것들은 실물에서만 드러나는 종류가 아니다:

  · 부호를 잃으면 후진이어야 할 것이 **전진**이 된다 — 도크를 들이받는다
  · 벽이 사라진 뒤에도 yaw 를 계속 믿으면 죽은 값으로 조향한다
  · 펄스가 수렴하지 않으면 목표를 지나쳐 계속 민다
"""
import math

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
    """SETTLE·SEARCH·ACQUIRE 를 통과시켜 주행 국면에 들어간 상태기계를 돌려준다.

    SEARCH 는 전용 kwarg(`search_wall_yaw`)로만 정렬 신호를 받는다(2026-08-04
    정정 — `obs.yaw` 는 더 이상 안 본다). 이 헬퍼는 SEARCH 자체를 시험하는
    게 아니라 ACQUIRE 이후를 시험하는 자리라, 이미 정렬이 끝난 것으로 보고
    `phase` 를 직접 대입해 곧장 ACQUIRE 로 넘긴다 — 다른 백박스 시험들
    (`test_acquire_aborts_without_rotating_when_nothing_is_found` 등)과 같은
    관례다.
    """
    cfg = cfg or raw_cfg()
    m = settled(cfg)
    m.phase = "ACQUIRE"
    m._phase_t0 = 2.0
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
    m.phase = "ACQUIRE"          # SEARCH 를 건너뛴다 — 여기선 ACQUIRE 만 시험한다
    m._phase_t0 = 2.0
    for _ in range(2):
        cmd = m.step(obs(), 2.0)
        assert cmd.phase == "ACQUIRE"
    cmd = m.step(obs(), 2.1)
    assert cmd.phase == "ALIGN"


def test_acquire_accepts_current_pose_detection():
    """현재 맵 자세에서 노치가 보이면 추가 좌우 회전 없이 확정한다."""
    cfg = raw_cfg(confirm_frames=3, search_align_tol_rad=0.15,
                  search_rot_rad_s=0.20)
    m = settled(cfg)
    m.phase = "ACQUIRE"
    m._phase_t0 = 2.0

    cmd = m.step(obs(yaw=0.40), 2.0)
    assert cmd.phase == "ACQUIRE"
    assert cmd.linear == 0.0 and cmd.angular == 0.0
    assert m._confirm == 1

    # 확인 프레임을 누적한 뒤 접근 국면으로 넘어간다.
    for _ in range(cfg.confirm_frames - 2):
        cmd = m.step(obs(yaw=0.40), 2.1)
        assert cmd.phase == "ACQUIRE"
    cmd = m.step(obs(yaw=0.40), 2.2)
    assert cmd.phase == "ALIGN"


def test_acquire_aborts_when_recovery_is_disabled():
    """회복을 0회로 끄면 종전처럼 노치 미검출을 즉시 안전 실패로 끝낸다."""
    cfg = LidarDockConfig(acquire_timeout_s=1.0, acquire_recovery_max=0)
    m = settled(cfg)
    m.phase = "ACQUIRE"
    m._phase_t0 = cfg.settle_sec + 2.0
    cmd = run(m, [None] * 20, t0=cfg.settle_sec + 2.0)
    assert cmd.phase == "ABORT"
    assert cmd.done is True
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_acquire_sweeps_both_sides_before_final_notch_not_found():
    """노치 확정 실패 시 같은 자세를 반복하지 않고 좌·우를 스윕한다.

    이 회복은 제자리 회전뿐이라 직선 속도는 항상 0이어야 하며, 정해진 횟수를
    모두 쓴 뒤에만 `notch_not_found`로 끝나야 한다.
    """
    cfg = LidarDockConfig(
        acquire_timeout_s=1.0,
        acquire_recovery_max=2,
        acquire_recovery_sweep_rad=0.20,
        acquire_recovery_settle_s=0.0,
        search_rot_rad_s=0.20,
    )
    m = settled(cfg)
    t = cfg.settle_sec + 2.0
    m.phase = "ACQUIRE"
    m._phase_t0 = t

    first = m.step(None, t + 1.1)
    assert first.phase == "REACQUIRE_SWEEP"
    assert first.linear == 0.0 and first.angular > 0.0
    done_first = m.step(None, m._recover_until + 0.01)
    assert done_first.phase == "REACQUIRE_SETTLE"
    reacquire = m.step(None, m._recover_settle_until + 0.01)
    assert reacquire.phase == "ACQUIRE"

    second = m.step(None, m._phase_t0 + 1.1)
    assert second.phase == "REACQUIRE_SWEEP"
    assert second.linear == 0.0 and second.angular < 0.0
    m.step(None, m._recover_until + 0.01)
    m.step(None, m._recover_settle_until + 0.01)

    final = m.step(None, m._phase_t0 + 1.1)
    assert final.phase == "ABORT" and final.reason == "notch_not_found"


def test_search_rotates_in_place_when_nothing_found_yet():
    """접근 전 회전이 부정확해도 라이다 스스로 벽을 찾아 정렬한다(2026-08-03 실측:
    벽 yaw -40.7도로 넘어온 사례). 못 찾는 동안은 제자리 회전 — 후진은 안 한다."""
    m = settled(LidarDockConfig())
    cmd = m.step(None, 5.0)
    assert cmd.phase == "SEARCH"
    assert cmd.linear == 0.0
    assert cmd.angular != 0.0


def test_search_aborts_on_timeout_when_nothing_found():
    """`t` 는 전체 타임아웃(`timeout_s`, 기본 90초)보다는 짧게, `search_timeout_s`
    보다는 한참 지나게 잡는다 — 안 그러면 전체 타임아웃이 먼저 걸려 `reason` 이
    "timeout" 으로 나와 이 시험이 실제로는 다른 것을 잰다."""
    cfg = LidarDockConfig(search_timeout_s=1.0)
    m = settled(cfg)
    cmd = m.step(None, cfg.settle_sec + 5.0)
    assert cmd.phase == "ABORT" and cmd.reason == "search_timeout"
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_search_accepts_visible_notch_in_current_pose():
    """[2026-08-04 실기 정정] 처음엔 `obs.yaw`(노치까지 확정된 값)로 돌았는데,
    각도가 심하게 틀어지면 벽은 잡혀도 노치 자체가 인식이 안 돼(라이다가 노치
    바닥 대신 옆벽을 스치는 등) `obs` 가 계속 `None` 이었다 — SEARCH 가 방향
    신호를 영영 못 받고 무한 스핀했다. 그래서 회전 방향은 `search_wall_yaw`
    (벽만 피팅한 값, 노치 확정 불필요)로만 정한다. 이 시험은 `obs` 에 뭘 담아
    넘겨도(정렬된 것처럼 보이는 값이라도) `search_wall_yaw` 를 안 주면 SEARCH
    가 절대 못 빠져나온다는 것을 잠근다 — obs 가 다시 슬쩍 쓰이면 여기서 걸린다.
    """
    m = settled(LidarDockConfig())
    cmd = m.step(obs(yaw=0.0), 5.0)
    assert cmd.phase == "ACQUIRE"
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_search_turns_toward_the_wall_and_hands_off_to_acquire_once_aligned():
    cfg = LidarDockConfig(search_align_tol_rad=0.4, steer_sign=1.0)
    m = settled(cfg)
    # 크게 어긋난 상태 — 아직 정렬 전이라 그 방향으로 계속 돈다
    cmd = m.step(None, 5.0, search_wall_yaw=-0.9)
    assert cmd.phase == "SEARCH"
    assert cmd.angular == pytest.approx(
        math.copysign(cfg.search_rot_rad_s, cfg.steer_sign * -0.9))
    # 허용 오차 안으로 들어왔다 — ACQUIRE 로 넘긴다(같은 tick 에 확정까지는 안 한다)
    cmd = m.step(None, 5.1, search_wall_yaw=0.1)
    assert cmd.phase == "ACQUIRE"
    assert cmd.linear == 0.0 and cmd.angular == 0.0
    assert m._confirm == 0, "핸드오프 tick 이 노치 확정 카운트를 슬쩍 올리면 안 된다"
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_search_uses_an_already_visible_notch_as_first_confirmation():
    """벽이 정렬된 첫 SEARCH 스캔에서 노치가 보이면 즉시 확정을 시작한다.

    단발 검출은 충분하지 않다. SEARCH는 ACQUIRE로 넘기되 confirm_frames가 남아
    있어 이 tick에도 직선·각속도는 모두 0이어야 한다.
    """
    cfg = raw_cfg(confirm_frames=3)
    m = settled(cfg)
    cmd = m.step(obs(), 5.0, search_wall_yaw=0.0)
    assert cmd.phase == "ACQUIRE"
    assert m._confirm == 1
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_near_observations_never_confirm_or_advance_out_of_acquire():
    """NEAR 에는 오검출 방어(직선 피팅)가 없다 — 실측 노치 없는 벽에서도 100% '검출'한다.
    ACQUIRE 가 NEAR 로 확정되면 오검출로 도킹이 **시작**된다. `confirm_frames` 를
    훌쩍 넘겨 NEAR 를 먹여도 ACQUIRE 를 벗어나면 안 되고, 결국 확정 실패로 ABORT 해야
    한다 — ALIGN·APPROACH·FINAL 어느 쪽으로도 넘어가지 않는다."""
    cfg = LidarDockConfig(
        acquire_timeout_s=1.0, confirm_frames=3, acquire_recovery_max=0)
    m = settled(cfg)
    t = 2.0
    m.phase = "ACQUIRE"          # SEARCH 를 건너뛴다 — 여기선 ACQUIRE 만 시험한다
    m._phase_t0 = t
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
                # RETREAT 만 유일한 예외다(정렬 게이트 재시도로 짧게 전진) —
                # 그 외 국면에서 전진이 나오면 진짜 부호 버그다.
                if cmd.phase != "RETREAT":
                    assert cmd.linear <= 0.0, f"전진 명령 {cmd.linear} (d={d}, phase={cmd.phase})"
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
    마지막 yaw 를 얼려서 쓰지 않는다. 죽은 값을 믿는 건 안 보는 것보다 나쁘다.

    [2026-08-04] **일단 FINAL 에 들어간 뒤**의 얘기다 — 진입 자체는 이제 yaw
    도 게이트를 거친다(아래 `test_final_entry_gate_also_rejects_large_yaw...`).
    그래서 작은 yaw 로 정상 진입시킨 다음, 그 이후 프레임의 큰 yaw 가
    무시되는지를 본다(게이트는 `_final_gate_checked` 로 한 번만 걸리므로
    두 번째 프레임에서는 다시 안 걸린다)."""
    m = ready(raw_cfg(), d=0.30)
    entered = m.step(obs(d=0.09, y=0.0, yaw=0.02, wall_m=0.065), 5.0)
    assert entered.phase == "FINAL", "이 시험의 전제 — 작은 yaw 는 정상 진입해야 한다"
    cmd = m.step(obs(d=0.08, y=0.0, yaw=0.5, wall_m=0.065), 5.1)
    assert cmd.phase == "FINAL"
    assert cmd.angular == 0.0, "이미 FINAL 이면 yaw 가 커져도 조향에 안 쓴다"


def test_final_entry_gate_also_rejects_large_yaw_because_y_is_unreliable_then():
    """[2026-08-04 실측] y 오차가 벽 yaw 에 거의 선형 비례한다(§10, 1도당
    2.4mm) — yaw 가 크면 y=0 이라 나와도 못 믿는다. 실측: yaw=12.8도에서
    y=24mm(문턱 15mm 는 넘었지만 상시문턱 30mm 는 통과)로 FINAL 진입했는데
    실제로는 물리 접촉이 났다. 벽이 진짜 사라지는 순간 yaw 가 크면(8.6도
    초과) y 값과 무관하게 막는다.

    `waypoint_margin_m=0.0` — 이 시험의 관심사는 `wall_really_gone` 게이트지
    웨이포인트 존 진입 게이트가 아니다(둘 다 같은 사유로 막을 수 있어
    안 떼어두면 어느 쪽이 막았는지 안 갈린다). `waypoint_retry_max=0` —
    이 시험의 관심사는 게이트 판정 자체지 재시도(RETREAT)가 아니다."""
    cfg = raw_cfg(yaw_max_enter_final_rad=0.15, wall_alive_min_m=0.08,
                  waypoint_margin_m=0.0, waypoint_retry_max=0)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.09, y=0.0, yaw=0.5, wall_m=0.065), 5.0)
    assert cmd.phase == "ABORT" and cmd.reason == "not_aligned_for_final"


def test_steering_authority_shrinks_smoothly_inside_the_waypoint_margin():
    """[2026-08-04, 웨이포인트, codex 검토 2차] 처음엔(1차) `stop_m` 앞
    `waypoint_margin_m` 안에서 조향을 완전히 0 으로 끊었다 — 그런데 그 전에
    못 맞춘 y·yaw 를 회수할 방법이 없어져 실기에서 그대로 `lateral_too_large`
    로 ABORT 됐다(yaw=15.6° 진입, d=112mm). y·yaw 피드백은 살려 두고, 대신
    상한만 `stop_m` 에 가까울수록 거리 비례로 줄인다 — 존 경계 근처는 거의
    평소 상한, `stop_m` 바로 앞은 아주 작은 상한.

    존 진입 게이트(다른 시험)를 안 건드리게 y·yaw 를 그 문턱(기본 15mm·2°)
    안으로 작게 잡는다 — 여기서 보는 건 게이트 통과 여부가 아니라 통과한
    뒤의 상한 축소다.

    `m._wall_yaw_ref = 0.02` 로 기준을 미리 맞춰 둔다 — `ready()` 의 확인
    프레임은 yaw=0.0 이라, 안 맞춰 두면 이 시험만의 갑작스러운 yaw 값이
    안정화 램프업(`_stabilize`, 별개 관심사)과 뒤섞여 d·y 가 이 시험이
    재려는 것과 다른 값으로 재투영된다."""
    cfg = raw_cfg(stop_m=0.065, waypoint_margin_m=0.10)
    m1, m2 = ready(cfg, d=0.50), ready(cfg, d=0.50)
    m1._wall_yaw_ref = m2._wall_yaw_ref = 0.02
    near_edge = m1.step(obs(d=0.16, y=0.01, yaw=0.02, wall_m=0.14), 5.0)
    near_stop = m2.step(obs(d=0.075, y=0.01, yaw=0.02, wall_m=0.14), 5.0)
    assert near_edge.linear < 0.0 and near_stop.linear < 0.0, "직진(후진)은 계속 살아 있어야 한다"
    assert near_edge.angular != 0.0 and near_stop.angular != 0.0, "0 으로 뚝 끊기면 안 된다"
    assert abs(near_stop.angular) < abs(near_edge.angular), \
        "stop_m 에 가까울수록 조향 상한이 더 작아져야 한다"


def test_waypoint_zone_entry_gate_aborts_immediately_if_misaligned():
    """[2026-08-04, codex 검토 2차] 실기 재현 — yaw=15.6°, d=112mm(존 안)로
    처음 들어왔는데 아무도 안 막아서 한참 뒤에야 FINAL 의 `lateral_too_large`
    로 잡혔다. 이제는 존 경계를 처음 넘는 그 프레임에 바로 막아야 한다 —
    재시도 여지를 살리려면 실패를 최대한 앞당겨 잡아야 한다(codex 권고).

    `waypoint_retry_max=0` — 이 시험의 관심사는 게이트가 그 프레임에 바로
    반응하는가지, RETREAT 재시도(별도 시험)가 아니다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=0)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert cmd.phase == "ABORT" and cmd.reason == "not_aligned_for_waypoint"


def test_waypoint_zone_entry_gate_only_checked_once():
    """진입 순간만 본다 — 존 안에서 이미 통과했으면, 이후 프레임이 잠깐
    흔들려도(펄스 사이 노이즈 등) 다시 묻지 않는다(`_final_gate_checked` 와
    같은 한 번짜리 래치 패턴)."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15)
    m = ready(cfg, d=0.30)
    entered = m.step(obs(d=0.115, y=0.0, yaw=0.0, wall_m=0.09), 5.0)
    assert entered.phase != "ABORT", "정렬된 채 진입해야 한다 — 이 시험의 전제"
    # y=20mm: 진입 게이트 문턱(15mm)은 넘지만, FINAL 상시 문턱(y_max_final_m,
    # 기본 30mm)과는 안 겹치게 — 그 별개 안전장치가 대신 걸려서 결과가
    # 헷갈리지 않게 한다.
    cmd = m.step(obs(d=0.11, y=0.02, yaw=0.5, wall_m=0.09), 5.1)
    assert cmd.phase != "ABORT", "게이트는 진입 순간 한 번만 — 이후 프레임엔 다시 안 걸린다"


def test_misaligned_entry_retreats_instead_of_aborting_when_retries_remain():
    """[2026-08-04, 사용자 요청] 정렬 게이트가 막았을 때 재시도가 남아있으면
    바로 ABORT 하지 않고 RETREAT(짧은 전진)로 물러난다. 이게 `Cmd.linear`
    "항상 ≤0" 불변식의 유일한 예외다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=2)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert cmd.phase == "RETREAT" and cmd.reason == "not_aligned_for_waypoint"
    assert cmd.linear > 0.0, "물러나는 유일한 예외 국면 — 전진이어야 한다"
    assert cmd.angular == 0.0


def test_retreat_reaches_v_far_dist_m_then_returns_to_align():
    """[2026-08-04, 사용자 요청] 시간이 아니라 **거리**로 끊는다 —
    `v_far_dist_m` 는 `_angular()` 우선순위 램프가 "먼 구간"(yaw 게인
    낮음, y 가 자연스럽게 맞춰질 여지 큼) 기준으로 쓰는 지점이라, 딱
    거기까지 물러나야 다음 시도가 그 여유를 곧바로 받는다. 시간 상한
    (`waypoint_retreat_s`) 은 넉넉히 둬서 이 시험이 재는 게 거리 기준임을
    분명히 한다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=2, waypoint_retreat_s=30.0, v_far_dist_m=0.30)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert cmd.phase == "RETREAT"
    still_short = m.step(obs(d=0.20, y=0.0, yaw=0.0, wall_m=0.175), 5.5)
    assert still_short.phase == "RETREAT", "v_far_dist_m 에 아직 못 미쳤다 — 계속 물러나야 한다"
    reached = m.step(obs(d=0.30, y=0.0, yaw=0.0, wall_m=0.275), 6.0)
    assert reached.phase != "RETREAT", "v_far_dist_m 에 도달했으면(시간과 무관하게) 복귀해야 한다"
    assert m.phase == "ALIGN"


def test_retreat_gives_up_on_the_time_cap_if_detection_never_recovers():
    """[2026-08-04, 사용자 요청] `waypoint_retreat_s` 는 거리 판정이 실패할
    때(검출이 안 잡히는 등)의 **최대 시간 상한**(안전판)이다 — 그게 없으면
    물러나는 동안 검출이 안 잡히면 무한정 RETREAT 에 머문다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=2, waypoint_retreat_s=1.0, v_far_dist_m=0.30)
    m = ready(cfg, d=0.30)
    m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    # 검출이 계속 실패한다(obs=None) — v_far_dist_m 도달을 영영 확인 못 한다.
    cmd = m.step(None, 5.1)
    assert cmd.phase == "RETREAT", "시간 상한(1.0초) 전이라 아직 물러나는 중이어야 한다"
    cmd = m.step(None, 6.2)
    assert cmd.phase != "RETREAT", "시간 상한을 넘겼으니 검출과 무관하게 복귀해야 한다"
    assert m.phase == "ALIGN"


def test_retreat_resets_the_per_attempt_gate_latches():
    """재시도가 이전 시도의 게이트 래치·확인 카운터를 그대로 물려받으면 안
    된다 — 새 시도는 새 판정을 받아야 한다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=2, waypoint_retreat_s=0.5)
    m = ready(cfg, d=0.30)
    m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert m._waypoint_gate_checked is False
    assert m._waypoint_confirm == 0
    assert m._waypoint_hold_t0 is None
    assert m._waypoint_confirmed_once is False


def test_min_range_safety_stop_is_skipped_during_retreat():
    """RETREAT 은 멀어지는 중이라 "가까움"이 도착 신호가 아니다 — 안전
    정지가 여기서 걸리면 물러나던 중에 DONE 으로 잘못 끝난다."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=2, safety_clearance_m=0.01)
    m = ready(cfg, d=0.30)
    m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert m.phase == "RETREAT"
    cmd = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.1,
                min_range_m=0.005)
    assert cmd.phase == "RETREAT", "안전 정지가 RETREAT 를 끊으면 안 된다"


def test_aborts_for_real_once_retries_are_exhausted():
    """재시도를 다 쓰면 다음 실패는 진짜 ABORT 다 — 무한루프 방지."""
    cfg = raw_cfg(stop_m=0.06, waypoint_margin_m=0.06,
                  y_max_enter_final_m=0.015, yaw_max_enter_final_rad=0.15,
                  waypoint_retry_max=1, waypoint_retreat_s=0.2)
    m = ready(cfg, d=0.30)
    first = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.0)
    assert first.phase == "RETREAT", "1번째는 재시도가 남아 있어야 한다"
    back = m.step(obs(d=0.30, y=0.0, yaw=0.0, wall_m=0.275), 5.3)
    assert back.phase != "RETREAT" and m.phase == "ALIGN"
    second = m.step(obs(d=0.112, y=-0.028, yaw=0.27, wall_m=0.09), 5.4)
    assert second.phase == "ABORT", "재시도(1회)를 다 썼으니 이번엔 진짜 ABORT"
    assert second.reason == "not_aligned_for_waypoint"


def test_angular_output_slew_rate_is_limited_between_frames():
    """[2026-08-04, codex 검토] "각속도 max 를 풀자"는 반박당했다 — P 제어라
    D 가 없어 그대로 풀면 관측 노이즈가 좌우 요동으로 직결된다. 대신 출력의
    프레임 간 변화량을 제한한다. 첫 프레임은 기준이 없어 그대로 통과하고,
    그다음 프레임에서 큰 반대 부호 오차가 들어와도 한 번에 다 못 튄다."""
    cfg = raw_cfg(ang_slew_max_rad_s2=1.0)
    m = ready(cfg, d=0.50)
    first = m.step(obs(d=0.30, y=0.2, yaw=0.0), 5.0)  # 첫 프레임 — 제한 없이 그대로
    assert first.angular == pytest.approx(cfg.ang_max_rad_s * cfg.steer_sign, abs=1e-6) \
        or abs(first.angular) <= cfg.ang_max_rad_s
    flipped = m.step(obs(d=0.29, y=-0.2, yaw=0.0), 5.1)  # 0.1초 뒤 정반대 큰 오차
    max_step = cfg.ang_slew_max_rad_s2 * 0.1
    assert abs(flipped.angular - first.angular) <= max_step + 1e-6, \
        "한 프레임 사이에 변화율 상한보다 더 튀면 안 된다"


def test_steering_still_active_just_outside_the_waypoint_margin():
    """위 시험의 대조군 — 웨이포인트 경계 바로 밖에서는 평소처럼 조향한다."""
    cfg = raw_cfg(stop_m=0.065, waypoint_margin_m=0.10)
    m = ready(cfg, d=0.50)
    cmd = m.step(obs(d=0.20, y=0.05, yaw=0.0, wall_m=0.18), 5.0)
    assert cmd.angular != 0.0, "웨이포인트 밖이면 평소처럼 y 를 교정해야 한다"


def test_align_phase_does_use_yaw():
    """FINAL 의 0 이 의미를 가지려면, 벽이 살아 있을 때는 실제로 써야 한다."""
    m = ready(raw_cfg(), d=0.50)
    cmd = m.step(obs(d=0.50, y=0.0, yaw=0.5), 3.0)
    assert cmd.phase == "ALIGN"
    assert cmd.angular != 0.0


def test_yaw_authority_is_weak_far_away_so_y_correction_can_angle_in():
    """[2026-08-04 실기, 6차] `k_y*y + k_yaw*yaw` 를 그냥 더하면 두 항이
    서로 싸운다 — 실기: d=343mm 에서 yaw=+0.4°(거의 완벽)인데 y=+29mm 는
    그대로였다(yaw 게인이 세서 y 를 줄이는 데 필요한 비스듬함을 계속 눌렀다).
    `v_far_dist_m` 보다 먼 곳(ALIGN)에서는 yaw 기여가 `k_yaw_far_scale`
    비율까지 줄어야 한다 — `k_y=0` 으로 y 항을 꺼서 순수 yaw 기여만 잰다.

    존 진입 게이트가 안 끼어들게 그 문턱은 넉넉히 풀어 둔다 — 이 시험의
    관심사는 램프 자체지 게이트 통과 여부가 아니다."""
    cfg = raw_cfg(v_far_dist_m=0.30, stop_m=0.065, waypoint_margin_m=0.06,
                  k_yaw_far_scale=0.25, k_yaw_align=2.0, k_y=0.0,
                  y_max_enter_final_m=1.0, yaw_max_enter_final_rad=1.0)
    far = ready(cfg, d=0.50).step(obs(d=0.31, y=0.0, yaw=0.2), 5.0)
    assert far.phase == "ALIGN", "이 시험의 전제 — v_far_dist_m 보다 멀면 ALIGN"
    # k_y=0 이라 각속도는 순수 yaw 기여뿐이다 — far_scale 만큼만 나와야 한다.
    expected_far = cfg.steer_sign * cfg.k_yaw_align * cfg.k_yaw_far_scale * 0.2
    assert far.angular == pytest.approx(expected_far, rel=1e-6)


def test_yaw_authority_ramps_up_to_full_near_the_waypoint_zone():
    """존 경계(`stop_m+waypoint_margin_m`)에 가까워지면 yaw 기여가 원래
    게인(전체)까지 램프업해야 한다 — 마지막엔 똑바로 세워야 하므로.

    이 거리(d=0.125<v_far_dist_m)에서는 이미 APPROACH 다 — `k_yaw_approach`
    가 기준 게인이다. 존 진입 게이트와 `pulse_dist_m` 선제 FINAL 진입이
    안 끼어들게 그 문턱들을 넉넉히·좁게 각각 조정한다 — 이 시험의 관심사는
    램프 자체지 국면 전이 타이밍이 아니다."""
    cfg = raw_cfg(v_far_dist_m=0.30, stop_m=0.065, waypoint_margin_m=0.06,
                  pulse_dist_m=0.10, k_yaw_far_scale=0.25, k_yaw_approach=1.2,
                  k_y=0.0, y_max_enter_final_m=1.0, yaw_max_enter_final_rad=1.0)
    near = ready(cfg, d=0.50).step(obs(d=0.125, y=0.0, yaw=0.2), 5.0)
    assert near.phase == "APPROACH", "이 시험의 전제 — 존 경계는 v_far_dist_m 보다 가깝다"
    expected_near = cfg.steer_sign * cfg.k_yaw_approach * 0.2   # far_scale 없이 100%
    assert near.angular == pytest.approx(expected_near, rel=1e-6)


def test_yaw_authority_ramp_does_not_apply_in_final():
    """FINAL 은 `k_yaw_final=0` 이라 램프업 자체가 무의미하다 — 램프가 실수로
    0 이 아닌 기여를 만들어내지 않는지 확인한다."""
    cfg = raw_cfg(k_yaw_far_scale=0.25, k_yaw_align=2.0)
    m = ready(cfg, d=0.30)
    entered = m.step(obs(d=0.09, y=0.0, yaw=0.02, wall_m=0.065), 5.0)
    assert entered.phase == "FINAL"
    cmd = m.step(obs(d=0.08, y=0.0, yaw=0.3, wall_m=0.065), 5.1)
    assert cmd.angular == 0.0, "FINAL 은 k_yaw_final=0 이라 yaw 기여가 없어야 한다"


def test_final_aborts_when_lateral_error_is_too_large():
    """이 시험의 관심사는 FINAL 의 **상시** 문턱(`y_max_final_m`)이지 진입
    게이트(`y_max_enter_final_m`)가 아니다 — 진입 게이트를 넉넉히 풀어서
    (`waypoint_margin_m=0.0` 로 웨이포인트 존 게이트도 함께) 상시 문턱까지
    통과해 들어가게 한다."""
    cfg = raw_cfg(y_max_final_m=0.03, y_max_enter_final_m=1.0,
                  waypoint_margin_m=0.0)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.09, y=0.06, wall_m=0.065), 5.0)
    assert cmd.phase == "ABORT" and cmd.done is True and cmd.reason == "lateral_too_large"


def test_waypoint_align_aborts_early_if_never_confirmed_within_the_timeout():
    """[2026-08-04, codex 검토] "정지해서 y를 맞춘다"는 반박당했다 — 제자리
    회전은 yaw만 바꾸고 y는 거의 못 바꾼다(실제 도킹 원리와 일치, 이동+조향
    으로만 lateral offset 이 줄어든다). 그래서 로봇은 계속 움직이며 조향하게
    두고, 대신 근접 구간에서 "정렬됐다"를 연속 N프레임으로 확인하다가 시간
    안에 못 채우면 90초 전체 타임아웃까지 안 끌고 여기서 먼저 ABORT한다.

    y=20mm 로 잡는다 — 확인 문턱(y_max_enter_final_m=15mm)은 못 넘지만
    상시 문턱(y_max_final_m, 기본 30mm)과는 안 겹치게.

    `waypoint_margin_m=0.0` — 이 시험의 관심사는 `pulse_dist_m` 기준 확인/
    타임아웃 메커니즘이지 웨이포인트 존 진입 게이트가 아니다(기본값이면
    d=0.12 가 그 존 안이라 게이트가 먼저 막아 버린다). `waypoint_retry_max=0`
    — 이 시험의 관심사는 타임아웃 판정 자체지 RETREAT 재시도가 아니다(재시도가
    있으면 이 타임아웃도 RETREAT 로 흡수돼 20 프레임 안에 최종 ABORT 까지
    안 간다)."""
    cfg = raw_cfg(waypoint_align_timeout_s=1.0, waypoint_align_confirm_frames=3,
                  pulse_dist_m=0.15, y_max_enter_final_m=0.015, y_max_final_m=0.03,
                  waypoint_margin_m=0.0, waypoint_retry_max=0)
    m = ready(cfg, d=0.30)
    # ⚠️ `run()` 은 마지막 Cmd 만 돌려준다 — ABORT **이후**에도 계속 먹이면
    #    approach.py 맨 위 "이미 끝났으면 그대로 반환" 분기가 원래 사유를
    #    "finished" 로 덮어써 버린다(dock_scan_visualize 에서 실기로 확인한
    #    바로 그 함정). ABORT 되는 그 순간의 Cmd 를 잡으려면 먼저 멈춘다.
    t, cmd = 5.0, None
    for _ in range(20):
        cmd = m.step(obs(d=0.12, y=0.02, wall_m=0.10), t)
        if cmd.phase == "ABORT":
            break
        t += 0.1
    assert cmd.phase == "ABORT" and cmd.reason == "waypoint_align_timeout"


def test_waypoint_align_does_not_abort_once_confirmed_for_enough_consecutive_frames():
    """정렬이 실제로 연속 N프레임 확인되면(y·yaw 둘 다 문턱 안) 타임아웃에
    안 걸려야 한다 — 그리고 한 번 확인되면(래치) 이후 시각이 타임아웃을
    넘겨도 다시 걸리지 않는다(codex 권고 — 히스테리시스, 노이즈로 안팎을
    왕복해도 흔들리면 안 된다)."""
    cfg = raw_cfg(waypoint_align_timeout_s=1.0, waypoint_align_confirm_frames=3,
                  pulse_dist_m=0.15, y_max_enter_final_m=0.015, y_max_final_m=0.03)
    m = ready(cfg, d=0.30)
    # 3프레임만 잘 정렬 — confirm_frames 를 딱 채운다.
    good = [obs(d=0.12, y=0.0, yaw=0.0, wall_m=0.10)] * 3
    cmd = run(m, good, t0=5.0, dt=0.1)
    assert cmd.reason != "waypoint_align_timeout"
    # 확인된 뒤 타임아웃(1.0초)을 훌쩍 넘기는 시간이 지나도(계속 그 자리에
    # 있다고 가정) 다시 걸리면 안 된다 — 래치가 안 걸렸으면 여기서 실패한다.
    more = [obs(d=0.12, y=0.0, yaw=0.0, wall_m=0.10)] * 15
    cmd = run(m, more, t0=5.3, dt=0.1)
    assert cmd.reason != "waypoint_align_timeout"


def test_waypoint_align_confirm_counter_resets_when_leaving_the_pulse_zone():
    """근접 구간(`pulse_dist_m`)을 벗어나면(d 가 다시 멀어지면) 확인 카운터·
    시각·래치가 초기화돼야 한다 — 예전 근접 시도의 타이머가 다음 근접 시도에
    이어지면 안 된다.

    `waypoint_margin_m=0.0` — 웨이포인트 존 진입 게이트(기본값이면 d=0.12 가
    그 존 안이라 첫 스텝에서 바로 ABORT 되어 이 시험이 보려는 확인 블록
    자체에 못 들어간다)와 안 섞이게 뗀다."""
    cfg = raw_cfg(waypoint_align_timeout_s=1.0, waypoint_align_confirm_frames=3,
                  pulse_dist_m=0.15, y_max_enter_final_m=0.015, y_max_final_m=0.03,
                  waypoint_margin_m=0.0)
    m = ready(cfg, d=0.30)
    m.step(obs(d=0.12, y=0.02, wall_m=0.10), 5.0)  # 근접 구간 진입, 정렬 안 됨
    assert m._waypoint_hold_t0 is not None
    m.step(obs(d=0.20, y=0.02, wall_m=0.18), 5.1)  # 다시 멀어짐(pulse_dist_m 밖)
    assert m._waypoint_hold_t0 is None
    assert m._waypoint_confirm == 0
    assert m._waypoint_confirmed_once is False


def test_enters_final_proactively_once_close_even_if_wall_m_still_looks_alive():
    """[2026-08-04 실측] "벽이 사라진 프레임"을 기다리면 안 된다 — 신호가
    서서히가 아니라 뚝 끊기면(실기: d≈111mm) 그 확인 프레임 자체가 안 온다.
    `d` 가 `pulse_dist_m` 안이면 `wall_m` 이 아직 살아있는 것처럼 보여도
    FINAL 로 미리 넘겨서, 다음 프레임에 검출이 죽어도 NEAR 인수인계
    (`phase=="FINAL"`) 준비가 이미 돼 있게 한다."""
    cfg = raw_cfg(pulse_dist_m=0.15)
    m = ready(cfg, d=0.30)
    # wall_m 은 wall_alive_min_m(0.08) 보다 훨씬 위 — 그것만 보면 아직 FINAL 이 아니다.
    cmd = m.step(obs(d=0.12, y=0.0, wall_m=0.10), 5.0)
    assert cmd.phase == "FINAL", "d 가 pulse_dist_m 안이면 wall_m 살아있어도 FINAL 로 넘겨야 한다"


def test_distance_triggered_final_entry_does_not_abort_even_if_not_yet_aligned():
    """[2026-08-04 실측, 2차 결함] 선제 FINAL 진입(`pulse_dist_m`) 시점에
    정렬 게이트를 바로 물으면, 아직 벽이 멀쩡히 보여 교정할 거리가 남았는데도
    성급하게 ABORT 된다(실측: y=-12mm, 문턱 15mm 인데도 ABORT — 그 순간
    filtered.y 는 아직 더 컸다). 벽이 멀쩡한(wall_m 충분히 큼) 선제 진입은
    정렬 안 됐어도 ABORT 하지 않고 계속 교정해야 한다.

    y=20mm 로 잡는다 — 진입 게이트(15mm)는 넘지만, FINAL 중 상시 문턱
    (`y_max_final_m`, 기본 30mm)은 아직 안 넘어서 그 별개의 안전장치와
    안 겹친다. `waypoint_margin_m` 은 0 으로 꺼서 이 시험의 관심사(정렬
    게이트)와 웨이포인트 직진 기능이 안 섞이게 한다."""
    cfg = raw_cfg(pulse_dist_m=0.15, y_max_enter_final_m=0.015, waypoint_margin_m=0.0)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.12, y=0.02, wall_m=0.10), 5.0)
    assert cmd.phase == "FINAL"
    assert cmd.reason != "not_aligned_for_final"
    # y 교정은 FINAL 에서도 계속 살아 있어야 한다(꺼지는 건 yaw 뿐).
    assert cmd.angular != 0.0


def test_still_aborts_when_the_wall_truly_vanishes_while_not_yet_aligned():
    """정렬 게이트 자체가 없어진 게 아니다 — 벽이 **진짜** 사라진 순간(wall_m
    이 wall_alive_min_m 아래)에는 여전히 정렬을 확인하고 못 미치면 막는다.
    d 는 `arrived`(DONE) 문턱을 안 건드리게 여유 있게 잡는다.

    `waypoint_margin_m=0.0` — 웨이포인트 존 진입 게이트와 안 섞이게 뗀다
    (이 시험의 관심사는 `wall_really_gone` 게이트다). `waypoint_retry_max=0`
    — 게이트 판정 자체가 관심사지 RETREAT 재시도가 아니다."""
    cfg = raw_cfg(pulse_dist_m=0.15, y_max_enter_final_m=0.015, wall_alive_min_m=0.08,
                  waypoint_margin_m=0.0, waypoint_retry_max=0)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.10, y=0.05, wall_m=0.04), 5.0)
    assert cmd.phase == "ABORT" and cmd.reason == "not_aligned_for_final"


def test_final_gate_is_checked_only_once_not_every_frame_after_entering_final():
    """벽이 진짜 사라진 첫 프레임에 정렬이 맞아 게이트를 통과했으면, 그 다음
    프레임에 y 가 다시 커져도(펄스 사이 노이즈 등) 게이트를 또 묻지 않는다 —
    **처음 진짜로 사라진 그 한 번**만 확인하고 끝이다. `y_max_final_m` 은
    이 시험의 관심사가 아니라 넉넉히 풀어 둔다(그 별개 상시 문턱과 안
    섞이게)."""
    cfg = raw_cfg(pulse_dist_m=0.15, y_max_enter_final_m=0.015, wall_alive_min_m=0.08,
                  y_max_final_m=1.0)
    m = ready(cfg, d=0.30)
    # 1) 벽이 진짜 사라짐 + 정렬 잘 됨 — 게이트 통과, 여기서 한 번 "소모"된다.
    cmd1 = m.step(obs(d=0.10, y=0.0, wall_m=0.04), 5.0)
    assert cmd1.phase == "FINAL"
    # 2) 다음 프레임엔 y 가 커져도 게이트를 다시 안 묻는다 — 이미 소모됐다.
    cmd2 = m.step(obs(d=0.09, y=0.05, wall_m=0.04), 5.1)
    assert cmd2.phase != "ABORT", "게이트는 한 번만 묻는다 — 이미 통과했으면 다시 걸면 안 된다"


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


def test_stabilize_is_a_no_op_when_the_wall_yaw_never_changes():
    """[2026-08-04, 사용자 지적] 벽 각도가 프레임마다 똑같으면(잡음 없음)
    안정화가 아무것도 바꾸면 안 된다 — 기준이 그 값에 바로 고정되므로."""
    m = LidarApproach(raw_cfg())
    for _ in range(5):
        st = m._stabilize(obs(d=0.25, y=0.012, yaw=0.2))
        assert st.d == pytest.approx(0.25) and st.y == pytest.approx(0.012)


def test_stabilize_preserves_the_underlying_raw_point():
    """재투영은 좌표 변환일 뿐이다 — 원래 잰 물리적 점(라이다 원점 기준
    raw x,y)은 어느 기준으로 복원해도 같아야 한다. 노이즈로 yaw 가 튀어도
    안정화 뒤의 `(d,y,ref)` 로 되돌린 raw 점이 원래 `(d,y,yaw)` 로 되돌린
    raw 점과 일치해야 한다 — 정보가 사라지거나 왜곡되면 안 된다."""
    m = LidarApproach(raw_cfg())
    m._stabilize(obs(d=0.30, y=0.0, yaw=0.05))       # 기준 부트스트랩
    o = obs(d=0.28, y=0.03, yaw=0.15)                # 다음 프레임 — yaw 가 튐
    st = m._stabilize(o)
    raw_x = o.d * math.cos(o.yaw) - o.y * math.sin(o.yaw)
    raw_y = o.d * math.sin(o.yaw) + o.y * math.cos(o.yaw)
    ref = m._wall_yaw_ref
    stab_x = st.d * math.cos(ref) - st.y * math.sin(ref)
    stab_y = st.d * math.sin(ref) + st.y * math.cos(ref)
    assert stab_x == pytest.approx(raw_x, abs=1e-9)
    assert stab_y == pytest.approx(raw_y, abs=1e-9)


def test_stabilize_reference_moves_slowly_not_all_the_way_to_a_single_frame():
    """한 프레임의 큰 yaw 튐이 기준을 그 값으로 바로 끌고 가면 안 된다 —
    그러면 노이즈를 그냥 그대로 따라가는 것과 같다(느린 필터의 의미가
    없어진다)."""
    cfg = raw_cfg(wall_yaw_ref_coef=0.08)
    m = LidarApproach(cfg)
    m._stabilize(obs(d=0.30, y=0.0, yaw=0.0))         # 기준 0 으로 부트스트랩
    m._stabilize(obs(d=0.30, y=0.0, yaw=0.5))         # 한 프레임짜리 큰 튐
    assert m._wall_yaw_ref == pytest.approx(0.08 * 0.5, abs=1e-9)
    assert abs(m._wall_yaw_ref) < 0.5, "한 프레임에 기준이 다 끌려가면 안 된다"


def test_stabilize_leaves_near_observations_untouched():
    """NEAR 는 애초에 벽 기준이 아니라 라이다 원점 기준 그대로다 — 재투영할
    기준 자체가 없다."""
    m = LidarApproach(raw_cfg())
    m._stabilize(obs(d=0.30, y=0.0, yaw=0.3))          # 기준을 0 아닌 값으로 만들어 둔다
    n = near_obs(d=0.06, y=0.015)
    st = m._stabilize(n)
    assert st.d == n.d and st.y == n.y


def test_stabilize_is_skipped_in_final_because_yaw_is_unreliable_there():
    """[2026-08-04 실기] FINAL 은 벽면이 사라지는 중이라 `obs.yaw` 자체가
    이미 못 믿을 값이다(`k_yaw_final=0` 인 이유와 같다). 못 믿는 yaw 로
    재투영하면 y 에 그 잡음을 그대로 옮겨 심는다 — 실기에서 yaw=0.5 한
    프레임에 y 가 0→38mm 로 튀어 `lateral_too_large` 오탐을 냈다."""
    m = LidarApproach(raw_cfg())
    m._stabilize(obs(d=0.30, y=0.0, yaw=0.0))          # 기준을 0 으로 잡아 둔다
    m.phase = "FINAL"
    st = m._stabilize(obs(d=0.08, y=0.0, yaw=0.5, wall_m=0.065))
    assert st.d == pytest.approx(0.08) and st.y == pytest.approx(0.0), \
        "FINAL 에서는 재투영 없이 그대로 통과해야 한다"


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


def test_min_range_safety_stop_fires_even_if_notch_detection_totally_fails():
    """[2026-08-04, 사용자 요청] 노치 검출(`obs`)은 추정값이라 틀릴 수 있다 —
    오늘 밤 그걸로 실제 접촉이 났다. `obs=None`(검출 완전 실패)이어도 원시
    최소거리가 안전 여유 아래면 정렬·검출 상태와 무관하게 즉시 서야 한다."""
    cfg = raw_cfg(safety_clearance_m=0.01)
    m = ready(cfg, d=0.30)
    cmd = m.step(None, 5.0, min_range_m=0.008)
    assert cmd.phase == "DONE" and cmd.reason == "min_range_safety_stop"
    assert cmd.linear == 0.0 and cmd.angular == 0.0


def test_min_range_safety_stop_does_not_fire_while_still_clear():
    cfg = raw_cfg(safety_clearance_m=0.01)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.30), 5.0, min_range_m=0.20)
    assert cmd.reason != "min_range_safety_stop"


def test_min_range_safety_stop_ignores_none_meaning_not_measured():
    """호출자가 안 넘기면(기본 `None`) 이 안전장치가 조용히 꺼진다 — 값이
    없다는 것과 "가깝다"를 혼동하면 안 된다."""
    cfg = raw_cfg(safety_clearance_m=0.01)
    m = ready(cfg, d=0.30)
    cmd = m.step(obs(d=0.30), 5.0)
    assert cmd.reason != "min_range_safety_stop"


def test_min_range_safety_stop_is_skipped_during_search():
    """SEARCH 는 제자리 회전만 한다(전진 성분이 없다) — 그 국면에서는 이
    안전 정지가 안 걸려야 정상적으로 회전을 계속할 수 있다."""
    cfg = raw_cfg(safety_clearance_m=0.01)
    m = settled(cfg)
    cmd = m.step(None, 5.0, search_wall_yaw=0.5, min_range_m=0.001)
    assert cmd.phase == "SEARCH" and cmd.reason != "min_range_safety_stop"
