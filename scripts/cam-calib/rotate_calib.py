#!/usr/bin/env python3
"""회전된 영상용 K 를 만든다 — 재촬영 없이 계산으로 옮긴다.

    python3 rotate_calib.py config/camera/picam_640x480.npz 180
    #  → config/camera/picam_640x480_rot180.npz

## 왜 필요한가

`camera_sender.py` 는 `--picamera` 일 때 **`--rotate 180` 이 기본**이다(카메라가 거꾸로
달려 있어 YOLO 가 사람을 똑바로 보게 하려는 것). 그런데 캘리브는 회전 없는 프레임으로
뽑았으므로, 회전된 스트림에 그 K 를 그대로 쓰면 **주점이 반대쪽에 있는 셈**이 된다.
거리(z)는 크게 안 틀리지만 좌우 오프셋(x)과 yaw 가 어긋난다 — 도킹에서 제일 나쁜 종류다.

회전은 픽셀 좌표의 1:1 사상이라 **다시 찍을 필요가 없다.** 180° 는

    u' = W-1-u,  v' = H-1-v   →   cx' = W-1-cx,  cy' = H-1-cy
    k1,k2,k3 그대로 (반경 방향은 회전 대칭)
    p1,p2 는 부호 반전 (정규화 좌표가 함께 뒤집히므로)

90/270 은 축이 바뀌므로 fx↔fy, cx↔cy 도 함께 교환하고 이미지 크기도 뒤집는다.
"""
import argparse
import pathlib
import sys

import numpy as np


def rotate_calib(K, dist, size, deg: int):
    """(K, dist, size) 를 deg 만큼 회전한 영상 기준으로 옮긴다. deg ∈ {0,90,180,270}."""
    K = K.copy().astype(float)
    d = dist.ravel().copy().astype(float)
    w, h = int(size[0]), int(size[1])
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    p1, p2 = (d[2], d[3]) if len(d) >= 4 else (0.0, 0.0)

    if deg == 0:
        return K, d, (w, h)
    if deg == 180:
        K[0, 2], K[1, 2] = w - 1 - cx, h - 1 - cy
        if len(d) >= 4:
            d[2], d[3] = -p1, -p2
        return K, d, (w, h)
    if deg in (90, 270):
        # 시계 90°: (u,v) → (h-1-v, u). 반시계(=270)는 (v, w-1-u).
        K[0, 0], K[1, 1] = fy, fx
        if deg == 90:
            K[0, 2], K[1, 2] = h - 1 - cy, cx
            if len(d) >= 4:
                d[2], d[3] = -p2, p1
        else:
            K[0, 2], K[1, 2] = cy, w - 1 - cx
            if len(d) >= 4:
                d[2], d[3] = p2, -p1
        return K, d, (h, w)
    raise SystemExit(f"회전각은 0/90/180/270 만 됩니다 (받은 값: {deg})")


def main() -> None:
    ap = argparse.ArgumentParser(description="회전된 영상용 캘리브 파일 생성")
    ap.add_argument("npz", help="원본 캘리브 npz")
    ap.add_argument("degrees", type=int, choices=(0, 90, 180, 270))
    ap.add_argument("--out", default=None, help="기본: <원본>_rot<각도>.npz")
    a = ap.parse_args()

    src = pathlib.Path(a.npz)
    if not src.exists():
        sys.exit(f"없는 파일: {src}")
    d = np.load(str(src))
    if "image_size" not in d:
        sys.exit(f"{src} 에 image_size 가 없습니다 — 회전은 영상 크기를 알아야 합니다.")
    size = tuple(int(x) for x in d["image_size"])
    K2, dist2, size2 = rotate_calib(d["camera_matrix"], d["dist_coeffs"], size, a.degrees)

    out = pathlib.Path(a.out) if a.out else src.with_name(f"{src.stem}_rot{a.degrees}.npz")
    np.savez(out, camera_matrix=K2, dist_coeffs=dist2.reshape(1, -1),
             image_size=np.array(size2),
             rms=d["rms"] if "rms" in d else np.array(float("nan")),
             rotated_from=str(src.name), rotation_deg=np.array(a.degrees))
    print(f"{out}")
    print(f"  {size[0]}x{size[1]} → {size2[0]}x{size2[1]}  ({a.degrees}° 회전 영상 전용)")
    print(f"  fx={K2[0,0]:.2f} fy={K2[1,1]:.2f} cx={K2[0,2]:.2f} cy={K2[1,2]:.2f}")


if __name__ == "__main__":
    main()
