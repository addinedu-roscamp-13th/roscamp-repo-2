# 순찰 정지 회귀 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **rev 2** — codex 적대적 검증 반영. rev 1은 "트랩이 어댑터를 죽인 것이 원인"이라 확정했으나
> `fleet_node`가 `robots_`를 만료시키지 않는다는 사실 때문에 그 인과가 성립하지 않는다.
> 설계문서 §2.0·§7 참조.

**Goal:** `fleet_node`가 로봇을 인식하지 못하는 두 경로(어댑터가 발행을 안 함 / 어댑터가 죽음)를 모두 막고, 둘 다 무증상이던 것을 소리 나게 만들며, sim 재현으로 이번 증상이 어느 쪽이었는지 판정한다.

**Architecture:** 상태 어댑터의 수명주기 소유권을 `robot-link.sh` 하나로 모으고(암묵적 정지 경로 제거, pid 신원 검증), 어댑터가 "살아 있음"과 "일하고 있음"을 구별해 로그로 드러내며, `fleet_node`에는 로봇 0대와 stale 로봇 두 가지 경고를 넣는다.

**Tech Stack:** bash, Python 3.12 + rclpy, C++17 + rclcpp, pytest, ROS 2 Jazzy, Gazebo(ros_gz_sim)

## Global Constraints

- **git 조작 금지.** commit·push·merge·rebase 전부 사용자 몫이다. 어떤 Task도 커밋하지 않는다. 커밋 대신 `git diff --stat`으로 변경 범위를 확인하고 끝낸다.
- **sudo 금지, 패키지 설치 금지.** 확인된 도구: colcon, pytest(시스템 7.4.4 / fms venv 9.1.1), gtest, Gazebo(`ros_gz_sim`), tmux, codex. `bats`는 없으므로 셸 테스트는 순수 bash + exit code.
- **수정 허용 파일**: `scripts/laptop/robot-link.sh`, `scripts/laptop/kill.sh`, `scripts/laptop/sim.sh`, `aba_fms_service/scripts/robot_state_adapter.py`, `aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp`, `aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py`, 신규 테스트 파일, 신규 문서. **그 밖은 읽기만 한다.**
- `fleet_node.cpp`·`driving.py`는 다른 담당자 코드다. 승인된 범위(관측성 2종 / ROS overlay 해석)를 벗어나지 않는다.
- 테스트는 실제 상태 디렉토리(`/tmp/libi-robot-link`)와 실제 ROS 도메인(86·90)을 오염시키지 않는다. `TMPDIR`·`LIBI_FMS_DOMAIN`·`ROS_DOMAIN_ID`를 테스트 전용 값으로 덮어쓴다.
- **완료 주장은 방금 실행한 명령의 출력이 있을 때만.** 각 Task의 "실패 확인" 단계를 건너뛰지 않는다.
- ROS 환경: `source /opt/ros/jazzy/setup.bash` + `source aba_fms_service/fleet_ws/install/setup.bash` (rmf_fleet_msgs가 여기 있다. 확인됨).
- 어댑터 발행 주기는 2 Hz (`PUBLISH_HZ = 2.0`). stale 판정·대기 경고의 시간 상수는 이보다 충분히 커야 한다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `aba_fms_service/scripts/robot_state_adapter.py` | amcl_pose → RobotState 재발행 | 종료 처리(R5·R12), 대기 상태 가시화(R11) |
| `aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py` | 위의 외부 동작 검증 | **신규** |
| `scripts/laptop/robot-link.sh` | 어댑터 수명주기 **단일 소유자** | 트랩 제거(R2), 정지 대상 도출 변경 + 신원 검증(R3·R13), 정지 흔적 기록(R12) |
| `scripts/laptop/kill.sh` | 노트북 정리의 **유일한 주체** | 어댑터 명시 정리 + 순서(R3) |
| `scripts/laptop/tests/test_robot_link_lifecycle.sh` | 위 두 스크립트 외부 동작 검증 | **신규** |
| `aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp` | 배차·교통 관제 | 로봇 0대 경고 + stale 경고(R4) |
| `scripts/laptop/sim.sh` | sim 스택 기동 래퍼 | 주석 정정(R6) |
| `aba_controller/.../robot_agent/app/routers/driving.py` | 로봇 프로세스 기동 API | ROS overlay 해석(R10) |
| `scripts/laptop/tests/test_nav2_command_resolves.sh` | 위의 검증 | **신규** |
| `docs/agents/review-patrol-chain-ros2.md` | R8 리뷰 결과 | **신규** |

---

### Task 1: 어댑터가 "일하고 있는지" 보이게 한다 (R11)

**Files:**
- Modify: `aba_fms_service/scripts/robot_state_adapter.py` (import, `__init__`, `_on_pose`, `_tick`)
- Test: Task 2에서 함께 검증한다 (같은 서브프로세스 하네스를 쓰므로 나눌 이유가 없다)

**Interfaces:**
- Consumes: 없음
- Produces: 로그 문자열 두 개. Task 2의 테스트와 Task 9의 sim 판정이 이 문자열을 grep한다:
  - 대기 경고: `amcl_pose 대기` 를 포함
  - 첫 수신: `첫 위치 수신` 을 포함

**배경 — 이것이 이번 사이클에서 가장 중요한 변경이다.**

`robot_state_adapter.py:90-93`:

```python
def _tick(self) -> None:
    # 위치를 아직 못 받았으면 발행하지 않는다 — 좌표 0,0 인 유령 로봇이 생기면 안 된다.
    if self._pose is None:
        return
```

`amcl_pose`를 못 받으면 `/robot_state`를 **영원히 발행하지 않으면서 로그도 안 남긴다.**
프로세스는 살아 있어 `pgrep`으로는 정상으로 보이고, `fleet_node`는 로봇을 0대로 본다.
이 상태가 이번 순찰 정지의 유력한 상류다(설계문서 §2.1, 경로 A). 지금은 완전히 무증상이라
재현해도 판정할 수가 없다. **먼저 이걸 소리 나게 만들어야 나머지 진단이 성립한다.**

- [ ] **Step 1: import와 상수를 추가한다**

파일 상단 import 블록(`import math` 근처)에 `time`을 추가한다. 이미 있으면 건너뛴다.

```python
import time
```

`PUBLISH_HZ = 2.0` 다음 줄에 추가:

```python
#: amcl_pose 를 못 받는 상태를 몇 초마다 알릴지. 발행 주기(2 Hz)보다 훨씬 커야 로그를 안 덮는다.
POSE_WAIT_WARN_SEC = 15.0
```

- [ ] **Step 2: 상태 필드를 추가한다**

`RobotStateAdapter.__init__`의 `self._seq = 0` 다음에 삽입:

```python
        # 진단용 — "프로세스가 살아 있다"와 "일하고 있다"를 구별하기 위한 최소 상태.
        self._prefix = prefix
        self._started_at = time.monotonic()
        self._last_wait_warn_at = 0.0
```

- [ ] **Step 3: 첫 위치 수신을 한 번 알린다**

`_on_pose`를 교체한다.

교체 전:
```python
    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation.z, p.orientation.w))
```

교체 후:
```python
    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        first = self._pose is None
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation.z, p.orientation.w))
        if first:
            # "언제부터 정상이었나"를 로그에 남긴다. 이 줄이 없으면 어댑터가 일을 시작한
            # 시각을 알 방법이 없고, 시작조차 못 한 경우와 구별되지 않는다.
            self.get_logger().info(
                f"첫 위치 수신 — /robot_state 발행을 시작합니다 "
                f"(대기 {time.monotonic() - self._started_at:.1f}s)"
            )
```

- [ ] **Step 4: 대기 상태를 주기적으로 알린다**

`_tick`의 앞부분을 교체한다.

교체 전:
```python
    def _tick(self) -> None:
        # 위치를 아직 못 받았으면 발행하지 않는다 — 좌표 0,0 인 유령 로봇이 생기면 안 된다.
        if self._pose is None:
            return
        x, y, yaw = self._pose
```

교체 후:
```python
    def _tick(self) -> None:
        # 위치를 아직 못 받았으면 발행하지 않는다 — 좌표 0,0 인 유령 로봇이 생기면 안 된다.
        if self._pose is None:
            # ⚠️ 예전엔 여기서 조용히 return 했다. 그래서 이 상태가 **완전히 무증상**이었다:
            #    프로세스는 살아 있고(pgrep 으로 보이고) 로그도 안 나오는데
            #    /robot_state 는 한 번도 안 나가고, fleet_node 는 로봇을 0대로 본다.
            #    → 배차·순회가 시작조차 안 되는데 관제 패널에는 로봇이 정상으로 보인다
            #      (패널은 amcl_pose 를 직접 읽는다). 2026-07-26 순찰 정지의 유력 상류.
            now = time.monotonic()
            if now - self._last_wait_warn_at >= POSE_WAIT_WARN_SEC:
                self._last_wait_warn_at = now
                self.get_logger().warn(
                    f"{self._prefix}/amcl_pose 대기 중 ({now - self._started_at:.0f}s) — "
                    "/robot_state 를 발행하지 못하고 있습니다. fleet_node 는 이 로봇을 "
                    "인식하지 못하며 배차·순회가 시작되지 않습니다. "
                    "도메인 브릿지(domain_bridge)와 AMCL 초기 위치를 확인하세요."
                )
            return
        x, y, yaw = self._pose
```

- [ ] **Step 5: 문법을 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('aba_fms_service/scripts/robot_state_adapter.py').read_text()); print('문법 OK')"
```
Expected: `문법 OK`

- [ ] **Step 6: 실제로 경고가 나오는지 확인한다**

`amcl_pose`가 없는 도메인에서 40초 띄운다 — 경고가 2회 이상 나와야 한다.

Run:
```bash
cd /home/ane/personal_repo/aba_project && bash -c '
source /opt/ros/jazzy/setup.bash
source aba_fms_service/fleet_ws/install/setup.bash
ROS_DOMAIN_ID=97 timeout 40 python3 -u aba_fms_service/scripts/robot_state_adapter.py \
  --robot WaitProbe-1 --prefix /waitprobe1 2>&1 | grep -c "amcl_pose 대기"'
```
Expected: **2 이상** (40초 / 15초 → 2~3회). 0이면 경고가 안 나온 것이다.

- [ ] **Step 7: 변경 범위 확인 (커밋하지 않는다)**

Run: `git diff --stat aba_fms_service/scripts/robot_state_adapter.py`
Expected: 1 file changed. 발행 로직(`msg = RobotState()` 이후)은 손대지 않았어야 한다.

---

### Task 2: 어댑터 종료 처리와 종료 흔적 (R5, R12)

**Files:**
- Create: `aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py`
- Modify: `aba_fms_service/scripts/robot_state_adapter.py` — `main()` (파일 끝)

**Interfaces:**
- Consumes: Task 1의 `_started_at` (종료 로그에 가동 시간을 싣는다)
- Produces: 종료 로그 문자열 `종료 신호 수신` — Task 9의 판정이 grep한다

**배경:** 현재 `main()`은 `ExternalShutdownException`을 잡지 않고 `finally`에서 무조건
`rclpy.shutdown()`을 부른다. rclpy는 SIGTERM/SIGINT에 기본 핸들러를 걸어 전역 컨텍스트를
종료시키므로 `kill` 한 번에 트레이스백 두 겹이 남고, **진짜 사인이 그 밑에 묻힌다.**

> **왜 "누가 보냈는지"는 로그에 안 남기나** — POSIX 시그널로 송신자 pid를 알려면
> `SA_SIGINFO`가 필요한데 파이썬이 노출하지 않는다. 대신 **`robot-link.sh`가 정지시킬 때
> 어댑터 로그 파일에 한 줄을 남기게** 한다(Task 4). 그 줄이 있으면 `robot-link.sh --stop`,
> 없으면 `pkill`·Ctrl+C·세션 종료다. 이 조합이면 출처가 갈린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py`

```python
"""robot_state_adapter 의 종료 동작과 대기 상태 가시화.

## 왜 서브프로세스로 재현하나
검사 대상이 함수 반환값이 아니라 **프로세스가 어떻게 죽느냐**다. rclpy 는 SIGTERM 에
기본 핸들러를 걸어 전역 컨텍스트를 종료시키고, 그러면 spin() 이
ExternalShutdownException 을 던진다. 그 뒤 finally 가 이미 종료된 컨텍스트에
rclpy.shutdown() 을 다시 부르면 RCLError 가 나면서 트레이스백이 로그를 덮는다.
그 동작은 실제로 프로세스를 띄우고 신호를 보내야만 관찰된다.

보는 것은 외부에서 관찰 가능한 것뿐이다: 종료 코드, 로그 문자열, 트레이스백 유무.
내부 구현(어떤 예외를 잡는지, rclpy.ok() 를 부르는지)은 보지 않는다.
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
ADAPTER = REPO / "aba_fms_service" / "scripts" / "robot_state_adapter.py"
ROS_SETUP = pathlib.Path("/opt/ros/jazzy/setup.bash")
FLEET_SETUP = REPO / "aba_fms_service" / "fleet_ws" / "install" / "setup.bash"

STARTUP_TIMEOUT_SEC = 25.0
EXIT_TIMEOUT_SEC = 15.0
#: robot_state_adapter.POSE_WAIT_WARN_SEC(15s) 보다 커야 경고가 최소 1회 나온다.
WAIT_OBSERVE_SEC = 20.0

requires_ros = pytest.mark.skipif(
    not (ROS_SETUP.is_file() and FLEET_SETUP.is_file()),
    reason="ROS2 Jazzy 또는 fleet_ws 빌드가 없다",
)


def _launch(robot: str, prefix: str) -> subprocess.Popen:
    """ROS 환경을 얹어 어댑터를 띄운다. rmf_fleet_msgs 는 fleet_ws overlay 에 있다."""
    script = "\n".join([
        f'source "{ROS_SETUP}"',
        f'source "{FLEET_SETUP}"',
        f'exec python3 -u "{ADAPTER}" --robot {robot} --prefix {prefix}',
    ])
    env = dict(os.environ)
    env["ROS_DOMAIN_ID"] = "97"          # 실제 fleet 도메인(86)·sim(90)을 오염시키지 않는다
    return subprocess.Popen(
        ["bash", "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env, start_new_session=True,
    )


def _read_until(proc: subprocess.Popen, needle: str, timeout: float) -> str:
    """needle 이 보일 때까지(또는 timeout 까지) 읽어 지금까지의 출력을 돌려준다."""
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        buf += line
        if needle in line:
            break
    return buf


@requires_ros
def test_sigterm_exits_cleanly_without_traceback():
    proc = _launch("ShutdownProbe-1", "/shutdownprobe1")
    try:
        started = _read_until(proc, "어댑터 시작", STARTUP_TIMEOUT_SEC)
        assert "어댑터 시작" in started, f"어댑터가 기동하지 못했다:\n{started}"

        proc.send_signal(signal.SIGTERM)
        rest = proc.communicate(timeout=EXIT_TIMEOUT_SEC)[0] or ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    out = started + rest

    # ⚠️ "Traceback 이 하나도 없을 것" 으로 단정하지 않는다.
    #   rclpy Jazzy 는 인터프리터 종료 시 자기 소멸자에서 `Exception ignored in:` 를
    #   간헐적으로 뿜는다(실측 6회 중 1회):
    #       rclpy/signals.py:56 __del__  → AttributeError: ... '_GuardCondition__gc'
    #       rclpy/executors.py:305 __del__ → AttributeError: ... '_sigint_gc'
    #   전부 rclpy 내부 파일에서 나고, 종료 코드는 0이며 우리 종료 로그도 정상으로 남는다.
    #   우리 코드가 고칠 수 있는 것이 아니고 이번 버그와도 무관하다.
    #   넓게 단정하면 그 잡음에 테스트가 흔들려 **아무것도 못 지키는 테스트**가 된다.
    #   그래서 우리가 통제하는 것만 본다.
    assert "rcl_shutdown already called" not in out, f"이중 shutdown 이 여전히 난다:\n{out}"
    # 우리 파일이 트레이스백에 등장하면 우리 코드에서 예외가 샌 것이다.
    # 정상 동작 중에는 이 파일 이름이 출력에 나올 이유가 없다.
    assert "robot_state_adapter.py" not in out, f"우리 코드에서 예외가 샜다:\n{out}"
    assert "종료 신호 수신" in out, f"종료 기록이 없다 — 사인을 알 수 없다:\n{out}"
    assert proc.returncode == 0, f"종료 코드 {proc.returncode}\n{out}"


@requires_ros
def test_warns_while_waiting_for_pose():
    """amcl_pose 가 오지 않는 동안 어댑터는 **조용하면 안 된다**.

    이 상태(프로세스는 살아 있는데 /robot_state 를 한 번도 발행하지 않음)가
    2026-07-26 순찰 정지의 유력 상류였고, 당시엔 로그가 한 줄도 없어 진단이 불가능했다.
    """
    proc = _launch("WaitProbe-1", "/waitprobe1")
    try:
        started = _read_until(proc, "어댑터 시작", STARTUP_TIMEOUT_SEC)
        assert "어댑터 시작" in started, f"어댑터가 기동하지 못했다:\n{started}"
        waited = _read_until(proc, "amcl_pose 대기", WAIT_OBSERVE_SEC)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=EXIT_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=10)

    assert "amcl_pose 대기" in waited, (
        "amcl_pose 를 못 받는 동안 아무 경고도 없었다 — 무증상 실패가 그대로 남아 있다:\n"
        + started + waited
    )
```

- [ ] **Step 2: 실패하는 것을 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project && \
  aba_fms_service/backend/.venv/bin/python -m pytest \
  aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py -v
```
Expected:
- `test_sigterm_exits_cleanly_without_traceback` **FAIL** (`종료 시 트레이스백이 남았다`)
- `test_warns_while_waiting_for_pose` **PASS** (Task 1을 이미 했으므로). Task 1을 안 했다면 FAIL이어야 한다.

`SKIP`이 뜨면 ROS/fleet_ws 문제이므로 먼저 해결한다 — 아무것도 안 재는 상태로 넘어가지 않는다.

- [ ] **Step 3: 최소 수정을 넣는다**

import 추가 (`from rclpy.node import Node` 다음 줄):

```python
from rclpy.executors import ExternalShutdownException
```

`os`가 import 되어 있는지 확인하고 없으면 상단에 추가한다 (`import argparse` 옆):

```python
import os
```

`main()` 전체 교체:

```python
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", default="pinky3", help="RobotState.name (FsmState.robot_id 와 같아야 함)")
    ap.add_argument("--prefix", default=None, help="브릿지 토픽 접두사 (기본 /<robot>)")
    args = ap.parse_args()
    prefix = args.prefix or f"/{args.robot}"

    rclpy.init()
    node = RobotStateAdapter(args.robot, prefix)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException) as exc:
        # 정상 종료 경로다. rclpy 는 SIGINT/SIGTERM 에 기본 핸들러를 걸어 전역 컨텍스트를
        # 종료시키고, 그러면 spin() 이 ExternalShutdownException 을 던진다.
        # 이걸 안 잡으면 트레이스백이 로그를 덮어 **진짜 사인이 가려진다.**
        #
        # 송신자 pid 는 남기지 않는다 — POSIX 시그널로 알려면 SA_SIGINFO 가 필요한데
        # 파이썬이 노출하지 않는다. 대신 robot-link.sh 가 정지시킬 때 이 로그 파일에
        # 한 줄을 남긴다. 그 줄이 있으면 --stop, 없으면 pkill/Ctrl+C/세션 종료다.
        node.get_logger().info(
            f"종료 신호 수신({type(exc).__name__}) — "
            f"pid={os.getpid()} ppid={os.getppid()} "
            f"가동 {time.monotonic() - node._started_at:.0f}s. 어댑터를 정리합니다"
        )
    finally:
        node.destroy_node()
        # 이미 종료된 컨텍스트에 다시 shutdown 을 부르면
        # `RCLError: rcl_shutdown already called` 로 죽는다. 그래서 상태를 먼저 본다.
        if rclpy.ok():
            rclpy.shutdown()
```

- [ ] **Step 4: 통과하는 것을 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project && \
  aba_fms_service/backend/.venv/bin/python -m pytest \
  aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py -v
```
Expected: **2 passed**

- [ ] **Step 5: 변경 범위 확인 (커밋하지 않는다)**

Run: `git diff --stat aba_fms_service/scripts/robot_state_adapter.py && git status --short aba_fms_service/backend/tests/`
Expected: 어댑터 1개 수정 + 테스트 1개 신규.

---

### Task 3: 어댑터가 창 종료로 죽지 않게 한다 (R2)

**Files:**
- Create: `scripts/laptop/tests/test_robot_link_lifecycle.sh`
- Modify: `scripts/laptop/robot-link.sh:141-155`

**Interfaces:**
- Consumes: Task 1·2의 어댑터
- Produces: 테스트 헬퍼 `pass_case` / `fail_case` / `wait_for_adapter` / `adapter_pid` / `adapter_alive` 와 변수 `TESTDIR` / `STATE_DIR` / `ROBOT` / `KEY` / `LINK` / `REPO`. **Task 4가 같은 파일에 케이스를 추가하며 이 이름들을 그대로 쓴다.**

**배경:** `robot-link.sh:143`의 `trap ... INT TERM`이 `--foreground` 모드에 걸린다. 이 창은
tmux 세션 `libi_fms`의 일부라(`fms_service.sh:108`), 세션 종료나 FMS 재시작만으로 트랩이
발동해 **`--all` 대상 어댑터 전부**를 정지시킨다. `sim.sh:132`가 띄운 것까지 같은 pid 파일을
쓰므로 함께 쓸려간다.

> **이것이 이번 증상의 입증된 원인은 아니다**(설계문서 §2.0). 하지만 실재하는 결함이고,
> 사용자가 요구한 "`kill.sh`를 부를 때만 정리된다"에 정면으로 어긋난다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`scripts/laptop/tests/test_robot_link_lifecycle.sh`

```bash
#!/usr/bin/env bash
# robot-link.sh / kill.sh 의 **외부 동작**만 검사한다 — 트랩이 있는지 같은 내부는 보지 않는다.
#
#   케이스 1: 관리 셸(--foreground)이 죽어도 어댑터는 살아 있는가        ← R2
#   케이스 2: 명시적 정지 후 프로세스와 pid 파일이 0 인가                 ← R3
#   케이스 3: 정지가 남의 프로세스를 죽이지 않는가 (pid 재사용 방어)      ← R13
#   케이스 4: 정지 흔적이 어댑터 로그에 남는가                            ← R12
#
# bats 를 쓰지 않는다(이 머신에 없고 sudo 없이 깔 수 없다). 순수 bash + 종료코드면 충분하다.
# 실제 상태 디렉토리(/tmp/libi-robot-link)와 실제 도메인을 오염시키지 않도록
# TMPDIR 과 LIBI_FMS_DOMAIN 을 테스트 전용 값으로 덮어쓴다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
LINK="$REPO/scripts/laptop/robot-link.sh"

ROS_SETUP=/opt/ros/jazzy/setup.bash
FLEET_SETUP="$REPO/aba_fms_service/fleet_ws/install/setup.bash"
if [ ! -f "$ROS_SETUP" ] || [ ! -f "$FLEET_SETUP" ]; then
  echo "SKIP: ROS2 Jazzy 또는 fleet_ws 빌드가 없다"
  exit 0
fi

FAILED=0
pass_case() { echo "  ✅ $1"; }
fail_case() { echo "  ❌ $1"; FAILED=1; }

TESTDIR="$(mktemp -d)"
export TMPDIR="$TESTDIR"                 # robot-link.sh 의 STATE_DIR 가 여기로 온다
export LIBI_FMS_DOMAIN=98                # 실제 fleet 도메인(86) 회피
STATE_DIR="$TESTDIR/libi-robot-link"
ROBOT="LifecycleProbe-1"

# ⚠️ 키를 하드코딩하지 않는다. `key_of()` 는 구분자를 지운 뒤 **첫 글자만** 소문자로
#    바꾸므로 "LifecycleProbe-1" → "lifecycleProbe1" 이다(전부 소문자가 아니다).
#    규칙을 여기 복제하면 규칙이 바뀔 때 조용히 어긋나고, 테스트는 pid 파일을 못 찾아
#    "어댑터 기동 실패" 라는 **엉뚱한 이유로** 죽는다. 그래서 상태 디렉토리에서 찾아낸다 —
#    이 디렉토리는 이 테스트 전용이라 여기 있는 .pid 는 우리 어댑터의 것뿐이다.
key_from_state_dir() {
  local pf
  for pf in "$STATE_DIR"/*.pid; do
    [ -e "$pf" ] || continue
    basename "$pf" .pid
    return 0
  done
  return 1
}

cleanup_all() {
  pkill -9 -f "robot_state_adapter.py --robot $ROBOT" 2>/dev/null
  rm -rf "$TESTDIR"
  return 0
}
trap cleanup_all EXIT

adapter_pid() {
  local pf
  for pf in "$STATE_DIR"/*.pid; do
    [ -e "$pf" ] || continue
    cat "$pf" 2>/dev/null
    return 0
  done
  return 1
}

adapter_alive() {
  local p; p="$(adapter_pid)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

# pid 파일 존재만으로는 부족하다 — start_one 은 실패해도 pid 파일을 남긴다.
# 그래서 **살아 있는 것**까지 확인한다. 안 그러면 죽은 어댑터로 테스트가 통과한다.
wait_for_adapter() {           # $1=초
  local deadline=$((SECONDS + $1))
  while [ $SECONDS -lt $deadline ]; do
    adapter_alive && return 0
    sleep 0.5
  done
  return 1
}

echo "[test] 케이스 1 — 관리 셸이 죽어도 어댑터는 산다 (R2)"

"$LINK" "$ROBOT" --foreground > "$TESTDIR/fg.log" 2>&1 &
FG_PID=$!

if ! wait_for_adapter 40; then
  fail_case "어댑터가 기동하지 못했다(또는 즉시 죽었다)"
  cat "$TESTDIR/fg.log"
  exit 1
fi
ADAPTER_PID="$(adapter_pid)"
KEY="$(key_from_state_dir)"              # 실제로 만들어진 키 (하드코딩하지 않는다)
pass_case "어댑터 기동 확인 (pid $ADAPTER_PID, key $KEY)"

# 창이 닫히는 상황 = 관리 셸에 SIGTERM.
kill -TERM "$FG_PID" 2>/dev/null
wait "$FG_PID" 2>/dev/null

sleep 3        # 트랩이 있다면 이 사이에 어댑터가 죽는다
if kill -0 "$ADAPTER_PID" 2>/dev/null; then
  pass_case "관리 셸 종료 후에도 어댑터 생존 (pid $ADAPTER_PID)"
else
  fail_case "관리 셸이 죽자 어댑터도 죽었다 — 암묵적 정지 경로가 남아 있다"
fi

echo
if [ "$FAILED" = "0" ]; then
  echo "[test] 전부 통과"
else
  echo "[test] 실패 있음"
fi
exit "$FAILED"
```

Run: `chmod +x /home/ane/personal_repo/aba_project/scripts/laptop/tests/test_robot_link_lifecycle.sh`

- [ ] **Step 2: 실패하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"`
Expected: `❌ 관리 셸이 죽자 어댑터도 죽었다`, `rc=1`.
`SKIP:`이면 ROS/fleet_ws 문제를 먼저 해결한다.

- [ ] **Step 3: 트랩을 제거한다**

`scripts/laptop/robot-link.sh` — `if [ "$FG" = "1" ]; then` 블록의 `trap` 줄과 안내 문구를 교체한다. `while` 워치독 루프는 **그대로 둔다.**

교체 전:
```bash
if [ "$FG" = "1" ]; then
  # tmux 창에서 살아 있게 두고, 자식이 죽으면 로그로 알린다.
  trap 'for r in "${TARGETS[@]}"; do stop_one "$r"; done; exit 0' INT TERM
  echo "[robot-link] (Ctrl+C 로 어댑터 종료)"
```

교체 후:
```bash
if [ "$FG" = "1" ]; then
  # ⚠️ 여기에 시그널 트랩을 두지 않는다. 되살리지 말 것.
  #
  # 예전엔 `trap '... stop_one ...' INT TERM` 이 있었다. 그런데 이 창은 tmux 세션
  # libi_fms 의 일부라(fms_service.sh:108), 세션 종료나 FMS 재시작만으로 트랩이 발동해
  # **--all 대상 어댑터 전부**를 정지시켰다. sim.sh:132 가 자기 몫으로 띄운 어댑터도
  # 같은 pid 파일을 쓰므로 남의 트랩에 함께 쓸려갔다.
  #
  # 그러면 /robot_state 가 끊기고, fleet_node 는 (아직 한 번도 로봇을 못 봤다면)
  # 로봇을 0대로 보아 배차·순회를 시작조차 하지 않는다. 그런데 관제 패널은 amcl_pose 를
  # 직접 읽으므로 로봇이 정상으로 보인다.
  #
  # 규칙: **어댑터는 명시적 정지 요청(--stop, kill.sh)에만 멈춘다.**
  #       창이 닫히는 것은 정지 요청이 아니다.
  echo "[robot-link] 이 창을 닫아도 어댑터는 계속 돕니다."
  echo "[robot-link] 정지: ./scripts/laptop/robot-link.sh --all --stop  (또는 ./scripts/laptop/kill.sh)"
```

- [ ] **Step 4: 통과하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"`
Expected: `✅ 관리 셸 종료 후에도 어댑터 생존`, `rc=0`

- [ ] **Step 5: 변경 범위 확인 (커밋하지 않는다)**

Run: `pgrep -af "robot_state_adapter.py --robot LifecycleProbe-1" || echo "잔여 없음"; git diff --stat scripts/laptop/robot-link.sh`
Expected: `잔여 없음` (테스트의 EXIT 트랩이 정리한다), 1 file changed.

---

### Task 4: 정지를 안전하고 완전하게 만든다 (R3, R13, R12)

**Files:**
- Modify: `scripts/laptop/robot-link.sh:75-80`(정지 로직), `:116-120`(`--stop` 분기)
- Modify: `scripts/laptop/kill.sh`
- Modify: `scripts/laptop/tests/test_robot_link_lifecycle.sh` (케이스 2·3·4 추가)

**Interfaces:**
- Consumes: Task 3의 테스트 파일과 헬퍼 전부. **Task 3의 트랩 제거가 선행되어야 한다** — 트랩이 남아 있으면 케이스 2가 우연히 통과해 아무것도 안 지킨다.
- Produces: bash 함수 두 개
  - `_is_adapter_pid <pid> <key>` → 종료코드 0이면 그 pid가 정말 그 로봇의 어댑터
  - `stop_by_key <key>` → 정지(신원 확인 후에만 신호). `stop_one <robot-name>`이 이걸 감싼다

**배경 (세 가지를 함께 고친다):**

1. **정지 대상이 DB에 묶여 있다.** `--all`은 `rc_robots` 조회로 대상을 정한다. DB가 죽었거나
   등록이 바뀌면 **이미 떠 있는 어댑터를 정리하지 못한다.** 정지에 관해서는 상태 디렉토리의
   pid 파일이 정본이어야 한다.
2. **pid 숫자만 믿고 죽인다.** pid는 재사용된다. stale pid 파일이 남은 상태에서 그 번호를
   다른 프로세스가 물려받으면 **무관한 프로세스를 죽인다.**
3. **`kill.sh`가 어댑터를 명시적으로 정리하지 않는다.** 지금은 Task 3에서 제거한 트랩에
   기대고 있었다.

> ⚠️ **정정 (codex 지적 수용)** — `ros_ws/scripts/kill.sh:86`의 `pkill -f "robot_state_adapter.py"`는
> 실제 커맨드라인에 매치하므로 **`kill.sh`는 이미 어댑터 프로세스를 죽이고 있다.**
> 이 Task가 더하는 값은 "죽이는 것"이 아니라 ① **pid 파일까지 정리**하고 ② **소유권을
> 명시**하고 ③ **신원을 확인**하는 것이다. `pkill`은 여전히 2차 그물로 남는다.

> ⚠️ **순서 근거도 정정한다** — rev 1은 "어댑터를 먼저 정지시키면 워치독이 10초 안에
> 되살린다"고 썼는데 틀렸다. 워치독(`robot-link.sh:149`)은 `[ -f "$pf" ]`일 때만 재기동하고
> `stop_by_key`는 신호 전에 pid 파일을 지운다. **그래도 tmux 세션을 먼저 죽인다** —
> 워치독 자체를 없애면 `rm -f`와 워치독의 `[ -f ]` 검사 사이의 경쟁이 원천적으로 사라진다.

- [ ] **Step 1: 실패하는 테스트를 추가한다**

`scripts/laptop/tests/test_robot_link_lifecycle.sh` — 케이스 1 블록 뒤, 마지막 요약 출력 **앞**에 삽입한다.

```bash
echo
echo "[test] 케이스 2 — 명시적 정지 후 아무것도 남지 않는다 (R3)"

# 케이스 1이 남긴 어댑터가 살아 있어야 한다. 죽어 있으면 정지 테스트가 무의미하다.
if ! adapter_alive; then
  "$LINK" "$ROBOT" > "$TESTDIR/start2.log" 2>&1
  if ! wait_for_adapter 40; then
    fail_case "케이스 2 준비 실패 — 어댑터 기동 불가"
    cat "$TESTDIR/start2.log"
  fi
fi
STOP_TARGET="$(adapter_pid)"
if [ -z "$STOP_TARGET" ] || ! kill -0 "$STOP_TARGET" 2>/dev/null; then
  fail_case "정지 대상 어댑터가 살아 있지 않다 — 이 케이스는 아무것도 검증하지 못한다"
else
  # --all 은 DB 를 조회한다. 이 테스트 로봇은 DB 에 없다.
  # 그래도 정지는 반드시 되어야 한다 — 정지 대상은 pid 파일이 정본이기 때문이다.
  "$LINK" --all --stop > "$TESTDIR/stop.log" 2>&1
  STOP_RC=$?
  sleep 2

  if [ "$STOP_RC" != "0" ]; then
    fail_case "--all --stop 이 0 이 아닌 코드로 끝났다 (rc=$STOP_RC)"
  elif kill -0 "$STOP_TARGET" 2>/dev/null; then
    fail_case "--all --stop 후에도 어댑터가 살아 있다 (pid $STOP_TARGET) — DB 조회에 묶여 있다"
  else
    pass_case "--all --stop 후 어댑터 프로세스 0개"
  fi

  LEFTOVER="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
  if [ "$LEFTOVER" = "0" ]; then
    pass_case "--all --stop 후 pid 파일 0개"
  else
    fail_case "pid 파일이 $LEFTOVER 개 남았다"
  fi
fi

echo
echo "[test] 케이스 3 — 정지가 남의 프로세스를 죽이지 않는다 (R13, pid 재사용 방어)"

# 어댑터가 아닌 프로세스의 pid 를 pid 파일에 심어 둔다. 신원 확인이 없으면 이걸 죽인다.
# 키는 케이스 1 에서 실제로 만들어진 것을 쓴다(하드코딩 금지 — key_of 규칙은 첫 글자만 소문자다).
sleep 300 &
VICTIM=$!
mkdir -p "$STATE_DIR"
echo "$VICTIM" > "$STATE_DIR/${KEY:-probefallback}.pid"

"$LINK" --all --stop > "$TESTDIR/stop_victim.log" 2>&1
sleep 1

if kill -0 "$VICTIM" 2>/dev/null; then
  pass_case "무관한 프로세스(pid $VICTIM)를 죽이지 않았다"
else
  fail_case "무관한 프로세스(pid $VICTIM)를 죽였다 — pid 신원 검증이 없다"
fi
kill -9 "$VICTIM" 2>/dev/null
wait "$VICTIM" 2>/dev/null

if [ "$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)" != "0" ]; then
  fail_case "신원 불일치 pid 파일이 정리되지 않았다"
else
  pass_case "신원 불일치 pid 파일은 제거됐다"
fi

echo
echo "[test] 케이스 4 — kill.sh 가 어댑터 정리를 tmux 종료 **뒤에** 호출한다 (R3 순서)"
KILLSH="$REPO/scripts/laptop/kill.sh"
TMUX_LINE="$(grep -n 'tmux kill-session' "$KILLSH" | head -1 | cut -d: -f1)"
STOP_LINE="$(grep -n 'robot-link.sh.*--stop' "$KILLSH" | head -1 | cut -d: -f1)"
if [ -z "$STOP_LINE" ]; then
  fail_case "kill.sh 가 어댑터를 명시적으로 정리하지 않는다"
elif [ -z "$TMUX_LINE" ]; then
  fail_case "kill.sh 에서 tmux kill-session 을 찾지 못했다"
elif [ "$STOP_LINE" -lt "$TMUX_LINE" ]; then
  fail_case "어댑터 정리($STOP_LINE)가 tmux 종료($TMUX_LINE)보다 앞선다 — 워치독과 경쟁한다"
else
  pass_case "kill.sh 순서: tmux 종료($TMUX_LINE) → 어댑터 정리($STOP_LINE)"
fi

echo
echo "[test] 케이스 5 — 로봇이 여러 대여도 --all --stop 이 전부 정리한다 (다중 로봇 경로)"
# ⚠️ 케이스 1~4 는 로봇이 **한 대**뿐이라, 다중 로봇에서만 나는 고장을 구조적으로 못 본다.
#    실제 배포는 주행 로봇 3대다. /proc 열거의 마지막 항목이 "다른 로봇의" 어댑터일 때
#    _adapter_pids_for_key 가 1 을 반환하고 errexit 이 스크립트를 죽이면,
#    --all --stop 이 중간에 끊겨 나머지 로봇 어댑터가 살아남는다. 여기서 그걸 잡는다.
ROBOT_B="LifecycleProbe-2"
"$LINK" "$ROBOT" > "$TESTDIR/m_a.log" 2>&1
"$LINK" "$ROBOT_B" > "$TESTDIR/m_b.log" 2>&1
sleep 2
BEFORE="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
if [ "$BEFORE" != "2" ]; then
  fail_case "다중 로봇 준비 실패 — pid 파일 $BEFORE 개 (2 개여야 함)"
  cat "$TESTDIR/m_a.log" "$TESTDIR/m_b.log"
else
  "$LINK" --all --stop > "$TESTDIR/m_stop.log" 2>&1
  MRC=$?
  sleep 2
  ALIVE="$(pgrep -f "robot_state_adapter.py --robot LifecycleProbe-" 2>/dev/null | wc -l)"
  AFTER="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
  if [ "$MRC" != "0" ]; then
    fail_case "--all --stop 이 중간에 죽었다 (rc=$MRC) — errexit 로 정리가 끊긴다"
    tail -3 "$TESTDIR/m_stop.log"
  elif [ "$ALIVE" != "0" ]; then
    fail_case "정지 후에도 어댑터 $ALIVE 개 생존 — 다중 로봇 정리가 불완전하다"
  elif [ "$AFTER" != "0" ]; then
    fail_case "정지 후 pid 파일 $AFTER 개 잔여"
  else
    pass_case "로봇 2대 → --all --stop → 프로세스 0개, pid 파일 0개, rc=0"
  fi
fi
pkill -9 -f "robot_state_adapter.py --robot LifecycleProbe-" 2>/dev/null
```

> 케이스 5 는 `cleanup_all` 이 `$ROBOT` 하나만 정리하므로, 자기가 띄운 두 번째 로봇을
> 마지막 줄에서 직접 거둔다. 실패 경로로 빠져도 그 줄은 실행된다.

- [ ] **Step 2: 실패하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"`
Expected: **FAIL** — 최소한 다음 셋이 ❌ 로 나와야 한다:
- `--all --stop 후에도 어댑터가 살아 있다` (DB 조회에 묶임)
- `무관한 프로세스를 죽였다` (신원 검증 없음)
- `kill.sh 가 어댑터를 명시적으로 정리하지 않는다`

- [ ] **Step 3: `robot-link.sh`의 정지 로직을 교체한다**

`stop_one` (현재 `:75-80`)을 아래로 교체한다.

```bash
# pid 파일은 **힌트일 뿐 소유권의 근거가 아니다.**
#
# 예전엔 pid 파일의 숫자를 그대로 kill 했다. 두 가지가 깨진다:
#   ① pid 재사용 — 그 번호를 물려받은 **무관한 프로세스를 죽인다**
#   ② 소유권 상실 — 파일이 낡은 pid 를 가리키는 동안 진짜 어댑터가 다른 pid 로 살아 있으면
#      영영 못 찾는다 (파일을 지우는 순간 추적 수단이 사라진다)
#
# 그래서 정지 대상을 **/proc 의 argv 로 직접 찾는다.** 파일이 뭘 가리키든,
# 그 로봇의 어댑터인 프로세스는 전부 잡힌다. 파일은 지우기만 한다.
# argv 를 NUL 로 끊어 **정확 비교**한다 — 부분일치/glob 로 비교하면 로봇 이름에 따라
# 다른 로봇의 어댑터를 잡을 수 있다.
_adapter_pids_for_key() {    # $1=key  → 해당 어댑터 pid 들을 줄 단위로 출력
  local key="$1" pid want="--prefix" argv
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    [ -r "/proc/$pid/cmdline" ] || continue
    # argv 를 배열로 읽는다(NUL 구분). 부분문자열 비교를 쓰지 않는다.
    mapfile -d '' -t argv < "/proc/$pid/cmdline" 2>/dev/null || continue
    local i found_script=0 found_prefix=0
    for i in "${argv[@]}"; do
      case "$i" in */robot_state_adapter.py|robot_state_adapter.py) found_script=1 ;; esac
    done
    [ "$found_script" = "1" ] || continue
    for ((i = 0; i < ${#argv[@]}; i++)); do
      if [ "${argv[i]}" = "$want" ] && [ "${argv[i+1]:-}" = "/$key" ]; then
        found_prefix=1; break
      fi
    done
    if [ "$found_prefix" = "1" ]; then echo "$pid"; fi
  done
  # ⚠️ 반드시 성공으로 끝낸다. 이 스크립트는 `set -eo pipefail` 아래에서 돌고,
  #   호출부는 `pids="$(_adapter_pids_for_key "$key")"` 라는 **단순 대입**이다.
  #   그 대입의 종료상태는 함수의 종료상태이고, 함수의 종료상태는 for 루프의
  #   **마지막 반복**이 남긴 값이다.
  #   → /proc 열거의 마지막 항목이 "다른 로봇의" 어댑터면 found_prefix=0 이라
  #     마지막 명령이 1 을 남기고, errexit 이 스크립트를 그 자리에서 죽인다.
  #     rm -f "$pf" 도, 남은 로봇들의 정리도 실행되지 않는다.
  #     kill.sh 의 `|| true` 가 그 죽음을 삼켜서 조용히 절반만 정리된다.
  #   실측 재현: 동형 함수로 확인 — 대입 다음 줄에 도달하지 못하고 exit 1.
  #   로봇이 1대뿐인 테스트로는 절대 드러나지 않는다(케이스 5 가 그래서 있다).
  return 0
}

# 단일 pid 가 그 로봇의 어댑터인지. 진단·테스트용 조건식으로 쓰이므로
# 실패(=아님)를 종료상태로 돌려주는 것이 맞다 — 위 함수와 용도가 다르다.
_is_adapter_pid() {          # $1=pid  $2=key
  local pid="$1" key="$2"
  [ -n "$pid" ] || return 1
  _adapter_pids_for_key "$key" | grep -qx "$pid"
}

# 정지 대상은 DB 가 아니라 **실제로 돌고 있는 프로세스**다.
# DB 가 죽었거나 로봇 등록이 바뀌어도, pid 파일이 낡았어도, 어댑터는 반드시 정리된다.
stop_by_key() {
  local key="$1"
  local pf="$STATE_DIR/$key.pid" log="$STATE_DIR/$key.log"
  local pids; pids="$(_adapter_pids_for_key "$key")"

  rm -f "$pf"      # 파일은 힌트였을 뿐이다. 워치독이 재기동하지 않게 지운다.

  [ -n "$pids" ] || return 0

  # 종료 흔적을 어댑터 로그에 남긴다 — 나중에 "누가 죽였나"를 가르는 유일한 단서다.
  # (POSIX 시그널로는 송신자 pid 를 알 수 없다. 이 줄이 있으면 --stop, 없으면 pkill/Ctrl+C.)
  [ -f "$log" ] && echo "[robot-link] $(date -Is) 정지 요청 (pid $(echo $pids | tr '\n' ' '))" >> "$log"

  local pid i
  for pid in $pids; do
    kill "$pid" 2>/dev/null
  done
  # 종료를 **확인**한다. 안 그러면 "정지했다"고 말해놓고 프로세스가 남는다.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ -z "$(_adapter_pids_for_key "$key")" ] && return 0
    sleep 0.5
  done
  for pid in $(_adapter_pids_for_key "$key"); do
    kill -KILL "$pid" 2>/dev/null
  done
  sleep 0.5
  return 0
}

stop_one() { stop_by_key "$(key_of "$1")"; }
```

`--stop` 분기 (현재 `:116-120`)를 교체한다.

```bash
if [ "$STOP" = "1" ]; then
  if [ "$ALL" = "1" ]; then
    # --all 은 DB 로 TARGETS 를 만들지만 **정지에는 쓰지 않는다.**
    # 상태 디렉토리에 pid 파일이 있는 것이 곧 "떠 있는 어댑터"다.
    n=0
    for pf in "$STATE_DIR"/*.pid; do
      [ -e "$pf" ] || continue
      stop_by_key "$(basename "$pf" .pid)"
      n=$((n + 1))
    done
    echo "[robot-link] 정리 완료 (${n}대)"
  else
    for r in "${TARGETS[@]}"; do stop_one "$r"; done
    echo "[robot-link] 정리 완료 (${#TARGETS[@]}대)"
  fi
  exit 0
fi
```

- [ ] **Step 4: `kill.sh`에 명시적 정리를 추가한다**

`scripts/laptop/kill.sh` — `tmux kill-session` 블록과 마지막 `exec` 사이에 삽입한다.

```bash
if tmux has-session -t libi_fms 2>/dev/null; then
  tmux kill-session -t libi_fms
  echo "killed tmux session: libi_fms"
fi

# 상태 어댑터를 **명시적으로** 정리한다.
#
# robot-link.sh 는 더 이상 시그널 트랩으로 자동 정지하지 않는다(2026-07-26, 의도된 변경 —
# 창이 닫혔다고 어댑터가 죽으면 sim 의 로봇 인식까지 함께 죽는다). 그래서 이 스크립트가
# 어댑터 정리의 명시적 주체다.
#
# ⚠️ tmux 세션을 죽인 **뒤에** 부른다. --foreground 워치독을 먼저 없애면
#    pid 파일 삭제와 워치독의 존재 검사 사이 경쟁이 원천적으로 사라진다.
#
# 아래 ros_ws/kill.sh 의 `pkill -f "robot_state_adapter.py"` 는 2차 그물이다 —
# 프로세스는 그것도 죽이지만, **pid 파일 정리와 신원 검증은 여기서만 한다.**
"$REPO_ROOT/scripts/laptop/robot-link.sh" --all --stop || true

# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
```

- [ ] **Step 5: 통과하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"`
Expected: 케이스 1~4 전부 ✅, `rc=0`

- [ ] **Step 6: 문법과 변경 범위를 확인한다 (커밋하지 않는다)**

Run:
```bash
bash -n /home/ane/personal_repo/aba_project/scripts/laptop/robot-link.sh && \
bash -n /home/ane/personal_repo/aba_project/scripts/laptop/kill.sh && echo "문법 OK"
cd /home/ane/personal_repo/aba_project && git diff --stat scripts/laptop/
```
Expected: `문법 OK`. `robot-link.sh`, `kill.sh` 2개 수정 + 신규 테스트. `sim.sh`는 아직 그대로.

---

### Task 5: `fleet_node`에 로봇 0대 / stale 경고를 넣는다 (R4)

**Files:**
- Modify: `aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp` — `on_robot_state`(:229), `on_timer`(:368), 멤버 선언(:944 근처)

**Interfaces:**
- Consumes: 없음
- Produces: 로그 문자열 두 개. Task 9의 판정이 grep한다: `로봇 0대`, `로봇 상태 끊김`

**⚠️ 다른 담당자의 코드다.** 배차·교통 로직은 건드리지 않는다. 이 Task 시작 시 사용자에게
"R4로 `fleet_node.cpp`를 수정한다"를 한 줄로 고지한다(승인은 이미 받았다 — 고지만 하고 진행).

**배경:** `robots_`는 `on_robot_state`의 `robots_[msg->name]` 한 곳에서만 채워지고
**어디서도 제거되지 않는다** (`erase`/`clear`/`stale` 검색 0건). 그래서 두 가지가 동시에 참이다.

- 로봇을 **한 번도 못 본** 경우: 순회 루프가 돌 대상이 없어 조용히 아무것도 안 한다.
- 로봇을 **한 번 본 뒤 어댑터가 죽은** 경우: 옛날 좌표를 현재 위치로 믿고 계속 관제한다.
  0대 경고만으로는 **이 경우를 영영 못 잡는다.** 그래서 stale 경고가 함께 필요하다.

**gtest를 추가하지 않는 이유:** 판단식이 `robots_.empty()`와 `(now - last) > 임계` 두 개의
비교뿐이다. 단위 테스트는 `>`를 테스트하게 된다. 대신 **실제로 노드를 띄워 두 경고가 모두
나오는지 확인**한다(Step 4·5) — 목적에 더 직접적이고, 다른 담당 패키지에 헤더·CMake 항목을
추가하지 않아도 된다.

- [ ] **Step 1: 현재는 아무 경고도 없다는 것을 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && source install/setup.bash && \
  ROS_DOMAIN_ID=96 timeout 25 ros2 run libi_fleet fleet_node \
    --ros-args -p navgraph_file:=/home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml -p arrive_radius:=0.05 2>&1 | tail -20
```
Expected: `libi_fleet FMS up` 이후 **완전한 침묵**.
> ⚠️ **`navgraph_file` 을 반드시 준다.** 안 주면 `fleet_node` 가 기동 즉시
> `terminate called after throwing an instance of 'YAML::BadFile'` 로 abort 한다 —
> 로그가 3줄만 남고 경고를 하나도 못 본다(실측 확인). 검증이 "경고가 안 나온다" 로
> **오진**된다. 이것이 지금의 문제다.
(도메인 96은 아무도 안 쓰므로 로봇 0대가 보장된다.)

- [ ] **Step 2: 멤버와 상수를 추가한다**

`std::map<std::string, RobotInfo> robots_;` (`:944`) 바로 다음에 삽입:

```cpp
  //: 로봇별 마지막 /robot_state 수신 시각. **robots_ 는 만료되지 않으므로** 이것이
  //  "이 로봇 소식이 끊겼다"를 알 수 있는 유일한 근거다.
  std::map<std::string, rclcpp::Time> last_state_at_;
```

상수는 **`fleet_node.cpp` 안에** 둔다. `kArriveDefault` 같은 기존 상수들은
`include/libi_fleet/fleet_task.hpp`에 있지만 **그 헤더는 이번 수정 허용 목록 밖이다.**
이 값은 이 파일에서만 쓰므로 헤더에 낼 이유도 없다.

`#include` 블록 다음, `class FleetNode` 정의 앞에 삽입:

```cpp
namespace
{
// 로봇 소식이 이 시간 이상 끊기면 stale 로 본다. 어댑터 발행 주기는 2 Hz 이므로
// 10 초면 20 프레임을 놓친 것이라 오탐이 아니다.
// 이 파일에서만 쓴다 — fleet_task.hpp 로 내보내지 않는다.
constexpr double kRobotStaleSec = 10.0;
}  // namespace
```

> `rclcpp/rclcpp.hpp`가 이미 include돼 있으므로 `RCLCPP_WARN_THROTTLE`과 `rclcpp::Time`은
> 추가 include 없이 쓸 수 있다. `now()`는 `rclcpp::Node`의 멤버라 클래스 안에서 바로 쓴다.
> **단 이 파일에는 두 API의 선례가 없다** — 빌드(Step 4)에서 반드시 확인한다.

- [ ] **Step 3: 수신 시각을 기록하고 경고를 넣는다**

3-1. `on_robot_state`(`:229-240`)의 끝, 닫는 중괄호 바로 앞에 삽입:

```cpp
    last_state_at_[msg->name] = now();
```

3-2. `on_timer`(`:368`) 본문 **맨 앞**(현재 `:370`의 주석 위)에 삽입:

```cpp
  void on_timer()
  {
    // ── 로봇 인식 상태 경고 ────────────────────────────────────────────────
    // robots_ 는 /robot_state 로만 채워지고(on_robot_state) **어디서도 제거되지 않는다.**
    // 그래서 두 가지 고장이 각각 다른 모습으로 나타나고, 둘 다 예전엔 무증상이었다:
    //
    //   ① 로봇을 한 번도 못 봄  → 순회 루프가 돌 대상이 없어 배차·순찰이 시작조차 안 된다
    //   ② 보다가 소식이 끊김    → 옛날 좌표를 현재 위치로 믿고 도착 판정·GRANT 를 계속 낸다
    //
    // 그동안 관제 패널에는 로봇이 정상으로 보인다(패널은 amcl_pose 를 직접 읽는다).
    // 2026-07-26 순찰 정지가 ①이었고, 침묵 때문에 진단이 몇 시간 걸렸다.
    if (robots_.empty()) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 15000,
        "로봇 0대 — /robot_state 를 아무도 발행하지 않고 있습니다. 배차·순회가 시작되지 않습니다. "
        "확인: pgrep -af robot_state_adapter  /  "
        "어댑터 로그에 'amcl_pose 대기' 가 있으면 브릿지·AMCL 문제입니다  /  "
        "기동: ./scripts/laptop/robot-link.sh --all");
    } else {
      // 두 경우를 **구별해서** 모은다 — 원인도 조치도 다르다.
      //   never : /robot_state 를 한 번도 못 받았다. 위치가 (0,0) 인 유령이다.
      //           robots_ 에는 on_set_battery(:880) 가 미관측 로봇도 만들어 넣는다.
      //   stale : 받다가 끊겼다. 어댑터가 죽었거나 브릿지가 끊긴 것이다.
      const auto t_now = now();
      std::string never_seen, stale;
      for (const auto & kv : robots_) {
        auto it = last_state_at_.find(kv.first);
        if (it == last_state_at_.end()) {
          if (!never_seen.empty()) { never_seen += ", "; }
          never_seen += kv.first;
        } else if ((t_now - it->second).seconds() > kRobotStaleSec) {
          if (!stale.empty()) { stale += ", "; }
          stale += kv.first;
        }
      }
      if (!never_seen.empty()) {
        RCLCPP_WARN_THROTTLE(
          get_logger(), *get_clock(), 15000,
          "위치 미관측 로봇: %s — /robot_state 를 한 번도 못 받았습니다. "
          "위치를 (0,0) 으로 두고 배차 판단이 돌아갑니다. "
          "확인: pgrep -af robot_state_adapter  /  어댑터 로그의 'amcl_pose 대기'",
          never_seen.c_str());
      }
      if (!stale.empty()) {
        RCLCPP_WARN_THROTTLE(
          get_logger(), *get_clock(), 15000,
          "로봇 상태 끊김(%.0fs 이상): %s — 이 로봇들의 위치는 옛날 값입니다. "
          "도착 판정과 통행 허가가 실제 위치와 어긋날 수 있습니다. "
          "확인: pgrep -af robot_state_adapter",
          kRobotStaleSec, stale.c_str());
      }
    }

    // 순회 모드(per-robot): task 없는 PATROL 로봇은 주간 순회, SECURITY_PATROL 로봇은
```

> `RCLCPP_WARN_THROTTLE(logger, clock, duration_ms, ...)`의 `clock`은 `rclcpp::Clock&`다.
> `get_clock()`은 `SharedPtr`를 돌려주므로 `*get_clock()`으로 역참조한다.
> `now()`는 `rclcpp::Node::now()`이며 `rclcpp::Time`을 돌려준다.

- [ ] **Step 4: 빌드한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && \
  colcon build --packages-select libi_fleet --symlink-install 2>&1 | tail -15
```
Expected: `Summary: 1 package finished`

> 이 빌드에는 부수 효과가 있다: `plugins.xml`이 선언한 `libi_fleet::ReservationDeadlock`
> 심볼이 드디어 `.so`에 들어간다. 지금은 `plugins.xml`(심볼릭 링크라 즉시 반영)과
> `.so`(2026-07-25 18:20 빌드본)가 어긋나 있다.

- [ ] **Step 5: 0대 경고가 나오는지 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && source install/setup.bash && \
  ROS_DOMAIN_ID=96 timeout 40 ros2 run libi_fleet fleet_node \
    --ros-args -p navgraph_file:=/home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml -p arrive_radius:=0.05 2>&1 | grep -c "로봇 0대"
```
Expected: **2 이상**

- [ ] **Step 6: stale 경고가 나오는지 확인한다**

어댑터를 잠깐 띄웠다 죽여서, 로봇이 등록된 뒤 소식이 끊긴 상황을 만든다.

Run:
```bash
cd /home/ane/personal_repo/aba_project && bash -c '
source /opt/ros/jazzy/setup.bash
source aba_fms_service/fleet_ws/install/setup.bash
export ROS_DOMAIN_ID=96

# 1) fleet_node 를 백그라운드로
timeout 70 ros2 run libi_fleet fleet_node --ros-args -p navgraph_file:=/home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml -p arrive_radius:=0.05 > /tmp/stale_verify.log 2>&1 &
FN=$!
sleep 8

# 2) RobotState 를 5초간만 발행한다 (어댑터 대신 직접 — amcl_pose 없이 등록만 시킨다)
timeout 5 python3 - <<'"'"'PY'"'"'
import rclpy
from rclpy.node import Node
from rmf_fleet_msgs.msg import Location, RobotMode, RobotState
rclpy.init()
n = Node("stale_probe")
pub = n.create_publisher(RobotState, "/robot_state", 10)
def tick():
    m = RobotState(); m.name = "StaleProbe-1"; m.model = "pinky"
    m.mode = RobotMode(mode=RobotMode.MODE_IDLE); m.battery_percent = 100.0
    loc = Location(); loc.x = 0.0; loc.y = 0.0; loc.yaw = 0.0; loc.level_name = "L1"
    loc.t = n.get_clock().now().to_msg()
    m.location = loc; m.path = []
    pub.publish(m)
n.create_timer(0.5, tick)
try:
    rclpy.spin(n)
except Exception:
    pass
PY

# 3) 발행이 멈춘 뒤 stale 경고를 기다린다
wait $FN 2>/dev/null
echo "--- 0대 경고: $(grep -c "로봇 0대" /tmp/stale_verify.log) ---"
echo "--- stale 경고: $(grep -c "로봇 상태 끊김" /tmp/stale_verify.log) ---"
grep "로봇 상태 끊김" /tmp/stale_verify.log | head -2'
```
Expected: `stale 경고: 1 이상`, 그리고 그 줄에 `StaleProbe-1`이 들어 있다.
stale 경고가 0이면 `last_state_at_` 기록이나 판정 조건이 잘못된 것이다.

- [ ] **Step 7: 기존 테스트가 깨지지 않았는지 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && \
  colcon test --packages-select libi_fleet 2>&1 | tail -8 && \
  colcon test-result --verbose 2>&1 | tail -20
```
Expected: 실패 0건. 실패가 있으면 이번 변경이 원인인지 먼저 확인한다.

- [ ] **Step 8: 변경 범위 확인 (커밋하지 않는다)**

Run: `git diff --stat aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp`
Expected: 1 file changed, 삭제 0줄(추가만).

---

### Task 6: sim 스크립트 주석을 사실대로 고친다 (R6)

**Files:**
- Modify: `scripts/laptop/sim.sh:126-133`

**Interfaces:** Consumes 없음 / Produces 없음

**배경:** 현재 주석은 상태 어댑터를 "사서 GUI에 이 로봇이 잡히게" 하는 장치로만 설명한다.
실제로는 `fleet_node`가 로봇을 인식하는 **유일한 경로**다. 이 축소된 설명이 진단을 늦췄다.

- [ ] **Step 1: 주석과 실패 메시지를 교체한다**

교체 전:
```bash
# 사서(libi) GUI 에 이 로봇이 잡히게 상태 어댑터를 함께 띄운다.
#   FMS 관제 모니터링은 /pinky{key}/amcl_pose 를 직접 읽어(fleet_telemetry) sim 이 뜨면 바로 보이지만,
#   사서 GUI 는 /robot_state(fleet_link)를 본다 — 그건 amcl_pose 를 재발행하는 이 어댑터가
#   내야 채워진다. 없으면 관제엔 뜨는데 사서 GUI 엔 안 잡힌다.
# 이 로봇 하나만 띄운다(백그라운드, setsid). 정리는 ./kill.sh 가 robot_state_adapter 를 함께 쓸어담는다.
# exec 로 넘어가면 이 셸이 사라지므로 반드시 그 전에 띄운다(어댑터는 amcl_pose 가 늦게 떠도 기다린다).
"$REPO_ROOT/scripts/laptop/robot-link.sh" "$FSM_ROBOT_ID" \
  || echo "[sim] ⚠️ 상태 어댑터 기동 실패 — 사서 GUI 에 로봇이 안 잡힐 수 있습니다(sim 은 계속 진행)"
```

교체 후:
```bash
# 상태 어댑터를 함께 띄운다. **이건 선택 사항이 아니다.**
#
#   fleet_node 는 /robot_state(rmf_fleet_msgs/RobotState)를 구독해서 "어떤 로봇이 어디
#   있는가"를 안다. 그런데 로봇도 sim 도 그 타입을 발행하지 않는다(amcl_pose·battery 만).
#   이 어댑터가 /pinky{key}/amcl_pose 를 읽어 /robot_state 로 재발행하는 **유일한 경로**다.
#   없으면 fleet_node 가 로봇을 0대로 보고 배차도 순회도 시작조차 되지 않는다.
#
#   ⚠️ 그런데 그 상태가 화면에는 정상으로 보인다. FMS 관제 모니터링은
#      /pinky{key}/amcl_pose 를 직접 읽으므로(fleet_telemetry) 로봇이 그대로 뜬다.
#      "패널에 보이니 괜찮겠지"가 2026-07-26 순찰 정지 진단을 몇 시간 늦췄다.
#      사서(libi) GUI 는 /robot_state(fleet_link)를 보므로 여기서만 로봇이 사라진다.
#
#   ⚠️ 프로세스가 떠 있다고 일하고 있는 것도 아니다. amcl_pose 를 못 받으면 어댑터는
#      /robot_state 를 한 번도 발행하지 않는다. 그 상태는 로그의 'amcl_pose 대기' 로 보인다:
#         tail -f /tmp/libi-robot-link/<key>.log
#
# 이 로봇 하나만 띄운다(백그라운드, setsid). 어댑터는 창·세션 수명과 분리돼 있고,
# 정지는 명시적 요청에만 일어난다 — ./kill.sh 또는 robot-link.sh --all --stop.
# exec 로 넘어가면 이 셸이 사라지므로 반드시 그 전에 띄운다(어댑터는 amcl_pose 가 늦게 떠도 기다린다).
"$REPO_ROOT/scripts/laptop/robot-link.sh" "$FSM_ROBOT_ID" \
  || echo "[sim] ⚠️ 상태 어댑터 기동 실패 — fleet_node 가 이 로봇을 인식하지 못해 배차·순회가 동작하지 않습니다. 확인: pgrep -af robot_state_adapter"
```

- [ ] **Step 2: 문법을 확인한다**

Run: `bash -n /home/ane/personal_repo/aba_project/scripts/laptop/sim.sh && echo "문법 OK"`
Expected: `문법 OK`

- [ ] **Step 3: 변경 범위 확인 (커밋하지 않는다)**

Run: `git diff scripts/laptop/sim.sh | grep -E "^\+" | grep -v "^+++" | grep -vE "^\+\s*#" | head`
Expected: 실행되는 코드 줄은 `|| echo ...` 메시지 하나만 바뀐다. 그 외에는 전부 주석이어야 한다.

---

### Task 7: robot_agent의 nav2 기동 API를 복구한다 (R10)

**Files:**
- Modify: `aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py:115-185`
- Create: `scripts/laptop/tests/test_nav2_command_resolves.sh`

**Interfaces:**
- Consumes: 없음
- Produces: `_ros_overlay_setup() -> Path | None` (존재하는 첫 overlay setup 파일), `_ros_env_lines() -> list[str]` (ROS 환경을 만드는 bash 줄들)

**배경:** `ROS_SETUP`이 `/opt/ros/jazzy/setup.bash` 하나로 하드코딩돼 있는데
`pinky_navigation`은 거기 없다. 그래서 `ros2 pkg prefix --share pinky_navigation`이 항상
실패하고 `POST /process/nav2/start`가 무조건 `exit 1` 한다.

**⚠️ overlay 경로를 하드코딩하면 안 된다 (codex 지적 수용).** 실배포와 개발 트리가 다르다:

| 환경 | overlay | 근거 |
|---|---|---|
| 실배포 로봇 | `/home/pinky/pinky_pro/install` | `robot_agent/scripts/service_run.sh:12`, `ros_ws/ros_source.sh:13` |
| 개발 트리 | `<repo>/aba_controller/libi_drive_controller/ros_ws/install` | 이 머신 실측 |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`scripts/laptop/tests/test_nav2_command_resolves.sh`

```bash
#!/usr/bin/env bash
# robot_agent 의 nav2 기동 명령이 실제로 파라미터 파일을 찾아내는지.
#
# 보는 것은 결과다: driving.py 가 만드는 ROS 환경으로 pinky_navigation 이 해석되는가.
# 명령 문자열의 모양이 아니라 **해석 결과**를 본다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DRIVING="$REPO/aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py"
ROS_SETUP=/opt/ros/jazzy/setup.bash
WS_SETUP="$REPO/aba_controller/libi_drive_controller/ros_ws/install/setup.bash"

[ -f "$ROS_SETUP" ] || { echo "SKIP: ROS2 Jazzy 없음"; exit 0; }
[ -f "$WS_SETUP" ]  || { echo "SKIP: ros_ws 미빌드 ($WS_SETUP)"; exit 0; }

FAILED=0
pass_case() { echo "  ✅ $1"; }
fail_case() { echo "  ❌ $1"; FAILED=1; }

ROS_ENV="$REPO/aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py"

echo "[test] ROS 환경 해석 모듈이 존재하는가"
if [ -f "$ROS_ENV" ]; then
  pass_case "app/core/ros_env.py 있음"
else
  fail_case "app/core/ros_env.py 가 없다 — overlay 해석이 웹 스택에 묶여 테스트 불가"
fi

echo "[test] overlay 경로가 하드코딩돼 있지 않은가 (실배포 대응)"
if [ -f "$ROS_ENV" ] && grep -q "LIBI_ROS_WS_SETUP" "$ROS_ENV" && grep -q "pinky_pro/install" "$ROS_ENV"; then
  pass_case "환경변수 주입 + 실배포 후보 경로 둘 다 있음"
else
  fail_case "환경변수 주입 또는 실배포 후보(/home/pinky/pinky_pro/install)가 없다"
fi

echo "[test] 모듈이 웹 스택 없이 import 되는가 (테스트 가능성의 전제)"
if python3 -c "
import importlib.util,sys
spec=importlib.util.spec_from_file_location('t','$ROS_ENV')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert callable(m.env_lines) and callable(m.overlay_setup) and callable(m.overlay_candidates)
" 2>/dev/null; then
  pass_case "표준 라이브러리만으로 import 됨"
else
  fail_case "ros_env.py 를 표준 파이썬으로 import 하지 못한다 — 무거운 의존성이 딸려온다"
fi

echo "[test] 프로덕션 모듈이 만드는 환경 줄로 pinky_navigation 이 해석되는가"
# ⚠️ 소스 텍스트를 잘라 exec 하지 않는다. 그렇게 하면 프로덕션이 아니라 **사본**을 검증하게
#    되고, 무해한 리팩터에도 테스트가 깨지거나 반대로 프로덕션이 갈라져도 통과한다.
#    ros_env.py 는 표준 라이브러리만 쓰므로 FastAPI 없이 그대로 import 된다.
ENV_LINES="$(python3 - "$REPO" <<'PY' 2>/dev/null
import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1])
mod_path = repo / "aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py"
spec = importlib.util.spec_from_file_location("libi_ros_env_under_test", mod_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)          # 패키지 import 를 타지 않으므로 의존성이 딸려오지 않는다
print("\n".join(m.env_lines()))
PY
)"
if [ -z "$ENV_LINES" ]; then
  fail_case "app/core/ros_env.py 에서 env_lines() 를 가져오지 못했다"
else
  RESOLVED="$(bash -c "$ENV_LINES
ros2 pkg prefix --share pinky_navigation 2>/dev/null" 2>/dev/null | tail -1)"
  if [ -n "$RESOLVED" ] && [ -f "$RESOLVED/params/nav2_params.yaml" ]; then
    pass_case "해석됨: $RESOLVED/params/nav2_params.yaml"
  else
    fail_case "ros_env.env_lines() 환경으로는 pinky_navigation/params/nav2_params.yaml 을 못 찾는다 (got '$RESOLVED')"
  fi
fi

echo "[test] driving.py 의 nav2 명령이 그 모듈을 실제로 쓰는가 (사본으로 갈라지지 않았는가)"
# 모듈만 맞고 라우터가 옛 경로를 쓰면 프로덕션은 여전히 깨져 있다. 둘의 연결을 확인한다.
if grep -q "ros_env.env_lines()" "$DRIVING" && grep -q "from app.core import ros_env" "$DRIVING"; then
  pass_case "driving.py 가 ros_env.env_lines() 를 사용"
else
  fail_case "driving.py 가 ros_env 를 import 해서 쓰지 않는다 — 모듈과 라우터가 갈라졌다"
fi

echo "[test] 명시 지정(LIBI_ROS_WS_SETUP)이 폴백에 밀리지 않는가"
# 오타 난 경로를 지정했는데 조용히 다른 overlay 가 쓰이면, 운영자는 자기 지정이 먹은 줄 안다.
# 명시 지정이 있으면 후보는 그것 하나여야 하고, 없으면 '못 찾음' 으로 크게 실패해야 한다.
OVERRIDE_OUT="$(LIBI_ROS_WS_SETUP=/definitely/not/here/setup.bash python3 - "$ROS_ENV" <<'PY' 2>/dev/null
import importlib.util, sys
spec = importlib.util.spec_from_file_location("t", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print("CANDS=" + str(len(m.overlay_candidates())))
print("SETUP=" + str(m.overlay_setup()))
PY
)"
if echo "$OVERRIDE_OUT" | grep -q "^CANDS=1$" && echo "$OVERRIDE_OUT" | grep -q "^SETUP=None$"; then
  pass_case "명시 지정이 유일 후보가 되고, 없으면 폴백 없이 실패한다"
else
  fail_case "명시 지정이 조용히 무시된다 — 오타 난 경로를 줘도 다른 overlay 가 선택됨 ($OVERRIDE_OUT)"
fi

echo "[test] bash 로 넘기는 경로가 인용되는가 (\$·공백 안전)"
QUOTE_OUT="$(python3 - "$ROS_ENV" <<'PY' 2>/dev/null
import importlib.util, os, pathlib, subprocess, sys, tempfile
spec = importlib.util.spec_from_file_location("t", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = tempfile.mkdtemp()
# 리터럴로 $(...) 를 이름에 담은 파일. 인용이 없으면 source 줄에서 명령이 실행된다.
name = "setup$(touch " + d + "/PWNED).bash"
pathlib.Path(d, name).write_text("")
os.environ["LIBI_ROS_WS_SETUP"] = str(pathlib.Path(d, name))
script = "\n".join(m.env_lines()[1:])          # 시스템 ROS 줄은 제외
subprocess.run(["bash", "-c", script], capture_output=True)
print("PWNED=" + str(pathlib.Path(d, "PWNED").exists()))
PY
)"
if [ "$QUOTE_OUT" = "PWNED=False" ]; then
  pass_case "경로가 인용돼 명령 치환이 일어나지 않는다"
else
  fail_case "경로가 인용되지 않아 bash 가 경로 안의 명령을 실행했다 ($QUOTE_OUT)"
fi

echo "[test] 시스템 ROS 만으로는 못 찾는다는 것(=수정이 필요했던 이유) 확인"
ONLY_SYS="$(bash -c "source '$ROS_SETUP' >/dev/null 2>&1 && ros2 pkg prefix --share pinky_navigation 2>/dev/null")"
if [ -z "$ONLY_SYS" ]; then
  pass_case "시스템 ROS 단독으로는 해석 불가 — overlay 가 반드시 필요"
else
  echo "  ℹ️  시스템 ROS 에도 pinky_navigation 이 있다($ONLY_SYS). 이 환경에선 원래 버그가 안 났을 수 있다."
fi

echo
[ "$FAILED" = "0" ] && echo "[test] 전부 통과" || echo "[test] 실패 있음"
exit "$FAILED"
```

Run: `chmod +x /home/ane/personal_repo/aba_project/scripts/laptop/tests/test_nav2_command_resolves.sh`

- [ ] **Step 2: 실패하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_nav2_command_resolves.sh; echo "rc=$?"`
Expected: **FAIL** — `❌ driving.py 가 ROS overlay 를 해석하지 않는다` 등. `rc=1`

- [ ] **Step 3: `driving.py`를 고친다**

3-1. **새 모듈을 만든다** — `aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py`

> **왜 별도 파일인가.** `driving.py`는 FastAPI·`ros_bridge` 등을 import하므로, 이 로직을
> 거기 두면 웹 스택 없이는 테스트할 수 없다(이 레포의 `robot_agent`에는 venv도 없다).
> 그래서 테스트가 소스 텍스트를 잘라 `exec`하는 편법에 의존하게 되고, 그런 테스트는
> **프로덕션 코드가 아니라 자기가 만든 사본을 검증한다.**
> ROS 환경 해석은 그 자체로 하나의 책임이고 표준 라이브러리만 있으면 된다. 분리한다.

```python
"""ROS2 환경(시스템 + 이 프로젝트 워크스페이스 overlay)을 해석한다.

## 왜 이 모듈이 따로 있나

`pinky_navigation` 같은 이 프로젝트의 패키지는 `/opt/ros` 가 아니라 워크스페이스
overlay 아래에 있다. 그런데 프로세스 기동 라우터는 `/opt/ros/jazzy/setup.bash` 만
source 했다. 그래서 `ros2 pkg prefix --share pinky_navigation` 이 **항상** 실패하고
nav2 기동이 통째로 죽었다 (2026-07-26 실측: Package not found → exit 1).

**overlay 경로를 하나로 하드코딩하면 안 된다** — 실배포와 개발 트리가 다르다:

    실배포 로봇 : /home/pinky/pinky_pro/install
                  (robot_agent/scripts/service_run.sh:12, ros_ws/ros_source.sh:13)
    개발 트리   : <repo>/aba_controller/libi_drive_controller/ros_ws/install

배포가 그 밖의 위치를 쓰면 LIBI_ROS_WS_SETUP 환경변수로 직접 지정한다.

## 왜 웹 스택과 분리돼 있나

표준 라이브러리만 쓴다. 그래야 FastAPI 없이 import 되고, 테스트가 **이 코드 자체**를
검증할 수 있다. 라우터 안에 두면 테스트가 소스를 잘라 흉내내게 되고, 그건 프로덕션이
아니라 사본을 검증하는 것이다.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

#: 시스템 ROS2.
SYSTEM_SETUP = Path("/opt/ros/jazzy/setup.bash")

#: 배포가 overlay 위치를 직접 지정할 때 쓰는 환경변수.
OVERLAY_ENV = "LIBI_ROS_WS_SETUP"

#: robot_agent 패키지 루트 (= 이 파일의 app/core 에서 두 단계 위).
_AGENT_ROOT = Path(__file__).resolve().parents[2]


def overlay_candidates() -> list[Path]:
    """overlay setup 파일 후보 — 앞에서부터 존재하는 첫 번째를 쓴다.

    ⚠️ **명시 지정(OVERLAY_ENV)이 있으면 그것 하나만 후보다.** 폴백을 붙이지 않는다.
       예전엔 후보 목록의 맨 앞에만 넣었는데, 그러면 오타 난 경로를 지정했을 때
       그 항목이 존재하지 않는다는 이유로 **조용히 건너뛰고 다른 overlay 를 쓴다.**
       운영자는 자기가 지정한 것이 쓰이는 줄 알고, 실제로는 엉뚱한 워크스페이스로
       nav2 가 뜬다 — 이번 사건 전체가 바로 그 "조용한 오설정" 부류였다.
       하나만 두면 못 찾았을 때 오류 메시지에 그 경로가 그대로 찍혀 오타가 바로 보인다.
    """
    injected = os.environ.get(OVERLAY_ENV)
    if injected:
        return [Path(injected)]

    out: list[Path] = []
    for prefix in (Path("/home/pinky/pinky_pro/install"),
                   _AGENT_ROOT.parent / "ros_ws" / "install"):
        out.append(prefix / "setup.bash")
        out.append(prefix / "local_setup.bash")
    return out


def overlay_setup() -> Path | None:
    """존재하는 첫 overlay setup 파일. 없으면 None."""
    for c in overlay_candidates():
        if c.is_file():
            return c
    return None


def env_lines() -> list[str]:
    """ROS 환경을 만드는 bash 줄들 — 시스템 ROS + 프로젝트 overlay.

    overlay 는 빌드 전이면 없을 수 있다. 없으면 건너뛰고, 그 사실은 호출부가
    오류 메시지에 실어 보낸다 — 배포 문제가 '패키지를 못 찾음' 으로 위장되면 안 된다.

    ⚠️ 경로는 **반드시 shlex.quote 로 감싼다.** 이 문자열은 bash 로 넘어가고,
       경로는 환경변수에서 온다. 큰따옴표로만 감싸면 `$`·백틱이 그 안에서 전개돼
       공백 있는 경로가 깨지는 정도가 아니라 명령이 실행될 수 있다.
    """
    lines = [f"source {shlex.quote(str(SYSTEM_SETUP))} || exit 1"]
    overlay = overlay_setup()
    if overlay is not None:
        lines.append(f"source {shlex.quote(str(overlay))} || exit 1")
    return lines


def describe_candidates() -> str:
    """오류 메시지에 실을 후보 목록 — bash 안전하게 인용된 한 줄."""
    return " | ".join(shlex.quote(str(c)) for c in overlay_candidates())
```

3-2. `driving.py`가 이 모듈을 쓰게 한다. import 블록에 추가:

```python
import shlex

from app.core import ros_env
```

> `shlex`가 필요한 이유: 아래 실패 안내 메시지가 `overlay` 경로를 bash 문자열에 끼워 넣는데,
> 그 경로는 환경변수에서 올 수 있다. 큰따옴표만으로는 `$`·백틱이 전개된다.

3-3. `_build_command`의 nav2 분기 첫 줄을 교체한다.

교체 전:
```python
        return ["bash", "-c", "\n".join([
            f"source {ROS_SETUP} || exit 1",
            "export TURTLEBOT3_MODEL=${TURTLEBOT3_MODEL:-burger}",
```

교체 후:
```python
        overlay = ros_env.overlay_setup()
        return ["bash", "-c", "\n".join([
            *ros_env.env_lines(),
            "export TURTLEBOT3_MODEL=${TURTLEBOT3_MODEL:-burger}",
```

3-4. 실패 안내 메시지를 교체한다. **overlay를 못 찾은 사실을 숨기지 않는다** — 그래야
배포 오류가 "패키지를 못 찾음"으로 위장되지 않는다.

교체 전:
```python
            '  echo "[nav2] ros_ws 에서 colcon build 후 install/setup.bash 를 source 했는지 확인" >&2',
```

교체 후:
```python
            f'  echo "[nav2] ROS overlay: {shlex.quote(str(overlay)) if overlay else "찾지 못함"}" >&2',
            f'  echo "[nav2] overlay 후보: {ros_env.describe_candidates()}" >&2',
            f'  echo "[nav2] 다른 위치에 있으면 {ros_env.OVERLAY_ENV} 환경변수로 지정하세요" >&2',
```

> `ROS_SETUP` 상수는 `slam` 분기와 `_run_ros`가 아직 쓰므로 **지우지 않는다.**
> `slam` 분기도 건드리지 않는다 — 이번에 깨진 것은 nav2 경로이고 최소 변경을 유지한다.

- [ ] **Step 4: 통과하는 것을 확인한다**

Run: `cd /home/ane/personal_repo/aba_project && ./scripts/laptop/tests/test_nav2_command_resolves.sh; echo "rc=$?"`
Expected: `rc=0`

- [ ] **Step 5: 문법을 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project && python3 -c "import ast,pathlib; ast.parse(pathlib.Path('aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py').read_text()); print('문법 OK')"
```
Expected: `문법 OK`

- [ ] **Step 6: 변경 범위 확인 (커밋하지 않는다)**

Run: `git diff --stat aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py`
Expected: 1 file changed.

---

### Task 8: ros2/nav2 기준 순찰 체인 리뷰 (R8)

**Files:**
- Create: `docs/agents/review-patrol-chain-ros2.md`
- Modify: (발견된 문제 중) Task 1~7이 이미 건드린 파일 **안에서만**

**Interfaces:**
- Consumes: Task 1~7의 결과
- Produces: `docs/agents/review-patrol-chain-ros2.md`

**⚠️ 범위 규칙 (사용자가 명시적으로 정했다):** 읽기는 순찰 체인 전체, **수정·삭제는 Task 1~7이
건드린 파일 안에서만.** 그 밖의 발견은 **보고만** 한다.

- [ ] **Step 1: 리뷰 렌즈를 로드한다**

`ros2-engineering-skills`와 `nav2-navigation-skill`을 호출하고 다음을 읽는다:
`references/nodes-executors.md`, `references/communication.md`,
`references/lifecycle-components.md`, `references/debugging.md`

- [ ] **Step 2: 점검 항목을 하나씩 확인한다**

| # | 점검 | 대상 |
|---|---|---|
| 1 | QoS 매칭 — 발행/구독 프로파일 불일치 | `robot_state_adapter.py:39-44`(TRANSIENT_LOCAL 구독), `fleet_node.cpp`의 `/robot_state`·`/libi/fsm_state` 구독, `state_io.py` 발행부 |
| 2 | 콜백 안 블로킹 — 타이머/구독 콜백의 긴 작업·동기 서비스 호출 | `fleet_node.cpp:on_timer`, `robot_state_adapter.py:_tick` |
| 3 | 파라미터 규율 — 선언 없이 쓰는 파라미터, 범위 설명 부재 | `fleet_node.cpp` 생성자 |
| 4 | 조용한 실패 — 실패했는데 로그도 상태 변화도 없는 분기가 또 있는가 | 순찰 체인 전체 |
| 5 | 죽은 코드 — 정의만 있고 아무도 안 쓰는 것 | `libi_modes` 순찰 관련부, `scripts/laptop` |
| 6 | 사실과 다른 주석 | 전체 |
| 7 | 같은 종료 처리 결함 — 다른 rclpy 스크립트에도 이중 shutdown 패턴이 있는가 | `aba_fms_service/scripts/`, `aba_controller/**/scripts/` |
| 8 | `robots_` 만료 없음의 다른 파급 — stale 좌표에 의존하는 다른 판단이 있는가 | `fleet_node.cpp` (도착 판정, 경매, 배터리 관문) |

항목 7은 기계적으로 확인한다:
```bash
cd /home/ane/personal_repo/aba_project
for f in $(grep -rl "rclpy.shutdown()" aba_fms_service/scripts/ aba_controller/ --include="*.py" | grep -v "\.venv"); do
  grep -q "ExternalShutdownException" "$f" || echo "미처리: $f"
done
```

- [ ] **Step 3: 리뷰 문서를 쓴다**

`docs/agents/review-patrol-chain-ros2.md`:

```markdown
# 순찰 체인 ros2/nav2 리뷰 — 2026-07-26

## 요약
(수정 N건 / 보고만 M건 / 확인했고 문제 없음 K건)

## 수정한 것 (Task 1~7 범위 안)
| # | 파일 | 문제 | 조치 |

## 보고만 한 것 (범위 밖 — 사용자 결정 필요)
| # | 파일 | 문제 | 왜 범위 밖인가 | 권고 |

## 확인했고 문제 없던 것
(무엇을 봤고 왜 괜찮은지 — 다음 사람이 같은 곳을 다시 안 뒤지게)
```

**"확인했고 문제 없던 것"을 반드시 채운다.** 빈 리뷰와 꼼꼼히 봤는데 깨끗한 리뷰는 다른
것이고, 그 차이는 이 절에서만 드러난다.

- [ ] **Step 4: 범위 안의 문제를 고치고 회귀를 확인한다**

Run:
```bash
cd /home/ane/personal_repo/aba_project
./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"
./scripts/laptop/tests/test_nav2_command_resolves.sh; echo "rc=$?"
aba_fms_service/backend/.venv/bin/python -m pytest aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py -q 2>&1 | tail -3
```
Expected: 셸 둘 다 `rc=0`, pytest 2 passed

- [ ] **Step 5: 변경 범위 확인 (커밋하지 않는다)**

Run: `git status --short && git diff --stat`
Expected: 변경 파일이 Global Constraints의 허용 목록 안에만 있어야 한다.

---

### Task 9: sim 재현 — 원인 판정과 전체 검증 (R1, US31)

**Files:** 없음 (검증 전용)

**Interfaces:** Consumes Task 1~8 전부 / Produces 검증 로그와 판정 결과

**이 Task가 두 가지를 한다:** ① 고쳐졌음을 증명하고 ② **이번 증상이 경로 A(어댑터가
`amcl_pose`를 못 받아 미발행)였는지 경로 B(어댑터 사망)였는지 판정**한다. Task 1의 경고가
없으면 판정 자체가 불가능하다.

- [ ] **Step 1: 깨끗한 출발선을 만든다 (R3 실사용 검증 겸함)**

Run:
```bash
cd /home/ane/personal_repo/aba_project && ./scripts/laptop/kill.sh 2>&1 | tail -20
sleep 2
pgrep -af robot_state_adapter || echo "어댑터 잔여 없음"
ls /tmp/libi-robot-link/*.pid 2>/dev/null || echo "pid 파일 잔여 없음"
```
Expected: `어댑터 잔여 없음`, `pid 파일 잔여 없음`

- [ ] **Step 2: sim을 헤드리스로 띄운다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/scripts/laptop && \
  ./sim.sh --robot Pinky-sim-1 --domain 90 --no-rviz 2>&1 | tail -25
```
Expected: `[robot-link] ✅ Pinky-sim-1`, `[sim] 브릿지 접두사 /pinkysim1 (domain 90 -> 86)`

- `viewer`를 붙이지 않는다 — Gazebo GUI는 기본 off(`ros_ws/scripts/sim.sh:48` `USE_GUI=false`)다.
- `--no-rviz`를 붙인다 — 창을 띄우지 않고 검증한다. `/plan`은 Step 6에서 토픽으로 직접 확인한다.
- 확인된 사실: Gazebo Harmonic 8.11.0 설치됨, world 파일
  `pinky_navigation/worlds/world2.sdf` 존재.

Gazebo 기동에 실패하면 **멈추지 말고** 실패 출력을 기록한 뒤 Step 7로 건너뛴다.
나머지 검증은 이미 끝났으므로 보고에 "sim 통합 검증 미완 + 사유"를 명시한다.

- [ ] **Step 3: 원인 판정 — 어댑터가 일하고 있는가**

⚠️ **로그만으로 판정하면 안 된다.** 한 로그 안에 `amcl_pose 대기`(초반) → `첫 위치 수신`
→ 이후 사망이 모두 들어 있을 수 있고, 그러면 A로도 B로도 읽힌다. **지금 이 순간의 관측**
두 가지를 함께 봐야 결론이 하나로 정해진다: ① 어댑터 프로세스가 살아 있는가
② 지금 `/robot_state`가 실제로 흐르는가.

Run:
```bash
sleep 30
echo "=== ① 어댑터 프로세스 (현재) ==="
ALIVE=$(pgrep -af "robot_state_adapter.py --robot Pinky-sim-1" | head -1); echo "${ALIVE:-없음}"

echo "=== ② /robot_state 가 지금 흐르는가 (2초 안에 한 건) ==="
source /opt/ros/jazzy/setup.bash
source /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/install/setup.bash
if ROS_DOMAIN_ID=86 timeout 2 ros2 topic echo /robot_state --once >/dev/null 2>&1; then
  FLOW=yes; else FLOW=no; fi
echo "흐름: $FLOW"

echo "=== 참고: 어댑터 로그 꼬리 ==="
tail -20 /tmp/libi-robot-link/pinkysim1.log
```

판정표 — ①과 ②의 **조합**이 §2.2의 A/B를 가른다:

| ① 프로세스 | ② `/robot_state` 흐름 | 판정 | 다음 행동 |
|---|---|---|---|
| 살아 있음 | **흐름 yes** | 정상 동작 | Step 4로 |
| 살아 있음 | **흐름 no** | **경로 A** — 어댑터가 `amcl_pose`를 못 받고 있다 | Step 3-1로 (로그에 `amcl_pose 대기`가 있는지로 교차 확인) |
| 없음 | (무관) | **경로 B** — 어댑터 사망 | 로그 꼬리에 `[robot-link] ... 정지 요청`이 있으면 `--stop`/`kill.sh`가 죽인 것, 없으면 `pkill`·Ctrl+C·세션 종료다 |

판정 결과와 그 근거(①②의 실제 출력)를 최종 보고에 반드시 적는다.

**Step 3-1 (경로 A일 때만)**: 어디서 끊겼는지 두 줄로 가른다.
```bash
source /opt/ros/jazzy/setup.bash
echo "--- sim 도메인(90) 원본 ---"; ROS_DOMAIN_ID=90 timeout 10 ros2 topic echo /amcl_pose --once 2>&1 | head -5
echo "--- 서버 도메인(86) 브릿지 건너편 ---"; ROS_DOMAIN_ID=86 timeout 10 ros2 topic echo /pinkysim1/amcl_pose --once 2>&1 | head -5
```
- 90에 있고 86에 없다 → **도메인 브릿지 문제**
- 90에도 없다 → **AMCL 초기 위치 문제** (`sim.sh`의 init-pose 창 확인)

판정 결과를 최종 보고에 반드시 적는다.

- [ ] **Step 4: `/robot_state`가 실제로 흐르는지 확인한다**

Run:
```bash
source /opt/ros/jazzy/setup.bash && source /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/install/setup.bash && \
  ROS_DOMAIN_ID=86 timeout 15 ros2 topic echo /robot_state --once
```
Expected: `name: Pinky-sim-1` 이 실린 메시지 한 건

- [ ] **Step 5: `fleet_node`가 순찰을 시작하는지 본다**

Run:
```bash
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && source install/setup.bash && \
  ROS_DOMAIN_ID=86 timeout 90 ros2 run libi_fleet fleet_node \
    --ros-args -p navgraph_file:=/home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml -p arrive_radius:=0.05 2>&1 | tee /tmp/fleet_verify.log | tail -40
```

확인:
```bash
echo "GRANT: $(grep -c '(GRANT)' /tmp/fleet_verify.log)"
echo "로봇0대: $(grep -c '로봇 0대' /tmp/fleet_verify.log)"
echo "stale: $(grep -c '로봇 상태 끊김' /tmp/fleet_verify.log)"
```
Expected: **GRANT ≥ 1**, 로봇0대 = 0, stale = 0.
GRANT가 0이면 아직 안 고쳐진 것이다 — Step 3의 판정으로 돌아가 원인을 좁힌다.

- [ ] **Step 6: nav2 `/plan`이 생성되는지 확인한다**

Run:
```bash
source /opt/ros/jazzy/setup.bash && \
  ROS_DOMAIN_ID=90 timeout 30 ros2 topic echo /plan --once 2>&1 | head -20
```
Expected: `poses:` 아래에 좌표가 실린 Path.
**이것이 원래 증상("`/plan`이 안 나온다")의 직접 반증이다.**

- [ ] **Step 7: 정리하고 사후 조건을 확인한다 (R3 재검증)**

Run:
```bash
cd /home/ane/personal_repo/aba_project && ./scripts/laptop/kill.sh 2>&1 | tail -20
sleep 3
pgrep -af robot_state_adapter || echo "어댑터 0개"
ls /tmp/libi-robot-link/*.pid 2>/dev/null || echo "pid 파일 0개"
tmux ls 2>&1 | head -3
```
Expected: `어댑터 0개`, `pid 파일 0개`, tmux 세션 없음

- [ ] **Step 8: 전체 테스트 스위트를 돌린다**

Run:
```bash
cd /home/ane/personal_repo/aba_project
echo "=== 셸 테스트 ==="
./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"
./scripts/laptop/tests/test_nav2_command_resolves.sh; echo "rc=$?"

echo "=== fms backend pytest ==="
cd aba_fms_service/backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -8

echo "=== libi_modes pytest (회귀 없음 확인) ==="
cd /home/ane/personal_repo/aba_project/aba_controller/libi_modes/ros_ws/src/libi_modes && \
  PYTHONPATH=. python3 -m pytest test/ -q 2>&1 | tail -5

echo "=== libi_fleet gtest ==="
cd /home/ane/personal_repo/aba_project/aba_fms_service/fleet_ws && \
  source /opt/ros/jazzy/setup.bash && colcon test --packages-select libi_fleet 2>&1 | tail -5 && \
  colcon test-result --verbose 2>&1 | tail -10
```
Expected: 셸 둘 다 `rc=0`, 파이썬·gtest 실패 0건.
기존에 이미 실패하던 테스트가 있으면 **이번 변경 때문인지 구분해서** 보고한다.

- [ ] **Step 9: 커밋 명령어를 준비한다 (실행하지 않는다)**

최종 보고에 그대로 제시한다. **실행은 사용자 몫이다.**

> ⚠️ 설계문서(`docs/superpowers/specs/...`)와 SDD 작업 디렉토리(`.superpowers/`)는
> `.gitignore:255-256`에 걸려 있다 — 이 레포 관례상 로컬 보관물이고, 같은 디렉토리의
> 기존 설계문서들도 전부 추적되지 않는다. 그래서 커밋 목록에서 뺀다.

```bash
git add \
  .gitignore \
  scripts/laptop/robot-link.sh \
  scripts/laptop/kill.sh \
  scripts/laptop/sim.sh \
  scripts/laptop/tests/test_robot_link_lifecycle.sh \
  scripts/laptop/tests/test_nav2_command_resolves.sh \
  aba_fms_service/scripts/robot_state_adapter.py \
  aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py \
  aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp \
  aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py \
  aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py \
  docs/agents/prd-patrol-regression.md \
  docs/agents/plan-patrol-regression.md \
  docs/agents/review-patrol-chain-ros2.md

git commit -m "fix: 로봇 상태 어댑터의 무증상 실패 두 가지를 드러내고 정리 소유권을 명확히 한다

fleet_node 가 로봇을 인식하는 유일한 경로인 /robot_state 가 끊기는 방법이 두
가지인데 둘 다 무증상이었다. 그동안 관제 패널에는 로봇이 정상으로 보인다
(패널은 amcl_pose 를 직접 읽는다).

  A) 어댑터가 살아 있는데 amcl_pose 를 못 받아 한 번도 발행하지 않음
  B) 어댑터가 창 종료만으로 죽음 (robot-link.sh 의 시그널 트랩)

- robot_state_adapter.py: amcl_pose 대기 상태를 주기적으로 경고하고 첫 수신을
  알린다. ExternalShutdownException 을 정상 종료로 처리해 진짜 사인이
  트레이스백에 덮이지 않게 한다
- robot-link.sh: 트랩 제거(정지는 --stop/kill.sh 에만). 정지 대상을 DB 가 아닌
  pid 파일에서 도출하고, /proc 커맨드라인으로 신원을 확인한 뒤에만 신호를 보낸다
  (pid 재사용 시 무관한 프로세스를 죽이던 것). 정지 흔적을 어댑터 로그에 남긴다
- kill.sh: 어댑터를 명시적으로 정리한다. tmux 종료 뒤에 부른다
- fleet_node.cpp: 로봇 0대와 stale 로봇을 15초 throttle 로 경고한다.
  robots_ 는 만료되지 않으므로 stale 경고가 없으면 '보다가 끊긴' 경우를 못 잡는다
- sim.sh: 어댑터를 '사서 GUI용'으로 설명하던 주석을 정정한다
- driving.py: nav2 기동 시 ROS overlay 를 후보 탐색으로 해석한다
  (실배포 /home/pinky/pinky_pro/install, 개발 트리 ros_ws/install,
   LIBI_ROS_WS_SETUP 으로 주입 가능)
"
```

---

## 요구사항 커버리지 자체체크

| US | 내용 | Task |
|---|---|---|
| 1 | 순찰 정지 원인을 로그 한 줄로 | 1, 5 |
| 2 | `kill.sh` 후 프로세스 0개 | 4(케이스 2), 9 Step 7 |
| 3 | `kill.sh` 안 불렀는데 안 죽음 | 3(케이스 1) |
| 4 | FMS 재시작해도 sim 로봇 인식 유지 | 3 |
| 5 | pid 파일 잔여 0 | 4(케이스 2), 9 Step 7 |
| 6 | 어댑터 되살리면 순찰 즉시 재개 | 9 Step 4~5 |
| 7 | 로봇 0대 경고 | 5 |
| 8 | 경고가 확인 방법을 알려줌 | 5 Step 3 (메시지에 `pgrep`·`amcl_pose 대기`·기동 명령 포함) |
| 9 | 경고가 로그를 안 덮음 | 5 (15초 throttle), Step 5에서 횟수 확인 |
| 10 | 종료 시 사람이 읽을 한 줄 | 2 |
| 11 | 외부 shutdown 시 예외 없이 종료 | 2 |
| 12 | sim.sh 주석이 실제 역할 설명 | 6 |
| 13 | 회귀 테스트로 지켜짐 | 2, 3, 4, 7 |
| 14 | 수정 전 실패를 직접 확인 | 2 Step 2, 3 Step 2, 4 Step 2, 7 Step 2 |
| 15 | ROS2 관례 점검 | 8 |
| 16 | 죽은 코드 정리 | 8 Step 2 항목 5 |
| 17 | 다른 담당 코드 임의 수정 안 함 | Global Constraints, 8 범위 규칙 |
| 18 | 범위 밖 발견은 목록 보고 | 8 Step 3 |
| 19 | 패널 표시와 fleet 인식이 다른 경로임을 앎 | 6, 5(경고 메시지) |
| 20 | nav2 기동 API가 실제로 뜸 | 7 |
| 21 | 실패 메시지가 다음 행동을 알려줌 | 7 Step 3-4 |
| 22 | sim에서 순회를 눈으로 확인 | 9 Step 5~6 |
| 23 | 기존 테스트 안 깨짐 | 5 Step 7, 9 Step 8 |
| 24 | 무관한 주제로 안 번짐 | Global Constraints, 8 Step 5 |
| 25 | 어댑터가 일하고 있는지 앎 | 1 |
| 26 | amcl_pose 미수신을 주기적으로 알림 | 1 Step 4 |
| 27 | 첫 위치 수신을 한 번 알림 | 1 Step 3 |
| 28 | stale 로봇 감지 | 5 Step 3 |
| 29 | 종료 신호 출처가 로그에 남음 | 2 Step 3(어댑터), 4 Step 3(robot-link 흔적) |
| 30 | 정지가 남의 프로세스를 안 죽임 | 4(케이스 3) |
| 31 | 원인이 A인지 B인지 재현으로 판정 | 9 Step 3 |

**missing 0.**

| R | Task |
|---|---|
| R1 | 9 |
| R2 | 3 |
| R3 | 4 |
| R4 | 5 |
| R5 | 2 |
| R6 | 6 |
| R7 | 2·3·4·7 (각 "실패 확인" 단계 포함) |
| R8 | 8 |
| R9 | (스코프 아웃 — 다음 사이클) |
| R10 | 7 |
| R11 | 1 |
| R12 | 2, 4 |
| R13 | 4 |

---

## 실행 순서

Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 **순차 실행**.

**병렬 wave를 쓰지 않는 이유**: Task 1·2가 같은 파이썬 파일을, Task 3·4가 같은 셸 파일과
같은 테스트 파일을 연달아 고친다. Task 8은 1~7의 결과 전체를 읽어야 하고, Task 9는 모든
변경이 들어간 상태에서만 의미가 있다. 파일이 겹치므로 worktree 격리는 이득 없이 비용만
생기고, sim 검증은 실제 `/tmp/libi-robot-link`와 실제 ROS 도메인을 쓰므로 워크스페이스를
복제하면 오히려 검증이 왜곡된다.
