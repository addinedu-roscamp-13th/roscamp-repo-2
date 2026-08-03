"""로봇 직결 Detection 채널.

기존 FMS push(UDP:9000 → TCP:9010)와는 별개의 경로다. 추종 제어 루프는 로봇에서
20Hz로 LiDAR와 융합되어 돌기 때문에, Detection 은 FMS 가 아니라 로봇의
libi_perception(TcpDetectionSource, 기본 :6000)으로 직접 가야 한다. 두 소비자는
페이로드도 주기도 다르므로 기존 경로를 바꾸지 않고 채널을 하나 추가한다.

주인이 안 보이고 화면에 아무도 없는 프레임은 JSON `null` 로 보낸다 — 수신측
detection_from_dict() 가 None 을 그대로 통과시키는 계약과 맞춘다. 주인은 없어도
누군가(통행인 포함) 보이면 `{"front_person_size": ...}` 만 실린 dict 를 보낸다 —
`cx` 가 없는 dict 는 "owner 없음"으로 읽힌다(detection_to_dict 참고).
"""
from __future__ import annotations

import json
import os
import socket
import threading

ROBOT_HOST = os.environ.get("ROBOT_DETECTION_HOST", "127.0.0.1")
ROBOT_PORT = int(os.environ.get("ROBOT_DETECTION_PORT", "6000"))


def detection_to_dict(det, frame=None, front_person_size=None):
    """libi_perception.detection.detection_from_dict 가 읽는 payload 형태.

    bbox 는 list 로 내보낸다 — tuple 은 JSON 왕복 후 어차피 list 가 되므로,
    보내는 쪽에서 맞춰야 양쪽 계약이 실제로 같아진다.

    ## [2026-07-31] `frame` 을 받아 `image_width` 를 싣는다

    `cx`·`area` 는 **이 프레임의 픽셀 좌표**다. 받는 쪽 PID 는 그것을 기준 폭
    (640)으로 환산할 준비가 이미 돼 있는데(`libi_perception/pid.py:47-54`),
    해상도를 못 받으면 `config.IMAGE_WIDTH=640` 이라고 **가정**한다.

    2026-07-30 에 스트리밍을 320x240 으로 내리면서(`scripts/all/libi_pi.sh` 의
    `--width 320`) 그 가정이 실제로 틀어졌다:

      · 조향: 화면 중심을 320 으로 잡는데 실제 중심은 160 → 사람이 정중앙에 있어도
        `e_cx=160px` 이라 deadzone(45)을 넘어 **한쪽으로 계속 돈다**.
      · 거리: `sqrt(area)` 가 선형으로 작아져 화면을 꽉 채워도 123 → `TARGET_SIZE=280`
        에 영영 못 닿아 **전진이 안 멈춘다**(LiDAR 하드 스톱만이 받친다).

    같은 이유로 `follower_perception/ai_server.py` 의 동명 함수는 이미 이 값을 싣지만,
    실제 배포 경로(`scripts/ai-server.sh` → `perception_server.py`)가 쓰는 것은
    **이 함수**다. 그래서 여기에도 넣는다.

    ⚠️ 그쪽 함수로 갈아타는 것은 답이 아니다 — 거기엔 `posture`/`motion_ok`/`camera`/
       `camera_epoch` 가 없어서, 받는 쪽이 `motion_ok` 를 기본값 True 로 채워
       **쓰러진 사람 앞에서 서는 게이트가 통째로 무력화된다.**

    `frame` 을 안 주면 키를 안 넣는다 — 받는 쪽이 예전처럼 640 가정으로 떨어진다.

    ## `front_person_size` — owner 와 독립인 값

    화면에서 **가장 큰 사람**(owner 매칭 전 전체 후보 중)의 `sqrt(area)`. 호출자가
    계산해 넘긴다(이 함수는 `det` 하나만 보므로 전체 후보 목록을 모른다). 안 주면
    (`None`) 키를 안 넣는다 — `image_width` 와 같은 관례다.

    ## [2026-08-03] `det is None` 이어도 크기는 보낸다

    주행(`navigate`) 중에는 등록된 추종 대상이 **애초에 없다.** 예전처럼 여기서
    바로 `None`(JSON `null`)을 돌려주면 `front_person_size` 가 실릴 자리가 없어,
    `PersonBlockGuard` 가 주행 내내 0.0 만 보고 사람을 영영 못 본다. `cx` 가 없는
    dict 는 받는 쪽(`detection_from_dict`)이 "owner 없음"으로 읽으므로 계약이
    깨지지 않는다.
    """
    if det is None:
        if front_person_size is None:
            return None
        return {"front_person_size": float(front_person_size)}
    out = {
        "cx": det.cx, "cy": det.cy, "area": det.area, "bbox": list(det.bbox),
        "track_id": det.track_id, "is_owner": det.is_owner,
        "confidence": det.confidence, "is_predicted": det.is_predicted,
        # 아래 넷은 optional 이다 — 받는 쪽(detection_from_dict)이 없으면 기본값을 쓴다.
        # getattr 로 읽는 이유: 이 함수는 테스트 대역 객체도 받는다.
        "posture": getattr(det, "posture", None),
        "motion_ok": bool(getattr(det, "motion_ok", True)),
        "camera": getattr(det, "camera", None),
        "camera_epoch": int(getattr(det, "camera_epoch", 0) or 0),
    }
    if frame is not None:
        h, w = frame.shape[:2]
        out["image_width"] = int(w)
        out["image_height"] = int(h)
    if front_person_size is not None:
        out["front_person_size"] = float(front_person_size)
    return out


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
