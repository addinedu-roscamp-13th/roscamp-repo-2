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
import threading
from dataclasses import dataclass, field
from enum import Enum


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
        }


def decompose_delivery(*, book: str, pickup, dropoff) -> list[Leg]:
    """배달 = 선반으로 주행 → 집기 → 전달지로 주행 → 놓기.

    pickup/dropoff 는 waypoint 식별자(정점 번호/이름) — 코어는 그 정체를 모른다
    (도서→선반→waypoint 해석은 상위 #27 데이터 계층이 한다).
    """
    return [
        Leg(LegType.NAVIGATE, {"waypoint": pickup}),
        Leg(LegType.PERFORM_ACTION, {"action": "pick", "book": book, "at": pickup}),
        Leg(LegType.NAVIGATE, {"waypoint": dropoff}),
        Leg(LegType.PERFORM_ACTION, {"action": "place", "book": book, "at": dropoff}),
    ]


class Orchestrator:
    """주문 큐 + composite task 상태기. 스레드 안전(요청 핸들러 + ROS 콜백이 함께 건드림).

    dispatch_leg(task_id, robot, leg) -> cmd_id(str):
        다리 하나를 로봇으로 내보내고, 완료 대조에 쓸 명령 id 를 돌려준다. 실패하면 예외.
        (실제 구현: NAVIGATE→fleet_node/fleet_cmd, PERFORM_ACTION→fleet_cmd. 배선 계층 담당)
    """

    def __init__(self, dispatch_leg, *, retry_max: int = 1):
        self._dispatch_leg = dispatch_leg
        self._retry_max = retry_max
        self._tasks: dict[str, Task] = {}
        self._queue: list[str] = []            # PENDING task id (미배정)
        self._by_cmd: dict[str, str] = {}      # cmd_id -> task_id (완료 대조)
        self._seq = itertools.count(1)
        self._lock = threading.RLock()

    # ── 주문 접수 ────────────────────────────────────────────────────────────
    def submit_delivery(self, *, book: str, pickup, dropoff, requester: str = "",
                        priority: int = 0) -> str:
        legs = decompose_delivery(book=book, pickup=pickup, dropoff=dropoff)
        return self._enqueue("delivery", legs, requester=requester, priority=priority)

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
            return
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
