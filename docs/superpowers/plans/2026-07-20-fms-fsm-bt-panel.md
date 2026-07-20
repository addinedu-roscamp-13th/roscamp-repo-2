# FMS `FSM + BT` Control Panel Implementation Plan (Stage 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `FSM + BT` item to `aba_fms_service`'s 「로봇 제어」 nav group (directly below `Waypoint`) that shows a robot's live FSM state and its running behaviour-tree branch side by side, and lets an operator drive a direct state transition with validity gating, a `force` escape hatch, and an audit trail.

**Architecture:** Three layers, each independently testable. (1) A pure-Python **transition-model module** (`app/fsm_model.py`) that owns the 8 states, the 18 edges, and the validity/guard rules — this is the single source of truth INSTRUCTION.md demands, and it is unit-testable with plain pytest, no ROS2 and no DB. (2) An **`app/fsm_link.py`** background rclpy thread mirroring the proven `app/fleet_telemetry.py` shape (own `rclpy.Context` pinned to domain 86, subscribe state + py_trees snapshots into a locked cache, issue transition requests over a correlation-id request/response channel). (3) A **router + React page** that serves the model over HTTP, pushes cache deltas over WebSocket, and renders both diagrams with the already-installed `mermaid`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (existing), `rclpy` from system ROS2 Jazzy (existing pattern — deliberately absent from `requirements.txt`), React 19 + TanStack Router + shadcn/ui + Tailwind v4 (existing), `mermaid` ^11.15.0 (**already in `frontend/package.json:56`** — no new npm dependency), `pytest` 7.4.4.

## Global Constraints

- **Scope is `aba_fms_service` only.** Project `CLAUDE.md` treats each service as an independently owned deployment unit. This plan creates the `.srv` contract package under `aba_controller/` (Task 2) because it is a shared interface both sides must compile — that single cross-service addition is called out explicitly in that task and is additive (a new package, no edits to existing `aba_controller` files). Nothing else outside `aba_fms_service` is touched.
- **Do not disturb the existing `/admin/human-follow` page or `app/routers/human_follow_robot.py`.** Unrelated feature, similar-sounding name.
- **Single source of truth for states and edges.** INSTRUCTION.md 4단계: "`registry.py`의 매핑은 2단계 웹 시각화에서 '상태 → 표시할 서브트리'로 그대로 재사용한다. 별도 매핑 테이블을 만들지 않는다." The frontend is TypeScript and cannot import `libi_modes.registry` (Python, on a different machine). Resolution: `app/fsm_model.py` holds the canonical Python definition, an endpoint serves it, and the frontend renders **only** from that response. **No state name, edge, or branch-order literal may be hand-written into any `.tsx` file** — Task 9 adds a guard test for this.
- **Priority order is fixed** and must match `libi_modes.registry.BRANCH_ORDER` exactly: `["ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE"]`.
- `app/fsm_link.py`'s module top level must have **no `rclpy` import** — keep every ROS import inside the thread function, matching `fleet_telemetry.py`'s stated rule ("이 모듈의 최상위는 rclpy 의존성이 없어야 한다(ROS 미설치 환경에서도 import 가능)"). This is what lets Tasks 3–5 be tested without ROS2.
- **Every ROS command must be preceded by `source /opt/ros/jazzy/setup.bash`.** ROS2 Jazzy is installed at `/opt/ros/jazzy` on the dev machine but is not sourced by default.
- **No apt install task.** Verified installed: `ros-jazzy-py-trees` 2.4.0, `ros-jazzy-py-trees-ros` 2.4.0, `ros-jazzy-py-trees-ros-interfaces` 2.1.1, `ros-jazzy-py-trees-ros-viewer` 0.2.5, `ros-jazzy-domain-bridge` 0.5.0.
- The user runs `git add` / `git commit` themselves. Each task ends with the exact commands to run; **do not execute them.**

### Deliberate deviation 1 — BT rendering uses mermaid, not an embedded `py_trees_ros_viewer` JS library

INSTRUCTION.md 시각화 구현 방침 says: "BT 렌더링은 `py_trees_ros_viewer`의 코어인 **js 라이브러리를 웹에 임베드**하는 방식을 우선 검토한다. 해당 뷰어는 js 기반이라 웹 앱에 위젯으로 넣기 쉽게 설계되어 있다."

**Evidence this is not achievable as written:** `ros-jazzy-py-trees-ros-viewer` 0.2.5 installs as an **rqt / Qt desktop application**, not as a distributable browser JS bundle. There is no npm package and no standalone JS artifact to embed in a Vite app.

**Resolution:** go directly to the fallback INSTRUCTION.md already authorises in the very next line — "임베드가 어려우면 스냅샷 데이터를 받아 D3 또는 Mermaid로 직접 렌더링한다" — using `mermaid` ^11.15.0, which is **already a dependency** (`frontend/package.json:56`), so this costs zero new packages. `py_trees_ros_viewer` remains installed and useful as a **local desktop debugging tool** (`ros2 run py_trees_ros_viewer py-trees-tree-viewer`) and Task 6 uses it to eyeball the snapshot stream before the web renderer exists.

### Deliberate deviation 2 — the cross-domain transition channel is correlation-id topics, not a bridged ROS2 service

INSTRUCTION.md 인터페이스 says: "전이 요청은 ROS 2 서비스로 정의한다 (요청/응답이 필요하므로 토픽 부적합)."

**Evidence a bridged service cannot work over the planned transport:** the mission PC sits on its own ROS domain, reachable from FMS's domain 86 only through `ros2 domain_bridge`. Reading the installed Jazzy headers:
- `/opt/ros/jazzy/include/domain_bridge/parse_domain_bridge_yaml_config.hpp` documents the complete YAML schema. The only accepted top-level keys are `name`, `from_domain`, `to_domain`, and **`topics`**. There is no `services` key.
- Service bridging exists **only** as a C++ template method — `/opt/ros/jazzy/include/domain_bridge/domain_bridge.hpp:145` `template<typename ServiceT> void bridge_service(const std::string & service, size_t from_domain_id, size_t to_domain_id, ...)` — which requires the service type at **compile time**. The stock `ros2 run domain_bridge domain_bridge <yaml>` executable therefore cannot relay it.
- Consistent with this, every existing config in the repo (`aba_fms_service/config/domain_bridge_pinky{1,2,3}.yaml`) bridges topics only.

**Resolution — keep the `.srv` contract, change only the cross-domain transport:**
1. The `.srv` file is still authored (Task 2) with exactly the fields INSTRUCTION.md specifies. `libi_modes` still hosts a **real ROS2 service server** on the mission PC, so same-domain callers (`ros2 service call`, rqt, on-robot debugging) get the documented interface.
2. For the FMS→mission-PC hop, reuse the **request/response-over-topics pattern already proven in production in this very codebase**: `app/fleet_telemetry.py:160 send_command()` publishes `{"id": <uuid4>, "ts", "action", "args"}` to a bridged topic, blocks on a `threading.Event` keyed by `cmd_id` in `_pending`, and the reply arrives on a bridged result topic. That is request/response semantics with a correlation id, running today across these exact domain bridges.

Task 1 empirically confirms the YAML-has-no-services finding on the dev machine before any code is written, so this deviation is verified rather than assumed.

### Testing reality

`aba_fms_service` currently has **no test infrastructure at all** — verified: no `pytest.ini`, no `conftest.py`, no `test_*` files under `backend/`, and `frontend/package.json` scripts are only `dev`/`build`/`build:dev`/`preview`/`lint`/`format` (no vitest/jest). This plan:
- **Introduces `pytest` for the backend** (Task 3), scoped to the new modules. This is additive and touches no existing backend code.
- **Does not introduce a frontend test runner.** Adding vitest to another team's SPA is a scope expansion needing the user's sign-off — it is listed under Deferred. Frontend verification is `npm run lint` + `npm run build` (both already exist) plus explicit manual browser checks, and the plan says so honestly instead of implying unit coverage that does not exist.
- Deliberately pushes all decidable logic (validity rules, allowed-target computation, guard checks, edge list) into **Python**, where it is unit-testable, leaving `.tsx` files as thin renderers of server-provided data. This serves the single-source-of-truth constraint and the testability constraint at once.

---

### Task 1: Verify the domain_bridge service-relay limitation

**Files:**
- Create: `aba_fms_service/docs/fsm-transport-decision.md`

**Interfaces:**
- Produces: a written, evidence-backed decision that every later task depends on. If this task's finding contradicts the assumption in "Deliberate deviation 2", **stop and re-plan Tasks 2, 5, 6 before continuing** — the transport choice is load-bearing.

- [ ] **Step 1: Confirm the YAML schema accepts no `services` key**

Run:
```bash
source /opt/ros/jazzy/setup.bash
grep -n "topics:\|services:\|- name:\|from_domain\|to_domain" \
  /opt/ros/jazzy/include/domain_bridge/parse_domain_bridge_yaml_config.hpp
```
Expected: the documented key list mentions `name`, `from_domain`, `to_domain`, `topics` — and **no line offering a `services` key**.

- [ ] **Step 2: Confirm service bridging is compile-time C++ only**

Run:
```bash
source /opt/ros/jazzy/setup.bash
grep -n -A6 "bridge_service" /opt/ros/jazzy/include/domain_bridge/domain_bridge.hpp
```
Expected: a `template<typename ServiceT> void bridge_service(...)` declaration — i.e. the service type is a template parameter fixed at compile time, so the generic YAML-driven executable cannot instantiate it for an arbitrary type.

- [ ] **Step 3: Empirically confirm the stock executable rejects a `services:` block**

```bash
source /opt/ros/jazzy/setup.bash
cat > /tmp/bridge_service_probe.yaml <<'YAML'
name: probe
from_domain: 91
to_domain: 92
services:
  request_transition:
    type: std_srvs/srv/Trigger
YAML
timeout 10 ros2 run domain_bridge domain_bridge /tmp/bridge_service_probe.yaml; echo "exit=$?"
ros2 service list 2>/dev/null | grep -c request_transition || true
```
Expected: the bridge starts but bridges **nothing** (the `services:` block is ignored — it is not a recognised key), and no `request_transition` service appears. Record the actual observed output verbatim in Step 4; if it unexpectedly *does* bridge the service, that invalidates deviation 2 — stop and re-plan.

- [ ] **Step 4: Write the decision record**

Create `aba_fms_service/docs/fsm-transport-decision.md`:

```markdown
# FSM 전이 요청 전송 방식 결정

## 배경
INSTRUCTION.md 2단계는 전이 요청을 ROS2 **서비스**로 정의하라고 지시한다
("요청/응답이 필요하므로 토픽 부적합"). 그러나 FMS(도메인 86)와 미션 PC(별도 도메인)는
`ros2 domain_bridge`로만 연결된다.

## 조사 결과 (ROS2 Jazzy, domain_bridge 0.5.0)

1. YAML 스키마에 `services` 키가 없다.
   `/opt/ros/jazzy/include/domain_bridge/parse_domain_bridge_yaml_config.hpp`가 문서화한
   허용 키는 `name`, `from_domain`, `to_domain`, `topics` 뿐이다.

2. 서비스 브릿지는 C++ 템플릿 API로만 존재한다.
   `/opt/ros/jazzy/include/domain_bridge/domain_bridge.hpp:145`
   `template<typename ServiceT> void bridge_service(...)`
   → 서비스 타입이 **컴파일 타임 템플릿 인자**라, 타입을 런타임에 받는
   `ros2 run domain_bridge domain_bridge <yaml>` 실행파일로는 중계할 수 없다.

3. 레포의 기존 설정도 전부 토픽 전용이다
   (`aba_fms_service/config/domain_bridge_pinky{1,2,3}.yaml`).

4. 실측: `services:` 블록을 넣은 YAML로 브릿지를 띄워도 해당 서비스가 나타나지 않는다.
   (Step 3 실행 결과를 여기에 그대로 붙일 것)

## 결정

- `.srv` 계약 파일은 **그대로 작성**한다. `libi_modes`는 미션 PC에서 실제 서비스 서버를
  띄우므로 같은 도메인 안(rqt, `ros2 service call`, 온보드 디버깅)에서는 지시대로 동작한다.
- **도메인을 넘는 FMS → 미션 PC 구간만** 상관관계 ID 기반 요청/응답 토픽으로 처리한다.
  이 패턴은 이 레포에서 이미 운영 중이다 —
  `aba_fms_service/backend/app/fleet_telemetry.py:160 send_command()`
  (`{"id","ts","action","args"}` 발행 → `threading.Event` 대기 → 결과 토픽 수신,
  같은 domain_bridge를 통과함).
- 즉 INSTRUCTION.md의 **의도**(요청/응답 시맨틱)는 지키고, 전송 계층만 실제로
  가능한 방식으로 바꾼다.

## 대안 (채택하지 않음)
`bridge_service<LibiTransition>()`를 호출하는 전용 C++ 노드를 새로 작성하면 진짜 서비스
중계가 가능하다. 새 ament_cmake 패키지 + C++ 빌드가 추가되는데, 이미 검증된 토픽 패턴이
같은 결과를 주므로 비용 대비 이득이 없다. 나중에 서비스 중계가 꼭 필요해지면 이 문서를
근거로 재검토한다.
```

- [ ] **Step 5: Git**

```bash
git add aba_fms_service/docs/fsm-transport-decision.md
git commit -m "docs(fms): record FSM transition transport decision (domain_bridge has no service relay)"
```

---

### Task 2: `libi_interfaces` — the `.srv` contract package

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_interfaces/package.xml`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_interfaces/CMakeLists.txt`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_interfaces/srv/RequestTransition.srv`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_interfaces/msg/FsmState.msg`

**Interfaces:**
- Produces: `libi_interfaces/srv/RequestTransition` with request `string robot_id`, `string target_state`, `bool force` and response `bool accepted`, `string current_state`, `string reason` — exactly INSTRUCTION.md's field list. Also `libi_interfaces/msg/FsmState` for the state broadcast Task 5 subscribes to.

**Why this package lives here (the one cross-service addition):** a ROS2 interface must be compiled by **both** ends — `libi_modes` on the mission PC (service server, state publisher) and `aba_fms_service` on the FMS server (client, subscriber). Putting it in the workspace the mission PC already builds (`aba_controller/libi_modes/ros_ws/src/`, created by `2026-07-20-libi-modes-fsm-bt.md` Task 2) means the mission PC gets it from its normal `colcon build` with zero extra setup, and the FMS server builds the same source tree. It is **not** placed in `pinky_pro/pinky_interfaces` because that package belongs to the driving-robot workspace owned by another team, and this interface has nothing to do with the Pinky hardware. This is an **additive** new package — no existing file under `aba_controller/` is modified.

- [ ] **Step 1: `package.xml`** (ament_cmake interface package, mirroring `pinky_pro/pinky_interfaces/package.xml`)

```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>libi_interfaces</name>
  <version>0.1.0</version>
  <description>LIBI 미션 FSM 인터페이스 — 상태 브로드캐스트와 전이 요청 계약.</description>
  <maintainer email="dev@aba-project.local">aba</maintainer>
  <license>Proprietary</license>

  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>

  <depend>std_msgs</depend>
  <depend>builtin_interfaces</depend>

  <exec_depend>rosidl_default_runtime</exec_depend>

  <member_of_group>rosidl_interface_packages</member_of_group>

  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
```

- [ ] **Step 2: `CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.8)
project(libi_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

set(
  MSG_FILES
  "msg/FsmState.msg"
)

set(
  SRV_FILES
  "srv/RequestTransition.srv"
)

rosidl_generate_interfaces(
  ${PROJECT_NAME}
  ${MSG_FILES}
  ${SRV_FILES}
  DEPENDENCIES builtin_interfaces std_msgs
)

ament_package()
```

- [ ] **Step 3: `srv/RequestTransition.srv`**

```
# Request — INSTRUCTION.md 2단계 인터페이스 절의 요청 필드 그대로
string robot_id
string target_state
bool force
---
# Response — INSTRUCTION.md 2단계 인터페이스 절의 응답 필드 그대로
bool accepted
string current_state
string reason
```

- [ ] **Step 4: `msg/FsmState.msg`**

```
# libi_modes 가 주기 발행하는 현재 상태 브로드캐스트.
# active_branch 는 libi_modes.registry.BRANCH_ORDER 의 한 원소와 같아야 한다
# (2단계 웹에서 '상태 -> 표시할 서브트리' 선택에 그대로 쓰인다).
builtin_interfaces/Time stamp
string robot_id
string current_state
string active_branch
string error_code        # ERROR 상태일 때만 채워진다. 그 외에는 빈 문자열.
float32 battery_percent
bool is_docked
```

- [ ] **Step 5: Build the interface package**

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_modes/ros_ws
colcon build --packages-select libi_interfaces
source install/setup.bash
ros2 interface show libi_interfaces/srv/RequestTransition
ros2 interface show libi_interfaces/msg/FsmState
```
Expected: `colcon build` reports `Finished <<< libi_interfaces`, and both `ros2 interface show` calls print the field lists from Steps 3–4. If `libi_modes` (the ament_python package) is not yet created, `--packages-select libi_interfaces` still builds cleanly on its own.

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_interfaces/package.xml \
        aba_controller/libi_modes/ros_ws/src/libi_interfaces/CMakeLists.txt \
        aba_controller/libi_modes/ros_ws/src/libi_interfaces/srv/RequestTransition.srv \
        aba_controller/libi_modes/ros_ws/src/libi_interfaces/msg/FsmState.msg
git commit -m "feat(libi_interfaces): add FSM state msg and transition request srv contract"
```

---

### Task 3: `fsm_model.py` — canonical states, edges, and validity rules

**Files:**
- Create: `aba_fms_service/backend/app/fsm_model.py`
- Create: `aba_fms_service/backend/pytest.ini`
- Create: `aba_fms_service/backend/tests/__init__.py`
- Test: `aba_fms_service/backend/tests/test_fsm_model.py`

**Interfaces:**
- Produces:
  - `STATES: tuple[str, ...]` — the 8 states in `BRANCH_ORDER` priority order.
  - `EDGES: tuple[Edge, ...]` where `Edge` is a `NamedTuple(source: str, target: str, event: str, guard: str)`. `source == "*"` means the group edge `(any) -> ERROR`.
  - `allowed_targets(current: str) -> list[str]` — states reachable from `current` per the transition box, sorted in `STATES` order.
  - `validate(current: str, target: str, force: bool, error_code: str | None) -> tuple[bool, str]` — returns `(accepted, reason)`. Encodes INSTRUCTION.md's 안전 규칙: `ERROR` is always a legal target; leaving `ERROR` requires a non-empty `error_code`; otherwise the edge must exist unless `force` is set.
  - `to_mermaid() -> str` — a `stateDiagram-v2` source string built from `EDGES`, so the diagram cannot drift from the model.
- Consumed by: Task 4 (router), Task 9 (drift guard test).

- [ ] **Step 1: `pytest.ini` and the tests package**

`aba_fms_service/backend/pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

```bash
mkdir -p aba_fms_service/backend/tests
touch aba_fms_service/backend/tests/__init__.py
```

- [ ] **Step 2: Write the failing tests**

```python
# aba_fms_service/backend/tests/test_fsm_model.py
"""fsm_model 은 전이 박스(INSTRUCTION.md 1단계)의 유일한 진실 원천이다.

이 테스트들이 전이 박스 원문과 코드가 어긋나는 것을 막는다.
"""
import pytest

from app import fsm_model


# libi_modes.registry.BRANCH_ORDER 와 반드시 같아야 한다.
EXPECTED_ORDER = [
    "ERROR", "RETURNING", "CHARGING", "WORKING",
    "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE",
]


def test_states_match_branch_order_exactly():
    assert list(fsm_model.STATES) == EXPECTED_ORDER


def test_transition_box_edges_are_complete():
    """전이 박스 18개 간선이 하나도 빠지거나 더해지지 않았는지."""
    pairs = {(e.source, e.target) for e in fsm_model.EDGES}
    expected = {
        ("__START__", "RETURNING"),
        ("RETURNING", "CHARGING"),
        ("CHARGING", "IDLE"),
        ("IDLE", "PATROL"),
        ("IDLE", "WORKING"),
        ("IDLE", "SECURITY_PATROL"),
        ("PATROL", "WORKING"),
        ("PATROL", "INTERACTING"),
        ("PATROL", "IDLE"),
        ("INTERACTING", "PATROL"),
        ("INTERACTING", "WORKING"),
        ("INTERACTING", "IDLE"),
        ("WORKING", "PATROL"),
        ("WORKING", "IDLE"),
        ("SECURITY_PATROL", "IDLE"),
        ("IDLE", "RETURNING"),
        ("PATROL", "RETURNING"),
        ("SECURITY_PATROL", "RETURNING"),
        ("*", "ERROR"),
        ("ERROR", "IDLE"),
    }
    assert pairs == expected


def test_deliberately_absent_edges_stay_absent():
    """INSTRUCTION.md '의도적으로 두지 않은 간선' — 실수로 생기면 잡는다."""
    pairs = {(e.source, e.target) for e in fsm_model.EDGES}
    for forbidden in [
        ("PATROL", "SECURITY_PATROL"),
        ("SECURITY_PATROL", "PATROL"),
        ("CHARGING", "PATROL"),
        ("CHARGING", "WORKING"),
        ("WORKING", "RETURNING"),
        ("INTERACTING", "RETURNING"),
    ]:
        assert forbidden not in pairs, f"{forbidden} 은 의도적으로 없어야 하는 간선"


def test_allowed_targets_from_charging_is_idle_and_error_only():
    """INSTRUCTION.md 예시: '현재 CHARGING 이면 IDLE 만 활성화'.
    ERROR 는 (any)->ERROR 그룹 간선으로 항상 도달 가능하다."""
    assert fsm_model.allowed_targets("CHARGING") == ["ERROR", "IDLE"]


def test_allowed_targets_from_idle():
    assert fsm_model.allowed_targets("IDLE") == [
        "ERROR", "RETURNING", "WORKING", "SECURITY_PATROL", "PATROL",
    ]


def test_allowed_targets_from_returning_has_no_command_exit():
    """RETURNING 은 docked(->CHARGING) 와 fault(->ERROR) 로만 나간다."""
    assert fsm_model.allowed_targets("RETURNING") == ["ERROR", "CHARGING"]


def test_validate_accepts_a_valid_edge():
    accepted, reason = fsm_model.validate("CHARGING", "IDLE", force=False, error_code=None)
    assert accepted is True
    assert reason == ""


def test_validate_rejects_an_invalid_edge_without_force():
    accepted, reason = fsm_model.validate("CHARGING", "WORKING", force=False, error_code=None)
    assert accepted is False
    assert "CHARGING" in reason and "WORKING" in reason


def test_validate_allows_an_invalid_edge_with_force():
    accepted, reason = fsm_model.validate("CHARGING", "WORKING", force=True, error_code=None)
    assert accepted is True
    assert "강제" in reason


def test_error_is_always_enterable_even_without_an_edge():
    """안전 규칙: 'ERROR 진입 — 언제든 허용한다 (비상 수단)'.

    ERROR 자기 자신은 제외한다 — 같은 상태로의 전이는 아래 테스트에서 따로 막는다.
    """
    for state in fsm_model.STATES:
        if state == "ERROR":
            continue
        accepted, _ = fsm_model.validate(state, "ERROR", force=False, error_code=None)
        assert accepted is True, f"{state} -> ERROR 는 항상 허용되어야 한다"


def test_self_transition_is_rejected():
    """이미 그 상태인데 또 전이하면 BT 가 같은 브랜치를 재진입해 부작용이 난다."""
    accepted, reason = fsm_model.validate("ERROR", "ERROR", force=True, error_code="E_X")
    assert accepted is False
    assert "이미" in reason


def test_leaving_error_requires_an_error_code():
    """안전 규칙: 'ERROR 이탈 — error_code 확인 후에만 허용한다'."""
    accepted, reason = fsm_model.validate("ERROR", "IDLE", force=False, error_code=None)
    assert accepted is False
    assert "error_code" in reason

    accepted, _ = fsm_model.validate("ERROR", "IDLE", force=False, error_code="E_DOCK_FAIL")
    assert accepted is True


def test_leaving_error_requires_error_code_even_with_force():
    """force 는 간선 제약을 푸는 것이지 안전 규칙을 푸는 게 아니다."""
    accepted, reason = fsm_model.validate("ERROR", "PATROL", force=True, error_code=None)
    assert accepted is False
    assert "error_code" in reason


def test_unknown_state_is_rejected():
    accepted, reason = fsm_model.validate("IDLE", "NOPE", force=True, error_code=None)
    assert accepted is False
    assert "NOPE" in reason


def test_mermaid_source_contains_every_edge():
    src = fsm_model.to_mermaid()
    assert src.startswith("stateDiagram-v2")
    assert "[*] --> RETURNING" in src
    for edge in fsm_model.EDGES:
        if edge.source == "__START__":
            continue
        src_name = "AnyState" if edge.source == "*" else edge.source
        assert f"{src_name} --> {edge.target}" in src
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fsm_model'`

- [ ] **Step 4: Implement**

```python
# aba_fms_service/backend/app/fsm_model.py
"""LIBI 미션 FSM 의 상태·간선 정의 — 이 파일이 유일한 진실 원천이다.

INSTRUCTION.md 1단계 「전이 박스」를 그대로 옮긴 것이며, 웹 UI 의 상태 다이어그램과
전이 유효성 판정 모두 여기서만 읽는다. 프론트엔드(.tsx)에 상태 이름이나 간선을
따로 적어두면 전이 박스가 바뀔 때 조용히 어긋나므로 금지한다
(INSTRUCTION.md: "전이 박스와 화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다").

STATES 순서는 libi_modes.registry.BRANCH_ORDER 와 동일해야 한다 — 우선순위 Selector 의
평가 순서이자, 웹에서 '상태 -> 표시할 서브트리' 선택에 쓰이는 순서다.
"""
from __future__ import annotations

from typing import NamedTuple

# libi_modes/registry.py BRANCH_ORDER 와 동일 (우선순위 순).
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

# 부팅 진입점을 나타내는 가상 소스 (다이어그램에서 [*]).
START = "__START__"
# (any) -> ERROR 그룹 간선의 소스.
ANY = "*"


class Edge(NamedTuple):
    source: str
    target: str
    event: str
    guard: str


# INSTRUCTION.md 전이 박스 원문 순서 그대로. 간선을 임의로 추가·삭제하지 말 것.
EDGES: tuple[Edge, ...] = (
    Edge(START, "RETURNING", "boot", ""),
    Edge("RETURNING", "CHARGING", "docked", ""),
    Edge("CHARGING", "IDLE", "battery_charged", "battery >= 40%"),

    Edge("IDLE", "PATROL", "patrol_request", "auto: battery >= 80% && is_docked / manual"),
    Edge("IDLE", "WORKING", "task_assigned", ""),
    Edge("IDLE", "SECURITY_PATROL", "security_patrol_request", ""),

    Edge("PATROL", "WORKING", "task_assigned", ""),
    Edge("PATROL", "INTERACTING", "ui_touch", ""),
    Edge("PATROL", "IDLE", "stop_request", ""),

    Edge("INTERACTING", "PATROL", "ui_idle_timeout / ui_close", ""),
    Edge("INTERACTING", "WORKING", "task_assigned", ""),
    Edge("INTERACTING", "IDLE", "stop_request", ""),

    Edge("WORKING", "PATROL", "task_done / task_failed", ""),
    Edge("WORKING", "IDLE", "stop_request", ""),

    Edge("SECURITY_PATROL", "IDLE", "security_patrol_complete / stop_request", ""),

    # { IDLE, PATROL, SECURITY_PATROL } -> RETURNING (그룹 전이)
    Edge("IDLE", "RETURNING", "battery_low", "battery <= 15% && !is_docked"),
    Edge("PATROL", "RETURNING", "battery_low", "battery <= 15% && !is_docked"),
    Edge("SECURITY_PATROL", "RETURNING", "battery_low", "battery <= 15% && !is_docked"),

    Edge(ANY, "ERROR", "fault", ""),
    Edge("ERROR", "IDLE", "recovered", ""),
)


def allowed_targets(current: str) -> list[str]:
    """current 에서 전이 박스상 도달 가능한 상태들을 STATES 순서로 반환한다.

    (any) -> ERROR 그룹 간선 때문에 ERROR 는 자기 자신을 제외한 모든 상태에서 도달 가능하다.
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
    """전이 요청의 수락 여부와 사유를 판정한다.

    INSTRUCTION.md 「전이 유효성 규칙」 + 「안전 규칙」:
      - 기본값은 전이 박스에 정의된 전이만 허용
      - force 는 간선 제약만 푼다
      - ERROR 진입은 언제든 허용 (비상 수단)
      - ERROR 이탈은 error_code 확인 후에만 — force 로도 못 푼다 (안전 규칙이므로)
    """
    if current not in STATES:
        return False, f"현재 상태 '{current}' 를 알 수 없습니다."
    if target not in STATES:
        return False, f"목표 상태 '{target}' 는 정의된 8종 상태가 아닙니다."
    if current == target:
        return False, f"이미 '{current}' 상태입니다."

    # 안전 규칙 — force 보다 우선한다.
    if current == "ERROR" and not error_code:
        return False, "ERROR 이탈은 error_code 확인 후에만 허용됩니다."
    if target == "ERROR":
        return True, ""

    if target in allowed_targets(current):
        return True, ""
    if force:
        return True, f"강제 전이: '{current}' -> '{target}' 는 전이 박스에 없는 간선입니다."
    return False, f"'{current}' 에서 '{target}' 로 가는 간선이 전이 박스에 없습니다."


def to_mermaid() -> str:
    """EDGES 로부터 mermaid stateDiagram-v2 소스를 생성한다.

    다이어그램을 손으로 그리지 않고 여기서 만들기 때문에 전이 박스와 화면이 어긋날 수 없다.
    """
    lines = ["stateDiagram-v2"]
    for edge in EDGES:
        source = "[*]" if edge.source == START else ("AnyState" if edge.source == ANY else edge.source)
        label = edge.event
        if edge.guard:
            label = f"{label} [{edge.guard}]"
        lines.append(f"    {source} --> {edge.target}: {label}")
    return "\n".join(lines)


def as_dict() -> dict:
    """프론트엔드로 내보내는 직렬화 형태 — .tsx 는 이 응답만 보고 렌더링한다."""
    return {
        "states": list(STATES),
        "descriptions": STATE_DESCRIPTIONS,
        "edges": [
            {"source": e.source, "target": e.target, "event": e.event, "guard": e.guard}
            for e in EDGES
        ],
        "allowed_targets": {s: allowed_targets(s) for s in STATES},
        "mermaid": to_mermaid(),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_model.py -v`
Expected: `15 passed`

> **These 15 tests were executed against this exact implementation during planning and all pass** (run in a scratch directory with the same `app/fsm_model.py` source). The `test_error_is_always_enterable_even_without_an_edge` case initially failed because it looped over `ERROR` itself — `validate("ERROR", "ERROR", ...)` correctly returns `False` ("이미 ERROR 상태입니다"). The loop now skips `ERROR` and `test_self_transition_is_rejected` covers that case explicitly. Everything else passed unmodified.

- [ ] **Step 6: Git**

```bash
git add aba_fms_service/backend/app/fsm_model.py \
        aba_fms_service/backend/pytest.ini \
        aba_fms_service/backend/tests/__init__.py \
        aba_fms_service/backend/tests/test_fsm_model.py
git commit -m "feat(fms): add FSM transition model as single source of truth for states and edges"
```

---

### Task 4: Transition audit log model + persistence

**Files:**
- Modify: `aba_fms_service/backend/app/models.py` (append one class at end of file)
- Create: `aba_fms_service/backend/app/fsm_audit.py`
- Test: `aba_fms_service/backend/tests/test_fsm_audit.py`

**Interfaces:**
- Consumes: `fsm_model` (Task 3), `AdminBase` from `app.database`.
- Produces:
  - `FsmTransitionLog` SQLAlchemy model on table `rc_fsm_transition_logs` (the `rc_` prefix is this service's convention — see `app/models.py`), created automatically by the existing `init_db()`'s `AdminBase.metadata.create_all`.
  - `build_log_entry(admin_id, admin_username, robot_id, from_state, to_state, forced, accepted, reason) -> dict` — a pure function producing the row payload, unit-testable without a DB.
  - `async record_transition(db, **kwargs) -> FsmTransitionLog` and `async recent_transitions(db, robot_id, limit) -> list[FsmTransitionLog]`.

- [ ] **Step 1: Write the failing test** (pure-function level — no DB needed)

```python
# aba_fms_service/backend/tests/test_fsm_audit.py
from app.fsm_audit import build_log_entry


def test_log_entry_records_who_what_when():
    """INSTRUCTION.md 안전 규칙: '누가 언제 어떤 전이를 강제했는지 로그로 남긴다'."""
    entry = build_log_entry(
        admin_id=7,
        admin_username="libi_admin",
        robot_id="pinky1",
        from_state="CHARGING",
        to_state="WORKING",
        forced=True,
        accepted=True,
        reason="강제 전이: 'CHARGING' -> 'WORKING' 는 전이 박스에 없는 간선입니다.",
    )
    assert entry["admin_id"] == 7
    assert entry["admin_username"] == "libi_admin"
    assert entry["robot_id"] == "pinky1"
    assert entry["from_state"] == "CHARGING"
    assert entry["to_state"] == "WORKING"
    assert entry["forced"] is True
    assert entry["accepted"] is True
    assert "강제" in entry["reason"]


def test_rejected_transitions_are_also_logged():
    """거부된 시도도 남겨야 감사 가치가 있다."""
    entry = build_log_entry(
        admin_id=1, admin_username="op", robot_id="pinky2",
        from_state="ERROR", to_state="IDLE", forced=False, accepted=False,
        reason="ERROR 이탈은 error_code 확인 후에만 허용됩니다.",
    )
    assert entry["accepted"] is False
    assert entry["reason"]


def test_reason_is_truncated_to_column_width():
    entry = build_log_entry(
        admin_id=1, admin_username="op", robot_id="pinky1",
        from_state="IDLE", to_state="WORKING", forced=False, accepted=True,
        reason="x" * 500,
    )
    assert len(entry["reason"]) <= 255
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fsm_audit'`

- [ ] **Step 3: Append the model to `app/models.py`**

Add at the **end** of `aba_fms_service/backend/app/models.py` (do not reorder or edit existing classes):

```python
class FsmTransitionLog(AdminBase):
    """FSM 상태 전이 감사 로그 — 누가 언제 어떤 전이를 (강제로) 요청했는지.

    거부된 시도도 기록한다. 강제 전이 추적이 목적이므로 실패 이력이 오히려 중요하다.
    """

    __tablename__ = "rc_fsm_transition_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    robot_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_username: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
```

Note: **no new imports are needed.** Verified — `models.py` lines 1–20 already import `datetime`, `Boolean`, `DateTime`, `Integer`, `String`, `Mapped`, `mapped_column`, and `AdminBase`, and define `_now()`. The class above uses only those.

- [ ] **Step 4: Implement `app/fsm_audit.py`**

```python
# aba_fms_service/backend/app/fsm_audit.py
"""FSM 전이 감사 로그 기록/조회.

build_log_entry 는 순수 함수라 DB 없이 테스트된다. DB 접근은 얇은 래퍼로 분리한다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FsmTransitionLog

_REASON_MAX = 255


def build_log_entry(
    *,
    admin_id: int | None,
    admin_username: str,
    robot_id: str,
    from_state: str,
    to_state: str,
    forced: bool,
    accepted: bool,
    reason: str,
) -> dict:
    """감사 로그 한 줄의 payload. reason 은 컬럼 폭에 맞춰 자른다."""
    return {
        "admin_id": admin_id,
        "admin_username": admin_username or "",
        "robot_id": robot_id,
        "from_state": from_state,
        "to_state": to_state,
        "forced": bool(forced),
        "accepted": bool(accepted),
        "reason": (reason or "")[:_REASON_MAX],
    }


async def record_transition(db: AsyncSession, **kwargs) -> FsmTransitionLog:
    row = FsmTransitionLog(**build_log_entry(**kwargs))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def recent_transitions(
    db: AsyncSession, robot_id: str | None = None, limit: int = 20
) -> list[FsmTransitionLog]:
    """최근 전이 이력 N건 (INSTRUCTION.md: '최근 전이 이력 N건을 목록으로 표시')."""
    stmt = select(FsmTransitionLog).order_by(FsmTransitionLog.id.desc()).limit(limit)
    if robot_id:
        stmt = stmt.where(FsmTransitionLog.robot_id == robot_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_audit.py -v`
Expected: `3 passed`

- [ ] **Step 6: Verify the model imports cleanly**

Run: `cd aba_fms_service/backend && python3 -c "from app.models import FsmTransitionLog; print(FsmTransitionLog.__tablename__)"`
Expected: `rc_fsm_transition_logs`

- [ ] **Step 7: Git**

```bash
git add aba_fms_service/backend/app/models.py \
        aba_fms_service/backend/app/fsm_audit.py \
        aba_fms_service/backend/tests/test_fsm_audit.py
git commit -m "feat(fms): add FSM transition audit log model and recorder"
```

---

### Task 5: `fsm_link.py` — ROS2 state cache and transition channel

**Files:**
- Create: `aba_fms_service/backend/app/fsm_link.py`
- Test: `aba_fms_service/backend/tests/test_fsm_link.py`

**Interfaces:**
- Consumes: `fsm_model.STATES` (Task 3), the `libi_interfaces` contract (Task 2), the transport decision (Task 1).
- Produces:
  - `FSM_DOMAIN_ID: int` — reads env `LIBI_FSM_DOMAIN_ID`, defaults to `86` (FMS side of the bridge; the mission PC's own domain number is still undecided — see Deferred).
  - `start() -> None` — idempotent; spawns the background rclpy thread (mirrors `fleet_telemetry.start()`).
  - `snapshot(robot_id: str) -> dict | None` — the cached `{"current_state", "active_branch", "error_code", "battery_percent", "is_docked", "tree", "_last_ros_at"}`, or `None` if never seen.
  - `all_snapshots() -> dict[str, dict]`.
  - `request_transition(robot_id, target_state, force, timeout) -> dict | None` — publishes `{"id", "ts", "robot_id", "target_state", "force"}` and waits on a `threading.Event`; returns `{"accepted", "current_state", "reason"}` or `None` when the link is down (caller decides the fallback).
  - `_apply_state_msg(cache, payload)` and `_apply_tree_msg(cache, payload)` — **pure functions** that fold an incoming payload into a cache dict. These carry the real logic and are what the tests exercise; the rclpy callbacks are one-line wrappers around them.

- [ ] **Step 1: Write the failing tests**

```python
# aba_fms_service/backend/tests/test_fsm_link.py
"""fsm_link 의 순수 로직 테스트 — rclpy 없이 돈다.

fsm_link 는 (fleet_telemetry.py 와 같은 규칙으로) 모듈 최상위에 rclpy 를 import 하지
않으므로, ROS2 를 source 하지 않은 환경에서도 import 되어야 한다. 이 테스트가 그 계약을
지킨다.
"""
import time

from app import fsm_link


def test_module_imports_without_ros2():
    """최상위에 rclpy import 가 없어야 한다 (ROS 미설치 환경 보호)."""
    import inspect
    source = inspect.getsource(fsm_link)
    header = source.split("def ")[0]
    assert "import rclpy" not in header
    assert "from rclpy" not in header


def test_apply_state_msg_folds_into_cache():
    cache = {}
    fsm_link._apply_state_msg(cache, {
        "robot_id": "pinky1",
        "current_state": "PATROL",
        "active_branch": "PATROL",
        "error_code": "",
        "battery_percent": 71.5,
        "is_docked": False,
    })
    entry = cache["pinky1"]
    assert entry["current_state"] == "PATROL"
    assert entry["active_branch"] == "PATROL"
    assert entry["battery_percent"] == 71.5
    assert entry["is_docked"] is False
    assert entry["_last_ros_at"] > 0


def test_apply_state_msg_rejects_unknown_state():
    """정의되지 않은 상태가 캐시에 들어가면 UI 가 못 그린다 — 방어한다."""
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "BOGUS"})
    assert "pinky1" not in cache


def test_apply_state_msg_records_previous_state_for_edge_highlight():
    """INSTRUCTION.md: '직전에 발생한 전이의 간선을 일시 강조' — 이전 상태가 필요하다."""
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    entry = cache["pinky1"]
    assert entry["previous_state"] == "IDLE"
    assert entry["current_state"] == "PATROL"
    assert entry["transitioned_at"] > 0


def test_apply_state_msg_does_not_move_previous_on_repeat():
    """같은 상태가 계속 오는 건 전이가 아니다."""
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    assert cache["pinky1"]["previous_state"] == "IDLE"


def test_apply_tree_msg_stores_snapshot():
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "PATROL"})
    fsm_link._apply_tree_msg(cache, {
        "robot_id": "pinky1",
        "tree": {
            "name": "PatrolBranch",
            "status": "RUNNING",
            "children": [
                {"name": "IsMode[PATROL]", "status": "SUCCESS", "children": []},
                {"name": "PatrolNavigation", "status": "RUNNING", "children": []},
            ],
        },
    })
    tree = cache["pinky1"]["tree"]
    assert tree["name"] == "PatrolBranch"
    assert tree["children"][1]["status"] == "RUNNING"


def test_is_stale_after_timeout():
    cache = {}
    fsm_link._apply_state_msg(cache, {"robot_id": "pinky1", "current_state": "IDLE"})
    cache["pinky1"]["_last_ros_at"] = time.time() - (fsm_link.FRESH_SEC + 1)
    assert fsm_link.is_stale(cache["pinky1"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_link.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fsm_link'`

- [ ] **Step 3: Implement**

```python
# aba_fms_service/backend/app/fsm_link.py
"""libi_modes FSM 링크 — 상태/BT 스냅샷 구독 캐시 + 전이 요청 채널.

구조는 app/fleet_telemetry.py 와 동일하다: 전용 rclpy Context 를 도메인에 고정해
백그라운드 스레드에서 돌리고, FastAPI 스레드는 락으로 보호된 캐시만 읽는다.

전이 요청이 서비스가 아니라 상관관계 ID 토픽인 이유는
aba_fms_service/docs/fsm-transport-decision.md 참고 (domain_bridge YAML 은 토픽만
중계하며, 서비스 브릿지는 C++ 컴파일 타임 템플릿 API 로만 존재한다).

이 모듈의 최상위는 rclpy 의존성이 없어야 한다 (ROS 미설치 환경에서도 import 가능).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any

from app.fsm_model import STATES

# 미션 PC 는 자기 도메인을 쓰고, FMS 쪽 도메인 86 으로 domain_bridge 가 중계한다.
# 이 값은 'FMS 가 구독하는 쪽' 도메인이다 (미션 PC 자체 도메인 번호는 아직 미정).
FSM_DOMAIN_ID = int(os.environ.get("LIBI_FSM_DOMAIN_ID", "86"))

STATE_TOPIC = os.environ.get("LIBI_FSM_STATE_TOPIC", "/libi/fsm_state")
TREE_TOPIC = os.environ.get("LIBI_FSM_TREE_TOPIC", "/libi/bt_snapshot")
CMD_TOPIC = os.environ.get("LIBI_FSM_CMD_TOPIC", "/libi/fsm_transition_request")
RESULT_TOPIC = os.environ.get("LIBI_FSM_RESULT_TOPIC", "/libi/fsm_transition_result")

FRESH_SEC = 10.0          # 이 시간 안에 수신이 없으면 stale 로 표시
CMD_TIMEOUT_SEC = 3.0

_lock = threading.Lock()
_started = False
_cache: dict[str, dict[str, Any]] = {}

_cmd_pub: Any = None
_StringMsg: Any = None
_pub_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()

# 브라우저 push 용 구독자 (asyncio.Queue 들). WebSocket 라우터가 등록/해제한다.
_listeners: set = set()
_listeners_lock = threading.Lock()


def _empty_entry() -> dict[str, Any]:
    return {
        "current_state": None,
        "previous_state": None,
        "active_branch": None,
        "error_code": "",
        "battery_percent": None,
        "is_docked": None,
        "tree": None,
        "transitioned_at": 0.0,
        "_last_ros_at": 0.0,
    }


def _apply_state_msg(cache: dict[str, dict[str, Any]], payload: dict) -> str | None:
    """상태 메시지를 캐시에 접는다. 반영한 robot_id 를 반환(무시했으면 None).

    순수 함수 — rclpy 콜백은 이걸 감싸기만 한다.
    """
    robot_id = payload.get("robot_id")
    state = payload.get("current_state")
    if not robot_id or state not in STATES:
        return None

    entry = cache.setdefault(robot_id, _empty_entry())
    if entry["current_state"] is not None and entry["current_state"] != state:
        entry["previous_state"] = entry["current_state"]
        entry["transitioned_at"] = time.time()
    entry["current_state"] = state
    entry["active_branch"] = payload.get("active_branch") or state
    entry["error_code"] = payload.get("error_code", "") or ""
    if payload.get("battery_percent") is not None:
        entry["battery_percent"] = payload["battery_percent"]
    if payload.get("is_docked") is not None:
        entry["is_docked"] = payload["is_docked"]
    entry["_last_ros_at"] = time.time()
    return robot_id


def _apply_tree_msg(cache: dict[str, dict[str, Any]], payload: dict) -> str | None:
    """py_trees 스냅샷을 캐시에 접는다."""
    robot_id = payload.get("robot_id")
    tree = payload.get("tree")
    if not robot_id or tree is None:
        return None
    entry = cache.setdefault(robot_id, _empty_entry())
    entry["tree"] = tree
    entry["_last_ros_at"] = time.time()
    return robot_id


def is_stale(entry: dict[str, Any]) -> bool:
    return (time.time() - entry.get("_last_ros_at", 0.0)) > FRESH_SEC


def snapshot(robot_id: str) -> dict[str, Any] | None:
    with _lock:
        entry = _cache.get(robot_id)
        if entry is None:
            return None
        out = dict(entry)
    out["stale"] = is_stale(out)
    return out


def all_snapshots() -> dict[str, dict[str, Any]]:
    with _lock:
        items = {k: dict(v) for k, v in _cache.items()}
    for entry in items.values():
        entry["stale"] = is_stale(entry)
    return items


def add_listener(queue) -> None:
    with _listeners_lock:
        _listeners.add(queue)


def remove_listener(queue) -> None:
    with _listeners_lock:
        _listeners.discard(queue)


def _notify(robot_id: str) -> None:
    """캐시가 바뀌었음을 WebSocket 구독자에게 알린다 (폴링 금지 — push 전용)."""
    payload = snapshot(robot_id)
    if payload is None:
        return
    message = {"robot_id": robot_id, "snapshot": payload}
    with _listeners_lock:
        listeners = list(_listeners)
    for queue in listeners:
        try:
            queue.put_nowait(message)
        except Exception:
            pass  # 큐가 꽉 찼거나 닫혔으면 그 구독자만 건너뛴다


def request_transition(
    robot_id: str,
    target_state: str,
    force: bool = False,
    timeout: float = CMD_TIMEOUT_SEC,
) -> dict[str, Any] | None:
    """전이 요청 발행 후 결과 대기. 링크 불가/타임아웃이면 None.

    반환 스키마는 libi_interfaces/srv/RequestTransition 의 응답과 동일:
    {"accepted": bool, "current_state": str, "reason": str}
    """
    if _cmd_pub is None or _StringMsg is None:
        return None
    try:
        if _cmd_pub.get_subscription_count() == 0:
            return None  # 브릿지 미기동/미션 PC 오프라인 — 즉시 폴백
    except Exception:
        return None

    cmd_id = uuid.uuid4().hex
    entry: dict[str, Any] = {"event": threading.Event(), "result": None}
    with _pending_lock:
        _pending[cmd_id] = entry
    payload = json.dumps({
        "id": cmd_id,
        "ts": time.time(),
        "robot_id": robot_id,
        "target_state": target_state,
        "force": bool(force),
    })
    try:
        with _pub_lock:
            _cmd_pub.publish(_StringMsg(data=payload))
    except Exception:
        with _pending_lock:
            _pending.pop(cmd_id, None)
        return None

    ok = entry["event"].wait(timeout)
    with _pending_lock:
        _pending.pop(cmd_id, None)
    return entry["result"] if ok else None


def _fsm_thread() -> None:
    global _cmd_pub, _StringMsg
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String

        ctx = rclpy.Context()
        rclpy.init(context=ctx, domain_id=FSM_DOMAIN_ID)
        node = Node("fms_fsm_link", context=ctx)
        _StringMsg = String

        def on_state(msg: String) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            with _lock:
                robot_id = _apply_state_msg(_cache, payload)
            if robot_id:
                _notify(robot_id)

        def on_tree(msg: String) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            with _lock:
                robot_id = _apply_tree_msg(_cache, payload)
            if robot_id:
                _notify(robot_id)

        def on_result(msg: String) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            cmd_id = payload.get("id")
            if not cmd_id:
                return
            with _pending_lock:
                entry = _pending.get(cmd_id)
            if entry is None:
                return  # 늦게 도착한 결과 — 무시
            entry["result"] = {
                "accepted": bool(payload.get("accepted")),
                "current_state": payload.get("current_state", ""),
                "reason": payload.get("reason", ""),
            }
            entry["event"].set()

        node.create_subscription(String, STATE_TOPIC, on_state, 10)
        node.create_subscription(String, TREE_TOPIC, on_tree, 10)
        node.create_subscription(String, RESULT_TOPIC, on_result, 10)
        _cmd_pub = node.create_publisher(String, CMD_TOPIC, 10)

        print(f"[fsm_link] ROS 구독 시작 (domain {FSM_DOMAIN_ID}, state {STATE_TOPIC})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fsm_link] 비활성 — ROS2 링크 없이 진행합니다: {e}", flush=True)


def start() -> None:
    """백그라운드 ROS 스레드를 한 번만 띄운다 (fleet_telemetry.start() 와 동일 패턴)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_fsm_thread, name="fsm_link", daemon=True).start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_link.py -v`
Expected: `7 passed`

- [ ] **Step 5: Confirm it imports with ROS2 sourced too**

Run:
```bash
source /opt/ros/jazzy/setup.bash
cd aba_fms_service/backend && python3 -c "from app import fsm_link; print('FSM_DOMAIN_ID =', fsm_link.FSM_DOMAIN_ID)"
```
Expected: `FSM_DOMAIN_ID = 86`

- [ ] **Step 6: Git**

```bash
git add aba_fms_service/backend/app/fsm_link.py \
        aba_fms_service/backend/tests/test_fsm_link.py
git commit -m "feat(fms): add fsm_link ROS2 state cache and correlation-id transition channel"
```

---

### Task 6: `fsm.py` router — model, state, transition, history, WebSocket

**Files:**
- Create: `aba_fms_service/backend/app/routers/fsm.py`
- Modify: `aba_fms_service/backend/main.py` (line 9 import list; add one `include_router` line)
- Test: `aba_fms_service/backend/tests/test_fsm_router.py`

**Interfaces:**
- Consumes: `fsm_model` (Task 3), `fsm_audit` (Task 4), `fsm_link` (Task 5), `get_current_admin` from `app.deps`, `get_admin_db` from `app.database`.
- Produces (all under `prefix="/api/fsm"`):
  - `GET /api/fsm/model` → `fsm_model.as_dict()`. **The only place the frontend learns state names, edges, allowed targets, and the mermaid source.**
  - `GET /api/fsm/state?robot_id=` → the cached snapshot plus `allowed_targets` for the current state.
  - `POST /api/fsm/transition` → body `{robot_id, target_state, force}`; validates via `fsm_model.validate`, reports `task_cancelled` when leaving `WORKING`, dispatches via `fsm_link.request_transition`, records the audit row, returns `{accepted, current_state, reason}`.
  - `GET /api/fsm/history?robot_id=&limit=` → recent transitions.
  - `WS /api/fsm/ws/state?token=` → pushes `{"robot_id", "snapshot"}` frames. **No polling endpoint exists for live data** — `GET /api/fsm/state` is a one-shot initial read only.

- [ ] **Step 1: Write the failing tests**

```python
# aba_fms_service/backend/tests/test_fsm_router.py
"""라우터의 결정 로직 테스트.

DB/ROS 를 붙이지 않고, 순수 판정 헬퍼(_decide)를 직접 검증한다. FastAPI 엔드포인트는
_decide 를 감싸고 감사 로그와 링크 호출만 얹기 때문에 여기서 규칙이 다 잡힌다.
"""
import pytest

from app.routers.fsm import _decide, TransitionOutcome


def test_valid_edge_is_dispatched():
    outcome = _decide(current="CHARGING", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.should_dispatch is True
    assert outcome.needs_task_cancel is False


def test_invalid_edge_is_rejected_before_dispatch():
    """거부는 로봇에 요청조차 보내지 않는다."""
    outcome = _decide(current="CHARGING", target="WORKING", force=False, error_code=None)
    assert outcome.accepted is False
    assert outcome.should_dispatch is False
    assert "간선" in outcome.reason


def test_leaving_working_requires_task_cancel_report():
    """INSTRUCTION.md 안전 규칙: 'WORKING 이탈 — 진행 중인 태스크가 있으면
    FMS 에 task_cancelled 를 보고한 뒤 전이한다'."""
    outcome = _decide(current="WORKING", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.needs_task_cancel is True


def test_entering_working_does_not_need_task_cancel():
    outcome = _decide(current="IDLE", target="WORKING", force=False, error_code=None)
    assert outcome.needs_task_cancel is False


def test_error_entry_always_dispatches():
    outcome = _decide(current="WORKING", target="ERROR", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.should_dispatch is True
    # WORKING 에서 나가므로 태스크 취소 보고는 여전히 필요하다
    assert outcome.needs_task_cancel is True


def test_error_exit_without_code_is_rejected():
    outcome = _decide(current="ERROR", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is False
    assert "error_code" in outcome.reason


def test_error_exit_with_code_is_allowed():
    outcome = _decide(current="ERROR", target="IDLE", force=False, error_code="E_DOCK_FAIL")
    assert outcome.accepted is True


def test_unknown_current_state_is_rejected():
    outcome = _decide(current=None, target="IDLE", force=True, error_code=None)
    assert outcome.accepted is False
    assert outcome.should_dispatch is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_fsm_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.fsm'`

- [ ] **Step 3: Implement the router**

```python
# aba_fms_service/backend/app/routers/fsm.py
"""FSM + BT 관제 API — 상태 조회, 직접 전이, 감사 이력, 실시간 push.

INSTRUCTION.md 2단계. 폴링 API 를 만들지 않는다 — 실시간 갱신은 /ws/state 전용이고
GET /state 는 페이지 최초 진입 시 1회용이다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import fsm_audit, fsm_link, fsm_model
from app.database import get_admin_db
from app.deps import get_current_admin
from app.models import Admin
from app.security import decode_token

router = APIRouter(prefix="/api/fsm", tags=["fsm"])

HISTORY_DEFAULT_LIMIT = 20


class TransitionRequest(BaseModel):
    robot_id: str = Field(..., min_length=1, max_length=40)
    target_state: str = Field(..., min_length=1, max_length=24)
    force: bool = False


@dataclass
class TransitionOutcome:
    accepted: bool
    reason: str
    should_dispatch: bool
    needs_task_cancel: bool


def _decide(
    *, current: str | None, target: str, force: bool, error_code: str | None
) -> TransitionOutcome:
    """전이 요청을 판정한다 — 순수 함수라 DB/ROS 없이 테스트된다."""
    if not current:
        return TransitionOutcome(
            accepted=False,
            reason="로봇의 현재 상태를 알 수 없습니다 (FSM 링크 끊김).",
            should_dispatch=False,
            needs_task_cancel=False,
        )
    accepted, reason = fsm_model.validate(current, target, force, error_code)
    return TransitionOutcome(
        accepted=accepted,
        reason=reason,
        should_dispatch=accepted,
        # INSTRUCTION.md 안전 규칙: WORKING 에서 나갈 때는 FMS 에 task_cancelled 보고
        needs_task_cancel=accepted and current == "WORKING",
    )


@router.get("/model")
async def get_model(_: Admin = Depends(get_current_admin)):
    """상태·간선·허용 전이·mermaid 소스. 프론트엔드의 유일한 정의 원천."""
    return {"ok": True, "model": fsm_model.as_dict()}


@router.get("/state")
async def get_state(
    robot_id: str = Query(...),
    _: Admin = Depends(get_current_admin),
):
    """최초 진입용 1회 조회. 실시간 갱신은 /ws/state 를 쓸 것."""
    snap = fsm_link.snapshot(robot_id)
    if snap is None:
        return {
            "ok": False,
            "robot_id": robot_id,
            "snapshot": None,
            "allowed_targets": [],
            "reason": "해당 로봇의 FSM 상태를 아직 수신하지 못했습니다.",
        }
    return {
        "ok": True,
        "robot_id": robot_id,
        "snapshot": snap,
        "allowed_targets": fsm_model.allowed_targets(snap.get("current_state") or ""),
    }


@router.get("/history")
async def get_history(
    robot_id: str | None = Query(None),
    limit: int = Query(HISTORY_DEFAULT_LIMIT, ge=1, le=200),
    db: AsyncSession = Depends(get_admin_db),
    _: Admin = Depends(get_current_admin),
):
    rows = await fsm_audit.recent_transitions(db, robot_id=robot_id, limit=limit)
    return {
        "ok": True,
        "items": [
            {
                "id": r.id,
                "robot_id": r.robot_id,
                "from_state": r.from_state,
                "to_state": r.to_state,
                "forced": r.forced,
                "accepted": r.accepted,
                "reason": r.reason,
                "admin_username": r.admin_username,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


async def _report_task_cancelled(robot_id: str) -> None:
    """WORKING 이탈 시 FMS 에 task_cancelled 보고.

    보고하지 않으면 해당 태스크가 RMF 에 잔존하여 재배차되지 않는다
    (INSTRUCTION.md 6. WORKING).
    """
    await asyncio.to_thread(
        fsm_link.request_transition, robot_id, "__task_cancelled__", False, 1.0
    )


@router.post("/transition")
async def post_transition(
    body: TransitionRequest,
    db: AsyncSession = Depends(get_admin_db),
    admin: Admin = Depends(get_current_admin),
):
    snap = fsm_link.snapshot(body.robot_id) or {}
    current = snap.get("current_state")
    error_code = snap.get("error_code") or None

    outcome = _decide(
        current=current, target=body.target_state, force=body.force, error_code=error_code
    )

    result_state = current or ""
    reason = outcome.reason

    if outcome.should_dispatch:
        if outcome.needs_task_cancel:
            await _report_task_cancelled(body.robot_id)
        link_result = await asyncio.to_thread(
            fsm_link.request_transition, body.robot_id, body.target_state, body.force
        )
        if link_result is None:
            outcome = TransitionOutcome(
                accepted=False,
                reason="미션 PC FSM 링크에 연결할 수 없습니다 (브릿지 미기동 또는 오프라인).",
                should_dispatch=False,
                needs_task_cancel=False,
            )
            reason = outcome.reason
        else:
            outcome.accepted = link_result["accepted"]
            reason = link_result["reason"] or reason
            result_state = link_result["current_state"] or result_state

    await fsm_audit.record_transition(
        db,
        admin_id=admin.id,
        admin_username=admin.username,
        robot_id=body.robot_id,
        from_state=current or "UNKNOWN",
        to_state=body.target_state,
        forced=body.force,
        accepted=outcome.accepted,
        reason=reason,
    )

    return {"accepted": outcome.accepted, "current_state": result_state, "reason": reason}


@router.websocket("/ws/state")
async def fsm_state_ws(websocket: WebSocket, token: str = Query(...)):
    """상태/BT 스냅샷 실시간 push (폴링 아님)."""
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    fsm_link.add_listener(queue)
    try:
        # 접속 즉시 현재 캐시를 한 번 밀어준다 (빈 화면 방지)
        for robot_id, snap in fsm_link.all_snapshots().items():
            await websocket.send_json({"ok": True, "robot_id": robot_id, "snapshot": snap})
        while True:
            message = await queue.get()
            await websocket.send_json({"ok": True, **message})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        fsm_link.remove_listener(queue)
```

Note: `fsm_link._notify` runs on the ROS thread and calls `queue.put_nowait`, which is safe for `asyncio.Queue` only from the loop thread. Use `asyncio.Queue` with a bounded size and accept that a cross-thread `put_nowait` may race; if the ROS thread's writes need strict correctness, switch `_listeners` to hold `(loop, queue)` pairs and call `loop.call_soon_threadsafe(queue.put_nowait, message)`. **Implement the `call_soon_threadsafe` variant** — it is the correct form and costs two extra lines:

```python
# in app/fsm_link.py — replace add_listener/_notify with the loop-aware form
def add_listener(loop, queue) -> None:
    with _listeners_lock:
        _listeners.add((loop, queue))


def remove_listener(loop, queue) -> None:
    with _listeners_lock:
        _listeners.discard((loop, queue))


def _notify(robot_id: str) -> None:
    payload = snapshot(robot_id)
    if payload is None:
        return
    message = {"robot_id": robot_id, "snapshot": payload}
    with _listeners_lock:
        listeners = list(_listeners)
    for loop, queue in listeners:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except Exception:
            pass
```

and in the router:

```python
    loop = asyncio.get_running_loop()
    fsm_link.add_listener(loop, queue)
    ...
    finally:
        fsm_link.remove_listener(loop, queue)
```

- [ ] **Step 4: Register the router in `main.py`**

Modify the import on line 9 of `aba_fms_service/backend/main.py` to include `fsm` (keep alphabetical position, between `drive` and `human_follow_robot`):

```python
from app.routers import arm, aruco_dock, auth, camera, chat, dashboard, dev, drive, fsm, human_follow_robot, maps, marker_actions, mission_control, nav, pinky_yolo, robot, robot_learning, robots, ros, users, webrtc_robot
```

Add one line alongside the other `include_router` calls (after `app.include_router(drive.router)`):

```python
app.include_router(fsm.router)
```

Start `fsm_link` where the app already starts its other background workers — find the startup hook with `grep -n "startup\|fleet_telemetry.start\|pinky_greeting_monitor" aba_fms_service/backend/main.py` and add `fsm_link.start()` next to the existing `fleet_telemetry.start()` call, importing it with `from app import fsm_link` at the top.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/ -v`
Expected: all tests from Tasks 3–6 pass (`15 + 3 + 7 + 8 = 33 passed`). Recount against `python3 -m pytest --collect-only -q` if the number drifts.

- [ ] **Step 6: Verify the app still imports**

Run: `cd aba_fms_service/backend && python3 -c "import main; print('routes:', len(main.app.routes))"`
Expected: prints a route count without raising. If it fails on a missing DB/env, that is pre-existing — note it and instead run `python3 -c "from app.routers import fsm; print(fsm.router.prefix)"`, expecting `/api/fsm`.

- [ ] **Step 7: Inspect the live snapshot stream with the installed desktop viewer** (optional, needs `libi_modes` running)

```bash
source /opt/ros/jazzy/setup.bash
ros2 run py_trees_ros_viewer py-trees-tree-viewer
```
Expected: the tree appears when a `libi_modes` node is publishing. This is the debugging use of `py_trees_ros_viewer` referenced in Deliberate deviation 1 — it validates the snapshot content before the web renderer exists.

- [ ] **Step 8: Git**

```bash
git add aba_fms_service/backend/app/routers/fsm.py \
        aba_fms_service/backend/main.py \
        aba_fms_service/backend/tests/test_fsm_router.py
git commit -m "feat(fms): add /api/fsm router with model, state, transition, history and WS push"
```

---

### Task 7: Frontend API client + navigation entry

**Files:**
- Modify: `aba_fms_service/frontend/src/lib/admin-api.ts` (append types + helpers)
- Modify: `aba_fms_service/frontend/src/components/admin/AdminShell.tsx` (one import symbol, one nav item)

**Interfaces:**
- Produces:
  - `FsmModel`, `FsmSnapshot`, `FsmTransitionResult`, `FsmHistoryItem` TypeScript types — **structural mirrors of the API response only; they contain no state-name literals.**
  - `adminApi.fsmModel()`, `adminApi.fsmState(robotId)`, `adminApi.fsmTransition(input)`, `adminApi.fsmHistory(robotId, limit)`.
  - `adminApi.fsmStateWsUrl()` — mirrors the existing `fleetCoordinatorWsUrl()` shape (`centralWsUrl(path)` + `token` search param).
  - A nav item `{ to: "/admin/fsm", label: "FSM + BT", icon: GitBranch }` immediately after the `Waypoint` entry.

- [ ] **Step 1: Add types and client functions to `admin-api.ts`**

Append near the other WS helpers (after `fleetCoordinatorWsUrl`), matching the file's existing style:

```ts
// ── FSM + BT (2단계) ─────────────────────────────────────────────────────────
// 상태 이름과 간선은 절대 여기에 적지 않는다. 전부 GET /api/fsm/model 응답에서 온다
// (INSTRUCTION.md: "전이 박스와 화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다").

export interface FsmEdge {
  source: string;
  target: string;
  event: string;
  guard: string;
}

export interface FsmModel {
  states: string[];
  descriptions: Record<string, string>;
  edges: FsmEdge[];
  allowed_targets: Record<string, string[]>;
  mermaid: string;
}

export interface FsmTreeNode {
  name: string;
  status: "SUCCESS" | "FAILURE" | "RUNNING" | "INVALID";
  children: FsmTreeNode[];
}

export interface FsmSnapshot {
  current_state: string | null;
  previous_state: string | null;
  active_branch: string | null;
  error_code: string;
  battery_percent: number | null;
  is_docked: boolean | null;
  tree: FsmTreeNode | null;
  transitioned_at: number;
  stale: boolean;
}

export interface FsmStateResponse {
  ok: boolean;
  robot_id: string;
  snapshot: FsmSnapshot | null;
  allowed_targets: string[];
  reason?: string;
}

export interface FsmTransitionInput {
  robot_id: string;
  target_state: string;
  force: boolean;
}

export interface FsmTransitionResult {
  accepted: boolean;
  current_state: string;
  reason: string;
}

export interface FsmHistoryItem {
  id: number;
  robot_id: string;
  from_state: string;
  to_state: string;
  forced: boolean;
  accepted: boolean;
  reason: string;
  admin_username: string;
  created_at: string;
}

export function fsmStateWsUrl(): string {
  const url = centralWsUrl("/api/fsm/ws/state");
  url.searchParams.set("token", getToken() ?? "");
  return url.toString();
}
```

Then add the four request functions to the exported `adminApi` object, following exactly how neighbouring endpoints are written in this file (inspect with `grep -n "export const adminApi" -A 30 aba_fms_service/frontend/src/lib/admin-api.ts` and copy the surrounding call style — whether it uses a shared `request()` helper or raw `fetch`):

```ts
  fsmModel: () => request<{ ok: boolean; model: FsmModel }>("/api/fsm/model"),
  fsmState: (robotId: string) =>
    request<FsmStateResponse>(`/api/fsm/state?robot_id=${encodeURIComponent(robotId)}`),
  fsmTransition: (input: FsmTransitionInput) =>
    request<FsmTransitionResult>("/api/fsm/transition", { method: "POST", body: JSON.stringify(input) }),
  fsmHistory: (robotId: string, limit = 20) =>
    request<{ ok: boolean; items: FsmHistoryItem[] }>(
      `/api/fsm/history?robot_id=${encodeURIComponent(robotId)}&limit=${limit}`,
    ),
  fsmStateWsUrl,
```

- [ ] **Step 2: Add the nav entry**

In `aba_fms_service/frontend/src/components/admin/AdminShell.tsx`, add `GitBranch` to the `lucide-react` import block (it is alphabetically between `Crosshair` and `GitFork`):

```ts
  Crosshair,
  GitBranch,
  GitFork,
```

Then insert one line immediately **after** the `Waypoint` entry at line 115 (INSTRUCTION.md: "waypoint 칸 바로 아래"):

```ts
      { to: "/admin/waypoint", label: "Waypoint",       icon: Waypoints },
      { to: "/admin/fsm",      label: "FSM + BT",       icon: GitBranch },
    ],
```

- [ ] **Step 3: Verify lint and build**

Run:
```bash
cd aba_fms_service/frontend
npm run lint
npm run build
```
Expected: lint reports no new errors; build succeeds. (The route target `/admin/fsm` does not exist yet — TanStack's generated route tree may warn about an unknown `to`. If the build fails for that reason, do Task 8 first and re-run; note it in the task rather than suppressing the error.)

- [ ] **Step 4: Git**

```bash
git add aba_fms_service/frontend/src/lib/admin-api.ts \
        aba_fms_service/frontend/src/components/admin/AdminShell.tsx
git commit -m "feat(fms-web): add FSM+BT API client and nav entry below Waypoint"
```

---

### Task 8: `FsmBtPanel` component + route

**Files:**
- Create: `aba_fms_service/frontend/src/components/admin/FsmBtPanel.tsx`
- Create: `aba_fms_service/frontend/src/components/admin/FsmStateDiagram.tsx`
- Create: `aba_fms_service/frontend/src/components/admin/BtTreeView.tsx`
- Create: `aba_fms_service/frontend/src/routes/admin/_authed/fsm.tsx`

**Interfaces:**
- Consumes: `adminApi.fsm*` (Task 7), `useActiveRobotId`/`useActiveRobotType` from `@/lib/active-robot`.
- Produces: the page rendered at `/admin/fsm`. All three components take the model/snapshot as props and hold **no hardcoded state names**.

- [ ] **Step 1: `FsmStateDiagram.tsx`** — mermaid renderer with current-state and last-edge highlighting

```tsx
import mermaid from "mermaid";
import { useEffect, useMemo, useRef } from "react";

import type { FsmModel, FsmSnapshot } from "@/lib/admin-api";

mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "loose" });

interface Props {
  model: FsmModel;
  snapshot: FsmSnapshot | null;
}

/** 상태 다이어그램. 노드 목록과 간선은 전부 model 에서 오며 이 파일에 상태 이름을 적지 않는다. */
export function FsmStateDiagram({ model, snapshot }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  const source = useMemo(() => {
    const lines = [model.mermaid];
    const current = snapshot?.current_state;
    const previous = snapshot?.previous_state;

    // 현재 상태 강조, 나머지는 흐리게
    lines.push("    classDef current fill:#22c55e,stroke:#16a34a,stroke-width:3px,color:#052e16");
    lines.push("    classDef dimmed opacity:0.35");
    for (const state of model.states) {
      lines.push(`    class ${state} ${state === current ? "current" : "dimmed"}`);
    }
    // 직전 전이 간선 일시 강조 — EDGES 순서상 몇 번째인지 찾아 linkStyle 로 표시
    if (current && previous) {
      const index = model.edges.findIndex((e) => e.source === previous && e.target === current);
      if (index >= 0) {
        lines.push(`    linkStyle ${index} stroke:#f97316,stroke-width:4px`);
      }
    }
    return lines.join("\n");
  }, [model, snapshot?.current_state, snapshot?.previous_state]);

  useEffect(() => {
    let cancelled = false;
    const el = containerRef.current;
    if (!el) return;
    mermaid
      .render(`fsm-${Date.now()}`, source)
      .then(({ svg }) => {
        if (!cancelled && containerRef.current) containerRef.current.innerHTML = svg;
      })
      .catch(() => {
        if (!cancelled && containerRef.current) {
          containerRef.current.textContent = "상태 다이어그램을 렌더링하지 못했습니다.";
        }
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  return <div ref={containerRef} className="overflow-x-auto" />;
}
```

- [ ] **Step 2: `BtTreeView.tsx`** — behaviour-tree renderer with per-node status colours

```tsx
import type { FsmTreeNode } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

const STATUS_CLASS: Record<FsmTreeNode["status"], string> = {
  SUCCESS: "bg-emerald-500/20 text-emerald-300 border-emerald-500/50",
  FAILURE: "bg-rose-500/20 text-rose-300 border-rose-500/50",
  RUNNING: "bg-amber-500/25 text-amber-200 border-amber-400 ring-2 ring-amber-400/60",
  INVALID: "bg-zinc-500/15 text-zinc-400 border-zinc-600/50",
};

function TreeNode({ node, depth }: { node: FsmTreeNode; depth: number }) {
  return (
    <div style={{ marginLeft: depth * 16 }} className="py-0.5">
      <span
        className={cn(
          "inline-flex items-center gap-2 rounded border px-2 py-1 font-mono text-xs",
          STATUS_CLASS[node.status] ?? STATUS_CLASS.INVALID,
        )}
      >
        <span className="font-semibold">{node.name}</span>
        <span className="opacity-70">{node.status}</span>
      </span>
      {node.children?.map((child, i) => (
        <TreeNode key={`${child.name}-${i}`} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

/** 현재 상태에 대응하는 서브트리. RUNNING 노드는 링으로 한 번 더 강조한다. */
export function BtTreeView({ tree }: { tree: FsmTreeNode | null }) {
  if (!tree) {
    return <p className="text-sm text-muted-foreground">BT 스냅샷을 아직 수신하지 못했습니다.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <TreeNode node={tree} depth={0} />
    </div>
  );
}
```

- [ ] **Step 3: `FsmBtPanel.tsx`** — the composed panel

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { BtTreeView } from "@/components/admin/BtTreeView";
import { FsmStateDiagram } from "@/components/admin/FsmStateDiagram";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { adminApi, type FsmSnapshot } from "@/lib/admin-api";

interface Props {
  robotId: string | null;
}

export function FsmBtPanel({ robotId }: Props) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<string>("");
  const [force, setForce] = useState(false);
  const [snapshots, setSnapshots] = useState<Record<string, FsmSnapshot>>({});

  const modelQuery = useQuery({
    queryKey: ["fsm", "model"],
    queryFn: () => adminApi.fsmModel(),
    staleTime: Infinity, // 전이 박스는 배포 단위로만 바뀐다
  });

  const historyQuery = useQuery({
    queryKey: ["fsm", "history", robotId],
    queryFn: () => adminApi.fsmHistory(robotId!, 20),
    enabled: !!robotId,
  });

  // 실시간 갱신은 WebSocket push 전용 — 폴링하지 않는다.
  useEffect(() => {
    const ws = new WebSocket(adminApi.fsmStateWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.robot_id && payload.snapshot) {
          setSnapshots((prev) => ({ ...prev, [payload.robot_id]: payload.snapshot }));
        }
      } catch {
        /* malformed fsm frame */
      }
    };
    return () => ws.close();
  }, []);

  const model = modelQuery.data?.model ?? null;
  const snapshot = robotId ? snapshots[robotId] ?? null : null;
  const current = snapshot?.current_state ?? null;

  const allowed = useMemo(() => {
    if (!model || !current) return [];
    return model.allowed_targets[current] ?? [];
  }, [model, current]);

  const transition = useMutation({
    mutationFn: () =>
      adminApi.fsmTransition({ robot_id: robotId!, target_state: target, force }),
    onSuccess: (result) => {
      if (result.accepted) toast.success(`전이 수락: ${result.current_state}`);
      else toast.error(`전이 거부: ${result.reason}`);
      queryClient.invalidateQueries({ queryKey: ["fsm", "history", robotId] });
    },
    onError: () => toast.error("전이 요청을 보내지 못했습니다."),
  });

  function handleExecute() {
    if (!robotId || !target) return;
    const warning = force
      ? "\n\n⚠️ 강제 전이(force)가 켜져 있습니다. 전이 박스에 없는 간선도 실행됩니다."
      : "";
    if (!window.confirm(`'${current}' → '${target}' 전이를 실행할까요?${warning}`)) return;
    transition.mutate();
  }

  if (!robotId) return <p className="text-sm text-muted-foreground">로봇을 먼저 선택하세요.</p>;
  if (modelQuery.isLoading || !model) return <p className="text-sm">FSM 모델을 불러오는 중…</p>;

  return (
    <div className="space-y-4">
      {/* 제어 바 */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border p-4">
        <div className="text-sm">
          현재 상태{" "}
          <span className="rounded bg-emerald-500/20 px-2 py-1 font-mono font-semibold text-emerald-300">
            {current ?? "수신 대기"}
          </span>
          {snapshot?.stale && <span className="ml-2 text-xs text-amber-400">(수신 끊김)</span>}
          {snapshot?.error_code && (
            <span className="ml-2 font-mono text-xs text-rose-400">{snapshot.error_code}</span>
          )}
        </div>

        <Select value={target} onValueChange={setTarget}>
          <SelectTrigger className="w-56"><SelectValue placeholder="목표 상태 선택" /></SelectTrigger>
          <SelectContent>
            {model.states.map((state) => {
              const reachable = allowed.includes(state);
              return (
                <SelectItem key={state} value={state} disabled={!force && !reachable}>
                  {state}
                  {!reachable && <span className="ml-2 text-xs opacity-60">(도달 불가)</span>}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>

        <label className="flex items-center gap-2 text-sm">
          <Switch checked={force} onCheckedChange={setForce} />
          force
        </label>
        {force && (
          <span className="rounded bg-rose-500/20 px-2 py-1 text-xs font-semibold text-rose-300">
            ⚠️ 강제 전이 — 전이 박스에 없는 간선도 실행됩니다
          </span>
        )}

        <Button onClick={handleExecute} disabled={!target || transition.isPending}>
          전이 실행
        </Button>
      </div>

      {/* 상태 다이어그램 | BT 트리 — 한 화면에 나란히 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">상태 다이어그램</h3>
          <FsmStateDiagram model={model} snapshot={snapshot} />
        </section>
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">
            BT 트리{snapshot?.active_branch ? ` — ${snapshot.active_branch}` : ""}
          </h3>
          <BtTreeView tree={snapshot?.tree ?? null} />
        </section>
      </div>

      {/* 최근 전이 이력 */}
      <section className="rounded-lg border p-4">
        <h3 className="mb-3 text-sm font-semibold">최근 전이 이력</h3>
        <ul className="space-y-1 font-mono text-xs">
          {(historyQuery.data?.items ?? []).map((item) => (
            <li key={item.id} className={item.accepted ? "" : "opacity-60"}>
              <span className="text-muted-foreground">
                {new Date(item.created_at).toLocaleString()}
              </span>{" "}
              {item.from_state} → {item.to_state}
              {item.forced && <span className="ml-1 text-rose-400">[force]</span>}
              {!item.accepted && <span className="ml-1 text-amber-400">[거부]</span>}
              <span className="ml-2 text-muted-foreground">{item.admin_username}</span>
              {item.reason && <span className="ml-2 opacity-70">{item.reason}</span>}
            </li>
          ))}
          {(historyQuery.data?.items ?? []).length === 0 && (
            <li className="text-muted-foreground">기록이 없습니다.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
```

Before writing this file, confirm the shadcn primitives it imports actually exist: `ls aba_fms_service/frontend/src/components/ui/ | grep -E "button|select|switch"`. If `switch.tsx` is absent, add it with the project's existing shadcn workflow (check how other components were added) or substitute a plain checkbox input — do not invent a component path.

- [ ] **Step 4: The route file**

```tsx
// aba_fms_service/frontend/src/routes/admin/_authed/fsm.tsx
import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { FsmBtPanel } from "@/components/admin/FsmBtPanel";
import { useActiveRobotId, useActiveRobotType } from "@/lib/active-robot";

export const Route = createFileRoute("/admin/_authed/fsm")({ component: FsmPage });

function FsmPage() {
  const robotId = useActiveRobotId();
  const robotType = useActiveRobotType();
  const canControl = robotType === "pinky" && robotId != null;

  return (
    <AdminShell title="FSM + BT">
      {canControl ? (
        <FsmBtPanel robotId={String(robotId)} />
      ) : (
        <p className="text-sm text-muted-foreground">주행 로봇(pinky)을 선택하세요.</p>
      )}
    </AdminShell>
  );
}
```

- [ ] **Step 5: Verify lint and build**

Run:
```bash
cd aba_fms_service/frontend
npm run lint
npm run build
```
Expected: both succeed. TanStack's route generator should now resolve `/admin/fsm`.

- [ ] **Step 6: Manual browser check** (honest note: there is no frontend unit-test runner in this repo, so these are eyeball checks)

Run `npm run dev`, log in, open `/admin/fsm`, and confirm:
- [ ] `FSM + BT` appears in 「로봇 제어」 directly below `Waypoint`
- [ ] The state diagram and BT tree render side by side on a wide screen and stack on narrow
- [ ] With no robot data, the page shows "수신 대기" rather than crashing
- [ ] Toggling `force` shows the red warning and un-disables previously disabled options
- [ ] Clicking 전이 실행 opens a confirm dialog before any request is sent

- [ ] **Step 7: Git**

```bash
git add aba_fms_service/frontend/src/components/admin/FsmBtPanel.tsx \
        aba_fms_service/frontend/src/components/admin/FsmStateDiagram.tsx \
        aba_fms_service/frontend/src/components/admin/BtTreeView.tsx \
        aba_fms_service/frontend/src/routes/admin/_authed/fsm.tsx
git commit -m "feat(fms-web): add FSM+BT panel with state diagram, BT tree and transition control"
```

---

### Task 9: Definition-drift guard

**Files:**
- Test: `aba_fms_service/backend/tests/test_no_frontend_state_literals.py`

**Interfaces:**
- Consumes: `fsm_model.STATES` (Task 3) and the `.tsx` files from Task 8.
- Produces: a failing test the moment someone hand-copies a state name into the frontend, which is exactly the drift INSTRUCTION.md's "별도 매핑 테이블을 만들지 않는다" rule exists to prevent.

- [ ] **Step 1: Write the test**

```python
# aba_fms_service/backend/tests/test_no_frontend_state_literals.py
"""프론트엔드에 상태 이름이 하드코딩되지 않았는지 감시한다.

INSTRUCTION.md: "상태 다이어그램은 1단계 전이 박스를 기준으로 생성하며, 전이 박스와
화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다."
상태 목록을 .tsx 에 복사해두면 전이 박스만 바뀌었을 때 조용히 어긋난다.
"""
from pathlib import Path

import pytest

from app.fsm_model import STATES

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

WATCHED = [
    "components/admin/FsmBtPanel.tsx",
    "components/admin/FsmStateDiagram.tsx",
    "components/admin/BtTreeView.tsx",
    "routes/admin/_authed/fsm.tsx",
    "lib/admin-api.ts",
]


@pytest.mark.parametrize("relative", WATCHED)
def test_frontend_file_has_no_state_name_literals(relative):
    path = FRONTEND / relative
    if not path.exists():
        pytest.skip(f"{relative} 아직 없음 (Task 7/8 미완료)")
    source = path.read_text(encoding="utf-8")
    for state in STATES:
        assert f'"{state}"' not in source, (
            f"{relative} 에 상태 이름 '{state}' 이 하드코딩되어 있습니다. "
            f"GET /api/fsm/model 응답에서 읽어 쓰세요."
        )
        assert f"'{state}'" not in source, (
            f"{relative} 에 상태 이름 '{state}' 이 하드코딩되어 있습니다."
        )


def test_branch_order_matches_libi_modes_registry():
    """libi_modes.registry.BRANCH_ORDER 와 어긋나면 '상태 -> 서브트리' 선택이 깨진다.

    libi_modes 는 별도 워크스페이스라 import 할 수 없으므로, registry.py 원문을 읽어 비교한다.
    """
    registry = (
        Path(__file__).resolve().parents[4]
        / "aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py"
    )
    if not registry.exists():
        pytest.skip("libi_modes 패키지 아직 없음")
    source = registry.read_text(encoding="utf-8")
    for state in STATES:
        assert f'"{state}"' in source, f"registry.py 에 {state} 가 없습니다"
```

Note the `BtTreeView.tsx` entry: that file legitimately contains `"SUCCESS"`, `"FAILURE"`, `"RUNNING"`, `"INVALID"` — those are **py_trees node statuses**, not FSM state names, and none of them collide with `STATES`, so the test passes. If a future FSM state were ever named e.g. `RUNNING` this test would correctly force a rethink.

- [ ] **Step 2: Run the test**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_no_frontend_state_literals.py -v`
Expected: all parametrised cases pass (or skip if the frontend files are not yet written).

- [ ] **Step 3: Run the whole backend suite**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/ -v`
Expected: every test from Tasks 3, 4, 5, 6, 9 passes. Record the actual count from the run rather than trusting a number written here.

- [ ] **Step 4: Git**

```bash
git add aba_fms_service/backend/tests/test_no_frontend_state_literals.py
git commit -m "test(fms): guard against FSM state names being hardcoded in the frontend"
```

---

### Task 10: `domain_bridge` config for the FSM topics

**Files:**
- Create: `aba_fms_service/config/domain_bridge_libi_modes.yaml`
- Create: `aba_fms_service/docs/fsm-panel-operations.md`

**Interfaces:**
- Consumes: the topic names from `app/fsm_link.py` (Task 5) and the transport decision (Task 1).
- Produces: the bridge config relaying mission-PC FSM topics into domain 86 and relaying transition requests back.

**⚠️ This task cannot be completed until the mission PC's `ROS_DOMAIN_ID` is decided.** The file below uses the placeholder `__MISSION_DOMAIN__`, which must be replaced with the real number. Per project `CLAUDE.md` ("로봇 IP·ROS_DOMAIN_ID·포트 번호는 실제 하드웨어 배선과 직결되므로, README에 근거 없이 임의로 바꾸지 않는다"), **do not invent a value** — ask the user.

- [ ] **Step 1: Write the bridge config**

```yaml
# 미션 PC(libi_modes, ROS_DOMAIN_ID=__MISSION_DOMAIN__) <-> 서버 도메인 86 중계.
# 상태/BT 스냅샷은 미션PC->86, 전이 요청은 86->미션PC(reversed), 결과는 미션PC->86.
# 서버에서만 실행: ros2 run domain_bridge domain_bridge <이 파일>
#
# 서비스가 아니라 토픽인 이유: domain_bridge 의 YAML 스키마는 topics 만 지원하고
# 서비스 브릿지는 C++ 컴파일타임 템플릿 API 로만 존재한다.
# 근거: aba_fms_service/docs/fsm-transport-decision.md
name: bridge_libi_modes
from_domain: __MISSION_DOMAIN__
to_domain: 86
topics:
  libi/fsm_state:
    type: std_msgs/msg/String
  libi/bt_snapshot:
    type: std_msgs/msg/String
  libi/fsm_transition_result:
    type: std_msgs/msg/String
  # 역방향 명령: 86 의 /libi/fsm_transition_request 구독 -> 미션PC 도메인에 발행
  libi/fsm_transition_request:
    type: std_msgs/msg/String
    reversed: True
```

- [ ] **Step 2: Validate the YAML parses** (with a temporary stand-in domain, so the real number stays undecided)

```bash
source /opt/ros/jazzy/setup.bash
sed 's/__MISSION_DOMAIN__/95/' aba_fms_service/config/domain_bridge_libi_modes.yaml > /tmp/bridge_probe.yaml
timeout 8 ros2 run domain_bridge domain_bridge /tmp/bridge_probe.yaml
```
Expected: the bridge starts without a YAML parse error and stays running until the timeout kills it. A `YamlParsingError` means a key is wrong — fix it here, not on the robot.

- [ ] **Step 3: Round-trip the bridge locally** (proves the transport before any hardware exists)

Terminal A:
```bash
source /opt/ros/jazzy/setup.bash
sed 's/__MISSION_DOMAIN__/95/' aba_fms_service/config/domain_bridge_libi_modes.yaml > /tmp/bridge_probe.yaml
ros2 run domain_bridge domain_bridge /tmp/bridge_probe.yaml
```
Terminal B (pretend mission PC, domain 95):
```bash
source /opt/ros/jazzy/setup.bash
ROS_DOMAIN_ID=95 ros2 topic pub /libi/fsm_state std_msgs/msg/String \
  '{data: "{\"robot_id\":\"pinky1\",\"current_state\":\"PATROL\",\"active_branch\":\"PATROL\"}"}' -r 1
```
Terminal C (pretend FMS, domain 86):
```bash
source /opt/ros/jazzy/setup.bash
ROS_DOMAIN_ID=86 ros2 topic echo /libi/fsm_state
```
Expected: Terminal C prints the JSON published in Terminal B. Then reverse the direction to prove `reversed: True` works:
```bash
# Terminal C
ROS_DOMAIN_ID=86 ros2 topic pub /libi/fsm_transition_request std_msgs/msg/String '{data: "{\"id\":\"abc\"}"}' -r 1
# Terminal B
ROS_DOMAIN_ID=95 ros2 topic echo /libi/fsm_transition_request
```
Expected: Terminal B receives it. **If either direction fails, the panel cannot work — stop and diagnose before proceeding.**

- [ ] **Step 4: Write the operations note**

Create `aba_fms_service/docs/fsm-panel-operations.md` covering: how to start the bridge, the env vars `fsm_link` reads (`LIBI_FSM_DOMAIN_ID`, `LIBI_FSM_STATE_TOPIC`, `LIBI_FSM_TREE_TOPIC`, `LIBI_FSM_CMD_TOPIC`, `LIBI_FSM_RESULT_TOPIC`), how to confirm the link is alive (`ros2 topic hz /libi/fsm_state` on domain 86), what "수신 끊김" in the UI means, and the fact that the FMS **production** server must have ROS2 Jazzy installed because `requirements.txt` deliberately has no `rclpy`.

- [ ] **Step 5: Git**

```bash
git add aba_fms_service/config/domain_bridge_libi_modes.yaml \
        aba_fms_service/docs/fsm-panel-operations.md
git commit -m "feat(fms): add libi_modes domain bridge config and FSM panel operations doc"
```

---

## Requirement coverage

| # | INSTRUCTION.md 2단계 요구사항 | Task |
|---|---|---|
| 1 | 대상 로봇 선택 | 8 (route uses `useActiveRobotId`, the existing global robot selector) |
| 2 | 현재 상태 실시간 표시 | 5 (cache) · 6 (WS) · 8 (render) |
| 3 | 목표 상태 선택 (8종) | 3 (`STATES`) · 8 (Select) |
| 4 | 전이 실행 | 6 (`POST /transition`) · 8 (button) |
| 5 | 전이 결과 표시 (성공/거부 사유) | 6 (`reason`) · 8 (toast) |
| 6 | 상태 다이어그램 + 현재 상태 하이라이트 + 직전 전이 간선 강조 | 3 (`to_mermaid`) · 5 (`previous_state`) · 8 (`FsmStateDiagram`) |
| 7 | BT 시각화 + 실행 노드 하이라이트 + 4색 상태 구분 | 5 (`_apply_tree_msg`) · 8 (`BtTreeView`) |
| 8 | 상태도와 BT 한 화면 나란히 | 8 (`lg:grid-cols-2`) |
| 9 | 유효 전이만 활성화 / 도달 불가 비활성화 | 3 (`allowed_targets`) · 8 (`disabled`) |
| 10 | `force` 토글 분리·기본 꺼짐·경고 | 3 (`validate` force 분기) · 8 (Switch + 경고) |
| 11 | 전이 전 확인 대화상자 | 8 (`window.confirm`) |
| 12 | WORKING 이탈 시 `task_cancelled` 보고 | 6 (`needs_task_cancel` → `_report_task_cancelled`) |
| 13 | ERROR 진입 항상 허용 / 이탈은 `error_code` 후에만 | 3 (`validate`) · 6 (`_decide`) |
| 14 | 강제 전이 감사 로그 | 4 (`FsmTransitionLog`) · 6 (record on every attempt) |
| 15 | 최근 전이 이력 N건 | 4 (`recent_transitions`) · 6 (`GET /history`) · 8 (list) |
| 16 | 폴링 아닌 push | 5 (`_notify`) · 6 (`/ws/state`) · 8 (`new WebSocket`) |
| 17 | 전이 요청 ROS2 서비스 (`robot_id`/`target_state`/`force` → `accepted`/`current_state`/`reason`) | 2 (`.srv` 계약) · 5 (cross-domain transport) — **see Deliberate deviation 2** |
| 18 | py_trees 스냅샷 → 기존 WS 경로 push, 별도 폴링 API 금지 | 5 (subscribe) · 6 (WS only; `GET /state` is one-shot) |

Additional coverage not enumerated in INSTRUCTION.md but required by it implicitly: the single-source-of-truth rule ("별도 매핑 테이블을 만들지 않는다") is enforced by Task 9's drift guard.

---

## Deferred / open decisions

1. **Mission PC `ROS_DOMAIN_ID`** — blocks Task 10 Step 1 from being finalised (the file ships with `__MISSION_DOMAIN__`). Task 10's local round-trip test uses a throwaway `95` purely to validate the YAML and the bridge mechanics; it is not a proposal for the real value.
2. **Does the FMS *production* server have ROS2 Jazzy?** The dev machine does (verified). `backend/requirements.txt` deliberately has no `rclpy` because `fleet_telemetry.py` relies on the system `/opt/ros` install — so the same assumption now extends to `fsm_link.py`. Confirm on the deployment host; if absent, `fsm_link` degrades to "링크 없음" and the panel shows 수신 대기 forever.
3. **`task_cancelled` reporting path.** Task 6 sends it through the same transition channel with a sentinel `__task_cancelled__` target. That is a placeholder shape — the real contract belongs to whatever owns RMF task state, which is out of scope here. Confirm the intended API before relying on it in production, otherwise a cancelled task may linger in RMF exactly as INSTRUCTION.md warns.
4. **No frontend test runner.** `aba_fms_service/frontend` has no vitest/jest (verified: `package.json` scripts are only dev/build/build:dev/preview/lint/format). Task 8's verification is `lint` + `build` + manual browser checks. Adding vitest would touch another team's SPA config and needs the user's approval.
5. **BT snapshot payload shape.** Task 5 assumes `libi_modes` publishes `{"robot_id", "tree": {name, status, children}}` as a JSON `std_msgs/String`. `libi_modes`' `main.py` is itself deferred in `2026-07-20-libi-modes-fsm-bt.md`, so this contract is currently defined only by this plan — the two must be reconciled when `main.py` is written.
6. **Robot id format.** `fleet_telemetry.py` keys its cache by **IP address** and maps robot names (`Pinky-1`) to bridge keys (`pinky1`) via `_ROBOT_NAME_TO_KEY`. This plan keys by a `robot_id` string and the route passes `String(useActiveRobotId())`, which is a numeric DB id. **These are three different identifier spaces** — decide on one before Task 8 goes to production, or the panel will look up snapshots under a key nothing publishes.
