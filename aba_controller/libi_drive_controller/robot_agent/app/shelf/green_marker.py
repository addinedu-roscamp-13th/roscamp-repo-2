"""서가 선반 위 청록 막대의 화면 중점을 찾는다.

## 왜 학습하지 않나

흰 서가 위의 단색 물체 하나다. 색 임계로 충분하고, 모델을 만들면 로봇 5대에 가중치를
배포·동기화해야 한다. 임계가 실제 조명에서 견디는지는 실기에서만 판정된다 —
`GreenConfig` 로 값을 밖에서 조정할 수 있게 둔다.

## 흰 테두리 검증 (2026-08-05, 실측으로 위쪽만 본다)

옆 서가의 표식이나 반사가 같이 잡힐 수 있다 — 예전엔 "가장 큰 덩어리"로만 걸렀는데,
반사가 실제 마커보다 크게 찍히면 그대로 속는다. 실제 마커는 흰 선반 판 바로 아래
붙어있어 **바로 위가 흰색**이라는 구조적 특징이 있다 — 눈으로는 "흰-초록-흰"으로
보이지만, 실측(2026-08-05, 실제 도킹 영상 3프레임 픽셀 HSV 직접 측정)으로는
**아래쪽은 선반 턱 그늘로 오히려 어둡다**(V≈27~45, 위쪽 V≈134~172와 뚜렷이 다름) —
그래서 "위/아래 둘 다 흰색"을 요구했던 첫 버전은 실전에서 한 번도 안 걸리고 항상
안전망(가장 큰 덩어리)으로만 떨어졌다. 위쪽 하나만 본다.
이 테두리가 있는 후보만 먼저 거르고, 그중 가장 큰 걸 고른다(엣지/휘도 기반
필터링 — 산업 머신비전의 shape/edge-based matching 과 같은 발상, ArUco 같은
학습·마커 없이 씀). 아무 후보도 못 채우면(마커가 화면 가장자리에 걸치는 등)
**가장 큰 덩어리로 그냥 떨어뜨린다** — 필터가 너무 빡빡해서 원래도 되던 검출을
막는 사고를 방지한다.
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
    #: 흰 테두리 검사에 볼 위쪽 밴드 높이 — 덩어리 높이의 이 비율만큼.
    white_band_frac: float = 0.5
    #: "흰색"으로 볼 기준 — 채도는 낮고(무채색) 명도는 높아야(밝음) 한다.
    #: 실측(2026-08-05, 도킹 영상 3프레임) 위쪽 밴드 V 134~172, S 35~56 — 여유 두고 잡음.
    white_s_max: int = 65
    white_v_min: int = 110


_DEFAULT = GreenConfig()


def _white_bordered(hsv, stats_row, cfg: GreenConfig) -> bool:
    """이 덩어리 바로 위가 흰색(저채도·고명도)인지. 옆 서가 반사처럼 흰 선반 판
    위에 안 붙어있는 초록 덩어리를 걸러내는 구조적 필터 — 색상 하나에만 안 기댄다.
    아래쪽은 실측으로 선반 턱 그늘이라 보지 않는다(모듈 머리말 "흰 테두리 검증" 참고)."""
    x, y, w, h = (int(stats_row[0]), int(stats_row[1]), int(stats_row[2]), int(stats_row[3]))
    band_h = max(2, int(round(h * cfg.white_band_frac)))
    above = hsv[max(0, y - band_h):y, x:x + w]
    if above.size == 0:
        return False
    return bool(above[:, :, 1].mean() <= cfg.white_s_max
                and above[:, :, 2].mean() >= cfg.white_v_min)


def detect_candidates(bgr, cfg: GreenConfig | None = None) -> list[dict]:
    """최소면적 넘는 초록 덩어리 전부를 박스+흰테두리 여부까지 담아 돌려준다.
    `centroid_uv()`가 이 함수 위에서 고르기만 하므로, 디버그 뷰어가 박스를 그려도
    실제 검출 판정과 절대 어긋나지 않는다(같은 마스크/같은 필터를 두 번 안 짠다)."""
    if bgr is None:
        return []
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
    out = []
    for i in range(1, n):    # 0 은 배경
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < cfg.min_area_px:
            continue
        x, y, w, h = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]))
        out.append({
            "x": x, "y": y, "w": w, "h": h, "area": area,
            "cx": float(centroids[i][0]), "cy": float(centroids[i][1]),
            "bordered": _white_bordered(hsv, stats[i], cfg),
        })
    return out


def centroid_uv(bgr, cfg: GreenConfig | None = None, hint_u: float | None = None):
    """BGR 프레임에서 초록 덩어리의 (가로, 세로) 중점 픽셀. 없으면 `None`.

    후보 중 바로 위가 흰색인(`_white_bordered`) 것들을 먼저 거른다 — 다 떨어지면
    그냥 전체 후보로 돌아간다(안전망). 그중 고르는 기준은:

    - `hint_u` 를 주면(직전 프레임의 필터된 u — 호출자가 계속 추적 중일 때) **그
      값에 가장 가까운 후보**를 고른다 — "면적 제일 큰 놈"만 보면 후보가 2개 이상일
      때 어느 게 이겼는지가 프레임마다 뒤바뀔 수 있다. 실측(2026-08-05, 도킹 영상
      2회): stable_frames 가 22/30까지 갔다가 한 프레임 만에 0으로 튀는 걸 확인했다
      — 마커는 그대로인데 값만 순간이동했다. 다른 후보로 갈아탄 것으로 보인다.
    - `hint_u` 가 없으면(첫 프레임, cold start) 예전처럼 가장 큰 걸 고른다.

    도킹 제어는 가로(u)만 쓴다(로봇은 yaw 로만 도니까) — `centroid_u()`가 그 값만
    돌려주는 이유다. 세로(v)는 실측(2026-08-05) 디버그 뷰어에 "실제로 어디를 보고
    있는지" 2D로 찍어 보여주기 위해 추가했다. 제어 로직엔 안 쓴다."""
    candidates = detect_candidates(bgr, cfg)
    if not candidates:
        return None

    bordered = [c for c in candidates if c["bordered"]]
    pool = bordered if bordered else candidates
    if hint_u is not None and len(pool) > 1:
        best = min(pool, key=lambda c: abs(c["cx"] - hint_u))
    else:
        best = max(pool, key=lambda c: c["area"])
    return best["cx"], best["cy"]


def centroid_u(bgr, cfg: GreenConfig | None = None, hint_u: float | None = None):
    """BGR 프레임에서 초록 덩어리의 **가로** 중점 픽셀. 없으면 `None`."""
    uv = centroid_uv(bgr, cfg, hint_u)
    return None if uv is None else uv[0]
