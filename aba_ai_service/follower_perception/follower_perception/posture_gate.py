"""자세 문자열 → 주행 가부. ROS 도 YOLO 도 모른다 — 그래서 로봇 없이 시험된다.

## `Unknown` 을 곧바로 정지로 치지 않는 이유

`posture.classify_posture()` 는 어깨 2점·골반 2점의 신뢰도가 **전부** 기준을 넘어야
판정한다. 사람이 옆으로 서 있기만 해도 반대쪽 어깨·골반이 가려져 `Unknown` 이 난다.
즉시 정지로 두면 정상 추종 중에도 계속 멈칫한다.

그래서 `Unknown` 은 **직전 판정을 유지**하고, 연속 N 프레임을 넘겨야 정지한다.
N 프레임 동안은 "판정 실패"이지 "누워 있음"이 아니다.

## `Calibrating` 이 정지인 이유

기준 비율을 재는 동안에는 판정 자체가 성립하지 않는다. 이때 움직이면 틀린 기준으로
판정하며 주행하게 된다. 등록 직후 몇 초 서 있는 편이 낫다(패널이 그 사실을 표시한다).

## `Side` 가 정지이지만 `lost` 는 아닌 이유

거리 추정이 `√bbox area` 라, 사람이 옆을 보면 bbox 폭이 줄어 "멀어졌다"로 오판해
로봇이 들이받는다 — 그래서 `Side` 는 즉시 정지 목록에 있다. 다만 이건 "놓쳤다"는
뜻이 아니다: 요청자가 서가를 보며 따라오는 것은 정상이고 흔하다. 그래서 별도
소비자인 `libi_perception/follow_node.requester_visible` 은 안내 미션에서 `Side` 를
일부러 "여전히 따라오는 중"으로 취급한다 — 여기(`PostureGate`)는 주행 가부만 정지시키고,
"보이는가"는 그쪽이 따로 판단한다.
"""
from .constants import UNKNOWN_STOP_FRAMES

STANDING = "Standing"
LYING = "Lying"
UNKNOWN = "Unknown"
CALIBRATING = "Calibrating"
SIDE = "Side"

#: 즉시 정지시키는 판정들. 여기 없는 값은 전부 `Unknown` 취급이다 —
#: 모르는 문자열이 들어왔을 때 "주행 허용"으로 새는 것이 가장 위험하다.
#:
#: [2026-07-31] `Side` 추가. 사람이 옆을 보면 bbox 폭이 줄어 `√area` 거리 추정이
#: "멀다"로 오판하고 로봇이 들이받는다. 자세 판정을 넣은 원래 목적이 이것이다.
_STOP_NOW = (LYING, CALIBRATING, SIDE)


class PostureGate:
    def __init__(self, unknown_limit: int = UNKNOWN_STOP_FRAMES):
        self.unknown_limit = max(1, int(unknown_limit))
        self._allowed = True
        self._unknown_run = 0

    def reset(self) -> None:
        """등록·카메라 전환처럼 판정 이력이 의미를 잃는 순간에 부른다."""
        self._allowed = True
        self._unknown_run = 0

    @property
    def allowed(self) -> bool:
        return self._allowed

    @property
    def unknown_run(self) -> int:
        return self._unknown_run

    def update(self, posture) -> bool:
        if posture is None:
            # 판정 소스가 아예 없다(자세 모델 없는 배포, 옛 payload).
            # 근거 없이 세우면 그런 배포에서 추종이 통째로 죽는다.
            self._unknown_run = 0
            self._allowed = True
        elif posture == STANDING:
            self._unknown_run = 0
            self._allowed = True
        elif posture in _STOP_NOW:
            self._unknown_run = 0
            self._allowed = False
        else:
            self._unknown_run += 1
            if self._unknown_run >= self.unknown_limit:
                self._allowed = False
        return self._allowed
