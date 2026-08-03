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
from .constants import EXIT_AREA_SURGE, EXIT_EDGE_MARGIN_RATIO, FRAME_DT

#: "아래로 빠졌다"로 보는 최소 세로속도 — 프레임 높이 대비 비율(**px/frame**).
#: 240px 프레임에서 0.02 = 4.8px/frame. 검출 흔들림(1~2px)은 여유 있게 걸러지고
#: 실제 낙하·접근만 남는다.
#
# ⚠️ [2026-08-02] **단위를 맞췄다 — 20배 예민했다.**
#   `BBoxSmoother.velocity` 는 `velocity += (beta/dt) * residual` 라 **px/초**다.
#   그런데 여기와 `EXIT_AREA_SURGE` 는 주석대로 **프레임당** 값이다. 그대로 비교하니
#   실효 임계가 `4.8 px/sec = 0.24 px/frame` 이 되어, **정상적으로 다가오는 사람도**
#   `DOWN` 으로 찍혔다. `DOWN` 은 `_COASTABLE` 이 아니라 코스팅이 통째로 꺼진다 —
#   사용자 보고 2026-08-02: "알파베타 필터가 잘 안 나오네, 가끔은 보이기는 해".
#   ("가끔"은 노이즈가 마침 위쪽이라 임계를 안 넘긴 순간이다.)
#
#   참조 구현(`arte_libi_perception`)은 이 게이트가 **아예 없어서** 잘 됐다.
#   게이트 자체는 옳다(코앞·낙하로 밀고 들어가면 안 된다) — 단위만 고친다.
_DOWN_VY_FRAC = 0.02

SIDE = "side"
DOWN = "down"
UP = "up"
CENTER = "center"

#: 예측 추종을 허용하는 방향. 나머지는 즉시 소실로 처리한다.
_COASTABLE = (SIDE, CENTER)

#: 예측 추종을 **막는** 자세. 나머지는 허용한다.
#
# ⚠️ [2026-08-02] **허용목록(Standing 만)에서 차단목록으로 뒤집었다.**
#
#   예전 규칙은 `last_posture != "Standing"` 이면 무조건 차단이었다. 그런데
#   **따라가는 사람은 대부분 옆이나 등을 보인다** — 그러면 자세가 `Side` 이거나
#   어깨·골반 신뢰도가 모자라 `Unknown` 이 된다. 즉 정상 추종의 대부분 구간에서
#   α-β 코스팅이 **통째로 꺼져 있었다.** 사용자 보고 2026-08-02:
#   "bbox 사라지면 알파베타 필터가 적용이 잘 안 되네".
#
#   그 규칙의 원래 목적은 문서에 적힌 대로 **"쓰러지는 중이던 대상을 예측 위치로
#   쫓아가지 마라"** 하나다. 그건 `Lying` 이지 `Side` 가 아니다. `control_loop` 도
#   2026-08-01 에 이미 "측면은 놓친 게 아니라 거리만 못 믿는 것"으로 되돌렸는데
#   (전진만 막고 방위는 유지) 여기만 옛 규칙에 남아 있었다.
#
#   `Calibrating` 도 막는다 — 기준을 재는 중이라 자세 판정 자체를 못 믿는다.
#
#   ⚠️ 차단목록이라 모르는 자세 이름은 **허용**된다. 그게 안전한 이유:
#     · 코스팅 상한이 1.4초이고 최대 전진이 0.06 m/s 라 최악 8cm 다
#     · 로봇 코앞으로 오는 경우는 자세와 무관하게 `DOWN` 방향이 따로 막는다
#     · 그 아래를 라이다 하드 스톱(9cm)이 받친다
#   새 자세 이름이 위험하다고 판단되면 여기 추가하는 것이 유일한 할 일이다.
_NO_COAST_POSTURES = ("Lying", "Calibrating")


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
    # ⚠️ 속도는 px/**초**, 임계는 **프레임당**이다 — 프레임당으로 환산해 비교한다.
    #    근거는 `_DOWN_VY_FRAC` 주석.
    vy_per_frame = vy * FRAME_DT
    varea_per_frame = varea * FRAME_DT
    if varea_per_frame >= area_surge:
        return DOWN
    # ⚠️ [2026-08-01] `vy > 0` 이 아니라 **뚜렷하게 아래로 움직일 때만** DOWN 이다.
    #
    #   따라가는 사람은 가까우면 **발이 늘 화면 아래 가장자리에 닿아 있다**
    #   (`at_bottom` 이 상시 참). 거기서 `vy > 0` 만 보면 검출이 한 픽셀 흔들려
    #   vy 가 +0.3 만 돼도 DOWN 으로 떨어지고, DOWN 은 `_COASTABLE` 이 아니라
    #   **코스팅이 통째로 건너뛰어진다** — 잠깐 가려진 것도 즉시 소실로 처리돼
    #   회복 탐색이 바로 돈다(실측 2026-08-01: "사라지면 바로 peek 된다").
    #
    #   진짜 "아래로 빠짐"은 프레임 높이에 비해 의미 있는 속도를 갖는다. 임계를
    #   두면 검출 흔들림은 걸러지고 실제 낙하·접근만 남는다. 0 이면 예전 동작.
    if at_bottom and vy_per_frame > frame_h * _DOWN_VY_FRAC:
        return DOWN
    if at_top and vy_per_frame < 0:
        return UP
    if at_side and abs(vx) > abs(vy):
        return SIDE
    return CENTER


def may_coast(direction, last_posture) -> bool:
    """예측 추종을 허용할지.

    마지막 자세가 `_NO_COAST_POSTURES`(Lying·Calibrating)면 방향과 무관하게 막는다 —
    쓰러지는 중이던 대상을 예측 위치로 쫓아가는 것은 정확히 피하려던 상황이다.
    그 외(Standing·Side·Unknown·None)는 허용한다 — 근거는 `_NO_COAST_POSTURES` 주석.
    """
    if last_posture in _NO_COAST_POSTURES:
        return False
    return direction in _COASTABLE
