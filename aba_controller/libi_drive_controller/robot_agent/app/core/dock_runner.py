"""정밀 도킹(주차)을 BT 명령 한 번으로 실행하고 결과를 돌려준다.

## 왜 이 파일이 필요한가

주차 절차 자체는 이미 `app/routers/park_dock.py` 의 `_park_loop()` 에 다 있다 —
진입각 정렬(TF yaw) → 테이프 탐색 → **라인 추종(IR)** → 벽 정지(초음파).
문제는 **거기에 도달할 길이 없었다**는 것이다:

⚠️ 주차는 **라인 트레이싱**이다. 같은 저장소의 `aruco_dock` 은 별개 구현이고 이 경로에
없다(관제 화면 수동 버튼 전용). `park_dock` 이 아르코를 쓸 수 있는 자리는 진입각 정렬
하나뿐인데, 아래 `rotate_ref: "odom"` 이라 그것도 안 탄다.

- `pi.sh` 는 `run_fleet_link.py`(ROS 스레드 두 개)만 띄운다. **FastAPI 서버가 없다.**
- `park_dock` 라우터는 그 FastAPI 에 마운트되므로, 서버가 없으면 호출할 방법이 없다.
- 그래서 `RETURNING` 의 명령은 nav2 로 주차장 **근처까지만** 가고 정밀 주차는
  시작조차 안 됐다. `is_docked` 가 안 나오던 것도 그 귀결이다.

HTTP 를 거치지 않고 **같은 프로세스 안에서 그 코루틴을 직접 돌린다.** BT 는 이미
`/fleet_cmd` 로 말하고 있으므로 새 프로토콜이 필요 없다.

## 왜 전용 이벤트 루프인가

`fleet_link` 은 큐 + 평범한 스레드로 돈다(asyncio 아님). `_park_loop` 은 코루틴이다.
명령마다 `asyncio.run()` 을 부르면 매번 루프를 새로 만들어 카메라·모터 클라이언트가
들고 있는 상태가 끊긴다. 그래서 **루프 하나를 데몬 스레드에 띄워 두고** 거기에
코루틴을 던진다.

## ⚠️ 아직 아무도 이 파일을 부르지 않는다

BT 의 복귀 드라이버는 주차장 좌표로 `goal` 을 보낸다 — `action: "dock"` 을 보내는 곳이
없다. 즉 지금 복귀는 정밀 주차를 안 거치고 nav2 로 주차장 정점까지 가고 끝난다.
`/is_docked` 는 `dock_confirm.py` 가 위치(반경 0.12m)로 판정한다.
이 파일을 살리려면 `libi_modes/main.py` 의 `return_dock` 드라이버를 바꿔야 한다.
미결 네 가지: `scripts/drive-pi/dock/README.md`

## ⚠️ 실물 미검증

IR·초음파·모터가 필요해 sim 에서는 돌릴 수 없고, 작성 시점에 실물 접근이 없었다.
실물에서 처음 돌릴 때는 `scripts/drive-pi/dock/` 의 단계별 스크립트로 먼저 확인할 것
(1-line → 2-rotate → 3-approach). 한 번에 돌리면 어디서 틀어졌는지 알 수 없다.
"""

from __future__ import annotations

import asyncio
import math
import threading

#: 주차 진입각(도). `입구 → 주차장` 은 y 가 같은 직선이라 −x 방향, 즉 180°다.
#:   입구   (0.581, −0.033)
#:   주차장 (−0.001, −0.033)
#: ⚠️ waypoint.yaml 의 `입구` yaw 는 90° 다. 그 자세로 도착하면 주차장이 아니라 옆을 본다.
#: 그래서 도킹이 **자기가 회전**한다 — 정점 yaw 를 180° 로 바꾸면 주차와 무관한 다른
#: 주행까지 입구에서 엉뚱한 방향을 보게 된다.
PARK_APPROACH_YAW_DEG = 180.0

#: 도킹 한 번의 상한(초). 넘으면 실패로 보고 로봇을 세운다 — 테이프를 영영 못 찾는
#: 동안 무한정 도는 것보다 실패를 보고하는 편이 낫다.
DOCK_TIMEOUT_SEC = 180.0

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True,
                             name="dock-runner").start()
        return _loop


def park_config(**overrides):
    """주차용 `ParkConfig`. import 를 함수 안에 두는 이유는 아래 참고."""
    # park_dock 은 opencv·카메라·모터를 끌고 온다. 모듈 최상단에서 import 하면
    # 그게 없는 환경(sim, 개발 노트북)에서 fleet_link 전체가 못 뜬다.
    from app.routers.park_dock import ParkConfig

    cfg = {"approach_yaw_deg": PARK_APPROACH_YAW_DEG, "rotate_ref": "odom"}
    cfg.update(overrides)
    return ParkConfig(**cfg)


def run_park(**overrides) -> tuple[bool, str]:
    """주차를 끝까지 돌린다. `(성공?, 사유)`.

    호출한 스레드를 막는다 — `fleet_link` 의 워커가 명령 하나를 끝까지 수행하는
    구조이므로 그게 맞다.
    """
    try:
        from app.routers.park_dock import _park_loop
    except Exception as exc:                                   # noqa: BLE001
        return False, f"주차 모듈을 못 불러옴 ({exc}) — 카메라/OpenCV 확인"

    try:
        cfg = park_config(**overrides)
    except Exception as exc:                                   # noqa: BLE001
        return False, f"주차 설정이 잘못됨: {exc}"

    fut = asyncio.run_coroutine_threadsafe(_park_loop(cfg), _ensure_loop())
    try:
        fut.result(timeout=DOCK_TIMEOUT_SEC)
    except TimeoutError:
        fut.cancel()
        return False, f"주차 시간 초과 ({DOCK_TIMEOUT_SEC:.0f}s)"
    except Exception as exc:                                   # noqa: BLE001
        return False, f"주차 실패: {exc}"

    return docked_now()


def docked_now() -> tuple[bool, str]:
    """주차 상태를 `park_dock` 의 상태에서 읽는다.

    `_park_loop` 은 예외 없이 끝나도 **성공했다는 뜻이 아니다** (탐색 실패로 조용히
    빠져나오는 경로가 있다). 그래서 끝난 사실이 아니라 상태를 근거로 삼는다 —
    `ReturnNavigation` 이 "명령 접수"를 "도착"으로 안 믿는 것과 같은 이유다.
    """
    try:
        from app.routers import park_dock
    except Exception as exc:                                   # noqa: BLE001
        return False, f"주차 상태를 못 읽음 ({exc})"

    payload = park_dock._status_payload()                      # noqa: SLF001
    done = bool(payload.get("docked") or payload.get("finished") or payload.get("done"))
    return done, "" if done else f"주차 미완료: {payload}"


def approach_offset_m(entrance_xy=(0.581, -0.033), dock_xy=(-0.001, -0.033)) -> float:
    """입구에서 주차장까지 직선 거리(m). 접근 구간 길이를 눈으로 확인할 때 쓴다."""
    return math.hypot(dock_xy[0] - entrance_xy[0], dock_xy[1] - entrance_xy[1])
