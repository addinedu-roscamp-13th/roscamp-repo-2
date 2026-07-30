"""대상을 놓친 **첫 순간**, 어느 쪽으로 사라졌는지 분류한다. 순수 함수다.

## 왜 방향이 중요한가

지금 파이프라인은 대상을 놓쳐도 `COAST_LIMIT` 프레임만큼 예측 위치로 계속 따라간다
(alpha-beta 스무더). 그 자체는 옳다 — 문틀·서가에 잠깐 가려질 때마다 추종이 끊기면
못 쓴다. 문제는 **어느 쪽으로 사라졌는지 안 본다**는 것이다.

    옆으로 사라짐   지나갔을 뿐이다               → 계속 따라가도 된다
    아래로 사라짐   로봇 코앞이거나 쓰러졌다      → 계속 밀고 들어가면 들이받는다
    위로 사라짐     누가 집어 올렸거나 시야 위    → 마지막 위치로 가 봐야 없다
    중앙에서 사라짐 가려졌다                      → 계속 따라가도 된다

좌표는 이미지 좌표다 — **y 는 아래로 증가한다.** `velocity` 는 `BBoxSmoother.velocity`
형태의 `[vcx, vcy, varea]` 다.

## 판정 우선순위

정지 쪽(`down`/`up`)이 진행 쪽(`side`/`center`)보다 먼저 이긴다. 가장자리 두 곳에
동시에 걸릴 수 있는데(예: 왼쪽 아래 모서리), 그때 "옆"으로 읽으면 코앞에 있는
대상을 향해 계속 전진한다.
"""
from .constants import EXIT_AREA_SURGE, EXIT_EDGE_MARGIN_RATIO

SIDE = "side"
DOWN = "down"
UP = "up"
CENTER = "center"

#: 예측 추종을 허용하는 방향. 나머지는 즉시 소실로 처리한다.
_COASTABLE = (SIDE, CENTER)


def classify_exit(bbox, velocity, frame_w, frame_h,
                  margin_ratio: float = EXIT_EDGE_MARGIN_RATIO,
                  area_surge: float = EXIT_AREA_SURGE) -> str:
    """`bbox`=(x1,y1,x2,y2), `velocity`=[vcx,vcy,varea] → 방향 문자열."""
    if not bbox or frame_w <= 0 or frame_h <= 0:
        return CENTER               # 판단 근거가 없다 — 기존 동작(예측 추종)을 유지한다
    x1, y1, x2, y2 = bbox
    vx = float(velocity[0]) if velocity is not None else 0.0
    vy = float(velocity[1]) if velocity is not None else 0.0
    varea = float(velocity[2]) if velocity is not None and len(velocity) > 2 else 0.0

    mx = frame_w * margin_ratio
    my = frame_h * margin_ratio
    at_bottom = y2 >= frame_h - my
    at_top = y1 <= my
    at_side = x1 <= mx or x2 >= frame_w - mx

    # 면적 급증은 가장자리와 무관하게 "아주 가까워졌다"는 뜻이다. 프레임 안에서
    # 커지다 사라지는 경우(코앞으로 다가와 시야를 덮음)를 가장자리 검사로는 못 잡는다.
    if varea >= area_surge:
        return DOWN
    if at_bottom and vy > 0:
        return DOWN
    if at_top and vy < 0:
        return UP
    if at_side and abs(vx) > abs(vy):
        return SIDE
    return CENTER


def may_coast(direction, last_posture) -> bool:
    """예측 추종을 허용할지.

    마지막 자세가 `Standing` 이 아니었으면 방향과 무관하게 막는다 — 쓰러지는 중이던
    대상을 예측 위치로 쫓아가는 것은 정확히 피하려던 상황이다.
    `last_posture` 가 None 이면 판정 소스가 없다는 뜻이라 막지 않는다.
    """
    if last_posture is not None and last_posture != "Standing":
        return False
    return direction in _COASTABLE
