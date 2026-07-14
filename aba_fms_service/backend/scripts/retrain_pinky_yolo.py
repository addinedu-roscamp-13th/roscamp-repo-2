"""Retrain the Pinky Pro detector with anti-overfitting settings.

기존 train_pinky_yolo.py 대비 개선점:
  - 데이터 증강(flip/scale/hsv/mosaic 등)으로 과적합 완화
  - patience 기반 조기 종료 (검증 성능이 정체되면 중단)
  - 학습 후 test 셋 자동 평가
  - 기존 models/pinky_best.pt 를 타임스탬프로 백업한 뒤 교체(롤백 가능)

권장 흐름:
  1) python scripts/prepare_pinky_dataset.py --sources pinky.v1i.yolov8 ...
  2) python scripts/retrain_pinky_yolo.py --data pinky_dataset/data.yaml

기본 --data 는 재분할된 pinky_dataset 을 가리킨다. 없으면 안내 메시지를 출력한다.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "pinky_dataset" / "data.yaml"
OUTPUT = ROOT / "models" / "pinky_best.pt"


def backup_existing() -> None:
    if OUTPUT.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = OUTPUT.with_name(f"pinky_best.{stamp}.bak.pt")
        shutil.copy2(OUTPUT, backup)
        print(f"기존 모델 백업: {backup.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Pinky YOLO 재학습")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="data.yaml 경로")
    ap.add_argument("--base", default="yolov8n.pt", help="시작 가중치(전이학습)")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--patience", type=int, default=25, help="조기 종료 인내 epoch")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0", help="GPU 인덱스 또는 'cpu'")
    ap.add_argument("--name", default="pinky_v2")
    ap.add_argument("--no-swap", action="store_true",
                    help="best.pt 를 models/ 로 복사하지 않음(평가만)")
    args = ap.parse_args()

    data = Path(args.data)
    if not data.exists():
        raise SystemExit(
            f"data.yaml 없음: {data}\n"
            "먼저 재분할하세요:\n"
            "  python scripts/prepare_pinky_dataset.py --sources pinky.v1i.yolov8"
        )

    model = YOLO(args.base)
    result = model.train(
        data=str(data),
        epochs=args.epochs,
        patience=args.patience,         # 조기 종료
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(ROOT / "runs"),
        name=args.name,
        exist_ok=True,
        # --- 과적합 완화용 증강 ---
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,   # 색/밝기 변화
        degrees=10.0, translate=0.1, scale=0.5, shear=2.0,
        fliplr=0.5, flipud=0.0,
        mosaic=1.0, mixup=0.1, close_mosaic=10,
        # --- 정규화 ---
        weight_decay=0.0005, dropout=0.1,
    )

    save_dir = Path(result.save_dir)
    best = save_dir / "weights" / "best.pt"
    print(f"\n학습 완료: {best}")

    # test 셋 평가(있을 때만)
    print("\n=== test 셋 평가 ===")
    try:
        metrics = YOLO(str(best)).val(data=str(data), split="test", device=args.device)
        print(f"mAP50: {metrics.box.map50:.3f}  mAP50-95: {metrics.box.map:.3f}")
    except Exception as exc:  # noqa: BLE001
        print(f"평가 건너뜀: {exc}")

    if args.no_swap:
        print("\n--no-swap: models/ 교체 생략. 결과는 runs/ 에서 확인하세요.")
        return

    backup_existing()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, OUTPUT)
    print(f"\n배포됨: {OUTPUT}")
    print("백엔드 재시작 후 핑키프로 인식 페이지에서 확인하세요.")


if __name__ == "__main__":
    main()
