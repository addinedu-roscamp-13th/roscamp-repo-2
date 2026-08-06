"""침입 이력에 **재생되는** 영상을 붙인다 — 데모용.

    .venv/bin/python scripts/seed_intrusion_clips.py

`seed_demo_data.py` 가 만든 침입 이력은 `clip_path` 가 비어 있어 관리자 화면에
"저장된 영상이 없습니다" 만 뜬다. 데모에서 「영상 보기」를 누르는 장면을 찍으려면
버튼이 있어야 한다.

## ⚠️ 버튼만 띄우지 않는다

`clip_path` 만 채우면 버튼은 생기지만 누르면 **404** 다(`security_clip` 이 파일 존재를
검사한다). 그건 데모 도중에 드러나는 종류의 거짓말이라, 여기서는 **ffmpeg 로 실제 mp4 를
만들어** 놓고 그 경로를 가리킨다. 눌러서 재생까지 된다.

## 계약 (ops_extra.py)

- 파일은 `SECURITY_MEDIA_DIR`(기본 `aba_service/backend/media/security`) 안에 둔다
- 파일명은 **uuid4 + `.mp4`** — 라우터가 정규식으로 검증한다(경로 탈출 방지)
- `clip_path` 는 `/api/admin/ops/security/clips/{name}` 형태여야 한다
  (`_CLIP_PATH_PREFIX` — 다른 형태는 `attach_clip` 이 거부한다)

멱등하다 — 이미 영상이 붙은 이력은 건너뛴다.
"""

import os
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import IntrusionEvent
from app.routers.ops_extra import _CLIP_PATH_PREFIX, SECURITY_MEDIA_DIR

#: 몇 건에 영상을 붙일까. 전부에 붙이면 "영상 없음" 경로가 화면에서 사라져,
#: 그 상태가 어떻게 보이는지 데모에서 못 보여 준다.
N_WITH_CLIP = 5
CLIP_SEC = 4


def make_clip(path) -> tuple[bool, str]:
    """야간 CCTV 처럼 보이는 짧은 mp4. `(성공, 사유)`.

    ⚠️ `drawtext` 를 쓰지 않는다 — 이 환경의 ffmpeg 은 **정적 빌드라 그 필터가 없다**
       (`No such filter: 'drawtext'`). 없는 필터를 쓰면 ffmpeg 이 통째로 실패하는데,
       그걸 "ffmpeg 이 없다"로 오해하기 쉬워 실제 사유를 그대로 돌려준다.
    """
    ff = shutil.which("ffmpeg")
    if ff is None:
        return False, "ffmpeg 을 찾을 수 없다"
    # 어두운 화면 + 노이즈 + 지나가는 형체. 실제 클립처럼 보이되 아무 실물도 안 담는다.
    cmd = [
        ff, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x0f1319:s=640x360:d={CLIP_SEC}:r=15",
        "-vf", ("noise=alls=14:allf=t+u,"
                "drawbox=x='80+120*t':y='140+40*sin(3*t)':w=70:h=120:"
                "color=0x38424f@0.9:t=fill,vignette"),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or "").strip().splitlines()[0] if r.stderr else "알 수 없는 실패"
    return True, ""


def main() -> int:
    SECURITY_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        todo = (db.query(IntrusionEvent)
                  .filter(IntrusionEvent.clip_path.is_(None))
                  .order_by(IntrusionEvent.detected_at.desc())
                  .limit(N_WITH_CLIP).all())
        if not todo:
            have = db.query(IntrusionEvent).filter(
                IntrusionEvent.clip_path.isnot(None)).count()
            print(f"영상이 붙은 이력이 이미 {have}건 — 그대로 둔다")
            return 0

        made = 0
        for e in todo:
            name = f"{uuid.uuid4()}.mp4"
            ok, why = make_clip(SECURITY_MEDIA_DIR / name)
            if not ok:
                print(f"⚠️ 영상을 못 만들었다 — clip_path 를 채우지 않는다"
                      f" (버튼만 생기고 누르면 404 가 되므로)\n   사유: {why}")
                return 1
            e.clip_path = f"{_CLIP_PATH_PREFIX}{name}"
            made += 1
            print(f"  + {e.detected_at:%m-%d %H:%M} {e.zone or '-'} → {name}")
        db.commit()
        total = db.query(IntrusionEvent).filter(
            IntrusionEvent.clip_path.isnot(None)).count()
        print(f"\n영상 {made}건 생성 · 영상 있는 이력 {total}건 "
              f"(나머지는 '영상 없음' 상태로 남겨 둔다)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
