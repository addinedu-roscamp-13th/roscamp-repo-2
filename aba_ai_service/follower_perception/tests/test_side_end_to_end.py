"""Side 게이트가 파이프라인 끝까지 이어지는지 — 조각이 아니라 이음매를 본다.

`pose_estimator`(축 선택·캘리브·bbox 가드), `posture`(기하 판정), `posture_gate`(주행 가부),
`pipeline`(Detection 조립), `detection_sink`(로봇 전송 직렬화)는 전부 각자 유닛 테스트가
있다. 그런데 "몸 폭이 몸통 길이 대비 좁은 한 프레임이 실제로 로봇을 세우는가"를
처음부터 끝까지 따라간 시험은 없었다 — 이 파일이 그 이음매다.

`FollowerPerception` 은 `tests/test_pipeline_posture.py` 의 대역(FakeDetector/FakeMatcher)
설정 방식을 그대로 따른다. 다른 점은 `pose` 다 — 저기는 `FakePose`(문자열을 그대로
돌려주는 대역)를 쓰지만, 여기는 **진짜** `PoseEstimator` + **진짜** `posture` 모듈에
가짜 YOLO 모델만 얹는다. 그래야 "기하 → Side" 판정 자체가 진짜로 성립하는지까지 본다.
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # aba_ai_service
for _p in (_ROOT, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from detection_sink import detection_to_dict                        # noqa: E402
from follower_perception.detection import TrackedBox                # noqa: E402
from follower_perception.pipeline import FollowerPerception         # noqa: E402
from follower_perception.pose_estimator import (                    # noqa: E402
    PoseEstimator, load_posture_module,
)

# COCO-17: 어깨 5/6, 골반 11/12 — 축 판정이 실제로 읽는 넷.
L_SH, R_SH, L_HIP, R_HIP = 5, 6, 11, 12

GOOD_CONF = np.ones(17, dtype=float)
LOW_CONF = np.zeros(17, dtype=float)

# bbox 종횡비를 시험 전체에서 고정한다. `PoseEstimator` 는 캘리브 구간 bbox 로
# `ref_bbox_hw` 를 재고(파일이 없으면 런타임 60프레임에서), 그 뒤로는 bbox 가드가
# 켜진다. bbox 를 바꾸면 이 시험이 보려는 "키포인트 기하 → Side" 판정과 다른 경로
# (bbox 가드)로 Lying/Side 가 나올 수 있어, 원인이 섞인다.
BBOX = (100, 50, 300, 400)
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)   # 진짜 PoseEstimator 가 crop 을 슬라이스한다


def _keypoints(shoulder_mid=(200.0, 100.0), hip_mid=(200.0, 300.0), width=100.0):
    """어깨 2점·골반 2점만 채운 COCO-17. torso 축 판정은 이 넷만 본다."""
    xy = np.zeros((17, 2), dtype=float)
    sx, sy = shoulder_mid
    hx, hy = hip_mid
    xy[L_SH] = (sx - width / 2.0, sy)
    xy[R_SH] = (sx + width / 2.0, sy)
    xy[L_HIP] = (hx - width / 2.0, hy)
    xy[R_HIP] = (hx + width / 2.0, hy)
    return xy


def _standing_kp():
    """정상 비율(몸통길이/몸폭 = 2.0), 화면상 수직 — 이걸로 기준(ref_ratio)을 세운다."""
    return _keypoints()


def _side_kp():
    """Side 의 정의 그대로: 몸 폭이 몸통 길이 대비 좁다. 화면상으로는 여전히 수직
    (어깨중점·골반중점의 x 가 같다) — 카메라를 보며 몸만 돌아선 모양이라, Lying 의
    화면각 경로를 건드리지 않고 오직 폭 붕괴만으로 Side 를 낸다."""
    return _keypoints(hip_mid=(200.0, 400.0), width=20.0)


class _KP:
    def __init__(self, xy, conf):
        self.xy = [xy]
        self.conf = [conf]


class _Res:
    def __init__(self, kp):
        self.keypoints = kp


class FakeModel:
    """YOLO pose 모델 대역. (xy, conf) 시퀀스를 순서대로 돌려주고, 소진되면 마지막
    값을 반복한다(`tests/test_pose_estimator.py` 의 동명 대역과 같은 계약)."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, crop, verbose=False):
        xy, conf = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        return [_Res(_KP(xy, conf))]


class FakeDetector:
    """항상 같은 bbox 의 트랙 하나만 낸다. 검출·재식별은 이 시험의 관심사가 아니다
    (`tests/test_pipeline_posture.py` 의 FakeDetector 와 같은 얕은 대역)."""

    def __init__(self, bbox):
        x1, y1, x2, y2 = bbox
        self._box = TrackedBox(bbox=bbox, cx=(x1 + x2) / 2.0, cy=(y1 + y2) / 2.0,
                               area=(x2 - x1) * (y2 - y1), track_id=1, confidence=0.9)

    def detect(self, frame):
        return [self._box]


class FakeMatcher:
    """owner 를 항상 유일한 후보로 잡는다. 재식별 로직은 관심사가 아니다."""

    def match(self, cands, frame):
        return cands[0].track_id if cands else None

    def calibrate(self, crop):
        pass


#: `RatioCalibrator` 가 기준을 확정하기까지 필요한 프레임 수. 상수를 베끼지 않고
#: 실제 PoseEstimator 에 물어본다 — posture.py 가 값을 바꿔도 이 시험이 안 깨진다.
_NEEDED = PoseEstimator(model=FakeModel([(_standing_kp(), GOOD_CONF)])).calibration_progress[1]


def _standing_then(xy, conf):
    """기준 비율을 세운 뒤(서 있는 프레임 `_NEEDED` 번) 마지막 한 프레임만 대상
    지오메트리로 바꾼 시퀀스. 이 준비 구간이 없으면 "Calibrating" 만 나가고 진짜
    판정을 못 본다."""
    return [(_standing_kp(), GOOD_CONF)] * _NEEDED + [(xy, conf)]


def _run_to_detection(seq, bbox=BBOX, frame=FRAME):
    """(xy, conf) 시퀀스를 실제 `FollowerPerception` 에 프레임 수만큼 흘려 마지막
    Detection 을 낸다. 검출기·매처만 대역이고, PoseEstimator·posture 모듈·
    PostureGate·FollowerPerception 은 전부 진짜다."""
    pose = PoseEstimator(model=FakeModel(seq))
    p = FollowerPerception(detector=FakeDetector(bbox), reid=object(), pose=pose)
    p.matcher = FakeMatcher()
    for _ in range(len(seq)):
        p.run(frame)
    return p.get_latest()


# ── 1. 옆으로 선 사람 → 멈춘다 ─────────────────────────────────────────────────

def test_sideways_person_stops_the_robot():
    """이 변경 전체가 존재하는 이유. 몸 폭이 몸통 길이 대비 좁은 한 프레임이
    검출 → 자세 판정 → 게이트를 지나 실제로 motion_ok=False 인 Detection 으로
    나오는지 — 조각은 다 있었지만 이 연결 자체를 확인한 시험은 없었다."""
    det = _run_to_detection(_standing_then(_side_kp(), GOOD_CONF))
    assert det is not None, "Side 여도 '보이는' 것은 맞다 — 검출 자체가 사라지면 안 된다"
    assert det.posture == "Side"
    assert det.motion_ok is False


# ── 2. 그 Side 판정이 직렬화에서도 살아남는다 ──────────────────────────────────

def test_sideways_detection_survives_serialisation():
    """로컬에서만 멈추고 로봇으로는 안 실리면 이 기능은 무의미하다.
    `detection_to_dict` 를 지난 뒤에도 posture/motion_ok 가 그대로 있어야 한다."""
    det = _run_to_detection(_standing_then(_side_kp(), GOOD_CONF))
    payload = detection_to_dict(det)
    assert payload["posture"] == "Side"
    assert payload["motion_ok"] is False


# ── 3. 대조군 — 정면이면 여전히 움직인다 ───────────────────────────────────────

def test_frontal_person_keeps_moving():
    """이 시험이 없으면 test 1 은 "뭘 넣어도 멈춘다"는 코드에서도 통과한다."""
    det = _run_to_detection(_standing_then(_standing_kp(), GOOD_CONF))
    assert det is not None
    assert det.posture == "Standing"
    assert det.motion_ok is True


# ── 4. 새 배선이 '정지'를 '허용'으로 뒤집지 않는다 ─────────────────────────────
#
# Side 게이트 이전의 순정 판정(보정 없음 — ref_ratio 는 하드코딩된 기본값, bbox 인자
# 없음)이 Lying/Unknown 이라던 지오메트리가, 캘리브레이션·bbox 가드·축 선택을 다
# 얹은 새 파이프라인에서 Standing 으로 뒤집히면 안 된다. 뒤집히면 "서야 할 때
# 로봇이 움직인다"는 뜻이라 이 변경 전체의 안전 목적과 정면으로 어긋난다.
#
# 무작위 표본이 아니라, 서로 다른 이유로 Lying/Unknown 이 나오는 지오메트리를
# 하나씩 고른다 — 실패하면 어떤 기하가 문제인지 이름으로 바로 드러난다.

def _lying_flat_kp():
    """화면상으로는 수직(어깨중점·골반중점의 x 가 같다)이지만 몸통이 극도로
    짧다 — 카메라 축 방향으로 누워 화면에는 서 있는 것처럼 보이는 자세
    (foreshorten 경로로 Lying 이 나오는 경우)."""
    return _keypoints(hip_mid=(200.0, 120.0))


def _diagonal_45_kp():
    """화면 기울기 45도 — Standing/Lying 임계 밴드(40~50도)의 정중앙. 몸 폭 대비
    몸통 길이가 커서(2.83) foreshorten 이 1로 클램프돼, 캘리브 기준값이 1.9 든
    2.0 이든 결과가 흔들리지 않는(=이 시험 자체가 뜬금없이 깨지지 않는) 경계 사례다."""
    return _keypoints(hip_mid=(400.0, 300.0))


def _degenerate_kp():
    """어깨중점과 골반중점이 같은 점 — 몸통 길이 0. 신뢰도와 무관하게, 기준
    비율과도 무관하게 Unknown 이어야 한다(`classify_posture` 의 EPS 가드)."""
    return _keypoints(hip_mid=(200.0, 100.0))


# 이름 → (xy, conf). 저마다 다른 코드 경로에서 Lying/Unknown 이 나온다:
# 원근 눕음(foreshorten) · 화면각 애매(밴드 경계) · 신뢰도 미달 · 축퇴(길이 0).
_OLD_STOPS_SPREAD = {
    "lying_flat_short_torso": (_lying_flat_kp(), GOOD_CONF),
    "diagonal_45_borderline": (_diagonal_45_kp(), GOOD_CONF),
    "low_confidence_all_torso_points": (_standing_kp(), LOW_CONF),
    "degenerate_zero_length_torso": (_degenerate_kp(), GOOD_CONF),
}


def test_new_pipeline_never_turns_an_old_stop_into_standing():
    posture = load_posture_module()
    for name, (xy, conf) in _OLD_STOPS_SPREAD.items():
        old_verdict, _angle = posture.classify_posture(xy, conf)   # 순정 기본값 — 보정도 bbox 도 없음
        assert old_verdict in (posture.LYING, posture.UNKNOWN), (
            f"{name}: 이 스프레드는 옛 판정이 Lying/Unknown 인 지오메트리만 골라야 "
            f"뜻이 있다 — 실제로는 {old_verdict} 가 나왔다(지오메트리를 다시 골라야 한다)")

        new_verdict = _run_to_detection(_standing_then(xy, conf)).posture
        assert new_verdict != "Standing", (
            f"{name}: 옛 순정 판정은 {old_verdict} 였는데 새 파이프라인은 Standing 이다 "
            f"— 정지했어야 할 자세에서 로봇이 움직인다")
