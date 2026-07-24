"""Handy(로봇팔) 노드 — Drive Controller 의 요청을 받아 팔을 움직이고 결과를 돌려준다.

신호 흐름 (요청서 5절, fleet_cmd/fleet_cmd_result 와 같은 패턴):
    구독  handy_cmd     {"id","action","object","location"}   요청
    발행  handy_result  {"id","success","error"}              완료

이 파일은 얇은 rclpy 껍데기다 — 검증·판정은 HandyCore(순수 로직)가 하고, 실제 팔 모션은
`motion` 콜러블(팔 담당자가 채움, BT 로 짜도 됨)이 한다. 그래서 코어는 ROS·팔 없이 테스트된다.

주의(블로킹 금지): 팔 모션이 오래 걸리면 콜백에서 직접 돌리지 말고 별 스레드로. 지금 골격은
스텁 모션이라 즉시 반환하지만, 실제 모션을 넣을 땐 spin 을 막지 않게 할 것.
"""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from libi_handy_controller.handy_core import HandyCore


class HandyNode(Node):
    def __init__(self, core: HandyCore | None = None):
        super().__init__("libi_handy_controller")
        self._core = core or HandyCore()
        self._result_pub = self.create_publisher(String, "handy_result", 10)
        self.create_subscription(String, "handy_cmd", self._on_cmd, 10)
        self.get_logger().info("handy up — handy_cmd 구독 / handy_result 발행")

    def _on_cmd(self, msg: String) -> None:
        try:
            req = json.loads(msg.data)
        except (ValueError, TypeError):
            self.get_logger().warning(f"handy_cmd 파싱 실패: {msg.data[:120]!r}")
            return
        cmd_id = str(req.get("id", ""))
        ok, error = self._core.perform(
            str(req.get("action", "")), str(req.get("object", "")), str(req.get("location", "")))
        if not ok:
            self.get_logger().warning(f"[{cmd_id}] 실패: {error}")
        self._publish_result(cmd_id, ok, error)

    def _publish_result(self, cmd_id: str, success: bool, error: str) -> None:
        out = String()
        out.data = json.dumps({"id": cmd_id, "success": success, "error": error},
                              ensure_ascii=False)
        self._result_pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = HandyNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
