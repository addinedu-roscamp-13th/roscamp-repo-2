"""fleet_orchestrator(코어) 를 FastAPI/ROS 와 잇는 서비스 층 — 싱글턴 + dispatch 배선.

코어(fleet_orchestrator.Orchestrator)는 순수 로직이라 "다리를 어떻게 실제로 내보내는가"를
모른다. 여기서 그 `dispatch_leg` 을 채운다.

## dispatch 배선 상태

실배선은 `app.fleet_dispatch_bridge` 가 소유한다 (`real_dispatch`). 기동 시
`LIBI_REAL_DISPATCH=1` 이면 그쪽이 `set_dispatch()` 로 갈아끼운다.

- 주행(NAVIGATE): fleet_link.submit_task(dropoff=waypoint) → fleet_node 가 교통까지 처리.
  fleet_node 가 허가한 노드는 `/fleet_cmd{navigate}` 로 로봇 BT 에 내려간다.
- 팔(PERFORM_ACTION): `/fleet_cmd{perform_action}` → 로봇 BT 의 ArmExec.

여기 남은 `_stub_dispatch` 는 **로봇 없이 패널에서 주문·배차·force-advance 로 시퀀스를
확인**하는 용도의 기본값이다. 실배선을 안 켜면 이게 쓰인다.
"""
from __future__ import annotations

import itertools
import logging

from app.fleet_orchestrator import LegType, Orchestrator

log = logging.getLogger("fleet_orchestrator")
_stub_seq = itertools.count(1)


def _stub_dispatch(task_id: str, robot: str, leg) -> str:
    """[미배선] 실제 로봇에 안 보내고 로그만. force-advance/디버그로 시퀀스 검증용."""
    cmd_id = f"stub-{next(_stub_seq)}"
    log.info("[orchestrator:stub] %s → %s %s(%s) cmd=%s",
             task_id, robot, leg.type.value, leg.params, cmd_id)
    return cmd_id


_orc = Orchestrator(_stub_dispatch)


def orchestrator() -> Orchestrator:
    return _orc


def set_dispatch(dispatch_leg) -> None:
    """dispatch 를 교체(테스트/실배선). 코어 재생성 없이 바꾼다."""
    _orc._dispatch_leg = dispatch_leg  # noqa: SLF001 — 서비스 층이 코어를 소유


def set_lifecycle(task_lifecycle) -> None:
    """task 시작/끝 신호 훅을 꽂는다 — 로봇 미션 FSM 을 WORKING 으로 넣고 뺀다."""
    _orc._task_lifecycle = task_lifecycle  # noqa: SLF001


def set_on_event(on_event) -> None:
    """사람에게 보일 사건 훅을 꽂는다 — "책이 도착했다" 알림의 출발점."""
    _orc._on_event = on_event  # noqa: SLF001


def set_release(release_robot) -> None:
    """취소·실패 시 로봇을 놓아주는 훅을 꽂는다(코어 재생성 없이).

    안 꽂으면 코어는 상태만 바꾸고 로봇은 계속 움직인다 — 스텁 모드에선 그게 맞다.
    """
    _orc._release_robot = release_robot  # noqa: SLF001


def reset(dispatch_leg=None) -> None:
    """싱글턴을 새 orchestrator 로 교체(테스트 격리용)."""
    global _orc
    _orc = Orchestrator(dispatch_leg or _stub_dispatch)
