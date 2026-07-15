"""Train the Pinky Pro detector and copy the best checkpoint into models/."""
from pathlib import Path
import shutil

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "pinky.v1i.yolov8" / "data.yaml"
OUTPUT = ROOT / "models" / "pinky_best.pt"


def main() -> None:
    model = YOLO("yolov8n.pt")
    result = model.train(
        data=str(DATA),
        epochs=50,
        imgsz=640,
        batch=8,
        device=0,
        project=str(ROOT / "runs"),
        name="pinky",
        exist_ok=True,
    )
    best = Path(result.save_dir) / "weights" / "best.pt"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, OUTPUT)
    print(f"saved: {OUTPUT}")


if __name__ == "__main__":
    main()
