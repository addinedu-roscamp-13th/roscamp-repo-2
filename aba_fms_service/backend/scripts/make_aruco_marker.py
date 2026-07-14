#!/usr/bin/env python3
"""인쇄용 아르코(ArUco) 마커 이미지를 생성한다.

예:
  python3 scripts/make_aruco_marker.py --id 7 --size 800 --out marker7.png
그 뒤 실측 인쇄 크기(mm)를 재서 도킹 시 marker_len_m 로 넘긴다.
"""
import argparse

import cv2

DICTS = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_7X7_50": cv2.aruco.DICT_7X7_50,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dict", default="DICT_4X4_50", choices=list(DICTS))
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--size", type=int, default=800, help="마커 픽셀 크기")
    ap.add_argument("--border", type=int, default=80, help="흰 여백(px)")
    ap.add_argument("--out", default="aruco_marker.png")
    args = ap.parse_args()

    d = cv2.aruco.getPredefinedDictionary(DICTS[args.dict])
    img = cv2.aruco.generateImageMarker(d, args.id, args.size)
    img = cv2.copyMakeBorder(
        img, args.border, args.border, args.border, args.border,
        cv2.BORDER_CONSTANT, value=255,
    )
    cv2.imwrite(args.out, img)
    print(f"저장: {args.out}  ({args.dict}, id={args.id})")
    print("인쇄 후 실제 변 길이(mm)를 재서 도킹 시 marker_len_m 로 사용하세요.")


if __name__ == "__main__":
    main()
