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
import threading
import time
from collections import deque
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


#: libi_modes BT 가 소유하는 명령. 이 실행기는 수락만 하고 실행하지 않는다.
#  (BT 가 처리한 뒤 실행 층 액션 goal/arm_home/... 으로 되돌아온다)
BT_LAYER_ACTIONS = frozenset({"navigate", "guide"})


def _dispatch(action: str, args: dict) -> tuple[bool, int, Any, str]:
    """(ok, status, data, msg) — ok=False 는 HTTP 라면 에러 응답이었을 상황."""
    from app.core import locations, mission, ros_bridge

    needs_ros = action in (
        "goal", "goto", "home", "mission_start", "schedule_start",
        "slam_reset", "slam_save_map", "waypoint_goto",
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

    if action == "waypoint_get":
        from app.core import waypoints
        return True, 200, waypoints.get_graph(), ""

    if action == "waypoint_save":
        from app.core import waypoints
        vertices = args.get("vertices") or {}
        lanes = args.get("lanes") or []
        graph = waypoints.set_graph(vertices, lanes)
        return True, 200, graph, ""

    if action == "waypoint_goto":
        import math
        from app.core import waypoints

        name = str(args.get("name", "")).strip()
        graph = waypoints.get_graph()
        vertices = graph["vertices"]
        if name not in vertices:
            return False, 404, None, f"'{name}' 노드가 없습니다"

        pose = ros_bridge.get_current_pose()
        if pose is None:
            return False, 409, None, "현재 위치(TF)를 아직 알 수 없습니다"
        cur_x, cur_y, _ = pose
        start = waypoints.nearest_vertex(cur_x, cur_y, vertices)
        if start is None:
            return False, 404, None, "그래프에 노드가 없습니다"

        path = waypoints.route(start, name, graph)
        if path is None:
            return False, 409, None, f"'{start}' → '{name}' 간선 경로를 찾을 수 없습니다"

        traveled = []
        for i, node in enumerate(path):
            v = vertices[node]
            if i < len(path) - 1:
                nxt = vertices[path[i + 1]]
                yaw = math.atan2(nxt["y"] - v["y"], nxt["x"] - v["x"])
            else:
                yaw = float(v.get("yaw", 0.0))
            ok = ros_bridge.nav_to(float(v["x"]), float(v["y"]), yaw)
            traveled.append(node)
            if not ok:
                return False, 502, {"path": path, "reached": traveled}, \
                    f"'{node}' 구간에서 주행 실패/중단"

        return True, 200, {"success": True, "name": name, "path": path}, ""

    if action in BT_LAYER_ACTIONS:
        # ── BT 층 명령 — 여기서 실행하지 않는다 ──────────────────────────────
        # `/fleet_cmd` 에는 소비자가 둘이다:
        #   ① 이 실행기(fleet_link)      goal/goto/home/… 을 실제로 수행
        #   ② libi_modes BT (providers)  명령을 blackboard 에 올려 브랜치가 처리
        # FMS 가 BT 에게 주는 명령(navigate 등)까지 여기서 실행하면 **같은 주행이 두 번**
        # 나간다. 그렇다고 "알 수 없는 action" 으로 답하면 FMS 가 그걸 다리 실패로 받아
        # 주문이 FAILED 로 떨어진다(실측: perform_action 이 그랬다).
        # 그래서 **수락만 하고 아무것도 하지 않는다** — 실제 수행은 BT 가 자기 드라이버로
        # 다시 이 함수를 부를 때(goal/arm_home/…) 일어난다.
        return True, 202, {"accepted": True, "handled_by": "bt"}, ""

    if action == "dock":
        # 정밀 도킹(주차). BT 의 ReturningBranch 가 nav2 로 입구까지 온 다음 이걸 부른다.
        #
        # nav2 는 주차장 "근처"까지만 데려다준다 — 충전 단자에 맞추려면 마커·테이프·벽
        # 거리를 보는 폐루프가 필요하고, 그건 app/routers/park_dock.py 에 이미 있다.
        # 여기서는 HTTP 를 거치지 않고 같은 프로세스에서 그 코루틴을 돌린다.
        #
        # ⚠️ **아직 아무도 이 액션을 보내지 않는다.** BT 의 복귀 드라이버는 주차장
        #    좌표로 `goal` 을 보낼 뿐이다. 정밀 주차를 붙이려면 그쪽을 먼저 바꿔야 한다
        #    (scripts/drive-pi/dock/README.md 의 미결 1~4). 실물 미검증.
        from app.core import dock_runner

        ok, why = dock_runner.run_park(**{k: v for k, v in args.items() if v is not None})
        _publish_docked(ok)
        if not ok:
            return False, 502, {"docked": False}, why
        return True, 200, {"docked": True}, ""

    if action == "perform_action":
        # 팔 동작(집기/놓기). FMS orchestrator 가 다리 하나로 내려보낸다.
        #
        # 이 명령은 **BT 도 같이 본다** — libi_modes 의 providers 가 /fleet_cmd 를 구독해
        # blackboard(active_command) 에 올리고, WorkingBranch 의 ArmExec 가 집어 실행한다.
        # 여기서 하는 일은 그 명령을 **받았다고 인정하고 결과를 돌려주는 것**뿐이다.
        # 인정하지 않으면 "알 수 없는 action" 이 /fleet_cmd_result 로 나가고, FMS 는 그걸
        # 다리 실패로 받아 주문이 FAILED 로 떨어진다(실측).
        #
        # ⚠️ 팔 하드웨어(libi_handy_controller)는 아직 없다. 그래서 지금은 **접수만** 하고
        #    성공으로 답한다. 실제 팔이 붙으면 여기서 팔 드라이버를 호출하고 그 결과를
        #    돌려주면 된다 — FMS 쪽 코드는 그대로 둬도 된다.
        act = str(args.get("action") or "")
        book = str(args.get("book") or "")
        at = str(args.get("at") or "")
        print(f"[fleet_link] perform_action 접수: {act}({book}) at {at} "
              f"— 팔 미배선이라 즉시 성공 처리", flush=True)
        return True, 200, {"success": True, "action": act, "book": book, "at": at,
                           "arm": "stub"}, ""

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
        status_pub = node.create_publisher(String, "fleet_status", qos_latched)
        costmap_pub = node.create_publisher(String, "fleet_costmaps", qos_big)
        # 도킹 확인 신호. libi_modes 의 ReturnNavigation 이 이걸 받아야
        # RETURNING → CHARGING 이 일어난다. 상태이지 사건이므로 latched 로 둔다.
        from std_msgs.msg import Bool
        docked_pub = node.create_publisher(Bool, "is_docked", qos_latched)
        _set_docked_publisher(lambda v: docked_pub.publish(Bool(data=bool(v))))

        def publish_result(cmd_id: str, ok: bool, status: int, data: Any, msg: str) -> None:
            payload = json.dumps(
                {"id": cmd_id, "ok": ok, "status": status, "data": data, "msg": msg})
            with _result_pub_lock:
                result_pub.publish(String(data=payload))

        def run_and_reply(cmd: dict) -> None:
            try:
                ok, status, data, msg = _dispatch(cmd["action"], cmd.get("args") or {})
            except Exception as e:
                ok, status, data, msg = False, 500, None, f"{type(e).__name__}: {e}"
            publish_result(cmd["id"], ok, status, data, msg)

        def on_cmd(msg: "String") -> None:
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
                publish_result(cmd_id, False, 408, None, "stale command (재전달 거부)")
                return
            # 정지 계열은 인라인 즉시 실행 — 워커가 블로킹돼 있어도 정지 보장
            if action in ("mission_stop", "schedule_stop"):
                run_and_reply(cmd)
                return
            try:
                _cmd_queue.put_nowait(cmd)
            except queue.Full:
                publish_result(cmd_id, False, 503, None, "명령 큐 포화")

        node.create_subscription(String, "fleet_cmd", on_cmd, 10)

        def worker() -> None:
            while True:
                run_and_reply(_cmd_queue.get())

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

        threads = [("fleet-link-worker", worker),
                   ("fleet-link-status", status_loop)]
        # FLEET_LINK_LITE=1(명령 실행 전용 경량 모드)이면 costmap 전송 루프를 띄우지
        # 않는다. ros_bridge 도 costmap 구독을 건너뛰어 보낼 데이터가 없고,
        # costmap dict 의 json.dumps 직렬화 자체도 CPU를 많이 쓴다.
        if not os.getenv("FLEET_LINK_LITE"):
            threads.append(("fleet-link-costmap", costmap_loop))
        for name, fn in threads:
            threading.Thread(target=fn, daemon=True, name=name).start()

        print(f"[fleet_link] 시작 (domain {dom or 'env 기본'})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fleet_link] 비활성화(HTTP 경로만 동작): {e}", flush=True)


_docked_publish = None


def _set_docked_publisher(fn) -> None:
    """ROS 스레드가 만든 발행자를 모듈에 걸어 둔다 (_dispatch 는 노드를 모른다)."""
    global _docked_publish
    _docked_publish = fn


def _publish_docked(value: bool) -> None:
    if _docked_publish is not None:
        _docked_publish(value)


def start() -> None:
    """robot_agent 기동 시 한 번 호출 (server.py lifespan)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_link_thread, daemon=True, name="fleet-link").start()
