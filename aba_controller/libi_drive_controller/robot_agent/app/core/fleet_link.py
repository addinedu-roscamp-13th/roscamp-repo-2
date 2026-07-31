"""
로봇(robot_agent) 온보드 전용 — 중앙서버와의 ROS2 fleet link (2026-07-08).

⚠️ 이 파일은 중앙서버 백엔드가 import 하지 않는다. robot_agent 저장소의
`app/core/fleet_link.py` 로 배포되어 로봇 위에서만 실행된다
(배포: `python3 scripts/deploy_fleet_link.py`, 관례는 line_dock_robot.py 와 동일).

배경: 중앙서버↔robot_agent 가 HTTP 폴링으로 통신해 라즈베리파이 CPU 가
포화됐다. 텔레메트리 5종은 7/7 에 domain_bridge push 로 전환됐고, 이 모듈이
나머지(미션 상태·costmap·명령)를 ROS2 토픽으로 옮긴다. 카메라/LCD/부저 등
프론트→로봇 직접 HTTP 경로는 그대로다.

토픽 (std_msgs/String JSON, 로봇 자신의 도메인에서 무접두 이름 —
서버 도메인 86 에는 domain_bridge 가 /pinky{N}/* 로 개명 중계):
  /fleet_cmd        구독  {"id","ts","action","args"}          서버→로봇 (reversed 중계)
  /fleet_cmd_result 발행  {"id","ok","status","data","msg"}    명령 실행 결과
  /fleet_status     발행  {"ts","mission":{...}}               2초 하트비트 (TRANSIENT_LOCAL)
  /fleet_costmaps   발행  {"ts","local_costmap","global_costmap"}  5초 주기

명령 dispatch 는 HTTP nav_router(driving.py 플랫 /api/*)와 **동일한 의미**로
locations/mission/ros_bridge 모듈 함수를 호출하고 동일 스키마의 응답 dict 를
data 로 돌려준다. ok=False 는 HTTP 라면 4xx/5xx 였을 상황(status 에 동등 코드).

안전 규칙:
  - /fleet_cmd 는 VOLATILE 필수 — TRANSIENT_LOCAL 이면 로봇/브릿지 재시작 시
    구명령이 재전달되어 모터가 움직일 수 있다. 추가로 ts 10초 초과 stale 거부
    + 최근 처리 id 덱(64) 중복 거부로 이중 방어한다.
  - mission_stop/schedule_stop 은 워커 큐를 거치지 않고 구독 콜백에서 인라인
    실행한다(워커가 긴 호출에 잡혀 있어도 정지는 즉시).
  - 기존 ros_bridge(기본 rclpy 컨텍스트)와 독립된 별도 Context 를 쓴다 —
    초기화 순서 무관, 실패 시 HTTP 경로 무손상.
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

CMD_STALE_SEC = 10.0    # ts 가 이보다 오래된 명령은 거부 (재전달 방어)
STATUS_PERIOD = 2.0     # /fleet_status 하트비트 주기 (~200B)
COSTMAP_PERIOD = 5.0    # /fleet_costmaps 주기 (~100KB, RELIABLE depth1)
QUEUE_MAX = 32

_started = False
_cmd_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
_recent_ids: deque[str] = deque(maxlen=64)
_result_pub_lock = threading.Lock()  # cmd_result 는 콜백/워커 두 스레드가 발행


# ── 명령 실행 (HTTP nav_router 와 동일 의미/스키마) ─────────────────────────

def _save_locations(locs: dict | None) -> None:
    """서버가 명령 args 에 내장해 보낸 위치들을 저장 (HTTP 사전동기화 대체)."""
    from app.core import locations
    for nm, p in (locs or {}).items():
        try:
            locations.set_location(str(nm), float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
        except Exception as e:
            print(f"[fleet_link] location '{nm}' 저장 실패: {e}", flush=True)


def _run_hw(cmd: str, timeout: float = 10.0) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return False, str(e)


def _dispatch_hardware(action: str, args: dict) -> tuple[bool, int, Any, str] | None:
    """하드웨어 드라이버 액션 디스패치 (LCD, LED, 부저, 모터, 센서). 지원하지 않는 액션이면 None 반환."""
    hw_dir = Path(__file__).resolve().parent.parent / "hardware"
    lcd_script = hw_dir / "lcd_ctrl.py"
    led_script = hw_dir / "led_ctrl.py"
    buzzer_script = hw_dir / "buzzer_ctrl.py"
    motor_script = hw_dir / "motor_ctrl.py"
    sensor_script = hw_dir / "sensor_ctrl.py"

    if action == "lcd_emotion":
        emotion = str(args.get("emotion", "smile"))
        ok, out = _run_hw(f"sudo -n python3 {lcd_script} emotion {emotion}")
        return ok, (200 if ok else 500), {"success": ok, "output": out}, out if not ok else ""

    if action == "lcd_clear":
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


def _dispatch(action: str, args: dict) -> tuple[bool, int, Any, str]:
    """(ok, status, data, msg) — ok=False 는 HTTP 라면 에러 응답이었을 상황."""
    # 1) 하드웨어 액션 디스패치
    hw_res = _dispatch_hardware(action, args)
    if hw_res is not None:
        return hw_res

    # 2) 내비게이션 및 미션 액션 디스패치
    from app.core import locations, mission, ros_bridge

    needs_ros = action in (
        "goal", "goto", "home", "mission_start", "schedule_start",
        "slam_reset", "slam_save_map",
    )
    if needs_ros and not ros_bridge.is_active():
        return False, 503, None, "ROS 브리지가 활성화되지 않았습니다"

    if action == "goal":
        ok = ros_bridge.send_nav_goal(float(args["x"]), float(args["y"]), float(args.get("yaw", 0.0)))
        return True, 200, {"success": ok}, ""

    if action == "goto":
        name = str(args.get("name", "")).strip()
        if args.get("location"):
            _save_locations({name: args["location"]})
        p = locations.get(name)
        if p is None:
            return False, 404, None, f"'{name}' 위치가 등록되지 않았습니다"
        ok = ros_bridge.send_nav_goal(float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
        return True, 200, {"success": ok, "name": name}, ""

    if action == "loc_set":
        name = str(args.get("name", "")).strip()
        if not name:
            return False, 400, None, "name이 필요합니다"
        if args.get("x") is not None and args.get("y") is not None:
            x, y, yaw = float(args["x"]), float(args["y"]), float(args.get("yaw") or 0.0)
        else:
            if not ros_bridge.is_active():
                return False, 503, None, "ROS 브리지가 활성화되지 않았습니다"
            pose = ros_bridge.get_current_pose()
            if pose is None:
                return False, 409, None, "현재 위치(TF)를 아직 알 수 없습니다. 위치추정/주행을 먼저 확인하세요."
            x, y, yaw = pose
        locations.set_location(name, x, y, yaw)
        return True, 200, {"success": True, "name": name, "x": x, "y": y, "yaw": yaw}, ""

    if action == "loc_delete":
        name = str(args.get("name", "")).strip()
        if locations.delete(name):
            return True, 200, {"success": True, "name": name}, ""
        return False, 404, None, f"'{name}' 위치가 없습니다"

    if action == "mission_start":
        _save_locations(args.get("locations"))
        names = args.get("names") or locations.names_sorted()
        loop = bool(args.get("loop"))
        ok = mission.start_mission(names, loop)
        return True, 200, {"success": ok, "names": names, "loop": loop}, ""

    if action == "mission_stop":
        mission.stop_mission()
        return True, 200, {"success": True}, ""

    if action == "home":
        return True, 200, {"success": mission.go_home()}, ""

    if action == "schedule_start":
        _save_locations(args.get("locations"))
        names = args.get("names") or locations.names_sorted()
        minutes = int(args.get("minutes") or 0)
        ok = mission.start_schedule(minutes, names, bool(args.get("loop")))
        return True, 200, {"success": ok, "minutes": minutes, "names": names}, ""

    if action == "schedule_stop":
        mission.stop_schedule()
        return True, 200, {"success": True}, ""

    if action == "slam_reset":
        ok = ros_bridge.slam_reset()
        if ok:
            ros_bridge.clear_map()
        return True, 200, {"success": ok}, ""

    if action == "slam_save_map":
        name = str(args.get("name", "")).strip() or time.strftime("pinky_map_%Y%m%d_%H%M%S")
        return True, 200, {"success": ros_bridge.slam_save_map(name), "name": name}, ""

    return False, 400, None, f"알 수 없는 action: {action}"


# ── ROS 스레드들 ────────────────────────────────────────────────────────────

def _link_thread() -> None:
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String

        # ros_bridge(기본 컨텍스트)와 독립 — 도메인은 로봇 자신의 env 값
        ctx = rclpy.Context()
        dom = os.environ.get("ROS_DOMAIN_ID")
        rclpy.init(context=ctx, domain_id=int(dom) if dom else None)
        node = rclpy.create_node("fleet_link", context=ctx)

        qos_latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        qos_big = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)

        result_pub = node.create_publisher(String, "fleet_cmd_result", 10)
        robot_result_pub = node.create_publisher(String, "robot_cmd_result", 10)
        status_pub = node.create_publisher(String, "fleet_status", qos_latched)
        costmap_pub = node.create_publisher(String, "fleet_costmaps", qos_big)

        def publish_result(cmd_id: str, ok: bool, status: int, data: Any, msg: str, is_robot_cmd: bool = False) -> None:
            payload = json.dumps(
                {"id": cmd_id, "ok": ok, "status": status, "data": data, "msg": msg})
            with _result_pub_lock:
                if is_robot_cmd:
                    robot_result_pub.publish(String(data=payload))
                else:
                    result_pub.publish(String(data=payload))

        def run_and_reply(cmd: dict, is_robot_cmd: bool = False) -> None:
            try:
                ok, status, data, msg = _dispatch(cmd["action"], cmd.get("args") or {})
            except Exception as e:
                ok, status, data, msg = False, 500, None, f"{type(e).__name__}: {e}"
            publish_result(cmd["id"], ok, status, data, msg, is_robot_cmd=is_robot_cmd)

        def on_cmd_generic(msg: "String", is_robot_cmd: bool = False) -> None:
            try:
                cmd = json.loads(msg.data)
                cmd_id, action = str(cmd["id"]), str(cmd["action"])
            except Exception:
                return  # 형식 불량 — 응답 대상 id 도 없음
            if cmd_id in _recent_ids:
                return  # 재전달 중복 — 이미 응답함
            _recent_ids.append(cmd_id)
            ts = float(cmd.get("ts") or 0)
            if ts and (time.time() - ts) > CMD_STALE_SEC:
                publish_result(cmd_id, False, 408, None, "stale command (재전달 거부)", is_robot_cmd=is_robot_cmd)
                return
            # 정지 계열은 인라인 즉시 실행 — 워커가 블로킹돼 있어도 정지 보장
            if action in ("mission_stop", "schedule_stop", "motor_stop", "stop"):
                run_and_reply(cmd, is_robot_cmd=is_robot_cmd)
                return
            try:
                _cmd_queue.put_nowait((cmd, is_robot_cmd))
            except queue.Full:
                publish_result(cmd_id, False, 503, None, "명령 큐 포화", is_robot_cmd=is_robot_cmd)

        node.create_subscription(String, "fleet_cmd", lambda m: on_cmd_generic(m, is_robot_cmd=False), 10)
        node.create_subscription(String, "robot_cmd", lambda m: on_cmd_generic(m, is_robot_cmd=True), 10)

        def worker() -> None:
            while True:
                cmd_item, is_robot_cmd = _cmd_queue.get()
                run_and_reply(cmd_item, is_robot_cmd=is_robot_cmd)

        def status_loop() -> None:
            from app.core import mission
            while True:
                try:
                    payload = json.dumps({"ts": time.time(), "mission": mission.get_status()})
                    status_pub.publish(String(data=payload))
                except Exception:
                    pass
                time.sleep(STATUS_PERIOD)

        def costmap_loop() -> None:
            from app.core import ros_bridge
            while True:
                try:
                    payload = json.dumps({
                        "ts": time.time(),
                        "local_costmap": ros_bridge.get_topic("local_costmap"),
                        "global_costmap": ros_bridge.get_topic("global_costmap"),
                    })
                    costmap_pub.publish(String(data=payload))
                except Exception:
                    pass
                time.sleep(COSTMAP_PERIOD)

        for name, fn in (("fleet-link-worker", worker),
                         ("fleet-link-status", status_loop),
                         ("fleet-link-costmap", costmap_loop)):
            threading.Thread(target=fn, daemon=True, name=name).start()

        print(f"[fleet_link] 시작 (domain {dom or 'env 기본'})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fleet_link] 비활성화(HTTP 경로만 동작): {e}", flush=True)


def start() -> None:
    """robot_agent 기동 시 한 번 호출 (server.py lifespan)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_link_thread, daemon=True, name="fleet-link").start()
