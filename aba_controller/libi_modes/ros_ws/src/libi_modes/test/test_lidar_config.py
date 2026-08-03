"""라이다 도킹 설정.

`clamped()` 가 하는 일은 하나다: **현장에서 잘못 적은 값이 로봇을 도크에 박게 두지
않는 것.** params.yaml 은 사람이 손으로 고치는 파일이고, 여기 들어오는 값 중 몇 개는
물리적으로 하한이 있다(모터 데드밴드 등).
"""
from libi_modes.lidar.config import LidarDockConfig


def test_defaults_are_physically_sane():
    c = LidarDockConfig()
    assert c.v_near_mps == 0.05, "실측 모터 데드밴드. 이 아래로는 바퀴가 안 돈다"
    assert c.v_far_mps > c.v_near_mps
    assert c.pulse_dist_m < c.v_far_dist_m
    assert c.notch_depth_min_m < 0.025 < c.notch_depth_max_m


def test_clamped_raises_speed_up_to_the_motor_deadband():
    """0.05 아래로 적으면 명령은 20Hz 로 나가는데 바퀴가 안 돈다 — 조용히 안 움직인다."""
    c = LidarDockConfig(v_near_mps=0.01, v_far_mps=0.02).clamped()
    assert c.v_near_mps == 0.05
    assert c.v_far_mps >= c.v_near_mps


def test_clamped_keeps_pulse_length_above_the_minimum_step():
    c = LidarDockConfig(pulse_min_s=0.0).clamped()
    assert c.pulse_min_s >= 0.10


def test_clamped_forces_final_yaw_gain_to_zero():
    """최종 국면에는 벽이 min range 아래로 사라져 yaw 를 잴 수 없다.
    죽은 값을 믿는 것은 안 보는 것보다 나쁘다."""
    c = LidarDockConfig(k_yaw_final=5.0).clamped()
    assert c.k_yaw_final == 0.0


def test_from_params_ignores_none_and_unknown_keys():
    c = LidarDockConfig.from_params({"stop_m": 0.07, "k_y": None, "무슨키": 1})
    assert c.stop_m == 0.07
    assert c.k_y == LidarDockConfig().k_y
