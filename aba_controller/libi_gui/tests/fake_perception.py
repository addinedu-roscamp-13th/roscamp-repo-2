"""`test_perception_client` 가 붙을 가짜 perception_server.

와이어 포맷은 `aba_ai_service/follower_perception/scripts/frame_proto.py` 와 같다
(4바이트 빅엔디언 길이 + 페이로드). 한 스트림에 **세 종류**가 섞여 나가는 것이
이 테스트가 붙드는 계약이다:

  · JPEG                 매 프레임
  · `LIDR <정수 8개>`    3프레임마다
  · `POSE <JSON>`        매 프레임

진짜 서버를 띄우면 카메라·모델·ROS 가 다 필요해서 GUI 파싱만 보려는 테스트가
못 돈다. 여기서는 바이트만 흉내 낸다.

    python3 tests/fake_perception.py 5099 /tmp/cmds.log
    ./build/test_perception_client 127.0.0.1:5099

받은 명령(register/reset)은 로그 파일에 한 줄씩 적는다 — 클라이언트가 실제로
보냈는지는 그 파일로 확인한다.
"""
import json
import socket
import struct
import sys
import time

W, H = 320, 240
LIDAR_EVERY = 3
FPS = 20.0


def _jpeg():
    """320x240 JPEG 한 장. 내용은 상관없고 **디코딩되는 것**만 중요하다."""
    try:
        import cv2
        import numpy as np
    except ImportError:                      # pragma: no cover - 환경 문제
        sys.exit("cv2/numpy 가 필요합니다: 레포 .venv 의 python3 로 실행하세요")
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = (40, 80, 120)
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        sys.exit("JPEG 인코딩 실패")
    return buf.tobytes()


def _send(sock, payload):
    sock.sendall(struct.pack(">I", len(payload)) + bytes(payload))


def _pose(i):
    # 실제 `_pose_payload` 와 같은 키를 낸다. 값이 없는 항목이 null 로 나가는 것도
    # 그대로 흉내 낸다 — 클라이언트가 null 을 0 으로 바꾸면 여기서 드러난다.
    return b"POSE " + json.dumps({
        "posture": "Side",
        "motionOk": False,
        "ratio": 3.42,
        "refRatio": 2.10,
        "sideTrip": 3.36,
        "axis": "torso",
        "state": "FOLLOWING",
        "linearX": 0.08,
        "angularZ": -0.20,
    }).encode("utf-8")


def serve(port, log_path):
    jpeg = _jpeg()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    print(f"[fake] {port} 대기 중", flush=True)

    log = open(log_path, "a", buffering=1) if log_path else None
    while True:
        conn, _ = srv.accept()
        print("[fake] 클라이언트 접속", flush=True)
        # 명령은 논블로킹으로 긁어 온다 — 안 그러면 프레임 송신이 멈춘다.
        conn.setblocking(False)
        i = 0
        try:
            while True:
                conn.setblocking(True)
                _send(conn, jpeg)
                _send(conn, _pose(i))
                if i % LIDAR_EVERY == 0:
                    _send(conn, b"LIDR 100 120 110 300 310 400 410 420")
                conn.setblocking(False)
                try:
                    data = conn.recv(4096)
                    if data == b"":
                        break                    # 상대가 닫았다
                    if log:
                        for line in data.decode("utf-8", "replace").split("\n"):
                            if line.strip():
                                log.write(line.strip() + "\n")
                except BlockingIOError:
                    pass
                i += 1
                time.sleep(1.0 / FPS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            conn.close()
            print("[fake] 연결 종료", flush=True)


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 5099,
          sys.argv[2] if len(sys.argv) > 2 else "")
