"""화면 가로 픽셀을 로봇 정면 기준 방위각으로 바꾼다. **카메라를 열지 않는다.**

## ⚠️ 보정값은 보정을 뜬 해상도에서만 맞는다

앞캠 보정 파일은 `picam_640x480_rot180.npz`(640×480)인데, 실제 스트림은
`scripts/all/libi_pi.sh:484` 가 `--width 320` 으로 띄워 **320×240** 이다. 그대로 쓰면
`fx`·`cx` 가 두 배 틀려 각도가 두 배가 된다. `app/marker/calib.py` 머리말이 같은 사고를
"거리는 그럴듯한데 정렬만 한쪽으로 흐르는, 가장 찾기 어려운 버그" 라고 적어 두었다.

K 는 해상도에 선형이므로 폭 비율을 그대로 곱하면 된다. 왜곡계수는 정규화된 값이라
스케일하지 않는다.

## ⚠️ 왜곡 보정을 안 한다 (보류 — 2026-08-04 codex 리뷰 P2)

`bearing_rad` 는 `u` 를 **원시(왜곡된) 픽셀**로 받아 pinhole 모델(`atan2`)에 바로
넣는다. 실제 렌즈는 왜곡이 있어 화면 가장자리로 갈수록(중심에서 멀수록) 이 각이
실제보다 더 틀어진다 — 중심 부근(표식이 화면 가운데 가까이 있을 때)은 오차가
작고, 가장자리로 갈수록 커진다. 맞는 지적이지만 이번 작업 범위 밖이다: 실기에서
접근 오차를 먼저 재고, 그게 목표를 넘으면 그때 고치기로 사용자와 정했다(D14).
승급 경로: `cv2.undistortPoints` 로 `u` 를 먼저 왜곡 보정한 뒤 `bearing_rad` 에
넣는다(왜곡계수는 `app/marker/calib.py` 가 이미 로드한다).
"""
from __future__ import annotations

import math


def scale_k(k, calib_width: int, frame_width: int):
    """`(fx, fy, cx, cy)` 를 실제 프레임 폭에 맞춘다."""
    s = float(frame_width) / float(calib_width)
    fx, fy, cx, cy = k
    return (fx * s, fy * s, cx * s, cy * s)


def bearing_rad(u: float, fx: float, cx: float) -> float:
    """가로 픽셀 `u` 가 가리키는 방향의 광축 기준 각(rad).

    `u > cx` 는 화면 오른쪽이고, 로봇 기준으로도 오른쪽이므로 양수다. 호출자는 이 값을
    로봇 yaw 에서 **빼서** 맵 프레임 방향을 만든다(오른쪽 = yaw 감소).
    """
    return math.atan2(float(u) - float(cx), float(fx))
