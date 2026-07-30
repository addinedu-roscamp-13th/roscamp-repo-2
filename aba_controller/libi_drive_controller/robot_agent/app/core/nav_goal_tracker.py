"""nav2 목표 핸들의 수명만 다루는 작은 상태기. **ROS 를 모른다.**

## 왜 떼어냈나

취소 로직이 `ros_bridge` 의 중첩 노드 클래스 안에 있어서, 고치고도 **맞는지 확인할 방법이
없었다** — 검증하려면 rclpy 초기화와 액션 서버가 필요하다. 여기서 다루는 것은 순수한
수명 규칙 세 가지뿐이라 ROS 없이 시험할 수 있다:

  ① 응답이 오면 핸들을 보관한다
  ② 취소가 응답보다 **먼저** 오면, 응답 시점에 그 핸들을 취소한다
  ③ 새 목표를 내면 이전 핸들을 취소하고, 낡은 세대의 응답은 버린다

②가 없으면 "취소했는데 로봇이 계속 간다". ③이 없으면 옛 목표 핸들이 고아가 되어
취소가 엉뚱한 목표로 나간다. 둘 다 타이밍 문제라 실기에서 재현하기 어렵고,
그래서 더욱 단위테스트로 못박아야 한다.

핸들은 `cancel_goal_async()` 와 `accepted` 만 있으면 되는 오리 타입이다.
"""


class NavGoalTracker:
    def __init__(self):
        self._handle = None
        self._generation = 0
        self._cancel_pending = False

    @property
    def active_handle(self):
        return self._handle

    def begin(self) -> int:
        """새 목표를 내기 직전에 부른다. 반환한 세대 번호를 응답 콜백에 실어 보낸다.

        ## [2026-07-30] 여기서 **이전 목표를 취소하지 않는다.** 그게 순회를 세우고 있었다

        예전 코드는 `prev` 가 있으면 `_safe_cancel(prev)` 를 했다(위 ③의 원래 구현).
        의도는 "고아 핸들을 남기지 않는다"였는데, **nav2 는 같은 액션에 새 goal 이 오면
        선점(preemption)으로 이전 목표를 알아서 대체한다.** 즉 취소는 불필요했고,
        불필요한 정도가 아니라 **해로웠다** — nav2 는 취소를 받으면 규정대로 바퀴를 세운다:

            [navigate_to_pose] [ActionServer] Client requested to cancel the goal. Cancelling.
            [navigate_to_pose] [ActionServer] Aborting handle.
            [controller_server] Cancellation was successful. **Stopping the robot.**

        선점이었다면 `Received goal preemption request` → `Begin navigating` 으로 끊김 없이
        이어졌을 자리다.

        ## 왜 간헐적이었나 — 경쟁 조건

        `prev` 는 이전 goal 의 **응답 콜백이 도착해야** 채워진다. 그래서:

            응답이 이미 왔다 → 취소 발생 → 로봇 정지 → (재시도까지 20~30초)
            응답이 아직이다 → 취소 없음 → 깨끗한 선점 → 매끄럽게 진행

        2026-07-30 실측 한 판(약 7분): 선점 22건 vs 취소 9건이 섞여 있었다. 취소는 **항상
        새 goal 전송과 같은 순간(±0.02초)** 이었다 — 밖에서 끼어든 게 아니라 우리가 낸 것이다.
        그날 이 증상을 열(스로틀)과 `default_server_timeout` 으로 세 번 오진했는데, 둘 다
        **응답 타이밍을 바꿔 발생 빈도만 흔들었을 뿐** 원인이 아니었다.

        ## 취소가 사라진 게 아니다

        명시적 정지(`cancel()`)는 그대로다 — 그게 "사람을 놓치면 멈춘다"를 지키는 경로다.
        바뀐 것은 **새 목표를 낼 때 이전 것을 끊지 않는다**는 것 하나뿐이고, 그 자리는
        nav2 선점이 대신한다.

        ⚠️ 되돌림 신호: 목표를 바꿨는데 **로봇이 옛 목표로 계속 가면** 선점이 안 된 것이다.
           그때는 여기서 다시 취소해야 한다(그리고 왜 선점이 안 되는지부터 봐야 한다 —
           보통 두 goal 이 **서로 다른 액션**으로 갈 때 그렇다. 지금은 navigate_to_pose 하나다).
        """
        self._handle = None
        self._generation += 1
        self._cancel_pending = False
        return self._generation

    def on_response(self, handle, generation=None) -> None:
        """goal 응답이 왔을 때. `handle` 이 None 이거나 거절이면 아무것도 보관하지 않는다."""
        if handle is None or not getattr(handle, "accepted", True):
            return
        if generation is not None and generation != self._generation:
            # 이미 대체된 목표의 응답이다. **그냥 버리면 고아가 된다** — nav2 는 그
            # 목표를 받아들였으므로, 우리가 잊는다고 로봇이 멈추지 않는다.
            # 새 목표가 선점하더라도 취소를 명시적으로 보내는 편이 결정적이다.
            _safe_cancel(handle)
            return
        if self._cancel_pending:
            self._cancel_pending = False
            _safe_cancel(handle)
            return
        self._handle = handle

    def cancel(self) -> bool:
        """취소한다. 핸들이 아직 없으면 의사를 남기고 응답 시점에 갚는다.

        반환값은 "지금 즉시 취소했는가"다(대기로 넘어갔으면 False).
        """
        handle, self._handle = self._handle, None
        if handle is None:
            self._cancel_pending = True
            return False
        _safe_cancel(handle)
        return True

    def is_current(self, generation) -> bool:
        """이 세대가 아직 살아 있는 목표인가.

        결과 콜백이 **자기 목표의 결과인지** 가릴 때 쓴다. 안 가리면 이전 목표의
        결과가 새 목표의 완료로 보고돼, 아직 도착하지도 않았는데 다음 단계로 넘어간다.
        """
        return generation is None or generation == self._generation

    def clear(self) -> None:
        """목표가 자연 종료됐을 때."""
        self._handle = None
        self._cancel_pending = False


def _safe_cancel(handle) -> None:
    try:
        handle.cancel_goal_async()
    except Exception:       # noqa: BLE001 — 취소 실패로 호출자 스레드를 죽이지 않는다
        pass
