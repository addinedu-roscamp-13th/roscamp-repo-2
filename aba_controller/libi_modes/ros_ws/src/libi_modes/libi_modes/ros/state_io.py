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
from std_msgs.msg import String

from libi_modes.blackboard import Keys
from libi_modes.registry import ANY, BRANCH_ORDER, START, TRANSITIONS

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
                 follow_snapshot_topic="/libi/follow_bt_snapshot",
                 follow_stale_sec=3.0,
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
        node.create_subscription(String, request_topic, self._on_request_topic, 10)
        self._srv = node.create_service(RequestTransition, service_name, self._on_request_srv)

        # 추종 회복 BT 는 **다른 프로세스**(libi_perception)에서 돈다. 그 트리를 받아
        # 우리 스냅샷의 `FollowExec` 밑에 접붙인다 — 관제 화면이 보는 토픽을 하나로 유지한다.
        # 새 파이프를 만들면 FMS 백엔드·WebSocket·프론트 세 군데를 다 고쳐야 한다.
        self._follow_stale_sec = follow_stale_sec
        self._follow_tree = None
        self._follow_at = 0.0
        node.create_subscription(String, follow_snapshot_topic, self._on_follow_snapshot, 10)

        self._bb = py_trees.blackboard.Client(name="state_io")
        for key in (Keys.CURRENT_MODE, Keys.ERROR_CODE, Keys.HOLD_UNTIL):
            self._bb.register_key(key=key, access=Access.WRITE)
        for key in (Keys.BATTERY_PERCENT, Keys.IS_DOCKED, Keys.INTERACTING_REMAINING):
            self._bb.register_key(key=key, access=Access.READ)

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
        }, ensure_ascii=False)
        self._state_pub.publish(state)

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
_GRAFT_POINT = "FollowExec"


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


def _to_dict(node, follow_subtree=None):
    """트리 → `{name, status, children}`.

    `follow_subtree` 가 있으면 `FollowExec` **밑에 붙인다.** 추종 회복 BT 는
    libi_perception 이라는 **다른 프로세스**에서 돌아 이 트리에 존재하지 않는데,
    관제 화면에서는 그 leaf 밑에 이어져 보여야 한다(그게 실제 실행 관계다).
    """
    children = [_to_dict(c, follow_subtree) for c in getattr(node, "children", [])]
    if follow_subtree is not None and node.name == _GRAFT_POINT and not children:
        children = [follow_subtree]
    return {
        "name": node.name,
        "kind": _kind(node),
        "status": _STATUS_NAME.get(node.status, "INVALID"),
        "children": children,
    }
