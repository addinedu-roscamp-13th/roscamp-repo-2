"""팔 동작을 `ArmTask` 액션으로 중계하는 드라이버.

`/fleet_cmd{perform_action}` 을 받은 BT 의 `ArmExec` 가 이 드라이버를 통해 팔 보드의
액션 서버(`arm_task`)를 부른다. 계약은 다른 드라이버(`FleetCmdDriver`)와 같다:

    start()  goal 을 보내고 즉시 리턴 (응답 안 기다림)
    poll()   "running" | "success" | "failure"
    stop()   진행 중인 goal 을 취소

## 왜 토픽이 아니라 액션인가

취소·중복방어·진행보고·서버감지를 직접 구현하지 않기 위해서다. 2026-07-30 하루에 관제 명령이
조용히 사라지는 경로 3개를 고쳤고 그중 2개가 정지 전파·명령 유실이었다 — 전부 요청/결과
상관관계와 취소를 손으로 구현한 층에서 났다.
상세: 옵시디언 `presen/final/14 로봇팔 통합 - 토픽 대신 액션.md`

## ⚠️ 절대 블로킹하지 않는다

`poll()` 이 future 를 기다리면 rclpy 실행기가 같은 스레드에서 spin 하지 못해 결과가 영영
안 오고 **트리 전체가 굳는다.** 그래서 `.done()` 만 확인하고 바로 리턴한다.
`FleetCmdDriver` 가 같은 이유로 같은 모양이다.

## 서버가 없을 때

팔 보드가 안 떠 있으면 `start()` 에서 즉시 실패로 표시한다. 기다리지 않는 이유: 팔이 없는
배포(주행만 검증)에서 팔 명령 하나가 트리를 120초 붙잡으면 그 뒤 모든 다리가 밀린다.
`ArmExec` 이 실패를 받으면 `CommandTimeout` 이 아니라 정상 경로로 ERROR 까지 간다.
"""
from libi_modes.arm_task_map import to_goal_fields


class ArmStubDriver:
    """팔 보드가 아직 없는 배포에서 팔 다리를 **성공으로 통과시킨다.**

    왜 실패가 아니라 성공인가: 이 로봇은 주행만으로도 배달 시나리오를 데모한다. 팔 다리를
    실패로 두면 첫 집기에서 주문이 FAILED 로 떨어져 그 뒤 주행 검증을 아예 못 한다.
    이 자리에서 결과를 **올리지 않는다** — robot_agent 의 `fleet_link` 가 답한다
    (`main.ARM_VIA_BT` 주석: 답하는 쪽은 하나여야 한다).
    """

    def __init__(self, node):
        self._log = node.get_logger()

    def start(self):
        self._log.warning("팔 스텁 — 동작 없이 성공 처리 (LIBI_ARM_VIA_BT=1 이면 실제 중계)")

    def poll(self):
        return "success"

    def stop(self):
        pass


class HandyActionDriver:
    """`arm_task` 액션 클라이언트. `args_fn()` 이 `/fleet_cmd` 의 args dict 를 돌려준다.

    `result_fn(ok, msg)` 을 주면 **완료 시 한 번** 부른다 — 노드가 그걸 `/fleet_cmd_result`
    로 올려 FMS 의 다리를 닫는다. 안 주면 결과를 아무도 안 올린다(robot_agent 가 답하는
    배포. `LIBI_ARM_VIA_BT` 주석 참고).
    """

    def __init__(self, node, args_fn, *, action_name="arm_task", timeout_sec=120.0,
                 result_fn=None):
        from rclpy.action import ActionClient
        from libi_interfaces.action import ArmTask

        self._node = node
        self._args_fn = args_fn
        self._timeout_sec = timeout_sec
        self._result_fn = result_fn
        self._log = node.get_logger()
        self._ArmTask = ArmTask
        self._client = ActionClient(node, ArmTask, action_name)

        self._send_future = None     # goal 수락 여부를 담은 future
        self._handle = None          # 수락된 goal handle
        self._result_future = None
        self._failed = False         # start 단계에서 이미 실패로 확정
        self._started_at = None
        #: **수락 전에 취소한 goal 을 쫓아가 끄기 위한 자리.** dict 를 쓰는 이유는 아래
        #  `_on_goal_response` 콜백이 실행기 스레드에서 늦게 돌기 때문이다 — 그때 이미
        #  `_reset()` 이 지나가 인스턴스 필드는 다음 goal 것으로 바뀌어 있을 수 있다.
        self._pending = None

    # ── 계약 ────────────────────────────────────────────────────────────────
    def start(self):
        self._reset()
        self._started_at = self._now()

        fields = to_goal_fields(self._args_fn())
        if fields is None:
            # 모르는 액션·장소다. 지어내지 않고 실패시킨다 — 추측해서 보내면 팔이
            # 엉뚱한 데로 간다(arm_task_map 머리말 참고).
            self._log.warning(f"팔 명령을 goal 로 옮길 수 없다: {self._args_fn()!r}")
            self._fail("팔 명령을 goal 로 옮길 수 없다 (액션·장소·좌표 불량)")
            return

        if not self._client.server_is_ready():
            self._log.warning(
                "팔 액션 서버(arm_task)가 없다 — 실패 처리. "
                "팔 보드가 떠 있는지, ROS_DOMAIN_ID·RMW 가 주행 로봇과 같은지 확인할 것")
            self._fail("팔 액션 서버(arm_task) 없음")
            return

        goal = self._ArmTask.Goal(**fields)
        self._log.info(
            f"팔 goal: {goal.action} {goal.object} {goal.from_place}→{goal.to_place}"
            f"{f' [{goal.book}]' if goal.book else ''}"
            f"{f' tier={goal.tier} row={goal.row}' if goal.tier or goal.row else ''}"
            f"{f' slot={goal.slot}' if goal.slot else ''}")
        self._pending = pending = {"cancel": False}
        self._send_future = self._client.send_goal_async(
            goal, feedback_callback=self._on_feedback)
        # ⚠️ **수락 전에 취소한 goal 을 쫓아간다.** 정지·타임아웃이 수락보다 먼저 오면
        #    아직 handle 이 없어 취소할 대상이 없다. 그대로 잊으면 서버는 뒤늦게 수락하고
        #    **아무도 안 보는 팔이 계속 움직인다** — 그 사이 주행이 시작되면 팔을 뻗은 채
        #    로봇이 간다. 그래서 취소 의사를 여기에 남겨 콜백이 대신 끊는다.
        self._send_future.add_done_callback(
            lambda fut, p=pending: self._on_goal_response(fut, p))

    def poll(self):
        if self._failed:
            return "failure"

        # ① goal 수락 대기
        if self._handle is None:
            if self._send_future is None:
                return "running"                 # start 전 (있을 수 없지만 방어)
            if not self._send_future.done():
                return self._running_or_timeout()
            try:
                handle = self._send_future.result()
            except Exception as e:                # noqa: BLE001 — 링크 단절·직렬화 실패 등
                self._log.warning(f"팔 goal 전송이 예외로 끝났다: {e}")
                self._fail(f"goal 전송 실패: {e}")
                return "failure"
            if handle is None or not handle.accepted:
                self._log.warning("팔이 goal 을 거절했다 (인자 불량이거나 이미 실행 중)")
                self._fail("팔이 goal 을 거절했다")
                return "failure"
            self._handle = handle
            self._result_future = handle.get_result_async()

        # ② 결과 대기
        if self._result_future is not None and self._result_future.done():
            try:
                wrapped = self._result_future.result()
            except Exception as e:                # noqa: BLE001
                self._log.warning(f"팔 결과를 읽지 못했다: {e}")
                self._fail(f"결과 수신 실패: {e}")
                return "failure"
            res = getattr(wrapped, "result", None)
            ok = bool(getattr(res, "ok", False))
            msg = str(getattr(res, "msg", "") or "")
            if not ok:
                self._log.warning(f"팔 동작 실패: {msg}")
            self._reset()
            self._report(ok, msg)
            return "success" if ok else "failure"

        return self._running_or_timeout()

    def stop(self):
        """진행 중인 goal 을 취소한다. 응답은 기다리지 않는다.

        취소가 액션 프로토콜에 있다는 것이 이 드라이버의 핵심 이득이다 — 정지 전파를
        직접 구현하지 않는다.

        수락 전이면 취소할 handle 이 없으므로 **취소 의사만 남긴다**(`_pending`) —
        `_on_goal_response` 가 뒤늦게 수락된 goal 을 끊는다.
        """
        if self._pending is not None:
            self._pending["cancel"] = True
        if self._handle is not None:
            self._handle.cancel_goal_async()
        self._reset()

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _on_goal_response(self, fut, pending):
        """실행기 스레드에서 도는 콜백 — 취소한 goal 이 뒤늦게 수락되면 즉시 끊는다."""
        if not pending.get("cancel"):
            return                       # 정상 경로는 poll() 이 처리한다
        try:
            handle = fut.result()
        except Exception:                # noqa: BLE001 — 어차피 취소 대상이다
            return
        if handle is not None and getattr(handle, "accepted", False):
            self._log.warning("취소한 뒤 수락된 팔 goal 을 끊는다 (고아 goal 방지)")
            handle.cancel_goal_async()

    def _fail(self, msg):
        """실패로 확정하고 결과를 한 번 올린다."""
        self._failed = True
        self._report(False, msg)

    def _report(self, ok, msg):
        if self._result_fn is not None:
            self._result_fn(bool(ok), str(msg or ""))

    def _on_feedback(self, msg):
        phase = getattr(getattr(msg, "feedback", None), "phase", "")
        if phase:
            self._log.debug(f"팔 진행: {phase}")

    def _running_or_timeout(self):
        if self._started_at is not None and self._now() - self._started_at >= self._timeout_sec:
            self._log.warning(f"팔 응답 없음 ({self._timeout_sec}s) — 실패 처리")
            # 취소를 보내고 정리한다. 수락 전이었어도 `stop()` 이 취소 의사를 남기므로
            # 뒤늦게 수락된 goal 까지 끊긴다 (`_on_goal_response`).
            self.stop()
            self._fail(f"팔 응답 없음 ({self._timeout_sec}s)")
            return "failure"
        return "running"

    def _now(self):
        return self._node.get_clock().now().nanoseconds / 1e9

    def _reset(self):
        self._send_future = None
        self._handle = None
        self._result_future = None
        self._failed = False
        self._started_at = None
        self._pending = None
