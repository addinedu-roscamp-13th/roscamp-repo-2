from dataclasses import dataclass
from typing import Tuple


@dataclass
class Detection:
    cx: float
    cy: float
    area: float
    bbox: Tuple[float, float, float, float]
    track_id: int
    is_owner: bool
    confidence: float
    is_predicted: bool
    #: 이 검출이 나온 프레임의 크기(px). 0 = 소스가 안 알려줬다.
    #:
    #: 이게 없어서 `config.IMAGE_WIDTH = 640` 이 화면 중심을 320 으로 가정하는데,
    #: 실제 카메라는 480x360 이라 중심이 240 이다(`robot_agent/app/hardware/
    #: camera_stream.py:338` Picamera2 `size=(480, 360)`). 그 상태로 돌면 방위 PID 에
    #: 항상 +80px 편향이 실려 로봇이 한쪽으로 계속 틀어진다.
    #:
    #: 좌표는 그것을 만든 쪽이 자기 해상도를 같이 말해야 한다. 받는 쪽이 추측할 일이 아니다.
    image_width: int = 0
    image_height: int = 0


def detection_from_dict(d):
    if d is None:
        return None
    return Detection(
        cx=d['cx'], cy=d['cy'], area=d['area'], bbox=tuple(d['bbox']),
        track_id=d['track_id'], is_owner=d['is_owner'],
        confidence=d['confidence'], is_predicted=d['is_predicted'],
        # 아직 안 보내는 소스가 있으므로 optional. 보내주면 그 값이 이긴다.
        image_width=int(d.get('image_width', 0) or 0),
        image_height=int(d.get('image_height', 0) or 0),
    )
