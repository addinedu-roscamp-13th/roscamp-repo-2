from .pid import FollowPID
from .lidar_avoidance import apply_avoidance


class TrackingController:
    """PID + LiDAR avoidance -> publish cmd_vel. Records last turn direction (LKD)."""

    def __init__(self, publish, cfg):
        self.publish = publish
        self.cfg = cfg
        self.pid = FollowPID(cfg)
        self.last_direction = None

    def step(self, detection, scan, dt, forward_blocked=False):
        """`forward_blocked` 면 **방위만** 낸다(전진·후진 0).

        측면 자세에서 쓴다 — 몸을 돌리면 bbox 폭이 줄어 실제보다 멀어 보이므로
        거리(√area)는 못 믿지만, 방위(cx)는 그 영향을 안 받는다. 2026-07-26 설계의
        "전진 금지 · 방위 PID 만 유지" 가 이것이다(control_loop 의 호출부 주석 참고).

        같은 경로를 쓰는 이유: 라이다 회피와 LKD 기록이 그대로 따라온다. 여기서
        따로 계산하면 회복 탐색이 방향을 잃는다.
        """
        lin, ang = self.pid.compute(detection.cx, detection.area, dt,
                                    image_width=getattr(detection, "image_width", 0))
        if forward_blocked:
            lin = 0.0
        lin, ang = apply_avoidance(lin, ang, scan, self.cfg)
        if abs(ang) > 0.01:
            self.last_direction = 1.0 if ang > 0 else -1.0
        self.publish(lin, ang)

    def reset(self):
        self.pid.reset()
        self.last_direction = None
