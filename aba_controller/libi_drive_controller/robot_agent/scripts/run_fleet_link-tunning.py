#!/usr/bin/env python3
"""run_fleet_link.py 의 경량(명령 실행 전용) 튜닝 버전.

이 프로세스의 목적은 오직 하나 — task_adapter(FMS)가 보내는 fleet_cmd 명령을
받아 실행하는 것이다. 대시보드용 실시간 구독(scan/odom/map/plan/cmd_vel_raw/
costmap/battery)은 명령 실행에 전혀 필요 없는데, Pi에서 초당 수십 건 역직렬화로
CPU를 크게 잡아먹는다(단독 프로세스가 반 코어 이상 점유).

여기서는 app 을 import 하기 "전에" FLEET_LINK_LITE 를 켜서 ros_bridge 의
대시보드 구독과 fleet_link 의 costmap 전송 루프를 모두 건너뛴다. 명령 실행에
필요한 것만 남는다: goal_pose/initialpose/cmd_vel 발행, TF pose(get_current_pose),
navigate_to_pose 액션, slam_toolbox 서비스, 그리고 fleet_cmd 구독.
"""
import os

# ros_bridge / fleet_link 의 구독·스레드 생성 시점에 반영되려면 import 전에 켜야 한다.
os.environ.setdefault("FLEET_LINK_LITE", "1")

import signal
import sys
import threading
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있어 sys.path[0] 가 scripts/ 로 잡힌다.
# robot_agent 루트(=app 패키지의 부모)를 경로에 넣어야 `app` 을 import 할 수 있다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import fleet_link, ros_bridge


def main() -> None:
    ros_bridge.start()
    fleet_link.start()
    print("[run_fleet_link-tunning] 명령 실행 전용 경량 모드(대시보드 구독 제외). "
          "ros_bridge + fleet_link 기동됨. Ctrl+C로 종료.", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()


if __name__ == "__main__":
    main()
