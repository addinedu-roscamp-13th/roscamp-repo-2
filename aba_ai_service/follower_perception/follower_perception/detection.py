from dataclasses import dataclass
from typing import Tuple


@dataclass
class Detection:
    """The only public output of perception. Consumed by the control layer."""
    cx: float
    cy: float
    area: float
    bbox: Tuple[float, float, float, float]
    track_id: int
    is_owner: bool
    confidence: float
    is_predicted: bool


@dataclass
class TrackedBox:
    """Internal per-frame detection with a ByteTrack id. Not public."""
    bbox: Tuple[float, float, float, float]
    cx: float
    cy: float
    area: float
    track_id: int
    confidence: float
