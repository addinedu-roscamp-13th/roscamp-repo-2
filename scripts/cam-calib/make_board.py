#!/usr/bin/env python3
"""인쇄용 ChArUco 보드 PDF/PNG 생성 (A4 가로, mm 정확).

    python3 make_board.py                       # 7x5칸 · 35mm · DICT_4X4_50 (기본)
    python3 make_board.py --squares 11x7 --square-mm 25
    python3 make_board.py --out-dir ~/Pictures

기본값은 `calib_client.py` 의 기본값과 **같은 값**이다(7x5칸 35mm, 마커=칸의 0.75, DICT_4X4_50).
이 조합은 A4 에 들어가면서 picam 480x360 에서 0.8m 까지 마커가 잡히는 선이다 —
30mm 로 줄이면 0.5m 부터 마커를 놓치기 시작한다(합성 시험으로 실측).
여기를 바꿨으면 촬영할 때도 `--squares` / `--dict` 를 같이 줘야 한다 — 보드와 검출기의
기하가 다르면 검출은 되는데 값이 조용히 틀린다.

PDF 로 뽑는 이유: PNG 를 인쇄하면 뷰어·드라이버가 제멋대로 배율을 먹는다. PDF 는
72dpi 포인트가 곧 물리 단위라 "실제 크기(100%)" 로 인쇄하면 그대로 나온다.
그래도 **인쇄물은 자로 재라** — 100% 로 뽑아도 프린터가 어긋난다.
"""
import argparse
import pathlib
import tempfile

import cairo
import cv2
import numpy as np

MM = 72.0 / 25.4          # 1mm in PDF points
PAGE_W, PAGE_H = 297.0, 210.0    # A4 가로
FOOTER_MM = 22.0          # 눈금자·설명이 들어갈 아래 여백
PX_PER_MM = 8             # 보드 래스터 해상도(= 203dpi). 인쇄 품질에 충분하다.


def build_board(dict_name: str, squares: tuple[int, int], square_m: float, marker_m: float):
    """calib_client.build_charuco 와 같은 분기 — cv2 4.6/4.7+ 양쪽."""
    dict_id = getattr(cv2.aruco, dict_name)
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    else:
        dictionary = cv2.aruco.Dictionary_get(dict_id)
    if hasattr(cv2.aruco, "CharucoBoard_create"):
        return cv2.aruco.CharucoBoard_create(squares[0], squares[1], square_m, marker_m, dictionary)
    return cv2.aruco.CharucoBoard(squares, square_m, marker_m, dictionary)


def render_board_png(board, squares, square_mm, path: str) -> None:
    w = int(round(squares[0] * square_mm * PX_PER_MM))
    h = int(round(squares[1] * square_mm * PX_PER_MM))
    img = (board.generateImage((w, h)) if hasattr(board, "generateImage")
           else board.draw((w, h)))
    cv2.imwrite(path, img)


def draw_page(ctx, board_png: str, squares, square_mm, marker_mm, dict_name) -> None:
    board_w, board_h = squares[0] * square_mm, squares[1] * square_mm
    if board_w > PAGE_W - 10 or board_h > PAGE_H - FOOTER_MM - 8:
        raise SystemExit(f"보드가 A4 를 넘습니다: {board_w:.0f}x{board_h:.0f}mm — 칸 수나 크기를 줄이세요.")
    ox = (PAGE_W - board_w) / 2
    oy = (PAGE_H - FOOTER_MM - board_h) / 2

    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()

    img = cairo.ImageSurface.create_from_png(board_png)
    ctx.save()
    ctx.translate(ox * MM, oy * MM)
    ctx.scale(board_w * MM / img.get_width(), board_h * MM / img.get_height())
    ctx.set_source_surface(img, 0, 0)
    ctx.get_source().set_filter(cairo.FILTER_NEAREST)   # 마커 비트가 뭉개지지 않게
    ctx.paint()
    ctx.restore()

    # 인쇄 배율 검증용 100mm 눈금자 — 자로 재서 100mm 가 아니면 그 배율만큼 K 가 틀어진다.
    ctx.set_source_rgb(0, 0, 0)
    bar_y = oy + board_h + 9
    ctx.set_line_width(0.4 * MM)
    ctx.move_to(ox * MM, bar_y * MM)
    ctx.line_to((ox + 100) * MM, bar_y * MM)
    for t in range(0, 101, 10):
        tick = 3.0 if t % 50 == 0 else 1.5
        ctx.move_to((ox + t) * MM, bar_y * MM)
        ctx.line_to((ox + t) * MM, (bar_y - tick) * MM)
    ctx.stroke()

    ctx.select_font_face("Sans")
    ctx.set_font_size(3.2 * MM)
    ctx.move_to(ox * MM, (bar_y + 5) * MM)
    ctx.show_text("|<- 100 mm ->|  print at 100% (no fit-to-page), then MEASURE one square")
    ctx.move_to(ox * MM, (bar_y + 10) * MM)
    ctx.show_text(f"ChArUco {squares[0]}x{squares[1]} squares @ {square_mm:g} mm  ·  "
                  f"marker {marker_mm:g} mm  ·  {dict_name}  ->  inner corners "
                  f"{squares[0]-1} x {squares[1]-1}")


def main() -> None:
    ap = argparse.ArgumentParser(description="인쇄용 ChArUco 보드 생성")
    ap.add_argument("--squares", default="7x5", help="칸 개수 가로x세로 (내부 코너가 아니다)")
    ap.add_argument("--square-mm", type=float, default=35.0)
    ap.add_argument("--marker-mm", type=float, default=None, help="생략하면 한 칸의 0.75")
    ap.add_argument("--dict", default="DICT_4X4_50",
                    help="비트가 적을수록 저해상도에 강하다 — picam 480x360 이라 4x4 가 기본")
    ap.add_argument("--out-dir", default="~/Pictures")
    a = ap.parse_args()

    squares = tuple(int(x) for x in a.squares.lower().split("x"))
    if len(squares) != 2:
        raise SystemExit(f"--squares 형식: 9x6 (받은 값: {a.squares!r})")
    marker_mm = a.marker_mm if a.marker_mm else a.square_mm * 0.75
    if not 0.4 * a.square_mm <= marker_mm < a.square_mm:
        raise SystemExit(f"마커({marker_mm}mm)는 칸({a.square_mm}mm)보다 작아야 하고 "
                         f"너무 작으면 검출이 안 됩니다.")

    out_dir = pathlib.Path(a.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"charuco_{squares[0]}x{squares[1]}_{a.square_mm:g}mm_{a.dict}"

    board = build_board(a.dict, squares, a.square_mm / 1000.0, marker_mm / 1000.0)
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        render_board_png(board, squares, a.square_mm, tmp.name)

        surf = cairo.PDFSurface(f"{stem}.pdf", PAGE_W * MM, PAGE_H * MM)
        draw_page(cairo.Context(surf), tmp.name, squares, a.square_mm, marker_mm, a.dict)
        surf.finish()

        dpi = 200                                  # 미리보기 PNG (인쇄용 아님)
        png = cairo.ImageSurface(cairo.FORMAT_RGB24,
                                 int(PAGE_W / 25.4 * dpi), int(PAGE_H / 25.4 * dpi))
        pctx = cairo.Context(png)
        pctx.scale(dpi / 72.0, dpi / 72.0)
        draw_page(pctx, tmp.name, squares, a.square_mm, marker_mm, a.dict)
        png.write_to_png(f"{stem}.png")

    print(f"{stem}.pdf\n{stem}.png")
    print(f"  ChArUco {squares[0]}x{squares[1]}칸 @ {a.square_mm:g}mm · 마커 {marker_mm:g}mm · "
          f"{a.dict} · 보드 {squares[0]*a.square_mm:g}x{squares[1]*a.square_mm:g}mm")
    print(f"  촬영:  ./calib-laptop.sh picam <PI_IP> {a.square_mm/1000:.3f}"
          + ("" if a.squares == "7x5" else f"   (+ SQUARES={a.squares})"))
    print("  ※ 인쇄 후 한 칸을 자로 재서 그 실측값(m)을 넣으세요.")


if __name__ == "__main__":
    main()
