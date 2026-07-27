#!/usr/bin/env python3
"""인쇄용 ArUco **도킹 마커** 시트 생성 (A4 세로, mm 정확).

    python3 make_marker.py                      # 5x5 · 50/60/70mm 세 장 · id 0~5
    python3 make_marker.py --sizes-mm 60 --ids 0-3
    python3 make_marker.py --dict DICT_6X6_50 --sizes-mm 50

캘리브 보드(make_board.py)와 **다른 용도**다. 이건 로봇이 주차할 때 보는 마커고,
`aruco_dock.py` 가 `marker_len_m` 과 함께 solvePnP 로 거리를 뽑는 대상이다.

⚠️ 인쇄한 크기를 코드에도 알려야 한다 — `parking.tsx:20` 의 `MARKER_LEN_M`.
   여기가 실제 인쇄물과 다르면 K 가 완벽해도 **거리가 그 비율만큼 통째로 틀어진다.**
   (50mm 로 뽑고 상수가 0.05 면 맞음. 60mm 로 뽑았으면 0.06 으로 고쳐야 한다)

⚠️ 마커 바깥의 **흰 여백(퀸존)은 장식이 아니다.** 1모듈 이상 없으면 검출이 급격히 나빠진다.
   잘라 붙일 때 검은 테두리에 바짝 자르지 말 것 — 자르는 선을 같이 찍어 둔다.
"""
import argparse
import pathlib
import tempfile

import cairo
import cv2

MM = 72.0 / 25.4                 # 1mm in PDF points
PAGE_W, PAGE_H = 210.0, 297.0    # A4 세로
# 페이지 여백. 70mm 마커는 퀸존까지 98mm 라 여백 10mm 면 한 줄에 하나밖에 안 들어간다.
# 6mm 로 줄여 두 개가 들어가게 한다 — 잘리더라도 자르는 점선 끄트머리뿐이고, 마커 자체는
# 퀸존 안쪽이라 가장자리에서 20mm 이상 떨어져 있다.
MARGIN_MM = 6.0
FOOTER_MM = 18.0                 # 눈금자 + 설명
PX_PER_MM = 12                   # 마커 래스터 해상도(= 305dpi)


def parse_ids(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out.extend(range(a, b + 1))
        elif part:
            out.append(int(part))
    return out


def marker_png(dictionary, marker_id: int, size_mm: float, path: str) -> None:
    px = int(round(size_mm * PX_PER_MM))
    if hasattr(cv2.aruco, "generateImageMarker"):
        img = cv2.aruco.generateImageMarker(dictionary, marker_id, px)      # 4.7+
    else:
        img = cv2.aruco.drawMarker(dictionary, marker_id, px)               # 4.6
    cv2.imwrite(path, img)


def draw_page(ctx, tiles, size_mm, quiet_mm, cols, rows, dict_name, n_bits) -> None:
    """tiles: [(marker_id, png_path), ...] — 좌상단부터 행 우선으로 채운다."""
    cell = size_mm + 2 * quiet_mm
    label_mm = 5.0                                   # 셀 아래 라벨 줄
    grid_w, grid_h = cols * cell, rows * (cell + label_mm)
    ox = (PAGE_W - grid_w) / 2
    oy = MARGIN_MM

    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    ctx.select_font_face("Sans")

    for i, (mid, png) in enumerate(tiles):
        r, c = divmod(i, cols)
        x0 = ox + c * cell
        y0 = oy + r * (cell + label_mm)

        # 자르는 선 — 퀸존(흰 여백) 바깥. 검은 테두리에 바짝 자르면 검출이 나빠진다.
        ctx.set_source_rgb(0.75, 0.75, 0.75)
        ctx.set_line_width(0.2 * MM)
        ctx.set_dash([2 * MM, 2 * MM])
        ctx.rectangle(x0 * MM, y0 * MM, cell * MM, cell * MM)
        ctx.stroke()
        ctx.set_dash([])

        img = cairo.ImageSurface.create_from_png(png)
        ctx.save()
        ctx.translate((x0 + quiet_mm) * MM, (y0 + quiet_mm) * MM)
        ctx.scale(size_mm * MM / img.get_width(), size_mm * MM / img.get_height())
        ctx.set_source_surface(img, 0, 0)
        ctx.get_source().set_filter(cairo.FILTER_NEAREST)     # 비트가 뭉개지지 않게
        ctx.paint()
        ctx.restore()

        ctx.set_source_rgb(0, 0, 0)
        ctx.set_font_size(3.0 * MM)
        ctx.move_to(x0 * MM, (y0 + cell + 3.6) * MM)
        ctx.show_text(f"id {mid}  ·  {n_bits}x{n_bits}  ·  {size_mm:g} mm")

    bar_y = oy + grid_h + 8
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(0.4 * MM)
    ctx.move_to(MARGIN_MM * MM, bar_y * MM)
    ctx.line_to((MARGIN_MM + 100) * MM, bar_y * MM)
    for t in range(0, 101, 10):
        tick = 3.0 if t % 50 == 0 else 1.5
        ctx.move_to((MARGIN_MM + t) * MM, bar_y * MM)
        ctx.line_to((MARGIN_MM + t) * MM, (bar_y - tick) * MM)
    ctx.stroke()
    ctx.set_font_size(3.0 * MM)
    ctx.move_to(MARGIN_MM * MM, (bar_y + 5) * MM)
    ctx.show_text("|<- 100 mm ->|  print at 100% (no fit-to-page), then MEASURE the black square")
    ctx.move_to(MARGIN_MM * MM, (bar_y + 9.5) * MM)
    ctx.show_text(f"{dict_name}  ·  black square = {size_mm:g} mm  ->  set MARKER_LEN_M = "
                  f"{size_mm/1000:.3f} in parking.tsx")
    ctx.move_to(MARGIN_MM * MM, (bar_y + 14) * MM)
    ctx.show_text("cut on the dashed line - the white border is part of the marker")


def main() -> None:
    ap = argparse.ArgumentParser(description="도킹용 ArUco 마커 시트 생성")
    ap.add_argument("--dict", default="DICT_5X5_50",
                    help="aruco_dock.py 의 _ARUCO_DICTS 에 있는 것만 쓴다")
    ap.add_argument("--sizes-mm", default="50,60,70", help="검은 사각형 한 변(mm), 쉼표 구분")
    ap.add_argument("--ids", default="0-5", help="예: 0-5 또는 1,4,7")
    ap.add_argument("--out-dir", default="~/Pictures")
    a = ap.parse_args()

    dict_id = getattr(cv2.aruco, a.dict, None)
    if dict_id is None:
        raise SystemExit(f"--dict 를 모르겠습니다: {a.dict!r}")
    dictionary = (cv2.aruco.getPredefinedDictionary(dict_id)
                  if hasattr(cv2.aruco, "getPredefinedDictionary")
                  else cv2.aruco.Dictionary_get(dict_id))
    n_bits = int(a.dict.split("_")[1].split("X")[0])          # DICT_5X5_50 → 5

    ids = parse_ids(a.ids)
    out_dir = pathlib.Path(a.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    for size_mm in (float(s) for s in a.sizes_mm.split(",")):
        quiet_mm = size_mm / n_bits                            # 1 모듈 = 최소 권장 퀸존
        cell = size_mm + 2 * quiet_mm
        cols = int((PAGE_W - 2 * MARGIN_MM) // cell)
        rows = int((PAGE_H - 2 * MARGIN_MM - FOOTER_MM) // (cell + 5.0))
        if cols < 1 or rows < 1:
            print(f"[skip] {size_mm:g}mm — A4 에 한 개도 안 들어갑니다")
            continue
        use = ids[:cols * rows]
        if len(use) < len(ids):
            print(f"[!] {size_mm:g}mm 는 A4 에 {cols*rows}개까지 — id {use[-1]} 까지만 넣습니다")

        stem = out_dir / f"aruco_{a.dict}_{size_mm:g}mm_id{use[0]}-{use[-1]}"
        with tempfile.TemporaryDirectory() as td:
            tiles = []
            for mid in use:
                p = f"{td}/m{mid}.png"
                marker_png(dictionary, mid, size_mm, p)
                tiles.append((mid, p))

            surf = cairo.PDFSurface(f"{stem}.pdf", PAGE_W * MM, PAGE_H * MM)
            draw_page(cairo.Context(surf), tiles, size_mm, quiet_mm, cols, rows, a.dict, n_bits)
            surf.finish()

            dpi = 200
            png = cairo.ImageSurface(cairo.FORMAT_RGB24,
                                     int(PAGE_W / 25.4 * dpi), int(PAGE_H / 25.4 * dpi))
            pctx = cairo.Context(png)
            pctx.scale(dpi / 72.0, dpi / 72.0)
            draw_page(pctx, tiles, size_mm, quiet_mm, cols, rows, a.dict, n_bits)
            png.write_to_png(f"{stem}.png")

        print(f"{stem}.pdf  ({cols}x{rows}칸 · id {use[0]}~{use[-1]} · 퀸존 {quiet_mm:.1f}mm)")


if __name__ == "__main__":
    main()
