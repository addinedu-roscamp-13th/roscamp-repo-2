"""캘리브 CLI. 영상 없이 인자 검증과 산식만 확인한다."""
import numpy as np
import pytest

from scripts.calibrate_pose import build_parser, summarise, threshold_report


def test_standing_segment_is_required():
    """없으면 기본값으로 조용히 틀린다. 거부하는 편이 낫다."""
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--video", "x.mp4", "--model", "yolo11n-pose"])


def test_standing_segment_is_accepted():
    p = build_parser()
    a = p.parse_args(["--video", "x.mp4", "--model", "yolo11n-pose",
                      "--standing", "0:03-0:33"])
    assert a.standing == ["0:03-0:33"]


def test_lying_segment_is_optional():
    p = build_parser()
    assert p.parse_args(["--video", "x.mp4", "--model", "yolo11n-pose",
                         "--standing", "0:03-0:33"]).lying == []


def test_standing_and_lying_are_repeatable():
    """실제 영상은 정면 직립·누움이 각각 여러 토막으로 흩어져 있다(segments_draft.md)
    — 한 번만 받으면 못 담는다."""
    p = build_parser()
    a = p.parse_args(["--video", "x.mp4", "--model", "yolo11n-pose",
                      "--standing", "0:00-0:10", "--standing", "0:24-0:27",
                      "--lying", "0:28-0:29", "--lying", "0:32-0:33"])
    assert a.standing == ["0:00-0:10", "0:24-0:27"]
    assert a.lying == ["0:28-0:29", "0:32-0:33"]


def test_reference_ratio_is_the_median():
    """상위 백분위를 쓰면 기준이 노이즈만큼 부풀어 이후 거의 모든 프레임이
    기준보다 낮게 나온다 — 똑바로 서 있어도 각도가 계속 부풀어 오른다."""
    out = summarise({"torso": [1.9, 2.0, 2.1, 9.9]}, whrs_standing=[2.8, 2.9])
    assert out["ref_ratios"]["torso"] == pytest.approx(2.05)


def test_conf_min_is_always_half():
    """임계는 안전에 직결돼 캘리브가 안 정한다."""
    out = summarise({"torso": [2.0] * 10}, whrs_standing=[2.8])
    assert out["conf_min"] == 0.5


def test_side_factor_has_margin_over_the_standing_spread():
    """정면 구간에서 관측된 최대 비율보다 위여야 정면이 측면으로 안 뒤집힌다."""
    out = summarise({"torso": [2.0, 2.2, 2.6]}, whrs_standing=[2.8])
    ref = out["ref_ratios"]["torso"]
    assert out["side_factor"] * ref > 2.6


def test_axis_is_dropped_when_it_never_passes():
    out = summarise({"torso": [2.0] * 10, "head_hip": []}, whrs_standing=[2.8])
    assert out["ref_ratios"]["head_hip"] is None
    assert "head_hip" not in out["axis_priority"]


def test_threshold_report_lists_pass_rates():
    cf = np.full((100, 17), 0.9)
    cf[:40, 5] = 0.35
    r = threshold_report(cf)
    assert r["0.5"] == pytest.approx(0.6)
    assert r["0.3"] == pytest.approx(1.0)


def test_filter_defaults_to_off():
    """켜면 판정 좌표가 바뀌어 무설정 회귀 방어선이 깨진다."""
    assert summarise({"torso": [2.0] * 5}, whrs_standing=[2.8])["filter"] is None


def test_collect_samples_pools_across_multiple_standing_segments():
    """서있음 구간이 여러 토막이면 표본을 다 합쳐야 한다 — 실제 영상은 한
    덩어리가 아니라 정면 직립 토막 사이사이에 다른 자세가 섞여 있다
    (reports/segments_draft.md). 영상·모델 없이, 고정 좌표로만 확인한다."""
    from scripts.calibrate_pose import _collect_samples
    from follower_perception.pose_estimator import load_posture_module

    posture = load_posture_module()
    n = 10
    xy = np.zeros((n, 17, 2))
    conf = np.full((n, 17), 0.9)
    for i in range(n):
        xy[i, 5] = (80.0, 100.0); xy[i, 6] = (120.0, 100.0)      # 어깨
        xy[i, 11] = (80.0, 200.0); xy[i, 12] = (120.0, 200.0)    # 골반
    frame_idx = np.arange(n)
    bbox = np.array([[0.0, 0.0, 40.0, 120.0]] * n)   # h/w = 3.0 고정
    fps = 1.0   # 프레임 i == i초 — 구간 경계를 초 단위로 그대로 쓸 수 있다

    # 서로 떨어진 두 토막(0~1초, 5~6초) -> 프레임 0,1,5,6 이 표본이 된다.
    standing_segs = [(0.0, 1.0), (5.0, 6.0)]
    ratios, whrs_standing, whrs_lying, standing_frames = _collect_samples(
        posture, frame_idx, bbox, xy, conf, fps, standing_segs, [])

    assert standing_frames == 4
    assert len(ratios["torso"]) == 4          # 두 토막의 표본이 한 리스트로 합쳐졌다
    assert len(whrs_standing) == 4
    assert whrs_lying == []
