# libi_perception 관리자 추종 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-triggered person following ("관리자 추종") to LIBI — a `libi_perception` ROS2 package ported from `arte_libi_perception/follower_control`, whose recovery order is expressed as real py_trees structure, exposed to `libi_modes`' WORKING branch as an opaque `FollowExec` leaf, triggered from a libi_gui admin button that goes through FMS and waits for approval, and fed by the YOLO perception pipeline relocated into `aba_ai_service`.

**Architecture:** `libi_perception` holds exactly two behaviours plus a thin switch between them — (1) `TrackingController`, a numeric PID + LiDAR control action (deliberately **not** a BT: it is a 20 Hz control loop, not a decision structure), and (2) `recovery_bt`, a py_trees tree whose `Sequence(memory=True)` **is** the recovery order (Hold → Scan1 → Turn180 → Scan2 → GiveUp) with a `CheckReacquired` interrupt above it. A 3-state `FollowSwitch` picks which one runs. `libi_modes` never sees any of this: its `FollowExec` leaf talks to the whole package through the same `start()/poll()/stop()` driver contract already used by `NavigationExec`/`ArmExec`, so the follower's internals can keep changing without touching the mission FSM.

**Tech Stack:** Python 3.12, ROS2 Jazzy (ament_python), `py_trees` 2.4.0, `pytest` 7.4.4. No new system packages required (all verified installed — see Global Constraints).

## Global Constraints

- **Repo boundary.** `arte_libi_perception` at `/home/asd/personal_repo/arte_libi_perception` is a **separate git repo**. This plan **copies** code out of it into `aba_project`. No task commits anything inside `arte_libi_perception`; it is read-only reference. Every `git add`/`git commit` below runs from the `aba_project` root.
- **Package location (settled with user in chat):** `aba_controller/libi_modes/ros_ws/src/libi_perception/` — a *second* ament_python package inside the **same** `libi_modes/ros_ws` colcon workspace created by `2026-07-20-libi-modes-fsm-bt.md` Task 2. (Deployment-target tension: this node needs `/scan` and `/cmd_vel`, which live on the driving Pi, not the mission PC — flagged in "Deferred / open decisions".)
- **The follow command string is `"follow_admin"`.** Used identically in `libi_modes` blackboard `active_command`, the FMS request payload, and the GUI. Stated once here; every task below uses it verbatim.
- **`transitions` must go.** INSTRUCTION.md: "별도 FSM 라이브러리(SMACH, YASMIN 등)를 사용하지 않는다". `follower_control/state_machine.py` is the only `transitions` user in the whole design (verified: `grep -rn transitions follower_control/` matches that one file, lines 1 and 11). Task 6 replaces it. `package.xml` never declared it — only `setup.py`'s `install_requires` — so removal is a one-line change there.
- **Recovery behaviour must not drift.** The port keeps `search_planner.search_command()` as a pure reference oracle, and Task 5 adds an equivalence test asserting the decomposed tree publishes bit-identical `angular_z` to it across the timeline at the real 20 Hz tick rate. (This is not aspirational — the decomposition and this equivalence were prototyped and verified before writing this plan; see the verification note in Task 5.)
- **Verified baseline:** `cd /home/asd/personal_repo/arte_libi_perception/follower_control && python3 -m pytest tests/ -q` → **`36 passed`** (actually executed 2026-07-20). Every port task below must keep the corresponding tests green.
- **Environment is ready — no install task exists in this plan.** Verified on this machine: ROS2 Jazzy at `/opt/ros/jazzy`, `colcon` at `/usr/bin/colcon`, and `ros-jazzy-py-trees` 2.4.0 / `-ros` 2.4.0 / `-ros-interfaces` 2.1.1 / `-ros-viewer` 0.2.5 / `ros-jazzy-domain-bridge` 0.5.0 all installed. ROS2 is **not sourced by default** — prefix ROS/colcon commands with `source /opt/ros/jazzy/setup.bash`.
- **Honest test scope.** Tasks 2–7 are pure-Python and fully verifiable here. Task 8 (`colcon build`) is verifiable here. Anything touching a real camera, LiDAR, motor, or robot network is **not** verifiable in this environment and is marked "verify on target machine" — never report it as tested.
- **Service independence** (project CLAUDE.md): `aba_fms_service`, `aba_controller`, and `aba_ai_service` are separately owned. Tasks 9–11 each touch exactly one of them and change nothing else.

---

### Task 1: Package skeleton for `libi_perception`

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/package.xml`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/setup.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/setup.cfg`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/pytest.ini`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/resource/libi_perception`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/__init__.py`
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/tests/__init__.py`

**Interfaces:** None — scaffolding. Later tasks import `libi_perception.*`.

- [ ] **Step 1: Record the pre-port baseline**

Run: `cd /home/asd/personal_repo/arte_libi_perception/follower_control && python3 -m pytest tests/ -q`
Expected: `36 passed`

Write that number down. It is the regression reference for Tasks 2–7. If it is not 36 on your machine, the upstream repo moved — reconcile before porting, do not proceed on a guess.

- [ ] **Step 2: `package.xml`** (mirrors the source package, minus `transitions`, plus `py_trees`)

```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>libi_perception</name>
  <version>0.1.0</version>
  <description>LIBI 사람 추종 — PID+LiDAR 추종 동작과 py_trees 회복 BT.</description>
  <maintainer email="dlrkdxor0821@gmail.com">leekt</maintainer>
  <license>MIT</license>

  <exec_depend>rclpy</exec_depend>
  <exec_depend>sensor_msgs</exec_depend>
  <exec_depend>geometry_msgs</exec_depend>
  <exec_depend>python3-py-trees</exec_depend>

  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

- [ ] **Step 3: `setup.py`** (note: no `transitions` in `install_requires` — that is the point of Task 6)

```python
from setuptools import find_packages, setup

package_name = 'libi_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'py_trees'],
    zip_safe=True,
    maintainer='leekt',
    maintainer_email='dlrkdxor0821@gmail.com',
    description='LIBI 사람 추종 (PID + LiDAR + 회복 BT).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'follow_node = libi_perception.follow_node:main',
        ],
    },
)
```

- [ ] **Step 4: `setup.cfg`**

```ini
[develop]
script_dir=$base/lib/libi_perception
[install]
install_scripts=$base/lib/libi_perception
```

- [ ] **Step 5: `pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 6: markers and package dirs**

```bash
cd /home/asd/personal_repo/aba_project
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_perception/resource
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception
mkdir -p aba_controller/libi_modes/ros_ws/src/libi_perception/tests
touch aba_controller/libi_modes/ros_ws/src/libi_perception/resource/libi_perception
touch aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/__init__.py
touch aba_controller/libi_modes/ros_ws/src/libi_perception/tests/__init__.py
```

- [ ] **Step 7: Verify layout**

Run: `find aba_controller/libi_modes/ros_ws/src/libi_perception -type f | sort`
Expected: exactly `package.xml`, `pytest.ini`, `resource/libi_perception`, `setup.cfg`, `setup.py`, `libi_perception/__init__.py`, `tests/__init__.py`.

- [ ] **Step 8: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/package.xml \
        aba_controller/libi_modes/ros_ws/src/libi_perception/setup.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/setup.cfg \
        aba_controller/libi_modes/ros_ws/src/libi_perception/pytest.ini \
        aba_controller/libi_modes/ros_ws/src/libi_perception/resource/libi_perception \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/__init__.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/__init__.py
git commit -m "feat: scaffold libi_perception ament_python package"
```

---

### Task 2: Port config + pure control modules (`config`, `detection`, `pid`, `lidar_avoidance`, `tracking_controller`)

**Files:**
- Create: `.../libi_perception/libi_perception/config.py`
- Create: `.../libi_perception/libi_perception/detection.py`
- Create: `.../libi_perception/libi_perception/pid.py`
- Create: `.../libi_perception/libi_perception/lidar_avoidance.py`
- Create: `.../libi_perception/libi_perception/tracking_controller.py`
- Test: `.../libi_perception/tests/test_pid.py`, `tests/test_detection.py`, `tests/test_lidar_avoidance.py`, `tests/test_tracking_controller.py`

(Base path for every `...` in this plan: `aba_controller/libi_modes/ros_ws/src/libi_perception`.)

**Interfaces:**
- Produces:
  - `config` — module of tuning constants (values copied verbatim from the source; changing any of them is a tuning decision, not a port decision).
  - `Detection(cx, cy, area, bbox, track_id, is_owner, confidence, is_predicted)` dataclass + `detection_from_dict(d) -> Detection | None`.
  - `clamp(v, lo, hi)`, `FollowPID(cfg)` with `.compute(cx, area, dt) -> (lin, ang)` and `.reset()`.
  - `apply_avoidance(linear_x, angular_z, scan, cfg) -> (lin, ang)`.
  - `TrackingController(publish, cfg)` with `.step(detection, scan, dt)` and `.reset()`; exposes `.last_direction` (the LKD used to seed recovery).

- [ ] **Step 1: Copy the four test files as-is, rewriting only the import root**

```bash
cd /home/asd/personal_repo/aba_project
SRC=/home/asd/personal_repo/arte_libi_perception/follower_control/tests
DST=aba_controller/libi_modes/ros_ws/src/libi_perception/tests
for t in test_pid test_detection test_lidar_avoidance test_tracking_controller; do
  sed 's/follower_control/libi_perception/g' "$SRC/$t.py" > "$DST/$t.py"
done
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_perception && python3 -m pytest tests/ -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'libi_perception.pid'` (and siblings).

- [ ] **Step 3: `config.py`** — verbatim constants

```python
# All values are reference starting points for tuning — not hard requirements.
# Ported verbatim from arte_libi_perception/follower_control/follower_control/config.py.

# ---- distance PID (linear_x) ----
TARGET_SIZE = 360.0            # sqrt(area) setpoint
KP_DIST = 0.0030
KI_DIST = 0.0001
KD_DIST = 0.0
INTEGRAL_DIST_CLAMP = 50.0
LINEAR_X_MAX = 0.12            # forward max (m/s)
LINEAR_X_REVERSE_MAX = 0.06   # reverse max (m/s) — smaller: LiDAR blind rear

# ---- bearing PID (angular_z) ----
IMAGE_WIDTH = 640
KP_ANGLE = 0.0010
KI_ANGLE = 0.0
KD_ANGLE = 0.0
INTEGRAL_ANGLE_CLAMP = 200.0
ANGLE_DEADZONE = 45.0         # px
ANGULAR_Z_MAX = 0.60          # rad/s
ANGULAR_SMOOTHING = 0.3       # low-pass: 0=frozen, 1=no smoothing

# ---- LiDAR avoidance ----
MIN_DIST = 0.20               # front-arc slowdown threshold (m)
AVOID_DIST = 0.40             # side-arc shy-away threshold (m)
AVOID_KP = 0.50
FRONT_ARC_DEG = 15            # +/- degrees around 0 (front)
SIDE_ARC = (20, 71)          # degrees range for a side arc (start, stop-exclusive)

# ---- miss / search ----
N_MISS_FRAMES = 40            # consecutive None before TRACKING -> SEARCHING
SEARCH_HOLD_SEC = 10.0        # phase 1: hold/wait before scanning
SEARCH_SCAN_SEC = 4.0         # duration of a +/-30 deg scan sweep
ANGULAR_Z_SEARCH = 0.35       # rad/s during search rotation
SEARCH_TURN_ANGLE = 3.14159   # phase 2: ~180 deg turn (radians)

# ---- loop ----
TICK_HZ = 20.0
FRAME_DT = 0.05               # nominal seconds per tick

# ---- transport ----
DETECTION_TCP_HOST = '0.0.0.0'
DETECTION_TCP_PORT = 6000
SCAN_TOPIC = '/scan'
CMD_VEL_TOPIC = '/cmd_vel'
```

- [ ] **Step 4: `detection.py`**

```python
from dataclasses import dataclass
from typing import Tuple


@dataclass
class Detection:
    cx: float
    cy: float
    area: float
    bbox: Tuple[float, float, float, float]
    track_id: int
    is_owner: bool
    confidence: float
    is_predicted: bool


def detection_from_dict(d):
    if d is None:
        return None
    return Detection(
        cx=d['cx'], cy=d['cy'], area=d['area'], bbox=tuple(d['bbox']),
        track_id=d['track_id'], is_owner=d['is_owner'],
        confidence=d['confidence'], is_predicted=d['is_predicted'],
    )
```

- [ ] **Step 5: `pid.py`**

```python
import math


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FollowPID:
    """Distance PID (sqrt-area) -> linear_x (with reverse); bearing PID (cx) -> angular_z."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self._i_size = 0.0
        self._prev_size = 0.0
        self._i_cx = 0.0
        self._prev_cx = 0.0
        self._ang = 0.0

    def compute(self, cx, area, dt):
        cfg = self.cfg
        dt = dt if dt > 0 else 1e-3

        # distance -> linear_x
        size = math.sqrt(max(0.0, area))
        e = cfg.TARGET_SIZE - size
        self._i_size = clamp(self._i_size + e * dt,
                             -cfg.INTEGRAL_DIST_CLAMP, cfg.INTEGRAL_DIST_CLAMP)
        d = (e - self._prev_size) / dt
        self._prev_size = e
        lin = cfg.KP_DIST * e + cfg.KI_DIST * self._i_size + cfg.KD_DIST * d
        lin = clamp(lin, -cfg.LINEAR_X_REVERSE_MAX, cfg.LINEAR_X_MAX)

        # bearing -> angular_z
        e_cx = (cfg.IMAGE_WIDTH / 2.0) - cx
        if abs(e_cx) < cfg.ANGLE_DEADZONE:
            e_cx = 0.0
        self._i_cx = clamp(self._i_cx + e_cx * dt,
                           -cfg.INTEGRAL_ANGLE_CLAMP, cfg.INTEGRAL_ANGLE_CLAMP)
        d_cx = (e_cx - self._prev_cx) / dt
        self._prev_cx = e_cx
        target_ang = (cfg.KP_ANGLE * e_cx + cfg.KI_ANGLE * self._i_cx
                      + cfg.KD_ANGLE * d_cx)
        s = cfg.ANGULAR_SMOOTHING
        self._ang = s * target_ang + (1.0 - s) * self._ang
        ang = clamp(self._ang, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

        return lin, ang
```

- [ ] **Step 6: `lidar_avoidance.py`**

```python
from .pid import clamp


def apply_avoidance(linear_x, angular_z, scan, cfg):
    """Post-process PID output with LiDAR: front slowdown + side shy-away."""
    if not scan:
        return linear_x, angular_z
    n = len(scan)
    step = n / 360.0

    def arc_min(deg_iter):
        idx = [int(i * step) % n for i in deg_iter]
        vals = [scan[i] for i in idx if scan[i] and scan[i] > 0.05]
        return min(vals) if vals else 10.0

    # front arc: proportional slowdown
    front = arc_min(range(-cfg.FRONT_ARC_DEG, cfg.FRONT_ARC_DEG + 1))
    if front < cfg.MIN_DIST:
        linear_x *= max(0.0, front / cfg.MIN_DIST)

    # side arcs: shy away
    lo, hi = cfg.SIDE_ARC
    left = arc_min(range(lo, hi))
    right = arc_min(range(360 - hi + 1, 360 - lo + 1))
    steer = 0.0
    if left < cfg.AVOID_DIST:
        steer -= (cfg.AVOID_DIST - left) * cfg.AVOID_KP    # wall on left -> steer right
    if right < cfg.AVOID_DIST:
        steer += (cfg.AVOID_DIST - right) * cfg.AVOID_KP   # wall on right -> steer left
    angular_z = clamp(angular_z + steer, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

    return linear_x, angular_z
```

- [ ] **Step 7: `tracking_controller.py`** — this is behaviour (1), the follow ACTION. Deliberately not a BT.

```python
from .pid import FollowPID
from .lidar_avoidance import apply_avoidance


class TrackingController:
    """PID + LiDAR avoidance -> publish cmd_vel. Records last turn direction (LKD).

    This is the follow ACTION, not a behaviour tree: it is a numeric 20 Hz control
    loop with no decisions to express as tree structure. The recovery BT
    (recovery_bt.py) is the part that has an order worth modelling as a tree.
    """

    def __init__(self, publish, cfg):
        self.publish = publish
        self.cfg = cfg
        self.pid = FollowPID(cfg)
        self.last_direction = None

    def step(self, detection, scan, dt):
        lin, ang = self.pid.compute(detection.cx, detection.area, dt)
        lin, ang = apply_avoidance(lin, ang, scan, self.cfg)
        if abs(ang) > 0.01:
            self.last_direction = 1.0 if ang > 0 else -1.0
        self.publish(lin, ang)

    def reset(self):
        self.pid.reset()
        self.last_direction = None
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: all four ported test files pass. Cross-check the count against the same four files upstream:
`cd /home/asd/personal_repo/arte_libi_perception/follower_control && python3 -m pytest tests/test_pid.py tests/test_detection.py tests/test_lidar_avoidance.py tests/test_tracking_controller.py -q`
The two counts must match exactly — that is the port's correctness criterion.

- [ ] **Step 9: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/config.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/detection.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/pid.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/lidar_avoidance.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/tracking_controller.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_pid.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_detection.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_lidar_avoidance.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_tracking_controller.py
git commit -m "feat: port PID/LiDAR/tracking control core into libi_perception"
```

---

### Task 3: Port transport modules (`detection_receiver`, `tcp_detection_source`)

**Files:**
- Create: `.../libi_perception/libi_perception/detection_receiver.py`
- Create: `.../libi_perception/libi_perception/tcp_detection_source.py`
- Test: `.../libi_perception/tests/test_detection_receiver.py`

**Interfaces:**
- Consumes: `detection_from_dict` (Task 2).
- Produces:
  - `DetectionReceiver(source)` with `.update()` (drains `source.poll()`, keeps the latest) and `.latest() -> Detection | None`.
  - `TcpDetectionSource(host, port)` — TCP server accepting newline-delimited Detection JSON, with `.poll()` draining the buffer. This is the robot-side endpoint that `aba_ai_service` connects to in Task 11.

- [ ] **Step 1: Copy the test, rewriting the import root**

```bash
cd /home/asd/personal_repo/aba_project
sed 's/follower_control/libi_perception/g' \
  /home/asd/personal_repo/arte_libi_perception/follower_control/tests/test_detection_receiver.py \
  > aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_detection_receiver.py
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_perception && python3 -m pytest tests/test_detection_receiver.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.detection_receiver'`

- [ ] **Step 3: `detection_receiver.py`**

```python
from .detection import detection_from_dict


class DetectionReceiver:
    """Holds the latest owner Detection parsed from incoming JSON dicts.

    `source.poll()` returns a list of payloads received since last poll;
    each payload is a Detection dict, or None meaning 'no owner this frame'.
    Concrete TCP socket wraps this small interface (integration-tested)."""

    def __init__(self, source):
        self._source = source
        self._latest = None

    def update(self):
        for payload in self._source.poll():
            self._latest = detection_from_dict(payload)

    def latest(self):
        return self._latest
```

- [ ] **Step 4: `tcp_detection_source.py`**

```python
import json
import socket
import threading


class TcpDetectionSource:
    """TCP server that receives newline-delimited Detection JSON from the AI
    server and buffers payloads for the follow loop. Non-owner frames arrive as
    the JSON literal `null`. `.poll()` drains the buffer."""

    def __init__(self, host, port):
        self._buf = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            conn, _ = self._sock.accept()
            with conn:
                buffer = b''
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        if not line.strip():
                            continue
                        payload = json.loads(line.decode('utf-8'))
                        with self._lock:
                            self._buf.append(payload)

    def poll(self):
        with self._lock:
            out, self._buf = self._buf, []
        return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_detection_receiver.py -q`
Expected: same pass count as `pytest tests/test_detection_receiver.py -q` upstream.

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/detection_receiver.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/tcp_detection_source.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_detection_receiver.py
git commit -m "feat: port detection transport into libi_perception"
```

---

### Task 4: Port the reference search timeline (`search_planner`)

**Files:**
- Create: `.../libi_perception/libi_perception/search_planner.py`
- Test: `.../libi_perception/tests/test_search_planner.py`

**Interfaces:**
- Produces: `search_command(elapsed, cfg, lkd=1.0) -> (angular_z, done)`.

**Why this survives the decomposition.** After Task 5 the recovery *tree* no longer calls this function — the order lives in tree structure instead. It is kept deliberately as a **pure reference oracle**: Task 5's equivalence test asserts the tree reproduces this function's output exactly, which is what makes the decomposition provably behaviour-preserving rather than merely plausible. Deleting it would throw away the regression anchor.

⚠️ **Blocked on an open decision** — see "Deferred / open decisions": this file encodes the HOLD-first timeline, while `follower_BT/recovery.py` upstream encodes a different PEEK-first one. Confirm which is canonical **before** running Task 5. If PEEK-first wins, this file and Task 5's phase list change together, and the equivalence test still holds them consistent.

- [ ] **Step 1: Copy the test**

```bash
cd /home/asd/personal_repo/aba_project
sed 's/follower_control/libi_perception/g' \
  /home/asd/personal_repo/arte_libi_perception/follower_control/tests/test_search_planner.py \
  > aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_search_planner.py
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_perception && python3 -m pytest tests/test_search_planner.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.search_planner'`

- [ ] **Step 3: Implement**

```python
def search_command(elapsed, cfg, lkd=1.0):
    """3-phase open-loop recovery given elapsed seconds since search start.

    Reference oracle only — the live recovery order lives in recovery_bt.py as
    tree structure. tests/test_recovery_bt.py asserts the tree matches this
    function exactly, so this stays the single source of truth for the timeline.

    Timeline:
      [0, HOLD)                      -> hold (0)
      [HOLD, HOLD+SCAN)              -> scan sweep (ANGULAR_Z_SEARCH * lkd)
      [.., + TURN)                   -> ~180 deg turn (ANGULAR_Z_SEARCH)
      [.., + SCAN)                   -> scan sweep (ANGULAR_Z_SEARCH * -lkd)
      after                          -> done (0)
    """
    hold = cfg.SEARCH_HOLD_SEC
    scan = cfg.SEARCH_SCAN_SEC
    turn = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH
    t_scan1_end = hold + scan
    t_turn_end = t_scan1_end + turn
    t_scan2_end = t_turn_end + scan

    if elapsed < hold:
        return 0.0, False
    if elapsed < t_scan1_end:
        return cfg.ANGULAR_Z_SEARCH * lkd, False
    if elapsed < t_turn_end:
        return cfg.ANGULAR_Z_SEARCH, False
    if elapsed < t_scan2_end:
        return cfg.ANGULAR_Z_SEARCH * -lkd, False
    return 0.0, True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_search_planner.py -q`
Expected: `5 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/search_planner.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_search_planner.py
git commit -m "feat: port search timeline reference oracle into libi_perception"
```

---

### Task 5: Recovery BT — express the order as tree structure ★ core of this plan

**Files:**
- Create: `.../libi_perception/libi_perception/recovery_bt.py`
- Test: `.../libi_perception/tests/test_recovery_bt.py`

**Interfaces:**
- Consumes: `search_planner.search_command` (Task 4, test-only), `config` (Task 2).
- Produces:
  - `SearchContext(get_detection, publish, cfg, now, lkd=1.0)` — injected deps; `.start` is the shared search-start timestamp.
  - `create_searching_tree(ctx) -> Selector` named `BT_Searching`.
  - `tick_tree(root) -> Status`.

**What changes and why.** Upstream `bt_searching.py` is already py_trees, but its recovery *order* is not in the tree: `SearchMotion` is a single leaf whose `update()` delegates the entire timeline to a time-indexed `if`-chain. This task replaces that one leaf with a `Sequence(memory=True)` of one leaf per phase, so the order is readable and editable as tree structure — which is the whole point of the user's request. `CheckReacquired` stays exactly where it is, above the sequence in a `memory=False` Selector, so a reacquire still interrupts from *any* phase.

**Design note — absolute windows, not per-phase durations.** Each phase leaf owns a `[begin, end)` window measured from the shared `ctx.start`, with the builder computing cumulative offsets from per-phase durations. The tempting alternative (each leaf starting its own timer on `initialise()`) is *not* equivalent: with sparse or jumped ticks it advances only one phase per tick and lets the total recovery time drift. Absolute windows reproduce the original timeline exactly.

**Verification status (done before this plan was written, not a prediction):** this exact decomposition was prototyped and run against the upstream tests. Result — all 3 of `test_bt_searching.py` pass **unmodified**, and an added equivalence check found **zero** angular-velocity mismatches versus `search_command()` sweeping `t = 0 → 30 s` at the real 0.05 s tick for both `lkd = +1` and `lkd = -1`. The code below is that verified prototype.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recovery_bt.py
from types import SimpleNamespace

import py_trees

from libi_perception.recovery_bt import (
    SearchContext, create_searching_tree, tick_tree,
)
from libi_perception.search_planner import search_command


def _cfg(**over):
    base = dict(SEARCH_HOLD_SEC=10.0, SEARCH_SCAN_SEC=4.0,
                ANGULAR_Z_SEARCH=0.35, SEARCH_TURN_ANGLE=3.14159)
    base.update(over)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Pub:
    def __init__(self):
        self.calls = []

    def __call__(self, lin, ang):
        self.calls.append((lin, ang))


# ---- behaviour preserved from the original BT_Searching ----

def test_reacquire_returns_success():
    pub = _Pub()
    ctx = SearchContext(get_detection=lambda: object(), publish=pub,
                        cfg=_cfg(), now=_Clock())
    root = create_searching_tree(ctx)
    assert tick_tree(root) == py_trees.common.Status.SUCCESS


def test_scanning_publishes_rotation_and_runs():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub,
                        cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)               # establishes start time
    clock.t = 12.0                # into scan phase
    assert tick_tree(root) == py_trees.common.Status.RUNNING
    assert pub.calls[-1][1] != 0.0   # rotating


def test_exhausted_returns_failure():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub,
                        cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 10_000.0
    assert tick_tree(root) == py_trees.common.Status.FAILURE


# ---- the order is really in the tree, not hidden in one leaf ----

def test_recovery_order_is_tree_structure():
    ctx = SearchContext(get_detection=lambda: None, publish=_Pub(),
                        cfg=_cfg(), now=_Clock())
    root = create_searching_tree(ctx)
    phases = [c for c in root.children if c.name == 'SearchPhases'][0]
    assert [c.name for c in phases.children] == [
        'Hold', 'Scan1', 'Turn180', 'Scan2', 'GiveUp',
    ]
    assert phases.memory is True      # a finished phase must not restart
    assert root.memory is False       # reacquire must be re-checked every tick


def test_reacquire_interrupts_from_any_phase():
    clock, pub = _Clock(), _Pub()
    visible = {'v': False}
    ctx = SearchContext(get_detection=lambda: object() if visible['v'] else None,
                        publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    for t in (0.0, 12.0, 20.0):        # hold, scan1, turn180
        clock.t = t
        assert tick_tree(root) == py_trees.common.Status.RUNNING
        visible['v'] = True
        assert tick_tree(root) == py_trees.common.Status.SUCCESS
        visible['v'] = False


# ---- equivalence with the reference oracle ----

def test_angular_output_matches_search_command_over_timeline():
    for lkd in (1.0, -1.0):
        cfg, clock, pub = _cfg(), _Clock(), _Pub()
        ctx = SearchContext(lambda: None, pub, cfg, clock, lkd=lkd)
        root = create_searching_tree(ctx)
        mismatches = []
        t = 0.0
        while t < 30.0:
            clock.t = t
            before = len(pub.calls)
            status = tick_tree(root)
            exp_ang, exp_done = search_command(t, cfg, lkd)
            if exp_done:
                assert status == py_trees.common.Status.FAILURE, f'lkd={lkd} t={t}'
            else:
                got = pub.calls[-1][1] if len(pub.calls) > before else None
                if got is None or abs(got - exp_ang) > 1e-9:
                    mismatches.append((t, exp_ang, got))
            t += 0.05          # the real TICK_HZ = 20
        assert not mismatches, f'lkd={lkd} mismatches: {mismatches[:5]}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_recovery_bt.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.recovery_bt'`

- [ ] **Step 3: Implement**

```python
"""Recovery behaviour tree — the search ORDER lives here as tree structure.

    BT_Searching (Selector, memory=False)
    ├── CheckReacquired          owner visible again -> SUCCESS (interrupts any phase)
    └── SearchPhases (Sequence, memory=True)
        ├── Hold                 stay still, give the owner a moment to reappear
        ├── Scan1                sweep toward the last-known direction
        ├── Turn180              turn around
        ├── Scan2                sweep the other way
        └── GiveUp               stop and report FAILURE

Each phase owns a [begin, end) window measured from the shared ctx.start, with
the builder computing cumulative offsets from per-phase durations. Per-phase
timers started on initialise() would NOT be equivalent: on a sparse or jumped
tick they advance only one phase and let total recovery time drift.
tests/test_recovery_bt.py pins the equivalence against search_planner.
"""
import py_trees
from py_trees.common import Status


class SearchContext:
    """Injected dependencies for the searching tree (no ROS)."""

    def __init__(self, get_detection, publish, cfg, now, lkd=1.0):
        self.get_detection = get_detection
        self.publish = publish
        self.cfg = cfg
        self.now = now
        self.lkd = lkd
        self.start = None


class CheckReacquired(py_trees.behaviour.Behaviour):
    """SUCCESS the moment the owner is visible again. Sits above the phase
    sequence in a memory=False Selector, so it is re-evaluated every tick and
    can cut recovery short from any phase."""

    def __init__(self, ctx):
        super().__init__(name='CheckReacquired')
        self.ctx = ctx

    def update(self):
        if self.ctx.get_detection() is not None:
            return Status.SUCCESS
        return Status.FAILURE


class SearchPhase(py_trees.behaviour.Behaviour):
    """Publishes a fixed angular velocity while elapsed time is inside [begin, end).

    SUCCESS once the window has passed, which advances the memory=True Sequence
    to the next phase. `angular_fn` is a callable so lkd is read at tick time."""

    def __init__(self, ctx, name, begin, end, angular_fn):
        super().__init__(name=name)
        self.ctx = ctx
        self.begin = begin
        self.end = end
        self.angular_fn = angular_fn

    def initialise(self):
        if self.ctx.start is None:
            self.ctx.start = self.ctx.now()

    def update(self):
        elapsed = self.ctx.now() - self.ctx.start
        if elapsed >= self.end:
            return Status.SUCCESS
        self.ctx.publish(0.0, self.angular_fn())
        return Status.RUNNING


class GiveUp(py_trees.behaviour.Behaviour):
    """Terminal phase: stop the robot and fail the tree so the caller can end
    the follow session."""

    def __init__(self, ctx):
        super().__init__(name='GiveUp')
        self.ctx = ctx

    def update(self):
        self.ctx.publish(0.0, 0.0)
        return Status.FAILURE


def create_searching_tree(ctx):
    cfg = ctx.cfg
    turn_sec = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH
    spec = [
        ('Hold', cfg.SEARCH_HOLD_SEC, lambda: 0.0),
        ('Scan1', cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * ctx.lkd),
        ('Turn180', turn_sec, lambda: cfg.ANGULAR_Z_SEARCH),
        ('Scan2', cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * -ctx.lkd),
    ]
    phases, offset = [], 0.0
    for name, duration, angular_fn in spec:
        phases.append(SearchPhase(ctx, name, offset, offset + duration, angular_fn))
        offset += duration

    body = py_trees.composites.Sequence(
        name='SearchPhases', memory=True, children=phases + [GiveUp(ctx)],
    )
    return py_trees.composites.Selector(
        name='BT_Searching', memory=False, children=[CheckReacquired(ctx), body],
    )


def tick_tree(root):
    root.tick_once()
    return root.status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recovery_bt.py -v`
Expected: `6 passed` — including `test_angular_output_matches_search_command_over_timeline`, which is the proof the decomposition changed structure without changing behaviour.

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/recovery_bt.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_recovery_bt.py
git commit -m "feat: express recovery order as py_trees structure (Hold/Scan/Turn/GiveUp phases)"
```

---

### Task 6: Thin switch — drop `transitions`

**Files:**
- Create: `.../libi_perception/libi_perception/switch.py`
- Test: `.../libi_perception/tests/test_switch.py`

**Interfaces:**
- Produces: `FollowSwitch()` with `.state` (`'TRACKING'|'SEARCHING'|'ENDED'`, initial `'TRACKING'`) and triggers `.lost()`, `.reacquired()`, `.search_failed()`, `.restart()`; illegal triggers raise `InvalidTransition`. Module constants `TRACKING`, `SEARCHING`, `ENDED`.

**Why.** This is the "thin switch" of the user's two-behaviours design: all it does is decide whether the tracking action or the recovery BT runs this tick. Upstream implements it with the `transitions` library, the design's only non-py_trees FSM, which INSTRUCTION.md rules out. Replacing it is behaviour-preserving — same states, same triggers, same rejection of illegal transitions — with the one visible difference that the raised exception is `InvalidTransition` instead of `transitions.MachineError`.

**Verification status:** prototyped and run before writing this plan — all 5 upstream `test_state_machine.py` cases pass against this implementation with only the expected-exception type adapted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_switch.py
import pytest

from libi_perception.switch import FollowSwitch, InvalidTransition


def test_initial_state_tracking():
    assert FollowSwitch().state == 'TRACKING'


def test_lost_then_reacquired():
    s = FollowSwitch()
    s.lost()
    assert s.state == 'SEARCHING'
    s.reacquired()
    assert s.state == 'TRACKING'


def test_search_failed_ends():
    s = FollowSwitch()
    s.lost()
    s.search_failed()
    assert s.state == 'ENDED'


def test_restart_from_ended():
    s = FollowSwitch()
    s.lost()
    s.search_failed()
    s.restart()
    assert s.state == 'TRACKING'


def test_invalid_transition_raises():
    s = FollowSwitch()
    with pytest.raises(InvalidTransition):
        s.reacquired()          # not valid from TRACKING


def test_no_transitions_library_dependency():
    """INSTRUCTION.md: 별도 FSM 라이브러리를 사용하지 않는다."""
    import inspect

    import libi_perception.switch as mod
    assert 'transitions' not in inspect.getsource(mod)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_switch.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.switch'`

- [ ] **Step 3: Implement**

```python
"""Thin switch between the two follow behaviours.

Replaces the `transitions`-backed ControlFSM: INSTRUCTION.md forbids a separate
FSM library, and this is a 3-state selector, not a state machine worth a
dependency. Same states, same triggers, same rejection of illegal transitions.
"""


class InvalidTransition(RuntimeError):
    """Raised when a trigger is not legal from the current state."""


TRACKING, SEARCHING, ENDED = 'TRACKING', 'SEARCHING', 'ENDED'

_TRANSITIONS = {
    ('lost', TRACKING): SEARCHING,
    ('reacquired', SEARCHING): TRACKING,
    ('search_failed', SEARCHING): ENDED,
    ('restart', ENDED): TRACKING,
}


class FollowSwitch:
    """Decides which behaviour runs: TRACKING -> tracking action,
    SEARCHING -> recovery BT, ENDED -> neither (session over)."""

    def __init__(self):
        self.state = TRACKING

    def _fire(self, trigger):
        try:
            self.state = _TRANSITIONS[(trigger, self.state)]
        except KeyError:
            raise InvalidTransition(f"{trigger!r} is not valid from {self.state!r}")

    def lost(self):
        self._fire('lost')

    def reacquired(self):
        self._fire('reacquired')

    def search_failed(self):
        self._fire('search_failed')

    def restart(self):
        self._fire('restart')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_switch.py -q`
Expected: `6 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/switch.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_switch.py
git commit -m "feat: replace transitions FSM with dependency-free FollowSwitch"
```

---

### Task 7: `control_loop` — compose the two behaviours

**Files:**
- Create: `.../libi_perception/libi_perception/control_loop.py`
- Test: `.../libi_perception/tests/test_control_loop.py`

**Interfaces:**
- Consumes: `TrackingController` (Task 2), `recovery_bt` (Task 5), `FollowSwitch` (Task 6).
- Produces: `ControlLoop(get_detection, get_scan, publish, cfg, now=time.monotonic)` with `.tick()` and a read-only `.state` property proxying the switch. This is the object `follow_node` ticks at 20 Hz and the object Task 8's session wrapper drives.

**Verification status:** prototyped before writing this plan — all 4 upstream `test_control_loop.py` cases pass **unmodified** with both the decomposed recovery BT and the new switch composed in.

- [ ] **Step 1: Copy the test unmodified except the import root**

```bash
cd /home/asd/personal_repo/aba_project
sed 's/follower_control/libi_perception/g' \
  /home/asd/personal_repo/arte_libi_perception/follower_control/tests/test_control_loop.py \
  > aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_control_loop.py
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_perception && python3 -m pytest tests/test_control_loop.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.control_loop'`

- [ ] **Step 3: Implement**

```python
import time

import py_trees

from .recovery_bt import SearchContext, create_searching_tree, tick_tree
from .switch import FollowSwitch
from .tracking_controller import TrackingController


class ControlLoop:
    """Runs exactly one of the two behaviours per tick, chosen by FollowSwitch:

      TRACKING  -> TrackingController (PID + LiDAR, numeric control)
      SEARCHING -> recovery BT (order expressed as tree structure)
      ENDED     -> nothing; the session is over

    No ROS here — the node injects get_detection/get_scan/publish."""

    def __init__(self, get_detection, get_scan, publish, cfg, now=time.monotonic):
        self.get_detection = get_detection
        self.get_scan = get_scan
        self.publish = publish
        self.cfg = cfg
        self.now = now
        self.switch = FollowSwitch()
        self.tracker = TrackingController(publish, cfg)
        self.miss = 0
        self._search_ctx = None
        self._search_tree = None

    @property
    def state(self):
        return self.switch.state

    def _start_search(self):
        lkd = self.tracker.last_direction or 1.0
        self._search_ctx = SearchContext(self.get_detection, self.publish,
                                         self.cfg, self.now, lkd=lkd)
        # Stamp the search start when SEARCHING begins, not on the tree's first
        # tick — those can be ticks apart, which would understate elapsed time.
        self._search_ctx.start = self.now()
        self._search_tree = create_searching_tree(self._search_ctx)

    def tick(self):
        if self.switch.state == 'TRACKING':
            det = self.get_detection()
            if det is not None:
                self.miss = 0
                self.tracker.step(det, self.get_scan(), self.cfg.FRAME_DT)
            else:
                self.miss += 1
                self.publish(0.0, 0.0)
                if self.miss >= self.cfg.N_MISS_FRAMES:
                    self.switch.lost()
                    self._start_search()
        elif self.switch.state == 'SEARCHING':
            status = tick_tree(self._search_tree)
            if status == py_trees.common.Status.SUCCESS:
                self.switch.reacquired()
                self.miss = 0
                self.tracker.reset()
            elif status == py_trees.common.Status.FAILURE:
                self.publish(0.0, 0.0)
                self.switch.search_failed()
        # ENDED: idle — the follow session is over.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_control_loop.py -q`
Expected: `4 passed`

- [ ] **Step 5: Run the whole ported suite and compare to baseline**

Run: `python3 -m pytest tests/ -q`
Expected: every ported test green. Compare against the `36 passed` baseline from Task 1: the total here should be **36 minus the `state_machine`/`bt_searching`/`control_node`-only cases that were replaced, plus the new `test_switch.py` (6) and `test_recovery_bt.py` (6)**. Run `python3 -m pytest tests/ --collect-only -q | tail -1` and reconcile the number explicitly — do not treat "it's green" as sufficient if the count is lower than expected, since a silently-uncopied test file also shows green.

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/control_loop.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_control_loop.py
git commit -m "feat: compose tracking action and recovery BT in libi_perception control loop"
```

---

### Task 8: ROS2 node + session control + `colcon build`

**Files:**
- Create: `.../libi_perception/libi_perception/scan_provider.py`
- Create: `.../libi_perception/libi_perception/cmd_publisher.py`
- Create: `.../libi_perception/libi_perception/follow_node.py`
- Test: `.../libi_perception/tests/test_follow_session.py`

**Interfaces:**
- Produces:
  - `ScanProvider(node, topic)` with `.get() -> list[float]`; `CmdPublisher(node, topic)` with `.publish(linear_x, angular_z)`.
  - `FollowSession(loop_factory)` — the ROS-free session state machine `FollowExec` (Task 9) ultimately drives: `.start()`, `.poll() -> "running"|"success"|"failure"`, `.stop()`. `poll()` maps loop state to the driver contract: `TRACKING`/`SEARCHING` → `"running"`, `ENDED` → `"failure"` (the owner was lost and recovery gave up), and `"success"` only after an explicit `.stop()` (an admin-follow session ends by being told to, never by completing on its own).
  - `follow_node:main()` — rclpy entry point ticking at `config.TICK_HZ`.

**Note on the driver contract.** `FollowSession` is deliberately a plain object with no ROS in it, so it is unit-testable here. Whether `libi_modes`' `FollowExec` reaches it in-process, over a ROS2 service, or over the domain bridge is a transport decision deferred with the rest of the ROS wiring (see "Deferred / open decisions") — exactly like the `nav`/`arm` drivers in the libi_modes plan, which are also injected rather than wired.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_follow_session.py
from libi_perception.follow_node import FollowSession


class _FakeLoop:
    def __init__(self):
        self.state = 'TRACKING'
        self.ticks = 0

    def tick(self):
        self.ticks += 1


def test_session_is_idle_before_start():
    session = FollowSession(lambda: _FakeLoop())
    assert session.poll() == 'failure'      # nothing running


def test_started_session_reports_running_and_ticks():
    loops = []

    def factory():
        loop = _FakeLoop()
        loops.append(loop)
        return loop

    session = FollowSession(factory)
    session.start()
    assert session.poll() == 'running'
    session.tick()
    assert loops[0].ticks == 1


def test_session_fails_when_recovery_gives_up():
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    loop.state = 'ENDED'
    assert session.poll() == 'failure'


def test_searching_still_counts_as_running():
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    loop.state = 'SEARCHING'
    assert session.poll() == 'running'


def test_stop_reports_success_and_halts_ticking():
    loop = _FakeLoop()
    session = FollowSession(lambda: loop)
    session.start()
    session.stop()
    assert session.poll() == 'success'
    session.tick()
    assert loop.ticks == 0, 'a stopped session must not keep driving the robot'


def test_restart_after_stop():
    session = FollowSession(lambda: _FakeLoop())
    session.start()
    session.stop()
    session.start()
    assert session.poll() == 'running'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_follow_session.py -q`
Expected: `ModuleNotFoundError: No module named 'libi_perception.follow_node'`

- [ ] **Step 3: `scan_provider.py` and `cmd_publisher.py`**

```python
# scan_provider.py
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanProvider:
    """Caches the latest /scan ranges as a plain list."""

    def __init__(self, node, topic):
        self._ranges = []
        node.create_subscription(LaserScan, topic, self._cb,
                                 qos_profile_sensor_data)

    def _cb(self, msg):
        self._ranges = list(msg.ranges)

    def get(self):
        return self._ranges
```

```python
# cmd_publisher.py
from geometry_msgs.msg import Twist


class CmdPublisher:
    """Publishes (linear_x, angular_z) as geometry_msgs/Twist."""

    def __init__(self, node, topic):
        self._pub = node.create_publisher(Twist, topic, 10)

    def publish(self, linear_x, angular_z):
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        self._pub.publish(msg)
```

- [ ] **Step 4: `follow_node.py`**

`FollowSession` is defined at module top so it imports without rclpy present; the rclpy imports live inside `main()` and the node class, mirroring how `fleet_telemetry.py` in `aba_fms_service` keeps its module top rclpy-free.

```python
"""ROS2 entry point for LIBI person-following.

FollowSession is ROS-free and testable on its own; the rclpy node below only
wires transports to it and ticks it. libi_modes' FollowExec sees nothing but
FollowSession's start()/poll()/stop() contract.
"""
from . import config
from .control_loop import ControlLoop
from .detection_receiver import DetectionReceiver
from .tcp_detection_source import TcpDetectionSource


class FollowSession:
    """start()/poll()/stop() wrapper around a ControlLoop, matching the driver
    contract libi_modes' _CommandDrivenAction expects.

    poll() mapping:
      not started / gave up -> 'failure'
      TRACKING or SEARCHING -> 'running'
      stopped on request    -> 'success'

    An admin-follow session never finishes by itself: it ends because an admin
    stopped it (success) or because recovery exhausted and the owner is gone
    (failure)."""

    def __init__(self, loop_factory):
        self._loop_factory = loop_factory
        self._loop = None
        self._stopped = False

    def start(self):
        self._loop = self._loop_factory()
        self._stopped = False

    def poll(self):
        if self._stopped:
            return 'success'
        if self._loop is None:
            return 'failure'
        return 'failure' if self._loop.state == 'ENDED' else 'running'

    def stop(self):
        self._loop = None
        self._stopped = True

    def tick(self):
        if self._loop is not None:
            self._loop.tick()


def main(args=None):
    import rclpy
    from rclpy.node import Node

    from .cmd_publisher import CmdPublisher
    from .scan_provider import ScanProvider

    class FollowNode(Node):
        def __init__(self):
            super().__init__('libi_perception')
            self._scan = ScanProvider(self, config.SCAN_TOPIC)
            self._cmd = CmdPublisher(self, config.CMD_VEL_TOPIC)
            self._receiver = DetectionReceiver(
                TcpDetectionSource(config.DETECTION_TCP_HOST,
                                   config.DETECTION_TCP_PORT))
            self.session = FollowSession(self._make_loop)
            self.session.start()
            self.create_timer(1.0 / config.TICK_HZ, self.session.tick)

        def _make_loop(self):
            return ControlLoop(
                get_detection=self._get_detection,
                get_scan=self._scan.get,
                publish=self._cmd.publish,
                cfg=config,
                now=lambda: self.get_clock().now().nanoseconds / 1e9,
            )

        def _get_detection(self):
            self._receiver.update()
            return self._receiver.latest()

    rclpy.init(args=args)
    node = FollowNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: `test_follow_session.py` contributes `6 passed`; the rest of the suite stays green.

- [ ] **Step 6: Build the workspace**

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_modes/ros_ws
colcon build --symlink-install --packages-select libi_perception
```
Expected: `Summary: 1 package finished`. (Verifiable on this machine — ROS2 Jazzy and colcon are installed; see Global Constraints.)

Then confirm the entry point registered:
```bash
source install/setup.bash
ros2 pkg executables libi_perception
```
Expected: `libi_perception follow_node`.

**Not verified here:** actually *running* `follow_node` needs `/scan` and `/cmd_vel` from a real robot. Marked for on-robot verification, not claimed as tested.

- [ ] **Step 7: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/scan_provider.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/cmd_publisher.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/follow_node.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_follow_session.py
git commit -m "feat: add libi_perception ROS2 node and follow session driver"
```

---

### Task 9: `FollowExec` — expose following to `libi_modes`

**Files:**
- Modify: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/working_actions.py` (add one class at end of file)
- Modify: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/working.py` (`create()` signature + `CommandDispatch` children)
- Modify: `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py` (`build_branches` WORKING line)
- Test: `aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_working.py` (add cases)

**Depends on:** `2026-07-20-libi-modes-fsm-bt.md` Tasks 10 and 12 being complete.

**Interfaces:**
- Consumes: `_CommandDrivenAction` from the libi_modes plan's Task 10.
- Produces: `FollowExec(driver, name=None)` — a `_CommandDrivenAction` handling `{"follow_admin"}`. `working.create(params, nav_driver, arm_driver, follow_driver, clock=time.monotonic)` gains a fourth positional driver. `registry.build_branches`'s `drivers` dict gains a `"follow"` key.

**Ordering constraint inside `CommandDispatch`.** The `Selector` is `memory=False` and each child returns FAILURE immediately when `active_command` is not one it handles, so the three exec leaves are mutually exclusive and their relative order does not affect which one runs. What *is* load-bearing: `Running(name="AwaitingCommand")` must stay **last**, since it unconditionally returns RUNNING and would mask every leaf after it. Add `FollowExec` before it.

- [ ] **Step 1: Write the failing tests** (append to `test/test_branch_working.py`; its existing `FakeDriver`, `_seed`, and `PARAMS` helpers are reused)

Every existing `_run(...)` call in that file must also gain the new driver argument. Update the helper in place:

```python
def _run(nav_driver, arm_driver, clock, follow_driver=None):
    root = working.create(PARAMS, nav_driver, arm_driver,
                          follow_driver or FakeDriver([]), clock=clock)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    reader = py_trees.blackboard.Client(name=f"reader-{id(root)}")
    reader.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
    return root.status, reader.get(Keys.CURRENT_MODE)
```

Then add:

```python
def test_working_dispatches_follow_admin_command():
    _seed(**{Keys.ACTIVE_COMMAND: "follow_admin"})
    nav, arm, follow = FakeDriver(["running"]), FakeDriver(["running"]), FakeDriver(["running"])
    status, mode = _run(nav, arm, clock=lambda: 1.0, follow_driver=follow)
    assert status == Status.RUNNING
    assert mode == "WORKING"
    assert follow.started and not nav.started and not arm.started


def test_follow_admin_does_not_trigger_nav_or_arm():
    """The three exec leaves must stay mutually exclusive."""
    _seed(**{Keys.ACTIVE_COMMAND: "navigate"})
    nav, arm, follow = FakeDriver(["running"]), FakeDriver(["running"]), FakeDriver(["running"])
    _run(nav, arm, clock=lambda: 1.0, follow_driver=follow)
    assert nav.started and not follow.started


def test_awaiting_command_stays_last_in_dispatch():
    """Running() masks anything after it — FollowExec must precede it."""
    from libi_modes.common.working_actions import FollowExec

    root = working.create(PARAMS, FakeDriver([]), FakeDriver([]), FakeDriver([]),
                          clock=lambda: 1.0)
    dispatch = None
    for node in root.iterate():
        if node.name == "CommandDispatch":
            dispatch = node
            break
    names = [c.name for c in dispatch.children]
    assert names[-1] == "AwaitingCommand"
    assert any(isinstance(c, FollowExec) for c in dispatch.children)
    assert names.index("FollowExec") < names.index("AwaitingCommand")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_modes && python3 -m pytest test/test_branch_working.py -q`
Expected: FAIL — `TypeError: create() takes 3 positional arguments but 4 were given`, and `ImportError: cannot import name 'FollowExec'`.

- [ ] **Step 3: Add `FollowExec` to `working_actions.py`** (append; nothing else in the file changes)

```python
class FollowExec(_CommandDrivenAction):
    """follow_admin -> libi_perception delegation.

    The follower's internals (PID tracking action + recovery BT + switch) live
    entirely in libi_perception and are opaque here: this leaf only drives the
    injected driver's start()/poll()/stop(). The follow logic can be retuned or
    restructured without touching libi_modes."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"follow_admin"}, name=name or "FollowExec")
```

- [ ] **Step 4: Wire it into `working.py`**

Change the import line to include `FollowExec`:

```python
from libi_modes.common.working_actions import ArmExec, FollowExec, NavigationExec
```

Change the signature:

```python
def create(params: dict, nav_driver, arm_driver, follow_driver,
           clock=time.monotonic) -> py_trees.behaviour.Behaviour:
```

Change the `CommandDispatch` children to:

```python
                        children=[
                            NavigationExec(nav_driver),
                            ArmExec(arm_driver),
                            FollowExec(follow_driver),
                            py_trees.behaviours.Running(name="AwaitingCommand"),
                        ],
```

- [ ] **Step 5: Wire it into `registry.py`**

Change the WORKING line of `build_branches` to:

```python
        "WORKING": working.create(params, drivers["nav"], drivers["arm"], drivers["follow"]),
```

And in `test/test_tree_integration.py`, add `"follow": _NullDriver(),` to the `_drivers()` dict so the integration test keeps passing.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest test/ -q`
Expected: the whole libi_modes suite green, now including the three new WORKING cases. If `test_tree_integration.py` raises `KeyError: 'follow'`, Step 5's `_drivers()` edit was missed.

- [ ] **Step 7: Git**

```bash
git add aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/common/working_actions.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/branches/working.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_branch_working.py \
        aba_controller/libi_modes/ros_ws/src/libi_modes/test/test_tree_integration.py
git commit -m "feat: add FollowExec leaf to libi_modes WORKING dispatch"
```

---

### Task 10: FMS admin-follow request endpoint

**Files:**
- Create: `aba_fms_service/backend/app/routers/admin_follow.py`
- Modify: `aba_fms_service/backend/main.py` (import line 9 + one `include_router` call)

**Interfaces:**
- Produces: `POST /api/robot/admin-follow/request` accepting `{"robot_id": str}` and returning `{"accepted": bool, "command": str | None, "reason": str | None}`. On acceptance, `command` is `"follow_admin"`.

**Why a request/response endpoint.** Settled with the user in chat: the GUI button does **not** locally force the robot into following. It asks FMS and waits. That keeps admin-follow inside the normal task-dispatch path — FMS knows the robot is busy, and the eventual `task_cancelled` bookkeeping on stop works the same as for any other task.

**Scope honesty.** This task builds the endpoint and its accept/reject policy. Actually *delivering* `active_command = "follow_admin"` to the robot's blackboard is the same unbuilt transport that `libi_modes`' `main.py` needs and is deferred with it (see "Deferred / open decisions"). The endpoint therefore records the decision and returns it; the dispatch hand-off is a one-line call site once that transport exists, marked with a `TODO(transport)` comment so it is greppable.

- [ ] **Step 1: Write the failing test**

```python
# aba_fms_service/backend/tests/test_admin_follow.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import admin_follow


def _client():
    app = FastAPI()
    app.include_router(admin_follow.router)
    return TestClient(app)


def test_accepts_request_for_known_robot():
    admin_follow.set_robot_state("pinky1", "IDLE")
    r = _client().post("/api/robot/admin-follow/request", json={"robot_id": "pinky1"})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["command"] == "follow_admin"


def test_rejects_when_robot_in_error():
    admin_follow.set_robot_state("pinky1", "ERROR")
    body = _client().post("/api/robot/admin-follow/request",
                          json={"robot_id": "pinky1"}).json()
    assert body["accepted"] is False
    assert "ERROR" in body["reason"]


def test_rejects_unknown_robot():
    body = _client().post("/api/robot/admin-follow/request",
                          json={"robot_id": "nope"}).json()
    assert body["accepted"] is False
    assert body["reason"]


def test_rejects_while_already_following():
    admin_follow.set_robot_state("pinky1", "WORKING")
    body = _client().post("/api/robot/admin-follow/request",
                          json={"robot_id": "pinky1"}).json()
    assert body["accepted"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_fms_service/backend && python3 -m pytest tests/test_admin_follow.py -q`
Expected: `ImportError: cannot import name 'admin_follow' from 'app.routers'`

- [ ] **Step 3: Implement `app/routers/admin_follow.py`**

```python
"""관리자 추종 요청 — libi_gui 관리자 버튼이 호출하고 응답을 기다린다.

로봇이 로컬에서 임의로 추종을 시작하지 않고 FMS 승인을 거치게 하여, 진행 중인
태스크 상태를 FMS가 계속 알고 있도록 한다(중단 시 task_cancelled 보고 포함).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/robot/admin-follow", tags=["admin-follow"])

FOLLOW_COMMAND = "follow_admin"

# 로봇별 최신 FSM 상태 캐시. fsm_link(2단계)가 채우며, 그전까지는 아래
# set_robot_state()로 주입한다.
_robot_state: dict[str, str] = {}

# 추종을 시작할 수 있는 상태 — 유휴하거나 순찰 중일 때만 받는다.
_ACCEPTING_STATES = {"IDLE", "PATROL"}


def set_robot_state(robot_id: str, state: str) -> None:
    _robot_state[robot_id] = state


class AdminFollowRequest(BaseModel):
    robot_id: str


class AdminFollowResponse(BaseModel):
    accepted: bool
    command: str | None = None
    reason: str | None = None


@router.post("/request", response_model=AdminFollowResponse)
async def request_admin_follow(req: AdminFollowRequest) -> AdminFollowResponse:
    state = _robot_state.get(req.robot_id)
    if state is None:
        return AdminFollowResponse(
            accepted=False, reason=f"알 수 없는 로봇입니다: {req.robot_id}")
    if state == "ERROR":
        return AdminFollowResponse(
            accepted=False, reason="로봇이 ERROR 상태입니다. 먼저 복구하세요.")
    if state not in _ACCEPTING_STATES:
        return AdminFollowResponse(
            accepted=False, reason=f"{state} 상태에서는 추종을 시작할 수 없습니다.")

    # TODO(transport): 미션 PC 도메인/브릿지 확정 후 여기서 로봇 blackboard 의
    # active_command 를 FOLLOW_COMMAND 로 설정하는 호출을 추가한다.
    return AdminFollowResponse(accepted=True, command=FOLLOW_COMMAND)
```

- [ ] **Step 4: Register the router in `main.py`**

Add `admin_follow` to the existing `from app.routers import ...` line (line 9), keeping alphabetical order — it becomes the first name in the list. Then add next to the other `include_router` calls:

```python
app.include_router(admin_follow.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_follow.py -q`
Expected: `4 passed`

Also confirm the app still imports with the new router:
Run: `python3 -c "import main; print(len(main.app.routes))"`
Expected: prints a route count without raising.

- [ ] **Step 6: Git**

```bash
git add aba_fms_service/backend/app/routers/admin_follow.py \
        aba_fms_service/backend/main.py \
        aba_fms_service/backend/tests/test_admin_follow.py
git commit -m "feat(fms): add admin-follow request endpoint"
```

---

### Task 11: libi_gui 관리자 추종 button

**Files:**
- Modify: `aba_controller/libi_gui/src/RobotController.h` (one property, one invokable, one signal)
- Modify: `aba_controller/libi_gui/src/RobotController.cpp` (one method + accessor)
- Modify: `aba_controller/libi_gui/qml/screens/AdminControlScreen.qml` (one `BigButton` in the 복구 제어 `Grid`)

**Interfaces:**
- Produces: `Q_PROPERTY(bool following ...)`, `Q_INVOKABLE void startAdminFollow()`, `Q_INVOKABLE void stopAdminFollow()`, signal `followingChanged()`.

**Guard order matches the existing `startPatrol()` exactly** (`RobotController.cpp:179-190`): admin check first, then e-stop, then state-specific refusals, each with a `toast()`. Following that shape keeps the admin screen's behaviour consistent rather than inventing a new interaction pattern.

- [ ] **Step 1: `RobotController.h` — add the property**

Next to the other state properties (after the `patrolActive` line):

```cpp
    Q_PROPERTY(bool following READ following NOTIFY followingChanged)
```

Add the getter next to `patrolActive()`:

```cpp
    bool following() const { return m_following; }
```

Add the invokables in the "관리자 작업상태/에러 복구" block, after `startPatrol()`:

```cpp
    Q_INVOKABLE void startAdminFollow();             // 관리자 추종 시작 (FMS 승인 후)
    Q_INVOKABLE void stopAdminFollow();              // 관리자 추종 종료
```

Add the signal next to `patrolActiveChanged()`:

```cpp
    void followingChanged();
```

Add the member next to `m_patrol`:

```cpp
    bool m_following = false;
```

- [ ] **Step 2: `RobotController.cpp` — add the methods**

Insert directly after `startPatrol()`'s closing brace (line 190):

```cpp
// ---- 관리자 추종 : ROS2-SEAM (실제론 FMS /api/robot/admin-follow/request 호출 후
//      승인 응답을 받아야 추종이 시작된다. 로컬에서 임의로 시작하지 않는다.) ----
void RobotController::startAdminFollow() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다. 먼저 에러를 해제하세요.")); return; }
    if (m_robotState == QLatin1String("에러")) { emit toast(QStringLiteral("에러 상태에서는 추종을 시작할 수 없습니다.")); return; }
    if (m_following) { emit toast(QStringLiteral("이미 추종 중입니다.")); return; }
    // ROS2-SEAM: FMS 승인 응답이 accepted=true 일 때만 아래로 진행한다.
    m_following = true; emit followingChanged();
    setRobotState(QStringLiteral("작업중"));
    setTaskStatus(QStringLiteral("관리자 추종 중"));
    log(QStringLiteral("관리자 추종 시작 — FMS 승인 요청"));
    emit toast(QStringLiteral("관리자 추종을 시작합니다."));
}

void RobotController::stopAdminFollow() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (!m_following) return;
    m_following = false; emit followingChanged();
    setRobotState(QStringLiteral("대기"));
    setTaskStatus(QStringLiteral("명령 대기"));
    log(QStringLiteral("관리자 추종 종료"));
    emit toast(QStringLiteral("관리자 추종을 종료했습니다."));
}
```

- [ ] **Step 3: `AdminControlScreen.qml` — add the button**

Inside the "🔄 복구 제어" `Grid` (the one with `columns: 2` and `property real cellW`), after the "🚶 순찰 시작" `BigButton`:

```qml
                    BigButton {
                        visible: !controller.following
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🚶‍♂️  관리자 추종"; color: S.sky; textColor: S.text
                        enabledLook: !controller.emergencyStopped
                        onClicked: controller.startAdminFollow()
                    }
                    BigButton {
                        visible: controller.following
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🛑  추종 종료"; color: S.warning; textColor: "white"
                        onClicked: controller.stopAdminFollow()
                    }
```

- [ ] **Step 4: Build**

```bash
cd aba_controller/libi_gui
cmake -S . -B build && cmake --build build -j
```
Expected: builds clean. A missing `Q_OBJECT` regeneration shows up here as an undefined-reference to `followingChanged()` — if so, delete `build/` and re-run rather than editing the moc output.

**Not verified here:** the button's on-screen appearance and behaviour need the touch panel. Marked for on-device verification.

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_gui/src/RobotController.h \
        aba_controller/libi_gui/src/RobotController.cpp \
        aba_controller/libi_gui/qml/screens/AdminControlScreen.qml
git commit -m "feat(gui): add admin-follow button to admin control screen"
```

---

### Task 12: `aba_ai_service` — YOLO perception server + robot-bound detection channel

**Files:**
- Create: `aba_ai_service/detection_sink.py`
- Modify: `aba_ai_service/main.py` (add the robot-bound channel alongside the existing FMS push)
- Test: `aba_ai_service/tests/test_detection_sink.py`

**Interfaces:**
- Produces: `RobotDetectionSink(host, port)` with `.send(payload: dict | None)` — connects to the robot's `TcpDetectionSource` (Task 3, default `:6000`) and writes newline-delimited JSON, reconnecting on failure. `detection_to_dict(det)` matching the payload shape `libi_perception.detection.detection_from_dict` consumes.

**The path mismatch this fixes.** `aba_ai_service/main.py` today only does `UDP:9000 → TCP:9010` to FMS. But following needs Detections at the **robot**, where the 20 Hz LiDAR-fused control loop runs — `libi_perception` listens on `:6000` for exactly that. These are two different consumers with different payloads and cadences, so this task **adds** the robot-bound channel rather than repurposing the FMS one; the existing push is left working exactly as-is.

**Scope.** This task builds the transport and the payload contract, both testable here. Dropping in the actual YOLO/ByteTrack/ReID pipeline from `arte_libi_perception/follower_perception/` is a separate, GPU-dependent step: that package is pure Python with its own `requirements.txt` and its `follower_perception/ai_server.py` already exposes a `result_sink` injection point whose `.send(source_id, payload)` shape this sink satisfies. Wiring it is deferred — see "Deferred / open decisions".

- [ ] **Step 1: Write the failing test**

```python
# aba_ai_service/tests/test_detection_sink.py
import json
import socket
import threading

from detection_sink import RobotDetectionSink, detection_to_dict


class _FakeRobot:
    """Stands in for libi_perception's TcpDetectionSource."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.lines = []
        self._ready = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self._sock.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self.lines.append(json.loads(line.decode()))
                        self._ready.set()

    def wait(self, timeout=2.0):
        assert self._ready.wait(timeout), "no payload arrived"
        self._ready.clear()


def test_detection_to_dict_matches_robot_contract():
    class _Det:
        cx, cy, area = 320.0, 240.0, 10000.0
        bbox = (1.0, 2.0, 3.0, 4.0)
        track_id, is_owner, confidence, is_predicted = 7, True, 0.91, False

    d = detection_to_dict(_Det())
    assert set(d) == {"cx", "cy", "area", "bbox", "track_id",
                      "is_owner", "confidence", "is_predicted"}
    assert d["bbox"] == [1.0, 2.0, 3.0, 4.0]      # list, not tuple — JSON round-trips


def test_detection_to_dict_passes_through_none():
    assert detection_to_dict(None) is None


def test_sink_delivers_newline_delimited_json():
    robot = _FakeRobot()
    sink = RobotDetectionSink("127.0.0.1", robot.port)
    sink.send({"cx": 1.0})
    robot.wait()
    assert robot.lines[-1] == {"cx": 1.0}


def test_sink_sends_null_for_no_owner():
    robot = _FakeRobot()
    sink = RobotDetectionSink("127.0.0.1", robot.port)
    sink.send(None)
    robot.wait()
    assert robot.lines[-1] is None


def test_sink_does_not_raise_when_robot_absent():
    """A dead robot link must not take the AI server down."""
    sink = RobotDetectionSink("127.0.0.1", 1)     # nothing listening
    sink.send({"cx": 1.0})                        # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aba_ai_service && python3 -m pytest tests/test_detection_sink.py -q`
Expected: `ModuleNotFoundError: No module named 'detection_sink'`

- [ ] **Step 3: Implement `detection_sink.py`**

```python
"""로봇 직결 Detection 채널.

기존 FMS push(UDP:9000 → TCP:9010)와는 별개의 경로다. 추종 제어 루프는 로봇에서
20Hz로 LiDAR와 융합되어 돌기 때문에, Detection 은 FMS 가 아니라 로봇의
libi_perception(TcpDetectionSource, 기본 :6000)으로 직접 가야 한다.

주인이 안 보이는 프레임은 JSON `null` 로 보낸다 — 수신측 detection_from_dict()
가 None 을 그대로 통과시키는 계약과 맞춘다.
"""
from __future__ import annotations

import json
import os
import socket
import threading

ROBOT_HOST = os.environ.get("ROBOT_DETECTION_HOST", "127.0.0.1")
ROBOT_PORT = int(os.environ.get("ROBOT_DETECTION_PORT", "6000"))


def detection_to_dict(det):
    """libi_perception.detection.detection_from_dict 가 읽는 payload 형태."""
    if det is None:
        return None
    return {
        "cx": det.cx, "cy": det.cy, "area": det.area, "bbox": list(det.bbox),
        "track_id": det.track_id, "is_owner": det.is_owner,
        "confidence": det.confidence, "is_predicted": det.is_predicted,
    }


class RobotDetectionSink:
    """줄바꿈 구분 JSON 을 로봇으로 보낸다. 링크가 끊겨도 예외를 올리지 않고
    다음 send() 에서 재연결을 시도한다 — 추론 루프가 통신 때문에 죽으면 안 된다."""

    def __init__(self, host: str = ROBOT_HOST, port: int = ROBOT_PORT):
        self._addr = (host, int(port))
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(self._addr)
        self._sock = sock

    def send(self, payload) -> bool:
        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._lock:
            for attempt in (1, 2):          # 두 번째 시도는 재연결 후
                try:
                    if self._sock is None:
                        self._connect()
                    self._sock.sendall(line)
                    return True
                except OSError:
                    self._close_locked()
                    if attempt == 2:
                        return False
            return False

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()
```

- [ ] **Step 4: Wire it into `main.py`**

Add the import and a module-level sink next to the existing config constants:

```python
from detection_sink import RobotDetectionSink, detection_to_dict

_robot_sink = RobotDetectionSink()
```

Then in `main()`'s loop, after the existing `push_to_fms(result)` block, add the robot-bound send. The existing FMS push stays untouched:

```python
        # 로봇 직결 채널 — 추종 제어용. FMS push 와 독립적으로 실패해도 무시한다.
        owner = result.get("owner")
        if not _robot_sink.send(detection_to_dict(owner)):
            print("[ai_service] robot detection link down", flush=True)
```

And extend `infer()`'s stub return with an `owner` key so the contract is explicit before the real pipeline lands:

```python
        "owner": None,   # 실물에서는 FollowerPerception 이 고른 주인 Detection
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_detection_sink.py -q`
Expected: `5 passed`

Also confirm the service still imports:
Run: `python3 -c "import main; print(main.UDP_PORT, main.FMS_PORT)"`
Expected: `9000 9010`

**Not verified here:** end-to-end frames from a real camera through YOLO to a real robot. Marked for on-hardware verification.

- [ ] **Step 6: Git**

```bash
git add aba_ai_service/detection_sink.py \
        aba_ai_service/main.py \
        aba_ai_service/tests/test_detection_sink.py
git commit -m "feat(ai): add robot-bound detection channel for person following"
```

---

## Deferred / open decisions

These block or reshape work above. Resolve the starred ones before the tasks that depend on them.

### ★ 1. Recovery timeline: HOLD-first or PEEK-first? (blocks Task 5)

Two incompatible timelines exist upstream:

| | `follower_control/search_planner.py` (running today) | `follower_BT/recovery.py` (prototype) |
|---|---|---|
| Order | HOLD 10 s → SCAN → TURN180 → SCAN → give up | PEEK 90° → SCAN ±45° → TURN180 → SCAN → TURN180 → give up |
| Initial move | stay still | turn toward last-known direction |
| Rotation speed | `ANGULAR_Z_SEARCH = 0.35` | `ANGULAR_SEARCH = 0.25` |
| Test coverage | yes (`test_search_planner.py`, `test_bt_searching.py`) | yes (`follower_BT/tests/test_recovery.py`) |

`recovery.py`'s own docstring says it exists to be "swapped for follower_control's real py_trees BT later", so the prototype was always meant to converge — it just never did, and now they disagree.

**Recommendation: keep HOLD-first for now.** It is what actually runs, it is the timeline this plan's equivalence test is built against, and holding still first is the safer default when a person is briefly occluded — a robot that immediately spins on a momentary occlusion looks broken and can lose the owner it would otherwise have kept. PEEK-first is genuinely smarter when the owner rounds a corner, so it is worth adopting later as a deliberate behaviour change with its own before/after comparison, not folded silently into a port.

If PEEK-first is chosen instead, Task 4's `search_planner` and Task 5's `spec` list change together and the equivalence test keeps them consistent — the decomposition itself does not change.

### ★ 2. Where does the follow node actually run? (affects Tasks 8–9)

The user placed `libi_perception` in `libi_modes/ros_ws`, which is the **mission PC's** workspace. But `follow_node` subscribes `/scan` and publishes `/cmd_vel` — both belong to the **driving Pi**, which owns the LiDAR and the motors. Running a 20 Hz LiDAR-fused control loop across a network hop would put the safety-critical avoidance path on the far side of a link that can stall.

The package location is settled and this plan follows it; the *deployment* target is not. Two options: build this workspace on the Pi as well and run the node there (keeps the control loop local, costs a second checkout), or move the package to `libi_drive_controller/ros_ws/src` (matches deployment, contradicts the stated location). Needs a decision before on-robot bring-up.

### 3. `FollowExec` driver transport

`FollowSession` (Task 8) is a plain object; `FollowExec` (Task 9) takes an injected driver. What connects them across machines — in-process, ROS2 service, or the domain bridge — is unresolved, exactly like the `nav`/`arm`/`return_dock` drivers in `2026-07-20-libi-modes-fsm-bt.md`, which are deferred with `main.py` on the same blocker.

### 4. Mission-PC `ROS_DOMAIN_ID` and bridge config

Still undecided (open in chat: FMS-side `domain_bridge`, number not chosen). Blocks `libi_modes`' `main.py`, this plan's cross-machine transport, and Task 10's `TODO(transport)` call site.

### 5. FMS admin-follow contract beyond the happy path

Task 10 covers accept/reject. Undecided: whether stopping an admin-follow reports `task_cancelled` the way INSTRUCTION.md requires for other `WORKING` exits; whether a follow session appears in the FMS task list at all; and what happens to it if the robot faults mid-follow. Worth settling alongside Stage 2's transition-request handler, which has the same `task_cancelled` obligation.

### 6. Dropping the real YOLO pipeline into `aba_ai_service`

Task 12 builds the channel, not the model. Remaining: vendoring `follower_perception/` (GPU deps in its own `requirements.txt`, `weights/`, `bytetrack.yaml`), deciding whether `aba_ai_service` grows a package dir or imports it as a dependency, and wiring `AiServer`'s `result_sink` to `RobotDetectionSink`. Also unresolved: what the AI server does when several robots follow at once — `AiServer` is already per-source keyed, but `RobotDetectionSink` as written targets exactly one robot address.

### 7. `libi_perception` README

Not written. The other components each have one (`libi_modes/README.md` is Task 1 of its plan). Worth adding once the timeline decision lands, so it documents the shipped recovery order rather than a provisional one.
