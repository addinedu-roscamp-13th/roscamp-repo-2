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

    #: 화면에서 **가장 큰 사람** 박스의 크기(sqrt(area) px, 320 원본 기준).
    #: 0 = 안 보이거나 소스가 안 알려줬다.
    #:
    #: ⚠️ 이건 `area`(등록 대상의 면적)와 **다른 값**이다. 등록 매칭과 무관하게
    #:    화면 안 사람 전체 중 가장 큰 것이라, 통행인도 여기에 잡힌다. 주행 중
    #:    사람 차단 판정(`libi_modes` 의 `PersonBlockGuard`)이 이 값을 쓴다.
    front_person_size: float = 0.0


def detection_from_dict(d):
    # owner 없는 프레임은 `cx` 가 없는 dict 로 온다(크기만 실려 있다) —
    # `detection_sink.detection_to_dict` 의 [2026-08-03] 변경. "모른다"를
    # KeyError 로 터뜨리지 않고, null 과 마찬가지로 "owner 없음"으로 읽는다.
    if d is None or 'cx' not in d:
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
        front_person_size=float(d.get('front_person_size', 0.0) or 0.0),
    )
