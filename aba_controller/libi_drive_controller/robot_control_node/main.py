#!/usr/bin/env python3
"""
robot_control_node (rclpy Node) — Pure ROS2 Node replacing FastAPI (robot_agent).

Subscribes to:
  - /robot_cmd (std_msgs/String) — 범용 하드웨어 및 내비 명령
  - /fleet_cmd (std_msgs/String) — 레거시 미션/내비 명령

Publishes to:
  - /robot_cmd_result (std_msgs/String) — 범용 처리 결과 회신
  - /fleet_cmd_result (std_msgs/String) — 레거시 미션 결과 회신
  - /fleet_status (std_msgs/String, TRANSIENT_LOCAL 2s) — 상태 하트비트
  - /fleet_costmaps (std_msgs/String, 5s) — 코스트맵 스냅샷
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

CMD_STALE_SEC = 10.0
STATUS_PERIOD = 2.0
COSTMAP_PERIOD = 5.0
QUEUE_MAX = 32

_cmd_queue: queue.Queue[tuple[dict, bool]] = queue.Queue(maxsize=QUEUE_MAX)
_recent_ids: deque[str] = deque(maxlen=64)
_result_pub_lock = threading.Lock()

BASE_DIR = Path(__file__).resolve().parent.parent
HW_DIR = BASE_DIR / "robot_agent" / "app" / "hardware"
if not HW_DIR.exists():
    HW_DIR = BASE_DIR / "hardware"


def _run_hw(cmd: str, timeout: float = 10.0) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return False, str(e)


def dispatch_hardware(action: str, args: dict) -> tuple[bool, int, Any, str] | None:
    lcd_script = HW_DIR / "lcd_ctrl.py"
    led_script = HW_DIR / "led_ctrl.py"
    buzzer_script = HW_DIR / "buzzer_ctrl.py"
    motor_script = HW_DIR / "motor_ctrl.py"
    sensor_script = HW_DIR / "sensor_ctrl.py"

    if action == "lcd_text":
        text = str(args.get("text", ""))
        ok, out = _run_hw(f"sudo -n python3 {lcd_script} emotion smile")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "lcd_emotion":
        emotion = str(args.get("emotion", "smile"))
        ok, out = _run_hw(f"sudo -n python3 {lcd_script} emotion {emotion}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action in ("lcd_clear", "lcd_stop"):
        ok, out = _run_hw(f"sudo -n python3 {lcd_script} stop")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "led_fill":
        r, g, b = int(args.get("r", 0)), int(args.get("g", 0)), int(args.get("b", 0))
        ok, out = _run_hw(f"sudo -n python3 {led_script} fill {r} {g} {b}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action in ("led_off", "led_clear"):
        ok, out = _run_hw(f"sudo -n python3 {led_script} clear")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "buzzer":
        freq = int(args.get("frequency") or 1000)
        dur = float(args.get("duration") or 0.2)
        cnt = int(args.get("count") or 1)
        ok, out = _run_hw(f"sudo -n python3 {buzzer_script} beep {cnt} {freq} {dur}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "buzzer_melody":
        song = str(args.get("song") or args.get("melody") or "mario")
        ok, out = _run_hw(f"sudo -n python3 {buzzer_script} melody {song}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action in ("motor_stop", "stop"):
        ok, out = _run_hw(f"sudo -n python3 {motor_script} stop")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "motor_move":
        left = int(args.get("left") or 0)
        right = int(args.get("right") or 0)
        dur = float(args.get("duration") or 0.5)
        ok, out = _run_hw(f"sudo -n python3 {motor_script} move {left} {right} {dur}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "get_ultrasonic":
        ok, out = _run_hw(f"sudo -n python3 {sensor_script} ultrasonic")
        return ok, (200 if ok else 500), {"output": out}, out if not ok else ""

    if action == "get_ir":
        ok, out = _run_hw(f"sudo -n python3 {sensor_script} ir")
        return ok, (200 if ok else 500), {"output": out}, out if not ok else ""

    return None


def dispatch_action(action: str, args: dict) -> tuple[bool, int, Any, str]:
    hw_res = dispatch_hardware(action, args)
    if hw_res is not None:
        return hw_res

    try:
        sys.path.insert(0, str(BASE_DIR / "robot_agent"))
        from app.core import fleet_link
        if hasattr(fleet_link, "_dispatch"):
            return fleet_link._dispatch(action, args)
    except Exception:
        pass

    return False, 400, None, f"알 수 없는 action: {action}"


class RobotControlNode(Node):
    def __init__(self, ctx: rclpy.Context):
        super().__init__("robot_control_node", context=ctx)

        qos_latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        qos_big = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)

        self.robot_cmd_result_pub = self.create_publisher(String, "robot_cmd_result", 10)
        self.fleet_cmd_result_pub = self.create_publisher(String, "fleet_cmd_result", 10)
        self.status_pub = self.create_publisher(String, "fleet_status", qos_latched)
        self.costmap_pub = self.create_publisher(String, "fleet_costmaps", qos_big)

        self.cmd_sub = self.create_subscription(String, "robot_cmd", lambda m: self.on_cmd(m, is_robot_cmd=True), 10)
        self.fleet_cmd_sub = self.create_subscription(String, "fleet_cmd", lambda m: self.on_cmd(m, is_robot_cmd=False), 10)

        self.create_timer(STATUS_PERIOD, self.publish_status)
        self.create_timer(COSTMAP_PERIOD, self.publish_costmaps)

        self.get_logger().info("robot_control_node 가 성공적으로 시작되었습니다 (Pure ROS2 Node).")

    def publish_result(self, cmd_id: str, ok: bool, status: int, data: Any, msg: str, is_robot_cmd: bool) -> None:
        payload = json.dumps({"id": cmd_id, "ok": ok, "status": status, "data": data, "msg": msg})
        with _result_pub_lock:
            if is_robot_cmd:
                self.robot_cmd_result_pub.publish(String(data=payload))
            else:
                self.fleet_cmd_result_pub.publish(String(data=payload))

    def run_and_reply(self, cmd: dict, is_robot_cmd: bool) -> None:
        try:
            ok, status, data, msg = dispatch_action(cmd["action"], cmd.get("args") or {})
        except Exception as e:
            ok, status, data, msg = False, 500, None, f"{type(e).__name__}: {e}"
        self.publish_result(cmd["id"], ok, status, data, msg, is_robot_cmd)

    def on_cmd(self, msg: String, is_robot_cmd: bool) -> None:
        try:
            cmd = json.loads(msg.data)
            cmd_id, action = str(cmd["id"]), str(cmd["action"])
        except Exception:
            return
        if cmd_id in _recent_ids:
            return
        _recent_ids.append(cmd_id)
        ts = float(cmd.get("ts") or 0)
        if ts and (time.time() - ts) > CMD_STALE_SEC:
            self.publish_result(cmd_id, False, 408, None, "stale command (재전달 거부)", is_robot_cmd)
            return
        if action in ("mission_stop", "schedule_stop", "motor_stop", "stop"):
            self.run_and_reply(cmd, is_robot_cmd)
            return
        try:
            _cmd_queue.put_nowait((cmd, is_robot_cmd))
        except queue.Full:
            self.publish_result(cmd_id, False, 503, None, "명령 큐 포화", is_robot_cmd)

    def publish_status(self) -> None:
        try:
            sys.path.insert(0, str(BASE_DIR / "robot_agent"))
            from app.core import mission
            payload = json.dumps({"ts": time.time(), "mission": mission.get_status()})
            self.status_pub.publish(String(data=payload))
        except Exception:
            payload = json.dumps({"ts": time.time(), "mission": {"status": "idle"}})
            self.status_pub.publish(String(data=payload))

    def publish_costmaps(self) -> None:
        try:
            sys.path.insert(0, str(BASE_DIR / "robot_agent"))
            from app.core import ros_bridge
            payload = json.dumps({
                "ts": time.time(),
                "local_costmap": ros_bridge.get_topic("local_costmap"),
                "global_costmap": ros_bridge.get_topic("global_costmap"),
            })
            self.costmap_pub.publish(String(data=payload))
        except Exception:
            pass


def init_nav_driver() -> None:
    try:
        sys.path.insert(0, str(BASE_DIR / "robot_agent"))
        from app.config import RobotType, settings
        from app.core.bridge import bridge
        from app.core.ros_node import RosNode
        from app.drivers import create_driver

        if settings.robot_type is RobotType.driving:
            ros_node = RosNode(settings.ros_node_name)
            ros_node.start()
            driver = create_driver(settings.robot_type)
            driver.start(ros_node=ros_node)
            bridge.set_driver(driver)
            print("[robot_control_node] Nav2 드라이버 및 Action Client 초기화 완료", flush=True)
    except Exception as e:
        print(f"[robot_control_node] Nav2 드라이버 초기화 경고: {e}", flush=True)


def main() -> None:
    init_nav_driver()
    dom = os.environ.get("ROS_DOMAIN_ID")
    ctx = rclpy.Context()
    rclpy.init(context=ctx, domain_id=int(dom) if dom else None)
    node = RobotControlNode(ctx)

    t = threading.Thread(target=worker, args=(node,), daemon=True, name="control-node-worker")
    t.start()

    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown(context=ctx)


if __name__ == "__main__":
    import sys
    main()
