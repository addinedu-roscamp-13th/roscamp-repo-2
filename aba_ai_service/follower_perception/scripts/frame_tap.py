"""생프레임 로컬 탭 — 같은 Pi 안의 다른 프로세스가 카메라 장치를 열지 않고 프레임을 얻는다.

## 왜 필요한가

Pi 에서 카메라 장치를 두 프로세스가 열면 앞캠이 `Device or resource busy` 로 죽는다
(`scripts/all/libi_pi.sh` 머리말에 기록된 실제 사고: 뒷캠을 `/dev/video0` 으로 잡았더니
앞캠 libcamera 가 장치를 못 잡고 죽었고, 죽는 건 앞캠 창이라 원인이 안 보였다).

그래서 장치는 `camera_sender` **하나만** 열고, 프레임이 필요한 쪽(마커 도킹 등)은
여기서 읽는다.

## 계약

- 슬롯은 `"front"`, `"back"` 두 개다. **`camera_select` 와 무관하게 둘 다 항상 기록한다.**
  소비자가 원하는 쪽을 직접 고르므로, 소비자가 `camera_select` 를 건드릴 필요가 없다
  (그 토픽의 발행자는 `follow_node` 하나뿐이라는 규칙이 유지된다).
- JPEG 이 아니라 **생프레임(BGR)** 이다. 마커 코너 추정에 재압축 손실을 주지 않는다.
- 쓰기는 임시파일 → `os.replace` 로 **원자적**이다. 읽는 쪽이 반쯤 쓰인 프레임을 보지
  않으므로 락이 필요 없다.
- **stale 판정은 소비자 몫이다.** 여기서는 `stamp`(`time.monotonic`)만 붙인다.
  "몇 초까지 신선한가"는 쓰는 쪽이 아니라 쓰는 용도가 정한다.
"""
import os
import tempfile

import numpy as np

SLOTS = ("front", "back")

#: 기본은 tmpfs(/dev/shm) — 디스크에 안 쓴다. 테스트는 이 환경변수로 옮긴다.
_DIR_ENV = "LIBI_CAM_TAP_DIR"
_DEFAULT_DIR = "/dev/shm"


def tap_dir() -> str:
    return os.environ.get(_DIR_ENV, _DEFAULT_DIR)


def path(slot: str) -> str:
    if slot not in SLOTS:
        raise ValueError(f"알 수 없는 슬롯: {slot!r} (가능: {SLOTS})")
    return os.path.join(tap_dir(), f"libi_cam_{slot}.npz")


def write(slot: str, frame, seq: int, stamp: float) -> None:
    """슬롯에 생프레임을 원자적으로 덮어쓴다."""
    target = path(slot)
    d = tap_dir()
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".libi_cam_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, frame=np.asarray(frame),
                     seq=np.int64(seq), stamp=np.float64(stamp))
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read(slot: str):
    """`(frame, seq, stamp)` 또는 None. 아직 없거나 쓰는 중이면 None 이다."""
    p = path(slot)
    if not os.path.exists(p):
        return None
    try:
        with np.load(p) as z:
            return z["frame"], int(z["seq"]), float(z["stamp"])
    except (OSError, ValueError, KeyError, EOFError):
        # 교체 직전의 순간을 잡았을 수 있다. 다음 tick 에 다시 읽으면 된다 —
        # 여기서 예외를 올리면 소비자의 제어 루프가 죽는다.
        return None


def cleanup() -> None:
    """송출기 종료 시 호출. 남은 슬롯이 stale 한 채로 보이지 않게 한다."""
    for s in SLOTS:
        try:
            os.unlink(path(s))
        except FileNotFoundError:
            pass
        except OSError:
            pass
