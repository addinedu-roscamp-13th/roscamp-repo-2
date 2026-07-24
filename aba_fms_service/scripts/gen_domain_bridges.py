#!/usr/bin/env python3
"""rc_robots(DB) 를 읽어 로봇별 domain_bridge 설정을 생성한다.

    python3 scripts/gen_domain_bridges.py            # 생성만
    python3 scripts/gen_domain_bridges.py --run      # 생성 후 브릿지 전부 기동
    python3 scripts/gen_domain_bridges.py --check    # 생성 없이 DB 와 기존 파일 비교

## 왜 필요한가

전에는 `config/domain_bridge_pinky{1,2,3}.yaml` 에 도메인이 손으로 박혀 있었다.
관제 패널에서 로봇 `domain_id` 를 고쳐도 이 파일은 그대로라, 실제로
`Pinky-3` 이 DB 에는 119, YAML 에는 87 로 어긋난 채 방치돼 있었다.
이제 DB 가 유일한 출처다 — 패널에서 도메인을 바꾸고 이 스크립트를 다시 돌리면 끝.

## 브릿지 키

토픽 접두사(`/pinky1/...`)로 쓰는 이름이다. 로봇 이름에서 기계적으로 유도한다:
하이픈·공백 제거 후 첫 글자만 소문자.

    Pinky-1 -> pinky1    PinkySim -> pinkySim

기존에 손으로 관리하던 매핑 4개를 그대로 재현하는 규칙이라, 이미 돌고 있는
토픽 이름이 바뀌지 않는다.
"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config" / "domain_bridge.template.yaml"
OUT_DIR = ROOT / "config" / "generated"


def bridge_key(name: str) -> str:
    s = name.replace("-", "").replace("_", "").replace(" ", "")
    return s[:1].lower() + s[1:] if s else s


def load_robots():
    """활성 pinky 로봇 중 domain_id 가 설정된 것만. fleet_telemetry 와 같은 DB 경로를 쓴다."""
    sys.path.insert(0, str(ROOT / "backend"))
    import pymysql
    from sqlalchemy.engine import make_url

    from app.config import ROBOT_DATABASE_URL

    url = make_url(ROBOT_DATABASE_URL.replace("+aiomysql", ""))
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306, user=url.username,
        password=url.password or "", database=url.database, charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, ip_address, domain_id, is_active FROM rc_robots "
                "WHERE robot_type = 'pinky' ORDER BY name"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    out, skipped = [], []
    for name, ip, domain_id, is_active in rows:
        if not is_active:
            skipped.append((name, "비활성"))
        elif domain_id is None:
            skipped.append((name, "domain_id 미설정"))
        else:
            out.append({"name": name, "ip": ip, "domain": int(domain_id),
                        "key": bridge_key(name)})
    return out, skipped


def render(robot: str) -> str:
    return (TEMPLATE.read_text()
            .replace("{NAME}", robot["name"])
            .replace("{IP}", robot["ip"] or "-")
            .replace("{FROM_DOMAIN}", str(robot["domain"]))
            .replace("{KEY}", robot["key"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="생성 후 브릿지를 전부 기동")
    ap.add_argument("--check", action="store_true", help="생성하지 않고 DB 값만 보고")
    args = ap.parse_args()

    robots, skipped = load_robots()
    for name, why in skipped:
        print(f"  건너뜀  {name} ({why})")
    if not robots:
        print("생성할 로봇이 없습니다. 관제 패널에서 domain_id 를 설정하세요.")
        return 1

    if args.check:
        for r in robots:
            existing = ROOT / "config" / f"domain_bridge_{r['key']}.yaml"
            note = ""
            if existing.exists():
                for line in existing.read_text().splitlines():
                    if line.startswith("from_domain:"):
                        old = line.split(":", 1)[1].strip()
                        note = "  일치" if old == str(r["domain"]) else f"  ← 기존 파일은 {old} (불일치)"
            print(f"  {r['name']:10} key={r['key']:10} domain={r['domain']}{note}")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for r in robots:
        path = OUT_DIR / f"domain_bridge_{r['key']}.yaml"
        path.write_text(render(r))
        written.append(path)
        print(f"  생성  {path.relative_to(ROOT)}  ({r['name']} domain {r['domain']})")

    if args.run:
        procs = []
        for path in written:
            print(f"  기동  {path.name}")
            procs.append(subprocess.Popen(
                ["ros2", "run", "domain_bridge", "domain_bridge", str(path)]))
        print(f"\n브릿지 {len(procs)}개 기동. Ctrl+C 로 전부 종료.")
        try:
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
