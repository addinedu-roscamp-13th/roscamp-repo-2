#!/usr/bin/env python3
"""안내판 지도 그림에서 **방 사각형과 구역 상자를 다시 재는** 도구.

`src/domain.h` 의 IMG_* 상수와 `RobotController::facilities()` 의 bx,by,bw,bh 는
그림에서 실측한 값이다. 그림을 갈아끼우면 조용히 어긋난다 — 알약은 그대로 보이는데
탭이 엉뚱한 곳에서 먹고, 로봇 마커가 벽을 뚫는다. 그때 이걸 돌려 값을 다시 만든다.

    python3 tools/measure_map_boxes.py qml/assets/artemap.png

출력한 값을 붙여 넣은 뒤 **반드시** 시험을 다시 돌린다(골든 좌표가 같이 바뀐다):

    cmake -S . -B build -DLIBI_GUI_TESTS=ON && cmake --build build -j && ./build/test_domain

## 어떻게 재나
- **방 사각형**: 남색 외벽을 찾아, 여러 행/열에서 "첫 벽 덩어리가 끝나는 곳"의 중앙값을
  안쪽 경계로 삼는다. 벽이 이중선이고 알약이 벽을 덮고 있어서 한 줄만 보면 틀린다.
- **구역 상자**: 색상(hue)별로 나눈 뒤 연결요소의 경계상자를 잡는다. 회색 테이블은
  채도가 없어 따로 잡는다. 두 개가 붙어 있으면(예술서가/문학서가) 한 덩어리로 나오므로
  아래 REGIONS 로 영역을 좁혀 다시 잰다.
"""
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

#: 색이 겹치거나 붙어 있어 자동 분리가 안 되는 것만 영역을 좁혀 준다 (x0,x1,y0,y1 비율).
REGIONS = {
    "화장실": (0.02, 0.30, 0.00, 0.16),
    "미술작품": (0.30, 0.62, 0.00, 0.16),
    "예술서가": (0.05, 0.22, 0.30, 0.60),
    "문학서가": (0.05, 0.22, 0.60, 0.93),
    "출입구": (0.88, 1.00, 0.35, 0.78),
}


def _navy(a):
    """남색 외벽. `a` 는 0..1 정규화라 임계도 0..1 로 쓴다 (0..255 로 쓰면 전부 False)."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (b > 60 / 255) & (b < 140 / 255) & (r < 100 / 255) & (g < 120 / 255) & (b > r + 20 / 255)


def room_rect(a):
    """벽 **안쪽** 사각형 (left, right, top, bottom) 픽셀."""
    navy = _navy(a)
    h, w = navy.shape

    def first_end(mask):
        idx = np.nonzero(mask)[0]
        if len(idx) < 2:
            return None
        gaps = np.nonzero(np.diff(idx) > 8)[0]
        return idx[gaps[0]] if len(gaps) else None

    def last_start(mask):
        idx = np.nonzero(mask)[0]
        if len(idx) < 2:
            return None
        gaps = np.nonzero(np.diff(idx) > 8)[0]
        return idx[gaps[-1] + 1] if len(gaps) else None

    med = lambda xs: int(np.median([v for v in xs if v is not None]))
    left = med([first_end(navy[y]) for y in range(int(h * 0.1), int(h * 0.9), 7)]) + 1
    right = med([last_start(navy[y]) for y in range(int(h * 0.1), int(h * 0.9), 7)]) - 1
    top = med([first_end(navy[:, x]) for x in range(int(w * 0.05), int(w * 0.95), 7)]) + 1
    bottom = med([last_start(navy[:, x]) for x in range(int(w * 0.05), int(w * 0.95), 7)]) - 1
    return left, right, top, bottom


def zone_boxes(a):
    """색상별 연결요소 경계상자 → [(비율 x, y, w, h, 대표색)]."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx, mn = a.max(2), a.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    h, w = mx.shape
    bg = (np.abs(r - 0.984) < 0.05) & (np.abs(g - 0.953) < 0.06) & (np.abs(b - 0.894) < 0.07)
    colored = (sat > 0.10) & (mx >= 0.55) & ~bg
    grey = (sat < 0.10) & ~bg & (mx > 0.60) & (mx < 0.95)

    out = []
    for mask in (colored, grey):
        m = ndimage.binary_opening(ndimage.binary_closing(mask, np.ones((13, 13))), np.ones((5, 5)))
        lab, _ = ndimage.label(m)
        for i, sl in enumerate(ndimage.find_objects(lab)):
            ys, xs = sl
            bw, bh = xs.stop - xs.start, ys.stop - ys.start
            if (lab[sl] == i + 1).sum() < 2500 or bw > 0.85 * w or bh > 0.85 * h:
                continue
            out.append((xs.start / w, ys.start / h, bw / w, bh / h))
    return sorted(out, key=lambda t: (t[1], t[0]))


def region_box(a, x0, x1, y0, y1):
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx, mn = a.max(2), a.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    h, w = mx.shape
    bg = (np.abs(r - 0.984) < 0.05) & (np.abs(g - 0.953) < 0.06) & (np.abs(b - 0.894) < 0.07)
    colored = (sat > 0.10) & (mx >= 0.55) & ~bg
    sub = colored[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return None
    X0, Y0 = int(x0 * w) + xs.min(), int(y0 * h) + ys.min()
    return (X0 / w, Y0 / h, (xs.max() - xs.min()) / w, (ys.max() - ys.min()) / h)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "qml/assets/artemap.png"
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(float) / 255
    W, H = im.size
    left, right, top, bottom = room_rect(a)

    print(f"# {path}  {W}x{H}")
    print("\n// src/domain.h — 벽 안쪽 방 사각형")
    print(f"constexpr double IMG_LEFT   = {left}.0  / {W}.0;")
    print(f"constexpr double IMG_RIGHT  = {right}.0 / {W}.0;")
    print(f"constexpr double IMG_TOP    = {top}.0  / {H}.0;")
    print(f"constexpr double IMG_BOTTOM = {bottom}.0 / {H}.0;")
    print(f"//   안쪽 {right-left}x{bottom-top}px  비율 {(right-left)/(bottom-top):.3f}")

    print("\n// facilities() — 구역 상자 (bx, by, bw, bh)")
    for x, y, bw, bh in zone_boxes(a):
        print(f"//   {x:.3f}, {y:.3f}, {bw:.3f}, {bh:.3f}")
    print("// 영역을 좁혀 다시 잰 것 (색이 겹치거나 붙어 있는 것들):")
    for name, (x0, x1, y0, y1) in REGIONS.items():
        box = region_box(a, x0, x1, y0, y1)
        if box:
            print(f"//   {name:8s} {box[0]:.3f}, {box[1]:.3f}, {box[2]:.3f}, {box[3]:.3f}")

    print("\n# 붙여 넣은 뒤 반드시: cmake --build build --target test_domain && ./build/test_domain")


if __name__ == "__main__":
    main()
