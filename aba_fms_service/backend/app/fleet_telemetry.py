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
import re
import threading
import time
import uuid
from typing import Any

from app import panel_bridge

# ── 플릿 구성: DB(rc_robots.ip_address)에서 매 기동 시 읽어온다 ────────────
# IP를 코드에 하드코딩해두면 DB에서 로봇 IP가 바뀔 때(예: Pinky-3 재배치) 서로
# 어긋나서 조용히 끊긴다(2026-07-20 실제로 겪음: DB 172.30.1.83 vs 코드
# 192.168.0.2). key/prefix(어느 domain_bridge_pinky{N}.yaml/도메인인지)는
# 물리적 로봇 슬롯에 고정된 정적 설정이라 이름으로만 매핑하고, IP는 DB가 진실.
#: 브릿지 키 -> {key, prefix, name, ip}.
#
# ⚠️ 예전엔 **IP 로 키를 잡았다.** 그런데 sim 로봇은 전부 127.0.0.1 이라, 두 번째
#    로봇을 등록하는 순간 첫 번째가 조용히 덮여 사라졌다 — 그 로봇에는 명령이
#    영영 안 갔다("명령 링크 없음", 실측 R12 2대 시험). 로봇 이름은 유일하므로
#    이름에서 유도한 브릿지 키를 쓴다.
FLEET_ROBOTS: dict[str, dict[str, Any]] = {}

# ROS 토픽 이름으로 쓸 수 있는 토큰: 영문으로 시작하는 영숫자/'_' (숫자 시작 불가).
_VALID_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def bridge_key(name: str) -> str:
    """로봇 이름 -> 브릿지 키 (토픽 접두사로 쓰는 이름).

        Pinky-1 -> pinky1        PinkySim -> pinkySim

    하이픈·공백을 없애고 첫 글자만 소문자로 내린다. 예전에는 이 매핑을 딕셔너리로
    박아뒀는데, 로봇을 새로 등록할 때마다 코드를 고쳐야 했고 그걸 잊으면 그 로봇만
    조용히 누락됐다. 규칙으로 바꿔서 DB 등록만으로 끝나게 했다.

    scripts/gen_domain_bridges.py 가 브릿지 설정을 만들 때 쓰는 규칙과 같아야 한다 —
    다르면 토픽 접두사와 조회 키가 어긋나 상태가 안 보인다.

    ⚠️ 키는 그대로 **ROS 토픽 이름**(`/<key>/scan`)이 된다. 토픽 이름에는 영숫자와 '_'
    만 쓸 수 있으므로, 화면 표기용 한글 이름(`주행로봇 2`)을 그대로 키로 쓰면
    `Invalid topic name` 이 나고 텔레메트리 스레드가 통째로 죽는다 — 실제로 그래서
    스캔·명령 링크가 모두 끊겼고 relative_move 가 계속 503("라이다 스캔 없음")을 냈다.
    그래서 토큰으로 못 쓰는 이름은 **끝의 번호로 `pinky<N>`** 를 만든다
    (`주행로봇 2` → `pinky2`) — 도메인 브릿지 설정(config/domain_bridge_pinky2.yaml)이
    쓰는 접두사와 같아진다. 번호조차 없으면 빈 문자열을 돌려주고, 호출측이 그 로봇만
    건너뛴다(나머지 로봇은 살린다).
    """
    s = name.replace("-", "").replace("_", "").replace(" ", "")
    if not s:
        return ""
    s = s[:1].lower() + s[1:]
    if _VALID_KEY_RE.fullmatch(s):
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    return f"pinky{digits}" if digits else ""


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
                        # 이름이 비었거나, 토픽으로 못 쓰는 이름인데 번호도 없는 경우.
                        print(f"[fleet_telemetry] 브릿지 키를 만들 수 없어 건너뜀 "
                              f"(name={name!r}, ip={ip}) — 이름에 번호를 넣어 주세요", flush=True)
                        continue
                    result[key] = {"key": key, "prefix": f"/{key}", "name": name, "ip": ip}
                # IP 를 나눠 쓰는 로봇이 있으면 알려 준다. 명령은 브릿지 키로 가므로
                # 문제없지만, HTTP 폴백(_cache)은 IP 로 갈려 있어 서로를 덮어쓴다.
                by_ip: dict[str, list[str]] = {}
                for cfg in result.values():
                    by_ip.setdefault(str(cfg["ip"]), []).append(cfg["name"])
                for ip, names in by_ip.items():
                    if len(names) > 1:
                        print(f"[fleet_telemetry] ⚠️ IP {ip} 를 {names} 가 공유합니다 — "
                              f"ROS 명령·상태는 정상이지만 HTTP 폴백 캐시는 구분하지 못합니다",
                              flush=True)
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
#: 브릿지 키 -> rclpy Publisher (`/<key>/fleet_cmd`).
#
# ⚠️ 예전엔 IP 로 갈렸다. sim 로봇은 전부 127.0.0.1 이라 **두 로봇이 발행자 하나를
#    공유**했고, 나중에 만들어진 쪽 토픽으로만 명령이 나갔다 — 다른 로봇은 명령을
#    받지 못했다(실측 R12 2대 시험).
_cmd_pubs: dict[str, Any] = {}
_StringMsg: Any = None                  # std_msgs.msg.String (텔레메트리 스레드가 설정)
_pub_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}  # cmd_id -> {"event", "result"}
_pending_lock = threading.Lock()

# ── 라이다 전방/후방 여유거리 + cmd_vel 직접 발행 (relative_move 안전정지용) ──────
# rplidar_link 는 URDF 에서 rpy="0 0 pi" 로 180° 회전 장착이다. 따라서 스캔각 0 = 로봇
# 후방, 스캔각 ±pi = 로봇 전방. base(로봇) 프레임 각도는 scan_angle + LIDAR_YAW_OFFSET
# 로 환산한다(값을 [-pi,pi] 로 wrap). sim 은 IP(127.0.0.1)를 공유하므로 상태·scan·cmd_vel
# 모두 IP가 아닌 bridge key 로 분리해야 한다.
LIDAR_YAW_OFFSET = math.pi
SCAN_SECTOR_HALF_RAD = math.radians(25.0)   # 전/후방 섹터 반각(±25°)
SCAN_FRESH_SEC = 2.0                        # 이보다 오래된 스캔은 신뢰 안 함(정지 판단은 fail-safe)
_scan_lock = threading.Lock()
_clearance: dict[str, dict[str, Any]] = {}  # bridge key -> {"front": m|None, "rear": m|None, "ts": t}
_cmd_vel_pubs: dict[str, Any] = {}          # bridge key -> /pinkyN/cmd_vel rclpy Publisher
_TwistMsg: Any = None                       # geometry_msgs.msg.Twist (텔레메트리 스레드가 설정)


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


_cache: dict[str, dict[str, Any]] = {}  # bridge key -> state


def _unique_key_for_ip(ip_address: str) -> str | None:
    """IP 전용 레거시 호출을 bridge key 로 변환한다.

    sim 은 같은 loopback IP 를 쓰므로 모호한 경우 None 을 돌려준다. 잘못된 로봇 상태나
    cmd_vel 을 쓰는 것보다 HTTP 폴백/안전 정지가 낫다.
    """
    keys = [key for key, cfg in FLEET_ROBOTS.items()
            if str(cfg.get("ip")) == str(ip_address)]
    return keys[0] if len(keys) == 1 else None


def get_state(ip_address: str) -> dict[str, Any] | None:
    """robot_agent /api/state 와 동일 스키마의 캐시 상태를 반환. stale 이면 None(호출측 HTTP 폴백)."""
    key = _unique_key_for_ip(ip_address)
    return _get_state_by_key(key) if key else None


def _get_state_by_key(key: str | None) -> dict[str, Any] | None:
    entry = _cache.get(key or "")
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


def get_state_for_robot(robot_name: str) -> dict[str, Any] | None:
    """이름/bridge key 로 특정 로봇의 캐시를 반환한다 (공유 IP sim 안전)."""
    return _get_state_by_key(key_of(robot_name))


def telemetry_info() -> dict[str, Any]:
    """진단용: 로봇별 캐시 신선도 + 명령 링크 상태."""
    now = time.time()
    out = {}
    with _lock:
        for key, cfg in FLEET_ROBOTS.items():
            e = _cache.get(key) or _empty_entry()
            pub = _cmd_pubs.get(key)
            subs = None
            if pub is not None:
                try:
                    subs = pub.get_subscription_count()
                except Exception:
                    subs = None
            out[key] = {
                "ip": FLEET_ROBOTS[key]["ip"],
                "ros_age_sec": round(now - e["_last_ros_at"], 1) if e["_last_ros_at"] else None,
                "status_age_sec": round(now - e["_last_status_at"], 1) if e["_last_status_at"] else None,
                "has_pose": e["pose"] is not None,
                "has_map": e["map"] is not None,
                "has_costmap": e["local_costmap"] is not None or e["global_costmap"] is not None,
                "cmd_subscribers": subs,  # 0 이면 브릿지/로봇 미연결 → 명령은 HTTP 폴백
            }
    return out


# ── 명령 결과 훅 ────────────────────────────────────────────────────────────
# orchestrator 배선(fleet_dispatch_bridge)이 여기에 핸들러를 건다. 이 모듈이 orchestrator
# 를 직접 알면 순환 의존이 되므로 훅으로 뒤집는다(fleet_link 의 task_state 훅과 같은 방식).
# 훅에서 난 예외는 삼킨다 — 구독 스레드가 죽으면 텔레메트리 전체가 멈춘다.
_cmd_result_hooks: list[Any] = []


def add_cmd_result_hook(fn) -> None:
    if fn not in _cmd_result_hooks:
        _cmd_result_hooks.append(fn)


def _fire_cmd_result_hooks(res: dict) -> None:
    for fn in list(_cmd_result_hooks):
        try:
            fn(res)
        except Exception as exc:  # noqa: BLE001
            print(f"[fleet_telemetry] cmd_result 훅 실패: {exc}", flush=True)


def _norm_name(v: str) -> str:
    return v.replace("-", "").replace("_", "").replace(" ", "").casefold()


def key_of(robot_name: str) -> str | None:
    """로봇 이름(rc_robots.name) → 브릿지 키. 명령 링크가 이 키로 갈린다.

    표기 차이(`Pinky-3` vs `pinky3`)를 흡수한다 — FSM 캐시에서 겪은 것과 같은 함정이다.
    """
    if not robot_name:
        return None
    want = _norm_name(robot_name)
    for key, cfg in FLEET_ROBOTS.items():
        # name 은 DB 원본, key 는 브릿지 키(`Pinky-3` → `pinky3`). 둘 다 본다.
        if _norm_name(str(cfg.get("name", ""))) == want or _norm_name(key) == want:
            return key
    return None


def ip_of(robot_name: str) -> str | None:
    """로봇 이름 → IP. HTTP 폴백 경로가 아직 IP 를 쓴다.

    ⚠️ IP 는 **유일하지 않다** (sim 로봇은 전부 127.0.0.1). 명령을 보낼 때는 쓰지 말고
    `key_of()` 를 쓴다.
    """
    key = key_of(robot_name)
    return str(FLEET_ROBOTS[key]["ip"]) if key else None


def send_command_async(robot_name: str, action: str, args: dict | None = None) -> str | None:
    """명령을 보내고 **기다리지 않고** cmd_id 를 돌려준다. 결과는 훅으로 온다.

    orchestrator 의 다리 dispatch 에서 쓴다 — 거기서 결과를 기다리면 상태기 락을
    수 초간 잡아 다른 주문까지 멈춘다. 반환한 id 가 그대로 orchestrator 의 cmd_id 가 되고,
    `/fleet_cmd_result` 가 같은 id 로 돌아오면 훅이 다리를 완료 처리한다.
    """
    key = key_of(robot_name)
    if key is None:
        return None
    pub = _cmd_pubs.get(key)
    if pub is None or _StringMsg is None:
        return None
    try:
        if pub.get_subscription_count() == 0:
            return None  # 브릿지 미기동/로봇 오프라인
    except Exception:
        return None

    cmd_id = uuid.uuid4().hex
    payload = json.dumps({"id": cmd_id, "ts": time.time(), "action": action, "args": args or {}})
    try:
        with _pub_lock:
            pub.publish(_StringMsg(data=payload))
    except Exception:
        return None
    return cmd_id


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
    # 이 함수는 아직 IP 로 불린다(HTTP 라우터 호환). 명령 링크는 키로 갈리므로 되짚는다.
    # IP 를 나눠 쓰는 로봇이 있으면 **누구에게 보낼지 정할 수 없다** — 조용히 엉뚱한
    # 로봇을 움직이느니 보내지 않는다. 이름을 아는 호출부는 send_command_async 를 쓴다.
    key = _unique_key_for_ip(ip_address)
    if key is None:
        return None
    return _send_command_by_key(key, action, args, timeout, assume_sent_on_timeout)


def send_command_for_robot(
    robot_name: str, action: str, args: dict | None = None,
    timeout: float = CMD_TIMEOUT_SEC, assume_sent_on_timeout: bool = False,
) -> dict[str, Any] | None:
    """이름/bridge key 로 동기 명령을 보낸다 (공유 IP sim 안전)."""
    key = key_of(robot_name)
    return _send_command_by_key(key, action, args, timeout, assume_sent_on_timeout) if key else None


def _send_command_by_key(
    key: str, action: str, args: dict | None, timeout: float, assume_sent_on_timeout: bool,
) -> dict[str, Any] | None:
    pub = _cmd_pubs.get(key)
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


def clearance(ip_address: str, direction: str = "forward") -> float | None:
    """진행 방향의 최소 장애물 거리(m, 라이다 중심 기준). 스캔이 없거나
    SCAN_FRESH_SEC 보다 오래됐으면 None(호출측이 fail-safe 로 판단)."""
    key = _unique_key_for_ip(ip_address)
    return _clearance_by_key(key, direction) if key else None


def _clearance_by_key(key: str, direction: str = "forward") -> float | None:
    with _scan_lock:
        e = _clearance.get(key)
        if e is None or (time.time() - e["ts"]) > SCAN_FRESH_SEC:
            return None
        return e["rear"] if direction == "backward" else e["front"]


def clearance_for_robot(robot_name: str, direction: str = "forward") -> float | None:
    key = key_of(robot_name)
    return _clearance_by_key(key, direction) if key else None


def publish_cmd_vel(ip_address: str, linear: float, angular: float) -> bool:
    """서버 도메인 /pinkyN/cmd_vel 로 Twist 를 발행한다. domain_bridge 가 로봇 /cmd_vel 로 역중계한다."""
    key = _unique_key_for_ip(ip_address)
    return _publish_cmd_vel_by_key(key, linear, angular) if key else False


def _publish_cmd_vel_by_key(key: str, linear: float, angular: float) -> bool:
    pub = _cmd_vel_pubs.get(key)
    if pub is None or _TwistMsg is None:
        return False
    try:
        msg = _TwistMsg()
        msg.linear.x = float(linear)
        msg.angular.z = float(angular)
        with _pub_lock:
            pub.publish(msg)
        return True
    except Exception:
        return False


def publish_cmd_vel_for_robot(robot_name: str, linear: float, angular: float) -> bool:
    key = key_of(robot_name)
    return _publish_cmd_vel_by_key(key, linear, angular) if key else False


# ── ROS 구독 노드 ───────────────────────────────────────────────────────────

def _yaw_from_quat(q) -> float:
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


def _telemetry_thread() -> None:
    global _StringMsg, _TwistMsg, FLEET_ROBOTS
    try:
        FLEET_ROBOTS = _load_fleet_robots()
        for key in FLEET_ROBOTS:
            _cache.setdefault(key, _empty_entry())

        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
        from nav_msgs.msg import OccupancyGrid, Path
        from sensor_msgs.msg import LaserScan
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
        _TwistMsg = Twist

        def _touch(key: str) -> None:
            _cache[key]["_last_ros_at"] = time.time()

        def _make_handlers(key: str):
            def on_pose(msg: PoseWithCovarianceStamped) -> None:
                p = msg.pose.pose
                with _lock:
                    _cache[key]["pose"] = {
                        "x": round(p.position.x, 4),
                        "y": round(p.position.y, 4),
                        "yaw": round(_yaw_from_quat(p.orientation), 4),
                    }
                    _touch(key)

            def on_map(msg: OccupancyGrid) -> None:
                with _lock:
                    _cache[key]["map"] = {
                        "width": msg.info.width,
                        "height": msg.info.height,
                        "resolution": msg.info.resolution,
                        "origin_x": msg.info.origin.position.x,
                        "origin_y": msg.info.origin.position.y,
                        "data": list(msg.data),
                    }
                    _touch(key)

            def on_plan(msg: Path) -> None:
                poses = msg.poses
                step = max(1, len(poses) // PLAN_MAX_POINTS)
                pts = [
                    {"x": round(p.pose.position.x, 4), "y": round(p.pose.position.y, 4)}
                    for p in poses[::step]
                ]
                with _lock:
                    _cache[key]["path"] = pts
                    _touch(key)

            def on_batt_pct(msg: Float32) -> None:
                with _lock:
                    _cache[key]["battery"]["percent"] = round(float(msg.data), 1)
                    _touch(key)

            def on_batt_volt(msg: Float32) -> None:
                with _lock:
                    _cache[key]["battery"]["voltage"] = round(float(msg.data), 2)
                    _touch(key)

            def on_fleet_status(msg: String) -> None:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    return
                mission = data.get("mission")
                with _lock:
                    if isinstance(mission, dict):
                        _cache[key]["mission"] = mission
                    # 하트비트는 _last_ros_at 과 분리 — nav2 다운을 은폐하지 않도록
                    _cache[key]["_last_status_at"] = time.time()

            def on_fleet_costmaps(msg: String) -> None:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    return
                with _lock:
                    _cache[key]["local_costmap"] = data.get("local_costmap")
                    _cache[key]["global_costmap"] = data.get("global_costmap")
                    _cache[key]["_last_status_at"] = time.time()

            def on_scan(msg: "LaserScan") -> None:
                # 전/후방 섹터의 최소 유효거리만 뽑아 캐시(원본 수백 점은 버린다).
                amin = msg.angle_min
                ainc = msg.angle_increment
                rmin = msg.range_min
                rmax = msg.range_max
                front = math.inf
                rear = math.inf
                a = amin
                two_pi = 2.0 * math.pi
                rear_edge = math.pi - SCAN_SECTOR_HALF_RAD
                for r in msg.ranges:
                    if rmin <= r <= rmax:
                        b = a + LIDAR_YAW_OFFSET
                        b = (b + math.pi) % two_pi - math.pi   # wrap [-pi, pi]
                        if -SCAN_SECTOR_HALF_RAD <= b <= SCAN_SECTOR_HALF_RAD:
                            if r < front:
                                front = r
                        elif b >= rear_edge or b <= -rear_edge:
                            if r < rear:
                                rear = r
                    a += ainc
                entry = {
                    "front": None if front == math.inf else round(front, 3),
                    "rear": None if rear == math.inf else round(rear, 3),
                    "ts": time.time(),
                }
                with _scan_lock:
                    _clearance[key] = entry

            return (on_pose, on_map, on_plan, on_batt_pct, on_batt_volt,
                    on_fleet_status, on_fleet_costmaps, on_scan)

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
            # 비동기 발신(send_command_async)은 _pending 에 등록하지 않으므로,
            # entry 유무와 무관하게 훅을 돌린다.
            _fire_cmd_result_hooks(res)

        def _subscribe_robot(cfg: dict[str, Any]) -> None:
            pre = cfg["prefix"]
            key = cfg["key"]
            (on_pose, on_map, on_plan, on_pct, on_volt,
             on_status, on_costmaps, on_scan) = _make_handlers(key)
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
            # 라이다는 sensor QoS(BEST_EFFORT). 브릿지가 발행 QoS 를 자동 매칭한다.
            node.create_subscription(
                LaserScan, f"{pre}/scan", on_scan,
                QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT),
            )
            _cmd_pubs[key] = node.create_publisher(String, f"{pre}/fleet_cmd", 10)
            _cmd_vel_pubs[key] = node.create_publisher(Twist, f"{pre}/cmd_vel", 10)
            # 패널(libi_gui) 요청 통로 — 방향이 위와 반대다(로봇이 요청, 서버가 승인).
            panel_bridge.attach(node, String, pre)

        # 로봇 단위로 막는다 — 하나가 실패해도 나머지는 살린다. 예전엔 접두사가 토픽으로
        # 못 쓰는 값이면(한글 이름 등) 여기서 예외가 나 스레드가 통째로 멈췄고, 스캔·명령
        # 링크가 전부 끊겨 relative_move 가 계속 503("라이다 스캔 없음")을 냈다.
        for cfg in FLEET_ROBOTS.values():
            try:
                _subscribe_robot(cfg)
            except Exception as e:  # noqa: BLE001
                print(f"[fleet_telemetry] ⚠️ '{cfg.get('name')}' 구독 실패 — 이 로봇만 "
                      f"건너뜁니다 (prefix={cfg.get('prefix')}): {e}", flush=True)

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
