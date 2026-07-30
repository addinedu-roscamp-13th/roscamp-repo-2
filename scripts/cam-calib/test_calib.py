#!/usr/bin/env python3
"""카메라 없이 도는 자체 검증 — 알려진 K 를 되찾는지 확인한다.

    python3 test_calib.py

체커보드를 찍기 전에 파이프라인(objp 생성 / 코너 순서 / 배열 형상 / 저장)이 맞는지
먼저 확인하려고 만든 것이다. 40장을 다 찍고 나서 스크립트가 틀렸다는 걸 알면 늦다.

  test 1  투영만 — 알려진 K + 왜곡으로 점을 투영해 calibrateCamera 가 되찾는지
  test 2  렌더링 — 합성 체커보드 이미지를 만들어 calib_client.find_corners 로 검출까지 (왜곡 없음)
  test 3  ChArUco — 실제 촬영에 쓰는 경로(build_charuco → find_charuco)로 같은 검증
"""
import sys
import pathlib

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from calib_client import (build_charuco,         # noqa: E402  (검출 경로를 그대로 검증한다)
                          find_charuco, find_corners)

COLS, ROWS, SQ = 9, 6, 0.038                     # 내부 코너 9x6, 한 칸 38mm
SIZE = (480, 360)                                # picam 런타임 해상도
K_TRUE = np.array([[420.0, 0, 236.0],
                   [0, 418.5, 181.5],
                   [0, 0, 1.0]])
DIST_TRUE = np.array([-0.32, 0.11, 0.001, -0.002, 0.0])

# 자세 = (rx, ry, rz [deg], dx, dy [m], z [m]).
# dx/dy 는 "보드 중심을 광축에 맞춘 상태"에서의 오프셋이다. 보드 원점(첫 내부코너)
# 기준으로 쓰면 보드가 화면 밖으로 잘려 검출이 안 된다 — 실제로 여기서 한 번 틀렸다.

_POSES = [
    (0, 0, 0, 0.00, 0.00, 0.60), (25, 0, 0, 0.00, 0.02, 0.62),
    (-25, 0, 0, 0.02, -0.02, 0.58), (0, 30, 0, -0.03, 0.00, 0.60),
    (0, -30, 0, 0.03, 0.01, 0.64), (20, 20, 10, -0.06, 0.05, 0.75),
    (-20, 25, -10, 0.07, -0.06, 0.80), (15, -25, 5, -0.06, 0.06, 0.72),
    (30, 10, -15, 0.05, 0.05, 0.90), (-15, -15, 20, -0.05, -0.05, 0.85),
    (10, 35, 0, 0.04, -0.05, 0.70), (-30, -10, -5, -0.04, 0.05, 0.66),
    (0, 0, 0, 0.00, 0.00, 1.10), (18, -18, 0, 0.00, 0.00, 0.50),
]


def _objp():
    p = np.zeros((ROWS * COLS, 3), np.float32)
    p[:, :2] = np.mgrid[0:COLS, 0:ROWS].T.reshape(-1, 2)
    return p * SQ


def _rt(pose):
    rx, ry, rz, dx, dy, z = pose
    rvec = np.deg2rad(np.array([rx, ry, rz], float)).reshape(3, 1)
    R, _ = cv2.Rodrigues(rvec)
    c = np.array([[(COLS - 1) / 2 * SQ], [(ROWS - 1) / 2 * SQ], [0.0]])   # 보드 중심(물체좌표)
    tvec = -R @ c + np.array([[dx], [dy], [z]])      # 회전 후에도 중심이 광축 위에 오게
    return rvec, tvec


def _report(name, K, dist, rms, check_dist):
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    ef = abs(fx - K_TRUE[0, 0]) / K_TRUE[0, 0] * 100
    ec = np.hypot(cx - K_TRUE[0, 2], cy - K_TRUE[1, 2])
    print(f"\n[{name}] RMS={rms:.4f}px")
    print(f"  fx {fx:8.2f} (참값 {K_TRUE[0,0]:.2f}, 오차 {ef:.3f}%)")
    print(f"  fy {fy:8.2f} (참값 {K_TRUE[1,1]:.2f})")
    print(f"  cx,cy ({cx:.2f}, {cy:.2f}) (참값 ({K_TRUE[0,2]:.1f}, {K_TRUE[1,2]:.1f}), 거리 {ec:.3f}px)")
    ok = ef < 1.0 and ec < 2.0
    if check_dist:
        ed = float(np.max(np.abs(dist.ravel()[:2] - DIST_TRUE[:2])))
        print(f"  dist {np.array2string(dist.ravel(), precision=4)} (k1,k2 최대오차 {ed:.4f})")
        ok = ok and ed < 0.02
    print(f"  → {'PASS' if ok else 'FAIL'}")
    return ok


def test_projection():
    objp = _objp()
    obj_pts, img_pts = [], []
    for pose in _POSES:
        rvec, tvec = _rt(pose)
        img, _ = cv2.projectPoints(objp, rvec, tvec, K_TRUE, DIST_TRUE)
        if np.any(img < 0) or np.any(img[:, 0, 0] > SIZE[0]) or np.any(img[:, 0, 1] > SIZE[1]):
            continue                              # 화면 밖으로 나간 자세는 버린다
        obj_pts.append(objp.copy())
        img_pts.append(img.astype(np.float32))
    print(f"test 1: {len(obj_pts)} 자세 사용")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, SIZE, None, None)
    return _report("test 1 · 투영", K, dist, rms, check_dist=True)


def _render(pose, ppm=4000):
    """체커보드 평면을 호모그래피로 워프해 합성 프레임을 만든다(왜곡 없음)."""
    sq_px = int(SQ * ppm)
    bw, bh = (COLS + 1) * sq_px, (ROWS + 1) * sq_px
    board = np.zeros((bh, bw), np.uint8)
    for j in range(ROWS + 1):
        for i in range(COLS + 1):
            if (i + j) % 2 == 0:
                board[j * sq_px:(j + 1) * sq_px, i * sq_px:(i + 1) * sq_px] = 255
    board = cv2.copyMakeBorder(board, sq_px, sq_px, sq_px, sq_px, cv2.BORDER_CONSTANT, value=255)

    rvec, tvec = _rt(pose)
    R, _ = cv2.Rodrigues(rvec)
    # 보드 이미지 픽셀 (u,v) → 평면 좌표. (0,0) 픽셀이 첫 내부코너보다 한 칸+테두리 앞이다.
    off = -2 * sq_px / ppm
    S = np.array([[1 / ppm, 0, off], [0, 1 / ppm, off], [0, 0, 1]])
    H = K_TRUE @ np.hstack([R[:, :2], tvec]) @ S
    return cv2.warpPerspective(board, H, SIZE, borderValue=128)


def test_render():
    objp = _objp()
    obj_pts, img_pts = [], []
    miss = 0
    for pose in _POSES:
        gray = _render(pose)
        found, corners = find_corners(gray, (COLS, ROWS))
        if not found:
            miss += 1
            continue
        obj_pts.append(objp.copy())
        img_pts.append(corners.astype(np.float32))
    print(f"\ntest 2: {len(obj_pts)}/{len(_POSES)} 검출 (미검출 {miss})")
    if len(obj_pts) < 6:
        print("  → FAIL (검출이 너무 적다 — find_corners 경로 문제)")
        return False
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, SIZE, None, None)
    return _report("test 2 · 렌더링+검출", K, dist, rms, check_dist=False)


#: make_board.py 기본값과 같아야 한다 — 여기가 어긋나면 시험은 통과해도 실물이 안 맞는다.
CH_SQUARES, CH_SQ_M, CH_DICT = (7, 5), 0.035, "DICT_4X4_50"

#: 화면 가장자리로 보드를 밀어낸 자세 — **주점(cx,cy)을 잡는 건 이 장면들이다.**
#: 가운데서만 찍으면 fx 는 맞아도 주점이 2px 씩 흔들린다(실제 촬영에서도 똑같다).
#: 체커보드(test 2)는 보드가 잘리면 통째로 버려져 이 자세를 못 쓴다 — ChArUco 라서 쓴다.
_POSES_EDGE = [
    (0, 0, 0, 0.11, 0.00, 0.50), (0, 0, 0, -0.11, 0.00, 0.50),
    (0, 0, 0, 0.00, 0.09, 0.50), (0, 0, 0, 0.00, -0.09, 0.50),
    (20, 0, 0, 0.09, 0.07, 0.55), (-20, 0, 0, -0.09, -0.07, 0.55),
    (0, 25, 0, 0.09, -0.07, 0.55), (0, -25, 0, -0.09, 0.07, 0.55),
]


def test_charuco():
    """ChArUco 보드를 합성 렌더 → find_charuco → calibrateCamera.

    체커보드(test 2)와 달리 **보드가 화면 밖으로 잘린 장면도 쓴다** — 부분 검출이 되는 게
    ChArUco 를 쓰는 이유라, 잘린 장면에서 코너가 안 나오면 그 자체가 회귀다.
    """
    board, dictionary = build_charuco(CH_DICT, CH_SQUARES, CH_SQ_M, CH_SQ_M * 0.75)
    ppm = 4000
    bw, bh = int(CH_SQUARES[0] * CH_SQ_M * ppm), int(CH_SQUARES[1] * CH_SQ_M * ppm)
    img = (board.generateImage((bw, bh)) if hasattr(board, "generateImage")
           else board.draw((bw, bh)))
    quiet = int(CH_SQ_M * ppm / 2)                    # 여백 없으면 가장자리 마커가 안 잡힌다
    img = cv2.copyMakeBorder(img, quiet, quiet, quiet, quiet, cv2.BORDER_CONSTANT, value=255)

    # ⚠️ 마커 비트는 칸보다 훨씬 작아서 480x360 으로 곧장 워프하면 앨리어싱 편향이 0.7px
    # 씩 실린다(실측). 그러면 검출 경로가 멀쩡해도 fx 가 2% 틀어져 FAIL 로 보인다.
    # 4배로 그린 뒤 INTER_AREA 로 줄여 실제 센서의 면적 적분에 가깝게 만든다 → 0.09px.
    ss = 4
    K_ss = K_TRUE.copy()
    K_ss[:2, :] *= ss

    obj_pts, img_pts, miss, partial = [], [], 0, 0
    poses = _POSES + _POSES_EDGE
    for pose in poses:
        rvec, tvec = _rt_charuco(pose)
        R, _ = cv2.Rodrigues(rvec)
        off = -quiet / ppm                            # 렌더 이미지 (0,0) 은 보드 원점보다 여백만큼 앞
        S = np.array([[1 / ppm, 0, off], [0, 1 / ppm, off], [0, 0, 1]])
        H = K_ss @ np.hstack([R[:, :2], tvec]) @ S
        big = cv2.warpPerspective(img, H, (SIZE[0] * ss, SIZE[1] * ss), borderValue=128)
        gray = cv2.resize(big, SIZE, interpolation=cv2.INTER_AREA)

        found, corners, objp, _ids = find_charuco(gray, board, dictionary)
        if not found:
            miss += 1
            continue
        if len(corners) < (CH_SQUARES[0] - 1) * (CH_SQUARES[1] - 1):
            partial += 1
        obj_pts.append(objp)
        img_pts.append(corners.astype(np.float32))

    print(f"\ntest 3: {len(obj_pts)}/{len(poses)} 검출 (미검출 {miss}, 부분검출 {partial})")
    if len(obj_pts) < 6:
        print("  → FAIL (검출이 너무 적다 — find_charuco 경로 문제)")
        return False
    if partial == 0:
        print("  ! 부분검출 장면이 하나도 없다 — 부분 검출 경로가 시험되지 않았다")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, SIZE, None, None)
    return _report("test 3 · ChArUco", K, dist, rms, check_dist=False)


def _rt_charuco(pose):
    """_rt 와 같지만 ChArUco 보드 기준(원점=보드 좌하단 모서리, 크기=칸수x칸크기)."""
    rx, ry, rz, dx, dy, z = pose
    rvec = np.deg2rad(np.array([rx, ry, rz], float)).reshape(3, 1)
    R, _ = cv2.Rodrigues(rvec)
    c = np.array([[CH_SQUARES[0] * CH_SQ_M / 2], [CH_SQUARES[1] * CH_SQ_M / 2], [0.0]])
    return rvec, -R @ c + np.array([[dx], [dy], [z]])


if __name__ == "__main__":
    ok1 = test_projection()
    ok2 = test_render()
    ok3 = test_charuco()
    ok = ok1 and ok2 and ok3
    print("\n" + ("전체 PASS — 촬영해도 됩니다." if ok else "★ FAIL — 촬영 전에 고쳐야 합니다."))
    sys.exit(0 if ok else 1)
