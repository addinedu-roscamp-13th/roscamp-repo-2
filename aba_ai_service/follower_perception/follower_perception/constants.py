# All values are reference starting points for tuning — not hard requirements.
import os as _os

# Detection
MIN_CONFIDENCE = 0.42          # YOLO person confidence floor

# Owner identification (dual gate)
REID_THRESHOLD = 0.68          # cosine similarity floor
HSV_THRESHOLD = 0.30           # histogram correlation floor ([0,1])
VERIFY_FRAMES = 5              # consecutive passes to lock safe_id

# Online gallery
CALIBRATION_INTERVAL = 15      # frames between online updates (ReID gallery + HSV EMA); ~1s @15fps
CALIBRATION_ADD_THRESHOLD = 0.85  # append only if best gallery sim < this
MAX_GALLERY_SIZE = 50
HSV_UPDATE_ALPHA = 0.1            # online HSV template EMA rate (adapt to lighting; 0 = off)

# Registration
REGISTRATION_STABLE_FRAMES = 3    # consecutive frames the central target must persist
REGISTRATION_MIN_AREA_RATIO = 0.01  # min bbox area / frame area to register

# Smoothing / coasting
SMOOTHER_ALPHA = 0.45
SMOOTHER_BETA = 0.15
FRAME_DT = 0.05                # nominal seconds per frame (20 FPS)
PREDICT_DT = 0.05             # latency-compensation lookahead
COAST_LIMIT = 30              # max consecutive missed frames still output (predicted; ~2s @15fps)

# HSV histogram
HSV_BINS = 16                 # per channel; total 48-d (H+S+V)

# ── 자세 게이트 ────────────────────────────────────────────────────────────
# Unknown 이 이만큼 **연속**되면 정지. 즉시 정지로 두면 안 되는 이유: 자세 판정은
# 어깨 2점·골반 2점의 신뢰도가 전부 기준을 넘어야 나오는데, 사람이 옆으로 서 있기만
# 해도 반대쪽이 가려져 Unknown 이 난다. 정상 추종 중에 계속 멈칫하게 된다.
UNKNOWN_STOP_FRAMES = 10
# 자세 추론 주기(프레임). 1 = 매 프레임. 프레임 예산(15fps → 66ms)을 넘기면 3 으로
# 올린다 — 자세는 프레임 단위로 바뀌지 않으므로 직전 판정을 유지해도 손실이 작다.
POSE_EVERY_N_FRAMES = 1
# yolo_pose 는 이 저장소 **밖**의 별개 저장소다. 상대경로로 짚으면 안 된다
# (aba_project/yolo_pose 를 가리켜 import 가 실패한다). 환경변수로 덮어쓸 수 있다.
YOLO_POSE_DIR = _os.environ.get("LIBI_YOLO_POSE_DIR", "/home/ane/personal_repo/yolo_pose")
# 자세 전용 2차 모델. 검출 가중치(weights/best.pt)는 task=detect 라 키포인트를 못 낸다.
# 이미 받아져 있는 파일을 기본값으로 둔다 — 이름만 주면 ultralytics 가 네트워크에서
# 받으려 하고, 로봇·서버가 오프라인이면 거기서 멈춘다.
POSE_WEIGHTS = _os.environ.get(
    "LIBI_POSE_WEIGHTS", _os.path.join(YOLO_POSE_DIR, "yolo11n-pose.pt"))

# ── 소실 방향 게이트 ────────────────────────────────────────────────────────
EXIT_EDGE_MARGIN_RATIO = 0.08   # 프레임 가장자리로 볼 비율(폭·높이 각각)
EXIT_AREA_SURGE = 8000.0        # 면적 속도(px^2/frame)가 이보다 크면 코앞으로 본다
