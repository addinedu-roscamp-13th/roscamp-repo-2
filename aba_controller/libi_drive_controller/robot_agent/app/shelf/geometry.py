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
