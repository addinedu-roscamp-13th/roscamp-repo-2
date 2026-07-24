"""로봇 직결 Detection 채널.

기존 FMS push(UDP:9000 → TCP:9010)와는 별개의 경로다. 추종 제어 루프는 로봇에서
20Hz로 LiDAR와 융합되어 돌기 때문에, Detection 은 FMS 가 아니라 로봇의
libi_perception(TcpDetectionSource, 기본 :6000)으로 직접 가야 한다. 두 소비자는
페이로드도 주기도 다르므로 기존 경로를 바꾸지 않고 채널을 하나 추가한다.

주인이 안 보이는 프레임은 JSON `null` 로 보낸다 — 수신측 detection_from_dict() 가
None 을 그대로 통과시키는 계약과 맞춘다.
"""
from __future__ import annotations

import json
import os
import socket
import threading

ROBOT_HOST = os.environ.get("ROBOT_DETECTION_HOST", "127.0.0.1")
ROBOT_PORT = int(os.environ.get("ROBOT_DETECTION_PORT", "6000"))


def detection_to_dict(det):
    """libi_perception.detection.detection_from_dict 가 읽는 payload 형태.

    bbox 는 list 로 내보낸다 — tuple 은 JSON 왕복 후 어차피 list 가 되므로,
    보내는 쪽에서 맞춰야 양쪽 계약이 실제로 같아진다.
    """
    if det is None:
        return None
    return {
        "cx": det.cx, "cy": det.cy, "area": det.area, "bbox": list(det.bbox),
        "track_id": det.track_id, "is_owner": det.is_owner,
        "confidence": det.confidence, "is_predicted": det.is_predicted,
    }


class RobotDetectionSink:
    """줄바꿈 구분 JSON 을 로봇으로 보낸다.

    링크가 끊겨도 예외를 올리지 않고 다음 send() 에서 재연결을 시도한다 — 로봇이
    꺼져 있다고 해서 추론 루프까지 죽으면 안 된다.
    """

    def __init__(self, host: str = ROBOT_HOST, port: int = ROBOT_PORT):
        self._addr = (host, int(port))
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(self._addr)
        self._sock = sock

    def send(self, payload) -> bool:
        """전송 성공 여부를 반환한다. 실패해도 예외를 올리지 않는다."""
        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._lock:
            for attempt in (1, 2):          # 두 번째 시도는 재연결 후
                try:
                    if self._sock is None:
                        self._connect()
                    self._sock.sendall(line)
                    return True
                except OSError:
                    self._close_locked()
                    if attempt == 2:
                        return False
            return False

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()
