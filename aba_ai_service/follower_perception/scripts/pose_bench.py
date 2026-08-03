"""여러 pose 모델을 **같은 crop** 에 돌려 비교한다.

## 결정론적 재생

검출(best.pt)을 한 번만 돌려 owner crop 을 .npz 로 캐시하고, 모든 모델이
비트 단위로 같은 crop 을 본다. 라이브로 두 번 돌리면 자세·조명 차이가 모델
차이보다 커진다.

## ⚠️ 대조군은 비대칭 실험이다

`rtmpose-m-coco` 의 ONNX 는 공개돼 있지 않다(2026-07-31 실측 404). 대조군을
`rtmpose-m-body7` 로 대신하는데, body7 은 실사 7종 합본이라 학습 데이터의
**양과 구성이 둘 다** 다르다. 그래서 해석이 한 방향으로만 유효하다:

  · humanart 승 -> body7 이 실사를 훨씬 많이 쓰고도 졌다 -> **도메인 갭 확정**
  · body7 승     -> 도메인 효과인지 데이터 양 효과인지 **구분 불가, 결론 못 냄**

이 문장을 리포트 머리에도 찍는다.

## 앞캠은 --rotate 180 이 필수다

picamera 가 거꾸로 달려 있어 송출 경로도 180 을 건다(image-sender.sh:43).
안 돌리면 모든 모델이 물구나무선 사람을 본다.

## ⚠️ 이 벤치가 재는 것과 안 재는 것

**재는 것**: "같은 detector crop 을 받았을 때 각 어댑터가 내는 결과" — 즉
**end-to-end 어댑터 비교**다.

**안 재는 것 ①: 순수 pose 정확도.** 두 런타임이 crop 을 다르게 다룬다. ultralytics 는
crop 안에서 사람을 다시 검출·선택한 뒤 pose 를 내고, RTMPose 는 crop 전체를 이미
정확한 instance bbox 로 가정한다(rtmlib 0.0.15 `RTMPose.__call__` 이 빈 bboxes 에
`[0,0,w,h]` 를 넣는다). 그래서 crop 패딩·잘린 몸·다른 사람 포함이 두 모델에 다르게
작용한다. 순수 비교를 하려면 모델별 권장 패딩·affine 을 맞춰야 하는데, **우리가 실제로
배포할 조건은 지금 이 조건**이라 이대로 재는 것이 운용에 더 맞는다.

**안 재는 것 ②: detector 실패.** crop 캐시에는 `best.pt` 가 검출·owner 선택에 성공한
프레임만 들어간다(앞캠 74% / 뒷캠 60%). 그래서 **분모를 두 개 낸다**:

  · `pose_frames`  — crop 이 있는 프레임 (pose 모델 간 비교용)
  · `label_frames` — 라벨 구간의 **원본 전체** 프레임 (파이프라인 안전 커버리지용)

`fatal_misses` 는 **원본 전체 프레임**을 훑는다. crop 이 없는 프레임은 판정이 없으므로
게이트가 직전 상태를 유지하고, 그것도 위험이다 — 분모에서 지우면 안 된다.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from statistics import median

import cv2
import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # follower_perception/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from follower_perception.detector import Detector                        # noqa: E402
from follower_perception.pose_calib import CONF_MIN                      # noqa: E402
from follower_perception.pose_estimator import load_posture_module       # noqa: E402
from follower_perception.posture_gate import CALIBRATING, UNKNOWN        # noqa: E402
from scripts.pose_backends import MODELS, make_backend                   # noqa: E402
from scripts.video_segments import frame_in_segment, parse_labeled       # noqa: E402

TORSO = (5, 6, 11, 12)


def metrics(xy_seq, conf_seq, conf_min):
    """(N,17,2)·(N,17) -> 지표 딕셔너리.

    지터는 **몸통 길이로 정규화**하고 **신뢰도 통과 프레임끼리만** 잰다.
    정규화 안 하면 거리에 따라 값이 변해 모델 간 비교가 안 되고, 죽은 점의
    난수 좌표를 섞으면 지터가 아니라 실패율을 재는 것이 된다.
    """
    xy = np.asarray(xy_seq, dtype=float)
    cf = np.asarray(conf_seq, dtype=float)
    ok = cf[:, list(TORSO)].min(axis=1) >= conf_min
    out = {
        "frames": int(len(xy)),
        "conf_mean": cf.mean(axis=0).tolist(),
        "pass_rate": (cf >= conf_min).mean(axis=0).tolist(),
        "torso4_pass": float(ok.mean()) if len(ok) else 0.0,
    }
    idx = np.flatnonzero(ok)
    # 연속으로 통과한 프레임 쌍에서만 차분을 낸다.
    pairs = idx[1:][np.diff(idx) == 1]
    if len(pairs) >= 2:
        prev = pairs - 1
        sh = (xy[pairs][:, 5] + xy[pairs][:, 6]) / 2.0
        hp = (xy[pairs][:, 11] + xy[pairs][:, 12]) / 2.0
        torso_len = np.hypot(*(hp - sh).T)
        torso_len = np.where(torso_len > 1e-6, torso_len, np.nan)
        d = np.linalg.norm(xy[pairs][:, list(TORSO)] - xy[prev][:, list(TORSO)], axis=2)
        out["jitter_torso4"] = float(np.nanstd(d / torso_len[:, None]))
    else:
        out["jitter_torso4"] = float("nan")
    return out


#: 라벨이 이것들이면 로봇이 가면 안 되는 상태다.
_MUST_STOP_LABELS = ("lying", "side")


def fatal_misses(states, labels, unknown_limit=None):
    """가면 안 되는 구간에서 **게이트가 주행을 허용한** 프레임 수와 최대 연속 길이.

    ## `Standing` 만 세면 안 되는 이유

    `PostureGate` 는 `Unknown` 을 즉시 정지로 치지 않는다. 직전 판정을 유지하며
    **25프레임(≈1.7초)** 을 버틴다(`constants.py:42`, 옆으로 서기만 해도 Unknown 이
    나기 때문에 정상 추종 중 멈칫하지 않으려고 넣은 값이다).

    그래서 누움 구간에서 `Unknown` 20프레임은 "치명오판 0" 이 아니라 **로봇이
    1.3초 전진** 이다. 판정 문자열이 아니라 **실제 게이트를 돌려서** 센다.

    ## 최대 연속 길이도 낸다

    개수 30 이 "여기저기 한 프레임씩" 인지 "2초 내리 전진" 인지는 완전히 다른
    이야기인데, 합계만으로는 구별되지 않는다.
    """
    from follower_perception.posture_gate import PostureGate
    gate = PostureGate() if unknown_limit is None else PostureGate(unknown_limit)
    frames = run = max_run = 0
    for s, lab in zip(states, labels):
        allowed = gate.update(s)
        if lab in _MUST_STOP_LABELS and allowed:
            frames += 1
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return {"frames": frames, "max_run": max_run}


#: 이 판정이면 `lying` 라벨과 일치하는 것으로 본다 — `Side` 도 즉시 정지시키므로
#: 로봇 입장에서는 `Lying` 과 같은 결과다.
_LYING_MATCH_STATES = ("Lying", "Side")
#: 판단을 못 한 상태. 라벨과 무관하게 오답으로 세지만, "확신을 갖고 틀렸다"와는
#: 다른 실패라서 `accuracy` 와 별도로 `undecided_rate_*` 로 낸다.
_UNDECIDED_STATES = ("Unknown", "Calibrating")


def label_metrics(states, labels):
    """라벨 있는 프레임만 보고 정확도·재현율·미결정율을 낸다.

    `Unknown`/`Calibrating` 는 오답으로 세되 `undecided_rate_*` 로 따로 낸다 —
    누움 구간 대부분이 `Unknown` 인 데이터셋에서 낮은 `accuracy` 하나만 보면
    "모델이 틀렸다"로 읽히지만 실제로는 "판단을 못 했다"이다. 로봇 입장에서도
    다른 실패다: `Unknown` 은 `PostureGate` 의 25프레임 유예를 태우고 나서야
    서고, 확신을 갖고 틀린 판정(예: 누움을 `Standing` 으로)은 즉시 그 방향으로
    움직인다. 원인 진단도 다르므로 하나로 뭉개면 안 된다.

    `side` 라벨은 `Side` 판정만 정답으로 친다 — `Lying` 판정은 로봇을 세우긴
    하니 위험하지는 않지만 자세를 정확히 못 맞혔으므로 오답이다. 그래서
    `stop_agreement`(라벨이 `lying`/`side` 인 프레임 중 정지 판정(`Lying` 또는
    `Side` 어느 쪽이든)이 나온 비율)를 따로 낸다 — "자세를 정확히 맞췄나"와
    "로봇이 실제로 서긴 섰나"는 안전 관점에서 다른 질문이고, `stop_agreement`
    가 안전 쪽 숫자다.
    """
    buckets = {"standing": {"total": 0, "correct": 0, "undecided": 0},
               "lying": {"total": 0, "correct": 0, "undecided": 0},
               "side": {"total": 0, "correct": 0, "undecided": 0}}
    correct = total = 0
    stop_total = stop_hit = 0
    for s, lab in zip(states, labels):
        b = buckets.get(lab)          # None(무라벨) 또는 이 벤치가 안 쓰는 라벨은 건너뛴다
        if b is None:
            continue
        total += 1
        b["total"] += 1
        if lab in _MUST_STOP_LABELS:
            stop_total += 1
            if s in _LYING_MATCH_STATES:
                stop_hit += 1
        if s in _UNDECIDED_STATES:
            b["undecided"] += 1
            continue
        hit = ((lab == "lying" and s in _LYING_MATCH_STATES) or
               (lab == "standing" and s == "Standing") or
               (lab == "side" and s == "Side"))
        if hit:
            correct += 1
            b["correct"] += 1

    def _rate(n, d):
        return (n / d) if d else float("nan")

    return {
        "accuracy": _rate(correct, total),
        "stop_agreement": _rate(stop_hit, stop_total),
        "recall_standing": _rate(buckets["standing"]["correct"], buckets["standing"]["total"]),
        "recall_lying": _rate(buckets["lying"]["correct"], buckets["lying"]["total"]),
        "recall_side": _rate(buckets["side"]["correct"], buckets["side"]["total"]),
        "undecided_rate_standing": _rate(buckets["standing"]["undecided"], buckets["standing"]["total"]),
        "undecided_rate_lying": _rate(buckets["lying"]["undecided"], buckets["lying"]["total"]),
        "undecided_rate_side": _rate(buckets["side"]["undecided"], buckets["side"]["total"]),
    }


# ─────────────────────────── crop 캐시 ────────────────────────────────────

_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def crop_cache_path(video, rotate):
    """`reports/crops_<파일명>_rot<각도>.npz`.

    `.gitignore` 가 이 패턴(`reports/crops_*.npz`)을 이미 막아둔다 — 캐시는
    영상에서 언제든 다시 만들 수 있는 수십 MB 파생물이라 커밋 대상이 아니다.
    rotate 를 파일명에 넣는 이유: 안 넣으면 각도를 잘못 준 이전 실행의 캐시를
    그대로 재사용해 물구나무선 crop 을 조용히 먹는다.
    """
    base = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(_PKG, "reports", f"crops_{base}_rot{int(rotate)}.npz")


def build_crop_cache(video, rotate, out):
    """`video` 전체를 한 번 돌며 best.pt owner crop 을 `out`(.npz) 에 저장하고
    그 경로를 돌려준다.

    이미 있으면 그대로 재사용한다 — 모델을 바꿔가며 반복 실행하는 것이 이
    벤치의 핵심 사용 패턴이라, 매번 다시 검출하면 "모든 모델이 같은 crop 을
    본다"는 결정론이 실행마다 흔들릴 위험만 생기고 느리기만 하다.

    ⚠️ owner 선택을 단순화했다: 이 벤치 영상은 피규어 하나를 손에 들고 찍은
    1인 클립이라 ReID 등록이 필요 없다 — 매 프레임 최고 신뢰도 검출을 owner 로
    삼는다(`follower_perception.pipeline` 의 `TargetMatcher` 는 등록 기준 crop 이
    있어야 돌아가므로 이 용도엔 안 맞는다). 여러 사람이 나오는 영상엔 이 가정이
    깨진다 — ponytail: 다중 인물 벤치가 필요해지면 그때 TargetMatcher 등록을 붙인다.
    """
    if os.path.exists(out):
        return out

    det = Detector()
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise ValueError(f"영상을 열 수 없습니다: {video!r}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    rot = _ROTATE.get(int(rotate))

    frame_ids, bboxes, confs, crops = [], [], [], []
    total = 0
    frame_h = frame_w = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if rot is not None:
                frame = cv2.rotate(frame, rot)
            # bbox_guard(안전 판정)가 "프레임 경계에서 잘렸나"를 보려면 프레임
            # 크기가 있어야 한다 — 영상 전체가 같은 크기라 매 프레임 덮어써도 된다.
            frame_h, frame_w = frame.shape[:2]
            boxes = det.detect(frame)
            if boxes:
                owner = max(boxes, key=lambda b: b.confidence)
                x1, y1, x2, y2 = (int(round(v)) for v in owner.bbox)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(frame_w, x2), min(frame_h, y2)
                if x2 - x1 >= 2 and y2 - y1 >= 2:
                    frame_ids.append(total)
                    bboxes.append((x1, y1, x2, y2))
                    confs.append(owner.confidence)
                    crops.append(frame[y1:y2, x1:x2].copy())
            total += 1
    finally:
        cap.release()

    d = os.path.dirname(os.path.abspath(out)) or "."
    os.makedirs(d, exist_ok=True)
    # 임시파일 -> os.replace 로 원자적으로 쓴다(frame_tap.py 와 같은 관례) —
    # 검출에 수십 초가 걸리므로, 쓰다 만 캐시를 다음 실행이 "완성됨"으로
    # 오해하면 안 된다.
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".pose_bench_cache_", suffix=".npz.tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(
                fh,
                frame_idx=np.asarray(frame_ids, dtype=np.int64),
                bbox=np.asarray(bboxes, dtype=np.float64),
                conf=np.asarray(confs, dtype=np.float64),
                fps=np.float64(fps),
                total_frames=np.int64(total),
                frame_w=np.int64(frame_w),
                frame_h=np.int64(frame_h),
                **{f"crop_{i:06d}": c for i, c in enumerate(crops)},
            )
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return out


def _load_crop_cache(path):
    """`build_crop_cache` 가 쓴 파일을 읽는다.

    -> `(frame_idx, crops, bboxes, fps, total_frames, frame_w, frame_h)`.

    crop 마다 크기가 달라(bbox 크기가 프레임마다 다르다) 하나의 ndarray 에
    못 담는다. object dtype + pickle 대신 `crop_000000`, `crop_000001` ... 처럼
    이름을 나눠 저장한다 — `np.load` 에 `allow_pickle` 이 필요 없어진다.
    """
    with np.load(path) as z:
        frame_idx = z["frame_idx"]
        bboxes = z["bbox"]
        fps = float(z["fps"])
        total_frames = int(z["total_frames"])
        frame_w = int(z["frame_w"])
        frame_h = int(z["frame_h"])
        crops = [z[f"crop_{i:06d}"] for i in range(len(frame_idx))]
    return frame_idx, crops, bboxes, fps, total_frames, frame_w, frame_h


# ─────────────────────────── 모델 실행 ────────────────────────────────────

def _run_model(backend, crops):
    """crop 순서대로 추론해 (N,17,2)/(N,17) 시퀀스와 ms/frame 을 낸다.

    한 프레임 실패(`backend.infer` 가 None)는 통째로 빼지 않고 신뢰도 0 인
    17개 점으로 채운다 — `metrics()` 의 통과율 계산 입장에서는 "그 프레임엔
    판정이 없었다"와 "신뢰도가 미달이었다"가 같은 값(0)이어도 된다. 둘 다
    "이 프레임은 못 믿는다"라는 같은 뜻이기 때문이다.
    """
    xy_seq = np.zeros((len(crops), 17, 2), dtype=float)
    conf_seq = np.zeros((len(crops), 17), dtype=float)
    t0 = time.perf_counter()
    for i, crop in enumerate(crops):
        got = backend.infer(crop)
        if got is not None:
            xy_seq[i], conf_seq[i] = got
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    ms_per_frame = elapsed_ms / len(crops) if len(crops) else 0.0
    return xy_seq, conf_seq, ms_per_frame


def _posture_states(frame_idx, xy_seq, conf_seq, bbox_seq, total_frames, fps, frame_wh,
                     standing_segs, conf_min):
    """calib-then-classify 를 프레임 순서대로 재현해 원본 전체 길이의 상태열을 낸다.

    `(states, ref_bbox_hw)` 를 돌려준다.

    실제 등록 흐름과 같다: 지정된 standing 구간에서 기준 비율(`RatioCalibrator`)과
    기준 bbox 종횡비(`ref_bbox_hw`)를 **같이** 재고, 확정 전까지는 전부
    `Calibrating`(로봇이 등록 직후 멈춰 있는 것과 동일한 뜻)이다. "자동으로 서
    있는 구간을 찾아 기준을 잰다"가 순환이라 사람이 지정한 구간으로 대신한다
    (`video_segments.py` 머리말과 같은 이유).

    확정되면 그 뒤로는 `posture.classify_posture` 로 판정한다 — 알고리즘을
    복제하지 않는다(`pose_estimator.py` 머리말과 같은 원칙).

    ⚠️ **`bbox_wh`/`ref_bbox_hw`/`bbox_clipped` 를 반드시 같이 넘긴다.** 안
    넘기면 `posture.bbox_guard`(키포인트를 하나도 안 보고 내리는 안전 판정)가
    이 벤치 내내 꺼진 채로 돈다. 하필 키포인트가 죽는 프레임(누움·측면)일수록
    이 가드가 필요한데, 그 프레임들에서 판정 근거가 통째로 사라지는 셈이라
    결과가 전부 `Unknown` 으로 쏠린다 — `fatal_max_run` 이 모델과 무관하게
    라벨 구간 길이 그대로 나오는 식으로 드러난다(실측: 누움 구간의 90%+ 가
    `Unknown`).
    """
    posture = load_posture_module()
    calibrator = posture.RatioCalibrator()
    frame_w, frame_h = frame_wh
    hw_samples = []
    ref_bbox_hw = None

    pose_at = {int(f): (xy_seq[i], conf_seq[i], bbox_seq[i]) for i, f in enumerate(frame_idx)}
    states = []
    for idx in range(total_frames):
        pair = pose_at.get(idx)
        if pair is None:
            # crop 자체가 없다(검출 실패) — 판정 근거가 없으니 확정 전이면
            # Calibrating 을, 확정 후면 Unknown 을 보고한다.
            states.append(UNKNOWN if calibrator.done else CALIBRATING)
            continue
        xy, conf, bbox = pair
        x1, y1, x2, y2 = (float(v) for v in bbox)
        w, h = x2 - x1, y2 - y1
        clipped = bool(x1 <= 2 or y1 <= 2 or x2 >= frame_w - 2 or y2 >= frame_h - 2)
        if not calibrator.done:
            in_standing = any(frame_in_segment(idx, fps, seg) for seg in standing_segs)
            # 신뢰도 미달 좌표를 표본에 섞지 않는다 — 섞이면 기준 자체가 어긋나
            # 그 뒤 모든 판정이 같이 틀어진다(Task 8 배경의 캘리브 버그와 같은
            # 위험이라 여기서도 같은 가드를 건다). ref_bbox_hw 도 **같은 표본**
            # 에서 잰다 — 다른 조건으로 재면 두 기준이 서로 다른 순간을 가리키게 된다.
            if in_standing and all(conf[i] >= conf_min for i in TORSO):
                calibrator.update(posture.torso_ratio(xy))
                if w > 0:
                    hw_samples.append(h / w)
                if calibrator.done:
                    # 표본이 모자라면 기준 없이 둔다 — 근거 없는 기준으로 판정하는
                    # 것보다 가드를 끄는 편이 낫다(posture.bbox_guard 와 같은 원칙).
                    min_samples = posture.CALIBRATION_FRAMES // 3
                    ref_bbox_hw = (float(median(hw_samples))
                                    if len(hw_samples) >= min_samples else None)
            states.append(CALIBRATING)
        else:
            state, _angle = posture.classify_posture(
                xy, conf, ref_ratio=calibrator.reference,
                bbox_wh=(w, h), ref_bbox_hw=ref_bbox_hw, bbox_clipped=clipped)
            states.append(state)
    return states, ref_bbox_hw


# ─────────────────────────── 리포트 조립 ──────────────────────────────────

def _segments_by_label(items):
    """`["front=0:03-0:33", ...]` -> `{"front": [(3.0, 33.0), ...]}`."""
    out = {}
    for item in items or []:
        label, start, end = parse_labeled(item)
        out.setdefault(label, []).append((start, end))
    return out


def _label_track(total_frames, fps, standing_segs, lying_segs, side_segs):
    """원본 전체 프레임 각각에 정답 라벨을 단다.

    겹치면 `lying` 이 `side` 를 이기고, 그 `side` 는 `standing` 을 이긴다(적용
    순서: standing -> side -> lying, 뒤에 적용한 라벨이 덮어쓴다). `classify_posture`
    자체가 이 우선순위다 — `Side` 는 하류 소비자에게 "그래도 정상 추종 중"으로
    읽히므로, 누운 프레임이 `side` 로 잘못 기록되면 안 된다. 실제 라벨 구간은
    겹치지 않게 주지만, 사람이 실수로 겹쳐 줘도 조용히 위험한 쪽으로 판정이
    새지 않게 한다.
    """
    labels = [None] * total_frames
    for seg in standing_segs:
        for i in range(total_frames):
            if frame_in_segment(i, fps, seg):
                labels[i] = "standing"
    for seg in side_segs:
        for i in range(total_frames):
            if frame_in_segment(i, fps, seg):
                labels[i] = "side"
    for seg in lying_segs:
        for i in range(total_frames):
            if frame_in_segment(i, fps, seg):
                labels[i] = "lying"
    return labels


def _bench_camera(video, rotate, label, models, standing_by_label, lying_by_label,
                   side_by_label, conf_min, device):
    cache = build_crop_cache(video, rotate, crop_cache_path(video, rotate))
    frame_idx, crops, bboxes, fps, total_frames, frame_w, frame_h = _load_crop_cache(cache)
    standing_segs = standing_by_label.get(label, [])
    lying_segs = lying_by_label.get(label, [])
    side_segs = side_by_label.get(label, [])
    labels = _label_track(total_frames, fps, standing_segs, lying_segs, side_segs)

    cam = {"video": video, "rotate": rotate, "fps": fps, "frame_wh": [frame_w, frame_h],
           "pose_frames": len(frame_idx), "label_frames": total_frames, "models": {}}
    for name in models:
        try:
            backend = make_backend(name, device=device)
            xy_seq, conf_seq, ms_per_frame = _run_model(backend, crops)
        except Exception as e:      # noqa: BLE001 — 모델 하나가 죽어도 벤치 전체를 죽이면 안 된다
            print(f"[warn] {label}/{name}: {type(e).__name__}: {e}")
            continue
        m = metrics(xy_seq, conf_seq, conf_min)
        states, ref_bbox_hw = _posture_states(frame_idx, xy_seq, conf_seq, bboxes,
                                               total_frames, fps, (frame_w, frame_h),
                                               standing_segs, conf_min)
        fatal = fatal_misses(states, labels)
        m.update(label_metrics(states, labels))
        m["ref_bbox_hw"] = ref_bbox_hw          # 감사 가능하게: bbox_guard 가 실제로 쓴 기준값
        m["ms_per_frame"] = ms_per_frame
        m["fatal_frames"] = fatal["frames"]
        m["fatal_max_run"] = fatal["max_run"]
        cam["models"][name] = m
    return cam


#: undecided_rate 를 accuracy 옆에 나란히 둔다 — 이 지표의 요점이 "낮은 accuracy
#: 하나가 아니라 판단을 못 한 비율이 얼마인지 보이게 하는 것"이라서다(label_metrics
#: 문서 참고). stop_agreement 는 accuracy 바로 옆 — "자세를 정확히 맞췄나"와
#: "그래도 로봇은 섰나"를 나란히 봐야 안전 관점의 차이가 드러난다.
_MD_COLS = ("model", "torso4_pass", "jitter_torso4", "accuracy", "stop_agreement",
            "undecided_st", "undecided_ly", "undecided_sd",
            "recall_st", "recall_ly", "recall_sd",
            "fatal_frames", "fatal_max_run", "ms/frame")


def _render_markdown(report):
    lines = ["# pose 모델 벤치", "",
             "⚠️ 대조군은 비대칭 실험이다 — `rtmpose-m-body7` 승은 도메인 효과인지",
             "데이터 양 효과인지 구분할 수 없다(모듈 머리말 참고).", ""]
    for label, cam in report.items():
        lines.append(f"## {label} (`{os.path.basename(cam['video'])}`, "
                      f"rotate={cam['rotate']}, crop={cam['pose_frames']}/{cam['label_frames']})")
        lines.append("")
        lines.append("| " + " | ".join(_MD_COLS) + " |")
        lines.append("|" + "---|" * len(_MD_COLS))
        for name, m in cam["models"].items():
            lines.append(
                f"| {name} | {m['torso4_pass']:.3f} | {m['jitter_torso4']:.3f} | "
                f"{m['accuracy']:.3f} | {m['stop_agreement']:.3f} | "
                f"{m['undecided_rate_standing']:.3f} | {m['undecided_rate_lying']:.3f} | "
                f"{m['undecided_rate_side']:.3f} | {m['recall_standing']:.3f} | "
                f"{m['recall_lying']:.3f} | {m['recall_side']:.3f} | "
                f"{m['fatal_frames']} | {m['fatal_max_run']} | {m['ms_per_frame']:.1f} |")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────── CLI ──────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="여러 pose 모델을 같은 crop 에 돌려 비교한다.")
    p.add_argument("--video", action="append", default=[], required=True,
                    help="반복 가능. --rotate/--label 을 같은 순서로 하나씩 짝지어 준다")
    p.add_argument("--rotate", action="append", type=int, default=[],
                    choices=[0, 90, 180, 270], help="--video 와 1:1 순서 대응(앞캠은 180)")
    p.add_argument("--label", action="append", default=[],
                    help="캠 이름(front/back 등). --video 와 1:1 순서 대응")
    p.add_argument("--standing", action="append", default=[],
                    help="label=시작-끝. 캘리브 기준 구간(연속된 정면 직립 블록)")
    p.add_argument("--lying", action="append", default=[],
                    help="label=시작-끝. 반복 가능 — 누움은 보통 여러 짧은 구간으로 흩어져 있다")
    p.add_argument("--side", action="append", default=[],
                    help="label=시작-끝. 반복 가능 — 선택 사항. 측면 유지 구간이 없는 "
                         "영상이면 생략한다(recall_side/undecided_rate_side 가 NaN 으로 나온다)")
    p.add_argument("--models", nargs="+", default=sorted(MODELS), choices=sorted(MODELS))
    p.add_argument("--out", default=os.path.join("reports", "pose_bench"),
                    help="확장자 없이 준다 — <out>.json 과 <out>.md 를 같이 쓴다")
    p.add_argument("--conf-min", type=float, default=CONF_MIN)
    p.add_argument("--device", default="cpu")
    p.add_argument("--cache-only", action="store_true",
                    help="crop 캐시만 만들고 종료한다(모델을 돌리지 않는다) — 본 실행 전에 쓴다")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # ⚠️ `--conf-min` 은 이 파일의 지표와 라벨 게이트만 바꾼다. `posture.classify_posture`
    #    는 **모듈 상수**(`yolo_pose/posture.py:22`)를 직접 읽으므로, 여기서 덮어쓰지
    #    않으면 판정 내부는 계속 기본값(0.5)으로 돈다 — 통과율만 오르고 정확도는 안
    #    변하는 거짓 표가 나온다.
    load_posture_module().CONF_MIN = args.conf_min
    if not (len(args.video) == len(args.rotate) == len(args.label)):
        parser.error("--video/--rotate/--label 개수가 같아야 합니다 "
                      "(카메라 하나마다 세 개를 순서대로 짝지어 준다)")
    cams = list(zip(args.video, args.rotate, args.label))

    if args.cache_only:
        for video, rotate, label in cams:
            path = build_crop_cache(video, rotate, crop_cache_path(video, rotate))
            print(f"[cache] {label}: {path}")
        return 0

    standing_by_label = _segments_by_label(args.standing)
    lying_by_label = _segments_by_label(args.lying)
    side_by_label = _segments_by_label(args.side)

    report = {}
    for video, rotate, label in cams:
        print(f"[bench] {label}: {video} (rotate={rotate})")
        report[label] = _bench_camera(video, rotate, label, args.models,
                                       standing_by_label, lying_by_label, side_by_label,
                                       args.conf_min, args.device)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write(_render_markdown(report))
    print(f"[done] {args.out}.json / {args.out}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
