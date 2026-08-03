"""오프라인 자세 판정 보정 — `pose_calib.json` 을 한 번 재서 파일로 굳힌다.

ArUco 마커가 알려진 기준으로 카메라를 한 번 재서 파일로 굳히듯, 이 스크립트는
**정면 직립 구간**(`--standing`)에서 관측한 비율을 기준(`ref_ratios`)으로 굳힌다.
런타임(`RatioCalibrator`)이 등록 직후 60프레임으로 매번 다시 재는 것과 같은 산식을,
녹화 영상 + 사람이 지정한 구간으로 미리 한 번 계산해 두는 것뿐이다.

## `--standing` 이 필수인 이유

자동 판별은 **순환**이다 — 서 있는지 알려면 기준 비율이 필요한데, 기준 비율을
재려고 서 있는 구간을 찾는 중이다. 게다가 기준을 누운 구간에서 재면 투영 단축
항이 죽고 화면 각도만 쓰는 것과 같아져 **조용히 틀린 채로** 돈다. 그래서 없으면
실행 자체를 거부한다(`build_parser` 의 `required=True`).

## `conf_min` 은 이 스크립트가 정하지 않는다

키포인트 신뢰도 임계는 안전에 직결된다(`pose_calib.py` 머리말과 같은 이유). 그래서
`summarise()` 는 항상 코드 상수 `CONF_MIN`(0.5)을 그대로 박고, 다른 임계를 뒀을 때
통과율이 어떻게 바뀌는지는 `threshold_report()` 로 **표로만** 남겨 사람이 읽게 한다.
"""
import argparse
import json
import math
import os
import sys
from statistics import median

import cv2
import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # follower_perception/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from follower_perception.pose_calib import CONF_MIN, DEFAULT_CALIB_NAME  # noqa: E402
from follower_perception.pose_estimator import load_posture_module       # noqa: E402
from scripts.perception_server import _draw_skeleton                     # noqa: E402
from scripts.pose_backends import MODELS, make_backend                   # noqa: E402
# ⚠️ build_crop_cache/crop_cache_path 는 공개 API. `_run_model`/`_load_crop_cache` 는
#   언더스코어지만 같은 패키지의 자매 스크립트다 — crop 당 추론 루프(실패 시 0 채움 등)와
#   캐시 로더를 여기서 다시 쓰면 두 스크립트의 정의가 갈릴 위험이 생긴다(Task 10 배경과
#   같은 이유로 "검출 패스를 중복하지 않는다"를 로더·추론 루프에도 적용한다).
from scripts.pose_bench import _load_crop_cache, _run_model, build_crop_cache, crop_cache_path  # noqa: E402
from scripts.video_segments import frame_in_segment, parse_segment       # noqa: E402

#: 자세 판정이 실제로 보는 4점(어깨 2 · 골반 2). pose_bench.py 의 같은 이름과
#: 값이 같다 — 두 모듈을 엮지 않으려고 각자 짧게 다시 적는다(이 코드베이스의
#: 기존 관례, `perception_server.py` 의 L_SH/R_SH/... 재선언과 같다).
TORSO = (5, 6, 11, 12)

#: threshold_report 가 훑는 후보 임계값. 0.5(코드 기본)를 반드시 포함한다.
_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)

#: 축 하나를 "쓸 수 있다"고 인정하는 최소 통과율. 미만이면 axis_priority 에서
#: 빼고 ref_ratios 를 None 으로 둔다 — 18%짜리 축을 우선순위에 넣으면 대부분의
#: 프레임에서 그 축을 고르고도 못 써서 UNKNOWN 으로 새는 것과 같다.
_AXIS_MIN_VIABLE = 0.5

#: side_factor 하한. posture.py 의 SIDE_FACTOR 문서화 한계와 같은 값 — 이보다
#: 낮추면 체형·배치 차이로 정면도 30%쯤 높게 나오는 게 정상이라 정면이 측면으로
#: 뒤집힌다.
_SIDE_FACTOR_FLOOR = 1.4
#: 관측된 최댓값 위에 얹는 여유(10%). 측정 구간에 없던 노이즈가 조금 더 커도
#: 정면을 측면으로 안 뒤집게 하는 헤드룸이다.
_SIDE_FACTOR_HEADROOM = 1.1


def threshold_report(conf_seq):
    """conf_min 후보별 torso4(어깨 2·골반 2) 동시 통과율.

    값을 고르지 않는다 — 사람이 표를 보고 고른다(`pose_calib.py`: "낮추면 판정
    커버리지가 조금 늘지만 저 신뢰도 좌표는 방향성 없는 난수라 양쪽으로 다
    틀린다").
    """
    cf = np.asarray(conf_seq, dtype=float)
    torso_min = cf[:, list(TORSO)].min(axis=1)
    return {f"{t:.1f}": float((torso_min >= t).mean()) for t in _THRESHOLDS}


def _side_factor(ref, samples):
    """관측된 최댓값 위로 여유를 둔 배수. 표본이 없으면 하한만 돌려준다."""
    if ref is None or ref <= 0 or not samples:
        return _SIDE_FACTOR_FLOOR
    return max(_SIDE_FACTOR_FLOOR, (max(samples) / ref) * _SIDE_FACTOR_HEADROOM)


def summarise(ratios, whrs_standing, whrs_lying=None, standing_frames=None):
    """정면 직립 구간 표본 -> 캘리브 JSON 본문(dict).

    `ratios` = `{축 이름: [그 축이 통과한 프레임의 torso_ratio, ...]}`.
    `standing_frames` 를 주면(실행 시 `main()` 이 준다) 축별 통과율을
    `len(표본)/standing_frames` 로 정확히 재 50% 미만인 축을 뺀다. 안 주면(이
    파일의 순수 산식 테스트들) "표본이 하나라도 있으면 그 축은 쓸 수 있다"로
    대신한다 — 분모를 모르는 채로 백분율을 지어내지 않기 위해서다.

    기준 비율은 **중앙값**이다. 상위 백분위나 최댓값을 쓰면 기준이 노이즈만큼
    부풀어 이후 거의 모든 프레임이 기준보다 낮게 나오고, 똑바로 서 있어도
    각도가 계속 부풀어 오른다(`RatioCalibrator` 와 같은 이유 — 알고리즘을
    복제하지 않으려고 같은 recipe 를 여기서도 그대로 쓴다).

    ⚠️ `whrs_standing`/`whrs_lying` 는 이 함수 안에서는 더 안 쓴다 — 예전엔
    절대 임계 `whr_lying_max` 를 냈지만, 카메라 높이·거리가 바뀌면 절대값은
    안 맞는다는 같은 이유로(`ref_ratio` 처럼) `ref_bbox_hw`(서있음 구간 bbox
    h/w 의 중앙값) + `bbox_lying_frac`(상대 비율) 조합으로 바뀌었다 —
    `main()` 이 그 두 값을 따로 계산한다. 매개변수는 남긴다 — 기존 호출부
    (테스트 다수)가 이미 이 인자로 부르고 있어, 시그니처를 요청 범위 밖에서
    바꾸지 않는다.
    """
    axis_pass_rate = {}
    clean = {}
    for axis, samples in ratios.items():
        # 방어적 필터 — 정상 호출자(main())는 이미 finite·양수만 넣지만, 축
        # 길이가 0(폭 0)이면 torso_ratio 가 inf 를 낸다는 사실은 여기서도 한 번
        # 더 막아 median 이 오염되지 않게 한다.
        c = [r for r in samples if math.isfinite(r) and r > 0]
        clean[axis] = c
        if standing_frames:
            axis_pass_rate[axis] = len(c) / standing_frames
        else:
            axis_pass_rate[axis] = 1.0 if c else 0.0

    axis_priority = [a for a in sorted(axis_pass_rate, key=lambda a: -axis_pass_rate[a])
                     if axis_pass_rate[a] >= _AXIS_MIN_VIABLE]
    ref_ratios = {a: (median(clean[a]) if a in axis_priority else None) for a in ratios}

    primary = axis_priority[0] if axis_priority else None
    side_factor = _side_factor(ref_ratios.get(primary), clean.get(primary, []))

    return {
        "conf_min": CONF_MIN,
        "axis_priority": axis_priority,
        "ref_ratios": ref_ratios,
        "side_factor": float(side_factor),
        "filter": None,
        "report": {"axis_pass_rate": axis_pass_rate},
    }


def build_parser():
    p = argparse.ArgumentParser(
        description="정면 직립 구간에서 자세 판정 기준값을 재 pose_calib.json 으로 굳힌다.")
    p.add_argument("--video", required=True, help="캘리브에 쓸 영상 파일")
    p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="앞캠은 180(image-sender.sh 와 같은 값)")
    p.add_argument("--model", required=True, choices=sorted(MODELS),
                    help="기준값은 모델마다 달라진다 — 모델을 바꾸면 다시 돌려야 한다")
    # action="append": 실제 영상은 정면 직립이 한 덩어리가 아니라 여러 토막으로
    # 흩어져 있다(reports/segments_draft.md — 한 캠에 최대 4토막). label= 접두는
    # 안 붙인다 — pose_bench.py 와 달리 이 스크립트는 캠 하나만 다뤄서 label 이
    # 잡음이다.
    p.add_argument("--standing", action="append", default=[], required=True,
                    help="시작-끝(예: 0:03-0:33). 반복 가능 — 정면 직립 토막마다 하나. 최소 1개 필수")
    p.add_argument("--lying", action="append", default=[],
                    help="시작-끝. 반복 가능 — 누움은 보통 여러 짧은 구간으로 흩어져 있다")
    p.add_argument("--device", default="cpu")
    p.add_argument(
        "--out", default=os.path.join(_PKG, os.path.splitext(DEFAULT_CALIB_NAME)[0]),
        help="확장자 없이 준다 — <out>.json 과 <out>_check_{1..4}.png 를 같이 쓴다")
    return p


# ─────────────────────────── main() 전용 헬퍼 ──────────────────────────────
# 아래는 video_segments/pose_bench/posture 모듈에서 실제 프레임을 읽어야 하므로
# summarise/threshold_report/build_parser 와 달리 영상·모델이 있어야 돈다.
# (브리프 요구사항: 테스트는 위 세 함수만 본다 — 이 아래는 테스트 대상이 아니다.)

def _bbox_hw(bbox_row):
    x1, y1, x2, y2 = bbox_row
    w, h = float(x2 - x1), float(y2 - y1)
    return h / w if w > 1e-6 else None


def _collect_samples(posture, frame_idx, bbox, xy_seq, conf_seq, fps, standing_segs, lying_segs):
    """구간(들)에 걸친 프레임을 훑어 축별 ratio 표본과 bbox h/w 표본을 모은다.

    구간이 여러 개일 수 있다 — 실제 영상은 "서 있음 30초" 처럼 한 덩어리가
    아니라 정면 직립 토막 사이사이에 다른 자세가 섞여 있다
    (reports/segments_draft.md). 그래서 프레임마다 "어느 standing 구간에든
    속하는가"를 묻고 표본을 전부 같은 리스트에 모은다 — `pose_bench.py` 의
    `_posture_states`/`_label_track` 가 이미 쓰는
    `any(frame_in_segment(...) for seg in segs)` 와 같은 패턴이다.
    """
    axes = (posture.AXIS_TORSO, posture.AXIS_HEAD_HIP, posture.AXIS_SHOULDER_KNEE)
    ratios = {a: [] for a in axes}
    whrs_standing, whrs_lying = [], []
    standing_frames = 0

    for i, f in enumerate(frame_idx):
        xy, conf = xy_seq[i], conf_seq[i]
        hw = _bbox_hw(bbox[i])
        if any(frame_in_segment(int(f), fps, seg) for seg in standing_segs):
            standing_frames += 1
            for axis in axes:
                if posture.axis_points(xy, conf, axis) is not None:
                    r = posture.torso_ratio(xy, conf, axis=axis)
                    if math.isfinite(r) and r > 0:
                        ratios[axis].append(r)
            if hw is not None:
                whrs_standing.append(hw)
        if any(frame_in_segment(int(f), fps, seg) for seg in lying_segs) and hw is not None:
            whrs_lying.append(hw)

    return ratios, whrs_standing, whrs_lying, standing_frames


def _bbox_lying_stats(whrs_standing, whrs_lying, ref_bbox_hw, frac):
    """bbox_lying_frac 을 이 데이터에 적용했을 때의 recall/오탐률 — 진단용.

    `bbox_lying_frac` 자체는 사람이 이미 실측(91% recall, FP 0%, 앞캠)으로
    검증해 고정한 값이다(Task 11 추가 요구사항). 이 함수는 그 값을 *이번
    실행의 데이터*에 대보고 report 에 실제 숫자를 남기기 위한 것으로, 값을
    새로 정하지 않는다.
    """
    if not ref_bbox_hw or not whrs_lying:
        return None
    threshold = ref_bbox_hw * frac
    recall = float(np.mean([w < threshold for w in whrs_lying]))
    fpr = float(np.mean([w < threshold for w in whrs_standing])) if whrs_standing else None
    return {"threshold": threshold, "lying_recall": recall,
            "standing_false_positive_rate": fpr}


#: bbox_side_frac 분리 미검증 사유 — 이 스크립트는 `--side` 라벨 구간을 받지
#: 않는다(측면은 직립 구간 안에 연속으로 섞여 있어 경계를 프레임 단위로 못
#: 긋는다, segments_draft.md 한계 ②와 같은 이유). 그래서 분리를 보여줄 데이터
#: 자체가 없다 — 항상 꺼 둔다. 실측(task-11 추가 요구사항): 이 피사체는
#: 팔·다리 자세가 bbox 폭을 지배해 몸을 돌려도 폭이 거의 안 줄기 때문에(사람은
#: 어깨 폭이 지배) 앞캠에서 임계가 한 번도 안 걸리고 뒷캠에서는 오탐만 냈다.
_BBOX_SIDE_FRAC_NOTE = (
    "bbox_side_frac 은 항상 null 이다 — 이 스크립트는 side 라벨 구간을 받지 않아 "
    "분리를 보여줄 데이터가 없다. 실측(앞캠/뒷캠)에서도 피규어는 팔다리 자세가 "
    "bbox 폭을 지배해 몸을 돌려도 폭이 거의 안 줄었다(사람이면 어깨 폭이 지배) — "
    "임계가 한 번도 안 걸리거나 오탐만 냈다. 분리가 보이는 피사체로 바뀌기 전엔 켜지 않는다."
)

#: bbox_guard 의 lying 임계 배수. Task 1 코드 기본값(BBOX_LYING_FRAC)과 같은
#: 값 — 이 스크립트는 이 값을 데이터로 다시 정하지 않는다(고정), `_bbox_lying_stats`
#: 로 이번 데이터에 대본 결과만 report 에 남긴다.
_BBOX_LYING_FRAC = 0.45


def _sanity_frames(frame_idx, fps, segments, n=4):
    """구간(들)을 합쳐 프레임 개수 기준 4등분한 자리의 프레임을 고른다.

    구간이 여러 토막이어도 "몇 번째 초인가"가 아니라 "합친 프레임 목록에서
    몇 번째인가"로 나누면 토막마다 따로 다룰 필요가 없다 — 시간 구간 하나를
    4등분하던 것과 같은 산식을 그대로 프레임 인덱스에 적용한 것뿐이다.
    """
    in_seg = [i for i, f in enumerate(frame_idx)
              if any(frame_in_segment(int(f), fps, seg) for seg in segments)]
    if not in_seg:
        return []
    picks = []
    for k in range(n):
        pos = min(int((k + 0.5) / n * len(in_seg)), len(in_seg) - 1)
        picks.append(in_seg[pos])
    return picks


def _save_sanity_pngs(frame_idx, crops, xy_seq, conf_seq, fps, segments, out_base, axis):
    """구간(들)을 4등분한 자리의 프레임에 스켈레톤을 얹어 저장한다 — 사람이 1초에 확인한다."""
    paths = []
    for n, i in enumerate(_sanity_frames(frame_idx, fps, segments), start=1):
        vis = crops[i].copy()
        _draw_skeleton(vis, (xy_seq[i], conf_seq[i], (0, 0)), CONF_MIN, (0, 255, 0), axis=axis)
        path = f"{out_base}_check_{n}.png"
        cv2.imwrite(path, vis)
        paths.append(path)
        print(f"[check] {path}")
    return paths


def main(argv=None):
    args = build_parser().parse_args(argv)
    standing_segs = [parse_segment(s) for s in args.standing]
    lying_segs = [parse_segment(s) for s in args.lying]

    cache = build_crop_cache(args.video, args.rotate, crop_cache_path(args.video, args.rotate))
    frame_idx, crops, bbox, fps, _total_frames, _frame_w, _frame_h = _load_crop_cache(cache)
    if len(frame_idx) == 0:
        print("[error] crop 캐시가 비어 있습니다 — 검출된 프레임이 없습니다")
        return 1

    backend = make_backend(args.model, device=args.device)
    xy_seq, conf_seq, _ms = _run_model(backend, crops)

    posture = load_posture_module()
    ratios, whrs_standing, whrs_lying, standing_frames = _collect_samples(
        posture, frame_idx, bbox, xy_seq, conf_seq, fps, standing_segs, lying_segs)

    if standing_frames == 0 or not whrs_standing:
        print(f"[error] --standing {args.standing!r} 구간에서 검출된 프레임이 없습니다 "
              f"— 캘리브를 거부합니다(근거 없는 기준을 굳히지 않는다)")
        return 1

    result = summarise(ratios, whrs_standing, whrs_lying or None,
                        standing_frames=standing_frames)

    # bbox 가드 필드(Task 11 추가 요구사항) — ref_bbox_hw 는 RatioCalibrator 와 같은
    # 최소 표본 수(CALIBRATION_FRAMES // 3) 미만이면 None 으로 둔다(Task 8 의
    # "근거 없는 기준으로 판정하느니 그 신호를 안 쓴다"와 같은 정책).
    min_bbox_samples = posture.CALIBRATION_FRAMES // 3
    ref_bbox_hw = float(median(whrs_standing)) if len(whrs_standing) >= min_bbox_samples else None
    result["ref_bbox_hw"] = ref_bbox_hw
    result["bbox_lying_frac"] = _BBOX_LYING_FRAC
    result["bbox_side_frac"] = None
    result["source"] = os.path.basename(args.video)
    result["report"]["conf_threshold_pass_rate"] = threshold_report(conf_seq)
    result["report"]["bbox_side_frac_note"] = _BBOX_SIDE_FRAC_NOTE
    lying_stats = _bbox_lying_stats(whrs_standing, whrs_lying, ref_bbox_hw, _BBOX_LYING_FRAC)
    if lying_stats:
        result["report"]["bbox_lying_frac_check"] = lying_stats

    out_json = args.out if args.out.endswith(".json") else args.out + ".json"
    out_dir = os.path.dirname(os.path.abspath(out_json))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"[done] {out_json}")

    primary_axis = result["axis_priority"][0] if result["axis_priority"] else "torso"
    out_base = os.path.splitext(out_json)[0]
    _save_sanity_pngs(frame_idx, crops, xy_seq, conf_seq, fps, standing_segs, out_base,
                       primary_axis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
