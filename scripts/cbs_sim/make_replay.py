#!/usr/bin/env python3
"""녹화(record.json) + navgraph → 자립 HTML 리플레이.

fetch 는 file:// 에서 CORS 로 막히므로 데이터를 템플릿에 **끼워 넣어 한 파일로** 만든다
(cbs_viewer 와 같은 방식). 열기만 하면 된다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--navgraph", required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--level", default="L1")
    args = ap.parse_args()

    ng = yaml.safe_load(open(args.navgraph, encoding="utf-8"))
    lv = ng["levels"][args.level]
    vertices = [{"i": i, "x": float(v[0]), "y": float(v[1])}
                for i, v in enumerate(lv["vertices"])]
    lanes = [{"u": int(l[0]), "v": int(l[1])} for l in lv["lanes"]]

    rec = json.loads(pathlib.Path(args.record).read_text(encoding="utf-8"))

    data = {
        "title": args.title,
        "vertices": vertices,
        "lanes": lanes,
        "frames": rec.get("frames", []),
        "events": rec.get("events", []),
        "plans": rec.get("plans", []),
    }

    tpl = pathlib.Path(args.template).read_text(encoding="utf-8")
    marker = "/*RUN_JSON*/"
    if marker not in tpl:
        print(f"템플릿에 {marker} 자리표시자가 없습니다: {args.template}", file=sys.stderr)
        return 2
    pathlib.Path(args.out).write_text(
        tpl.replace(marker, json.dumps(data, ensure_ascii=False), 1), encoding="utf-8")

    print(f"[replay] {args.out}  (프레임 {len(data['frames'])}, "
          f"시간표 {len(data['plans'])}회, 사건 {len(data['events'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
