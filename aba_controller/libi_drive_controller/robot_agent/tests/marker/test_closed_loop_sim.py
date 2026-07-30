"""폐루프 시뮬레이션 — 렌더 → 검출 → 상태기계 → 로봇 이동 → 다시 렌더.

단위 테스트는 상태기계에 **가짜 관측값**을 넣어 전이만 본다. 그건 제어법이 실제로
수렴하는지는 말해 주지 않는다. 여기서는 로봇 자세로부터 카메라 영상을 실제로 투영해
그리고, 그 영상을 같은 검출기로 풀어서, 나온 명령으로 로봇을 움직인다.

그래서 이 파일이 잡는 것:
- 조향 극성이 맞을 때 정말 수렴하는가(그리고 틀리면 정말 발산하는가)
- 정지 위치가 목표(마커 앞 10cm)에 실제로 들어오는가
- 도착했을 때 몸이 벽과 정렬돼 있는가

카메라 좌표 약속은 이상적이다(장착 오차 없음). 실기의 극성은 이 시뮬레이션으로
정할 수 없다 — 그게 steer_sign 이 현장 값인 이유다. 대신 **둘 중 하나만 수렴한다**는
사실을 확인해서, 그 플래그 하나가 올바른 조정 지점임을 보인다.
"""
import math

import cv2
import numpy as np

from app.marker.approach import MarkerApproach
from app.marker.config import MarkerDriveConfig
from app.marker.detect import detect_marker, make_marker_image, marker_object_points

W, H = 640, 480
FX = FY = 609.2
K = np.array([[FX, 0, W / 2], [0, FY, H / 2], [0, 0, 1]], dtype=np.float64)
DIST = np.zeros(5, dtype=np.float64)
LEN_M = 0.07
DICT = "DICT_5X5_100"
SIDE_PX, PAD = 300, 60
_MARKER = make_marker_image(DICT, 1, SIDE_PX)
_CANVAS = np.full((SIDE_PX + 2 * PAD, SIDE_PX + 2 * PAD), 255, dtype=np.uint8)
_CANVAS[PAD:PAD + SIDE_PX, PAD:PAD + SIDE_PX] = _MARKER
_SRC = np.array([[PAD, PAD], [PAD + SIDE_PX, PAD],
                 [PAD + SIDE_PX, PAD + SIDE_PX], [PAD, PAD + SIDE_PX]], dtype=np.float32)


class Robot:
    """마커 좌표계 위의 2D 로봇.

    lat  : 마커 법선축에서의 좌우 이탈(m). 마커 로컬 +X 방향이 양수.
    dist : 마커 평면까지의 거리(m).
    psi  : 카메라 광축이 법선에서 벌어진 각(rad). 0 이면 마커를 정면으로 마주본다.
    """

    def __init__(self, lat: float, dist: float, psi: float = 0.0):
        self.lat, self.dist, self.psi = lat, dist, psi
        self.forward_m = 0.0

    @property
    def odom_yaw_deg(self) -> float:
        """ROS odom 관례의 방위각.

        psi 는 '광축이 법선에서 벌어진 각'이라 좌회전(angular>0)에 감소한다.
        odom yaw 는 좌회전에 증가하므로 부호를 뒤집어 넘긴다.
        """
        return -math.degrees(self.psi)

    def axis(self) -> np.ndarray:
        # 광축: psi=0 이면 (0,0,-1) — z 가 줄어드는 쪽(마커 쪽)을 본다
        return np.array([math.sin(self.psi), 0.0, -math.cos(self.psi)])

    def move(self, linear: float, angular: float, dt: float, turn_sign: float) -> None:
        """ROS 관례: angular.z > 0 = 좌회전(위축 기준 CCW).

        광축을 axis(psi) = (sin psi, 0, -cos psi) 로 뒀으므로, 위축(+Y) 기준 CCW 회전
        R_y(t)*(0,0,-1) = (-sin t, 0, -cos t) 과 맞추려면 **psi 는 감소**해야 한다.
        turn_sign=-1 은 배선이 반대로 된 하드웨어를 흉내 낸다.
        """
        a = self.axis()
        self.lat += linear * dt * a[0]
        self.dist += linear * dt * a[2]
        self.psi -= turn_sign * angular * dt
        self.forward_m += linear * dt

    def render(self):
        C = np.array([self.lat, 0.0, self.dist])
        z_cam = self.axis()
        y_cam = np.array([0.0, -1.0, 0.0])
        x_cam = np.cross(y_cam, z_cam)
        x_cam /= np.linalg.norm(x_cam)
        y_cam = np.cross(z_cam, x_cam)
        R = np.stack([x_cam, y_cam, z_cam])
        t = -R @ C
        if t[2] <= 0.02:                       # 마커가 카메라 뒤/너무 가까움
            return None
        rvec, _ = cv2.Rodrigues(R)
        dst, _ = cv2.projectPoints(marker_object_points(LEN_M), rvec, t, K, DIST)
        dst = dst.reshape(4, 2).astype(np.float32)
        if not np.isfinite(dst).all() or np.abs(dst).max() > 1e4:
            return None
        warp = cv2.getPerspectiveTransform(_SRC, dst)
        out = cv2.warpPerspective(_CANVAS, warp, (W, H),
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        return np.dstack([out] * 3)


def run_sim(steer_sign=1.0, turn_sign=1.0, start=(0.12, 0.9, 0.0), max_ticks=1200,
            cfg_kw=None):
    """수렴할 때까지 돌리고 (마지막 Cmd, 로봇, 틱 수) 를 돌려준다."""
    kw = dict(steer_sign=steer_sign, stop_m=0.10, front_offset_m=0.0,
              timeout_s=1e9, align_stall_s=1e9, search_step_timeout_s=1e9,
              move_pause_s=0.15, lin_homing=0.10, lin_pulse=0.05)
    kw.update(cfg_kw or {})
    cfg = MarkerDriveConfig(**kw)
    m = MarkerApproach(cfg)
    r = Robot(*start)
    dt = 1.0 / cfg.loop_hz
    cmd, t = None, 0.0
    for i in range(max_ticks):
        frame = r.render()
        obs = (detect_marker(frame, K, DIST, marker_len_m=LEN_M, target_id=1,
                             dict_name=DICT) if frame is not None else None)
        cmd = m.step(obs, yaw_deg=r.odom_yaw_deg, forward_m=r.forward_m,
                     front_m=max(r.dist, 0.0), now_s=t)
        if cmd.done:
            return cmd, r, i
        r.move(cmd.linear, cmd.angular, dt, turn_sign)
        t += dt
    return cmd, r, max_ticks


def test_converges_to_target_when_polarity_matches():
    """제어법이 실제로 수렴해서 마커 앞 10cm 에 선다."""
    cmd, robot, ticks = run_sim(steer_sign=1.0, turn_sign=1.0)
    assert cmd.done and cmd.phase == "DONE", f"{cmd.phase}/{cmd.reason} after {ticks} ticks"
    assert abs(robot.dist - 0.10) < 0.03, f"정지 거리 {robot.dist:.3f}m"
    assert abs(robot.lat) < 0.06, f"축이탈 {robot.lat:.3f}m"
    assert abs(math.degrees(robot.psi)) < 15.0, f"정면각 {math.degrees(robot.psi):.1f}deg"


def test_wrong_polarity_is_dramatically_worse():
    """극성이 반대면 결국 도착은 해도 훨씬 오래 걸리고 몸이 크게 돈다.

    실측(이 시뮬레이션): 정상 217틱·순회전 -6°, 반대 887틱·순회전 353°.
    탐색이 어떻게든 마커를 다시 잡아 주기 때문에 '영영 못 간다'가 아니라
    '헤매다 간다'가 실제 증상이다. 현장에서 이 모습이 보이면 --steer-sign 을 뒤집는다.
    """
    ok_cmd, ok_robot, ok_ticks = run_sim(steer_sign=1.0, turn_sign=1.0)
    bad_cmd, bad_robot, bad_ticks = run_sim(steer_sign=1.0, turn_sign=-1.0)
    assert ok_cmd.done and ok_cmd.phase == "DONE"
    assert ok_ticks < 400 and abs(math.degrees(ok_robot.psi)) < 30.0
    assert bad_ticks > 2 * ok_ticks or abs(math.degrees(bad_robot.psi)) > 90.0, (
        f"정상 {ok_ticks}틱/{math.degrees(ok_robot.psi):.0f}° vs "
        f"반대 {bad_ticks}틱/{math.degrees(bad_robot.psi):.0f}° — 차이가 없다")


def test_flipping_steer_sign_recovers_from_reversed_hardware():
    """하드웨어 극성이 반대여도 steer_sign 만 뒤집으면 다시 수렴해야 한다."""
    cmd, robot, _ = run_sim(steer_sign=-1.0, turn_sign=-1.0)
    assert cmd.done and cmd.phase == "DONE", f"{cmd.phase}/{cmd.reason}"
    assert abs(robot.dist - 0.10) < 0.03
    assert abs(robot.lat) < 0.06


def test_converges_from_larger_offset():
    """더 크게 벗어난 자리에서도 축에 올라타 도착한다."""
    cmd, robot, _ = run_sim(start=(0.18, 1.2, 0.0))
    assert cmd.done and cmd.phase == "DONE", f"{cmd.phase}/{cmd.reason}"
    assert abs(robot.dist - 0.10) < 0.04
    assert abs(robot.lat) < 0.08


def test_too_large_offset_fails_safely_instead_of_docking_crooked():
    """활주로가 모자라면 비뚤게 도착하지 않고 멈춘다.

    기본 게인(steer_ang_max 0.08rad/s)으로는 1.1m 거리에서 25cm 이탈을 다 못 지운다.
    그 경우 조용히 비뚤게 붙는 것보다 이유를 남기고 서는 편이 낫다.
    현장에서 이 증상이 보이면 출발 위치를 축에 가깝게 잡거나 게인을 올린다.
    """
    cmd, robot, _ = run_sim(start=(0.25, 1.1, 0.0))
    assert cmd.done
    if cmd.phase == "DONE":                      # 도착했다면 비뚤지 않아야 한다
        assert abs(robot.lat) < 0.08, f"비뚤게 도착 lat={robot.lat:+.3f}"
    else:
        assert cmd.reason in ("lost_misaligned", "align_stall",
                              "final_align_failed"), cmd.reason


def test_scan_guard_stops_before_the_wall():
    """전방 감시 거리를 크게 잡으면 목표보다 먼저 멈춘다(안전장치가 실제로 작동)."""
    cmd, robot, _ = run_sim(cfg_kw={"scan_guard_m": 0.30})
    assert cmd.done and cmd.phase == "ABORT" and cmd.reason == "scan_guard"
    assert robot.dist >= 0.28
