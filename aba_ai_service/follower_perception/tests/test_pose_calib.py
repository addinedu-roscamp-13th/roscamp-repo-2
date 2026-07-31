"""캘리브 JSON 로더. 파일은 **없는 것이 정상**이다.

설정 파일 하나가 20Hz 추종 루프를 세우면 안 된다. 그래서 없거나 깨져 있으면
경고만 찍고 기본값으로 간다 — 예외를 올리지 않는다.
"""
import json

import pytest

from follower_perception.pose_calib import PoseCalib, load_pose_calib


def test_missing_file_gives_defaults():
    c = load_pose_calib("/nonexistent/pose_calib.json")
    assert c.conf_min == 0.5
    assert c.axis_priority == ("torso",)
    assert c.ref_ratios == {}
    assert c.side_factor == 1.6
    assert c.filter is None


def test_none_path_gives_defaults():
    assert load_pose_calib(None) == PoseCalib()


def test_values_are_loaded(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({
        "axis_priority": ["head_hip", "torso"],
        "ref_ratios": {"torso": 2.08, "head_hip": 3.4},
        "side_factor": 1.8,
        "filter": {"min_cutoff": 1.2, "beta": 0.06},
        "source": "pi_20260731.mp4",
    }))
    c = load_pose_calib(str(p))
    assert c.axis_priority == ("head_hip", "torso")
    assert c.ref_ratios == {"torso": 2.08, "head_hip": 3.4}
    assert c.side_factor == 1.8
    assert c.filter == {"min_cutoff": 1.2, "beta": 0.06}
    assert c.source == "pi_20260731.mp4"


def test_broken_json_does_not_raise(tmp_path, capsys):
    """추종 루프를 죽이지 않는다. 경고만 찍고 기본값으로 간다."""
    p = tmp_path / "pose_calib.json"
    p.write_text("{ this is not json")
    c = load_pose_calib(str(p))
    assert c == PoseCalib()
    assert "pose_calib" in capsys.readouterr().out


def test_conf_min_in_file_is_ignored(tmp_path):
    """임계는 안전에 직결돼 사람이 정한다. 파일이 낮춰도 안 따른다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"conf_min": 0.25}))
    assert load_pose_calib(str(p)).conf_min == 0.5


def test_unknown_keys_are_ignored(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"side_factor": 1.7, "future_field": 123}))
    assert load_pose_calib(str(p)).side_factor == 1.7


def test_env_var_overrides_path(tmp_path, monkeypatch):
    p = tmp_path / "custom.json"
    p.write_text(json.dumps({"side_factor": 2.2}))
    monkeypatch.setenv("LIBI_POSE_CALIB", str(p))
    assert load_pose_calib().side_factor == 2.2


# ── bbox 종횡비 가드 필드 (Task 1 의 BBOX_LYING_FRAC/BBOX_SIDE_FRAC 에 대응) ──
#
# Task 1(yolo_pose/posture.py) 의 bbox_guard 가 쓰는 두 임계값을 캘리브에서도
# 받는다. lying 은 코드 기본값과 같은 값(0.45)으로 켜져 있고, side 는 실측상
# 앞캠에서 0%만 발동하고 뒷캠에서는 오탐만 내서 기본은 꺼짐(None)으로 나간다.

def test_bbox_lying_frac_default_and_load(tmp_path):
    """기본값은 Task 1 의 BBOX_LYING_FRAC(0.45) 과 같다. 파일 값이 있으면 그걸 따른다."""
    assert PoseCalib().bbox_lying_frac == 0.45
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"bbox_lying_frac": 0.5}))
    assert load_pose_calib(str(p)).bbox_lying_frac == 0.5


def test_bbox_side_frac_defaults_to_none():
    """실측: 앞캠은 0%만 발동, 뒷캠은 오탐만 냈다 — 그래서 기본은 꺼짐(None)으로 나간다."""
    assert PoseCalib().bbox_side_frac is None
    assert load_pose_calib("/nonexistent/pose_calib.json").bbox_side_frac is None


def test_bbox_side_frac_is_loaded_when_present(tmp_path):
    """캘리브가 재서 켜기로 판단하면 그 값을 따른다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"bbox_side_frac": 1.7}))
    assert load_pose_calib(str(p)).bbox_side_frac == 1.7


def test_ref_bbox_hw_defaults_to_none():
    """기준을 잰 적이 없으면 가드를 꺼 둔다 — 근거 없는 기준이 있는 가드보다 낫다."""
    assert PoseCalib().ref_bbox_hw is None
    assert load_pose_calib("/nonexistent/pose_calib.json").ref_bbox_hw is None


def test_ref_bbox_hw_is_loaded_when_present(tmp_path):
    """캘리브(`calibrate_pose.py`)가 서있음 구간에서 잰 값이 있으면 그걸 따른다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"ref_bbox_hw": 3.14}))
    assert load_pose_calib(str(p)).ref_bbox_hw == 3.14


# ── 필드 값 검증 — JSON 은 멀쩡한데 값이 잘못된 경우 ────────────────────────
#
# 문법은 통과하고 값만 잘못된 파일(문자열이 들어갈 자리에 숫자, 범위 밖 값,
# NaN 등)은 파싱 단계에서 안 걸린다. 필드마다 검증하고, 하나가 잘못돼도 그
# 필드만 기본값으로 내린다 — 프로세스는 항상 살아 있어야 한다.

def test_axis_priority_drops_unknown_names_but_keeps_known_ones(tmp_path, capsys):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"axis_priority": ["torso", "banana"]}))
    c = load_pose_calib(str(p))
    assert c.axis_priority == ("torso",)
    assert "axis_priority" in capsys.readouterr().out


def test_axis_priority_falls_back_when_nothing_known_survives(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"axis_priority": ["banana", "kiwi"]}))
    assert load_pose_calib(str(p)).axis_priority == ("torso",)


def test_axis_priority_falls_back_when_not_a_list(tmp_path):
    """흔한 실수 — 목록 대신 문자열 하나만 준 경우."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"axis_priority": "torso"}))
    assert load_pose_calib(str(p)).axis_priority == ("torso",)


def test_ref_ratios_drops_invalid_entries_individually(tmp_path):
    """축 하나가 깨졌다고 나머지 기준까지 버리면 손해가 더 크다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"ref_ratios": {
        "torso": 2.1, "head_hip": "bad", "shoulder_knee": -5.0,
    }}))
    assert load_pose_calib(str(p)).ref_ratios == {"torso": 2.1}


def test_ref_ratios_keeps_null_as_a_meaningful_value(tmp_path):
    """`null` 은 "이 축을 재봤지만 못 썼다"는 뜻이라 지우면 안 된다 — 지우면
    소비자가 축 무관 기본값으로 조용히 새어나간다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"ref_ratios": {"torso": 2.1, "head_hip": None}}))
    assert load_pose_calib(str(p)).ref_ratios == {"torso": 2.1, "head_hip": None}


def test_side_factor_below_one_falls_back(tmp_path):
    """1 미만이면 `posture.is_side` 의 "Side 가 Lying 을 못 가린다" 불변식이 깨진다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"side_factor": 0.9}))
    assert load_pose_calib(str(p)).side_factor == 1.6


def test_side_factor_nan_falls_back_with_warning(tmp_path, capsys):
    """NaN 이 가장 위험하다 — 모든 비교가 False 라 임계가 조용히 영영 안 켜진다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"side_factor": float("nan")}))
    c = load_pose_calib(str(p))
    assert c.side_factor == 1.6
    assert "side_factor" in capsys.readouterr().out


def test_bbox_lying_frac_out_of_range_falls_back(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"bbox_lying_frac": 1.5}))
    assert load_pose_calib(str(p)).bbox_lying_frac == 0.45


def test_bbox_side_frac_not_greater_than_one_falls_back(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"bbox_side_frac": 0.9}))
    assert load_pose_calib(str(p)).bbox_side_frac is None


def test_ref_bbox_hw_non_positive_falls_back(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"ref_bbox_hw": -1.0}))
    assert load_pose_calib(str(p)).ref_bbox_hw is None


def test_filter_conf_min_is_dropped_but_rest_survives(tmp_path):
    """`conf_min` 을 안 빼면 추정기가 `KeypointFilter(**filter, conf_min=...)`
    로 만들 때 키워드가 겹쳐 죽는다(이번 회귀)."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"filter": {"min_cutoff": 1.2, "conf_min": 0.9}}))
    assert load_pose_calib(str(p)).filter == {"min_cutoff": 1.2}


def test_filter_unknown_key_is_dropped(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"filter": {"min_cutoff": 1.2, "bogus": 1}}))
    assert load_pose_calib(str(p)).filter == {"min_cutoff": 1.2}


def test_filter_not_a_mapping_falls_back(tmp_path):
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"filter": "on"}))
    assert load_pose_calib(str(p)).filter is None


def test_one_bad_field_does_not_affect_the_others(tmp_path):
    """필드 하나가 잘못됐다고 나머지 멀쩡한 필드까지 기본값으로 내리면 손해다."""
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({
        "side_factor": 0.5,               # 잘못됨(< 1) -> 기본값
        "bbox_lying_frac": 0.3,           # 정상 -> 파일 값 유지
        "ref_bbox_hw": 2.9,               # 정상 -> 파일 값 유지
        "axis_priority": ["head_hip"],    # 정상 -> 파일 값 유지
    }))
    c = load_pose_calib(str(p))
    assert c.side_factor == 1.6
    assert c.bbox_lying_frac == 0.3
    assert c.ref_bbox_hw == 2.9
    assert c.axis_priority == ("head_hip",)


def test_dropped_conf_min_cannot_collide_with_keypointfilter(tmp_path):
    """이번 회귀의 실제 증상을 재현해서 닫는다 — 안 빠졌으면 여기서
    `TypeError: got multiple values for keyword argument 'conf_min'` 로 죽는다."""
    from follower_perception.keypoint_filter import KeypointFilter

    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"filter": {"min_cutoff": 1.2, "conf_min": 0.9}}))
    c = load_pose_calib(str(p))
    KeypointFilter(**c.filter, conf_min=0.5)   # 여기서 안 죽으면 통과
