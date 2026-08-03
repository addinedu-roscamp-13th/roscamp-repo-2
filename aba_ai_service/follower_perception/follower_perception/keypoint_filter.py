"""One-Euro 필터 — 키포인트 지터를 깎되 실제 움직임은 안 늦춘다.

Casiez, Roussel, Vogel, "1€ Filter: A Simple Speed-based Low-pass Filter for
Noisy Input in Interactive Systems", CHI 2012.

## EMA 를 안 쓰는 이유

EMA 는 고정 지연이라 사람이 실제로 움직일 때 그만큼 늦는다. 15fps 에서 한
프레임이 66.7ms 라 그 지연이 비싸다. One-Euro 는 **속도에 따라 차단 주파수를
올려서** 정지 구간에서만 세게 깎는다.

## 기본은 꺼짐이다

`PoseEstimator` 는 `pose_calib.json` 의 `filter` 가 있을 때만 이 필터를 만든다
(Task 6). 필터를 켜면 판정 좌표가 바뀌어 "설정이 없으면 예전과 같은 판정"
이라는 회귀 방어선이 깨진다. 켤지는 벤치마크가 모델별 지터를 낸 뒤에 정한다.
"""
import math

import numpy as np


def _alpha(cutoff, dt):
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuro:
    """스칼라 하나를 거른다. 좌표축마다 하나씩 쓴다."""

    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.reset()

    def reset(self):
        self._x = None
        self._dx = 0.0

    def filter(self, x, dt):
        x = float(x)
        if dt <= 0:
            dt = 1e-3
        if self._x is None:
            # 첫 표본은 그대로 통과한다 — 비교할 과거가 없다. 0 으로 시작하면
            # 화면 왼쪽 위에서 날아오는 골격이 몇 프레임 보인다.
            self._x = x
            return x
        dx = (x - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        # 속도가 클수록 cutoff 를 올려 차단을 푼다 — 빠른 움직임은 안 늦춘다.
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = _alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        return self._x


class KeypointFilter:
    """(N,2) 키포인트 배열을 거른다. 점마다 축(x/y) 필터를 하나씩 둔다."""

    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0, conf_min=0.5,
                 n_points=17):
        self.conf_min = float(conf_min)
        self._f = [[OneEuro(min_cutoff, beta, d_cutoff) for _ in range(2)]
                   for _ in range(n_points)]

    def reset(self):
        for pair in self._f:
            for f in pair:
                f.reset()

    def apply(self, xy, conf, dt):
        """신뢰도를 통과한 점만 거른다. 미달 점은 원본 그대로 두고 상태를 리셋한다.

        난수 좌표를 필터에 먹이면 상태가 오염돼, 그 점이 되살아난 뒤에도 몇
        프레임 동안 엉뚱한 값을 낸다.
        """
        xy = np.asarray(xy, dtype=float)
        conf = np.asarray(conf, dtype=float)
        out = xy.copy()          # 입력 배열은 건드리지 않는다
        for i in range(min(len(xy), len(self._f))):
            if conf[i] < self.conf_min:
                self._f[i][0].reset()
                self._f[i][1].reset()
                continue
            out[i][0] = self._f[i][0].filter(xy[i][0], dt)
            out[i][1] = self._f[i][1].filter(xy[i][1], dt)
        return out
