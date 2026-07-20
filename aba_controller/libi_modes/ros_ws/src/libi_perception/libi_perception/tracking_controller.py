from .pid import FollowPID
from .lidar_avoidance import apply_avoidance


class TrackingController:
    """PID + LiDAR avoidance -> publish cmd_vel. Records last turn direction (LKD)."""

    def __init__(self, publish, cfg):
        self.publish = publish
        self.cfg = cfg
        self.pid = FollowPID(cfg)
        self.last_direction = None

    def step(self, detection, scan, dt):
        lin, ang = self.pid.compute(detection.cx, detection.area, dt)
        lin, ang = apply_avoidance(lin, ang, scan, self.cfg)
        if abs(ang) > 0.01:
            self.last_direction = 1.0 if ang > 0 else -1.0
        self.publish(lin, ang)

    def reset(self):
        self.pid.reset()
        self.last_direction = None
