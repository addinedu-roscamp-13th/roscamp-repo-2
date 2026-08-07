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


def test_상실_시계는_더_이상_쓰이지_않는다():
    """⚠️ [2026-08-07] `intruder_lose_sec` 을 걷어냈다.

    예전 규칙은 "탐색 훑기 시작보다 짧아야 한다"(안 그러면 밤에 빙빙 돈다)였는데,
    그 유도가 **탐색 시작 기준**이라 소실→탐색 진입 지연(약 3.4초)이 빠져 있었다.
    실제로는 회복 트리에 1.6초밖에 안 남아 탐색이 아예 안 돌았다.

    이제 소실 판정과 회복 종료는 인지 쪽이 하고, 밤에 도는 시간은 `max_chase_sec`
    하나가 막는다. params.yaml 에 값이 남아 있어도 **읽지 않는다** —
    `branches/security_patrol.py` 가 `ChasePolicy` 에 안 넘긴다.
    """
    from libi_modes.common.intruder_chase import ChasePolicy
    assert not hasattr(ChasePolicy(), "lose_sec"), \
        "되살렸으면 회복 탐색이 다시 안 돈다 — ChasePolicy 머리말 참고"
    assert _sp()["max_chase_sec"] > 0, "이제 이것이 유일한 시계다"


def test_실패_백오프가_0_보다_크다():
    """0 이면 owner 가 없을 때 트리거→실패→재트리거가 무한 반복해 순찰이 마비된다."""
    assert _sp()["failure_backoff_sec"] > 0


def test_추종_잎이_BT_스냅샷_접붙임점에_등록돼_있다():
    """안 넣으면 추종 하위 트리가 관제 BT 화면에서 통째로 사라진다."""
    assert "IntruderChase" in state_io._GRAFT_POINTS
