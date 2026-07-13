"""ABA AI Service — walking-skeleton 스텁.

역할: UDP(:9000)로 image_sender 프레임을 수신 → 더미 추론 결과를 TCP로
aba_fms_service(:9010)에 push. (YOLO 없음. 실물 AI가 같은 UDP/TCP 계약으로 드롭인.)

엣지: image_sender ──UDP──▶ ai_service ──TCP──▶ aba_fms_service
"""
import json
import os
import socket
import time

UDP_PORT = int(os.environ.get("AI_SERVICE_UDP_PORT", "9000"))
FMS_HOST = os.environ.get("FMS_TCP_HOST", "127.0.0.1")
FMS_PORT = int(os.environ.get("FMS_TCP_PORT", "9010"))


def infer(frame: bytes, src: str) -> dict:
    """추론 스텁 — 실물에서는 YOLO/OCR 결과."""
    return {
        "predictions": [{"cls": "person", "conf": 0.9, "bbox": [10, 10, 50, 80]}],
        "frame_bytes": len(frame),
        "src": src,
        "ts": time.time(),
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


if __name__ == "__main__":
    main()
