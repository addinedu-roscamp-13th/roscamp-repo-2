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
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _on_select(self, msg):
        v = (msg.data or "").strip()
        with self._lock:
            self._value = v if v in ("front", "back", "none") else None

    def _spin(self):
        import rclpy
        while self._run:
            try:
                rclpy.spin_once(self._node, timeout_sec=0.2)
            except Exception:   # noqa: BLE001 — 구독이 죽어도 추론 루프는 계속 돈다
                break

    def latest(self):
        with self._lock:
            return self._value

    def close(self):
        self._run = False
        try:
            self._node.destroy_node()
        except Exception:       # noqa: BLE001
            pass
