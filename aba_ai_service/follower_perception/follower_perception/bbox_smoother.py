import numpy as np

from .constants import SMOOTHER_ALPHA, SMOOTHER_BETA


class BBoxSmoother:
    """Alpha-beta filter over [cx, cy, area]; predicts during detection gaps."""

    def __init__(self, alpha=SMOOTHER_ALPHA, beta=SMOOTHER_BETA):
        self.alpha = alpha
        self.beta = beta
        self.state = None            # np.array([cx, cy, area])
        self.velocity = np.zeros(3)

    def update(self, cx, cy, area, dt):
        z = np.array([cx, cy, area], dtype=float)
        if self.state is None or dt <= 0:
            self.state = z
            self.velocity = np.zeros(3)
            return
        pred = self.state + self.velocity * dt
        residual = z - pred
        self.state = pred + self.alpha * residual
        self.velocity = self.velocity + (self.beta / dt) * residual

    def predict(self, dt):
        if self.state is None:
            return None
        p = self.state + self.velocity * dt
        return float(p[0]), float(p[1]), float(p[2])

    def reset(self):
        self.state = None
        self.velocity = np.zeros(3)
