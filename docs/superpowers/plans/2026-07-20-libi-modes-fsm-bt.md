# libi_modes FSM+BT Core Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `libi_modes` ROS2 package — an 8-state mission FSM implemented entirely as a py_trees behaviour tree (no separate FSM library), running on a dedicated mission PC, that owns `current_mode`/`next_mode` transition logic for one LIBI unit (AMR + arm).

**Architecture:** One `Parallel` root ticks two children every cycle: `Topics2BB` (sensor/command inputs → blackboard) and `Priorities` (a `Selector` of 8 state branches ordered by priority, each guarded by `IsMode(state)`, each branch decides `next_mode` and hands off to a shared `RequestTransition()` leaf). A trailing `Running()` keeps the tree alive on ticks where no branch's guard matches (should not normally happen since exactly one `IsMode` succeeds).

**Tech Stack:** Python 3, ROS2 Jazzy (ament_python package), `py_trees` 2.x (confirmed via `pip show py_trees` → 2.4.0 in this repo's environment; apt equivalents `ros-jazzy-py-trees*` for the mission PC), `pytest`.

## Global Constraints

- All 18 transitions in the transition box (source: `/home/asd/Downloads/INSTRUCTION.md`) must be implemented via py_trees only — no `transitions`/SMACH/YASMIN library (per INSTRUCTION.md 구현 방침, confirmed with user in chat).
- No hardcoded thresholds: `BATTERY_READY=40`, `BATTERY_CHARGED=80`, `BATTERY_LOW=15` must come from `config/params.yaml`, injected at tree-build time (INSTRUCTION.md 구현 규칙 표).
- Priority `Selector` nodes: `memory=False`. Action `Sequence` nodes: `memory=True` (INSTRUCTION.md 구현 규칙 표).
- `RequestTransition()` is always the last child of a branch's root `Sequence`, never inside a `Parallel` (INSTRUCTION.md 주의).
- `ERROR` is the only branch without a `FaultDetected` leaf (INSTRUCTION.md 8. ERROR, "공통 규약의 유일한 예외").
- Package name: `libi_modes`, located at `aba_controller/libi_modes/ros_ws/src/libi_modes/` (agreed in chat: sibling top-level component to `libi_drive_controller`/`libi_gui`/`libi_handy_controller`, own colcon workspace since it runs on a separate mission PC).
- Out of scope for this plan (deferred, per INSTRUCTION.md and chat): the `ArmExec` internal pick/place subtree ("작성 예정" in INSTRUCTION.md — 8 branches must be designed first), real `rclpy` topic/service wiring in `main.py` (blocked on the mission-PC ROS domain + `domain_bridge` decision, still open in chat), and anything in Stage 2 (FMS UI panel) or Stage 3 (LED package) — those are separate plans (`2026-07-20-fms-fsm-bt-panel.md`, `2026-07-20-libi-led-state.md`).
- This plan is fully testable today with plain `pytest` + pip's `py_trees` (verified: `import py_trees` works in this repo's Python env) — no ROS2 install required for Tasks 1–12. `colcon build` (Task 2) cannot be verified in this sandbox (no `/opt/ros/jazzy` sourced here) and is flagged as "verify on mission PC" rather than claimed as tested.

---

### Task 1: FSM design doc (`README.md`)

**Files:**
- Create: `aba_controller/libi_modes/README.md`

**Interfaces:** None (doc only).

- [ ] **Step 1: Write the design doc**

Create `aba_controller/libi_modes/README.md` containing, verbatim from `/home/asd/Downloads/INSTRUCTION.md`:
1. The transition box (원문 그대로, no edges added/removed) under `## 전이 박스`
2. The 8-state definition table under `## 상태 목록`
3. `## IDLE 이탈 조건` (3 conditions + 배차 우선순위 표 + 배터리 임계값 표 + `docked` 가드 주의문)
4. `## 구현 방침 — 모든 전이를 py_trees로 구성` (apt install block, tree structure ASCII, state↔branch table, 구현 규칙 표, 주의 항목)
5. `## 의도적으로 두지 않은 간선` (표 + 야간 동작 루프)
6. `# 4단계 — BT` section: 패키지 구조, 브랜치 공통 규약, 공통 노드 표, 이탈 조건 leaf 규약, 브랜치 공통 골격, 이탈 조건 우선순위, fault 검사 배치, 트리 죽음 방지, and all 8 `## 브랜치 설계` subsections exactly as designed in INSTRUCTION.md
7. Keep `## 로봇팔 서브트리 (ArmExec 내부)` as `> **작성 예정.**` (INSTRUCTION.md marks this explicitly pending — do not invent content for it)
8. Note at the top of the package-structure section that the actual package name is `libi_modes` (not `libi_bt` as in the original instruction draft) — this was clarified in chat: `libi_bt` was only the working name for the BT-detail sub-design, the shipped package follows the `gogoping_modes`-style `<project>_modes` convention.

- [ ] **Step 2: Verify required sections are present**

Run: `grep -c '^## \|^# ' aba_controller/libi_modes/README.md`
Expected: at least 14 (1단계 5 sections + 4단계 intro sections + 8 브랜치 설계 subsections, headers vary — eyeball the count is non-zero and every section name above appears via `grep -n "전이 박스\|상태 목록\|IDLE 이탈\|의도적으로 두지 않은\|브랜치 설계" aba_controller/libi_modes/README.md`).

- [ ] **Step 3: Git**

```bash
git add aba_controller/libi_modes/README.md
git commit -m "docs: add libi_modes FSM+BT design (transition box, 8 states, branch designs)"
```

---

### Task 2: ROS2 package skeleton

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/package.xml`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/setup.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/setup.cfg`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/resource/libi_modes`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/__init__.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/__init__.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/__init__.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/__init__.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/config/params.yaml`

**Interfaces:** None yet — pure scaffolding. Later tasks import from `libi_modes.common.*`, `libi_modes.branches.*`, `libi_modes.blackboard`, `libi_modes.registry`, `libi_modes.tree`.

- [ ] **Step 1: package.xml** (ament_python, mirrors `gogoping_modes/package.xml` pattern found in the reference project)

```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>libi_modes</name>
  <version>0.1.0</version>
  <description>LIBI 미션 FSM+BT — py_trees 하나로 8개 상태 전이와 상태별 동작을 구현한다.</description>
  <maintainer email="dev@aba-project.local">aba</maintainer>
  <license>Proprietary</license>

  <exec_depend>rclpy</exec_depend>
  <exec_depend>std_msgs</exec_depend>
  <exec_depend>python3-py-trees</exec_depend>
  <exec_depend>python3-yaml</exec_depend>

  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

- [ ] **Step 2: setup.py**

```python
from setuptools import find_packages, setup

package_name = 'libi_modes'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/params.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='aba',
    maintainer_email='dev@aba-project.local',
    description='LIBI 미션 FSM+BT (py_trees)',
    license='Proprietary',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [],
    },
)
```

- [ ] **Step 3: setup.cfg**

```ini
[develop]
script_dir=$base/lib/libi_modes
[install]
install_scripts=$base/lib/libi_modes
```

- [ ] **Step 4: resource marker + empty `__init__.py` files**

```bash
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_modes/resource
touch aba_controller/libi_modes/ros_ws/src/libi_modes/resource/libi_modes
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_modes/test
touch aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/__init__.py
touch aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/__init__.py
touch aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/__init__.py
touch aba_controller/libi_modes/ros_ws/src/libi_modes/test/__init__.py
```

- [ ] **Step 5: `config/params.yaml`** (thresholds — INSTRUCTION.md 구현 규칙: "임계값 하드코딩 금지, params.yaml에서 주입")

```yaml
libi_modes:
  battery:
    ready: 40        # CHARGING -> IDLE
    charged: 80       # IDLE -> PATROL (auto, docked only)
    low: 15           # -> RETURNING
  interacting:
    ui_idle_timeout_sec: 20
  working:
    command_timeout_sec: 120
  returning:
    dock_retry_max: 3
  security_patrol:
    idle_interval_sec: 900   # 15분 야간 루프 — Topics2BB/외부 스케줄러가 채우는 값, 이 패키지가 타이머 자체를 소유하지 않음
```

- [ ] **Step 6: Verify the package structure is well-formed**

Run: `find aba_controller/libi_modes/ros_ws/src/libi_modes -type f | sort`
Expected: lists `package.xml`, `setup.py`, `setup.cfg`, `resource/libi_modes`, `config/params.yaml`, and the four `__init__.py` files created above.

Note: `colcon build` itself cannot be verified in this sandbox (no ROS2 sourced here — confirmed via `echo $ROS_DISTRO` returning empty and `which ros2` finding nothing). Verify on the mission PC after `sudo apt install ros-jazzy-py-trees ros-jazzy-py-trees-ros-interfaces ros-jazzy-py-trees-ros ros-jazzy-py-trees-ros-viewer` (Task 6 of `2026-07-20-libi-led-state.md` covers a similar first-build check pattern).

- [ ] **Step 7: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/package.xml \
        aba_controller/libi_modes/ros_ws/src/libi_modes/setup.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/setup.cfg \
        aba_controller/libi_modes/ros_ws/src/libi_modes/resource/libi_modes \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/__init__.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/__init__.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/__init__.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/__init__.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/config/params.yaml
git commit -m "feat: scaffold libi_modes ament_python package"
```

---

### Task 3: Blackboard key constants

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/blackboard.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_blackboard.py`

**Interfaces:**
- Produces: `Keys` class with string attributes `CURRENT_MODE`, `NEXT_MODE`, `FAULT`, `BATTERY_PERCENT`, `IS_DOCKED`, `LAST_COMMAND`, `UI_LAST_TOUCH_AT`, `DRIVE_LOCK`, `ARM_LOCK`, `ACTIVE_COMMAND`, `COMMAND_RECEIVED_AT`, `DOCK_RETRY_COUNT`, `ERROR_CODE`. All later tasks import `from libi_modes.blackboard import Keys` and use `Keys.X` instead of raw strings (mirrors `gogoping_modes/gogoping_modes/bt/blackboard.py`'s `Keys.DESTINATION_KEY` pattern seen in the reference project).

- [ ] **Step 1: Write the failing test**

```python
# test/test_blackboard.py
from libi_modes.blackboard import Keys


def test_keys_are_unique_strings():
    values = [v for k, v in vars(Keys).items() if not k.startswith('_')]
    assert len(values) == len(set(values)), "duplicate blackboard key values"
    assert all(isinstance(v, str) for v in values)


def test_expected_keys_present():
    expected = {
        'CURRENT_MODE', 'NEXT_MODE', 'FAULT', 'BATTERY_PERCENT', 'IS_DOCKED',
        'LAST_COMMAND', 'UI_LAST_TOUCH_AT', 'DRIVE_LOCK', 'ARM_LOCK',
        'ACTIVE_COMMAND', 'COMMAND_RECEIVED_AT', 'DOCK_RETRY_COUNT', 'ERROR_CODE',
    }
    assert expected.issubset(set(vars(Keys).keys()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_modes && python3 -m pytest test/test_blackboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.blackboard'`

- [ ] **Step 3: Implement**

```python
# libi_modes/blackboard.py
"""Blackboard key name constants shared by every common node and branch."""


class Keys:
    CURRENT_MODE = "current_mode"
    NEXT_MODE = "next_mode"
    FAULT = "fault"
    BATTERY_PERCENT = "battery_percent"
    IS_DOCKED = "is_docked"
    LAST_COMMAND = "last_command"
    UI_LAST_TOUCH_AT = "ui_last_touch_at"
    DRIVE_LOCK = "drive_lock"
    ARM_LOCK = "arm_lock"
    ACTIVE_COMMAND = "active_command"
    COMMAND_RECEIVED_AT = "command_received_at"
    DOCK_RETRY_COUNT = "dock_retry_count"
    ERROR_CODE = "error_code"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_blackboard.py -v`
Expected: `2 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/blackboard.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_blackboard.py
git commit -m "feat: add libi_modes blackboard key constants"
```

---

### Task 4: Common leaf nodes (`IsMode`, `RequestTransition`, `FaultDetected`, `BatteryCheck`, `CommandListener`)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/is_mode.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/request_transition.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/fault_detected.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/battery_check.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/command_listener.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_common_leaves.py`

**Interfaces:**
- Consumes: `Keys` from Task 3.
- Produces:
  - `IsMode(mode: str, name: str | None = None)` — SUCCESS iff `blackboard.current_mode == mode`.
  - `RequestTransition(name: str | None = None)` — reads `next_mode`; if truthy, sets `current_mode = next_mode`, clears `next_mode = None`, returns SUCCESS; else FAILURE.
  - `FaultDetected(name: str | None = None)` — SUCCESS + `next_mode = "ERROR"` if `blackboard.fault` truthy, else FAILURE.
  - `BatteryCheck(op: str, threshold: float, next_mode: str, require_docked: bool | None = None, name: str | None = None)` — `op` is `"<="` or `">="`. SUCCESS + writes `next_mode` if the comparison holds AND (`require_docked is None` or `blackboard.is_docked == require_docked`); else FAILURE.
  - `CommandListener(mapping: dict[str, str], name: str | None = None)` — reads `blackboard.last_command`; if it's a key in `mapping`, writes `next_mode = mapping[cmd]`, clears `last_command = None`, returns SUCCESS; else FAILURE.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_common_leaves.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener


def _writer():
    bb = py_trees.blackboard.Client(name="test-writer")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.BATTERY_PERCENT,
                Keys.IS_DOCKED, Keys.LAST_COMMAND):
        bb.register_key(key=key, access=Access.WRITE)
    return bb


def _tick(node):
    tree = py_trees.trees.BehaviourTree(root=node)
    tree.setup(timeout=15)
    tree.tick()
    return node.status


def test_is_mode_success_and_failure():
    bb = _writer()
    bb.set(Keys.CURRENT_MODE, "IDLE")
    assert _tick(IsMode("IDLE")) == Status.SUCCESS
    assert _tick(IsMode("WORKING")) == Status.FAILURE


def test_request_transition_consumes_next_mode():
    bb = _writer()
    bb.set(Keys.NEXT_MODE, "PATROL")
    node = RequestTransition()
    assert _tick(node) == Status.SUCCESS
    reader = py_trees.blackboard.Client(name="test-reader")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    reader.register_key(key=Keys.NEXT_MODE, access=Access.READ)
    assert reader.get(Keys.CURRENT_MODE) == "PATROL"
    assert reader.get(Keys.NEXT_MODE) is None


def test_request_transition_fails_without_next_mode():
    _writer()  # next_mode left unset (None default not registered -> use explicit None)
    bb = py_trees.blackboard.Client(name="test-writer-2")
    bb.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
    bb.set(Keys.NEXT_MODE, None)
    assert _tick(RequestTransition()) == Status.FAILURE


def test_fault_detected():
    bb = _writer()
    bb.set(Keys.FAULT, True)
    assert _tick(FaultDetected()) == Status.SUCCESS
    reader = py_trees.blackboard.Client(name="test-reader-2")
    reader.register_key(key=Keys.NEXT_MODE, access=Access.READ)
    assert reader.get(Keys.NEXT_MODE) == "ERROR"

    bb.set(Keys.FAULT, False)
    assert _tick(FaultDetected()) == Status.FAILURE


def test_battery_check_threshold_and_dock_guard():
    bb = _writer()
    bb.set(Keys.BATTERY_PERCENT, 85.0)
    bb.set(Keys.IS_DOCKED, True)
    assert _tick(BatteryCheck(">=", 80, "PATROL", require_docked=True)) == Status.SUCCESS

    bb.set(Keys.IS_DOCKED, False)
    assert _tick(BatteryCheck(">=", 80, "PATROL", require_docked=True)) == Status.FAILURE

    bb.set(Keys.BATTERY_PERCENT, 10.0)
    assert _tick(BatteryCheck("<=", 15, "RETURNING", require_docked=False)) == Status.SUCCESS


def test_command_listener_maps_and_consumes():
    bb = _writer()
    bb.set(Keys.LAST_COMMAND, "task_assigned")
    node = CommandListener({"task_assigned": "WORKING", "ui_touch": "INTERACTING"})
    assert _tick(node) == Status.SUCCESS
    reader = py_trees.blackboard.Client(name="test-reader-3")
    reader.register_key(key=Keys.NEXT_MODE, access=Access.READ)
    reader.register_key(key=Keys.LAST_COMMAND, access=Access.READ)
    assert reader.get(Keys.NEXT_MODE) == "WORKING"
    assert reader.get(Keys.LAST_COMMAND) is None

    bb.set(Keys.LAST_COMMAND, "unmapped_command")
    assert _tick(CommandListener({"task_assigned": "WORKING"})) == Status.FAILURE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_common_leaves.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libi_modes.common.is_mode'` (and siblings)

- [ ] **Step 3: Implement each leaf**

```python
# libi_modes/common/is_mode.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class IsMode(py_trees.behaviour.Behaviour):
    """SUCCESS iff blackboard.current_mode == self.mode. Guard leaf — first child of every branch."""

    def __init__(self, mode: str, name: str | None = None):
        super().__init__(name=name or f"IsMode[{mode}]")
        self.mode = mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.READ)

    def update(self) -> Status:
        return Status.SUCCESS if self.blackboard.get(Keys.CURRENT_MODE) == self.mode else Status.FAILURE
```

```python
# libi_modes/common/request_transition.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class RequestTransition(py_trees.behaviour.Behaviour):
    """Applies blackboard.next_mode to current_mode and clears next_mode. Always the last child of a branch."""

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "RequestTransition")

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        target = self.blackboard.get(Keys.NEXT_MODE)
        if not target:
            return Status.FAILURE
        self.blackboard.set(Keys.CURRENT_MODE, target)
        self.blackboard.set(Keys.NEXT_MODE, None)
        return Status.SUCCESS
```

```python
# libi_modes/common/fault_detected.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class FaultDetected(py_trees.behaviour.Behaviour):
    """SUCCESS + next_mode=ERROR when blackboard.fault is truthy. Highest-priority leaf in every
    branch except ErrorBranch (already ERROR — no self-transition needed, per INSTRUCTION.md)."""

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "FaultDetected")

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        if self.blackboard.get(Keys.FAULT):
            self.blackboard.set(Keys.NEXT_MODE, "ERROR")
            return Status.SUCCESS
        return Status.FAILURE
```

```python
# libi_modes/common/battery_check.py
import operator

import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys

_OPS = {"<=": operator.le, ">=": operator.ge}


class BatteryCheck(py_trees.behaviour.Behaviour):
    """SUCCESS + writes next_mode when battery_percent {op} threshold and (require_docked matches
    is_docked, if set). E.g. BatteryCheck(">=", 80, "PATROL", require_docked=True)."""

    def __init__(self, op: str, threshold: float, next_mode: str,
                 require_docked: bool | None = None, name: str | None = None):
        if op not in _OPS:
            raise ValueError(f"unsupported op {op!r}, expected '<=' or '>='")
        super().__init__(name=name or f"BatteryCheck[{op}{threshold}->{next_mode}]")
        self.op = op
        self.threshold = threshold
        self.next_mode = next_mode
        self.require_docked = require_docked

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.BATTERY_PERCENT, access=Access.READ)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        percent = self.blackboard.get(Keys.BATTERY_PERCENT)
        if percent is None or not _OPS[self.op](percent, self.threshold):
            return Status.FAILURE
        if self.require_docked is not None and self.blackboard.get(Keys.IS_DOCKED) != self.require_docked:
            return Status.FAILURE
        self.blackboard.set(Keys.NEXT_MODE, self.next_mode)
        return Status.SUCCESS
```

```python
# libi_modes/common/command_listener.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class CommandListener(py_trees.behaviour.Behaviour):
    """SUCCESS + writes next_mode when blackboard.last_command is a key in `mapping`; consumes
    (clears) last_command either way it matches, so the same command can't re-fire twice."""

    def __init__(self, mapping: dict, name: str | None = None):
        super().__init__(name=name or f"CommandListener{list(mapping.keys())}")
        self.mapping = mapping

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.LAST_COMMAND, access=Access.READ)
        self.blackboard.register_key(key=Keys.LAST_COMMAND, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        cmd = self.blackboard.get(Keys.LAST_COMMAND)
        if cmd not in self.mapping:
            return Status.FAILURE
        self.blackboard.set(Keys.NEXT_MODE, self.mapping[cmd])
        self.blackboard.set(Keys.LAST_COMMAND, None)
        return Status.SUCCESS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_common_leaves.py -v`
Expected: `6 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/is_mode.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/request_transition.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/fault_detected.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/battery_check.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/command_listener.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_common_leaves.py
git commit -m "feat: add libi_modes common BT leaves (IsMode, RequestTransition, FaultDetected, BatteryCheck, CommandListener)"
```

---

### Task 5: `Topics2BB` (sensor/command bridge)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/topics2bb.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_topics2bb.py`

**Interfaces:**
- Consumes: `Keys` from Task 3.
- Produces: `Topics2BB(providers: dict[str, callable])` — a `Behaviour` that always returns `RUNNING` and, each tick, calls each provider callable and writes its return value to the matching blackboard key. `providers` keys expected: `"battery_percent"`, `"is_docked"`, `"fault"`, `"last_command"`, `"ui_last_touch_at"`. Real `rclpy` subscriptions are wired into these providers later in `main.py` (deferred — see Global Constraints); this task only builds the injectable, unit-testable bridge.

- [ ] **Step 1: Write the failing test**

```python
# test/test_topics2bb.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.common.topics2bb import Topics2BB


def test_topics2bb_writes_provider_values_and_stays_running():
    calls = {"battery_percent": 0}

    def battery():
        calls["battery_percent"] += 1
        return 42.0

    node = Topics2BB({
        "battery_percent": battery,
        "is_docked": lambda: True,
        "fault": lambda: False,
        "last_command": lambda: None,
        "ui_last_touch_at": lambda: 0.0,
    })
    tree = py_trees.trees.BehaviourTree(root=node)
    tree.setup(timeout=15)
    tree.tick()
    assert node.status == Status.RUNNING
    assert calls["battery_percent"] == 1

    reader = py_trees.blackboard.Client(name="test-reader")
    for key in (Keys.BATTERY_PERCENT, Keys.IS_DOCKED, Keys.FAULT, Keys.LAST_COMMAND, Keys.UI_LAST_TOUCH_AT):
        reader.register_key(key=key, access=Access.READ)
    assert reader.get(Keys.BATTERY_PERCENT) == 42.0
    assert reader.get(Keys.IS_DOCKED) is True

    tree.tick()
    assert calls["battery_percent"] == 2, "provider must be re-read every tick"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_topics2bb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.common.topics2bb'`

- [ ] **Step 3: Implement**

```python
# libi_modes/common/topics2bb.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys

_KEY_BY_PROVIDER = {
    "battery_percent": Keys.BATTERY_PERCENT,
    "is_docked": Keys.IS_DOCKED,
    "fault": Keys.FAULT,
    "last_command": Keys.LAST_COMMAND,
    "ui_last_touch_at": Keys.UI_LAST_TOUCH_AT,
}


class Topics2BB(py_trees.behaviour.Behaviour):
    """Pulls each provider() every tick and writes the result to blackboard. Never blocks the
    tree (always RUNNING) — real ROS2 subscriptions are wired into `providers` from main.py,
    kept out of this class so it's testable without rclpy."""

    def __init__(self, providers: dict, name: str = "Topics2BB"):
        super().__init__(name=name)
        unknown = set(providers) - set(_KEY_BY_PROVIDER)
        if unknown:
            raise ValueError(f"unknown providers: {unknown}")
        self.providers = providers

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        for key in _KEY_BY_PROVIDER.values():
            self.blackboard.register_key(key=key, access=Access.WRITE)

    def update(self) -> Status:
        for provider_name, fn in self.providers.items():
            self.blackboard.set(_KEY_BY_PROVIDER[provider_name], fn())
        return Status.RUNNING
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_topics2bb.py -v`
Expected: `1 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/topics2bb.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_topics2bb.py
git commit -m "feat: add libi_modes Topics2BB blackboard bridge"
```

---

### Task 6: `CHARGING` and `ERROR` branches (no new leaves)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/charging.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/error.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_charging_error.py`

**Interfaces:**
- Consumes: `IsMode`, `RequestTransition`, `FaultDetected`, `BatteryCheck`, `CommandListener` (Task 4), `Keys` (Task 3).
- Produces: `charging.create(params: dict) -> py_trees.behaviour.Behaviour` and `error.create(params: dict) -> py_trees.behaviour.Behaviour`. Every branch module in this plan exposes exactly one function, `create(params)`, per INSTRUCTION.md's "노출 함수: `create()` 하나만" rule — `params` is the parsed `config/params.yaml`'s `libi_modes` dict (Task 2), so thresholds are never hardcoded in branch files.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_charging_error.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import charging, error

PARAMS = {
    "battery": {"ready": 40, "charged": 80, "low": 15},
}


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.BATTERY_PERCENT,
                Keys.IS_DOCKED, Keys.LAST_COMMAND):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.LAST_COMMAND, None)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run(root):
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE)


def test_charging_waits_below_ready_threshold():
    _seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 35.0})
    status, mode = _run(charging.create(PARAMS))
    assert status == Status.FAILURE   # branch doesn't fire -> guard(Priorities) tries next branch
    assert mode == "CHARGING"          # unchanged


def test_charging_transitions_to_idle_at_ready_threshold():
    _seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 41.0})
    status, mode = _run(charging.create(PARAMS))
    assert status == Status.SUCCESS
    assert mode == "IDLE"


def test_charging_fault_goes_to_error():
    _seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 10.0, Keys.FAULT: True})
    status, mode = _run(charging.create(PARAMS))
    assert status == Status.SUCCESS
    assert mode == "ERROR"


def test_error_stays_until_recovered_command():
    _seed(**{Keys.CURRENT_MODE: "ERROR", Keys.LAST_COMMAND: None})
    status, mode = _run(error.create(PARAMS))
    assert status == Status.FAILURE
    assert mode == "ERROR"


def test_error_recovers_to_idle():
    _seed(**{Keys.CURRENT_MODE: "ERROR", Keys.LAST_COMMAND: "recovered"})
    status, mode = _run(error.create(PARAMS))
    assert status == Status.SUCCESS
    assert mode == "IDLE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_charging_error.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.charging'`

- [ ] **Step 3: Implement**

```python
# libi_modes/branches/charging.py
import py_trees

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """ChargingBranch — INSTRUCTION.md '1. CHARGING'. No new leaves."""
    ready = params["battery"]["ready"]
    return py_trees.composites.Sequence(
        name="ChargingBranch",
        memory=False,
        children=[
            IsMode("CHARGING"),
            py_trees.composites.Selector(
                name="ChargingExitConditions",
                memory=False,
                children=[
                    FaultDetected(),
                    BatteryCheck(">=", ready, "IDLE"),
                ],
            ),
            RequestTransition(),
        ],
    )
```

```python
# libi_modes/branches/error.py
import py_trees

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """ErrorBranch — INSTRUCTION.md '8. ERROR'. Only branch without FaultDetected (already
    ERROR — no self-transition needed). No autonomous exit; only an explicit `recovered` command."""
    return py_trees.composites.Sequence(
        name="ErrorBranch",
        memory=False,
        children=[
            IsMode("ERROR"),
            CommandListener({"recovered": "IDLE"}),
            RequestTransition(),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_charging_error.py -v`
Expected: `5 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/charging.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/error.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_charging_error.py
git commit -m "feat: add libi_modes CHARGING and ERROR branches"
```

---

### Task 7: `IDLE` branch

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/idle.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_idle.py`

**Interfaces:**
- Consumes: same common leaves as Task 6.
- Produces: `idle.create(params) -> Behaviour`.

**Note on a spec gap found while planning (flagged, not silently resolved):** INSTRUCTION.md's "야간 동작 루프" section shows `IDLE --(15분 타이머)--> SECURITY_PATROL`, but the `IdleBranch` ASCII diagram only has `FaultDetected`, `BatteryCheck(<=15)`, `CommandListener`, `BatteryCheck(>=80)` — no timer leaf. This plan implements exactly what the branch diagram shows (`CommandListener` handling `security_patrol_request` like any other command) and treats the 15-minute recurrence as **external** — some other node (not designed yet, likely part of a future `main.py`/scheduler) is assumed to publish `last_command = "security_patrol_request"` every 15 minutes while docked at night. Confirm this assumption with the user before Task 12 wires `main.py` for real.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_idle.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import idle

PARAMS = {"battery": {"ready": 40, "charged": 80, "low": 15}}


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.BATTERY_PERCENT,
                Keys.IS_DOCKED, Keys.LAST_COMMAND):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.CURRENT_MODE, "IDLE")
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.LAST_COMMAND, None)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run():
    root = idle.create(PARAMS)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE)


def test_idle_docked_battery_charged_goes_patrol():
    _seed(**{Keys.BATTERY_PERCENT: 85.0, Keys.IS_DOCKED: True})
    assert _run() == (Status.SUCCESS, "PATROL")


def test_idle_undocked_high_battery_stays_idle():
    """Guard from INSTRUCTION.md: docked=True gate prevents an undocked stopped robot
    from auto-leaving IDLE just because battery reads high."""
    _seed(**{Keys.BATTERY_PERCENT: 95.0, Keys.IS_DOCKED: False})
    assert _run() == (Status.FAILURE, "IDLE")


def test_idle_undocked_low_battery_returns():
    _seed(**{Keys.BATTERY_PERCENT: 10.0, Keys.IS_DOCKED: False})
    assert _run() == (Status.SUCCESS, "RETURNING")


def test_idle_docked_low_battery_does_not_return():
    """docked=False guard on the RETURNING check prevents a docked robot from tripping
    the low-battery path while it's still charging past 15%."""
    _seed(**{Keys.BATTERY_PERCENT: 10.0, Keys.IS_DOCKED: True})
    assert _run() == (Status.FAILURE, "IDLE")


def test_idle_task_assigned_goes_working():
    _seed(**{Keys.BATTERY_PERCENT: 50.0, Keys.IS_DOCKED: True, Keys.LAST_COMMAND: "task_assigned"})
    assert _run() == (Status.SUCCESS, "WORKING")


def test_idle_resume_request_from_stopped_undocked_robot():
    _seed(**{Keys.BATTERY_PERCENT: 50.0, Keys.IS_DOCKED: False, Keys.LAST_COMMAND: "resume_request"})
    assert _run() == (Status.SUCCESS, "PATROL")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_idle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.idle'`

- [ ] **Step 3: Implement**

```python
# libi_modes/branches/idle.py
import py_trees

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition

_COMMAND_MAP = {
    "task_assigned": "WORKING",
    "security_patrol_request": "SECURITY_PATROL",
    "resume_request": "PATROL",
}


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """IdleBranch — INSTRUCTION.md '2. IDLE'. Order matches the doc's Selector priority:
    fault > low-battery-return > command > battery-charged-auto-patrol."""
    low = params["battery"]["low"]
    charged = params["battery"]["charged"]
    return py_trees.composites.Sequence(
        name="IdleBranch",
        memory=False,
        children=[
            IsMode("IDLE"),
            py_trees.composites.Selector(
                name="IdleExitConditions",
                memory=False,
                children=[
                    FaultDetected(),
                    BatteryCheck("<=", low, "RETURNING", require_docked=False),
                    CommandListener(_COMMAND_MAP),
                    BatteryCheck(">=", charged, "PATROL", require_docked=True),
                ],
            ),
            RequestTransition(),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_idle.py -v`
Expected: `6 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/idle.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_idle.py
git commit -m "feat: add libi_modes IDLE branch"
```

---

### Task 8: `PATROL` and `SECURITY_PATROL` branches

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/navigation_actions.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/patrol.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/security_patrol.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_patrol.py`

**Interfaces:**
- Consumes: common leaves (Task 4), `Keys` (Task 3).
- Produces:
  - `PatrolNavigation(driver, name=None)` and `SecurityPatrolNavigation(driver, name=None)` in `navigation_actions.py` — thin `Behaviour` wrappers around an injected `driver` object exposing `start()`, `poll() -> "running"|"success"|"failure"`, `stop()`. `driver` is a stand-in for the real Nav2/robot_agent client, wired later in `main.py` (out of scope here — same deferral as Task 5's `Topics2BB` providers).
  - `patrol.create(params, driver) -> Behaviour`, `security_patrol.create(params, driver) -> Behaviour`. Unlike other `create()` functions, these two take a second `driver` argument (the navigation client) since INSTRUCTION.md's "신규 작성할 leaf" for these branches is explicitly a hardware-facing action node — `registry.py` (Task 12) is responsible for supplying it.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_patrol.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import patrol, security_patrol

PARAMS = {"battery": {"ready": 40, "charged": 80, "low": 15}}


class FakeDriver:
    """Records start()/stop() calls; poll() result is controlled by the test."""

    def __init__(self, poll_sequence):
        self._poll_sequence = list(poll_sequence)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def poll(self):
        return self._poll_sequence.pop(0) if self._poll_sequence else "running"

    def stop(self):
        self.stopped = True


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.BATTERY_PERCENT, Keys.LAST_COMMAND):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.LAST_COMMAND, None)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run(root):
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE)


def test_patrol_keeps_navigating_with_no_exit_condition():
    _seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0})
    driver = FakeDriver(poll_sequence=["running"])
    status, mode = _run(patrol.create(PARAMS, driver))
    assert status == Status.RUNNING
    assert mode == "PATROL"
    assert driver.started


def test_patrol_low_battery_interrupts_navigation_and_returns():
    _seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 10.0})
    driver = FakeDriver(poll_sequence=["running"])
    status, mode = _run(patrol.create(PARAMS, driver))
    assert status == Status.SUCCESS
    assert mode == "RETURNING"


def test_patrol_task_assigned_goes_working():
    _seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0, Keys.LAST_COMMAND: "task_assigned"})
    driver = FakeDriver(poll_sequence=["running"])
    status, mode = _run(patrol.create(PARAMS, driver))
    assert status == Status.SUCCESS
    assert mode == "WORKING"


def test_security_patrol_completes_one_loop_then_idle():
    _seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0})
    driver = FakeDriver(poll_sequence=["success"])
    status, mode = _run(security_patrol.create(PARAMS, driver))
    assert status == Status.SUCCESS
    assert mode == "IDLE"


def test_security_patrol_only_stop_request_interrupts_not_task_or_touch():
    _seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0, Keys.LAST_COMMAND: "task_assigned"})
    driver = FakeDriver(poll_sequence=["running"])
    status, mode = _run(security_patrol.create(PARAMS, driver))
    assert status == Status.RUNNING
    assert mode == "SECURITY_PATROL"   # task_assigned is not in SECURITY_PATROL's mapping (only stop_request)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_patrol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.patrol'`

- [ ] **Step 3: Implement**

```python
# libi_modes/common/navigation_actions.py
import py_trees
from py_trees.common import Status


class _DriverAction(py_trees.behaviour.Behaviour):
    """Shared start/poll/stop wiring for driver-backed navigation leaves."""

    def __init__(self, driver, name: str):
        super().__init__(name=name)
        self.driver = driver
        self._started = False

    def initialise(self):
        self._started = False

    def update(self) -> Status:
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result == "success":
            return Status.SUCCESS
        if result == "failure":
            return Status.FAILURE
        return Status.RUNNING

    def terminate(self, new_status):
        if self._started and new_status != Status.SUCCESS:
            self.driver.stop()
        self._started = False


class PatrolNavigation(_DriverAction):
    """Continuous library-round navigation. Never returns SUCCESS on its own — the doc's
    'PATROL -> 계속 RUNNING (무한 순회)' — it's interrupted by the exit-condition Selector
    inside the Parallel, not by finishing."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, name or "PatrolNavigation")

    def update(self) -> Status:
        result = super().update()
        return Status.RUNNING if result == Status.SUCCESS else result


class SecurityPatrolNavigation(_DriverAction):
    """One security patrol loop (route + intrusion detect/record/alert per SR-19, handled
    inside the driver). SUCCESS when the loop completes."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, name or "SecurityPatrolNavigation")
```

```python
# libi_modes/branches/patrol.py
import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import PatrolNavigation
from libi_modes.common.request_transition import RequestTransition

_COMMAND_MAP = {
    "task_assigned": "WORKING",
    "ui_touch": "INTERACTING",
    "stop_request": "IDLE",
}


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """PatrolBranch — INSTRUCTION.md '3. PATROL'."""
    low = params["battery"]["low"]
    return py_trees.composites.Sequence(
        name="PatrolBranch",
        memory=False,
        children=[
            IsMode("PATROL"),
            py_trees.composites.Parallel(
                name="PatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    PatrolNavigation(driver),
                    py_trees.composites.Selector(
                        name="PatrolExitConditions",
                        memory=False,
                        children=[
                            FaultDetected(),
                            BatteryCheck("<=", low, "RETURNING"),
                            CommandListener(_COMMAND_MAP),
                        ],
                    ),
                ],
            ),
            RequestTransition(),
        ],
    )
```

```python
# libi_modes/branches/security_patrol.py
import py_trees
from py_trees.common import Access, ParallelPolicy, Status

from libi_modes.blackboard import Keys
from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import SecurityPatrolNavigation
from libi_modes.common.request_transition import RequestTransition

_COMMAND_MAP = {"stop_request": "IDLE"}


class _SetNextMode(py_trees.behaviour.Behaviour):
    """One-shot: always SUCCESS, writes a fixed next_mode. Used after a self-completing
    action (e.g. one security patrol loop) rather than reading an external condition."""

    def __init__(self, next_mode: str, name: str | None = None):
        super().__init__(name=name or f"SetNextMode[{next_mode}]")
        self.next_mode = next_mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        self.blackboard.set(Keys.NEXT_MODE, self.next_mode)
        return Status.SUCCESS


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """SecurityPatrolBranch — INSTRUCTION.md '4. SECURITY_PATROL'. Same skeleton as PATROL;
    the nav leaf completes after one loop instead of running forever, and only
    `stop_request` is listened for (night operation — no task/touch handling)."""
    low = params["battery"]["low"]
    return py_trees.composites.Sequence(
        name="SecurityPatrolBranch",
        memory=False,
        children=[
            IsMode("SECURITY_PATROL"),
            py_trees.composites.Parallel(
                name="SecurityPatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="OnePatrolLoop",
                        memory=True,
                        children=[
                            SecurityPatrolNavigation(driver),
                            _SetNextMode("IDLE"),
                        ],
                    ),
                    py_trees.composites.Selector(
                        name="SecurityPatrolExitConditions",
                        memory=False,
                        children=[
                            FaultDetected(),
                            BatteryCheck("<=", low, "RETURNING"),
                            CommandListener(_COMMAND_MAP),
                        ],
                    ),
                ],
            ),
            RequestTransition(),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_patrol.py -v`
Expected: `5 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/navigation_actions.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/patrol.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/security_patrol.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_patrol.py
git commit -m "feat: add libi_modes PATROL and SECURITY_PATROL branches"
```

---

### Task 9: `INTERACTING` branch (`UiSessionTimer` + interlock)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/ui_session_timer.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/interacting.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_interacting.py`

**Interfaces:**
- Consumes: common leaves (Task 4), `Keys` (Task 3).
- Produces: `UiSessionTimer(timeout_sec: float, clock: callable = time.monotonic, name=None)` — SUCCESS + `next_mode="PATROL"` once `clock() - blackboard.ui_last_touch_at >= timeout_sec`; RUNNING otherwise. Sets `blackboard.drive_lock = blackboard.arm_lock = True` in `initialise()`, sets both to `False` in `terminate()`. `interacting.create(params) -> Behaviour`.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_interacting.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import interacting

PARAMS = {"interacting": {"ui_idle_timeout_sec": 20}}


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.LAST_COMMAND,
                Keys.UI_LAST_TOUCH_AT, Keys.DRIVE_LOCK, Keys.ARM_LOCK):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.CURRENT_MODE, "INTERACTING")
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.LAST_COMMAND, None)
    bb.set(Keys.UI_LAST_TOUCH_AT, 0.0)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run(clock):
    root = interacting.create(PARAMS, clock=clock)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    reader.register_key(key=Keys.DRIVE_LOCK, access=Access.READ)
    reader.register_key(key=Keys.ARM_LOCK, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE), reader.get(Keys.DRIVE_LOCK), reader.get(Keys.ARM_LOCK)


def test_interacting_locks_both_drive_and_arm_while_running():
    _seed()
    status, mode, drive_lock, arm_lock = _run(clock=lambda: 5.0)   # 5s since touch, timeout=20s
    assert status == Status.RUNNING
    assert mode == "INTERACTING"
    assert drive_lock is True and arm_lock is True


def test_interacting_timeout_returns_to_patrol_and_unlocks():
    _seed()
    status, mode, drive_lock, arm_lock = _run(clock=lambda: 25.0)   # 25s since touch, timeout=20s
    assert status == Status.SUCCESS
    assert mode == "PATROL"
    assert drive_lock is False and arm_lock is False


def test_interacting_task_assigned_overrides_timeout_wait():
    _seed(**{Keys.LAST_COMMAND: "task_assigned"})
    status, mode, drive_lock, arm_lock = _run(clock=lambda: 5.0)
    assert status == Status.SUCCESS
    assert mode == "WORKING"
    assert drive_lock is False and arm_lock is False


def test_interacting_ignores_low_battery():
    """No BatteryCheck leaf in this branch — INTERACTING must not react to battery at all."""
    _seed()
    root = interacting.create(PARAMS, clock=lambda: 5.0)
    assert not any(isinstance(node, type(None)) for node in [])  # sanity placeholder removed below
```

Note on the last test: `BatteryCheck` objects aren't reachable by isinstance-walking easily without importing the class — replace the body with an explicit walk instead of the placeholder line above:

```python
def test_interacting_has_no_battery_check_leaf():
    from libi_modes.common.battery_check import BatteryCheck
    root = interacting.create(PARAMS, clock=lambda: 5.0)

    def walk(node):
        yield node
        for child in getattr(node, "children", []):
            yield from walk(child)

    assert not any(isinstance(n, BatteryCheck) for n in walk(root))
```

(Replace the earlier placeholder test body with this version before running — this is the actual Step 1 content; the intermediate line above was corrected here so the test file has no leftover placeholder assertion.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_interacting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.interacting'`

- [ ] **Step 3: Implement**

```python
# libi_modes/common/ui_session_timer.py
import time

import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class UiSessionTimer(py_trees.behaviour.Behaviour):
    """SUCCESS + next_mode=PATROL once `timeout_sec` elapse since the last UI touch.
    Owns the interlock: sets drive_lock/arm_lock True on initialise(), False on terminate()
    (INSTRUCTION.md: '체결·해제는 UiSessionTimer의 initialise()/terminate()에서 처리')."""

    def __init__(self, timeout_sec: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"UiSessionTimer[{timeout_sec}s]")
        self.timeout_sec = timeout_sec
        self.clock = clock

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.UI_LAST_TOUCH_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.DRIVE_LOCK, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.ARM_LOCK, access=Access.WRITE)

    def initialise(self):
        self.blackboard.set(Keys.DRIVE_LOCK, True)
        self.blackboard.set(Keys.ARM_LOCK, True)

    def update(self) -> Status:
        elapsed = self.clock() - self.blackboard.get(Keys.UI_LAST_TOUCH_AT)
        if elapsed >= self.timeout_sec:
            self.blackboard.set(Keys.NEXT_MODE, "PATROL")
            return Status.SUCCESS
        return Status.RUNNING

    def terminate(self, new_status):
        self.blackboard.set(Keys.DRIVE_LOCK, False)
        self.blackboard.set(Keys.ARM_LOCK, False)
```

```python
# libi_modes/branches/interacting.py
import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.ui_session_timer import UiSessionTimer

_COMMAND_MAP = {
    "ui_close": "PATROL",
    "task_assigned": "WORKING",
    "stop_request": "IDLE",
}


def create(params: dict, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """InteractingBranch — INSTRUCTION.md '5. INTERACTING'. Deliberately no BatteryCheck leaf:
    the doc excludes INTERACTING -> RETURNING so a user session in progress never gets cut
    by low battery (same reasoning as WORKING, see Task 10)."""
    timeout = params["interacting"]["ui_idle_timeout_sec"]
    return py_trees.composites.Sequence(
        name="InteractingBranch",
        memory=False,
        children=[
            IsMode("INTERACTING"),
            py_trees.composites.Parallel(
                name="SessionAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    UiSessionTimer(timeout, clock=clock),
                    py_trees.composites.Selector(
                        name="InteractingExitConditions",
                        memory=False,
                        children=[
                            FaultDetected(),
                            CommandListener(_COMMAND_MAP),
                        ],
                    ),
                ],
            ),
            RequestTransition(),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_interacting.py -v`
Expected: `4 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/ui_session_timer.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/interacting.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_interacting.py
git commit -m "feat: add libi_modes INTERACTING branch with drive/arm interlock"
```

---

### Task 10: `WORKING` branch (`NavigationExec`, `ArmExec` dispatch, `CommandTimeout`)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/command_timeout.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/working_actions.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/working.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_working.py`

**Interfaces:**
- Consumes: common leaves (Task 4), `Keys` (Task 3).
- Produces:
  - `CommandTimeout(timeout_sec, clock=time.monotonic, name=None)` — SUCCESS + `next_mode="ERROR"` when `clock() - blackboard.command_received_at >= timeout_sec` AND `blackboard.active_command` is falsy (no command currently running); FAILURE otherwise.
  - `NavigationExec(nav_driver, name=None)`, `ArmExec(arm_driver, name=None)` in `working_actions.py` — each checks `blackboard.active_command`; `NavigationExec` only engages for `"navigate"`/`"dock"`, `ArmExec` only for `"perform_action"` (INSTRUCTION.md "서브스테이트 대응" table); each is FAILURE immediately if `active_command` doesn't match its own kind (so the parent Selector falls through to the next), else drives `driver.start()/poll()/stop()` like `_DriverAction` in Task 8, and clears `active_command` to `None` on its own SUCCESS/FAILURE so the next command can be dispatched.
  - `working.create(params, nav_driver, arm_driver, clock=time.monotonic) -> Behaviour`.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_working.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import working

PARAMS = {"working": {"command_timeout_sec": 120}}


class FakeDriver:
    def __init__(self, poll_sequence):
        self._poll_sequence = list(poll_sequence)
        self.started = False

    def start(self):
        self.started = True

    def poll(self):
        return self._poll_sequence.pop(0) if self._poll_sequence else "running"

    def stop(self):
        pass


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.LAST_COMMAND,
                Keys.ACTIVE_COMMAND, Keys.COMMAND_RECEIVED_AT):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.CURRENT_MODE, "WORKING")
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.LAST_COMMAND, None)
    bb.set(Keys.ACTIVE_COMMAND, None)
    bb.set(Keys.COMMAND_RECEIVED_AT, 0.0)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run(nav_driver, arm_driver, clock):
    root = working.create(PARAMS, nav_driver, arm_driver, clock=clock)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE)


def test_working_dispatches_navigate_command():
    _seed(**{Keys.ACTIVE_COMMAND: "navigate"})
    nav = FakeDriver(["running"])
    arm = FakeDriver(["running"])
    status, mode = _run(nav, arm, clock=lambda: 1.0)
    assert status == Status.RUNNING
    assert mode == "WORKING"
    assert nav.started and not arm.started


def test_working_dispatches_perform_action_command():
    _seed(**{Keys.ACTIVE_COMMAND: "perform_action"})
    nav = FakeDriver(["running"])
    arm = FakeDriver(["running"])
    status, mode = _run(nav, arm, clock=lambda: 1.0)
    assert status == Status.RUNNING
    assert mode == "WORKING"
    assert arm.started and not nav.started


def test_working_no_command_waits_without_timing_out_early():
    _seed()
    status, mode = _run(FakeDriver([]), FakeDriver([]), clock=lambda: 1.0)
    assert status == Status.RUNNING
    assert mode == "WORKING"


def test_working_command_timeout_goes_error():
    _seed(**{Keys.COMMAND_RECEIVED_AT: 0.0})
    status, mode = _run(FakeDriver([]), FakeDriver([]), clock=lambda: 200.0)
    assert status == Status.SUCCESS
    assert mode == "ERROR"


def test_working_task_done_goes_patrol():
    _seed(**{Keys.LAST_COMMAND: "task_done"})
    status, mode = _run(FakeDriver([]), FakeDriver([]), clock=lambda: 1.0)
    assert status == Status.SUCCESS
    assert mode == "PATROL"


def test_working_stop_request_goes_idle():
    _seed(**{Keys.LAST_COMMAND: "stop_request"})
    status, mode = _run(FakeDriver([]), FakeDriver([]), clock=lambda: 1.0)
    assert status == Status.SUCCESS
    assert mode == "IDLE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_working.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.working'`

- [ ] **Step 3: Implement**

```python
# libi_modes/common/command_timeout.py
import time

import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class CommandTimeout(py_trees.behaviour.Behaviour):
    """SUCCESS + next_mode=ERROR if no command has arrived for `timeout_sec` while WORKING
    and nothing is currently active. Prevents the robot from waiting forever if task_adapter
    dies (INSTRUCTION.md: 'CommandTimeout이 필요한 이유')."""

    def __init__(self, timeout_sec: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"CommandTimeout[{timeout_sec}s]")
        self.timeout_sec = timeout_sec
        self.clock = clock

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.COMMAND_RECEIVED_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        if self.blackboard.get(Keys.ACTIVE_COMMAND):
            return Status.FAILURE
        elapsed = self.clock() - self.blackboard.get(Keys.COMMAND_RECEIVED_AT)
        if elapsed >= self.timeout_sec:
            self.blackboard.set(Keys.NEXT_MODE, "ERROR")
            return Status.SUCCESS
        return Status.FAILURE
```

```python
# libi_modes/common/working_actions.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class _CommandDrivenAction(py_trees.behaviour.Behaviour):
    """FAILURE immediately if blackboard.active_command isn't one of `handles`; otherwise
    drives `driver` like the navigation leaves in Task 8, clearing active_command on
    completion so the next queued command can be picked up."""

    def __init__(self, driver, handles: set, name: str):
        super().__init__(name=name)
        self.driver = driver
        self.handles = handles
        self._started = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.READ)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.WRITE)

    def initialise(self):
        self._started = False

    def update(self) -> Status:
        if self.blackboard.get(Keys.ACTIVE_COMMAND) not in self.handles:
            return Status.FAILURE
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result in ("success", "failure"):
            self.blackboard.set(Keys.ACTIVE_COMMAND, None)
            self._started = False
            return Status.SUCCESS if result == "success" else Status.FAILURE
        return Status.RUNNING

    def terminate(self, new_status):
        if self._started and new_status == Status.INVALID:
            self.driver.stop()
        self._started = False


class NavigationExec(_CommandDrivenAction):
    """navigate()/dock() -> Nav2 action delegation (INSTRUCTION.md 서브스테이트 대응)."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"navigate", "dock"}, name=name or "NavigationExec")


class ArmExec(_CommandDrivenAction):
    """perform_action() -> arm subtree delegation. The grasp/place internals are the
    'ArmExec 내부' subtree INSTRUCTION.md marks '작성 예정' — out of scope for this plan;
    `driver` here is a placeholder client swapped for the real one once that subtree exists."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"perform_action"}, name=name or "ArmExec")
```

```python
# libi_modes/branches/working.py
import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.command_timeout import CommandTimeout
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.working_actions import ArmExec, NavigationExec

_COMMAND_MAP = {
    "task_done": "PATROL",
    "task_failed": "PATROL",
    "stop_request": "IDLE",
}


def create(params: dict, nav_driver, arm_driver, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """WorkingBranch — INSTRUCTION.md '6. WORKING'. No BatteryCheck leaf: FMS accounts for
    battery at dispatch time, so a task in progress is never cut by low battery
    (INSTRUCTION.md: 'BatteryCheck를 두지 않는 이유'). Sequencing across multiple commands
    within one task is task_adapter's job, not this branch's — it only executes whatever
    `active_command` currently holds."""
    timeout = params["working"]["command_timeout_sec"]
    return py_trees.composites.Sequence(
        name="WorkingBranch",
        memory=False,
        children=[
            IsMode("WORKING"),
            py_trees.composites.Parallel(
                name="ExecuteAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Selector(
                        name="CommandDispatch",
                        memory=False,
                        children=[
                            NavigationExec(nav_driver),
                            ArmExec(arm_driver),
                            py_trees.behaviours.Running(name="AwaitingCommand"),
                        ],
                    ),
                    py_trees.composites.Selector(
                        name="WorkingExitConditions",
                        memory=False,
                        children=[
                            FaultDetected(),
                            CommandTimeout(timeout, clock=clock),
                            CommandListener(_COMMAND_MAP),
                        ],
                    ),
                ],
            ),
            RequestTransition(),
        ],
    )
```

**Note (`stop_request` FMS report, INSTRUCTION.md "`stop_request`로 이탈할 때는 FMS에 `task_cancelled`를 보고한다"):** that report is a side effect owned by whichever code turns the FMS UI's stop button into `blackboard.last_command = "stop_request"` (Stage 2's backend, not this BT package) — `CommandListener` here only reacts to the blackboard value. Cross-referenced in `2026-07-20-fms-fsm-bt-panel.md` Task on the transition-request service handler so the report isn't dropped.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_working.py -v`
Expected: `6 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/command_timeout.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/working_actions.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/working.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_working.py
git commit -m "feat: add libi_modes WORKING branch (navigation/arm dispatch, command timeout)"
```

---

### Task 11: `RETURNING` branch (`ReturnNavigation`, dock retry, arm-home-first)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/return_navigation.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/returning.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_returning.py`

**Interfaces:**
- Consumes: common leaves (Task 4), `Keys` (Task 3).
- Produces: `ReturnNavigation(arm_driver, dock_driver, retry_max, name=None)` — on `initialise()` calls `arm_driver.go_home()` once; drives `dock_driver.start()/poll()/stop()` for the actual docking; on `poll() == "failure"` increments `blackboard.dock_retry_count` and restarts the dock attempt (up to `retry_max` times) instead of failing immediately; after `retry_max` consecutive failures, returns FAILURE (so the branch's outer Sequence fails, the exit-condition `FaultDetected` on the next tick catches it via an externally-set `fault` — see note below). `returning.create(params, arm_driver, dock_driver) -> Behaviour`.

**Note on how "재시도 소진 → fault 발생 → ERROR" is implemented:** INSTRUCTION.md says "도킹 재시도 최대 3회. 소진 시 FAILURE → fault 발생 → ERROR" for `ReturnNavigation`. A leaf returning FAILURE only makes the *parent Sequence* fail — it doesn't set `blackboard.fault` by itself. This plan makes `ReturnNavigation` set `blackboard.fault = True` directly when retries are exhausted (in addition to returning FAILURE), so the *next* tick's `FaultDetected` leaf (still present in `ReturningBranch`, per INSTRUCTION.md's exit-condition table) picks it up and drives `next_mode = "ERROR"` through the normal path — no bypass of the "fault 검사는 브랜치가 자체적으로 갖는다" rule.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_branch_returning.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.branches import returning

PARAMS = {"returning": {"dock_retry_max": 3}}


class FakeArmDriver:
    def __init__(self):
        self.went_home = False

    def go_home(self):
        self.went_home = True


class FakeDockDriver:
    def __init__(self, poll_sequence):
        self._poll_sequence = list(poll_sequence)
        self.start_count = 0

    def start(self):
        self.start_count += 1

    def poll(self):
        return self._poll_sequence.pop(0) if self._poll_sequence else "running"

    def stop(self):
        pass


def _seed(**kwargs):
    bb = py_trees.blackboard.Client(name=f"seed-{id(kwargs)}")
    for key in (Keys.CURRENT_MODE, Keys.NEXT_MODE, Keys.FAULT, Keys.DOCK_RETRY_COUNT):
        bb.register_key(key=key, access=Access.WRITE)
    bb.set(Keys.CURRENT_MODE, "RETURNING")
    bb.set(Keys.NEXT_MODE, None)
    bb.set(Keys.FAULT, False)
    bb.set(Keys.DOCK_RETRY_COUNT, 0)
    for k, v in kwargs.items():
        bb.set(k, v)
    return bb


def _run(arm_driver, dock_driver):
    root = returning.create(PARAMS, arm_driver, dock_driver)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    reader.register_key(key=Keys.FAULT, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE), reader.get(Keys.FAULT)


def test_returning_homes_arm_before_docking():
    _seed()
    arm, dock = FakeArmDriver(), FakeDockDriver(["running"])
    _run(arm, dock)
    assert arm.went_home is True


def test_returning_docks_successfully_goes_charging():
    _seed()
    arm, dock = FakeArmDriver(), FakeDockDriver(["success"])
    status, mode, fault = _run(arm, dock)
    assert status == Status.SUCCESS
    assert mode == "CHARGING"
    assert fault is False


def test_returning_retries_docking_up_to_max_before_faulting():
    _seed(**{Keys.DOCK_RETRY_COUNT: 3})   # already exhausted 3 retries
    arm, dock = FakeArmDriver(), FakeDockDriver(["failure"])
    status, mode, fault = _run(arm, dock)
    assert fault is True
    assert mode == "RETURNING"   # this tick's branch Sequence fails on the nav leaf;
                                   # FaultDetected fires on the *next* tick (see note above)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_branch_returning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.branches.returning'`

- [ ] **Step 3: Implement**

```python
# libi_modes/common/return_navigation.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class ReturnNavigation(py_trees.behaviour.Behaviour):
    """Arm home -> dock, with up to `retry_max` dock retries (SR-18). Exhausting retries sets
    blackboard.fault so the branch's own FaultDetected leaf drives ERROR on the next tick
    (INSTRUCTION.md '도킹 재시도 최대 3회. 소진 시 FAILURE -> fault 발생 -> ERROR')."""

    def __init__(self, arm_driver, dock_driver, retry_max: int, name: str | None = None):
        super().__init__(name=name or "ReturnNavigation")
        self.arm_driver = arm_driver
        self.dock_driver = dock_driver
        self.retry_max = retry_max
        self._homed = False
        self._dock_started = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.DOCK_RETRY_COUNT, access=Access.READ)
        self.blackboard.register_key(key=Keys.DOCK_RETRY_COUNT, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.WRITE)

    def initialise(self):
        if not self._homed:
            self.arm_driver.go_home()
            self._homed = True

    def update(self) -> Status:
        if not self._dock_started:
            self.dock_driver.start()
            self._dock_started = True
        result = self.dock_driver.poll()
        if result == "success":
            return Status.SUCCESS
        if result == "failure":
            retries = self.blackboard.get(Keys.DOCK_RETRY_COUNT) + 1
            self.blackboard.set(Keys.DOCK_RETRY_COUNT, retries)
            self._dock_started = False
            if retries >= self.retry_max:
                self.blackboard.set(Keys.FAULT, True)
                return Status.FAILURE
            return Status.RUNNING   # retry: next tick calls dock_driver.start() again
        return Status.RUNNING

    def terminate(self, new_status):
        self._homed = False
        self._dock_started = False
```

```python
# libi_modes/branches/returning.py
import py_trees
from py_trees.common import Access, ParallelPolicy, Status

from libi_modes.blackboard import Keys
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.return_navigation import ReturnNavigation


class _SetNextMode(py_trees.behaviour.Behaviour):
    def __init__(self, next_mode: str, name: str | None = None):
        super().__init__(name=name or f"SetNextMode[{next_mode}]")
        self.next_mode = next_mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        self.blackboard.set(Keys.NEXT_MODE, self.next_mode)
        return Status.SUCCESS


def create(params: dict, arm_driver, dock_driver) -> py_trees.behaviour.Behaviour:
    """ReturningBranch — INSTRUCTION.md '7. RETURNING'. Deliberately no CommandListener:
    a robot below 15% battery must not be stoppable mid-return (doc: 'stop_request로 세우면
    충전소에 도달하지 못하고 방전된다'). Only `docked` (SUCCESS path) and `fault` leave here."""
    retry_max = params["returning"]["dock_retry_max"]
    return py_trees.composites.Sequence(
        name="ReturningBranch",
        memory=False,
        children=[
            IsMode("RETURNING"),
            py_trees.composites.Parallel(
                name="ReturnAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="OneReturnAttempt",
                        memory=True,
                        children=[
                            ReturnNavigation(arm_driver, dock_driver, retry_max),
                            _SetNextMode("CHARGING"),
                        ],
                    ),
                    FaultDetected(),
                ],
            ),
            RequestTransition(),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_branch_returning.py -v`
Expected: `3 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/return_navigation.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/returning.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_returning.py
git commit -m "feat: add libi_modes RETURNING branch (arm-home-first, dock retry)"
```

---

### Task 12: `registry.py` + `tree.py` (assembly)

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/tree.py`
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_tree_integration.py`

**Interfaces:**
- Consumes: every `branches/*.create(...)` from Tasks 6–11, `Topics2BB` from Task 5.
- Produces:
  - `registry.BRANCH_ORDER: list[str]` — the 8 state names in priority order, `["ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE"]` (matches INSTRUCTION.md's `Priorities` diagram ordering exactly). **Stage 2's FMS panel reuses this list verbatim for "상태 → 표시할 서브트리" per INSTRUCTION.md's "별도 매핑 테이블을 만들지 않는다" rule** — do not hand-copy this list into the frontend/backend; Stage 2's plan must import or otherwise source it from here.
  - `registry.build_branches(params, drivers) -> dict[str, Behaviour]` — `drivers` is `{"patrol": ..., "security_patrol": ..., "nav": ..., "arm": ..., "return_arm": ..., "return_dock": ...}`, one entry per branch that needs a hardware client.
  - `tree.build_root(params, drivers, providers) -> py_trees.behaviour.Behaviour` — the full `Parallel(Topics2BB, Priorities)` root, `Priorities` ending in `py_trees.behaviours.Running()` (INSTRUCTION.md "트리 죽음 방지").

- [ ] **Step 1: Write the failing test**

```python
# test/test_tree_integration.py
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes import registry, tree

PARAMS = {
    "battery": {"ready": 40, "charged": 80, "low": 15},
    "interacting": {"ui_idle_timeout_sec": 20},
    "working": {"command_timeout_sec": 120},
    "returning": {"dock_retry_max": 3},
}


class _NullDriver:
    def start(self):
        pass

    def poll(self):
        return "running"

    def stop(self):
        pass

    def go_home(self):
        pass


def _drivers():
    return {
        "patrol": _NullDriver(),
        "security_patrol": _NullDriver(),
        "nav": _NullDriver(),
        "arm": _NullDriver(),
        "return_arm": _NullDriver(),
        "return_dock": _NullDriver(),
    }


def _providers(**overrides):
    base = {
        "battery_percent": lambda: 60.0,
        "is_docked": lambda: False,
        "fault": lambda: False,
        "last_command": lambda: None,
        "ui_last_touch_at": lambda: 0.0,
    }
    base.update(overrides)
    return base


def test_registry_branch_order_matches_instruction_priorities():
    assert registry.BRANCH_ORDER == [
        "ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING",
        "SECURITY_PATROL", "PATROL", "IDLE",
    ]


def test_tree_boots_into_returning_then_charging_then_idle():
    root = tree.build_root(PARAMS, _drivers(), _providers(is_docked=lambda: True, battery_percent=lambda: 20.0))
    t = py_trees.trees.BehaviourTree(root=root)
    t.setup(timeout=15)

    seed = py_trees.blackboard.Client(name="seed")
    seed.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
    seed.set(Keys.CURRENT_MODE, "RETURNING")   # '[*] -> RETURNING : boot' happens once, outside the tree

    reader = py_trees.blackboard.Client(name="reader")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)

    # Docked immediately (NullDriver never fails) -> RETURNING's dock succeeds this tick -> CHARGING
    t.tick()
    assert reader.get(Keys.CURRENT_MODE) == "CHARGING"

    # battery already >= ready(40) and docked -> CHARGING exits to IDLE next tick
    t.tick()
    assert reader.get(Keys.CURRENT_MODE) == "IDLE"


def test_tree_never_dies_when_no_branch_guard_matches():
    """Defensive case: current_mode set to something no IsMode matches. Priorities' trailing
    Running() must still keep the whole tree from returning FAILURE (INSTRUCTION.md '트리 죽음 방지')."""
    root = tree.build_root(PARAMS, _drivers(), _providers())
    t = py_trees.trees.BehaviourTree(root=root)
    t.setup(timeout=15)
    seed = py_trees.blackboard.Client(name="seed2")
    seed.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
    seed.set(Keys.CURRENT_MODE, "__UNKNOWN__")
    t.tick()
    assert root.status == Status.RUNNING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tree_integration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'libi_modes.registry'`

- [ ] **Step 3: Implement**

```python
# libi_modes/registry.py
"""State -> branch factory mapping, in Priorities-selector order. INSTRUCTION.md: 'registry.py의
매핑은 2단계 웹 시각화에서 상태 -> 표시할 서브트리로 그대로 재사용한다. 별도 매핑 테이블을 만들지
않는다.' — Stage 2 must import BRANCH_ORDER from this module rather than redefining it."""

from libi_modes.branches import (
    charging, error, idle, interacting, patrol, returning, security_patrol, working,
)

BRANCH_ORDER = [
    "ERROR", "RETURNING", "CHARGING", "WORKING", "INTERACTING",
    "SECURITY_PATROL", "PATROL", "IDLE",
]


def build_branches(params: dict, drivers: dict) -> dict:
    return {
        "ERROR": error.create(params),
        "RETURNING": returning.create(params, drivers["return_arm"], drivers["return_dock"]),
        "CHARGING": charging.create(params),
        "WORKING": working.create(params, drivers["nav"], drivers["arm"]),
        "INTERACTING": interacting.create(params),
        "SECURITY_PATROL": security_patrol.create(params, drivers["security_patrol"]),
        "PATROL": patrol.create(params, drivers["patrol"]),
        "IDLE": idle.create(params),
    }
```

```python
# libi_modes/tree.py
import py_trees

from libi_modes.common.topics2bb import Topics2BB
from libi_modes.registry import BRANCH_ORDER, build_branches


def build_root(params: dict, drivers: dict, providers: dict) -> py_trees.behaviour.Behaviour:
    """Root -> Parallel(Topics2BB, Priorities). Priorities is a memory=False Selector over the
    8 branches in BRANCH_ORDER, ending in Running() so the tree is always alive even if no
    IsMode guard matches on a given tick (INSTRUCTION.md 트리 죽음 방지)."""
    branches = build_branches(params, drivers)
    priorities = py_trees.composites.Selector(
        name="Priorities",
        memory=False,
        children=[branches[state] for state in BRANCH_ORDER] + [py_trees.behaviours.Running(name="NoBranchMatched")],
    )
    return py_trees.composites.Parallel(
        name="Root",
        policy=py_trees.common.ParallelPolicy.SuccessOnAll(synchronise=False),
        children=[Topics2BB(providers), priorities],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tree_integration.py -v`
Expected: `3 passed`

- [ ] **Step 5: Run the full test suite for this package**

Run: `python3 -m pytest test/ -v`
Expected: all tests from Tasks 3–12 pass (34 tests total: 2+6+1+5+6+5+4+6+3+3 = 41 — recount against actual `pytest --collect-only -q` output and adjust this expected count if it drifts before treating the run as green).

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/tree.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_tree_integration.py
git commit -m "feat: assemble libi_modes registry + root tree, wire all 8 branches"
```

---

## Deferred (explicitly out of scope for this plan)

- **`main.py`** (real `rclpy` node: wires `Topics2BB` providers and branch `drivers` to actual ROS2 topics/services/robot_agent HTTP calls, ticks the tree on a timer, publishes `current_mode` for Stage 2/3 to consume). Blocked on: the mission-PC `ROS_DOMAIN_ID` and FMS-side `domain_bridge` config decision (open in chat — user said "다른 걸로 할 건데... FMS에서 ros_domain_bridge로 할 것 같아" but the exact domain number and bridge YAML aren't decided yet).
- **`ArmExec` internal pick/place subtree** — INSTRUCTION.md marks this "작성 예정. 8개 브랜치 설계 완료 후 착수" — not designed yet, so nothing to implement.
- Stage 2 (FMS panel) and Stage 3 (LED) — separate plans, see `2026-07-20-fms-fsm-bt-panel.md` and `2026-07-20-libi-led-state.md`.
