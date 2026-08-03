"""서가 선반 위 청록 막대의 화면 중점을 찾는다.

## 왜 학습하지 않나

흰 서가 위의 단색 물체 하나다. 색 임계로 충분하고, 모델을 만들면 로봇 5대에 가중치를
배포·동기화해야 한다. 임계가 실제 조명에서 견디는지는 실기에서만 판정된다 —
`GreenConfig` 로 값을 밖에서 조정할 수 있게 둔다.

## 왜 가장 큰 덩어리인가

옆 서가의 표식이나 반사가 같이 잡힐 수 있다. 가장 큰 것이 지금 마주 본 서가의 것이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GreenConfig:
    #: OpenCV HSV 색상(0~179). 청록~초록 구간.
    h_lo: int = 60
    h_hi: int = 95
    s_min: int = 80
    v_min: int = 60
    #: 이보다 작은 덩어리는 잡티로 본다(픽셀 수).
    min_area_px: int = 200


_DEFAULT = GreenConfig()


def centroid_u(bgr, cfg: GreenConfig | None = None):
    """BGR 프레임에서 가장 큰 초록 덩어리의 **가로** 중점 픽셀. 없으면 `None`."""
    if bgr is None:
        return None
    cfg = cfg or _DEFAULT

    import cv2
    import numpy as np

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([cfg.h_lo, cfg.s_min, cfg.v_min], dtype=np.uint8),
        np.array([cfg.h_hi, 255, 255], dtype=np.uint8),
    )
    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    best_i, best_area = -1, 0
    for i in range(1, n):                       # 0 은 배경
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area >= cfg.min_area_px and area > best_area:
            best_i, best_area = i, area
    if best_i < 0:
        return None
    return float(centroids[best_i][0])
