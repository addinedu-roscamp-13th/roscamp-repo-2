"""AI 서버 → 로봇 검출 채널의 통합 확인.

`perception_server` 가 만든 sink 로 보낸 payload 를, 로봇 쪽 `detection_from_dict` 가
그대로 읽을 수 있어야 한다. 이 채널이 없으면 로봇의 회복 BT 는 더미 스텁만 받는다.
"""
import json
import os
import socket
import sys
import threading

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))               # aba_ai_service
_ROBOT = os.path.join(
    os.path.dirname(_ROOT),
    "aba_controller/libi_modes/ros_ws/src/libi_perception")
for p in (_ROOT, _ROBOT, os.path.dirname(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from follower_perception.detection import Detection                   # noqa: E402
from scripts.perception_server import _make_detection_sink            # noqa: E402


class _FakeRobot:
    """로봇의 TcpDetectionSource 대역. 줄바꿈 구분 JSON 한 줄을 받는다."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.lines = []
        self._got = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        buf = b""
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line:
                        self.lines.append(json.loads(line))
                        self._got.set()

    def wait(self, timeout=2.0):
        assert self._got.wait(timeout), "로봇이 payload 를 못 받았다"
        self._got.clear()


def _det(**over):
    kw = dict(cx=320.0, cy=240.0, area=10000.0, bbox=(1.0, 2.0, 3.0, 4.0),
              track_id=7, is_owner=True, confidence=0.9, is_predicted=False,
              posture="Standing", motion_ok=True, camera="back", camera_epoch=4)
    kw.update(over)
    return Detection(**kw)


def test_new_fields_reach_the_robot():
    robot = _FakeRobot()
    send = _make_detection_sink("127.0.0.1", robot.port)
    send(_det())
    robot.wait()
    got = robot.lines[-1]
    assert got["posture"] == "Standing"
    assert got["motion_ok"] is True
    assert got["camera"] == "back"
    assert got["camera_epoch"] == 4


def test_robot_side_parses_what_we_send():
    """양끝 계약이 실제로 맞물리는지 — 한쪽만 고치면 여기서 깨진다."""
    from libi_perception.detection import detection_from_dict

    robot = _FakeRobot()
    send = _make_detection_sink("127.0.0.1", robot.port)
    send(_det(posture="Lying", motion_ok=False, camera="front", camera_epoch=9))
    robot.wait()
    parsed = detection_from_dict(robot.lines[-1])
    assert parsed.posture == "Lying"
    assert parsed.motion_ok is False
    assert parsed.camera == "front"
    assert parsed.camera_epoch == 9
    assert parsed.bbox == (1.0, 2.0, 3.0, 4.0)


def test_frame_resolution_rides_along():
    """[2026-07-31] 이 파일이 못 잡아 실제로 물렸던 구멍.

    좌표(`cx`/`area`)만 보내고 해상도를 안 보내면, 받는 쪽 PID 는 640 이라고
    **가정**한다(`libi_perception/pid.py:47-54`). 스트림을 320 으로 내린 뒤로는
    그 가정이 "사람이 화면 정중앙인데 늘 왼쪽에 있다"로 나타나 로봇이 계속 돌았다.

    폭은 좌표를 만든 프레임에서 나와야 한다 — 앞뒤 캠 폭이 달라져도 자동으로 맞는다.
    """
    import numpy as np
    from libi_perception.detection import detection_from_dict
    from libi_perception.pid import FollowPID
    from libi_perception import config

    robot = _FakeRobot()
    send = _make_detection_sink("127.0.0.1", robot.port)
    send(_det(cx=160.0, area=2500.0), np.zeros((240, 320, 3), dtype=np.uint8))
    robot.wait()

    parsed = detection_from_dict(robot.lines[-1])
    assert parsed.image_width == 320
    assert parsed.image_height == 240

    # [2026-08-02] PID 의 해상도 환산은 걷어냈다 — 이제 `config.IMAGE_WIDTH` 가 곧
    # 카메라 폭이고 원본 픽셀이 그대로 들어간다. 그래서 여기서 볼 것은 "환산이
    # 걸렸나"가 아니라 **payload 의 폭과 config 의 폭이 같은가**다. 어긋나면 사람이
    # 정중앙에 있어도 각속도가 난다.
    assert parsed.image_width == config.IMAGE_WIDTH, (
        f"검출은 {parsed.image_width}px 프레임에서 나왔는데 config.IMAGE_WIDTH 는 "
        f"{config.IMAGE_WIDTH} 다 — 튜닝값이 카메라와 안 맞는다")
    _, ang = FollowPID(config).compute(parsed.cx, parsed.area, 0.05)
    assert abs(ang) < 1e-9, f"정중앙인데 각속도가 {ang} 다"


def test_missing_owner_sends_null():
    """안 보이는 프레임은 null 을 보낸다 — 받는 쪽이 None 을 그대로 통과시키는 계약이다."""
    robot = _FakeRobot()
    send = _make_detection_sink("127.0.0.1", robot.port)
    send(None)
    robot.wait()
    assert robot.lines[-1] is None


def test_dead_robot_does_not_kill_the_loop():
    """로봇이 꺼져 있다고 추론 루프가 죽으면 안 된다."""
    send = _make_detection_sink("127.0.0.1", 1)      # 아무도 안 듣는 포트
    send(_det())                                     # 예외가 새어 나오면 실패
