"""ABA AI Service — walking-skeleton 스텁.

역할: UDP(:9000)로 image_sender 프레임을 수신 → 더미 추론 결과를 TCP로
aba_fms_service(:9010)에 push. (YOLO 없음. 실물 AI가 같은 UDP/TCP 계약으로 드롭인.)

소비자가 둘이라 경로도 둘이다:
  image_sender ──UDP──▶ ai_service ──TCP:9010──▶ aba_fms_service   (관제용)
                                   └─TCP:6000──▶ 로봇 libi_perception (추종 제어용)

추종 제어 루프는 로봇에서 20Hz로 LiDAR와 융합되어 돌기 때문에 Detection 이 FMS 를
거치면 안 된다. 두 채널은 서로 독립적으로 실패한다.
"""
import json
import os
import socket
import time

from detection_sink import RobotDetectionSink, detection_to_dict

UDP_PORT = int(os.environ.get("AI_SERVICE_UDP_PORT", "9000"))
FMS_HOST = os.environ.get("FMS_TCP_HOST", "127.0.0.1")
FMS_PORT = int(os.environ.get("FMS_TCP_PORT", "9010"))

_robot_sink = RobotDetectionSink()


def infer(frame: bytes, src: str) -> dict:
    """추론 스텁 — 실물에서는 YOLO/OCR 결과."""
    return {
        "predictions": [{"cls": "person", "conf": 0.9, "bbox": [10, 10, 50, 80]}],
        "frame_bytes": len(frame),
        "src": src,
        "ts": time.time(),
        # 실물에서는 FollowerPerception 이 고른 주인 Detection. 스텁이라 아직 없지만
        # 키를 미리 둬서 로봇 채널 계약을 명시한다.
        "owner": None,
    }


def push_to_fms(result: dict) -> None:
    t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    t.settimeout(3)
    t.connect((FMS_HOST, FMS_PORT))
    t.sendall(json.dumps(result).encode())
    t.close()


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", UDP_PORT))
    print(f"[ai_service] UDP listen :{UDP_PORT} -> TCP push {FMS_HOST}:{FMS_PORT}", flush=True)
    while True:
        frame, addr = s.recvfrom(65536)
        result = infer(frame, addr[0])
        try:
            push_to_fms(result)
            print(f"[ai_service] frame {len(frame)}B from {addr[0]} -> pushed to fms", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[ai_service] TCP push failed: {type(e).__name__}: {e}", flush=True)

        # 로봇 직결 채널 — 추종 제어용. FMS push 와 독립적으로 실패해도 무시한다.
        if not _robot_sink.send(detection_to_dict(result.get("owner"))):
            print("[ai_service] robot detection link down", flush=True)


if __name__ == "__main__":
    main()
