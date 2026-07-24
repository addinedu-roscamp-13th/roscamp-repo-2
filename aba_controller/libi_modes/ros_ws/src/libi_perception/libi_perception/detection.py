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


def detection_from_dict(d):
    if d is None:
        return None
    return Detection(
        cx=d['cx'], cy=d['cy'], area=d['area'], bbox=tuple(d['bbox']),
        track_id=d['track_id'], is_owner=d['is_owner'],
        confidence=d['confidence'], is_predicted=d['is_predicted'],
    )
