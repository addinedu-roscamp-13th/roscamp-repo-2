#!/usr/bin/env python3
"""노트북에서 실행 — Pi 스트림을 보면서 체커보드를 수집하고 intrinsic 을 계산한다.

로봇(Pi):   python3 calib_stream_pi.py --source picam
노트북:     python3 calib_client.py --host 172.30.1.83 --square-m 0.0382 \
                --out <repo>/aba_controller/libi_drive_controller/robot_agent/config/camera_calib.npz

키:
    SPACE  현재 프레임 수집        a  자동수집 on/off (1초 간격 + 이전과 다른 자세일 때만)
    u      마지막 수집 취소        c  계산 + 저장
    q      저장 없이 종료

화면 우상단 3x3 격자는 보드 중심이 지나간 칸이다. **가장자리 칸을 안 채우면 왜곡계수가
0 근처로 나온다** — 중앙에서만 찍는 것이 이 작업의 가장 흔한 실패다.

⚠️ 이 결과는 스트림 해상도 전용이다. 런타임 해상도가 바뀌면 K 는 무효다.
"""
import argparse
import pathlib
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

_FONT = cv2.FONT_HERSHEY_SIMPLEX


class MjpegReader:
    """multipart 헤더는 무시하고 JPEG SOI/EOI 로만 자른다(경계 문자열 파싱보다 견고)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.frame: np.ndarray | None = None
        self.err: str | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            r = urllib.request.urlopen(self.url, timeout=10)
        except Exception as e:
            self.err = f"스트림 접속 실패: {e}\n  로봇에서 calib_stream_pi.py 가 떠 있는지 확인하세요."
            return
        if r.status != 200:
            self.err = f"스트림 오류 {r.status}: {r.read(500).decode(errors='replace')}"
            return
        buf = b""
        while True:
            chunk = r.read(16384)
            if not chunk:
                self.err = "스트림이 끊겼습니다 (로봇 측 종료?)"
                return
            buf += chunk
            while True:
                s = buf.find(b"\xff\xd8")
                if s < 0:
                    buf = b""
                    break
                e = buf.find(b"\xff\xd9", s + 2)
                if e < 0:
                    buf = buf[s:] if s > 0 else buf
                    break
                img = cv2.imdecode(np.frombuffer(buf[s:e + 2], np.uint8), cv2.IMREAD_COLOR)
                buf = buf[e + 2:]
                if img is not None:
                    with self._lock:
                        self.frame = img

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self.frame is None else self.frame.copy()


def guess_pattern(gray: np.ndarray):
    """내부 코너 개수를 자동으로 알아낸다.

    사람이 세면 자주 틀리고(칸 개수와 코너 개수를 헷갈린다), 틀린 채로 두면 검출이 안 되거나
    더 나쁘게는 '부분 격자'가 잡혀 조용히 틀린 캘리브가 나온다. 그래서 큰 패턴부터 훑어
    가장 큰 것을 채택한다(작은 것부터 찾으면 부분격자에 먼저 걸린다)."""
    # 한 번 훑는 데 수 초가 걸리므로 축소본으로 찾는다(개수만 알면 되고, 정밀도는
    # 확정 후 원본에서 find_corners 가 다시 낸다).
    scale = 320.0 / gray.shape[1]
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cands = []
    for c in range(3, 11):
        for r in range(3, 11):
            if r <= c:
                cands.append((c, r))
    cands.sort(key=lambda p: -(p[0] * p[1]))
    for pat in cands:
        for p in ({pat, (pat[1], pat[0])} if pat[0] != pat[1] else {pat}):
            ok, _ = cv2.findChessboardCorners(
                gray, p, flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
                | cv2.CALIB_CB_FAST_CHECK)
            if ok:
                return p
    return None


def find_corners(gray: np.ndarray, pattern: tuple[int, int]):
    """SB 검출기가 있으면 그걸 쓴다(저해상도·부분가림에 더 강하고 서브픽셀이 내장)."""
    if hasattr(cv2, "findChessboardCornersSB"):
        ok, c = cv2.findChessboardCornersSB(
            gray, pattern, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
        if ok:
            return True, c
    ok, c = cv2.findChessboardCorners(
        gray, pattern,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK)
    if not ok:
        return False, None
    c = cv2.cornerSubPix(gray, c, (11, 11), (-1, -1),
                         (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
    return True, c


def calibrate_and_report(obj_pts, img_pts, size, out_path: str, meta: dict) -> bool:
    if len(obj_pts) < 8:
        print(f"[!] 수집 {len(obj_pts)}장 — 너무 적습니다(최소 8, 권장 30~40).")
        return False
    print(f"\n계산 중... ({len(obj_pts)}장, {size[0]}x{size[1]})")
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)

    per_view = []
    for i in range(len(obj_pts)):
        proj, _ = cv2.projectPoints(obj_pts[i], rvecs[i], tvecs[i], K, dist)
        per_view.append(float(cv2.norm(img_pts[i], proj, cv2.NORM_L2) / len(proj)))

    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    d = dist.ravel()
    print(f"\n{'='*58}\nRMS 재투영오차 = {rms:.4f} px")
    print(f"fx={fx:.2f}  fy={fy:.2f}  cx={cx:.2f}  cy={cy:.2f}")
    print(f"dist = {np.array2string(d, precision=5)}")
    print(f"해상도 {size[0]}x{size[1]} 전용 · source={meta.get('source')} flip={meta.get('flip')}")

    print(f"\n[수치 검증 — 계획서 §5.1]")
    checks = [
        ("RMS < 0.5 px", rms < 0.5, f"{rms:.4f}"),
        ("왜곡계수가 0 이 아님", bool(np.any(np.abs(d) > 1e-6)), "가장자리 촬영 여부"),
        ("주점이 정중앙이 아님", abs(cx - size[0] / 2) > 0.5 or abs(cy - size[1] / 2) > 0.5,
         f"중앙 대비 ({cx - size[0]/2:+.1f}, {cy - size[1]/2:+.1f}) px"),
        ("fx != fy", abs(fx - fy) > 1e-6, f"차이 {abs(fx - fy):.3f}"),
    ]
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    worst = sorted(range(len(per_view)), key=lambda i: -per_view[i])[:3]
    print(f"  오차 큰 장면: " + ", ".join(f"#{i}={per_view[i]:.3f}px" for i in worst))
    if rms >= 1.0:
        print("  ★ RMS 1.0 초과 — 촬영이 부족하거나 보드가 휘었습니다. 다시 찍으세요.")

    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, camera_matrix=K, dist_coeffs=dist,
             image_size=np.array(size), rms=np.array(rms))
    pts = out.with_suffix(".points.npz")          # 재촬영 없이 다시 계산할 수 있게 남긴다
    np.savez(pts, obj_pts=np.array(obj_pts), img_pts=np.array(img_pts), image_size=np.array(size))
    print(f"\n저장: {out}\n원본 점: {pts}")
    print("★ 합격선은 RMS 가 아니라 실측 거리 오차 < 5% 입니다 (계획서 §5.2). 반드시 재세요.")
    print(f"{'='*58}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="체커보드 캘리브레이션 (노트북 측)")
    ap.add_argument("--host", required=True, help="로봇 IP")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--pattern", default="auto",
                    help="내부 코너 '가로x세로' (예: 9x6). 기본 auto = 화면에서 자동 감지")
    ap.add_argument("--square-m", type=float, required=True,
                    help="인쇄물 한 칸 실측 크기(m). 자로 재서 넣는다 — 여기가 틀리면 "
                         "재투영오차는 멀쩡한데 거리만 그 비율로 전부 틀어진다")
    ap.add_argument("--num", type=int, default=40, help="목표 장수")
    ap.add_argument("--out", required=True, help="저장할 npz 경로")
    a = ap.parse_args()

    pattern: tuple[int, int] | None = None
    objp: np.ndarray | None = None
    if a.pattern != "auto":
        try:
            c, r = (int(x) for x in a.pattern.lower().split("x"))
            pattern = (c, r)
        except Exception:
            sys.exit(f"--pattern 형식이 잘못됐습니다: {a.pattern!r} (예: 9x6 또는 auto)")

    def make_objp(pat: tuple[int, int]) -> np.ndarray:
        p = np.zeros((pat[1] * pat[0], 3), np.float32)
        p[:, :2] = np.mgrid[0:pat[0], 0:pat[1]].T.reshape(-1, 2)
        return p * a.square_m

    if pattern is not None:
        objp = make_objp(pattern)

    base = f"http://{a.host}:{a.port}"
    try:
        meta = eval(urllib.request.urlopen(f"{base}/info", timeout=5).read().decode())
    except Exception as e:
        sys.exit(f"로봇 스트림에 붙지 못했습니다: {e}\n"
                 f"  로봇에서: python3 calib_stream_pi.py --source picam|usb")
    print(f"[ok] {base}  {meta}")

    reader = MjpegReader(f"{base}/stream")
    obj_pts: list[np.ndarray] = []
    img_pts: list[np.ndarray] = []
    size: tuple[int, int] | None = None
    coverage = np.zeros((3, 3), bool)
    auto, last_grab, msg, msg_until = False, 0.0, "", 0.0

    print("SPACE 수집 · a 자동 · u 취소 · c 계산+저장 · q 종료")
    while True:
        if reader.err:
            sys.exit(reader.err)
        frame = reader.latest()
        if frame is None:
            time.sleep(0.05)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if size is None:
            size = gray.shape[::-1]
            print(f"[ok] 스트림 해상도 {size[0]}x{size[1]} — 이 해상도 전용 결과가 나옵니다")
        elif gray.shape[::-1] != size:
            sys.exit(f"해상도가 도중에 바뀌었습니다 {size} → {gray.shape[::-1]}. 처음부터 다시.")

        if pattern is None:                       # 첫 프레임들에서 패턴 자동 감지
            # 훑는 데 1초 안팎 걸린다 → 화면을 '먼저' 그려서 얼어 보이지 않게 하고,
            # 1초에 한 번만 훑는다.
            view = frame.copy()
            cv2.putText(view, "SEARCHING pattern... show the WHOLE board",
                        (8, 20), _FONT, 0.5, (0, 165, 255), 1, cv2.LINE_AA)
            cv2.imshow("calib (laptop)", view)
            if (cv2.waitKey(1) & 0xFF) == ord('q'):
                break
            if time.time() - last_grab < 1.0:
                continue
            last_grab = time.time()
            guess = guess_pattern(gray)
            if guess is not None:
                pattern = guess
                objp = make_objp(pattern)
                last_grab = 0.0
                print(f"[ok] 체커보드 자동 감지: 내부 코너 {pattern[0]}x{pattern[1]} "
                      f"(= 인쇄 칸 {pattern[0]+1}x{pattern[1]+1}). "
                      f"틀리면 Ctrl-C 후 --pattern 5x5 처럼 직접 주세요.")
            continue

        found, corners = find_corners(gray, pattern)
        view = frame.copy()
        if found:
            cv2.drawChessboardCorners(view, pattern, corners, True)

        # ── 오버레이 (cv2 는 한글을 못 그린다 — ASCII 만) ──────────────────
        h, w = view.shape[:2]
        cv2.putText(view, f"{len(obj_pts)}/{a.num}  {pattern[0]}x{pattern[1]}"
                          f"  {'AUTO' if auto else 'MANUAL'}  {'BOARD' if found else '-'}",
                    (8, 20), _FONT, 0.5, (0, 255, 0) if found else (0, 0, 255), 1, cv2.LINE_AA)
        cv2.putText(view, "SPACE grab  a auto  u undo  c calc  q quit",
                    (8, h - 8), _FONT, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        cell = 14
        for gy in range(3):                       # 커버리지 3x3
            for gx in range(3):
                x0, y0 = w - 3 * cell - 6 + gx * cell, 30 + gy * cell
                cv2.rectangle(view, (x0, y0), (x0 + cell - 2, y0 + cell - 2),
                              (0, 200, 0) if coverage[gy, gx] else (60, 60, 60),
                              -1 if coverage[gy, gx] else 1)
        if time.time() < msg_until:
            cv2.putText(view, msg, (8, 40), _FONT, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow("calib (laptop)", view)

        def grab() -> str:
            c = corners.reshape(-1, 2)
            # 중복 판정은 '코너 전체의 평균 이동량'으로 한다. 중심+폭으로 재면 같은 자리에서
            # 기울이기만 한 장면(= 캘리브에 꼭 필요한 장면)을 중복으로 버린다.
            for prev in img_pts:
                if float(np.linalg.norm(c - prev.reshape(-1, 2), axis=1).mean()) < 12.0:
                    return "too similar"
            obj_pts.append(objp.copy())
            img_pts.append(corners)
            cxr, cyr = c.mean(0)
            coverage[min(2, int(cyr / (size[1] / 3))), min(2, int(cxr / (size[0] / 3)))] = True
            return f"grabbed {len(obj_pts)}"

        k = cv2.waitKey(1) & 0xFF
        now = time.time()
        if auto and found and now - last_grab > 1.0:
            msg, msg_until, last_grab = grab(), now + 1.0, now
            if len(obj_pts) >= a.num:
                auto = False
                msg, msg_until = f"target {a.num} reached - press c", now + 3.0
        if k == ord(' ') and found:
            msg, msg_until, last_grab = grab(), now + 1.0, now
        elif k == ord('a'):
            auto = not auto
        elif k == ord('u') and obj_pts:
            obj_pts.pop(), img_pts.pop()
            msg, msg_until = f"undo -> {len(obj_pts)}", now + 1.0
        elif k == ord('c'):
            if calibrate_and_report(obj_pts, img_pts, size, a.out, meta):
                break
            msg, msg_until = "need more views", now + 2.0
        elif k == ord('q'):
            print("저장 없이 종료")
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
