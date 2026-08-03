"""바깥 세상에 상태를 알리고, 바깥에서 온 전이 요청을 받는다.

## 소비자가 셋이고 서로 다른 형식을 원한다 — 지어내지 말고 맞춘다

| 소비자 | 토픽 | 형식 |
|---|---|---|
| LED 노드 (`state_led_node.py`) | `fsm_state` | `String` — **상태 이름 원문** (`msg.data.strip().upper()`) |
| FMS 패널 (`fsm_link.py`) | `/libi/fsm_state` | `String` — **JSON** `{robot_id, current_state, ...}` |
| FMS 패널 (BT 뷰) | `/libi/bt_snapshot` | `String` — JSON 트리 |

같은 `std_msgs/String` 인데 내용이 다르다. 둘 다 발행한다.
타입 있는 `FsmState` 도 같이 내보내지만(같은 도메인의 타입 소비자용), 도메인을 건너는
쪽은 전부 String 이다 — `domain_bridge` 가 중계하려면 양쪽 호스트에 타입이 있어야 하는데
`std_msgs` 는 어디에나 있고 `libi_interfaces` 는 아니다. 이 레포의 기존 fleet 토픽
(`fleet_status`, `fleet_cmd_result`)도 같은 이유로 전부 String+JSON 이다.

## 전이 요청 경로가 둘인 이유

`request_transition` **서비스** 는 같은 도메인 호출자용이다. 하지만 `domain_bridge` 는
서비스를 중계하지 못하므로(YAML 에 `services` 키 자체가 없다), FMS 는 상관 id 를 실은
요청/결과 **토픽 쌍** 으로 부른다. 두 경로가 같은 `validate()` 를 통과한다.

## 전이를 tick 경계에서만 적용하는 이유

콜백은 tick 과 다른 시점에 들어온다. 거기서 `current_mode` 를 바로 바꾸면 트리가
절반쯤 순회한 상태에서 상태가 뒤바뀌어, 한 tick 안에서 두 브랜치가 섞여 돈다.
그래서 큐에 넣고 **다음 tick 맨 앞** 에서 적용한다.
"""
import json
import threading
import time

import py_trees
from py_trees.common import Access, Status

from libi_interfaces.msg import FsmState
from libi_interfaces.srv import RequestTransition
from geometry_msgs.msg import Twist
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

#: `/is_docked` 발행용. 도킹은 **상태**라 늦게 뜬 구독자도 마지막 값을 받아야 한다
#: (`dock_confirm.py` 도 TRANSIENT_LOCAL 이었다 — sim_battery.py 주석 참고).
#: VOLATILE 구독자와도 호환된다(발행자 durability 가 더 강한 쪽은 통과).
_LATCHED_PUB = QoSProfile(depth=1)
_LATCHED_PUB.durability = DurabilityPolicy.TRANSIENT_LOCAL
_LATCHED_PUB.reliability = ReliabilityPolicy.RELIABLE

from libi_modes.blackboard import Keys
from libi_modes.registry import ANY, BRANCH_ORDER, START, TRANSITIONS

#: 자율주행이 돌면 **안 되는** 상태. 이 동안 twist_mux 가 자동 제어 입력을 통째로 막는다.
#
# 왜 필요한가 — 지금까지는 "각 BT leaf 가 자기 목표를 끊는다"에 전적으로 기대고 있었다.
# 그런데 leaf 가 이미 끝난 뒤(도착 판정 등)에는 끊을 주체가 사라져서, 화면은 대기인데
# nav2 goal 이 살아 바퀴가 돌았다(2026-07-28 실측). 취소를 **기억하는지**에 의존하는 대신
# 상태 자체로 문을 닫는다.
#
# 잠금은 twist_mux 의 `fsm_motion_lock`(priority 150)이라 자동 제어(follow/recovery/nav)만
# 막고 사람 조작(manual 200 · fleet 150)은 안 막는다 — 대기 상태에서 사람이 로봇을
# 미세 조작해야 할 때가 있고, 그것까지 막으면 복구 수단이 사라진다.
#   설정: pinky_drive/../pinky_bringup/config/twist_mux.yaml
MOTION_LOCKED_STATES = frozenset({"IDLE", "INTERACTING", "ERROR", "CHARGING"})

_STATUS_NAME = {
    Status.SUCCESS: "SUCCESS",
    Status.FAILURE: "FAILURE",
    Status.RUNNING: "RUNNING",
    Status.INVALID: "INVALID",
}


def allowed_targets(current: str) -> set:
    """전이 박스가 허용하는 목적지.

    START("[*]")는 부팅 진입점이라 실제 상태에서 도달할 수 없으므로 건너뛴다 —
    이걸 '모든 상태에서'로 읽으면 WORKING/INTERACTING -> RETURNING 이 열려서
    "작업·응대 중에는 복귀하지 않는다" 규칙이 깨진다.
    """
    out = set()
    for source, target, _ in TRANSITIONS:
        if source == START:
            continue
        if source == ANY or source == current:
            out.add(target)
    out.discard(current)
    return out


def _round_or(v, fallback: float) -> float:
    """`None`(= 값 없음)을 JSON 이 쓸 수 있는 숫자로 바꾼다.

    `null` 을 그대로 내면 받는 쪽마다 다르게 읽는다(0 으로 보거나 예외로 보거나).
    "없음" 을 **범위 밖 한 값**으로 고정한다 — `arrive_tick` 이 `-1` 을 같은 뜻으로
    쓰는 것과 같은 규칙이다.
    """
    if v is None:
        return fallback
    return round(float(v), 1)


def validate(current: str, target: str, force: bool, error_code: str) -> tuple:
    """(accepted, reason).

    force 는 간선 제약과 안전 규칙(ERROR 이탈의 error_code 확인)을 둘 다 푼다 —
    관리자가 직접 켠 이상 원인 미상 상태에서도 수동으로 빠져나갈 수 있어야 한다.
    이 판정이 fsm_model.py(FMS 패널의 사전 검증)와 어긋나면 UI 는 승인이라 보여주는데
    로봇은 거부하는 상황이 생기므로, 로직을 바꿀 때 두 파일을 같이 고쳐야 한다.
    """
    if target not in BRANCH_ORDER:
        return False, f"'{target}' 는 정의된 8종 상태가 아닙니다."
    if current == target:
        return False, f"이미 '{current}' 상태입니다."

    error_guard = current == "ERROR" and not error_code
    if error_guard and not force:
        return False, "ERROR 이탈은 error_code 확인 후에만 허용됩니다. (force 로 우회 가능)"
    if target == "ERROR":
        return True, ""              # ERROR 진입은 언제나 허용 (비상 수단)
    if target in allowed_targets(current):
        if error_guard:
            return True, "강제 전이: error_code 확인 없이 ERROR 이탈을 허용했습니다."
        return True, ""
    if force:
        reason = f"강제 전이: '{current}' -> '{target}' 는 전이 박스에 없는 간선입니다."
        if error_guard:
            reason += " error_code 확인도 생략했습니다."
        return True, reason
    return False, f"'{current}' 에서 '{target}' 로 가는 간선이 없습니다."


class StateIO:
    def __init__(self, node, robot_id, *,
                 manual_hold_sec=2.0,
                 clock=time.monotonic,
                 led_state_topic="fsm_state",
                 state_topic="/libi/fsm_state",
                 snapshot_topic="/libi/bt_snapshot",
                 request_topic="/libi/fsm_transition_request",
                 result_topic="/libi/fsm_transition_result",
                 typed_state_topic="fsm_state_typed",
                 motion_lock_topic="/libi/motion_lock",
                 hold_topic="/cmd_vel_hold",
                 # ⚠️ `providers` 의 `docked_topic` 기본값과 **같아야 한다**(둘 다 상대
                 #    이름 `is_docked` — 같은 노드 네임스페이스라 같은 토픽이 된다).
                 #    다르면 우리가 낸 값을 우리가 못 받아 IS_DOCKED 가 계속 None 이다.
                 docked_topic="is_docked",
                 follow_snapshot_topic="/libi/follow_bt_snapshot",
                 follow_stale_sec=3.0,
                 snapshot_period_sec=0.5,
                 service_name="request_transition"):
        self._node = node
        self._robot_id = robot_id
        self._log = node.get_logger()
        self._lock = threading.Lock()
        self._pending = None
        self._manual_hold_sec = manual_hold_sec
        self._clock = clock

        self._led_pub = node.create_publisher(String, led_state_topic, 10)
        self._state_pub = node.create_publisher(String, state_topic, 10)
        self._snap_pub = node.create_publisher(String, snapshot_topic, 10)
        self._result_pub = node.create_publisher(String, result_topic, 10)
        self._typed_pub = node.create_publisher(FsmState, typed_state_topic, 10)
        # twist_mux 잠금. latched 가 아니라 매 tick 낸다 — twist_mux 는 마지막 값을
        # 들고 있고, 우리가 안 내면 옛 값이 그대로 유효하다. 상태가 바뀌는 순간
        # 반영돼야 하므로 상태 발행과 같은 주기로 붙여 둔다.
        self._lock_pub = node.create_publisher(Bool, motion_lock_topic, 10)

        # ── 잠금 상태의 **정지 명령** ────────────────────────────────────────
        #
        # 잠금(위 Bool)은 twist_mux 에서 아래 입력을 **통과시키지 않을 뿐, 0 을 만들지
        # 않는다.** 그래서 잠긴 순간 `/cmd_vel` 은 침묵이고, 실제로 바퀴를 세우는 건
        # 모터 워치독이다(pinky_bringup/cmd_watchdog.py, 0.5초). 즉 **최대 0.5초는
        # 마지막 속도로 굴러간다** — 1.26×2.16m 축소맵에서 무시할 거리가 아니다.
        #
        # 그래서 잠근 주체가 정지도 같이 낸다: `cmd_vel_hold`(twist_mux priority 160)로
        # 0 을 20Hz. 160 인 이유는 **잠금(150)보다 위, 비상정지(255)보다 아래**여야 하기
        # 때문이다 — 자기가 건 잠금에 자기가 막히면 안 되고, 비상정지를 이겨서도 안 된다.
        #
        # ⚠️ 20Hz 인 이유: twist_mux 는 timeout(0.5s) 안에 안 오는 입력을 없는 것으로
        #    친다. 상태 발행 주기(느릴 수 있다)에 얹으면 그 사이에 잠금이 풀린 것처럼
        #    보여 nav2 가 다시 통과한다. 그래서 **별도 타이머**를 쓴다.
        self._hold_pub = node.create_publisher(Twist, hold_topic, 10)
        self._locked = False
        node.create_timer(0.05, self._publish_hold)      # 20Hz
        node.create_subscription(String, request_topic, self._on_request_topic, 10)
        self._srv = node.create_service(RequestTransition, service_name, self._on_request_srv)

        # 추종 회복 BT 는 **다른 프로세스**(libi_perception)에서 돈다. 그 트리를 받아
        # 우리 스냅샷의 `FollowExec` 밑에 접붙인다 — 관제 화면이 보는 토픽을 하나로 유지한다.
        # 새 파이프를 만들면 FMS 백엔드·WebSocket·프론트 세 군데를 다 고쳐야 한다.
        self._follow_stale_sec = follow_stale_sec
        self._follow_tree = None
        self._follow_at = 0.0

        # ── BT 스냅샷 스로틀 ─────────────────────────────────────────────────
        #
        # ⚠️ [2026-07-30] 스냅샷만 **주기 발행**으로 바꿨다. 상태·잠금·LED 는 매 tick 그대로다.
        #
        # 왜: `publish()` 가 매 tick(10Hz) `_to_dict(root)` 로 **75노드 트리를 재귀 순회**하고
        # `json.dumps` 로 직렬화한다. 실측(Pi 4코어, 2026-07-30) fsm_node 가 CPU **33.9%** 를
        # 썼고, 그 부하에서 nav2 가 제어 주기를 놓쳤다
        # (`Control loop missed its desired rate of 10.0000 Hz`) — 순회가 20초씩 멈췄다.
        # 관제 화면의 트리 뷰는 초당 10장을 쓸 수 없다. 2Hz 면 사람 눈에 충분하다.
        #
        # ⚠️ **상태·잠금은 절대 스로틀하지 않는다.** 모션 잠금(`/libi/motion_lock`)은
        #    twist_mux 의 입력 timeout(0.5s)과 짝이다 — 늦추면 twist_mux 가 잠금이 풀린 것으로
        #    보고 자율 제어를 통과시킨다. 그건 "대기인데 바퀴가 돈다"로 되돌아가는 길이다.
        #
        # 상태가 바뀐 tick 은 주기를 무시하고 **즉시** 낸다 — 전이 순간의 트리를 놓치면
        # 화면이 한 박자 늦게 바뀐다.
        self._snap_period = float(snapshot_period_sec)
        self._snap_at = 0.0
        self._snap_state = None
        node.create_subscription(String, follow_snapshot_topic, self._on_follow_snapshot, 10)

        self._bb = py_trees.blackboard.Client(name="state_io")
        for key in (Keys.CURRENT_MODE, Keys.ERROR_CODE, Keys.HOLD_UNTIL, Keys.NEXT_MODE):
            self._bb.register_key(key=key, access=Access.WRITE)
        for key in (Keys.BATTERY_PERCENT, Keys.IS_DOCKED, Keys.INTERACTING_REMAINING,
                    Keys.DOCK_DECLARED, Keys.PERSON_BLOCKED,
                    Keys.FRONT_PERSON_SIZE, Keys.PERSON_BLOCK_IN,
                    Keys.PERSON_BLOCK_SEQ, Keys.PERSON_BLOCK_NODE):
            self._bb.register_key(key=key, access=Access.READ)
        #: BT 가 선언한 도킹 여부를 밖으로 낸다 (`Keys.DOCK_DECLARED` 주석 참고).
        #  latched 다 — 늦게 뜬 구독자(providers·sim_battery·FMS)도 마지막 값을 받는다.
        self._docked_pub = node.create_publisher(Bool, docked_topic, _LATCHED_PUB)
        #: 마지막으로 낸 값. **바뀔 때만** 낸다 — latched 라 재발행이 필요 없고,
        #  20Hz 로 계속 내면 로그·대역만 먹는다. None = 아직 한 번도 선언 안 됨.
        self._docked_sent = None

    def _publish_hold(self):
        """잠긴 동안 0 을 20Hz 로 낸다. 안 잠겼으면 **아무것도 안 낸다** —
        발행을 멈추면 twist_mux 가 timeout(0.5s) 뒤 이 입력을 없는 것으로 치고
        아래 입력(추종·nav2)이 다시 통과한다."""
        if self._locked:
            self._hold_pub.publish(Twist())

    def _on_follow_snapshot(self, msg):
        try:
            self._follow_tree = (json.loads(msg.data) or {}).get("tree")
        except (ValueError, TypeError):
            return                                  # 깨진 프레임은 조용히 버린다
        self._follow_at = self._clock()

    def _follow_subtree(self):
        """살아 있는 추종 서브트리. 발행이 끊기면 붙이지 않는다.

        끊긴 걸 그대로 두면 화면에 **멈춘 트리가 계속 붙어 있어** 추종이 도는 것처럼
        보인다. 그 오독이 이 값이 없는 것보다 나쁘다.
        """
        if self._follow_tree is None:
            return None
        if self._clock() - self._follow_at > self._follow_stale_sec:
            return None
        return self._follow_tree

    def _read(self, key, default=None):
        try:
            return self._bb.get(key)
        except KeyError:
            return default

    # ── 전이 요청 (서비스 · 토픽 두 경로가 같은 판정을 쓴다) ────────────────────

    def _decide(self, target_state, force):
        current = self._read(Keys.CURRENT_MODE) or ""
        error_code = self._read(Keys.ERROR_CODE) or ""
        accepted, reason = validate(current, target_state, force, error_code)
        if accepted:
            with self._lock:
                self._pending = target_state
            self._log.info(f"전이 요청 수락: {current} -> {target_state}"
                           + (" (force)" if reason == "force" else ""))
        else:
            self._log.warning(f"전이 요청 거부: {current} -> {target_state} — {reason}")
        return accepted, current, reason

    def _on_request_srv(self, req, res):
        res.accepted, res.current_state, res.reason = self._decide(req.target_state, req.force)
        return res

    def _on_request_topic(self, msg):
        try:
            payload = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        # 이 노드는 로봇 한 대만 담당한다 — 남의 요청은 무시.
        # 공유 토픽(/libi/fsm_transition_request)이 모든 로봇 도메인에 뿌려지므로,
        # robot_id 가 내 것과 **정확히** 같을 때만 받는다. 빈/None 이면 거부한다 —
        # 예전엔 그걸 통과시켜, robot_id 없는 요청 한 건이 전 로봇을 동시에 전이시켰다.
        # 서버(fsm_link.request_transition)는 항상 특정 robot_id 를 실어 보낸다.
        if payload.get("robot_id") != self._robot_id:
            return
        cmd_id = payload.get("id")
        if not cmd_id:
            return
        accepted, current, reason = self._decide(
            str(payload.get("target_state", "")), bool(payload.get("force")))
        out = String()
        out.data = json.dumps({"id": cmd_id, "accepted": accepted,
                               "current_state": current, "reason": reason},
                              ensure_ascii=False)
        self._result_pub.publish(out)

    def apply_pending(self):
        """tick 맨 앞에서 노드가 부른다. 적용했으면 True.

        ## 왜 여기서 유지 시간을 찍나

        패널이 시킨 전이는 **BT 가 다음 tick 에 곧바로 되돌릴 수 있다.** 이미 충전된
        로봇을 CHARGING 으로 보내면 `BatteryCheck(>=40)` 이 즉시 통과해 같은 tick 에
        IDLE 로 나가고, 관제 화면에는 아무 일도 안 일어난 것처럼 보인다.

        그래서 사람이 시킨 전이에만 유지 시각을 남긴다. `RequestTransition` 이 이걸 보고
        **BT 의 자동 전이만** 그때까지 미룬다 — 사람의 다음 전이는 이 경로로 오므로 막히지
        않는다. 즉 "로봇이 스스로 못 나가는" 것이지 조작이 잠기는 게 아니다.
        """
        with self._lock:
            target, self._pending = self._pending, None
        if target is None:
            return False
        self._bb.set(Keys.CURRENT_MODE, target)
        # 사람이 상태를 정했으면 **아직 적용 안 된 BT 전이 요청은 무효다.** 안 지우면 그
        # 낡은 요청이 나중에(다른 상태에서) 살아나 사람이 정한 상태를 되돌릴 수 있다.
        self._bb.set(Keys.NEXT_MODE, None)
        if self._manual_hold_sec > 0:
            self._bb.set(Keys.HOLD_UNTIL, self._clock() + self._manual_hold_sec)
        return True

    # ── 발행 ──────────────────────────────────────────────────────────────────

    def publish(self, root):
        current = self._read(Keys.CURRENT_MODE) or ""
        battery = self._read(Keys.BATTERY_PERCENT)
        docked = self._read(Keys.IS_DOCKED)
        error_code = self._read(Keys.ERROR_CODE) or ""
        branch = f"{current.title().replace('_', '')}Branch" if current else ""

        led = String(); led.data = current                       # LED 는 이름 원문만 본다
        self._led_pub.publish(led)

        # 자율주행 잠금 — MOTION_LOCKED_STATES 주석 참고.
        self._locked = current in MOTION_LOCKED_STATES
        self._lock_pub.publish(Bool(data=self._locked))

        # BT 가 선언한 도킹을 밖으로 낸다. 아무도 `/is_docked` 를 안 내던 자리를 메운다
        # (`Keys.DOCK_DECLARED` 주석에 왜 위치 판정을 안 쓰는지 적어 뒀다).
        # None(아직 선언 없음)이면 **아무것도 안 낸다** — 모르는 것을 False 로 단정하면
        # 그것도 판정이 된다. 지금까지와 똑같이 구독자 쪽 값이 None 으로 남는다.
        declared = self._read(Keys.DOCK_DECLARED)
        if declared is not None and declared != self._docked_sent:
            self._docked_sent = bool(declared)
            self._docked_pub.publish(Bool(data=self._docked_sent))

        # remaining_sec 은 INTERACTING 일 때만 의미가 있다(UiSessionTimer 가 그 브랜치에서만
        # 쓴다). 다른 상태에선 0.0 으로 내보내 패널이 남은 카운트다운을 오인하지 않게 한다.
        remaining = self._read(Keys.INTERACTING_REMAINING) if current == "INTERACTING" else 0.0

        state = String()
        state.data = json.dumps({
            "robot_id": self._robot_id,
            "current_state": current,
            "active_branch": branch,
            "error_code": error_code,
            "battery_percent": battery,
            "is_docked": docked,
            "remaining_sec": round(float(remaining or 0.0), 1),
            # 사람이 앞을 막아 서 있는가(PersonBlockGuard). 다른 이유의 정지는 안 들어온다.
            # 아직 한 번도 안 쓰였으면 False — 패널이 모르는 값을 "정지"로 그리면 안 된다.
            "person_blocked": bool(self._read(Keys.PERSON_BLOCKED, False)),
            # 지금 앞캠에 보이는 가장 큰 사람의 크기(sqrt(area) px @320). 0 = 안 보임.
            # 임계값(person_stop_size)을 실기에서 맞추려면 **지금 몇 px 인지**가 화면에
            # 보여야 한다 — 안 보이면 감으로 숫자를 고치게 된다.
            "front_person_size": round(float(self._read(Keys.FRONT_PERSON_SIZE, 0.0) or 0.0), 1),
            # 사람 때문에 정점 차단을 알리기까지 남은 초. -1 = 세고 있지 않음.
            # 관제에서 재계획이 **사람 때문인지 지연 때문인지** 구분이 안 돼 넣었다.
            # 판정에 쓰는 값 그 자체다 — 화면이 따로 계산하지 않는다.
            "person_block_in": _round_or(self._read(Keys.PERSON_BLOCK_IN), -1.0),
            # 사람 차단을 **알린 횟수**와 그 정점. 화면은 횟수가 늘어난 순간에만 기록을
            # 남긴다 — `person_blocked`(레벨)로 남기면 임계 근처 깜빡임이 로그를 채운다.
            # 이 두 값이 있어야 관제 기록에서 "이 재계획은 사람 때문" 을 가릴 수 있다.
            "person_block_seq": int(self._read(Keys.PERSON_BLOCK_SEQ, 0) or 0),
            "person_block_node": int(self._read(Keys.PERSON_BLOCK_NODE, -1) or -1),
        }, ensure_ascii=False)
        self._state_pub.publish(state)

        # 스냅샷만 주기 발행 (위 `_snap_period` 주석 참고). 상태 변화는 즉시 낸다.
        now = self._clock()
        if current != self._snap_state or (now - self._snap_at) >= self._snap_period:
            self._snap_at = now
            self._snap_state = current
            snap = String()
            snap.data = json.dumps(
                {"robot_id": self._robot_id, "current_state": current,
                 "tree": _to_dict(root, self._follow_subtree())},
                ensure_ascii=False)
            self._snap_pub.publish(snap)

        typed = FsmState()
        typed.stamp = self._node.get_clock().now().to_msg()
        typed.robot_id = self._robot_id
        typed.current_state = current
        typed.active_branch = branch
        typed.error_code = error_code
        typed.battery_percent = float(battery) if battery is not None else -1.0
        typed.is_docked = bool(docked) if docked is not None else False
        self._typed_pub.publish(typed)


#: 다른 프로세스의 서브트리를 붙일 자리. 이름이 곧 접합점이라 여기만 고치면 된다.
#:
#: 후보가 둘인 이유 — 회복 BT 는 **추종과 길잡이 양쪽에서 돈다.** 길잡이도 사람을
#: 놓치면 반대 캠으로 찾기 때문이다(감시 세션도 트리를 돌린다. 속도만 삼킨다).
#: 그런데 `CommandDispatch` 는 memory=False Selector 라, 길잡이 중에는 `GuideExec` 이
#: tick 을 집어가고 `FollowExec` 은 **한 번도 안 돌아 INVALID** 로 남는다.
#: 고정해 두면 화면에 **꺼진 노드 밑에서 무언가 돌고 있는** 그림이 나온다 — 코드는
#: 멀쩡한데 화면만 거짓이 되는, 이 파일이 막으려는 바로 그 상황이다.
#:
#: 야간 침입 추종도 같은 자리다 — 이걸 안 넣으면 `_GRAFT_FALLBACK`("FollowExec")로
#: 떨어지는데 SECURITY_PATROL 브랜치엔 그 잎이 없어 **추종 하위 트리가 관제 화면에서
#: 통째로 사라진다.** 코드는 멀쩡한데 화면만 거짓이 되는 종류다.
_GRAFT_POINTS = ("FollowExec", "GuideExec", "IntruderChase")
#: RUNNING 인 후보가 없을 때(추종·길잡이 둘 다 안 도는데 스냅샷은 온 경우) 붙일 자리.
_GRAFT_FALLBACK = "FollowExec"


def _kind(node) -> str:
    """노드의 **성격**. 화면에서 이름만으로는 Sequence 인지 Selector 인지 알 수 없다.

    예전엔 스냅샷에 `{name, status, children}` 뿐이라, 관제 화면이 제어노드 종류를
    자식 유무로만 추측했다 — Selector 가 Sequence 처럼 보였다. 그건 오독을 부른다:
    Sequence 는 "왼쪽부터 다 통과해야", Selector 는 "하나만 통과하면" 이라 뜻이 정반대다.

    표기
        Sequence      왼쪽부터 순서대로, 하나라도 실패하면 실패
        Sequence*     `*` = memory. 통과한 자식은 다음 tick 에 건너뛴다
        Selector      왼쪽부터 묻다가 하나 통과하면 거기서 멈춤
        Selector*     `*` = memory
        Parallel/SuccessOnOne   자식을 **동시에** 돌리고 하나만 성공하면 성공
        Parallel/SuccessOnAll   전부 성공해야 성공
        (그 밖)       leaf 의 클래스명 그대로 (IsMode, BatteryCheck, …)

    ⚠️ py_trees Parallel 은 정책과 무관하게 **자식 하나라도 실패하면 즉시 실패**한다.
       그래서 감시용 Selector 끝에 항상 Running 이 붙어 있다(NoExitCondition).
    """
    cls = type(node).__name__
    if cls == "Parallel":
        return f"Parallel/{type(getattr(node, 'policy', None)).__name__}"
    if cls in ("Sequence", "Selector"):
        return f"{cls}*" if getattr(node, "memory", False) else cls
    return cls


def _pick_graft_point(node) -> str:
    """지금 tick 을 쥔 접합 후보의 이름. 없으면 `_GRAFT_FALLBACK`.

    추종이면 `FollowExec`, 길잡이면 `GuideExec` 이 RUNNING 이다. 둘 다 아니면(세션은
    떴는데 미션 BT 가 다른 브랜치를 돌고 있는 등) 기본 자리에 붙인다 — 안 붙이면
    회복 트리가 화면에서 통째로 사라져, 돌고 있는데 안 보이는 쪽으로 거짓이 된다.
    """
    for cand in _GRAFT_POINTS:
        if _find_running(node, cand):
            return cand
    return _GRAFT_FALLBACK


def _find_running(node, name) -> bool:
    if node.name == name and _STATUS_NAME.get(node.status) == "RUNNING":
        return True
    return any(_find_running(c, name) for c in getattr(node, "children", []))


def _to_dict(node, follow_subtree=None, graft_at=None):
    """트리 → `{name, status, children}`.

    `follow_subtree` 가 있으면 지금 도는 실행 leaf(`FollowExec`/`GuideExec`) **밑에
    붙인다.** 추종·길잡이 회복 BT 는 libi_perception 이라는 **다른 프로세스**에서 돌아
    이 트리에 존재하지 않는데, 관제 화면에서는 그 leaf 밑에 이어져 보여야 한다
    (그게 실제 실행 관계다).
    """
    if follow_subtree is not None and graft_at is None:
        graft_at = _pick_graft_point(node)
    children = [_to_dict(c, follow_subtree, graft_at)
                for c in getattr(node, "children", [])]
    if follow_subtree is not None and node.name == graft_at and not children:
        children = [follow_subtree]
    return {
        "name": node.name,
        "kind": _kind(node),
        "status": _STATUS_NAME.get(node.status, "INVALID"),
        "children": children,
    }
