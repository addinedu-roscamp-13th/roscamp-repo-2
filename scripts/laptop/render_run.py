#!/usr/bin/env python3
"""`record_run.py` 가 남긴 기록 → **타임라인 표**와 **지도 위 궤적 그림**.

화면 녹화가 안 되는 환경에서 "무슨 일이 언제 일어났는지"를 보여주는 두 가지 산출물을
만든다. 둘 다 같은 기록에서 나오므로 서로 어긋날 수 없다.

    타임라인   .md   주문 사건 + 상태 전이를 한 시각 축에
    궤적       .png  로봇이 실제로 지난 길을 점유 격자 위에

## 궤적을 지도 위에 그리는 방법

`arte2.yaml` 의 `origin`·`resolution` 으로 (x, y) 를 픽셀로 옮긴다.

    col = (x - origin_x) / resolution
    row = (높이 - 1) - (y - origin_y) / resolution     ← 이미지는 위에서 아래로 센다

이 변환을 틀리면 궤적이 지도와 어긋난 채 그럴듯해 보인다(상하 반전이 특히 눈에 안 띈다).
그래서 정점 이름표도 같이 찍는다 — `주차장`·`입구` 가 제자리에 있으면 변환이 맞은 것이다.

## 실행

    .venv/bin/python scripts/laptop/render_run.py /tmp/run.jsonl --out-dir /tmp/out
"""

from __future__ import annotations

import argparse
import json
import pathlib

import yaml
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAP_YAML = ROOT / "aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_navigation/map/arte2.yaml"
NAVGRAPH = ROOT / "aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml"

#: 확대 배수. 원본이 63x108 셀이라 그냥 그리면 궤적이 뭉갠다.
SCALE = 8
#: 로봇별 궤적 색. 2대 이상이어도 구분된다.
COLORS = [(0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167)]


def load_records(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


# ── 타임라인 ─────────────────────────────────────────────────────────────────

def timeline_md(records: list[dict]) -> str:
    """사건과 상태 전이만 뽑아 표로. 위치는 초당 하나씩이라 표에 넣으면 못 읽는다."""
    rows = []
    for r in records:
        t = f"{r['t']:6.1f}s"
        if r["kind"] == "event":
            what = {"task_started": "작업 시작", "task_done": "배달 완료",
                    "task_failed": "실패"}.get(r.get("kind2", ""), r.get("text", ""))
            leg = f" ({r.get('leg_idx','?')}/{r.get('leg_count','?')})" if "leg_idx" in r else ""
            rows.append((t, "주문", f"{r.get('text','')}{leg}"))
        elif r["kind"] == "state":
            rows.append((t, f"로봇 {r['robot']}", f"**{r['frm']} → {r['to']}**"))
        elif r["kind"] in ("start", "end"):
            rows.append((t, "—", f"기록 {'시작' if r['kind']=='start' else '끝'} ({r.get('wall','')})"))

    out = ["| 시각 | 어디 | 무엇 |", "|---|---|---|"]
    out += [f"| `{t}` | {w} | {m} |" for t, w, m in rows]
    return "\n".join(out)


def summary_md(records: list[dict]) -> str:
    poses = [r for r in records if r["kind"] == "pose"]
    if not poses:
        return "(위치 기록 없음)"
    robots = sorted({r["robot"] for r in poses})
    lines = []
    for name in robots:
        pts = [(r["x"], r["y"]) for r in poses if r["robot"] == name]
        dist = sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                   for a, b in zip(pts, pts[1:]))
        states = []
        for r in records:
            if r["kind"] == "state" and r["robot"] == name:
                states.append(r["to"])
        lines.append(f"- **{name}** — 이동 거리 {dist:.2f} m · 상태 {len(states)}회 전이"
                     + (f" ({' → '.join(states)})" if states else ""))
    total = records[-1]["t"] if records else 0
    lines.append(f"- 전체 {total:.0f}초")
    return "\n".join(lines)


# ── 궤적 그림 ────────────────────────────────────────────────────────────────

def draw_track(records: list[dict], out_png: pathlib.Path) -> pathlib.Path:
    meta = yaml.safe_load(MAP_YAML.read_text())
    res, (ox, oy, *_) = meta["resolution"], meta["origin"]
    img = Image.open(MAP_YAML.parent / meta["image"]).convert("RGB")
    w, h = img.size
    img = img.resize((w * SCALE, h * SCALE), Image.NEAREST)
    d = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[float, float]:
        # 이미지 행은 위에서 아래로 세지만 지도 y 는 아래에서 위로 커진다 — 그래서 뒤집는다.
        return ((x - ox) / res * SCALE, ((h - 1) - (y - oy) / res) * SCALE)

    # 정점 이름표 — 변환이 맞았는지 눈으로 확인하는 근거다.
    for v in yaml.safe_load(NAVGRAPH.read_text())["levels"]["L1"]["vertices"]:
        m = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
        cx, cy = px(float(v[0]), float(v[1]))
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(150, 150, 150))
        if m.get("name") in ("주차장", "입구", "복도-5", "테이블-1번-좌", "안내데스크"):
            d.text((cx + 5, cy - 6), str(m["name"]), fill=(90, 90, 90))

    poses = [r for r in records if r["kind"] == "pose"]
    for i, name in enumerate(sorted({r["robot"] for r in poses})):
        color = COLORS[i % len(COLORS)]
        pts = [px(r["x"], r["y"]) for r in poses if r["robot"] == name]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=3)
        if pts:
            sx, sy = pts[0]
            ex, ey = pts[-1]
            d.ellipse([sx - 6, sy - 6, sx + 6, sy + 6], outline=color, width=3)   # 출발 ○
            d.ellipse([ex - 6, ey - 6, ex + 6, ey + 6], fill=color)               # 도착 ●
            d.text((ex + 9, ey - 6), name, fill=color)

    img.save(out_png)
    return out_png


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", help="record_run.py 가 만든 .jsonl")
    ap.add_argument("--out-dir", default="/tmp/run_out")
    ap.add_argument("--title", default="주문 한 건의 진행")
    args = ap.parse_args()

    records = load_records(args.record)
    out = pathlib.Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    png = draw_track(records, out / "track.png")
    md = out / "timeline.md"
    md.write_text(
        f"# {args.title}\n\n## 요약\n\n{summary_md(records)}\n\n"
        f"## 궤적\n\n![[{png.name}]]\n\n○ 출발 · ● 도착\n\n"
        f"## 타임라인\n\n{timeline_md(records)}\n"
    )
    print(f"  ✔ {png}")
    print(f"  ✔ {md}")


if __name__ == "__main__":
    main()
