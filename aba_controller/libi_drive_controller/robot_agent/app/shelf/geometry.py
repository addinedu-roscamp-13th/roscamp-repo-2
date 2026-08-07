"""목표 지점까지의 이동을 **회전과 직진**으로 분해한다.

## ⚠️ 옆으로 못 간다

PinkyPro 는 차동구동이다(`pinky_bringup/config/pinky_params.yaml`
`wheel_separation: 0.0961`). 노트의 "x축과 y축으로 개루프 odom" 은 **이동량을 두 축으로
분해해 구한다**는 뜻이지 두 축을 따로 주행한다는 뜻이 아니다. 실제 주행은
목표 방위로 돌아 → 직진 → 최종 자세로 다시 도는 세 동작이다.

## 복귀

나갈 때 만든 이동 목록과 그때의 `heading`(진행 방향)·`final_yaw`(도착 자세)를 받아,
복귀 시작 시점(= 이미 `final_yaw` 로 서 있다)에서 나갈 때의 반대 방향
(`heading + π`)을 향해 돌고 같은 거리를 **전진**한다. 후진하지 않는 이유: 차동구동은
전진이 더 똑바로 가고, 다 갔을 때 이미 진행 방향을 보고 있어 회전이 한 번 줄어든다.

⚠️ 회전량은 `π` 가 **아니다** — `final_yaw == heading` 일 때만 우연히 π 와 같다.
2026-08-03 리뷰가 실측 왕복오차 0.2448m 로 잡았다: `approach_moves` 의 마지막 동작이
로봇을 `heading` 이 아니라 `final_yaw` 로 다시 돌려놓으므로, 복귀는 거기서
`wrap_pi(heading + π − final_yaw)` 만큼 돌아야 한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TURN, DRIVE = "turn", "drive"


@dataclass(frozen=True)
class Move:
    kind: str
    value: float
    #: TURN 일 때의 **map 프레임 절대 목표 yaw**(rad). `None` 이면 예전대로 `value`(상대각)를
    #: odom 으로 적분해 돈다.
    #:
    #: 왜 필요한가 — 목표는 언제나 map 절대 자세인데 실행이 odom 상대 회전이면 그 사이
    #: 오차가 남고, 회전이 이어지면서 **누적된다.** 도킹은 2026-08-05 에 이 이유로
    #: `turn_to_map_yaw`(매 tick AMCL yaw 재측정)로 갈아탔는데(`shelf_dock.py` 의
    #: MAP_YAW_ANG 주석: "다른 거 보고 갔어"), 복귀(`backup`)만 옛 방식이 남아 있었다.
    #: 복귀는 체크포인트 2개 × (TURN·DRIVE·TURN) = **회전 6번**이라 누적이 더 크다.
    abs_yaw: float | None = None


def wrap_pi(a: float) -> float:
    """각을 [-pi, pi) 로 접는다."""
    return (float(a) + math.pi) % (2 * math.pi) - math.pi


def approach_moves(rx: float, ry: float, ryaw: float,
                   tx: float, ty: float, clearance: float,
                   final_yaw: float) -> list:
    """로봇 `(rx, ry, ryaw)` 에서 목표 `(tx, ty)` 까지. 마지막에 `final_yaw` 로 선다.

    `clearance` 만큼 목표 **앞에서** 멈춘다(서가를 밀지 않으려고). 이미 그보다 가까우면
    직진은 0 이다 — 음수 직진(후진)으로 새지 않게 한다.
    """
    dx, dy = float(tx) - float(rx), float(ty) - float(ry)
    heading = math.atan2(dy, dx)
    dist = math.hypot(dx, dy) - float(clearance)
    if dist < 0.0:
        dist = 0.0
    # 두 회전 다 **map 절대 목표**를 같이 싣는다(`Move.abs_yaw`). 상대각(`value`)은
    # map pose 를 못 얻는 경로를 위한 예비값으로 그대로 둔다 — 실행 쪽이 고른다.
    return [
        Move(TURN, wrap_pi(heading - float(ryaw)), abs_yaw=heading),
        Move(DRIVE, dist),
        Move(TURN, wrap_pi(float(final_yaw) - heading), abs_yaw=wrap_pi(float(final_yaw))),
    ]


def axis_aligned_moves(rx: float, ry: float, ryaw: float,
                       tx: float, ty: float, final_yaw: float) -> list:
    """목표까지 x축, 이어 y축 순서로 가는 회전·직진 목록.

    각 축의 이동량이 0이면 그 축의 회전·직진은 생략한다. 차동구동 로봇은 각
    축 구간마다 몸을 돌려 전진하므로, 옆으로 미끄러지는 동작은 없다.
    """
    moves: list[Move] = []
    yaw = float(ryaw)
    dx = float(tx) - float(rx)
    if not math.isclose(dx, 0.0, abs_tol=1e-9):
        x_yaw = 0.0 if dx > 0.0 else math.pi
        moves.extend((Move(TURN, wrap_pi(x_yaw - yaw)), Move(DRIVE, abs(dx))))
        yaw = x_yaw

    dy = float(ty) - float(ry)
    if not math.isclose(dy, 0.0, abs_tol=1e-9):
        y_yaw = math.pi / 2 if dy > 0.0 else -math.pi / 2
        moves.extend((Move(TURN, wrap_pi(y_yaw - yaw)), Move(DRIVE, abs(dy))))
        yaw = y_yaw

    moves.append(Move(TURN, wrap_pi(float(final_yaw) - yaw)))
    return moves


def retreat_moves(rx: float, ry: float, ryaw: float,
                  tx: float, ty: float, face_yaw: float | None = None) -> list:
    """목표 쪽으로 **등을 돌려** 후진으로 간다 — 코를 돌려 전진하는 `approach_moves`
    의 짝이다.

    서가 도킹을 마치고 빠져나올 때 쓴다.

    ## ⚠️ 회전량이 아니라 **회전 방향**이 문제다 (2026-08-07 실기: 꽁무늬가 닿았다)

    도킹이 끝난 로봇은 서가와 **나란히** 선다: `FINAL_YAW_RAD`(180°) 인데 서가는
    `SHELF_YAW`(±90°) 쪽, 즉 **몸 옆구리 3cm** 옆에 있다(팔이 옆으로 뻗으라고 그렇게
    세운다 — `shelf_dock.py` 두 상수 주석). 빠져나갈 방향은 그 반대편(∓90°)이라
    **어느 쪽으로 가든 제자리 회전 90° 를 피할 수 없다.** 그러니 "안 돌기"는 답이 아니다.

    답은 **어느 쪽이 서가를 쓸고 지나가느냐**다. 3cm 는 로봇을 반지름 0.06 **원**으로
    친 값이고 실제 PinkyPro 는 원이 아니다 — 꽁무늬가 그 원 밖으로 나온다.

        approach_moves : 코를 목표(서가 반대편)로 돌린다 → 꽁무늬가 **서가 쪽으로**
                         돌아 그 자리에 선다. 쓸고 지나간다. ← 닿은 게 이것이다
        retreat_moves  : 코를 서가 쪽으로 돌린다 → 꽁무늬가 **서가 반대편으로** 빠진다.
                         회전 내내 꽁무늬가 서가에서 멀어지기만 한다.

    두 서가 다 성립한다(문학 +90°/과학-인문학 −90°): 꽁무늬는 180°에서 0° 로 시작해
    항상 서가의 **반대쪽** ∓90° 로 빠지고, 도중에 서가 쪽을 스치지 않는다.

    ⚠️ 대신 **코가 서가를 마주 본 채 끝난다.** 앞쪽 돌출이 뒤보다 크면 이 맞바꿈이
    손해다 — 그때는 이 함수가 아니라 `FINAL_YAW_RAD` 를 다시 본다.

    ⚠️ 끝나도 로봇은 여전히 서가를 보고 있다. 다음 다리인 nav2 주행이 알아서 돌린다 —
    그 자리(`side_start_pose`)는 nav2 가 데려다 준 진입점이라 돌 자리가 있다.

    ## `face_yaw` — 방위를 **map 상수로 못박는다** (권장)

    안 주면 코가 볼 방위를 두 점의 `atan2` 로 구한다. 그런데 그 두 점은 **AMCL 자세
    두 개**고, 서가에서 물러나는 거리는 `hit_dist − CLEARANCE_M` 이라 **수 cm 밖에
    안 되는 경우가 있다.** 몇 cm 떨어진 두 점의 각도는 AMCL 잡음(±2~3cm)에 그대로
    휘둘린다 — 짧을수록 심해서, 최악에는 방위가 사실상 무작위가 된다. 그러면 위에서
    보장한 "꽁무늬가 서가 반대편으로 빠진다" 가 **깨진다.**

    서가에서 물러나는 방향은 추정할 게 아니라 **이미 아는 값**이다: 서가 법선
    `SHELF_YAW[shelf]`. 그걸 그대로 주면 회전 목표가 잡음과 무관해진다.
    (거리는 그대로 두 점 사이 거리를 쓴다 — 거리 오차는 방위 오차와 달리 몇 cm 어긋나는
    데서 끝나고, 그건 다음 다리 nav2 가 흡수한다.)

    ⚠️ **부호가 서가마다 다르다** — 문학·예술 `+1.5708`, 과학-인문학 `−1.5708`.
       한 값으로 못박으면 반대쪽 서가에서 꽁무늬가 정확히 서가로 돌아간다(고치기 전
       동작 그대로). 그래서 상수가 아니라 `SHELF_YAW[shelf]` 를 실어 보낸다.
    """
    dx, dy = float(tx) - float(rx), float(ty) - float(ry)
    dist = math.hypot(dx, dy)
    if dist <= 0.0:
        return []
    # 코가 볼 방위. map 상수를 받았으면 그걸 쓰고, 없으면 두 점에서 추정한다.
    nose_yaw = wrap_pi(float(face_yaw)) if face_yaw is not None \
        else wrap_pi(math.atan2(dy, dx) + math.pi)
    return [Move(TURN, wrap_pi(nose_yaw - float(ryaw)), abs_yaw=nose_yaw),
            Move(DRIVE, -dist)]


def return_moves(outbound: list, heading: float, final_yaw: float) -> list:
    """나갈 때의 이동 목록을 되돌리는 이동 목록.

    `heading` 은 나갈 때의 진행 방향(`approach_moves` 호출 시의 `atan2(dy, dx)`),
    `final_yaw` 는 나갈 때 마지막에 선 자세 — 둘 다 `approach_moves` 를 부른 쪽이
    같이 들고 있어야 한다(`Move` 목록만으로는 복원이 안 된다).
    """
    dist = sum(m.value for m in outbound if m.kind == DRIVE)
    if dist <= 0.0:
        return []
    return [Move(TURN, wrap_pi(float(heading) + math.pi - float(final_yaw))),
            Move(DRIVE, dist)]
