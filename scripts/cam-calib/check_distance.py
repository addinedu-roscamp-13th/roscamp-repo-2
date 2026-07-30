#!/usr/bin/env python3
"""캘리브 합격 판정 — ArUco 마커까지 **실제 거리**를 재서 npz 가 맞는지 본다.

    # Pi:      ./calib-pi.sh picam        (robot_agent 필요 없음 — 이 스트림만 있으면 된다)
    # 노트북:
    python3 check_distance.py 192.168.1.11 --marker-m 0.05
    python3 check_distance.py 192.168.1.11 --marker-m 0.05 --truth-m 0.50   # 오차%까지

재투영오차(RMS)로는 **보드 크기 오입력을 못 잡는다** — 그때도 RMS 는 멀쩡하고 거리만
비율대로 전부 틀어진다. 그래서 진짜 합격선은 자로 잰 거리와의 오차 < 5% 다.

마커 크기가 의심될 때: 같은 자리에서 `--marker-m` 만 바꿔 재본다. 크기 오입력이면
z 가 정확히 그 비율만큼 변한다(0.05→0.06 이면 z 가 1.2배).

`_pose()` 는 로봇 도킹 코드(`aruco_dock.py:184`)와 같은 방식이다 — IPPE_SQUARE 고정.
새 도킹 코드를 짤 때 이 함수를 그대로 가져다 쓰면 된다.
"""
import argparse
import pathlib
import sys
import time
import urllib.request

import cv2
import numpy as np

CALIB_DIR = pathlib.Path(__file__).resolve().parents[2] / "config/camera"
DEFAULT_NPZ = CALIB_DIR / "picam_640x480.npz"     # 가장 흔한 경우. 다르면 --npz


def load_calib(path: pathlib.Path):
    if not path.exists():
        have = sorted(p.name for p in CALIB_DIR.glob("*.npz") if not p.name.endswith(".points.npz"))
        sys.exit(f"캘리브 파일이 없습니다: {path}\n"
                 + (f"  config/camera 에 있는 것: {', '.join(have)}\n  --npz 로 골라 주세요.\n" if have else "")
                 + "  먼저 ./calib-laptop.sh picam <IP> <한칸_m> 를 하세요.")
    d = np.load(str(path))
    size = tuple(int(x) for x in d["image_size"]) if "image_size" in d else None
    return d["camera_matrix"], d["dist_coeffs"], size


def get_dictionary(dict_name: str):
    dict_id = getattr(cv2.aruco, dict_name, None)
    if dict_id is None:
        sys.exit(f"--dict 를 모르겠습니다: {dict_name!r}")
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(dict_id)     # 4.7+
    return cv2.aruco.Dictionary_get(dict_id)                  # 4.6


def detect_markers(gray, dictionary):
    """(corners, ids) — 4.6 / 4.7+ / 5.x 모두. ids 는 (N,) 로 평탄화해서 돌려준다."""
    params = (cv2.aruco.DetectorParameters_create()
              if hasattr(cv2.aruco, "DetectorParameters_create")
              else cv2.aruco.DetectorParameters())
    if hasattr(cv2.aruco, "ArucoDetector"):                   # 4.7+
        corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
    else:                                                     # 4.6
        corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary, parameters=params)
    if ids is None or len(ids) == 0:
        return [], np.empty((0,), np.int32)
    return corners, np.asarray(ids, np.int32).ravel()


def pose(corners, K, dist, marker_len_m: float):
    """마커까지 x(우+)·z(거리)·yaw(정면각). aruco_dock.py:184 와 같은 IPPE_SQUARE.

    ITERATIVE 는 작은 마커에서 yaw 부호가 튀어 축 접근을 망친다 — 그래서 평면 사각형
    전용 해법을 고정으로 쓴다.
    """
    img = np.asarray(corners, np.float32).reshape(4, 2)
    s = marker_len_m / 2.0
    obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], np.float32)
    try:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    except cv2.error:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec.reshape(3))
    yaw = float(np.degrees(np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))))
    t = tvec.reshape(3)
    return {"x_m": float(t[0]), "y_m": float(t[1]), "z_m": float(t[2]), "yaw_deg": yaw}


def grab(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        buf = np.frombuffer(r.read(), np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"JPEG 디코드 실패: {url}")
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description="ArUco 마커 거리 실측 검증")
    ap.add_argument("host", help="로봇 IP (calib-pi.sh 가 떠 있어야 한다)")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--marker-m", type=float, required=True, help="마커 검은 사각형 한 변(m) — 자로 잰 값")
    ap.add_argument("--dict", default="DICT_5X5_50")
    ap.add_argument("--npz", default=str(DEFAULT_NPZ))
    ap.add_argument("--truth-m", type=float, default=None, help="자로 잰 실제 거리(m). 주면 오차%%를 찍는다")
    ap.add_argument("--n", type=int, default=5, help="평균낼 프레임 수 (기본 5)")
    ap.add_argument("--show", action="store_true",
                    help="창을 띄워 조준한다 — 마커가 화면에 있는지 눈으로 보고 SPACE 로 측정. "
                         "이게 없으면 '마커를 못 찾았습니다'가 조준 실패인지 검출 실패인지 모른다")
    a = ap.parse_args()

    K, dist, size = load_calib(pathlib.Path(a.npz))
    print(f"[ok] {a.npz}\n     fx={K[0,0]:.2f} fy={K[1,1]:.2f} cx={K[0,2]:.2f} cy={K[1,2]:.2f}"
          + (f"  ({size[0]}x{size[1]} 전용)" if size else ""))

    dictionary = get_dictionary(a.dict)
    url = f"http://{a.host}:{a.port}/snapshot"

    if a.show:
        print("창에서 마커가 보이게 맞춘 뒤 SPACE — q 로 종료")
        while True:
            frame = grab(url)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids = detect_markers(gray, dictionary)
            view = frame.copy()
            if len(ids):
                cv2.aruco.drawDetectedMarkers(view, corners, ids.reshape(-1, 1))
                for c, i in zip(corners, ids):
                    p = pose(c, K, dist, a.marker_m)
                    if p:
                        cv2.putText(view, f"id{i} z={p['z_m']*100:.1f}cm",
                                    tuple(np.int32(np.asarray(c).reshape(4, 2).mean(0)) + [-40, -12]),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.putText(view, f"{len(ids)} marker(s)  {a.dict}   SPACE=measure  q=quit",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 255, 0) if len(ids) else (0, 0, 255), 1, cv2.LINE_AA)
            cv2.imshow("check_distance", view)
            k = cv2.waitKey(30) & 0xFF
            if k == ord('q'):
                cv2.destroyAllWindows()
                return
            if k == ord(' ') and len(ids):
                break
        cv2.destroyAllWindows()

    acc: dict[int, list[dict]] = {}
    for _ in range(a.n):
        frame = grab(url)
        if size and frame.shape[1::-1] != tuple(size):
            sys.exit(f"⚠ 해상도가 캘리브와 다릅니다: 스트림 {frame.shape[1]}x{frame.shape[0]} "
                     f"vs 캘리브 {size[0]}x{size[1]}\n"
                     f"  K 는 해상도 전용입니다. 같은 파이프라인으로 다시 뽑거나 K 를 스케일하세요.")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids = detect_markers(gray, dictionary)
        for c, i in zip(corners, ids):
            p = pose(c, K, dist, a.marker_m)
            if p:
                acc.setdefault(int(i), []).append(p)
        time.sleep(0.2)

    if not acc:
        sys.exit(f"마커를 못 찾았습니다 ({a.dict}). 딕셔너리·조명·거리를 확인하세요.")

    for mid, samples in sorted(acc.items()):
        z = float(np.mean([s["z_m"] for s in samples]))
        zs = float(np.std([s["z_m"] for s in samples]))
        x = float(np.mean([s["x_m"] for s in samples]))
        yaw = float(np.mean([s["yaw_deg"] for s in samples]))
        line = (f"id {mid:3d}  z={z*100:6.1f}cm (±{zs*100:.1f})  x={x*100:+6.1f}cm  "
                f"yaw={yaw:+6.1f}°  [{len(samples)}프레임]")
        if a.truth_m:
            err = (z - a.truth_m) / a.truth_m * 100
            line += f"   실측 {a.truth_m*100:.1f}cm 대비 {err:+.1f}%  {'PASS' if abs(err) < 5 else 'FAIL'}"
        print(line)

    if a.truth_m:
        print("\n오차가 크고 모든 거리에서 비율이 일정하면 → 마커 크기(--marker-m) 또는 fx 스케일 문제.")
        print("거리마다 부호·크기가 들쭉날쭉하면 → 왜곡계수(가장자리 촬영 부족) 문제.")


if __name__ == "__main__":
    main()
