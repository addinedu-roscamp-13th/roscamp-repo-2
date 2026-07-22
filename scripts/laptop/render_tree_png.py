#!/usr/bin/env python3
"""마크다운 안의 트리 코드블록을 그대로 PNG 로 찍는다. (발표 자료용)

## 왜 텍스트를 그림으로 굽나

트리 구조는 **글로 있을 때 제일 정확하다** — 검색되고, 복사되고, 코드가 바뀌면 그 자리에서
고칠 수 있다. 그래서 원본은 마크다운에 두고, 발표 슬라이드에 붙일 때만 그림으로 굽는다.
**그림을 원본으로 삼지 않는다** — 그러면 코드가 바뀌었을 때 그림만 남아 거짓말이 된다.

## 왜 직접 스크린샷을 안 찍나

이 환경은 Wayland 라 화면 캡처에 포털 권한이 필요하고, 그건 열 수 없다(실측: X11 캡처는
검은 화면만 나온다). 그래서 터미널에 띄워 찍는 대신 **같은 글꼴로 직접 그린다.**

## 색

터미널에서 보던 대로 읽히게 최소한만 칠한다.

    회색   트리 선(│ ├ └ ─)      구조는 배경이다
    검정   노드 이름              읽어야 할 것
    연회색 # 주석                 설명
    빨강   ⚠ 로 시작하는 주석     일부러 뺀 것 — 이 그림의 요점

## 실행

    .venv/bin/python scripts/laptop/render_tree_png.py <파일.md> --after "## 전체 구조도" --out /tmp/tree.png
"""

from __future__ import annotations

import argparse
import pathlib
import re

from PIL import Image, ImageDraw, ImageFont

#: 고정폭 CJK 는 .ttc 안에 인덱스로 들어 있다. 이걸 안 맞추면 자간이 흔들려
#: 트리 선(│ ├ └)이 세로로 안 이어진다.
FONT_TTC = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 6          # Noto Sans Mono CJK KR
FONT_BOLD_TTC = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

FS = 20                     # 글자 크기
LH = 30                     # 줄 높이
PAD = 44

#: 테마 — 발표 자리(빔·인쇄·화면)에 따라 고르라고 여러 벌 둔다.
#  키: 배경 · 글자 · 트리선 · 주석 · 경고 · 강조 · 줄무늬(없으면 None)
THEMES = {
    # 종이처럼. 인쇄·유인물에 제일 안전하다.
    "paper":    dict(bg=(253,253,251), ink=(32,34,40),    tree=(176,182,192),
                     note=(128,134,146), warn=(198,48,48),  accent=(0,92,148),  band=None),
    # 어두운 터미널. 빔프로젝터에서 눈이 덜 부시다.
    "terminal": dict(bg=(24,26,31),     ink=(226,229,235), tree=(78,84,96),
                     note=(122,132,148), warn=(255,110,110), accent=(120,190,255), band=None),
    # 한 줄 걸러 옅은 띠 — 줄이 많을 때 눈이 안 미끄러진다.
    "striped":  dict(bg=(252,252,250),  ink=(32,34,40),    tree=(176,182,192),
                     note=(128,134,146), warn=(198,48,48),  accent=(0,92,148),
                     band=(245,246,248)),
    # 흑백. 복사·흑백 인쇄용. 경고는 굵기로 구분한다.
    "mono":     dict(bg=(255,255,255),  ink=(20,20,20),    tree=(170,170,170),
                     note=(120,120,120), warn=(20,20,20),  accent=(20,20,20),  band=None),
    # 따뜻한 종이색. 오래 봐도 덜 피로하다.
    "sepia":    dict(bg=(250,246,238),  ink=(52,44,34),    tree=(196,184,166),
                     note=(140,128,110), warn=(176,60,36),  accent=(28,88,120), band=None),
}

TREE_CHARS = set("│├└─┌┐┘┬┴┼ ")


def extract_block(md: pathlib.Path, after: str) -> list[str]:
    """`after` 제목 뒤 첫 코드블록의 내용."""
    text = md.read_text()
    i = text.index(after)
    m = re.search(r"```[^\n]*\n(.*?)```", text[i:], re.S)
    if not m:
        raise SystemExit(f"'{after}' 뒤에 코드블록이 없습니다")
    # 마크다운 강조 기호는 글에서만 뜻이 있다 — 그림에 그대로 찍히면 잡티가 된다.
    return [l.replace("**", "") for l in m.group(1).rstrip("\n").split("\n")]


def split_comment(line: str) -> tuple[str, str]:
    """(코드 부분, 주석 부분). 주석은 첫 `#` 부터."""
    i = line.find("#")
    return (line, "") if i < 0 else (line[:i], line[i:])


def split_columns(lines: list[str], n: int) -> list[list[str]]:
    """줄을 n 열로 나눈다. **가지 사이(빈 줄)에서만** 자른다 —
    한 갈래가 두 열에 걸치면 읽는 사람이 눈으로 다시 이어붙여야 한다."""
    if n <= 1:
        return [lines]
    breaks = [i for i, l in enumerate(lines) if set(l.strip()) <= {"│", ""} and l.strip()]
    target, cols, start = len(lines) / n, [], 0
    for c in range(1, n):
        want = target * c
        cut = min((b for b in breaks if b > start), key=lambda b: abs(b - want), default=None)
        if cut is None:
            break
        cols.append(lines[start:cut])
        start = cut + 1
    cols.append(lines[start:])
    return cols


def draw(lines: list[str], out: pathlib.Path, title: str, theme: str,
         columns: int = 1) -> pathlib.Path:
    C = THEMES[theme]
    mono = ImageFont.truetype(FONT_TTC, FS, index=FONT_INDEX)
    bold = ImageFont.truetype(FONT_BOLD_TTC, 30)
    sub = ImageFont.truetype(FONT_TTC, 16, index=FONT_INDEX)

    probe = Image.new("RGB", (1, 1))
    pd = ImageDraw.Draw(probe)
    cols = split_columns(lines, columns)
    # 한글이 섞이면 글자 폭이 배가 되므로, 가장 넓은 줄을 실측해서 폭을 잡는다.
    col_w = int(max(pd.textlength(l, font=mono) for l in lines)) + 40
    rows = max(len(c) for c in cols)
    head = 96 if title else 0

    W = PAD * 2 + col_w * len(cols)
    H = head + rows * LH + PAD * 2
    img = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(img)

    if title:
        d.text((PAD, 34), title, font=bold, fill=C["ink"])
        d.text((PAD + 2, 70), "#  설명      ⚠  일부러 뺀 것 (아직 안 만든 것이 아니다)",
               font=sub, fill=C["note"])

    for ci, col in enumerate(cols):
        x0 = PAD + ci * col_w
        y = head + PAD
        for row, line in enumerate(col):
            if C["band"] and row % 2 == 1:
                d.rectangle((x0 - 8, y - 4, x0 + col_w - 16, y + LH - 4), fill=C["band"])
            code, comment = split_comment(line)

            # 트리 선과 노드 이름을 갈라 칠한다 — 선은 배경, 이름은 읽을 것.
            x, j = x0, 0
            while j < len(code) and code[j] in TREE_CHARS:
                j += 1
            if j:
                d.text((x, y), code[:j], font=mono, fill=C["tree"])
                x += pd.textlength(code[:j], font=mono)
            if code[j:]:
                body = code[j:]
                color = C["accent"] if body.rstrip().endswith(
                    ("Branch  (Sequence)", "(Selector, memory=False)")) else C["ink"]
                d.text((x, y), body, font=mono, fill=color)
                x += pd.textlength(body, font=mono)

            if comment:
                d.text((x, y), comment, font=mono,
                       fill=C["warn"] if comment.lstrip("# ").startswith("⚠") else C["note"])
            y += LH

    img.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="트리 코드블록이 든 마크다운")
    ap.add_argument("--after", required=True, help="이 제목 뒤 첫 코드블록을 쓴다")
    ap.add_argument("--out", default="/tmp/tree.png")
    ap.add_argument("--title", default="")
    ap.add_argument("--theme", default="paper", choices=sorted(THEMES) + ["all"])
    ap.add_argument("--columns", type=int, default=1, help="열 수. 슬라이드에 넣으려면 2~3")
    args = ap.parse_args()

    lines = extract_block(pathlib.Path(args.md), args.after)
    out = pathlib.Path(args.out)
    themes = sorted(THEMES) if args.theme == "all" else [args.theme]
    for t in themes:
        target = out if len(themes) == 1 else out.with_name(f"{out.stem}-{t}{out.suffix}")
        p = draw(lines, target, args.title, t, args.columns)
        img = Image.open(p)
        print(f"  ✔ {t:9} {p.name}  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()
