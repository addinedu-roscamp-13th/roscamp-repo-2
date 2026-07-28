"""명령이 끊기면 모터를 세운다.

## 왜 있나

`twist_callback` 은 메시지가 올 때만 RPM 을 쓴다. 워치독이 없으면 **상위 노드가 죽는
순간 로봇이 마지막 속도로 계속 굴러간다.** 30Hz 타이머는 odom 만 발행했다.

2026-07-28 에 `/cmd_vel` 발행자를 twist_mux 하나로 모으면서(중재자 도입) 이 위험이
커졌다. 예전엔 발행자가 10개라 하나 죽어도 다른 게 계속 밀었지만, 이제 twist_mux 가
단일 실패점이다. 이 워치독이 그 짝이다.

판정은 `cmd_watchdog.py` 로 떼어 뒀다 — `bringup.py` 는 로봇 전용 `dynamixel_sdk` 를
import 해서, 거기 두면 하드웨어 없는 곳에서 시험조차 못 한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pinky_bringup.cmd_watchdog import CMD_VEL_TIMEOUT_SEC, command_expired  # noqa: E402


def test_no_command_yet_is_not_expired():
    """한 번도 못 받은 상태는 만료가 아니다.

    로봇은 애초에 안 움직이고 있다. 부팅 직후 매 tick 정지 명령을 쏘면 시리얼만 낭비하고,
    정작 첫 실제 명령이 그 뒤로 밀린다.
    """
    assert command_expired(100.0, None) is False


def test_fresh_command_is_not_expired():
    assert command_expired(100.0, 99.9) is False


def test_old_command_is_expired():
    """이 파일의 존재 이유 — 여기가 False 면 발행자가 죽어도 바퀴가 계속 돈다."""
    assert command_expired(100.0, 99.0) is True


def test_the_boundary_is_exclusive():
    """정확히 timeout 만큼 지난 것은 아직 살아 있다. 20Hz 제어에서 한 프레임 늦은 것을
    죽은 것으로 치면 정상 주행이 매번 끊긴다."""
    assert command_expired(100.0, 100.0 - CMD_VEL_TIMEOUT_SEC) is False
    assert command_expired(100.0, 100.0 - CMD_VEL_TIMEOUT_SEC - 1e-6) is True


def test_timeout_matches_the_mux_input_timeout():
    """twist_mux 입력 timeout(0.5s)과 같아야 한다. 다르면 두 층이 서로 다른 순간에
    "발행자가 죽었다"고 판단해, 그 사이 구간의 동작이 설명되지 않는다.
      설정: pinky_bringup/config/twist_mux.yaml
    """
    import pathlib

    import yaml
    cfg = pathlib.Path(__file__).resolve().parents[1] / "config" / "twist_mux.yaml"
    topics = yaml.safe_load(cfg.read_text())["twist_mux"]["ros__parameters"]["topics"]
    assert {t["timeout"] for t in topics.values()} == {CMD_VEL_TIMEOUT_SEC}
