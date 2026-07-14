#!/usr/bin/env python3
"""체스보드로 카메라 내부파라미터(intrinsics)를 구해 config/camera_calib.npz 로 저장한다.

헤드리스(SSH) 환경에서도 동작하도록 imshow 없이, 체스보드가 보이면 ~1초 간격으로
자동 수집한다. 기본 보드는 내부코너 9x6 (10x7칸 인쇄물).

예:
  # 카메라 사용 중이면 백엔드 스트림을 잠시 끄고 실행
  python3 scripts/calibrate_camera.py --device /dev/video0 --num 20 --cols 9 --rows 6

결과 파일이 있으면 아르코 도킹이 자동으로 pose(미터) 모드를 쓸 수 있다.
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np

OUT_DEFAULT = Path(__file__).parent.parent / "config" / "camera_calib.npz"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/video0")
    ap.add_argument("--cols", type=int, default=9, help="체스보드 내부코너 열")
    ap.add_argument("--rows", type=int, default=6, help="체스보드 내부코너 행")
    ap.add_argument("--square-size-m", type=float, default=0.025, help="한 칸 실제 크기(m)")
    ap.add_argument("--num", type=int, default=20, help="수집할 유효 프레임 수")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    pattern = (args.cols, args.rows)
    objp = np.zeros((args.rows * args.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2)
    objp *= args.square_size_m

    cap = cv2.VideoCapture(args.device)
    if not cap.isOpened():
        raise SystemExit(f"카메라 열기 실패: {args.device}")

    obj_pts: list = []
    img_pts: list = []
    last = 0.0
    size = None
    print(f"체스보드 {pattern} 를 여러 각도/거리에서 보여주세요. 목표 {args.num}장.")
    while len(obj_pts) < args.num:
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        size = gray.shape[::-1]
        found, corners = cv2.findChessboardCorners(gray, pattern, None)
        if found and time.time() - last > 1.0:
            corners = cv2.cornerSubPix(
                gray, corners, (11, 11), (-1, -1),
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
            )
            obj_pts.append(objp.copy())
            img_pts.append(corners)
            last = time.time()
            print(f"  수집 {len(obj_pts)}/{args.num}")
    cap.release()

    print("계산 중...")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out, camera_matrix=K, dist_coeffs=dist)
    print(f"완료. RMS 재투영오차={rms:.4f}")
    print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
