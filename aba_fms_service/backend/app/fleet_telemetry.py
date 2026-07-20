"""
플릿 텔레메트리 캐시 + 명령 링크 — ROS2 기반 (2026-07-07 텔레메트리, 07-08 명령 전환).

배경: 기존 /api/control/state 는 요청마다 로봇 robot_agent 에 HTTP 프록시했는데,
응답이 ~103KB(맵 그리드 포함)라 1초 폴링 × 3대 × 다중 페이지에서 라즈베리파이
CPU 가 포화되어 502/504 가 발생했다. 이 모듈은 데이터 흐름을 push 로 뒤집고,
명령(goal/mission 등)도 HTTP 대신 ROS2 토픽으로 보낸다:

  로봇1(도메인88)·로봇2(89)·로봇3(87) ─(domain_bridge, /pinky{N}/*)→ 서버 도메인 86
  → 본 모듈의 rclpy 노드가 구독·캐시 → /api/control/state 는 캐시에서 즉시 응답.
  ← 명령은 /pinky{N}/fleet_cmd 발행 → 브릿지 reversed 중계 → 로봇 fleet_link 실행
    → /pinky{N}/fleet_cmd_result 로 결과 회신 (id 상관관계, send_command()).

- 텔레메트리 5종(amcl_pose/map/plan/battery)에 더해 로봇 fleet_link 모듈이
  /fleet_status(미션, 2초 하트비트)·/fleet_costmaps(5초)를 발행한다 — HTTP 폴링 0.
- 레거시 ros_bridge(프로세스 env 도메인 88, 조이스틱 등)와 분리된 **별도 rclpy
  Context** 를 도메인 86으로 고정 생성한다.
- 공유기 멀티캐스트 차단 환경이므로 로봇 쪽 ROS_STATIC_PEERS=192.168.0.19 가 전제
  (로봇의 /home/robotPrj/rosPkg/ros_source.sh 또는 pm2_start_*.sh 에 설정됨).

이 모듈의 최상위는 rclpy 의존성이 없어야 한다(ROS 미설치 환경에서도 import 가능).
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from typing import Any

# ── 플릿 구성: DB(rc_robots.ip_address)에서 매 기동 시 읽어온다 ────────────
# IP를 코드에 하드코딩해두면 DB에서 로봇 IP가 바뀔 때(예: Pinky-3 재배치) 서로
# 어긋나서 조용히 끊긴다(2026-07-20 실제로 겪음: DB 172.30.1.83 vs 코드
# 192.168.0.2). key/prefix(어느 domain_bridge_pinky{N}.yaml/도메인인지)는
# 물리적 로봇 슬롯에 고정된 정적 설정이라 이름으로만 매핑하고, IP는 DB가 진실.
FLEET_ROBOTS: dict[str, dict[str, Any]] = {}

def bridge_key(name: str) -> str:
    """로봇 이름 -> 브릿지 키 (토픽 접두사로 쓰는 이름).

        Pinky-1 -> pinky1        PinkySim -> pinkySim

    하이픈·공백을 없애고 첫 글자만 소문자로 내린다. 예전에는 이 매핑을 딕셔너리로
    박아뒀는데, 로봇을 새로 등록할 때마다 코드를 고쳐야 했고 그걸 잊으면 그 로봇만
    조용히 누락됐다. 규칙으로 바꿔서 DB 등록만으로 끝나게 했다.

    scripts/gen_domain_bridges.py 가 브릿지 설정을 만들 때 쓰는 규칙과 같아야 한다 —
    다르면 토픽 접두사와 조회 키가 어긋나 상태가 안 보인다.
    """
    s = name.replace("-", "").replace("_", "").replace(" ", "")
    return s[:1].lower() + s[1:] if s else s


def _load_fleet_robots() -> dict[str, dict[str, Any]]:
    """rc_robots 테이블에서 활성 pinky 로봇의 IP를 읽어 FLEET_ROBOTS 형태로 구성한다.
    브릿지 키는 이름에서 유도한다(bridge_key) — 등록만 하면 코드 수정 없이 잡힌다."""
    import pymysql
    from sqlalchemy.engine import make_url

    from app.config import ROBOT_DATABASE_URL

    url = make_url(ROBOT_DATABASE_URL.replace("+aiomysql", ""))
    result: dict[str, dict[str, Any]] = {}
    try:
        conn = pymysql.connect(
            host=url.host, port=url.port or 3306, user=url.username,
            password=url.password or "", database=url.database, connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, ip_address FROM rc_robots WHERE robot_type='pinky' AND is_active=1"
                )
                for name, ip in cur.fetchall():
                    key = bridge_key(name)
                    if not key:
                        print(f"[fleet_telemetry] 이름이 비어 있는 로봇을 건너뜀 (ip={ip})", flush=True)
                        continue
                    result[ip] = {"key": key, "prefix": f"/{key}"}
        finally:
            conn.close()
    except Exception as e:
        print(f"[fleet_telemetry] FLEET_ROBOTS DB 로드 실패, 빈 목록으로 진행: {e}", flush=True)
    return result

TELEMETRY_DOMAIN_ID = 86
FRESH_SEC = 30.0        # 이 시간 안에 아무 수신도 없으면 캐시를 stale 로 판정(HTTP 폴백)
PLAN_MAX_POINTS = 400   # /plan 다운샘플 상한 (ros_bridge 와 동일 정책)
CMD_TIMEOUT_SEC = 3.0   # 명령 결과 대기 기본값 (기존 HTTP TIMEOUT_SEC 과 동일 체감)

_lock = threading.Lock()
_started = False

# 명령 링크 상태 — publisher 는 텔레메트리 스레드가 만들고 FastAPI 스레드가 쓴다.
# 동일 publisher 동시 publish 는 rcl 수준 보장이 없으므로 _pub_lock 으로 직렬화.
_cmd_pubs: dict[str, Any] = {}          # ip -> rclpy Publisher
_StringMsg: Any = None                  # std_msgs.msg.String (텔레메트리 스레드가 설정)
_pub_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}  # cmd_id -> {"event", "result"}
_pending_lock = threading.Lock()


def _empty_entry() -> dict[str, Any]:
    return {
        "map": None,
        "pose": None,
        "path": [],
        "local_costmap": None,
        "global_costmap": None,
        "battery": {"percent": None, "voltage": None},
        "mission": {"status": "idle", "current": None, "names": [], "loop": False, "schedule_minutes": 0},
        "_last_ros_at": 0.0,     # 텔레메트리 토픽(nav2 계열) 마지막 수신 시각
        "_last_status_at": 0.0,  # fleet_status 하트비트 시각 — nav2 다운/robot_agent 생존 구분용
    }


_cache: dict[str, dict[str, Any]] = {}  # _telemetry_thread 시작 시 FLEET_ROBOTS 로드 후 채움


def get_state(ip_address: str) -> dict[str, Any] | None:
    """robot_agent /api/state 와 동일 스키마의 캐시 상태를 반환. stale 이면 None(호출측 HTTP 폴백)."""
    entry = _cache.get(ip_address)
    if entry is None:
        return None
    with _lock:
        last = max(entry["_last_ros_at"], entry["_last_status_at"])
        if last <= 0 or (time.time() - last) > FRESH_SEC:
            return None
        if entry["pose"] is None and entry["map"] is None:
            return None
        return {
            "map": entry["map"],
            "pose": entry["pose"],
            "path": list(entry["path"]),
            "local_costmap": entry["local_costmap"],
            "global_costmap": entry["global_costmap"],
            "battery": dict(entry["battery"]),
            "mission": dict(entry["mission"]),
        }


def telemetry_info() -> dict[str, Any]:
    """진단용: 로봇별 캐시 신선도 + 명령 링크 상태."""
    now = time.time()
    out = {}
    with _lock:
        for ip, e in _cache.items():
            pub = _cmd_pubs.get(ip)
            subs = None
            if pub is not None:
                try:
                    subs = pub.get_subscription_count()
                except Exception:
                    subs = None
            out[FLEET_ROBOTS[ip]["key"]] = {
                "ip": ip,
                "ros_age_sec": round(now - e["_last_ros_at"], 1) if e["_last_ros_at"] else None,
                "status_age_sec": round(now - e["_last_status_at"], 1) if e["_last_status_at"] else None,
                "has_pose": e["pose"] is not None,
                "has_map": e["map"] is not None,
                "has_costmap": e["local_costmap"] is not None or e["global_costmap"] is not None,
                "cmd_subscribers": subs,  # 0 이면 브릿지/로봇 미연결 → 명령은 HTTP 폴백
            }
    return out


def send_command(
    ip_address: str,
    action: str,
    args: dict | None = None,
    timeout: float = CMD_TIMEOUT_SEC,
    assume_sent_on_timeout: bool = False,
) -> dict[str, Any] | None:
    """ROS2 로 명령을 보내고 결과를 기다린다. 링크 불가/타임아웃이면 None(호출측 HTTP 폴백).

    반환 dict 스키마(로봇 fleet_link 발행): {"id","ok","status","data","msg"}
    """
    pub = _cmd_pubs.get(ip_address)
    if pub is None or _StringMsg is None:
        return None
    try:
        if pub.get_subscription_count() == 0:
            return None  # 브릿지 미기동/로봇 오프라인 — timeout 만큼 기다리지 않고 즉시 폴백
    except Exception:
        return None

    cmd_id = uuid.uuid4().hex
    entry: dict[str, Any] = {"event": threading.Event(), "result": None}
    with _pending_lock:
        _pending[cmd_id] = entry
    payload = json.dumps({"id": cmd_id, "ts": time.time(), "action": action, "args": args or {}})
    try:
        with _pub_lock:
            pub.publish(_StringMsg(data=payload))
    except Exception:
        with _pending_lock:
            _pending.pop(cmd_id, None)
        return None
    ok = entry["event"].wait(timeout)
    with _pending_lock:
        _pending.pop(cmd_id, None)  # 타임아웃이어도 pop — 누수 방지(늦은 결과는 콜백이 무시)
    if not ok and assume_sent_on_timeout:
        return {
            "id": cmd_id,
            "ok": True,
            "status": 202,
            "data": {"success": True, "accepted": True, "pending_result": True},
            "msg": "명령을 전송했지만 결과 응답이 시간 안에 도착하지 않았습니다.",
        }
    return entry["result"] if ok else None


# ── ROS 구독 노드 ───────────────────────────────────────────────────────────

def _yaw_from_quat(q) -> float:
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


def _telemetry_thread() -> None:
    global _StringMsg, FLEET_ROBOTS
    try:
        FLEET_ROBOTS = _load_fleet_robots()
        for ip in FLEET_ROBOTS:
            _cache.setdefault(ip, _empty_entry())

        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        from geometry_msgs.msg import PoseWithCovarianceStamped
        from nav_msgs.msg import OccupancyGrid, Path
        from std_msgs.msg import Float32, String

        # 레거시 ros_bridge(기본 컨텍스트, env 도메인)와 독립된 컨텍스트를 도메인 86으로 생성
        ctx = rclpy.Context()
        rclpy.init(context=ctx, domain_id=TELEMETRY_DOMAIN_ID)

        qos_latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        node = Node("fleet_telemetry", context=ctx)
        _StringMsg = String

        def _touch(ip: str) -> None:
            _cache[ip]["_last_ros_at"] = time.time()

        def _make_handlers(ip: str):
            def on_pose(msg: PoseWithCovarianceStamped) -> None:
                p = msg.pose.pose
                with _lock:
                    _cache[ip]["pose"] = {
                        "x": round(p.position.x, 4),
                        "y": round(p.position.y, 4),
                        "yaw": round(_yaw_from_quat(p.orientation), 4),
                    }
                    _touch(ip)

            def on_map(msg: OccupancyGrid) -> None:
                with _lock:
                    _cache[ip]["map"] = {
                        "width": msg.info.width,
                        "height": msg.info.height,
                        "resolution": msg.info.resolution,
                        "origin_x": msg.info.origin.position.x,
                        "origin_y": msg.info.origin.position.y,
                        "data": list(msg.data),
                    }
                    _touch(ip)

            def on_plan(msg: Path) -> None:
                poses = msg.poses
                step = max(1, len(poses) // PLAN_MAX_POINTS)
                pts = [
                    {"x": round(p.pose.position.x, 4), "y": round(p.pose.position.y, 4)}
                    for p in poses[::step]
                ]
                with _lock:
                    _cache[ip]["path"] = pts
                    _touch(ip)

            def on_batt_pct(msg: Float32) -> None:
                with _lock:
                    _cache[ip]["battery"]["percent"] = round(float(msg.data), 1)
                    _touch(ip)

            def on_batt_volt(msg: Float32) -> None:
                with _lock:
                    _cache[ip]["battery"]["voltage"] = round(float(msg.data), 2)
                    _touch(ip)

            def on_fleet_status(msg: String) -> None:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    return
                mission = data.get("mission")
                with _lock:
                    if isinstance(mission, dict):
                        _cache[ip]["mission"] = mission
                    # 하트비트는 _last_ros_at 과 분리 — nav2 다운을 은폐하지 않도록
                    _cache[ip]["_last_status_at"] = time.time()

            def on_fleet_costmaps(msg: String) -> None:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    return
                with _lock:
                    _cache[ip]["local_costmap"] = data.get("local_costmap")
                    _cache[ip]["global_costmap"] = data.get("global_costmap")
                    _cache[ip]["_last_status_at"] = time.time()

            return on_pose, on_map, on_plan, on_batt_pct, on_batt_volt, on_fleet_status, on_fleet_costmaps

        def on_cmd_result(msg: String) -> None:
            try:
                res = json.loads(msg.data)
                cmd_id = str(res.get("id"))
            except Exception:
                return
            with _pending_lock:
                entry = _pending.get(cmd_id)
            if entry is not None:  # 없으면 타임아웃 후 늦은 결과 — 무시
                entry["result"] = res
                entry["event"].set()

        for ip, cfg in FLEET_ROBOTS.items():
            pre = cfg["prefix"]
            on_pose, on_map, on_plan, on_pct, on_volt, on_status, on_costmaps = _make_handlers(ip)
            # amcl_pose·map·fleet_status 는 TRANSIENT_LOCAL — 구독 즉시 마지막 값을 받는다
            node.create_subscription(PoseWithCovarianceStamped, f"{pre}/amcl_pose", on_pose, qos_latched)
            node.create_subscription(OccupancyGrid, f"{pre}/map", on_map, qos_latched)
            node.create_subscription(Path, f"{pre}/plan", on_plan, 10)
            node.create_subscription(Float32, f"{pre}/battery/percent", on_pct, 10)
            node.create_subscription(Float32, f"{pre}/battery/voltage", on_volt, 10)
            node.create_subscription(String, f"{pre}/fleet_status", on_status, qos_latched)
            node.create_subscription(String, f"{pre}/fleet_costmaps", on_costmaps,
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE))
            node.create_subscription(String, f"{pre}/fleet_cmd_result", on_cmd_result, 10)
            _cmd_pubs[ip] = node.create_publisher(String, f"{pre}/fleet_cmd", 10)

        print(f"[fleet_telemetry] ROS 구독+명령링크 시작 (domain {TELEMETRY_DOMAIN_ID}, robots {list(FLEET_ROBOTS)})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fleet_telemetry] 비활성화(HTTP 폴백만 동작): {e}", flush=True)


def start() -> None:
    """FastAPI 시작 시 한 번 호출한다."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_telemetry_thread, daemon=True, name="fleet-telemetry").start()
