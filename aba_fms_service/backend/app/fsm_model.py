"""LIBI 미션 FSM 의 상태·간선 정의 — 웹 패널이 읽는 유일한 진실 원천.

원본은 `libi_modes/registry.py` 의 `BRANCH_ORDER` / `TRANSITIONS` 다. 이 파일은 그것을
FMS 쪽에 옮겨 담은 것이며, **손으로 고치면 안 된다** — 두 정의가 어긋나면
`tests/test_fsm_registry_drift.py` 가 실패한다.

## 왜 import 하지 않고 옮겨 담았나

`libi_modes.registry` 는 최상위에서 `libi_modes.branches` 를 import 하고, 그 아래로
`py_trees` 가 딸려온다. FMS 백엔드가 그걸 import 하면

  - FMS 배포 호스트에 py_trees 가 필요해지고 (`requirements.txt` 에 없다),
  - 별도 워크스페이스(`aba_controller/libi_modes/ros_ws/src`)를 sys.path 에 끼워 넣어야 하며,
  - `aba_controller` 없이 FMS 만 배포하는 순간 부팅이 깨진다.

그래서 런타임에는 아래 값을 쓰고, **정합성은 테스트로 강제**한다. drift 테스트는
registry.py 를 `ast` 로 파싱해(실행하지 않고, 따라서 py_trees 없이) 간선 집합 전체를
글자 단위로 비교한다. 한쪽만 바뀌면 그 즉시 실패한다.

## 프론트엔드 규칙

상태 이름·간선을 `.tsx` 에 적지 않는다. 전부 `GET /api/fsm/model` 응답에서 온다
(INSTRUCTION.md: "전이 박스와 화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다").
`tests/test_no_frontend_state_literals.py` 가 이를 감시한다.
"""
from __future__ import annotations

from typing import NamedTuple

# libi_modes/registry.py BRANCH_ORDER 와 동일 — 우선순위 Selector 평가 순서이자
# 웹에서 '상태 -> 표시할 서브트리' 선택에 쓰이는 순서다.
STATES: tuple[str, ...] = (
    "ERROR",
    "RETURNING",
    "CHARGING",
    "WORKING",
    "INTERACTING",
    "SECURITY_PATROL",
    "PATROL",
    "IDLE",
)

STATE_DESCRIPTIONS: dict[str, str] = {
    "CHARGING": "충전소에 도킹하여 충전 중",
    "IDLE": "대기 상태. 충전 완료 후 명령 대기, 또는 정지 명령으로 멈춘 상태",
    "PATROL": "도서관 내부를 순회하며 작업 요청을 대기",
    "SECURITY_PATROL": "영업 외 시간 침입 감지 순찰",
    "INTERACTING": "이용자가 로봇 터치패널(libi_gui)을 조작 중",
    "WORKING": "FMS로부터 배정받은 task_id를 수행 중",
    "RETURNING": "충전소로 복귀 및 도킹 시도",
    "ERROR": "고장, 비상정지 등 복구가 필요한 상태",
}

# 부팅 진입점 — 전이 박스의 `[*] -> IDLE`. 실제 상태에서 도달할 수 있는 곳이 아니다.
# (2026-07-22 RETURNING -> IDLE 변경 — 이유는 libi_modes/main.py 의 BOOT_STATE 주석)
# registry.py 에서는 `registry.START`("[*]") 로 적혀 있다.
#
# 여기서는 "__START__" 를 쓴다. 이 값은 API 응답으로 프론트까지 나가는데, 상태 이름과
# 절대 겹치지 않아야 하기 때문이다.
START = "__START__"

# 진짜 그룹 전이 소스 — 전이 박스의 `(any) -> ERROR`. registry.py 의 `registry.ANY`.
ANY = "*"

# 두 값의 registry 측 대응. tests/test_fsm_registry_drift.py 가 이 매핑을 고정한다.
# registry 가 [*] 와 (any) 를 서로 다른 마커로 구분하므로 트리거로 추측할 필요가 없다 —
# 예전에는 둘 다 "*" 였고, 그걸 '모든 상태'로 읽으면 WORKING/INTERACTING -> RETURNING 이
# 열려서 "작업·응대 중에는 복귀하지 않는다" 규칙이 조용히 깨졌다.
_REGISTRY_START = "[*]"
_REGISTRY_ANY = "(any)"


class Edge(NamedTuple):
    source: str
    target: str
    trigger: str

    @property
    def guard(self) -> str:
        """트리거 문자열 안 대괄호 조건. 표시용이며 판정에는 쓰지 않는다."""
        if "[" in self.trigger and "]" in self.trigger:
            return self.trigger[self.trigger.index("[") + 1: self.trigger.rindex("]")]
        return ""


# libi_modes/registry.py TRANSITIONS 와 1:1 (source "*" 만 START/ANY 로 풀어 적었다).
# 순서까지 원본과 같게 유지한다 — 프론트의 linkStyle 인덱스가 이 순서를 따른다.
EDGES: tuple[Edge, ...] = (
    Edge(START, "IDLE", "boot"),
    Edge("RETURNING", "CHARGING", "docked"),
    Edge("CHARGING", "IDLE", "battery_charged [battery >= 40%]"),
    Edge("IDLE", "PATROL", "patrol_request (auto [battery >= 80% && is_docked] / manual)"),
    Edge("IDLE", "WORKING", "task_assigned"),
    Edge("IDLE", "SECURITY_PATROL", "security_patrol_request"),
    Edge("PATROL", "WORKING", "task_assigned"),
    Edge("PATROL", "INTERACTING", "ui_touch"),
    Edge("PATROL", "IDLE", "stop_request"),
    Edge("INTERACTING", "PATROL", "ui_idle_timeout / ui_close"),
    Edge("INTERACTING", "WORKING", "task_assigned"),
    Edge("INTERACTING", "IDLE", "stop_request"),
    Edge("WORKING", "PATROL", "task_done / task_failed"),
    Edge("WORKING", "IDLE", "stop_request"),
    Edge("SECURITY_PATROL", "IDLE", "security_patrol_complete / stop_request"),
    Edge("IDLE", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    Edge("PATROL", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    Edge("SECURITY_PATROL", "RETURNING", "battery_low [battery <= 15% && !is_docked]"),
    Edge(ANY, "ERROR", "fault"),
    Edge("ERROR", "IDLE", "recovered"),
)


def allowed_targets(current: str) -> list[str]:
    """current 에서 전이 박스상 도달 가능한 상태들을 STATES 순서로 반환한다.

    (any) -> ERROR 그룹 간선 덕분에 ERROR 는 자기 자신을 제외한 모든 상태에서 도달 가능하다.
    boot 간선은 시작 의사상태에서만 나가므로 어떤 실제 상태의 목적지도 되지 않는다.
    """
    if current not in STATES:
        return []
    targets = {e.target for e in EDGES if e.source == current}
    if current != "ERROR":
        targets.add("ERROR")
    return [s for s in STATES if s in targets]


def validate(
    current: str,
    target: str,
    force: bool,
    error_code: str | None,
) -> tuple[bool, str]:
    """전이 요청의 수락 여부와 사유.

    INSTRUCTION.md 「전이 유효성 규칙」 + 「안전 규칙」:
      - 기본값은 전이 박스에 정의된 간선만 허용
      - ERROR 진입은 언제든 허용 (비상 수단)
      - ERROR 이탈은 기본적으로 error_code 확인 후에만

    force 는 간선 제약과 안전 규칙(ERROR 이탈의 error_code 확인)을 **둘 다** 푼다 —
    "강제전이는 모든 걸 가능하게" 요청에 따라, 관리자가 직접 켠 이상 원인 미상 상태
    에서도 수동으로 빠져나갈 수 있게 한다. 대신 그 사실을 reason 에 남겨 감사 로그에
    "왜 안전 규칙을 우회했는지"가 남게 한다.
    """
    if current not in STATES:
        return False, f"현재 상태 '{current}' 를 알 수 없습니다."
    if target not in STATES:
        return False, f"목표 상태 '{target}' 는 정의된 8종 상태가 아닙니다."
    if current == target:
        return False, f"이미 '{current}' 상태입니다."

    error_guard = current == "ERROR" and not error_code
    if error_guard and not force:
        return False, "ERROR 이탈은 error_code 확인 후에만 허용됩니다. (force 로 우회 가능)"
    if target == "ERROR":
        return True, ""

    if target in allowed_targets(current):
        if error_guard:
            return True, "강제 전이: error_code 확인 없이 ERROR 이탈을 허용했습니다."
        return True, ""
    if force:
        reason = f"강제 전이: '{current}' -> '{target}' 는 전이 박스에 없는 간선입니다."
        if error_guard:
            reason += " error_code 확인도 생략했습니다."
        return True, reason
    return False, f"'{current}' 에서 '{target}' 로 가는 간선이 전이 박스에 없습니다."


def to_mermaid() -> str:
    """EDGES 로부터 mermaid stateDiagram-v2 소스를 생성한다.

    다이어그램을 손으로 그리지 않으므로 전이 박스와 화면이 어긋날 수 없다.
    """
    lines = ["stateDiagram-v2"]
    for edge in EDGES:
        if edge.source == START:
            source = "[*]"
        elif edge.source == ANY:
            source = "AnyState"
        else:
            source = edge.source
        lines.append(f"    {source} --> {edge.target}: {edge.trigger}")
    return "\n".join(lines)


def as_dict() -> dict:
    """프론트엔드로 내보내는 직렬화 형태 — .tsx 는 이 응답만 보고 렌더링한다."""
    return {
        "states": list(STATES),
        "descriptions": STATE_DESCRIPTIONS,
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "trigger": e.trigger,
                "guard": e.guard,
            }
            for e in EDGES
        ],
        "allowed_targets": {s: allowed_targets(s) for s in STATES},
        "mermaid": to_mermaid(),
        "start": START,
        "any": ANY,
    }
