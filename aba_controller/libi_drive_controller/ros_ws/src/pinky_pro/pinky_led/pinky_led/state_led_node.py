"""FSM 상태 토픽을 구독해 LED 색상·패턴을 출력하는 노드.

이 파일은 얇은 껍데기다 — 패턴 계산은 patterns.py, 매핑은 state_led_config.py,
상태·타임아웃 판정은 led_state_model.py 가 맡으며 셋 다 ROS·하드웨어 없이 테스트된다.
여기서는 rclpy 배선과 실제 LED 쓰기만 한다.

주의 1 (블로킹 금지): pinkyled.LED 의 color_wipe / theater_chase / rainbow / rainbowCycle /
theaterChaseRainbow 는 내부에서 time.sleep() 루프를 돈다. 콜백에서 호출하면 rclpy.spin()
전체가 멈춰 다른 콜백이 하나도 실행되지 않는다. 절대 쓰지 않는다 — 매 tick 계산된 프레임을
set_pixel() + show() 로 한 번에 밀어넣는 논블로킹 방식만 사용한다.

주의 2 (LED 점유): rpi_ws281x 는 한 프로세스만 스트립을 소유할 수 있다. 같은 패키지의
led_server 와 이 노드를 동시에 띄울 수 없다. 둘 중 하나만 실행할 것 (README 참조).

주의 3 (root): pinkyled 모듈은 import 시점에 root 가 아니면 sudo 로 자기 자신을 재실행한다.
따라서 이 노드도 결국 root 권한으로 동작하며, 개발 PC 에서는 import 자체가 불가능하다.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from pinky_led.led_state_model import LedStateModel
from pinky_led.pinkyled import LED
from pinky_led.state_led_config import load


class StateLedNode(Node):
    def __init__(self):
        super().__init__("state_led")
        self.declare_parameter("config_path", "")
        self.declare_parameter("state_topic", "fsm_state")
        self.declare_parameter("tick_hz", 20.0)

        config_path = self.get_parameter("config_path").value
        if not config_path:
            raise RuntimeError("config_path parameter is required (path to led_state_map.yaml)")

        self.config = load(config_path)
        self.model = LedStateModel(self.config)
        self.led = LED(num=self.config.num_pixels)
        self._last_frame = None

        topic = self.get_parameter("state_topic").value
        self.create_subscription(String, topic, self._on_state, 10)

        tick_hz = float(self.get_parameter("tick_hz").value)
        self.create_timer(1.0 / tick_hz, self._tick)
        self.get_logger().info(f"state_led ready — topic '{topic}', {tick_hz:.0f} Hz")

    def _now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def _on_state(self, msg):
        self.model.on_state(msg.data.strip().upper(), self._now())

    def _tick(self):
        frame = self.model.frame(self._now())
        if frame == self._last_frame:
            return                       # nothing changed — skip the strip write
        for index, color in enumerate(frame):
            self.led.set_pixel(index, color)
        self.led.show()
        self._last_frame = frame


def main(args=None):
    rclpy.init(args=args)
    node = StateLedNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.led.clear()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
