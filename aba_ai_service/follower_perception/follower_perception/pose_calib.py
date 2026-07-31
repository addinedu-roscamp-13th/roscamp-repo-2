"""자세 판정 보정값(`pose_calib.json`) 로더.

ArUco 마커가 알려진 기준으로 카메라를 한 번 재서 파일로 굳히듯, 이 파일은
`scripts/calibrate_pose.py` 가 **정면 직립 구간**에서 잰 값을 담는다.

## 파일이 없는 것이 정상이다

없으면 런타임은 지금까지처럼 등록 시 `RatioCalibrator` 로 60프레임을 재는
경로를 그대로 탄다. 있으면 기준 비율을 즉시 채워 **등록 직후 멈춰 있는 구간이
사라진다.**

## 깨져 있어도 추종을 죽이지 않는다

설정 파일 하나가 20Hz 제어 루프를 세우면 안 된다. 파싱 실패는 경고만 찍고
기본값을 돌려준다.

## `conf_min` 은 파일에서 안 읽는다

키포인트 신뢰도 임계는 안전에 직결된다. 낮추면 판정 커버리지가 조금 늘지만
(실측 0.5→0.3 이 앞캠 +2.6%p) 저 신뢰도 좌표는 방향성 없는 난수라 **양쪽으로 다
틀린다.** 그중 "누웠는데 Standing" 은 쓰러진 사람에게 로봇을 보낸다.
그래서 값은 코드에 박고 캘리브는 임계별 통과율을 **리포트에만** 싣는다.

## JSON 이 파싱돼도 값은 또 다른 문제다

파일이 없거나 깨진 JSON 이면 파싱 단계에서 걸러진다. 하지만 문법은 멀쩡한데
`side_factor` 가 문자열이거나, `ref_ratios` 가 배열이거나, `NaN` 이 섞여 있으면
파싱은 통과하고 그대로 추정기에 들어간다. `NaN` 이 특히 위험하다 — 모든 비교가
`False` 라 임계가 조용히 영영 안 켜진다. 그래서 필드마다 값을 검증하고,
하나가 잘못돼도 **그 필드만** 기본값으로 내린다 — 파일 하나를 통째로 버리면
멀쩡한 나머지 필드까지 같이 잃는다.
"""
import json
import math
import os
from dataclasses import dataclass, field

CALIB_PATH_ENV = "LIBI_POSE_CALIB"
DEFAULT_CALIB_NAME = "pose_calib.json"

#: 키포인트 최소 신뢰도. **파일로 못 바꾼다** — 위 머리말 참고.
CONF_MIN = 0.5


@dataclass(frozen=True)
class PoseCalib:
    conf_min: float = CONF_MIN
    axis_priority: tuple = ("torso",)
    ref_ratios: dict = field(default_factory=dict)
    side_factor: float = 1.6
    #: Task 1 bbox 가드가 비교하는 기준 h/w(서있음 구간 bbox 높이/폭의 중앙값).
    #: 기본은 **None** — 근거 없는 기준으로 판정하느니 가드를 꺼 둔다(캘리브 없이
    #: 안 켜진다는 원칙은 `bbox_side_frac` 과 같다). 캘리브가 실측하면 파일 값이 켠다.
    ref_bbox_hw: float = None
    #: Task 1 bbox 가드의 lying 임계. 코드 기본값(BBOX_LYING_FRAC=0.45)과 같은
    #: 값으로 시작하고, 캘리브가 다시 재면 파일 값이 이긴다.
    bbox_lying_frac: float = 0.45
    #: Task 1 bbox 가드의 side 임계. 기본은 **꺼짐(None)** — 실측상 앞캠에서
    #: 0%만 발동했고 뒷캠에서는 오탐만 냈다. 그래서 기본으로는 안 켜고,
    #: 캘리브가 실측으로 켜기로 판단했을 때만 파일 값으로 켠다.
    bbox_side_frac: float = None
    filter: dict = None
    source: str = None


def _calib_path(path=None):
    if path is not None:
        return path
    env = os.environ.get(CALIB_PATH_ENV)
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        DEFAULT_CALIB_NAME)


#: posture.py(yolo_pose, 별도 저장소)가 아는 축 이름. 여기서 값만 맞춰 다시
#: 적는다 — 이 로더는 그 저장소에 의존하지 않는다. 파일 하나 검증하겠다고
#: 외부 저장소 import 가 걸리면(경로 없음 등) 20Hz 루프가 죽는다.
_KNOWN_AXES = ("torso", "head_hip", "shoulder_knee")

#: KeypointFilter(OneEuro)가 받는 이름 중 `conf_min` 을 뺀 것. `conf_min` 은
#: 추정기가 `KeypointFilter(**filter, conf_min=self.conf_min)` 로 직접 채운다
#: — 파일에도 섞여 있으면 그 자리에서 "중복 키워드" 로 죽는다(이번 회귀의
#: 원인). 화이트리스트라 오타·모르는 키도 같이 걸러진다.
_FILTER_KEYS = ("min_cutoff", "beta", "d_cutoff", "n_points")


def _validated_float(d, key, default, ok, hint):
    """`d[key]` 가 `ok` 를 만족하는 유한 실수면 그 값, 아니면 기본값 + 경고.

    키가 없으면 조용히 기본값(설정 안 함은 정상). `default` 가 `None` 인
    필드(가드 꺼짐이 유효한 값)는 파일의 `null` 도 그대로 통과시킨다.
    `NaN`/`inf` 는 항상 막는다 — `NaN` 은 모든 비교가 `False` 라 임계가
    조용히 영영 안 켜진다.
    """
    if key not in d:
        return default
    raw = d[key]
    if raw is None and default is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float("nan")
    if not math.isfinite(v) or not ok(v):
        print(f"[pose_calib] {key} 값이 잘못됐습니다({raw!r}, {hint}) "
              f"— 기본값({default!r})으로 갑니다")
        return default
    return v


def _validated_axis_priority(d, default):
    """문자열 목록만 받는다. 모르는 축 이름은 하나씩 걸러낸다 — 하나가
    이상하다고 나머지 축까지 버리면 손해다. 하나도 안 남으면 기본값."""
    if "axis_priority" not in d:
        return default
    raw = d["axis_priority"]
    if not isinstance(raw, (list, tuple)):
        print(f"[pose_calib] axis_priority 가 문자열 목록이 아닙니다({raw!r}) "
              f"— 기본값으로 갑니다")
        return default
    known = tuple(a for a in raw if isinstance(a, str) and a in _KNOWN_AXES)
    dropped = [a for a in raw if a not in known]
    if dropped:
        print(f"[pose_calib] axis_priority 에서 모르는 축을 뺐습니다: {dropped!r}")
    if not known:
        print("[pose_calib] axis_priority 에 남은 축이 없습니다 — 기본값으로 갑니다")
        return default
    return known


def _validated_ref_ratios(d):
    """`{축 이름: 유한 양수}` 만 남긴다. 항목 하나만 버린다 — 축 하나가
    깨졌다고 나머지 기준까지 버리면 손해가 더 크다.

    `None` 은 예외로 그대로 통과시킨다 — "이 축을 재봤지만 못 썼다"는 뜻으로
    `calibrate_pose.py` 가 실제로 이렇게 낸다. 여기서 지우면 그 축 이름
    자체가 없어져, 소비자가 `ref_ratios.get(axis, ref_ratio)` 로 조회할 때
    "못 씀"이 아니라 축 무관 기본값(`TORSO_REF_RATIO` 등)으로 조용히
    새어나간다.
    """
    raw = d.get("ref_ratios", {})
    if not isinstance(raw, dict):
        print(f"[pose_calib] ref_ratios 가 객체가 아닙니다({raw!r}) — 빈 값으로 갑니다")
        return {}
    out = {}
    for k, v in raw.items():
        if v is None:
            out[k] = None
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            fv = float("nan")
        if math.isfinite(fv) and fv > 0:
            out[k] = fv
        else:
            print(f"[pose_calib] ref_ratios[{k!r}] 값이 잘못됐습니다({v!r}, "
                  f"유한 양수 또는 null 이어야 함) — 뺍니다")
    return out


def _validated_filter(d, default):
    """`None` 이거나, 화이트리스트 키만 남긴 매핑. `conf_min` 은 이름을 짚어
    경고한다 — 추정기와 키워드가 겹쳐 죽는 원인이 그것 하나이기 때문이다.
    """
    if "filter" not in d:
        return default
    raw = d["filter"]
    if raw is None:
        return None
    if not isinstance(raw, dict):
        print(f"[pose_calib] filter 가 객체가 아닙니다({raw!r}) — 기본값({default!r})으로 갑니다")
        return default
    out = {}
    for k, v in raw.items():
        if k == "conf_min":
            print("[pose_calib] filter.conf_min 은 추정기가 직접 채웁니다 — 파일 값은 뺍니다 "
                  "(안 빼면 KeypointFilter 생성 시 키워드가 겹쳐 죽습니다)")
        elif k not in _FILTER_KEYS:
            print(f"[pose_calib] filter 에 모르는 키를 뺐습니다: {k!r}")
        else:
            out[k] = v
    return out


def load_pose_calib(path=None):
    """보정값을 읽는다. 없거나 깨져 있으면 기본값. 필드 하나가 잘못돼도
    그 필드만 기본값으로 내리고 나머지는 파일 값 그대로 쓴다."""
    p = _calib_path(path)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return PoseCalib()
    except (OSError, ValueError) as e:      # 깨진 JSON, 권한 없음 등
        print(f"[pose_calib] {p} 를 못 읽었습니다({type(e).__name__}: {e}) — 기본값으로 갑니다")
        return PoseCalib()
    if not isinstance(d, dict):
        print(f"[pose_calib] {p} 가 객체가 아닙니다 — 기본값으로 갑니다")
        return PoseCalib()
    default = PoseCalib()
    return PoseCalib(
        conf_min=CONF_MIN,                      # 파일 값을 일부러 무시한다
        axis_priority=_validated_axis_priority(d, default.axis_priority),
        ref_ratios=_validated_ref_ratios(d),
        side_factor=_validated_float(d, "side_factor", default.side_factor,
                                     lambda v: v >= 1, "1 이상의 유한값이어야 함"),
        ref_bbox_hw=_validated_float(d, "ref_bbox_hw", default.ref_bbox_hw,
                                     lambda v: v > 0, "0보다 큰 유한값 또는 null 이어야 함"),
        bbox_lying_frac=_validated_float(d, "bbox_lying_frac", default.bbox_lying_frac,
                                         lambda v: 0 < v < 1, "0과 1 사이(양끝 제외)여야 함"),
        bbox_side_frac=_validated_float(d, "bbox_side_frac", default.bbox_side_frac,
                                        lambda v: v > 1, "1보다 큰 유한값 또는 null 이어야 함"),
        filter=_validated_filter(d, default.filter),
        source=d.get("source"),
    )
