#!/usr/bin/env python3
"""FastAPI(robot_agent) 없이 fleet_link만 단독 실행한다.

FMS(fleet_cmd)와 통신하려면 ros_bridge(nav goal 발행/구독)와 fleet_link(fleet_cmd
구독) 두 rclpy 스레드만 있으면 되고, 둘 다 FastAPI/driver 초기화에 의존하지
않는다. sim은 robot_agent(FastAPI) 자체가 없으므로, Waypoint "이동" 명령이
fleet_cmd 토픽으로 sim에 도달하려면 이 스크립트가 떠 있어야 한다.

sim.sh가 이 스크립트를 자동으로 같이 띄운다(별도 tmux 창). ROS_DOMAIN_ID는
sim.sh가 export한 값을 그대로 물려받는다.
"""
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
    print("[run_fleet_link] ros_bridge + fleet_link 기동됨. Ctrl+C로 종료.", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()


if __name__ == "__main__":
    main()
