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

# ── 등록 직후 집중 학습 ─────────────────────────────────────────────────────
#
# `register()` 는 **한 장**으로 템플릿을 만든다(gallery = [template]). 그 뒤 갤러리는
# `CALIBRATION_INTERVAL`(15프레임 ≈ 0.9초) 마다 한 번씩만 자라므로, 등록하자마자
# 카메라가 뒤로 바뀌거나 사람이 조금만 돌아서면 **유사도가 REID_THRESHOLD 를 못 넘어
# owner 가 안 잡힌다.** 그때 파이프라인은 코스팅으로 넘어가고 화면에는 예측(주황) 박스만
# 뜬다 — `requester_visible` 은 예측을 거르므로 길잡이가 **영영 출발하지 못한다**
# (실측 2026-08-02: 뒷캠에 노란 박스만 나와 테스트 자체가 안 됐다).
#
# 그래서 등록 직후 짧게 **매 프레임** 갤러리를 채운다. 사람이 서 있는 그 몇 초 동안
# 자세·조명·거리가 조금씩 달라지므로, 한 장보다 훨씬 넓은 표현을 얻는다.
#
# ⚠️ 이 구간에도 `CALIBRATION_ADD_THRESHOLD` 는 그대로 적용된다 — 새로운 각도일 때만
#    추가되므로 같은 그림이 50장 쌓이지 않는다(MAX_GALLERY_SIZE 도 그대로).
REGISTRATION_LEARN_SEC = 3.0      # 등록 직후 매 프레임 학습할 시간(초)
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
# [2026-08-02] 30 → **24. 사용자 지정 1.4초 @17fps** (24/17 = 1.41s).
#   유실 구간 동안 예측 위치로 계속 몰아 목표 크기에 맞춰 가고, 다 쓰면 회복 BT 의
#   `LkdPeek` 가 **마지막으로 돌던 방향(LKD)** 부터 훑는다. 그 방향은 코스팅 중에
#   기록된다 — 코스팅이 꺼져 있으면 LKD 도 안 갱신되므로 peek 이 엉뚱한 쪽을 본다.
#
# [2026-08-01] 30 → 10 → 30, [2026-08-02] → **24**.
#   10(0.6초)은 사람이 잠깐 몸을 돌리거나 서가에 가려지는 흔한 구간에서 먼저 끊겨
#   회복 탐색이 바로 돌았다(실측 "사라지면 바로 peek 된다").
#   24 로 맞춘 이유: 로봇쪽 길잡이 코스팅(`libi_perception/config.py GUIDE_COAST_SEC`)이
#   **1.4초**인데, 파이프라인이 그보다 길면 길잡이에서 두 값 중 짧은 쪽만 의미를 갖고
#   긴 쪽은 죽은 설정이 된다. 24/17 = **1.41초** 라 둘이 사실상 같아진다
#   (사용자 확인 2026-08-02: "1.4초 유지해줘 추종과 같이").
#
# ⚠️ **단위가 초가 아니라 프레임이다.** 실제 송출 fps 가 바뀌면 유예 시간도 같이 바뀐다.
#    실측 기준은 **17fps** — `scripts/all/libi_pi.sh:486` 이 `FPS='17'` 로 띄운다
#    (`image-sender.sh:14` 의 기본값 15 를 덮어쓴다). 24/17 = 1.41초.
#    ⚠️ fps 를 바꾸면 `GUIDE_COAST_SEC`(초 단위)과 어긋난다 — 그날 같이 고칠 것.
#    ⚠️ 이 파일의 다른 프레임 상수 주석은 아직 15fps 로 적혀 있다
#       (CALIBRATION_INTERVAL, UNKNOWN_STOP_FRAMES) — 실제로는 각각 12% 짧다.
COAST_LIMIT = 24              # max consecutive missed frames still output (predicted)

# HSV histogram
HSV_BINS = 16                 # per channel; total 48-d (H+S+V)

# ── 자세 게이트 ────────────────────────────────────────────────────────────
# Unknown 이 이만큼 **연속**되면 정지. 즉시 정지로 두면 안 되는 이유: 자세 판정은
# 어깨 2점·골반 2점의 신뢰도가 전부 기준을 넘어야 나오는데, 사람이 옆으로 서 있기만
# 해도 반대쪽이 가려져 Unknown 이 난다. 정상 추종 중에 계속 멈칫하게 된다.
#
# [2026-07-28] 10 → 25. 현장에서 **정상 추종 중에 자꾸 멈칫**했다.
#   15fps 기준 10프레임 = 0.67초. 사람이 몸을 돌리거나 옆모습이 되는 흔한 상황에서
#   그 정도는 쉽게 연속으로 뜬다. 25프레임 = 약 1.7초로 늘려 진짜 놓쳤을 때만 선다.
#   (Unknown 이 아니라 **Lying** 이 나오면 이 카운터와 무관하게 즉시 멈춘다 —
#    쓰러진 사람에게 다가가지 않는 규칙은 그대로다. posture_gate.py 참고)
UNKNOWN_STOP_FRAMES = 25
# 자세 추론 주기(프레임). 1 = 매 프레임. 프레임 예산(15fps → 66ms)을 넘기면 3 으로
# 올린다 — 자세는 프레임 단위로 바뀌지 않으므로 직전 판정을 유지해도 손실이 작다.
#
# [2026-07-28] 3 으로 올렸다가 **되돌렸다.** 두 가지 이유다:
#   ① 대가가 크다 — 보정(Calibrating)이 표본 수 기준이라 3 이면 등록 직후 로봇이
#      멈춰 있는 시간이 **3배**가 된다. tests/test_pose_estimator.py 가 이걸 잡았다.
#   ② 근거가 없었다 — 포화된 기계는 **Pi** 이고 자세 추론은 **노트북**에서 돈다.
#      노트북이 병목이라는 측정을 하지 않은 채 올린 값이었다.
#   올리려면 먼저 AI 서버의 프레임 처리 시간을 재고, 66ms(15fps 예산)를 넘는지 확인할 것.
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
