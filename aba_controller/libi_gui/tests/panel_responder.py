#!/usr/bin/env python3
"""test_panel_link 용 응답자 — 브릿지가 로봇 쪽에 보여주는 모습 그대로 흉내낸다.

    /panel_request  구독  ←  libi_gui
    /panel_result   발행  →  libi_gui

실제 FMS(app/panel_bridge.py)는 서버 도메인에서 `/pinkyN/...` 로 듣는다. 그 사이를
domain_bridge 가 개명·중계하므로, 로봇 쪽에서 보면 접두사가 없는 이 모양이다.

`delay_ms` 를 주면 그만큼 늦게 답한다 — 타임아웃 뒤 늦게 온 응답을 시험할 때 쓴다.

    source /opt/ros/jazzy/setup.bash && python3 tests/panel_responder.py
"""
import json
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def main() -> None:
    rclpy.init()
    node = Node("panel_responder")
    pub = node.create_publisher(String, "/panel_result", 10)

    def on_request(msg: String) -> None:
        try:
            req = json.loads(msg.data)
        except Exception:
            return

        def reply() -> None:
            out = String()
            out.data = json.dumps({
                "id": req.get("id"),
                "ok": True,
                "echo": req.get("op"),
                "accepted": True,
            })
            pub.publish(out)

        delay = float(req.get("delay_ms") or 0) / 1000.0
        if delay > 0:
            threading.Timer(delay, reply).start()
        else:
            reply()
        print(f"[responder] {req.get('op')} id={req.get('id')} delay={delay}s", flush=True)

    node.create_subscription(String, "/panel_request", on_request, 10)
    print("[responder] /panel_request 대기", flush=True)
    rclpy.spin(node)


if __name__ == "__main__":
    main()
