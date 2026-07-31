"""벤치 지표 산식. 모델 없이 순수 계산만 확인한다."""
import numpy as np
import pytest

from scripts.pose_bench import fatal_misses, label_metrics, metrics

L_SH, R_SH, L_HIP, R_HIP = 5, 6, 11, 12


def _seq(n, jitter_px=0.0, conf=0.9, seed=0):
    rng = np.random.default_rng(seed)
    xy = np.zeros((n, 17, 2)); cf = np.full((n, 17), conf)
    for i in range(n):
        xy[i, L_SH] = (80.0, 100.0); xy[i, R_SH] = (120.0, 100.0)
        xy[i, L_HIP] = (80.0, 200.0); xy[i, R_HIP] = (120.0, 200.0)
        if jitter_px:
            xy[i] += rng.normal(0.0, jitter_px, (17, 2))
    return xy, cf


def test_torso4_pass_rate():
    xy, cf = _seq(10)
    cf[3:, L_HIP] = 0.1
    assert metrics(xy, cf, 0.5)["torso4_pass"] == 0.3


def test_jitter_is_normalised_by_torso_length():
    """거리가 다르면 픽셀 지터도 달라진다. 몸통 길이로 나눠야 비교된다."""
    near_xy, cf = _seq(200, jitter_px=2.0, seed=1)
    far_xy = near_xy * 0.5                          # 절반 거리 -> 픽셀도 절반
    a = metrics(near_xy, cf, 0.5)["jitter_torso4"]
    b = metrics(far_xy, cf, 0.5)["jitter_torso4"]
    assert abs(a - b) < a * 0.15, "정규화가 안 됐다"


def test_jitter_only_counts_passing_frames():
    """죽은 점의 난수 좌표를 섞으면 지터가 아니라 실패율을 재는 것이 된다."""
    xy, cf = _seq(100, jitter_px=1.0, seed=2)
    clean = metrics(xy, cf, 0.5)["jitter_torso4"]
    xy[50:60] = 9999.0
    cf[50:60, :] = 0.1                              # 신뢰도 미달로 표시
    assert metrics(xy, cf, 0.5)["jitter_torso4"] == np.float64(clean).item() or \
        abs(metrics(xy, cf, 0.5)["jitter_torso4"] - clean) < clean * 0.4


def test_conf_mean_is_per_keypoint():
    xy, cf = _seq(10)
    cf[:, L_SH] = 0.3
    m = metrics(xy, cf, 0.5)
    assert m["conf_mean"][L_SH] == pytest.approx(0.3)
    assert m["conf_mean"][R_SH] == pytest.approx(0.9)


def test_fatal_misses_counts_standing_on_lying_labels():
    states = ["Standing", "Lying", "Standing", "Side"]
    labels = ["lying", "lying", "standing", "lying"]
    assert fatal_misses(states, labels)["frames"] == 1


def test_unknown_counts_as_fatal_while_the_gate_still_allows():
    """이게 이 지표의 핵심이다.

    `PostureGate` 는 `Unknown` 을 즉시 정지로 치지 않고 25프레임(≈1.7초) 동안
    직전 허용 상태를 유지한다(constants.py:42). 그래서 누움 구간에서 `Unknown`
    20프레임은 "치명오판 0" 이 아니라 **로봇이 1.3초 전진** 이다.
    """
    states = ["Standing"] + ["Unknown"] * 20
    labels = ["standing"] + ["lying"] * 20
    assert fatal_misses(states, labels)["frames"] == 20


def test_gate_eventually_stops_on_a_long_unknown_run():
    """25프레임을 넘기면 게이트가 막는다 — 그 뒤는 치명이 아니다.

    허용되는 것은 **24**프레임이다. `PostureGate` 가 `_unknown_run >= unknown_limit`
    로 막으므로 25번째 Unknown 은 이미 차단된 프레임이다. 25 를 기대하면 게이트를
    한 프레임 관대하게 읽는 셈이라, 이 지표가 재려는 "실제로 몇 프레임 전진하나" 가
    틀어진다. (기존 test_unknown_holds_previous_until_limit 와 같은 경계다)
    """
    states = ["Standing"] + ["Unknown"] * 40
    labels = ["standing"] + ["lying"] * 40
    assert fatal_misses(states, labels)["frames"] == 24


def test_max_consecutive_allowed_is_reported():
    """개수만으로는 '1.3초 연속 전진' 이 안 보인다."""
    states = ["Standing"] + ["Unknown"] * 10 + ["Lying"] + ["Unknown"] * 5
    labels = ["standing"] + ["lying"] * 16
    assert fatal_misses(states, labels)["max_run"] == 10


def test_fatal_misses_ignores_unlabelled_frames():
    assert fatal_misses(["Standing"] * 3, [None, None, None])["frames"] == 0


def test_side_is_not_a_fatal_miss_on_a_lying_label():
    """측면도 즉시 정지시키므로 로봇을 사람에게 밀어 넣지 않는다."""
    assert fatal_misses(["Side"], ["lying"])["frames"] == 0


def test_calibrating_is_not_a_fatal_miss():
    assert fatal_misses(["Calibrating"], ["lying"])["frames"] == 0


# ─────────────────────────── label_metrics ────────────────────────────────

def test_label_metrics_accuracy_counts_matches():
    states = ["Standing", "Lying", "Side", "Standing"]
    labels = ["standing", "lying", "lying", "lying"]      # 마지막 한 개만 오답
    assert label_metrics(states, labels)["accuracy"] == 0.75


def test_label_metrics_side_counts_as_a_lying_match():
    """Side 도 즉시 정지시키므로 lying 라벨과 일치하는 것으로 본다."""
    assert label_metrics(["Side"], ["lying"])["recall_lying"] == 1.0


def test_label_metrics_undecided_counts_as_wrong_but_reported_separately():
    """Unknown/Calibrating 은 오답이지만, '확신을 갖고 틀렸다'와는 다른 실패라
    accuracy 와 undecided_rate 를 따로 낸다."""
    states = ["Unknown", "Calibrating", "Lying"]
    labels = ["lying", "lying", "lying"]
    m = label_metrics(states, labels)
    assert m["accuracy"] == pytest.approx(1 / 3)
    assert m["undecided_rate_lying"] == pytest.approx(2 / 3)


def test_label_metrics_ignores_unlabelled_frames():
    m = label_metrics(["Standing", "Lying"], [None, None])
    assert np.isnan(m["accuracy"])


# ─────────────────────────── _posture_states 의 bbox_guard 배선 ───────────

def test_posture_states_forwards_bbox_guard_args():
    """bbox_guard 가 실제로 켜지는지 확인한다.

    안 켜지면 키포인트가 죽는 프레임(누움·측면)에서 전부 Unknown 이 나,
    fatal_misses 가 모델이 아니라 라벨 구간 길이만 재는 것이 된다 — 실측으로
    걸린 회귀다(fix round 2).
    """
    from follower_perception.pose_estimator import load_posture_module
    from scripts.pose_bench import _posture_states

    n = 65   # RatioCalibrator 기본 60프레임을 다 채우고 몇 프레임 더 classify 를 본다
    xy_seq = np.zeros((n, 17, 2)); conf_seq = np.full((n, 17), 0.9)
    for i in range(n):
        xy_seq[i, L_SH] = (80.0, 100.0); xy_seq[i, R_SH] = (120.0, 100.0)
        xy_seq[i, L_HIP] = (80.0, 200.0); xy_seq[i, R_HIP] = (120.0, 200.0)
    bbox_seq = np.tile([10.0, 10.0, 60.0, 210.0], (n, 1))     # w=50, h=200 -> h/w=4.0
    frame_idx = np.arange(n)

    posture = load_posture_module()
    real = posture.classify_posture
    seen = {}

    def spy(xy, conf, **kw):
        seen.update(kw)
        return real(xy, conf, **kw)

    posture.classify_posture = spy
    try:
        _posture_states(frame_idx, xy_seq, conf_seq, bbox_seq, n, 15.0, (320, 240),
                         [(0.0, 1000.0)], 0.5)
    finally:
        posture.classify_posture = real

    assert seen.get("bbox_wh") == (50.0, 200.0)
    assert seen.get("ref_bbox_hw") == 4.0
    assert seen.get("bbox_clipped") is False


# ─────────────────────────── --side (fix round 3) ──────────────────────────

def test_side_flag_parses_and_is_repeatable():
    from scripts.pose_bench import build_parser
    args = build_parser().parse_args([
        "--video", "a.mp4", "--rotate", "0", "--label", "front",
        "--side", "front=0:10-0:12", "--side", "front=0:20-0:21",
    ])
    assert args.side == ["front=0:10-0:12", "front=0:20-0:21"]


def test_lying_wins_over_side_on_overlap():
    """겹치면 안전 쪽(lying)이 이긴다 — Side 는 하류에서 '정상 추종'으로 읽히므로
    누운 프레임이 side 로 잘못 기록되면 안 된다."""
    from scripts.pose_bench import _label_track
    labels = _label_track(5, 1.0, [], [(2.0, 2.0)], [(2.0, 2.0)])
    assert labels[2] == "lying"


def test_label_metrics_recall_side_counts_a_side_verdict():
    assert label_metrics(["Side"], ["side"])["recall_side"] == 1.0


def test_stop_agreement_counts_lying_on_a_side_label_as_a_stop():
    """자세를 못 맞혀도(Lying != Side) 로봇은 섰다 — 안전 쪽 숫자는 따로 100%."""
    assert label_metrics(["Lying"], ["side"])["stop_agreement"] == 1.0


def test_stop_agreement_does_not_count_unknown():
    states = ["Side", "Unknown"]
    labels = ["side", "side"]
    m = label_metrics(states, labels)
    assert m["stop_agreement"] == 0.5
    assert m["undecided_rate_side"] == 0.5
