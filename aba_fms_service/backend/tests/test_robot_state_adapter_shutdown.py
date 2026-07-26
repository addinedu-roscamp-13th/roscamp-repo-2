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
    #   간헐적으로 뿜는다(실측 14회 중 2회):
    #       rclpy/signals.py:56 __del__    → AttributeError: ... '_GuardCondition__gc'
    #       rclpy/executors.py:305 __del__ → AttributeError: ... '_sigint_gc'
    #   전부 rclpy 내부 파일에서 나고, 종료 코드는 0이며 우리 종료 로그도 정상으로 남는다.
    #   우리가 고칠 수 있는 것이 아니고 이번 버그와도 무관하다. 넓게 단정하면 그 잡음에
    #   테스트가 흔들려 **아무것도 못 지키는 테스트**가 된다. 그래서 우리 것만 본다.
    assert "rcl_shutdown already called" not in out, f"이중 shutdown 이 여전히 난다:\n{out}"

    # 트레이스백이 **있어도 되는 경우가 하나 있다**: 종료와 겹친 예상 밖 예외를
    # 어댑터가 일부러 진단용으로 찍는 경우다(그 앞에 전용 문구가 붙는다).
    # 그건 예외 "누출" 이 아니라 의도된 기록이다. 반대로 그 문구 없이 트레이스백만
    # 나왔다면 처리되지 않은 예외가 샌 것이므로 실패로 본다.
    if "Traceback" in out:
        assert "종료 중 예상치 못한 예외" in out, (
            f"처리되지 않은 트레이스백이 남았다(의도된 진단 문구가 없다):\n{out}"
        )
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
