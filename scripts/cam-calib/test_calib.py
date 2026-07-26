#!/usr/bin/env python3
"""카메라 없이 도는 자체 검증 — 알려진 K 를 되찾는지 확인한다.

    python3 test_calib.py

체커보드를 찍기 전에 파이프라인(objp 생성 / 코너 순서 / 배열 형상 / 저장)이 맞는지
먼저 확인하려고 만든 것이다. 40장을 다 찍고 나서 스크립트가 틀렸다는 걸 알면 늦다.

  test 1  투영만 — 알려진 K + 왜곡으로 점을 투영해 calibrateCamera 가 되찾는지
  test 2  렌더링 — 합성 체커보드 이미지를 만들어 calib_client.find_corners 로 검출까지 (왜곡 없음)
"""
import sys
import pathlib

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from calib_client import find_corners            # noqa: E402  (검출 경로를 그대로 검증한다)

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


if __name__ == "__main__":
    ok1 = test_projection()
    ok2 = test_render()
    print("\n" + ("전체 PASS — 촬영해도 됩니다." if ok1 and ok2 else "★ FAIL — 촬영 전에 고쳐야 합니다."))
    sys.exit(0 if (ok1 and ok2) else 1)
