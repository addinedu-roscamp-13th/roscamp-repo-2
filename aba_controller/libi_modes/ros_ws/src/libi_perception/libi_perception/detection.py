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
    #: ⚠️ [2026-08-02] **PID 는 이 값을 더 이상 안 쓴다.** 해상도 환산을 걷어냈고
    #:    (`pid.py` 머리말), 이제 `config.IMAGE_WIDTH` 가 곧 카메라 폭이라는 전제로
    #:    원본 픽셀을 그대로 계산한다.
    #:
    #:    그래도 필드는 남긴다 — 검출이 어느 해상도에서 나왔는지는 로그·디버깅에서
    #:    유일한 근거이고, `config.IMAGE_WIDTH` 와 이 값이 어긋나면 그게 곧
    #:    "튜닝값이 카메라와 안 맞는다"는 신호다.
    image_width: int = 0
    image_height: int = 0

    #: 자세 판정("Standing"/"Lying"/"Unknown"/"Calibrating"). None = 판정 소스가 없다.
    posture: str = None
    #: 자세 + 소실방향을 합친 최종 주행 가부.
    #:
    #: 기본값이 True 인 것은 의도적이다 — 옛 payload(이 필드가 없는 소스)에서 False 로
    #: 잡으면 로봇이 보이는 대상을 두고 영영 안 움직인다. "모른다"는 "가지 마라"가 아니다.
    motion_ok: bool = True
    #: 이 프레임이 온 카메라("front"/"back"). 회복 BT 가 "어느 캠에서 찾았나"를 판단한다.
    camera: str = None
    #: 카메라 전환마다 1 증가. 전환 순간 섞여 들어온 옛 프레임을 버리는 데 쓴다.
    camera_epoch: int = 0


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
        posture=d.get('posture'),
        motion_ok=bool(d.get('motion_ok', True)),
        camera=d.get('camera'),
        camera_epoch=int(d.get('camera_epoch', 0) or 0),
    )
