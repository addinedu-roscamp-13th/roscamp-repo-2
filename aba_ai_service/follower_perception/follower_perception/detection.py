from dataclasses import dataclass
from typing import Tuple


@dataclass
class Detection:
    """The only public output of perception. Consumed by the control layer.

    ⚠️ 같은 이름의 dataclass 가 로봇 쪽(`libi_perception/detection.py`)에도 있다.
    이쪽이 **생산자**, 그쪽이 **소비자**이며 `detection_sink.detection_to_dict()` 가
    둘을 잇는다. 필드를 더할 때는 **셋을 같이** 고쳐야 한다 — 한쪽만 고치면
    직렬화에서 조용히 빠지거나 생성자에서 TypeError 가 난다.
    """
    cx: float
    cy: float
    area: float
    bbox: Tuple[float, float, float, float]
    track_id: int
    is_owner: bool
    confidence: float
    is_predicted: bool
    #: 자세 판정("Standing"/"Lying"/"Unknown"/"Calibrating"). None = 자세 모델이 안 돈다.
    posture: str = None
    #: 자세 + 소실방향을 합친 최종 주행 가부. 모르면 True — 판단 근거가 없다고 해서
    #: 로봇을 세우면, 자세 모델 없는 배포에서 추종이 통째로 죽는다.
    motion_ok: bool = True
    #: 이 프레임이 온 카메라("front"/"back"). None = 안 알려줬다.
    camera: str = None
    #: 카메라 전환마다 1 증가. 전환 순간 버퍼에 남은 옛 프레임을 버리는 데 쓴다.
    #: 카메라 이름만으로는 같은 카메라로 두 번 돌아온 경우를 구분할 수 없다.
    camera_epoch: int = 0


@dataclass
class TrackedBox:
    """Internal per-frame detection with a ByteTrack id. Not public."""
    bbox: Tuple[float, float, float, float]
    cx: float
    cy: float
    area: float
    track_id: int
    confidence: float
