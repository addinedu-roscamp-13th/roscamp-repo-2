"""owner 견고성 측정 — 같은 사람 쌍의 ReID·HSV 유사도 분포.

⚠️ **이 스크립트는 사칭자 판별을 재지 못한다.** `reports/crops_*.npz` 는 1인 클립에서
뽑은 것이라(`pose_bench.py:221-233`) 비-owner 표본이 없다. 여기서 나오는 것은
"같은 사람을 얼마나 안정적으로 붙잡는가"이고, "남이 통과하는가"가 아니다.
그 질문에 답하려면 다른 사람(가능하면 비슷한 색 옷)의 crop 을 라벨링해 넣어야 한다.

운영과 **같은 백엔드·같은 전처리**를 쓴다. 여기서 갈라지면 숫자가 거짓말이 된다.
"""
import argparse
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from follower_perception.color_hist import hist_similarity, hsv_hist   # noqa: E402
from follower_perception.constants import HSV_THRESHOLD, REID_THRESHOLD  # noqa: E402
from follower_perception.reid_engine import ReIDEngine                  # noqa: E402


def load_crops(npz_path, stride):
    """시간적으로 떨어진 crop 들만 고른다.

    이웃 프레임끼리 비교하면 "거의 같은 그림"이라 유사도가 당연히 높게 나온다.
    그건 견고성이 아니라 프레임 중복을 재는 것이다.
    """
    data = np.load(npz_path, allow_pickle=True)
    keys = sorted(k for k in data.files if k.startswith("crop_"))
    return [data[k] for k in keys[::stride]]


def pairwise(crops, reid):
    reid_vecs = [reid.extract(c) for c in crops]
    hsv_vecs = [hsv_hist(c) for c in crops]
    rs, hs = [], []
    for i, j in itertools.combinations(range(len(crops)), 2):
        rs.append(reid.similarity(reid_vecs[i], reid_vecs[j]))
        hs.append(hist_similarity(hsv_vecs[i], hsv_vecs[j]))
    return np.array(rs), np.array(hs)


def report(name, rs, hs):
    joint = ((rs >= REID_THRESHOLD) & (hs >= HSV_THRESHOLD)).mean()
    print(f"\n## {name}  (쌍 {len(rs)}개)")
    print(f"| 지표 | p5 | 중앙 | p95 |")
    print(f"|---|---|---|---|")
    for label, arr in (("ReID cosine", rs), ("HSV similarity", hs)):
        p5, med, p95 = np.percentile(arr, [5, 50, 95])
        print(f"| {label} | {p5:.3f} | {med:.3f} | {p95:.3f} |")
    print(f"\n- ReID 게이트({REID_THRESHOLD}) 통과율: {(rs >= REID_THRESHOLD).mean():.1%}")
    print(f"- HSV 게이트({HSV_THRESHOLD}) 통과율: {(hs >= HSV_THRESHOLD).mean():.1%}")
    print(f"- **동시 통과율: {joint:.1%}**  ← 같은 사람인데 이게 낮으면 추종이 자주 끊긴다")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--npz", action="append", required=True,
                   help="crop 캐시 (.npz). 여러 개 주면 각각 따로 보고한다")
    p.add_argument("--stride", type=int, default=20,
                   help="이 간격으로만 crop 을 고른다 — 이웃 프레임 중복을 피한다")
    args = p.parse_args()

    reid = ReIDEngine()
    backend = getattr(reid, "_backend", "?")
    print(f"# owner 견고성 측정\n")
    print(f"- ReID 백엔드: **{backend}** (feat_dim={reid.feat_dim})")
    print(f"- crop 간격: {args.stride}프레임")
    print(f"\n⚠️ **사칭자 판별은 못 쟀다.** 캐시가 1인 클립이라 비-owner 표본이 없다.")

    for path in args.npz:
        crops = load_crops(path, args.stride)
        rs, hs = pairwise(crops, reid)
        report(os.path.basename(path), rs, hs)


if __name__ == "__main__":
    main()
