"""마커 주행이 주고받는 값 두 개. 로직 없음."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MarkerObs:
    """한 프레임에서 뽑은 마커 관측값.

    ex        : 화면 중앙 대비 좌우 오차. -1(왼쪽 끝) ~ +1(오른쪽 끝)
    z_m       : 카메라에서 마커까지 거리(m)
    yaw_deg   : 마커 정면각(0 = 마커를 정면으로 마주봄)
    lateral_m : 마커 법선축에서 로봇이 벗어난 거리(m)
    size_frac : 마커 한 변이 프레임 폭에서 차지하는 비율
    """
    marker_id: int
    ex: float
    z_m: float
    yaw_deg: float
    lateral_m: float
    size_frac: float

    def describe(self, stop_m: float = 0.0) -> str:
        """사람이 읽는 한 줄. drive 와 watch 가 같은 문장을 쓰도록 여기 둔다."""
        return (f"z={self.z_m:.3f}m ex={self.ex:+.3f} yaw={self.yaw_deg:+.1f} "
                f"lat={self.lateral_m:+.3f} size={self.size_frac:.2f} "
                f"| 앞면까지 남음 {self.z_m - stop_m:+.3f}m")


@dataclass(frozen=True)
class Cmd:
    """상태기계가 내는 한 틱의 명령."""
    linear: float
    angular: float
    phase: str
    done: bool
    reason: str
