"""프레임 한 장 → MarkerObs.

자세 추정은 IPPE_SQUARE 고정이다. aba_project 의 기존 도킹 구현이
"estimatePoseSingleMarkers(ITERATIVE)는 작은 마커에서 yaw 부호가 튀어 축 접근을
망친다"는 이유로 이미 고른 방식이다.

cv2.aruco API 는 4.6 과 4.7+ 가 다르다(ArucoDetector / generateImageMarker 는 4.7+).
로봇과 노트북의 설치 버전이 다를 수 있으므로 양쪽을 지원한다.
"""
import math

import cv2
import numpy as np

from .types import MarkerObs

DICTS = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
}

# solvePnP 에 넘기는 마커 코너 순서(TL, TR, BR, BL) — detectMarkers 반환 순서와 같다.
_CORNER_ORDER = ((-1, 1), (1, 1), (1, -1), (-1, -1))


def _dictionary(dict_name: str):
    if dict_name not in DICTS:
        raise ValueError(f"지원하지 않는 사전: {dict_name} (가능: {', '.join(DICTS)})")
    ident = DICTS[dict_name]
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(ident)
    return cv2.aruco.Dictionary_get(ident)


def _params():
    p = (cv2.aruco.DetectorParameters() if hasattr(cv2.aruco, "ArucoDetector")
         else cv2.aruco.DetectorParameters_create())
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return p


def _detect_raw(gray, dict_name: str):
    dictionary, params = _dictionary(dict_name), _params()
    if hasattr(cv2.aruco, "ArucoDetector"):                              # 4.7+
        return cv2.aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=params)  # 4.6


def marker_object_points(marker_len_m: float) -> np.ndarray:
    """마커 로컬 좌표계의 코너 4점. X=수평, Y=수직, Z=법선(카메라 쪽)."""
    s = marker_len_m / 2.0
    return np.array([[x * s, y * s, 0.0] for x, y in _CORNER_ORDER], dtype=np.float32)


def make_marker_image(dict_name: str, marker_id: int, side_px: int):
    """마커 이미지(그레이스케일). 테스트와 인쇄용 생성 양쪽에 쓴다."""
    dictionary = _dictionary(dict_name)
    if hasattr(cv2.aruco, "generateImageMarker"):                # 4.7+
        return cv2.aruco.generateImageMarker(dictionary, marker_id, side_px)
    return cv2.aruco.drawMarker(dictionary, marker_id, side_px)  # 4.6


def _gray(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


def scan_dicts(frame) -> list[tuple[str, list[int]]]:
    """어떤 사전으로 만든 마커인지 모를 때 전부 훑어 알려 준다.

    사전이 틀리면 예외가 아니라 '검출 0개'로 조용히 실패하기 때문에 필요하다.
    """
    gray = _gray(frame)
    found = []
    for name in DICTS:
        _, ids, _ = _detect_raw(gray, name)
        if ids is not None and len(ids):
            found.append((name, sorted(int(i) for i in ids.flatten())))
    return found


def detect_all(frame, dict_name: str) -> list[tuple[int, np.ndarray]]:
    """그 사전에서 잡힌 마커 전부 [(id, 코너4점), ...]. 관찰용이다.

    detect_marker 는 대상 ID 하나만 보고 나머지는 버린다. 안 잡힌다고 할 때
    필요한 정보는 '아무것도 안 보인다'와 '보이는데 ID 가 다르다'의 구분이다.
    """
    _gray_frame = _gray(frame)
    corners, ids, _ = _detect_raw(_gray_frame, dict_name)
    if ids is None or len(ids) == 0:
        return []
    return [(int(i), c.reshape(4, 2)) for c, i in zip(corners, ids.flatten())]


def detect_marker(frame, K, dist, *, marker_len_m: float, target_id: int,
                  dict_name: str, max_reproj_px: float = 4.0) -> MarkerObs | None:
    """대상 ID 마커를 찾아 관측값을 만든다. 없으면 None.

    `lateral_m` 은 **마커 로컬 X 축(벽면 수평축) 위의 부호 있는 이탈**이다.
    카메라 중심을 마커 좌표계로 옮긴 값의 X 성분이며, 수직(Y) 성분은 버린다 —
    벽에 붙은 마커를 지상 로봇이 보는 상황에서 높이 차는 조향에 쓸 값이 아니다.
    (법선축까지의 기하학적 거리를 원한다면 hypot(X, Y) 여야 하지만, 그 값은
     카메라 장착 높이 때문에 항상 0 이 아니어서 조향 입력으로 못 쓴다.)
    """
    gray = _gray(frame)
    corners, ids, _ = _detect_raw(gray, dict_name)
    if ids is None or len(ids) == 0:
        return None
    hit = next((c for c, i in zip(corners, ids.flatten()) if int(i) == target_id), None)
    if hit is None:
        return None

    h, w = gray.shape[:2]
    pts = hit.reshape(4, 2).astype(np.float32)
    if min(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) < 8.0:
        return None                          # 8px 미만은 유령 검출로 버린다
    ex = (float(pts[:, 0].mean()) - w / 2.0) / (w / 2.0)
    side_px = float(np.mean([np.linalg.norm(pts[(i + 1) % 4] - pts[i]) for i in range(4)]))

    obj = marker_object_points(marker_len_m)
    ok, rvec, tvec = cv2.solvePnP(obj, pts, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        return None
    tvec = np.asarray(tvec, dtype=np.float64).reshape(3)
    if not np.isfinite(tvec).all() or tvec[2] <= 0.0:
        return None      # 카메라 뒤쪽 해는 물리적으로 불가능하다. 음수 거리를 그대로
        #                  내보내면 정지 조건(z <= stop_m)을 즉시 만족시켜 버린다.
    # 재투영 오차 게이트: 자세 해가 코너와 안 맞으면 그 pose 는 못 믿는다.
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    rms = float(np.sqrt((((proj.reshape(4, 2) - pts) ** 2).sum(axis=1)).mean()))
    if not math.isfinite(rms) or rms > max_reproj_px:
        return None
    R, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3))
    yaw = float(np.degrees(np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))))
    cam_in_marker = R.T @ -tvec              # 카메라 중심을 마커 좌표계로
    lateral = float(cam_in_marker[0])
    if not np.isfinite([tvec[2], yaw, lateral, ex]).all():
        return None
    return MarkerObs(marker_id=target_id, ex=ex, z_m=float(tvec[2]),
                     yaw_deg=yaw, lateral_m=lateral, size_frac=side_px / float(w))
