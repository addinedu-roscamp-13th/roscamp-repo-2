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
# [2026-08-07] 24 → **21. 송출 fps 를 17 → 15 로 내려서다** (21/15 = 1.40s).
#   초 단위 값(1.4초)은 그대로다 — 프레임 단위라 fps 를 따라 같이 움직여야 한다.
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
#    실측 기준은 **15fps** — `scripts/all/libi_pi.sh` 가 `FPS='15'` 로 띄운다.
#    21/15 = 1.40초.
#    ⚠️ fps 를 바꾸면 `GUIDE_COAST_SEC`(초 단위)과 어긋난다 — 그날 같이 고칠 것.
#    ⚠️ 이 파일의 다른 프레임 상수 주석은 아직 15fps 로 적혀 있다
#       (CALIBRATION_INTERVAL, UNKNOWN_STOP_FRAMES) — 실제로는 각각 12% 짧다.
COAST_LIMIT = 21              # max consecutive missed frames still output (predicted)

# ── 코스팅 외삽 (2026-08-06 재도입) ─────────────────────────────────────────
#
# [2026-08-02] 에 외삽을 **껐다가** 사용자 요청으로 되돌린다. 다만 그때 실측된 결함
# 두 개는 각각 막고 넣는다 — 안 막으면 "그냥 들이박네"(2026-07-28)가 재현된다.
#
#   ① **면적은 절대 외삽하지 않는다.** 서가·문틀 뒤로 들어가면 사라지기 직전 bbox 가
#      잘려 면적이 급감하고, 필터가 그 급감을 속도로 학습해 밀고 나갔다. 실측
#      **3프레임 만에 area 0**(700 → -459/frame). `√area = 0` 은 거리 PID 에 "아주
#      멀다"로 읽혀 **전속 전진**이 된다. 위치만 외삽하고 면적은 마지막 실측을 든다.
#
#   ② **밀어낼 수 있는 거리에 상한을 둔다.** 가려지기 직전 몇 프레임은 중심도 튀고,
#      그 튐이 속도로 학습돼 박스를 엉뚱한 데로 끌고 갔다. 물리적 근거로 자른다 —
#      코스팅 상한 1.4초 × 최대 전진 0.06m/s ≈ 8cm 로 사람의 실제 이동은 작고,
#      화면에서 그만큼은 대개 bbox 폭 언저리다. 폭의 배수로 자른다(해상도 무관).
#: 코스팅 중 중심을 밀어낼 수 있는 최대 거리 — **마지막 bbox 폭의 배수**.
#: 0 이면 외삽을 끄고 2026-08-02~08-06 처럼 마지막 박스를 그대로 든다.
COAST_MAX_DRIFT_W = 1.0

#: 기준 비율 측정(`Calibrating`)을 포기하는 시각(초). 0 이면 **무한 대기**(옛 동작).
#
# ⚠️ 왜 필요한가 (실측 2026-08-06)
#   `RatioCalibrator.done` 은 **몸통 키포인트 4점이 전부 conf ≥ 0.5 인 프레임 60장**이
#   모여야 True 가 된다. 조건 미달 프레임은 표본에 안 들어가므로, 역광·측면·먼 거리처럼
#   골격이 잘 안 잡히는 상황에서는 **영영 안 끝난다.** 그동안:
#     · `PostureGate` 가 `Calibrating` 을 즉시정지로 봐서 로봇이 안 간다
#     · `exit_direction._NO_COAST_POSTURES` 에 `Calibrating` 이 있어 **코스팅이 통째로
#       막힌다** — 주황 박스가 아예 안 나오고, 놓치는 즉시 소실 처리된다
#   `--pose` 를 끄면 자세가 None 이라 둘 다 안 걸린다. "골격 없이는 잘 됐다"가 이것이다.
#
# ⚠️ 시간이 다 되면 **기준을 억지로 확정하지 않는다.** 모자란 표본으로 기준을 세우면
#    "누웠는데 Standing" 이 나올 수 있고, 그건 쓰러진 사람에게 로봇을 보낸다.
#    대신 `Unknown` 으로 넘어간다 — 판정을 포기하되 거짓말은 안 하는 쪽이다.
#    `Unknown` 은 `PostureGate` 가 25연속이면 정지시키고(안전은 유지), 코스팅
#    차단목록에는 없어서 주황 박스는 돌아온다.
#
# 5초인 이유: 15fps 에서 60장은 4.0초다. 조건이 정상이면 그 안에 끝난다 — 5초를
# 넘겼다는 것은 "느린 것"이 아니라 "안 잡히는 것"이다.
POSE_CALIBRATION_TIMEOUT_SEC = 5.0

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
