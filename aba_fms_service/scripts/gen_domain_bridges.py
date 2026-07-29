#!/usr/bin/env python3
"""rc_robots(DB) 를 읽어 로봇별 domain_bridge 설정을 생성한다.

    python3 scripts/gen_domain_bridges.py            # 생성만
    python3 scripts/gen_domain_bridges.py --run      # 생성 후 브릿지 전부 기동
    python3 scripts/gen_domain_bridges.py --check    # 생성 없이 DB 와 기존 파일 비교

## 왜 필요한가

전에는 `config/domain_bridge_pinky{1,2,3}.yaml` 에 도메인이 손으로 박혀 있었다.
관제 패널에서 로봇 `domain_id` 를 고쳐도 이 파일은 그대로라, 실제로
`pinky-3` 이 DB 에는 119, YAML 에는 87 로 어긋난 채 방치돼 있었다.
이제 DB 가 유일한 출처다 — 패널에서 도메인을 바꾸고 이 스크립트를 다시 돌리면 끝.

## 로봇 추가 (.env → DB)

로봇을 늘릴 때 패널까지 열지 않아도 되게, 루트 `.env` 의 `PINKY{N}_IP` 를 먼저
`rc_robots` 에 반영한다(없으면 등록, IP 가 바뀌었으면 갱신). 도메인은 `PINKY{N}_DOMAIN`
이 있으면 그 값, 없으면 `116+N`. **삭제·비활성화는 하지 않는다** — sim 로봇처럼 .env 에
없는 로봇이 정상적으로 존재하기 때문이다.

    PINKY1_IP=172.30.1.81   # .env 에 이 한 줄 → 다음 기동에 pinky-1(domain 117) 등록

## 브릿지 키

토픽 접두사(`/pinky1/...`)로 쓰는 이름이다. 로봇 이름에서 기계적으로 유도한다:
하이픈·공백 제거 후 첫 글자만 소문자.

    pinky-1 -> pinky1    pinky-sim-2 -> pinkysim2

기존에 손으로 관리하던 매핑 4개를 그대로 재현하는 규칙이라, 이미 돌고 있는
토픽 이름이 바뀌지 않는다.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config" / "domain_bridge.template.yaml"
OUT_DIR = ROOT / "config" / "generated"
ENV_FILE = ROOT.parent / ".env"

# .env 에 PINKY{N}_DOMAIN 이 없을 때 쓰는 도메인 규칙: PINKY{N} → 116+N.
# 실물 pinky-3 이 이미 119 라 그 값과 어긋나지 않는 유일한 규칙이다.
# ⚠️ 도메인은 로봇 셸의 ROS_DOMAIN_ID 와 **반드시 같아야** 브릿지가 로봇을 찾는다.
#    다른 번호를 쓰려면 .env 에 PINKY{N}_DOMAIN 을 명시한다.
DOMAIN_BASE = 116


def bridge_key(name: str) -> str:
    s = name.replace("-", "").replace("_", "").replace(" ", "")
    return s[:1].lower() + s[1:] if s else s


def parse_env(text: str) -> dict:
    """.env 를 KEY=VALUE 로 읽는다. 셸이 아니라 이 스크립트가 직접 읽는 이유:
    ros-domain-bridge.sh 는 .env 를 source 하지 않고, tmux 창 환경도 못 믿는다."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return out


def current_env() -> dict:
    """.env 파일 값 + **셸에 이미 있는 값이 이긴다** — 런처와 같은 규칙(_load_env.sh:11-16).

    안 맞추면 `PINKY1_IP=x ./fms_service.sh` 같은 일회성 override 에서 DDS 피어는 x 를,
    DB(텔레메트리 대상)는 파일 값을 보게 되어 둘이 갈린다.
    """
    env = parse_env(ENV_FILE.read_text()) if ENV_FILE.exists() else {}
    for k, v in os.environ.items():
        if v and re.fullmatch(r"PINKY\d+_(IP|DOMAIN)", k):
            env[k] = v
    return env


def env_robots(env: dict) -> list:
    """.env 의 `PINKY{N}_IP` 를 로봇 등록 후보로 바꾼다. 빈 값은 아직 없는 로봇이라 건너뛴다."""
    out = []
    for key, ip in sorted(env.items()):
        m = re.fullmatch(r"PINKY(\d+)_IP", key)
        if not m or not ip:
            continue
        n = int(m.group(1))
        domain = env.get(f"PINKY{n}_DOMAIN") or str(DOMAIN_BASE + n)
        out.append({"name": f"pinky-{n}", "ip": ip, "domain": int(domain), "n": n})
    return out


def sync_env(conn, robots):
    """.env 로봇을 rc_robots 에 반영한다 — **등록과 IP 갱신만.**

    삭제·비활성화는 하지 않는다: .env 는 이 머신의 배선일 뿐이라, 거기 없다고 해서
    로봇이 사라진 게 아니다(sim 로봇들도 .env 에 없다). 지우는 건 패널에서 한다.
    """
    if not robots:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT name, ip_address, domain_id, robot_type FROM rc_robots")
        have = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        for r in robots:
            if r["name"] not in have:
                # ⚠️ INSERT 는 **ON DUPLICATE KEY** 로 간다. name 이 unique 라
                # (models.py Robot.name), 브릿지가 두 곳에서 동시에 뜨면 둘 다 "없다"고
                # 판단해 한쪽이 duplicate-key 로 죽고 **브릿지 기동 전체가 날아간다.**
                cur.execute(
                    "INSERT INTO rc_robots (name, robot_type, ip_address, port, domain_id,"
                    " description, is_active, created_at, updated_at)"
                    " VALUES (%s, 'pinky', %s, 9001, %s, %s, 1, NOW(), NOW())"
                    " ON DUPLICATE KEY UPDATE ip_address=VALUES(ip_address), updated_at=NOW()",
                    (r["name"], r["ip"], r["domain"], ".env 자동 등록"))
                print(f"  등록  {r['name']}  ip={r['ip']}  domain={r['domain']}")
                print(f"        └ 이 로봇 셸의 ROS_DOMAIN_ID 도 {r['domain']} 이어야 합니다")
                continue
            ip, domain, rtype = have[r["name"]]
            # 같은 이름의 **다른 자산**(서버·팔·패널에서 예약해 둔 행)을 덮어쓰지 않는다.
            # .env 는 주행 로봇 배선일 뿐이라, 이름이 겹쳤다고 그 행의 주인이 될 수는 없다.
            if rtype != "pinky":
                print(f"  ⚠ 건너뜀  {r['name']} — DB 에 robot_type='{rtype}' 로 이미 있습니다"
                      f" (.env 의 PINKY{r['n']}_IP 를 지우거나 패널에서 이름을 바꾸세요)")
                continue
            if ip != r["ip"]:
                cur.execute(
                    "UPDATE rc_robots SET ip_address=%s, updated_at=NOW() WHERE name=%s",
                    (r["ip"], r["name"]))
                print(f"  IP갱신  {r['name']}  {ip} → {r['ip']}")
            if domain is None:
                cur.execute(
                    "UPDATE rc_robots SET domain_id=%s, updated_at=NOW() WHERE name=%s",
                    (r["domain"], r["name"]))
                print(f"  도메인 설정  {r['name']} = {r['domain']}")
            elif int(domain) != r["domain"]:
                print(f"  ⚠ {r['name']} 도메인 불일치: DB={domain} / .env 규칙={r['domain']}"
                      f" — DB 를 그대로 둡니다 (.env 에 PINKY{r['n']}_DOMAIN={domain} 를 넣으면 조용해집니다)")
    conn.commit()


def load_robots(sync=True):
    """활성 pinky 로봇 중 domain_id 가 설정된 것만. fleet_telemetry 와 같은 DB 경로를 쓴다."""
    # ⚠️ DB URL 을 **직접 .env 에서도 읽는다.** 셸이 .env 를 안 실어 준 채 이 스크립트를
    #    부르면 app.config 가 전역 DATABASE_URL(=도서관 웹의 `aba` DB)로 폴백해
    #    엉뚱한 DB 를 본다 — 실측 에러: (1054, "Unknown column 'ip_address' in 'SELECT'").
    #    호출 경로가 늘어날 때마다 "source 했나"를 기억하는 대신 여기서 막는다.
    _env = parse_env(ENV_FILE.read_text()) if ENV_FILE.exists() else {}
    for _key in ("ROBOT_DATABASE_URL", "ADMIN_DATABASE_URL"):
        if not os.environ.get(_key) and _env.get(_key):
            os.environ[_key] = _env[_key]

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
        if sync:
            sync_env(conn, env_robots(current_env()))
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
    ap.add_argument("--check", action="store_true", help="생성하지 않고 DB 값만 보고(.env 반영도 안 함)")
    ap.add_argument("--self-test", action="store_true", help="DB 없이 .env 해석 규칙만 검사")
    ap.add_argument("--sync-only", action="store_true",
                    help=".env → rc_robots 반영만 하고 끝낸다(브릿지 생성·기동 안 함)")
    args = ap.parse_args()

    if args.self_test:
        env = parse_env('PINKY1_IP=10.0.0.1\nPINKY2_IP=\n# c\nPINKY3_IP=10.0.0.3  # 주석\n'
                        'PINKY3_DOMAIN=119\nLAPTOP_IP=10.0.0.9\n')
        got = env_robots(env)
        assert [r["name"] for r in got] == ["pinky-1", "pinky-3"], got   # 빈 값은 건너뛴다
        assert got[0]["domain"] == 117 and got[1]["domain"] == 119, got  # 규칙 116+N / 명시값
        assert got[1]["ip"] == "10.0.0.3", got                           # 줄끝 주석 제거
        assert bridge_key("pinky-1") == "pinky1"
        os.environ["PINKY9_IP"] = "10.0.0.9"        # 셸 값이 .env 파일을 이긴다
        assert current_env().get("PINKY9_IP") == "10.0.0.9"
        print("self-test ok")
        return 0

    # ⚠️ `--sync-only` 는 **브릿지 창을 띄우기 전에** 부르라고 있는 것이다.
    #    안 그러면 경쟁이 생긴다: 브릿지 창 안에서 등록이 일어나는데, 같은 순간 뜬
    #    robot-link(상태 어댑터)는 DB 목록을 **한 번만** 읽고 재시도하지 않는다
    #    (robot-link.sh --all). 새 로봇이 첫 기동에서 어댑터 0대로 굳어, 브릿지는 떠도
    #    fleet_node 가 그 로봇을 못 본다. (codex 적대적 검토 2026-07-29)
    robots, skipped = load_robots(sync=not args.check)
    if args.sync_only:
        print(f"  동기화 완료 — 대상 로봇 {len(robots)}대")
        return 0
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
