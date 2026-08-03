"""야간 순찰 파라미터 — 유도값이 범위를 벗어나면 로봇이 밤에 빙빙 돈다."""
from pathlib import Path

import yaml

from libi_modes.ros import state_io

_PARAMS = (Path(__file__).resolve().parents[1]
           / "config" / "params.yaml")

# libi_perception/config.py 의 값. 여기가 바뀌면 lose_sec 도 같이 바꿔야 한다.
SEARCH_PEEK_ANGLE = 1.5708
ANGULAR_Z_SEARCH = 0.35
SEARCH_HOLD_SEC = 5.0


def _sp():
    raw = yaml.safe_load(_PARAMS.read_text(encoding="utf-8"))
    for node in raw.values():
        # ⚠️ 이 파일은 ROS2 표준 `ros__parameters` 래핑이 아니라 flat 구조다
        #    (main.py:_load_params 가 `yaml.safe_load(f)["libi_modes"]` 로 직접 읽는다).
        params = node or {}
        if "security_patrol" in params:
            return params["security_patrol"]
    raise AssertionError("params.yaml 에 security_patrol 블록이 없다")


def test_추종_지속시간이_AI_서버_트리거보다_길다():
    """등록이 추종보다 먼저 오게 하는 유일한 장치다(설계문서 §18.2).

    ⚠️ AI 쪽 값을 **직접 import** 한다. 리터럴 `1.0` 과 비교하면 AI 의
    `SecurityParams.trigger_sec` 이 바뀌어도 이 시험이 계속 초록이라 아무것도
    검증하지 않는다.
    """
    import sys
    # parents[6] = 레포/워크트리 루트 (aba_controller 와 aba_ai_service 가 형제).
    sys.path.insert(0, str(Path(__file__).resolve().parents[6]
                           / "aba_ai_service" / "follower_perception"))
    from scripts.security_recorder import SecurityParams

    assert _sp()["intruder_sustain"] > SecurityParams().trigger_sec


def test_상실_시계가_탐색_훑기_시작보다_짧다():
    """넘으면 인지 쪽 탐색이 ±90° 훑기·180° 회전까지 진입해 로봇이 빙빙 돈다."""
    peek = SEARCH_PEEK_ANGLE / ANGULAR_Z_SEARCH
    assert _sp()["intruder_lose_sec"] < peek + SEARCH_HOLD_SEC


def test_실패_백오프가_0_보다_크다():
    """0 이면 owner 가 없을 때 트리거→실패→재트리거가 무한 반복해 순찰이 마비된다."""
    assert _sp()["failure_backoff_sec"] > 0


def test_추종_잎이_BT_스냅샷_접붙임점에_등록돼_있다():
    """안 넣으면 추종 하위 트리가 관제 BT 화면에서 통째로 사라진다."""
    assert "IntruderChase" in state_io._GRAFT_POINTS
