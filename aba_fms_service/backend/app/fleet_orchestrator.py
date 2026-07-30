"""배달 주문 오케스트레이터 — composite task 시퀀서 (배차·교통 위, 로봇 아래).

주문 1건(배달)을 여러 "다리(leg)"로 분해해 **한 번에 하나씩** 로봇에 명령하고, 완료
보고를 받아 다음 다리로 넘긴다. 예: 주행(선반)→팔(집기)→주행(전달지)→팔(놓기).

## 층 위치 (설계: docs / fleet_ws README)
    UI → [주문 큐 + 이 시퀀서] → 배차(auction 플러그인) 로 로봇 선택
                              → 다리 하나씩 → Drive Controller(libi_modes)가 실행
                                            → 팔이면 Handy 로 넘김 → 완료 보고
    · 배차/교통 알고리즘은 fleet_node(C++) 플러그인이 소유 — 여기서 안 한다.
    · 이 모듈은 "무슨 주문을 어떤 순서로" 만 담당한다.

## rclpy 비의존 (중요)
이 파일 최상위는 ROS 를 import 하지 않는다 (`fsm_link`/`fleet_link` 와 같은 원칙).
`dispatch_leg` 콜러블을 주입받아 다리를 실제로 내보내므로, ROS·DB·로봇 없이 전부
단위테스트된다. 실제 배선(다리→Navigate/PerformAction, 완료→fleet_cmd_result)은 이
코어를 감싸는 rclpy 계층이 맡는다 (아직 미구현 — 핸드오프 문서 참고).

## 완료/실패 신호
`on_result(cmd_id, ok)` 로 다리 완료를 받는다. 로봇은 이미 명령별로 결과를 correlation
id 와 함께 돌려준다(`/fleet_cmd_result {"id","ok",...}`) — 그 id 를 그대로 cmd_id 로 쓰면
배선 때 새 프로토콜이 필요 없다.
"""
from __future__ import annotations

import itertools
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("fleet_orchestrator")


class LegType(str, Enum):
    NAVIGATE = "navigate"            # 정점으로 주행 (교통은 fleet_node 가)
    PERFORM_ACTION = "perform_action"  # 팔 동작 (Drive→Handy)


class TaskStatus(str, Enum):
    # TaskState.msg 어휘와 맞춘다 (PENDING 은 큐 대기 — msg 엔 없지만 내부용).
    PENDING = "PENDING"       # 큐에 있고 아직 로봇 미배정
    ASSIGNED = "ASSIGNED"     # 로봇 배정됨, 첫 다리 직전
    EXECUTING = "EXECUTING"   # 다리 하나가 로봇에서 진행 중
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# 실패해도 재시도하지 않고 바로 FAILED 로 갈 상태들과 달리, 진행 중 취소는 언제든 받는다.
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass
class Leg:
    type: LegType
    params: dict = field(default_factory=dict)
    # 런타임
    cmd_id: str | None = None   # 이 다리를 내보낼 때 붙은 명령 id (완료 대조용)
    attempts: int = 0


@dataclass
class Task:
    id: str
    task_type: str
    legs: list[Leg]
    requester: str = ""
    priority: int = 0
    robot: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    leg_idx: int = 0            # 현재(진행 중이거나 다음에 낼) 다리 인덱스
    reason: str = ""           # 실패/거절 사유

    def current_leg(self) -> Leg | None:
        if 0 <= self.leg_idx < len(self.legs):
            return self.legs[self.leg_idx]
        return None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "requester": self.requester,
            "priority": self.priority,
            "robot": self.robot,
            "status": self.status.value,
            "leg_idx": self.leg_idx,
            "leg_count": len(self.legs),
            "current_leg": (self.current_leg().type.value if self.current_leg() else None),
            "reason": self.reason,
            # 다리 전체의 원자료(타입+파라미터). task_type 마다 다리 뜻이 다르므로
            # (수거 4다리는 배달 4다리와 전혀 다른 일을 한다), 화면이 leg_idx 를 4로
            # 나눈 나머지로 라벨을 추측하면 반드시 틀린다 — 표시 문구는 여기서
            # 만들지 않는다(이 모듈은 ROS 뿐 아니라 UI 문구도 모른다). 화면용 한글
            # 라벨은 `fleet_dispatch_bridge.leg_label()` 이 이 raw 값으로 만든다.
            "legs": [{"type": leg.type.value, "params": dict(leg.params)} for leg in self.legs],
        }


def decompose_delivery(*, book: str, pickup, dropoff, tier: int = 0, row: int = 0,
                       slot: int = 1) -> list[Leg]:
    """배달 = 선반으로 주행 → 집기 → 전달지로 주행 → 놓기.

    pickup/dropoff 는 waypoint 식별자(정점 번호/이름) — 코어는 그 정체를 모른다
    (도서→선반→waypoint 해석은 상위 #27 데이터 계층이 한다).

    ## 팔 계약 필드 (2026-07-30)

    팔은 정점 이름이 아니라 **장소 종류**로 움직인다 — 어느 서가든 동작이 같으므로.
    그래서 팔 leg 에 `object`/`from_place`/`to_place` 를 싣는다.

    ⚠️ `place` 의 `to_place` 는 **비워 둔다.** 목적지가 테이블인지 안내데스크인지는
    정점의 정체를 알아야 정해지고, 그 지식은 이 코어에 없다(위 docstring). 로봇 쪽 중계
    (`libi_modes/arm_task_map.py`)가 `at` 정점 이름에서 유도한다 — 거기 규칙과 테스트가 있다.

    `tier`/`row` 는 도서 DB 값(`books.tier`/`books.row`)이라 상위가 넘겨준다. 0 이면
    "층·줄 정보 없음"이고 팔이 시각으로 찾는다. **`pick` 에만 싣는다** — `place` 는 서가가
    아니라 바구니에서 꺼내므로 층·줄이 뜻을 갖지 않는다(계약: 0).
    `slot` 은 리비바구니 칸이다. 주문 1건 = 도서 1권이라 지금은 항상 1 이고, 다중 배달을
    넣을 때 여기서 배정한다 — **팔은 상태를 갖지 않으므로 pick 과 place 가 같은 번호여야 한다.**
    """
    arm = {"book": book, "object": "book", "slot": int(slot)}
    return [
        Leg(LegType.NAVIGATE, {"waypoint": pickup}),
        Leg(LegType.PERFORM_ACTION, {"action": "pick", "at": pickup,
                                     "from_place": "서가", "to_place": "리비바구니",
                                     "tier": int(tier), "row": int(row), **arm}),
        Leg(LegType.NAVIGATE, {"waypoint": dropoff}),
        # `place` 는 서가에 손을 안 뻗는다(바구니 → 테이블). 층·줄을 실어 보내면 팔이
        # 무시할지 따를지 헷갈린다 — 계약대로 0 으로 비운다.
        Leg(LegType.PERFORM_ACTION, {"action": "place", "at": dropoff,
                                     "from_place": "리비바구니",
                                     "tier": 0, "row": 0, **arm}),
    ]


def decompose_navigation(*, dropoff) -> list[Leg]:
    """주행만 = 한 곳으로 가서 끝.

    집고 놓을 물건이 없는 지시(서가 정돈 점검 「정리」, 지정 위치 대기 「파견」)가 여기 해당한다.
    배달 4다리로 내보내면 **있지도 않은 책을 집으러 간다** — 종류마다 다리 구성이 달라야 하는
    이유가 이것이다.
    """
    return [Leg(LegType.NAVIGATE, {"waypoint": dropoff})]


#: 수거의 출발·도착이 늘 이 정점 하나다 — 사람이 고를 게 없다(복귀의 도크 좌표가 고정인 것과
#: 같은 성질). `aba_controller/.../pinky_navigation/params/waypoint.yaml` 의 실제 정점 이름.
COLLECTION_WAYPOINT = "수거함"


def decompose_collection() -> list[Leg]:
    """수거 = 수거함으로 주행 → 바구니 3단계 교체. **다리 4개, 그중 3개가 팔이다.**

    분류(sort, 실운영에서 없어짐 — waypoint 도 "분류함"→"미정"으로 이미 리네임됨)를 대신하는
    간소화된 반납 수거 동작이다. 배달(navigate→pick→navigate→place)과 달리 **두 번째 주행이
    없다** — 로봇은 수거함에 선 채로 팔만 세 번 움직인다.

        ① 로봇 위 바구니를 바닥에 내려놓는다(자리를 만든다)
        ② 수거함의 바구니를 로봇에 싣는다(반납된 책이 든 바구니)
        ③ 바닥의 빈 바구니를 수거함에 채운다(다음 반납을 받을 빈 바구니를 남긴다)

    액션 이름(`unload_to_floor`/`load_from_box`/`refill_box`)은 지금 팔이 스텁이라 물리
    동작으로 안 이어진다 — 나중에 팔이 실제로 붙을 때 이 이름에 맞는 동작을 넣으면 된다.

    ## [2026-07-30] `object` 로 대상 종류를 분리했다

    예전에는 `book: "바구니"` 였다 — **대상 종류를 도서명 필드에 문자열로 위장**해 넣은
    것이다. 팔이 "이게 도서냐 바구니냐"를 도서명으로 추측해야 했고, 그건 오타 하나로
    엉뚱한 파지 루틴을 도는 종류의 배선이다. 이제 `object: "basket"` 이 그 일을 한다.

    `book` 은 빈 문자열이다(바구니는 전체를 드니 도서명이 없다). `tier`/`row`/`slot` 도
    비운다 — 바구니는 칸을 지목하지 않는다.

    세 이동은 **바닥을 임시 자리로 쓰는 3단 교대**라 순서가 고정이다. 모든 이동의 목적지가
    비어 있어야 하기 때문이다: 리비→바닥(리비 빔) → 수거함→리비(수거함 빔) → 바닥→수거함.
    """
    at = COLLECTION_WAYPOINT
    common = {"book": "", "object": "basket", "at": at, "tier": 0, "row": 0, "slot": 0}
    return [
        Leg(LegType.NAVIGATE, {"waypoint": at}),
        Leg(LegType.PERFORM_ACTION, {"action": "unload_to_floor",
                                     "from_place": "리비바구니", "to_place": "바닥", **common}),
        Leg(LegType.PERFORM_ACTION, {"action": "load_from_box",
                                     "from_place": "수거함", "to_place": "리비바구니", **common}),
        Leg(LegType.PERFORM_ACTION, {"action": "refill_box",
                                     "from_place": "바닥", "to_place": "수거함", **common}),
    ]


class Orchestrator:
    """주문 큐 + composite task 상태기. 스레드 안전(요청 핸들러 + ROS 콜백이 함께 건드림).

    dispatch_leg(task_id, robot, leg) -> cmd_id(str):
        다리 하나를 로봇으로 내보내고, 완료 대조에 쓸 명령 id 를 돌려준다. 실패하면 예외.
        (실제 구현: NAVIGATE→fleet_node/fleet_cmd, PERFORM_ACTION→fleet_cmd. 배선 계층 담당)
    """

    def __init__(self, dispatch_leg, *, release_robot=None, task_lifecycle=None,
                 on_event=None, retry_max: int = 1):
        self._dispatch_leg = dispatch_leg
        #: on_event(kind, task, leg) -> None — 사람에게 보일 **사건**을 알린다.
        #  task_lifecycle 과 목적이 다르다:
        #    task_lifecycle  로봇을 움직이는 신호 (WORKING 진입/이탈). task 단위.
        #    on_event        사람에게 보일 알림 ("책이 도착했다"). **다리 단위**로도 낸다 —
        #                    "지금 어떤 상태인가"는 폴링으로 알 수 있지만
        #                    "방금 도착했다"는 사건이 아니면 표현할 방법이 없다.
        #  안 꽂으면 no-op. 여기서 실패해도 주문 진행은 막지 않는다.
        self._on_event = on_event
        #: task_lifecycle(robot, phase) — phase 는 "start" | "done" | "failed".
        #  로봇의 미션 FSM 을 task 수명주기에 맞춰 움직이는 신호다(libi_modes 는
        #  task_assigned 로 WORKING 에 들어가고 task_done 으로 나온다).
        #  **다리 단위가 아니라 task 단위**여야 한다 — 팔 다리마다 WORKING 을 들락거리면
        #  주행 중에 로봇이 작업 상태가 아니게 된다. 안 꽂으면 no-op.
        self._task_lifecycle = task_lifecycle
        #: release_robot(robot) -> None — 로봇을 배선 계층에서 놓아준다(선택).
        #  주문을 취소·실패 처리해도 **로봇 쪽은 여전히 그 일을 하고 있다.** 코어는
        #  fleet_node 를 모르므로, 실제로 멈추는 일은 이 훅이 한다. 안 꽂으면 no-op.
        self._release_robot = release_robot
        self._retry_max = retry_max
        self._tasks: dict[str, Task] = {}
        self._queue: list[str] = []            # PENDING task id (미배정)
        self._by_cmd: dict[str, str] = {}      # cmd_id -> task_id (완료 대조)
        self._seq = itertools.count(1)
        self._lock = threading.RLock()

    # ── 주문 접수 ────────────────────────────────────────────────────────────
    def submit_delivery(self, *, book: str, pickup, dropoff, requester: str = "",
                        priority: int = 0, tier: int = 0, row: int = 0) -> str:
        legs = decompose_delivery(book=book, pickup=pickup, dropoff=dropoff,
                                  tier=tier, row=row)
        return self._enqueue("delivery", legs, requester=requester, priority=priority)

    def submit_navigation(self, *, dropoff, requester: str = "",
                          priority: int = 0) -> str:
        """주행만 하는 주문(정리·파견). 다리가 1개라 도착하면 곧바로 COMPLETED 다."""
        legs = decompose_navigation(dropoff=dropoff)
        return self._enqueue("navigate", legs, requester=requester, priority=priority)

    def submit_collection(self, *, requester: str = "", priority: int = 0) -> str:
        """수거. 사람이 고를 게 없다 — book/pickup/dropoff 인자 자체가 없다."""
        legs = decompose_collection()
        return self._enqueue("collect", legs, requester=requester, priority=priority)

    def submit(self, task_type: str, legs: list[Leg], *, requester: str = "",
               priority: int = 0) -> str:
        if not legs:
            raise ValueError("legs 가 비어 있습니다")
        return self._enqueue(task_type, legs, requester=requester, priority=priority)

    def _enqueue(self, task_type, legs, *, requester, priority) -> str:
        with self._lock:
            tid = f"t{next(self._seq)}"
            self._tasks[tid] = Task(id=tid, task_type=task_type, legs=legs,
                                    requester=requester, priority=priority)
            self._queue.append(tid)
            return tid

    # ── 배차(로봇 배정) ──────────────────────────────────────────────────────
    def assign(self, task_id: str, robot: str) -> None:
        """PENDING 주문에 로봇을 배정하고 첫 다리를 내보낸다.

        로봇 선택 자체(auto=auction / manual=지정)는 상위가 결정해 여기로 넘긴다.
        이 코어는 "정해진 로봇에게 순서대로 먹인다" 만 한다.
        """
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.PENDING:
                raise ValueError(f"배정 불가: {task_id} 상태 {task.status.value}")
            # 이 로봇이 이미 **다른 살아있는 주문**에 배정돼 있으면 거절한다.
            #
            # 이게 없으면: 자동배차/수동배차가 거의 동시에 같은 로봇을 두 주문에 배정할 때
            # (fleet_link 의 busy 캐시는 ROS 로 뒤늦게 갱신되므로 그 틈에 겹칠 수 있다),
            # fleet_node 는 두 번째 배정에서 `cancel_task(robot)` 으로 **첫 번째 fleet task 를
            # 지운다**(강제 배정은 busy 여도 받는다). 그러면 첫 번째 orchestrator task 는
            # cmd_id 에 대한 완료 신호를 영영 못 받고 EXECUTING 에 갇힌다 — 로봇은 두 번째
            # 주문으로 가는데 관제엔 첫 번째가 "진행 중"으로 계속 남는, 원인 불명 상태가 된다.
            # 여기서 막으면 그 경합이 우리 프로세스 안에서는 원천적으로 안 생긴다(외부에서
            # fleet_node 를 직접 건드리는 경우까지는 못 막지만, orchestrator 를 거치는
            # 자동/수동 배차 둘 다 이 관문을 지난다).
            if any(t.robot == robot and t.status not in _TERMINAL
                   for t in self._tasks.values()):
                raise ValueError(f"배정 불가: {robot} 는 이미 다른 주문에 배정돼 있습니다")
            task.robot = robot
            task.status = TaskStatus.ASSIGNED
            if task_id in self._queue:
                self._queue.remove(task_id)
            self._start_leg(task)

    def _start_leg(self, task: Task) -> None:
        """현재 다리를 내보낸다. dispatch 실패 시 재시도 정책에 따라 처리."""
        leg = task.current_leg()
        if leg is None:                     # 다리 다 소진 → 완료
            task.status = TaskStatus.COMPLETED
            self._lifecycle(task, "done")
            self._event("task_done", task, None)
            return
        if task.leg_idx == 0 and leg.attempts == 0:
            self._lifecycle(task, "start")  # 첫 다리 직전에 딱 한 번
            self._event("task_started", task, leg)
        leg.attempts += 1
        try:
            cmd_id = self._dispatch_leg(task.id, task.robot, leg)
        except Exception as exc:            # noqa: BLE001 — dispatch 실패도 다리 실패로
            self._fail_or_retry(task, f"dispatch 실패: {exc}")
            return
        leg.cmd_id = cmd_id
        self._by_cmd[cmd_id] = task.id
        task.status = TaskStatus.EXECUTING

    # ── 완료/실패 보고 ───────────────────────────────────────────────────────
    def on_result(self, cmd_id: str, ok: bool, msg: str = "") -> None:
        """로봇이 다리 하나를 끝냈다는 보고. cmd_id 로 어느 task/다리인지 찾는다.

        늦게 온 결과(이미 취소/완료됐거나 id 불일치)는 조용히 무시한다.
        """
        with self._lock:
            task_id = self._by_cmd.pop(cmd_id, None)
            if task_id is None:
                return                       # 모르는/중복 id
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.EXECUTING:
                return                       # 취소됐거나 상태 어긋남 → 무시
            leg = task.current_leg()
            if leg is None or leg.cmd_id != cmd_id:
                return                       # stale (다리가 이미 넘어감)
            if ok:
                # 이 다리가 끝났다는 건, 주행 다리라면 **목적지에 도착했다**는 뜻이다.
                # 다리를 넘기기 **전에** 알린다 — 넘긴 뒤엔 방금 끝난 다리가 뭐였는지
                # 알 수 없다(current_leg 가 다음 것을 가리킨다).
                self._event("leg_done", task, leg)
                task.leg_idx += 1
                self._start_leg(task)        # 다음 다리(없으면 COMPLETED)
            else:
                self._fail_or_retry(task, msg or "다리 실패")

    def _fail_or_retry(self, task: Task, reason: str) -> None:
        leg = task.current_leg()
        if leg is not None and leg.attempts <= self._retry_max:
            leg.cmd_id = None
            self._start_leg(task)            # 같은 다리 재시도
            return
        task.status = TaskStatus.FAILED
        task.reason = reason
        self._lifecycle(task, "failed")
        self._event("task_failed", task, None)
        self._release(task)

    def _event(self, kind: str, task: Task, leg: "Leg | None") -> None:
        """사건을 알린다. 실패해도 주문 진행은 막지 않는다 — 알림이 주문보다 덜 중요하다."""
        if self._on_event is None:
            return
        try:
            self._on_event(kind, task, leg)
        except Exception:  # noqa: BLE001
            log.exception("[orchestrator] 사건 알림 실패: %s (task=%s)", kind, task.id)

    def _lifecycle(self, task: Task, phase: str) -> None:
        """배선 계층에 task 수명주기를 알린다(로봇 FSM 전이용). 실패해도 상태 전이는 막지 않는다."""
        if self._task_lifecycle is None or not task.robot:
            return
        try:
            self._task_lifecycle(task.robot, phase)
        except Exception:  # noqa: BLE001
            log.exception("[orchestrator] 수명주기 신호 실패: %s %s (task=%s)",
                          task.robot, phase, task.id)

    def _release(self, task: Task) -> None:
        """배선 계층에 "이 로봇 이제 안 쓴다"를 알린다.

        이게 없으면 주문을 취소·실패시켜도 **로봇은 계속 그 목적지로 간다** — 화면의
        주문은 사라졌는데 로봇만 혼자 움직이는, 설명 불가능한 상태가 된다.
        훅이 없거나(스텁 모드) 배정 전(PENDING)이면 할 일이 없다.
        """
        if self._release_robot is None or not task.robot:
            return
        try:
            self._release_robot(task.robot)
        except Exception:  # noqa: BLE001 — 놓아주기 실패가 상태 전이를 막으면 안 된다
            log.exception("[orchestrator] 로봇 해제 실패: %s (task=%s)", task.robot, task.id)

    # ── 취소 / 디버그 ────────────────────────────────────────────────────────
    def cancel(self, task_id: str) -> None:
        """진행 중이든 대기 중이든 취소. 종료 상태면 무시."""
        with self._lock:
            task = self._require(task_id)
            if task.status in _TERMINAL:
                return
            leg = task.current_leg()
            if leg is not None and leg.cmd_id is not None:
                self._by_cmd.pop(leg.cmd_id, None)   # 늦게 올 결과 무시되도록
            if task_id in self._queue:
                self._queue.remove(task_id)
            task.status = TaskStatus.CANCELLED
            task.reason = "취소됨"
            self._lifecycle(task, "failed")   # 로봇 입장에선 "작업이 끝났다"가 같다
            self._release(task)

    def force_advance(self, task_id: str) -> None:
        """[디버그 전용] 현재 다리를 "완료됨"으로 치고 다음으로. 로봇 없이 시퀀스 검증용.

        ⚠️ 운영 중 쓰면 orchestrator 는 완료라 믿는데 로봇은 아직 → 어긋난다.
        진짜 완료 경로(on_result)를 주로 두고 이건 보조.
        """
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.EXECUTING:
                raise ValueError(f"강제전진 불가: {task_id} 상태 {task.status.value}")
            leg = task.current_leg()
            if leg is not None and leg.cmd_id is not None:
                self._by_cmd.pop(leg.cmd_id, None)
            task.leg_idx += 1
            self._start_leg(task)

    def dismiss(self, task_id: str) -> bool:
        """끝난 주문을 큐에서 치운다(관제 화면 정리용).

        `cancel` 은 **종료 상태를 무시**하도록 되어 있어서, 한 번 FAILED/COMPLETED 가 된
        주문은 화면에서 없앨 방법이 없었다. 실패한 주문이 계속 쌓이면 큐를 읽을 수 없으므로
        이 메서드로 제거한다.

        진행 중인 주문은 지우지 않는다 — 그건 `cancel` 이 할 일이고, 여기서 지워버리면
        로봇은 계속 도는데 관제만 잊어버리는 상태가 된다. 반환값은 실제로 지웠는지 여부.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in _TERMINAL:
                return False
            leg = task.current_leg()
            if leg is not None and leg.cmd_id is not None:
                self._by_cmd.pop(leg.cmd_id, None)
            if task_id in self._queue:
                self._queue.remove(task_id)
            del self._tasks[task_id]
            return True

    # ── 조회 ─────────────────────────────────────────────────────────────────
    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def pending(self) -> list[dict]:
        with self._lock:
            return [self._tasks[t].snapshot() for t in self._queue]

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [t.snapshot() for t in self._tasks.values()]

    def _require(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"없는 task: {task_id}")
        return task
