"""AI 서버 쪽 `/libi/camera_select` 구독자 — "지금 어느 캠 영상이 오는가".

`scan_ros_source.py` 와 같은 결로 **자기 모듈에 격리**한다. import 하려면 ROS2 가
sourcing 돼 있고(`rclpy`), 로봇과 `ROS_DOMAIN_ID` 가 맞아야 한다. 옵트인이 아니면
`perception_server` 는 ROS 를 전혀 모른 채로 돈다.

## 왜 필요한가

로봇의 `camera_sender` 는 회복 중 앞↔뒤를 바꿔 가며 **같은 UDP 포트**로 보낸다.
AI 서버가 그 전환을 모르면:

  · ByteTrack id 가 시점이 통째로 바뀐 프레임에 그대로 이어져, 추적기가 엉뚱한
    사람을 주인으로 붙들 수 있다
  · 자세 기준 비율을 다른 카메라 배치의 값으로 계속 쓴다
  · 검출 payload 의 `camera`/`camera_epoch` 가 거짓이 된다

전환을 알면 `FollowerPerception.set_camera()` 가 추적 상태만 비우고 epoch 를 올린다
(등록 템플릿은 유지한다 — 사람이 바뀐 게 아니라 보는 각도가 바뀐 것이다).

## 왜 영상 프로토콜에 안 싣나

UDP 청크 헤더를 바꾸면 송·수신 양쪽과 그 위의 재조립을 같이 바꿔야 한다. 전환은
초당 몇 번 있는 일이 아니라 토픽 하나로 충분하다 — 프로토콜을 건드리지 않는 쪽이
되돌리기도 쉽다.
"""
import threading


class CameraSelectSource:
    """마지막으로 받은 카메라 선택값. 못 받았으면 None."""

    def __init__(self, topic="/libi/camera_select"):
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
        self._node = Node("libi_camera_select_listener")
        # 발행자가 TRANSIENT_LOCAL 이라 이쪽도 맞춰야 늦게 붙어도 현재 값을 받는다.
        qos = QoSProfile(depth=1,
                         reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._node.create_subscription(String, topic, self._on_select, qos)
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

    def _on_select(self, msg):
        v = (msg.data or "").strip()
        with self._lock:
            self._value = v if v in ("front", "back", "none") else None

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
