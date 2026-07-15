"""Rebuild the Pinky dataset split so adjacent video frames don't leak.

기존 pinky.v1i.yolov8 는 한 영상의 연속 프레임(frame_000001, 000003, ...)을
train/valid/test 로 *무작위* 분할해, 검증 이미지가 학습 이미지의 바로 옆
프레임(거의 복제본)이 되어 과적합 측정이 불가능했다.

이 스크립트는:
  1) 여러 roboflow export 폴더(촬영본)를 하나로 병합하고
  2) 각 촬영본 안에서 프레임 번호 순으로 정렬해 *연속 블록*으로 분할한다.
     (train = 앞쪽 구간, val = 가운데, test = 뒤쪽 구간)
  → 학습/검증이 시간적으로 분리되어 일반화 성능을 정직하게 측정할 수 있다.

사용법:
  python scripts/prepare_pinky_dataset.py \
      --sources pinky.v1i.yolov8 [다른_촬영본_폴더 ...] \
      --output pinky_dataset --val 0.2 --test 0.1
"""
from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")
FRAME_RE = re.compile(r"(\d+)")


@dataclass
class Sample:
    source: str
    order: int  # 촬영본 내 정렬 키 (프레임 번호)
    image: Path
    label: Path | None


def _frame_order(stem: str) -> int:
    m = FRAME_RE.search(stem)
    return int(m.group(1)) if m else 0


def collect(source: Path) -> list[Sample]:
    """source 폴더의 train/valid/test 모든 이미지+라벨을 하나로 모은다."""
    samples: list[Sample] = []
    for split in ("train", "valid", "test", "val"):
        img_dir = source / split / "images"
        lbl_dir = source / split / "labels"
        if not img_dir.is_dir():
            continue
        for img in sorted(img_dir.iterdir()):
            if img.suffix.lower() not in IMAGE_EXTS:
                continue
            label = lbl_dir / f"{img.stem}.txt"
            samples.append(
                Sample(
                    source=source.name,
                    order=_frame_order(img.stem),
                    image=img,
                    label=label if label.exists() else None,
                )
            )
    # 같은 stem 중복 제거(여러 split에 동일 프레임이 있을 경우)
    seen: dict[str, Sample] = {}
    for s in samples:
        seen.setdefault(s.image.stem, s)
    return sorted(seen.values(), key=lambda s: (s.source, s.order, s.image.stem))


def split_contiguous(
    samples: list[Sample], val_frac: float, test_frac: float
) -> dict[str, list[Sample]]:
    """촬영본별로 연속 블록을 잘라 train/val/test 에 배분(시간적 분리)."""
    out: dict[str, list[Sample]] = {"train": [], "valid": [], "test": []}
    by_source: dict[str, list[Sample]] = {}
    for s in samples:
        by_source.setdefault(s.source, []).append(s)

    for source, items in by_source.items():
        n = len(items)
        n_test = int(round(n * test_frac))
        n_val = int(round(n * val_frac))
        n_train = n - n_val - n_test
        if n_train <= 0:
            raise SystemExit(f"[{source}] 표본이 너무 적습니다(n={n}). 비율을 낮추세요.")
        out["train"].extend(items[:n_train])
        out["valid"].extend(items[n_train : n_train + n_val])
        out["test"].extend(items[n_train + n_val :])
    return out


def write_split(splits: dict[str, list[Sample]], output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    for split, items in splits.items():
        img_out = output / split / "images"
        lbl_out = output / split / "labels"
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)
        for s in items:
            # 촬영본 이름을 접두어로 붙여 다른 폴더 간 파일명 충돌 방지
            stem = f"{s.source}__{s.image.stem}"
            shutil.copy2(s.image, img_out / f"{stem}{s.image.suffix.lower()}")
            dst_label = lbl_out / f"{stem}.txt"
            if s.label is not None:
                shutil.copy2(s.label, dst_label)
            else:
                dst_label.touch()  # 배경 전용 이미지(라벨 없음)


def write_yaml(output: Path, names: list[str]) -> None:
    lines = [
        f"path: {output.resolve()}",
        "train: train/images",
        "val: valid/images",
        "test: test/images",
        "",
        f"nc: {len(names)}",
        f"names: {names}",
        "",
    ]
    (output / "data.yaml").write_text("\n".join(lines), encoding="utf-8")


def read_names(source: Path) -> list[str]:
    yaml = source / "data.yaml"
    if yaml.exists():
        for line in yaml.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("names:"):
                raw = line.split("names:", 1)[1].strip()
                return [n.strip(" '\"[]") for n in raw.strip("[]").split(",") if n.strip()]
    return ["human", "pinky_63"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Pinky 데이터셋 재분할(시간적 누수 방지)")
    ap.add_argument("--sources", nargs="+", default=["pinky.v1i.yolov8"],
                    help="병합할 roboflow export 폴더들 (backend 기준 상대경로 가능)")
    ap.add_argument("--output", default="pinky_dataset", help="출력 데이터셋 폴더")
    ap.add_argument("--val", type=float, default=0.2, help="검증 비율(촬영본별)")
    ap.add_argument("--test", type=float, default=0.1, help="테스트 비율(촬영본별)")
    args = ap.parse_args()

    sources = [ROOT / s if not Path(s).is_absolute() else Path(s) for s in args.sources]
    for s in sources:
        if not s.is_dir():
            raise SystemExit(f"폴더 없음: {s}")

    names = read_names(sources[0])
    all_samples: list[Sample] = []
    for s in sources:
        items = collect(s)
        print(f"[{s.name}] 수집: {len(items)}장")
        all_samples.extend(items)

    if len(sources) == 1:
        print("⚠️  촬영본이 1개뿐입니다. 연속 분할로 측정은 정직해지지만, "
              "실제 일반화를 위해선 다른 장면/조명의 촬영본을 추가하세요.")

    splits = split_contiguous(all_samples, args.val, args.test)
    output = ROOT / args.output if not Path(args.output).is_absolute() else Path(args.output)
    write_split(splits, output)
    write_yaml(output, names)

    print(f"\n생성됨: {output}")
    for split in ("train", "valid", "test"):
        print(f"  {split}: {len(splits[split])}장")
    print(f"  names: {names}")
    print(f"\n다음: python scripts/retrain_pinky_yolo.py --data {output}/data.yaml")


if __name__ == "__main__":
    main()
