"""AI 서버 쪽 `/libi/fsm_state` 구독자 — "로봇이 지금 어떤 미션 상태인가".

`role_source.py` / `camera_select_source.py` 와 같은 결로 **자기 모듈에 격리**한다.
import 하려면 ROS2 가 sourcing 돼 있고(`rclpy`), 로봇과 `ROS_DOMAIN_ID` 가 맞아야 한다.
옵트인이 아니면 `perception_server` 는 ROS 를 전혀 모른 채로 돈다.

## 왜 필요한가 — 야간 모드만으로는 녹화 조건이 안 된다

녹화기 무장은 지금까지 관제의 **운영 모드**(`/api/admin/ops/security/mode` = day/night)
하나로만 정해졌다(`security_recorder.ModePoller`). 그런데 그건 "지금이 밤이다"이지
**"이 로봇이 야간 순찰 중이다"가 아니다.** 밤에 로봇이 충전 중이든, 배달을 돌든,
사서가 패널로 추종을 걸든 무장이 그대로라 사람만 보이면 클립이 열렸다.

미션 상태는 `libi_modes` 가 `/libi/fsm_state` 로 5Hz 발행한다(JSON). 거기
`current_state == "SECURITY_PATROL"` 일 때만 야간 순찰이다.

## 왜 역할(`perception_role`)로 대신 판정하지 않나

역할이 `security` 가 되는 건 **추격이 시작된 뒤**다(`IntruderChase._start`). 그런데
녹화는 그보다 **먼저** 열려야 한다 — 설계상 `trigger_sec`(1.0) < 로봇
`sustain_sec`(1.5) 라 클립·ReID 등록이 추격보다 앞선다(`SecurityParams` 머리말).
역할로 무장을 걸면 그 순서가 뒤집혀 프리롤도 등록도 사라진다.

역할은 추격이 **끝나는** 순간을 잡는 데 쓴다(`SecurityRecorder.end_chase`). 둘은
같은 축의 서로 다른 끝이라 하나로 합칠 수 없다.

## robot_id 를 왜 거르나

`/libi/fsm_state` 는 payload 에 `robot_id` 를 실어 **모든 로봇이 같은 토픽에 모인다**
(`domain_bridge_pinky3.yaml` 의 그 주석). AI 서버가 서버 도메인에 뜨면 남의 상태를
자기 것으로 읽는다. 로봇 도메인에 뜨면 어차피 한 대뿐이라 걸러도 결과가 같다.
"""
import json
import threading

#: 이 상태일 때만 야간 순찰이다. `libi_modes` 의 상태 이름 그대로다.
SECURITY_PATROL = "SECURITY_PATROL"


class FsmStateSource:
    """마지막으로 받은 미션 상태. 못 받았으면 None."""

    def __init__(self, topic="/libi/fsm_state", robot_id=None):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String

        self._value = None
        self._robot_id = robot_id or None
        self._lock = threading.Lock()
        self._run = True

        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = Node("libi_fsm_state_listener")
        # ⚠️ 발행자(`state_io._state_pub`)는 **기본 QoS**(VOLATILE)다 — 짝인
        #    `role_source` 의 TRANSIENT_LOCAL 을 그대로 베끼면 QoS 가 안 맞아
        #    **한 줄도 안 받는다.** 구독 생성은 성공하므로 로그는 정상으로 보인다.
        qos = QoSProfile(depth=1,
                         reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self._node.create_subscription(String, topic, self._on_state, qos)
        # ⚠️ **전용 실행기를 쓴다** — 전역 실행기를 짝들과 공유하면 나중에 만든 쪽이
        #    콜백을 영영 못 받는다. 근거와 실측은 `role_source.py` 의 같은 자리 주석.
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self._node)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _on_state(self, msg):
        try:
            payload = json.loads(msg.data or "{}")
        except (ValueError, TypeError):
            return                      # 깨진 줄 하나로 마지막 값을 잃지 않는다
        if self._robot_id and payload.get("robot_id") != self._robot_id:
            return
        state = payload.get("current_state")
        with self._lock:
            self._value = state if isinstance(state, str) and state else None

    def _spin(self):
        while self._run:
            try:
                self._exec.spin_once(timeout_sec=0.2)
            except Exception:   # noqa: BLE001 — 구독이 죽어도 추론 루프는 계속 돈다
                break

    def latest(self):
        with self._lock:
            return self._value

    def in_security_patrol(self):
        """야간 순찰 중인가. **모르면 True** — 상태를 못 받는다는 이유로 야간 녹화를
        통째로 꺼 버리면, ROS 옵트인이 없는 배포에서 기능이 조용히 사라진다.
        그때는 예전처럼 운영 모드(day/night)만으로 판정한다."""
        v = self.latest()
        return True if v is None else v == SECURITY_PATROL
