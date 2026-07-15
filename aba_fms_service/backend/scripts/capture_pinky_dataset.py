"""Capture frames from the robot-arm camera for YOLO labeling.

현재 pinky_best.pt 는 핑키 로봇이 바닥에서 찍은 영상으로 학습돼, 실제 로봇팔
카메라 시점(192.168.0.70)에서는 아무것도 못 잡는다. 이 스크립트는 그 카메라
스냅샷을 일정 간격으로 받아 라벨링용 이미지 폴더에 순번으로 저장한다.

촬영 팁: 핑키봇과 사람을 여러 각도/거리/조명/회전으로 움직이며 모을 것.

사용법:
  python scripts/capture_pinky_dataset.py --robot 192.168.0.70 --count 200 --interval 0.5
  # 저장: captures/<타임스탬프>/000001.jpg ...
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.security import create_access_token  # noqa: E402


def snapshot(robot: str, token: str, timeout: float) -> bytes | None:
    url = f"http://{robot}:9001/api/robot/camera/snapshot?token={token}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  스냅샷 실패: {exc}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="로봇팔 카메라 데이터 캡처")
    ap.add_argument("--robot", default="192.168.0.70", help="로봇 IP")
    ap.add_argument("--count", type=int, default=200, help="저장할 이미지 수")
    ap.add_argument("--interval", type=float, default=0.5, help="캡처 간격(초)")
    ap.add_argument("--out", default=None, help="저장 폴더(기본: captures/<타임스탬프>)")
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()

    out_dir = (
        Path(args.out)
        if args.out
        else ROOT / "captures" / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    token = create_access_token("admin")
    print(f"로봇: {args.robot}  목표: {args.count}장  간격: {args.interval}s")
    print(f"저장: {out_dir}\nCtrl+C 로 중단 가능\n")

    saved = 0
    miss = 0
    try:
        while saved < args.count:
            data = snapshot(args.robot, token, args.timeout)
            if data:
                path = out_dir / f"{saved + 1:06d}.jpg"
                path.write_bytes(data)
                saved += 1
                miss = 0
                if saved % 10 == 0 or saved == 1:
                    print(f"  {saved}/{args.count} 저장됨 ({len(data)} bytes)")
            else:
                miss += 1
                if miss >= 5:
                    print("연속 5회 실패 — 로봇 IP/카메라 상태를 확인하세요. 중단.")
                    break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n사용자 중단")

    print(f"\n완료: {saved}장 → {out_dir}")
    print("다음 단계:")
    print("  1) Roboflow 등에 업로드해 human / pinky_63 라벨링")
    print("  2) export 후 prepare_pinky_dataset.py → retrain_pinky_yolo.py")


if __name__ == "__main__":
    main()
