"""AI 서버 쪽 `/libi/perception_role` 구독자 — "지금 어떤 세션이 도는가".

`camera_select_source.py` 와 같은 결로 **자기 모듈에 격리**한다. import 하려면 ROS2 가
sourcing 돼 있고(`rclpy`), 로봇과 `ROS_DOMAIN_ID` 가 맞아야 한다. 옵트인이 아니면
`perception_server` 는 ROS 를 전혀 모른 채로 돈다.

## 왜 필요한가

자세(pose) 추정은 **추종 전용**이다. 사람에게 다가가는 로봇이 누운·측면 자세에서
멈추라고 두는 안전 규칙이고, 로봇 쪽 `control_loop._is_guide()` 가 길잡이에서는 그
값을 이미 버린다. 그런데 AI 서버는 역할을 몰라서:

  · 길잡이 중에도 매 프레임 2차 추론을 돌린다(`pipeline.run` — 낭비)
  · 골격을 **JPEG 에 구워** 패널로 보낸다(`perception_server.draw_overlay`).
    패널은 그 그림을 못 끈다 — `RobotController.cpp` 주석이 그렇게 적어 두고 있었다.
  · 뒷캠 전환 때 자세 기준을 다시 재느라 "Calibrating" 이 뜬다

역할을 알면 길잡이에서 계산도 표시도 건너뛴다.

## 왜 카메라로 대신 판정하지 않나

역할과 카메라가 1:1 이 아니기 때문이다. 길잡이 **등록**은 앞캠에서 하고, 추종 회복은
뒷캠을 잠깐 본다(`follow_node.request_camera`). 카메라로 갈랐으면 등록 화면에서는
골격이 그대로 나오고, 추종 회복에서는 자세 판정이 꺼졌을 것이다.
"""
import threading


class RoleSource:
    """마지막으로 받은 세션 역할. 못 받았으면 None."""

    #: [2026-08-04] "security" 추가 — 야간순찰 추종(IntruderChase). follow 와 주행
    #: 로직은 같지만 pipeline.POSE_ROLES 에 없어 자세 게이트를 안 받는다(요구사항:
    #: 옆모습이어도 bbox 크기만으로 판단). session.py 머리말 참고.
    VALUES = ("follow", "guide", "watch", "security", "none")

    def __init__(self, topic="/libi/perception_role"):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String

        self._value = None
        self._lock = threading.Lock()
        self._run = True

        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = Node("libi_perception_role_listener")
        # 발행자가 TRANSIENT_LOCAL 이라 이쪽도 맞춰야 늦게 붙어도 현재 값을 받는다.
        qos = QoSProfile(depth=1,
                         reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._node.create_subscription(String, topic, self._on_role, qos)
        # ⚠️ [2026-08-02] **전용 실행기를 쓴다 — 전역 실행기를 공유하면 안 받는다.**
        #
        #   `rclpy.spin_once(node)` 는 executor 를 안 주면 **전역 실행기**를 쓴다.
        #   매 호출마다 `add_node` → `spin_once` → `finally: remove_node` 를 하는데,
        #   이 모듈과 짝(`camera_select_source.py` / `role_source.py`)이 각자 스레드에서
        #   동시에 그 짓을 하면 서로를 밟아 **나중에 만든 쪽이 콜백을 영영 못 받는다.**
        #
        #   실측 2026-08-02 (같은 로봇·같은 도메인, 차이는 실행기 하나):
        #       전역 공유 : camera='none'  role=None      ← 끝까지 안 옴
        #       전용     : camera='none'  role='guide'   ← 온다
        #   `perception_server.main()` 이 camera_source 를 먼저 만들므로 **항상 역할
        #   쪽이 졌고**, `pose_active` 는 "역할을 모르면 켠다" 가 기본이라 길잡이에서
        #   골격이 그대로 나왔다. 구독 생성은 성공해서 `[ok]` 로그도 정상으로 보인다 —
        #   증상이 로그에 안 드러나는 종류다.
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self._node)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _on_role(self, msg):
        v = (msg.data or "").strip()
        with self._lock:
            self._value = v if v in self.VALUES else None

    def _spin(self):
        while self._run:
            try:
                self._exec.spin_once(timeout_sec=0.2)
            except Exception:   # noqa: BLE001 — 구독이 죽어도 추론 루프는 계속 돈다
                break

    def latest(self):
        with self._lock:
            return self._value

    def close(self):
        # ⚠️ 스레드가 멈춘 **뒤에** 노드를 부순다. 안 그러면 spin 중인 노드를 밑에서
        #    치워 종료 때 `terminate called without an active exception` 으로 abort 한다
        #    (실측 2026-08-02 — 데스크톱에 크래시 창이 떴다).
        self._run = False
        self._thread.join(timeout=1.0)
        try:
            self._exec.remove_node(self._node)
            self._node.destroy_node()
        except Exception:       # noqa: BLE001
            pass
