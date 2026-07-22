"""액션 leaf 의 start/poll/stop 을 `/fleet_cmd` 왕복으로 구현한다.

로봇에는 이미 `robot_agent` 의 `fleet_link` 가 떠 있다 — `/fleet_cmd`(JSON) 을 구독해 실행하고
`/fleet_cmd_result` 로 결과를 돌려준다. 요청/응답을 `id` 로 대조하는 비동기 구조라
`DriverAction` 이 요구하는 모양과 그대로 맞는다. 새 프로토콜을 만들 이유가 없다.

    start()  명령 JSON 발행하고 즉시 리턴 (응답 안 기다림)
    poll()   그동안 도착한 result 중 내 id 가 있는지 확인만
    stop()   취소 명령 발행

`poll()` 이 절대 블로킹하지 않는 게 핵심이다. 여기서 응답을 기다리면 rclpy 실행기가
같은 스레드에서 spin 하지 못해 result 가 영영 안 오고 트리 전체가 굳는다.
"""
import json


class FleetCmdDriver:
    """`/fleet_cmd` 한 종류의 액션을 담당하는 드라이버.

    action: robot_agent fleet_link 의 `_dispatch` 가 아는 액션 이름 ("goal", "home", ...)
    args_fn: 호출 시점에 인자 dict 를 만드는 콜러블 (목적지가 매번 다르므로)
    """

    def __init__(self, node, action, args_fn=None, *, timeout_sec=120.0):
        self._node = node
        self._action = action
        self._args_fn = args_fn or (lambda: {})
        self._timeout_sec = timeout_sec
        self._log = node.get_logger()

        self._pending_id = None
        self._started_at = None
        self._results = {}          # id -> (ok, msg)
        self._seq = 0

    # 노드가 /fleet_cmd_result 구독 콜백에서 넘겨준다 (드라이버마다 구독하지 않는다).
    def on_result(self, payload):
        cmd_id = payload.get("id")
        if cmd_id:
            self._results[cmd_id] = (bool(payload.get("ok")), str(payload.get("msg", "")))

    def _now(self):
        return self._node.get_clock().now().nanoseconds / 1e9

    def start(self):
        self._seq += 1
        self._pending_id = f"{self._action}-{self._seq}-{int(self._now() * 1000)}"
        self._started_at = self._now()
        self._publish(self._action, self._args_fn(), self._pending_id)

    def poll(self):
        if self._pending_id is None:
            return "running"
        got = self._results.pop(self._pending_id, None)
        if got is not None:
            ok, msg = got
            if not ok:
                self._log.warning(f"{self._action} 실패: {msg}")
            self._pending_id = None
            return "success" if ok else "failure"
        if self._now() - self._started_at >= self._timeout_sec:
            self._log.warning(f"{self._action} 응답 없음 ({self._timeout_sec}s) — 실패 처리")
            self._pending_id = None
            return "failure"
        return "running"

    def stop(self):
        if self._pending_id is None:
            return
        self._publish("stop", {}, f"stop-{self._pending_id}")
        self._pending_id = None

    def _publish(self, action, args, cmd_id):
        self._cmd_pub.publish_json({"id": cmd_id, "ts": self._now(), "action": action, "args": args})

    # 노드가 주입한다 (발행자는 하나만 두고 드라이버들이 공유).
    def bind(self, cmd_pub):
        self._cmd_pub = cmd_pub
        return self


class ArmHomeDriver:
    """`ReturnNavigation` 이 요구하는 `go_home()` 하나짜리 인터페이스.

    팔을 홈 자세로 보내는 건 주행 시작 전 한 번이고 결과를 기다리지 않는다 —
    기다리면 tick 이 막힌다. 실패는 도킹 단계에서 드러난다.
    """

    def __init__(self, cmd_driver):
        self._driver = cmd_driver

    def go_home(self):
        self._driver.start()
