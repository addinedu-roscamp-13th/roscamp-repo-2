#!/usr/bin/env python3
"""fleet_link(로봇 온보드 ROS2 명령/상태 링크) 배포 스크립트 (2026-07-08).

저장소의 `aba_controller/.../robot_agent/app/core/fleet_link.py` 를 각 핑키 로봇의
같은 경로로 올리고, server.py lifespan 에 fleet_link.start() 를 멱등하게 배선한 뒤
pm2 robot_agent 를 재시작한다.

⚠️ [2026-07-22] 원본이 `backend/app/fleet_link_robot.py` 였는데, 그건 로봇 소스의
**복사본**이라 시간이 지나며 갈라졌다. 실제로 BT 주행 전환(`BT_LAYER_ACTIONS`) 이
복사본에 반영되지 않아, 그대로 배포했으면 실물이 `navigate` 를 직접 실행해
**같은 주행이 두 갈래로 나갔다**. 복사본을 지우고 로봇 소스를 직접 올린다.

사용:
  python3 scripts/deploy_fleet_link.py 192.168.0.42   # 한 대
  python3 scripts/deploy_fleet_link.py --all          # 3대 순차

검증(무주행): 재시작 후 /api/state 200 + 로봇 도메인에서 /fleet_* 토픽 존재 확인.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parent.parent
# 저장소 루트 = aba_fms_service 의 부모
SRC = (REPO.parent / "aba_controller" / "libi_drive_controller" / "robot_agent"
       / "app" / "core" / "fleet_link.py")

ROBOT_IPS = ["192.168.0.28", "192.168.0.42", "192.168.0.2"]
SSH_USER, SSH_PW = "pinky", "1"

AGENT_DIR = "/home/robotPrj/controller/drive/robot_agent"
DST = f"{AGENT_DIR}/app/core/fleet_link.py"
SERVER_PY = f"{AGENT_DIR}/app/core/server.py"

# server.py lifespan 의 bridge.set_driver(driver) 직후에 삽입한다.
ANCHOR = "    bridge.set_driver(driver)\n"
WIRING = (
    "\n"
    "    # [2026-07-08] fleet link — 중앙서버와 ROS2 토픽 통신 (HTTP 폴링 대체).\n"
    "    # 배포 원본: aba_controller/.../robot_agent/app/core/fleet_link.py (deploy_fleet_link.py)\n"
    "    if settings.robot_type is RobotType.driving:\n"
    "        from app.core import fleet_link\n"
    "        fleet_link.start()\n"
)


def run(ssh: paramiko.SSHClient, cmd: str, timeout: float = 30.0) -> tuple[int, str]:
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return rc, (out.read().decode() + err.read().decode()).strip()


def deploy(ip: str) -> bool:
    print(f"\n===== {ip} =====")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username=SSH_USER, password=SSH_PW, timeout=8)
    sftp = ssh.open_sftp()
    try:
        # 1) fleet_link.py 업로드
        sftp.put(str(SRC), DST)
        print(f"  업로드: {DST}")

        # 2) server.py 배선 (멱등 — 'fleet_link' 문자열이 있으면 스킵)
        with sftp.open(SERVER_PY) as f:
            content = f.read().decode()
        if "fleet_link" in content:
            print("  server.py: 이미 배선됨 — 스킵")
        else:
            if ANCHOR not in content:
                print(f"  [실패] server.py 에서 앵커({ANCHOR.strip()!r})를 못 찾음 — 수동 확인 필요")
                return False
            stamp = time.strftime("%Y%m%d")
            run(ssh, f"cp {SERVER_PY} {SERVER_PY}.bak_{stamp}")
            with sftp.open(SERVER_PY, "w") as f:
                f.write(content.replace(ANCHOR, ANCHOR + WIRING, 1))
            print(f"  server.py: fleet_link.start() 배선 (백업 .bak_{stamp})")

        # 3) 문법 검증
        rc, out = run(ssh, f"python3 -m py_compile {DST} {SERVER_PY}")
        if rc != 0:
            print(f"  [실패] py_compile: {out}")
            return False
        print("  py_compile OK")

        # 4) robot_agent 재시작
        rc, out = run(ssh, "bash -lc 'pm2 restart robot_agent'", timeout=60)
        if rc != 0:
            print(f"  [실패] pm2 restart: {out}")
            return False
        print("  pm2 restart robot_agent OK")

        # 5) 무주행 검증: /api/state 200 + fleet 토픽 존재 (기동 시간 감안해 재시도)
        code = ""
        for _ in range(8):
            time.sleep(4)
            rc, code = run(ssh, "curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:9001/api/state")
            if code.strip() == "200":
                break
        print(f"  /api/state → HTTP {code}")
        rc, topics = run(
            ssh,
            "bash -c 'dom=$(grep -oE \"ROS_DOMAIN_ID=[0-9]+\" ~/.bashrc | tail -1 | cut -d= -f2); "
            "export ROS_DOMAIN_ID=$dom; source /opt/ros/jazzy/setup.bash; "
            "timeout 12 ros2 topic list 2>/dev/null | grep fleet'",
            timeout=40,
        )
        print(f"  fleet 토픽:\n{topics or '  (없음 — robot_agent 로그 확인 필요)'}")
        ok = code.strip() == "200" and "/fleet_status" in topics
        print(f"  → {'성공' if ok else '검증 미통과'}")
        return ok
    finally:
        sftp.close()
        ssh.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ips", nargs="*", help="로봇 IP (생략+--all 시 3대)")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    targets = ROBOT_IPS if args.all else args.ips
    if not targets:
        ap.error("IP 를 지정하거나 --all 을 사용하세요")
    results = {ip: deploy(ip) for ip in targets}
    print("\n===== 결과 =====")
    for ip, ok in results.items():
        print(f"  {ip}: {'OK' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
