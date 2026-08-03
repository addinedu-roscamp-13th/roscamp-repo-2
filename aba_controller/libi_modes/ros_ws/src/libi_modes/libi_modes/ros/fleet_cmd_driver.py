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

    def __init__(self, node, action, args_fn=None, *, timeout_sec=120.0,
                 stop_action="stop"):
        self._node = node
        self._action = action
        self._args_fn = args_fn or (lambda: {})
        self._timeout_sec = timeout_sec
        #: `stop()` 이 보낼 액션 이름. 세션 드라이버는 `"follow_stop"` 을 써야 한다.
        #
        # `"stop"` 은 **두 소비자가 서로 다른 뜻으로 받는다**:
        #   follow_node  : 실린 id 의 세션을 닫는다 (session.py target_session_id)
        #   fleet_link   : id 와 **무관하게** nav2 목표를 취소한다 (fleet_link.py `stop` 분기)
        # 후자는 "응대중인데 바퀴가 계속 돈다"를 고치려고 일부러 넣은 것이라 옳다.
        #
        # 문제는 추종 세션을 닫으려고 낸 `stop` 이 **형제 leaf 가 방금 낸 주행까지**
        # 죽인다는 것이다. 실측 2026-07-28 `/fleet_cmd`::
        #
        #     t=172.393  goal-1               NavigationExec 이 nav2 목표를 냈다
        #     t=172.404  stop-follow_admin-1  11ms 뒤 FollowExec 이 세션을 닫으며 냈다
        #     t=182.489  goal-2               재전송(arrive_resend_sec)까지 10초를 서 있었다
        #
        # `follow_stop` 은 follow_node 의 `STOP_ACTIONS` 에 이미 있고(follow_node.py),
        # fleet_link 의 nav 취소 분기는 `action == "stop"` **정확히 일치**만 잡는다.
        # 그래서 액션 이름만 바꾸면 세션은 닫히고 주행은 안 끊긴다.
        self._stop_action = stop_action
        self._log = node.get_logger()

        self._pending_id = None
        #: 타임아웃으로 **포기한** 명령의 id. 응답이 없다고 상대가 안 도는 게 아니라서,
        #: 뒤따르는 `stop()` 이 그 id 로 취소를 보낼 수 있게 남겨 둔다.
        #: 이게 없으면 `poll()` 이 `_pending_id` 를 지운 뒤 `stop()` 이 조용히 아무것도
        #: 안 해서, 원격 세션(추종 루프 등)이 고아로 계속 돈다.
        self._abandoned_id = None
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

    def start(self, args=None):
        """`args` 를 주면 `args_fn` 대신 그것을 싣는다.

        ⚠️ 같은 액션인데 **상황에 따라 인자가 달라지는** 경우에만 쓴다. `args_fn` 은
        노드가 만들 때 고정되므로 leaf 의 그때그때 상태(예: 회복 회전 허가 여부)를
        담을 수 없다. 그렇다고 leaf 상태를 노드까지 끌어내리면 배선이 한 겹 늘고,
        "누가 그 값을 쓰는지" 가 코드에서 안 보인다. 호출부에서 명시하는 쪽이 낫다.
        (`GuideExec._allow_rotate` 가 유일한 사용처다.)
        """
        self._seq += 1
        self._abandoned_id = None       # 새 명령을 내면 옛 포기 기록은 의미가 없다
        self._pending_id = f"{self._action}-{self._seq}-{int(self._now() * 1000)}"
        self._started_at = self._now()
        self._publish(self._action, self._args_fn() if args is None else args,
                      self._pending_id)

    def poll(self):
        if self._pending_id is None:
            return "running"
        got = self._results.pop(self._pending_id, None)
        if got is not None:
            ok, msg = got
            if not ok:
                self._log.warning(f"{self._action} 실패: {msg}")
            # 결과가 왔다 = 상대가 그 명령을 끝냈다. 취소할 것이 없다.
            self._pending_id = None
            self._abandoned_id = None
            return "success" if ok else "failure"
        if self._now() - self._started_at >= self._timeout_sec:
            self._log.warning(f"{self._action} 응답 없음 ({self._timeout_sec}s) — 실패 처리")
            # **id 를 버리지 않는다.** 응답이 없다는 건 상대가 안 돈다는 뜻이 아니다 —
            # 오히려 아직 돌고 있을 가능성이 크다. 여기서 지우면 곧이어 불리는
            # `stop()` 이 early return 으로 아무것도 안 보내고, 원격 세션이 고아가 된다.
            # (실측 경로: FollowExec 타임아웃 → terminate → stop() 무동작 →
            #  follow_node 의 ControlLoop 이 계속 돌며 /cmd_vel 을 민다)
            self._abandoned_id = self._pending_id
            self._pending_id = None
            return "failure"
        return "running"

    def stop(self):
        # 포기한 id 도 취소 대상이다 — 위 타임아웃 주석 참고.
        cmd_id = self._pending_id or self._abandoned_id
        if cmd_id is None:
            return
        # ⚠️ **id 접두어는 액션 이름과 무관하게 `stop-` 이다.** 세션을 찾는 쪽
        # (session.py `target_session_id`)이 `"stop-"` 만 벗겨 낸다. 여기서 접두어를
        # 액션 이름에 맞춰 바꾸면 세션 id 가 안 맞아 **조용히 아무것도 안 닫힌다.**
        self._publish(self._stop_action, {}, f"stop-{cmd_id}")
        self._pending_id = None
        self._abandoned_id = None

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


class YawGoalDriver:
    """제자리 회전을 `goal` 하나로 표현한다. `start(target_yaw)` 로 부른다.

    ## 왜 /cmd_vel 을 직접 안 쓰나

    libi_modes 는 지금 `/cmd_vel` 을 발행하지 않는다. 회전 때문에 발행자를 하나 더
    만들면, 중재자(twist_mux)가 없는 이 시스템에서 **조용한 경합 창이 하나 늘어난다** —
    마지막에 도착한 명령이 이기므로 누가 이겼는지 로그로 가릴 수도 없다.

    같은 x·y 에 목표 yaw 만 다른 `goal` 을 보내면 nav2 가 제자리 회전으로 처리한다.
    실행 경로도 기존 주행과 완전히 같아진다(같은 통로, 같은 결과 상관).

    현재 위치를 모르면 회전 명령을 보내지 않는다 — 좌표 없이 goal 을 내면 실행 층이
    `args["x"]` 에서 죽는다.
    """

    def __init__(self, cmd_driver, pose_fn):
        self._driver = cmd_driver
        self._pose_fn = pose_fn
        self._target_yaw = 0.0
        self._driver._args_fn = self._args

    def _args(self):
        pose = self._pose_fn() or {}
        return {"x": pose.get("x", 0.0), "y": pose.get("y", 0.0), "yaw": self._target_yaw}

    def start(self, target_yaw=0.0):
        if not self._pose_fn():
            return                      # 위치를 모른다 — 보내지 않는다
        self._target_yaw = float(target_yaw)
        self._driver.start()

    def poll(self):
        return self._driver.poll()

    def stop(self):
        self._driver.stop()


class NudgeDriver:
    """정해진 속도를 정해진 시간만큼 밀어 **개루프로 몇 cm** 를 움직인다.

    복귀 ⑤단계(`DockNudge`)가 쓴다. ArUco 접근이 6cm 에서 손을 떼면, 마커가 카메라
    화각을 벗어나는 마지막 구간이 남는다. 그 구간은 볼 수단이 없으므로 시간으로 민다.

    ## ⚠️ `/cmd_vel` 이 아니라 `cmd_vel_dock` 이다

    `/cmd_vel` 직접 발행은 twist_mux.yaml 이 금지한다 — 중재를 우회하면 "마지막에
    도착한 명령이 이긴다"로 되돌아간다. `dock` 입력(priority 120)으로 보내면
    비상정지(255)와 FSM hold(160)에는 지고 나머지는 이긴다. 그게 맞는 서열이다:
    **주차 중에도 비상정지는 통해야 하고, 새어 나온 nav2 목표에는 져선 안 된다.**

    ## ⚠️ 왜 BT tick 이 아니라 자기 타이머로 미는가

    BT 는 5Hz 로 돌고, 이 로봇은 CPU 부하로 tick 이 밀린다(2026-07-30 실측: nav2 가
    제어 주기를 놓칠 정도). tick 에 얹어 발행하면 부하가 튈 때 명령이 0.5초 이상
    끊기고, 그러면 twist_mux 가 입력을 버리고 모터 워치독이 로봇을 세운다 —
    **개루프 거리가 부하에 따라 달라진다.** 그래서 20Hz 전용 타이머로 민다.

    ## ⚠️ 끝나고 0 을 실제로 낸다

    발행만 멈추면 twist_mux timeout(0.5s)·모터 워치독(0.5s)이 세울 때까지 굴러간다.
    0.08m/s 면 그동안 4cm 를 더 간다 — 3cm 를 재려는 판에 **이동량보다 크다.**
    그래서 주행이 끝나면 0 을 명시적으로 낸다.

    ## ⚠️ [2026-07-30] 시간이 아니라 **발행**을 기준으로 센다 (codex 리뷰 지적)

    처음엔 `start()` 시각으로 종료 시각을 박고, 0 구간도 "종료 + 0.3초"로 두었다.
    둘 다 지터에 샜다:

      ① `start()` 는 아무것도 발행하지 않는다. 첫 timer callback 이 위상에 따라
         0~50ms 뒤에 오는데 종료 시각은 `start()` 기준이라 **매번 그만큼 짧게** 갔다.
         부하로 첫 callback 이 더 밀리면(수백 ms 는 실제로 일어난다) 그만큼 더 짧아진다.
         → 종료 시각을 **첫 발행 tick** 에서 정한다. 그러면 실제로 민 시간이 곧 `duration` 이다.

      ② 0 구간을 시각으로 재면, 주행 종료 직후 callback 이 0.3초 넘게 밀렸을 때
         `now >= _zero_until` 로 바로 빠져 **0 을 한 번도 안 낸다.** 그러면 위에서 막으려던
         "워치독이 세울 때까지 2.5cm 더 감"이 그대로 되살아난다.
         → 0 을 **횟수로** 센다. 몇 번 밀리든 0 은 반드시 그만큼 나간다.
    """

    def __init__(self, node, topic, *, distance_m, speed_mps,
                 rate_hz=20.0, zero_sec=0.3):
        from geometry_msgs.msg import Twist

        self._Twist = Twist
        self._pub = node.create_publisher(Twist, topic, 10)
        self._clock = node.get_clock()
        self._log = node.get_logger()
        #: 속도 부호가 방향이다. **음수 = 후진** (충전 단자가 뒤에 있다).
        self._speed = float(speed_mps)
        self._duration = abs(float(distance_m) / self._speed) if self._speed else 0.0
        #: 주행 뒤 낼 0 의 **개수**. 시각이 아니라 개수라야 지터에 안 샌다(위 ② 참고).
        self._zero_ticks = max(1, round(float(zero_sec) * float(rate_hz)))
        self._deadline = None           # 첫 발행 tick 에서 정한다
        self._zeroed = 0
        self._active = False
        node.create_timer(1.0 / float(rate_hz), self._tick)

    def _now(self):
        return self._clock.now().nanoseconds / 1e9

    def start(self):
        self._deadline = None
        self._zeroed = 0
        self._active = True
        self._log.info(
            f"도킹 미세 이동: {self._speed:+.3f} m/s × {self._duration:.2f}s")

    def poll(self):
        return "running" if self._active else "success"

    def stop(self):
        """중단. 남은 주행을 버리고 0 구간으로 넘어간다 — 조용히 손을 떼는 게 아니라
        **0 을 내고** 멈춘다(위 ② 와 같은 이유).

        아직 한 번도 발행하지 않았어도 `_deadline` 을 지금으로 박는다. `None` 으로 두면
        다음 tick 이 "첫 발행"으로 보고 거기서부터 다시 `duration` 을 재 **끊었는데 그때부터
        3cm 를 간다.**"""
        if self._active:
            self._deadline = self._now()

    def _tick(self):
        if not self._active:
            return                      # 안 도는 동안은 침묵 — 입력이 없는 것으로 친다
        now = self._now()
        if self._deadline is None:
            self._deadline = now + self._duration
        msg = self._Twist()
        if now < self._deadline:
            msg.linear.x = self._speed
            self._pub.publish(msg)
            return
        self._pub.publish(msg)          # 0
        self._zeroed += 1
        if self._zeroed >= self._zero_ticks:
            self._active = False
