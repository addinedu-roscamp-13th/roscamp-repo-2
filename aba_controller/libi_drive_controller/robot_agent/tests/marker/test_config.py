from app.marker.config import MarkerDriveConfig
from app.marker.types import Cmd, MarkerObs


def test_defaults_match_field_verified_constants():
    c = MarkerDriveConfig()
    assert c.steer_kp == 0.22
    assert c.steer_ki == 0.01
    assert c.steer_kd == 0.0
    assert c.steer_deadband == 0.012
    assert c.steer_ang_max == 0.08
    assert c.steer_sign == 1.0
    assert c.axis_gate_m == 0.6
    assert c.stop_m == 0.10
    assert c.front_offset_m == 0.0
    assert c.marker_len_m == 0.07
    assert c.marker_id == 1
    assert c.dict_name == "DICT_5X5_100"


def test_clamped_forces_sane_ranges():
    c = MarkerDriveConfig(steer_kp=-5.0, loop_hz=999.0, search_step_deg=0.0).clamped()
    assert c.steer_kp == 0.0
    assert c.loop_hz == 30.0
    assert c.search_step_deg == 1.0


def test_steer_sign_is_normalized_to_plus_or_minus_one():
    assert MarkerDriveConfig(steer_sign=-0.3).clamped().steer_sign == -1.0
    assert MarkerDriveConfig(steer_sign=4.0).clamped().steer_sign == 1.0


def test_value_types_are_frozen():
    obs = MarkerObs(marker_id=1, ex=0.0, z_m=1.0,
                    yaw_deg=0.0, lateral_m=0.0, size_frac=0.1)
    cmd = Cmd(linear=0.0, angular=0.0, phase="SEARCH", done=False, reason="")
    for frozen, field in ((obs, "z_m"), (cmd, "linear")):
        try:
            setattr(frozen, field, 9.9)
        except Exception as exc:
            assert "frozen" in type(exc).__name__.lower()
        else:
            raise AssertionError(f"{type(frozen).__name__} 이 frozen 이 아니다")


def test_non_finite_values_fall_back_to_defaults():
    """_clamp 는 NaN 을 못 막는다 — 모든 비교가 False 라 그대로 통과한다.

    그러면 `--scan-guard nan` 한 번에 근접 보호와 센서 끊김 감지가 조용히 꺼진다
    (0.01 < NaN 은 False, age() > NaN 도 False).
    """
    c = MarkerDriveConfig(scan_guard_m=float("nan"),
                          sensor_timeout_s=float("nan"),
                          stop_m=float("inf"),
                          steer_kp=float("-inf")).clamped()
    d = MarkerDriveConfig()
    assert c.scan_guard_m == d.scan_guard_m
    assert c.sensor_timeout_s == d.sensor_timeout_s
    assert c.stop_m == d.stop_m
    assert c.steer_kp == d.steer_kp


def test_pulse_distance_not_duration_is_what_matters():
    """펄스 길이를 시간으로 고정하면 느린 로봇이 기어간다.

    실기(pinky)에서 모터 불감대 때문에 lin_pulse 를 0.05m/s 로 낮췄더니
    한 펄스가 8.3mm, 정지 0.9초를 더한 실효 속도가 7.8mm/s 가 됐다.
    게이트~정지 구간 0.5m 에 64초 — 기본 제한시간 60초를 넘겨
    도착 직전에 timeout 으로 중단됐다.

    pi/setup.sh 가 펄스 길이를 '거리 2cm' 기준으로 계산해 넣는 이유가 이것이다.
    """
    def effective(lin, pulse):
        c = MarkerDriveConfig(lin_pulse=lin, move_pulse_s=pulse).clamped()
        return c.lin_pulse * c.move_pulse_s / (c.move_pulse_s + c.move_pause_s)

    slow_fixed = effective(0.05, 0.10)            # 시간 고정 — 옛 방식
    slow_scaled = effective(0.05, 0.02 / 0.05)    # 거리 기준 — setup.sh 방식
    assert 0.5 / slow_fixed > 60, "이 시나리오가 60초를 안 넘으면 회귀를 못 잡는다"
    assert 0.5 / slow_scaled < 45, f"거리 기준으로도 느리다: {0.5/slow_scaled:.0f}초"


def test_pulse_shorter_than_two_ticks_is_raised():
    """/cmd_vel 은 다음 명령까지 유지된다. 한 틱보다 짧은 펄스는 설정대로 안 움직인다."""
    c = MarkerDriveConfig(move_pulse_s=0.01, loop_hz=12.0).clamped()
    assert c.move_pulse_s >= 2.0 / 12.0
