# 로봇팔 JetCobot 관리자 모듈

Yahboom **JetCobot** 7축 비전 협동 로봇팔을 웹 관리자(Admin)에서 제어하는 모듈.
`gazebo_fastapi` 프로젝트의 로봇팔 모듈을 `fms_service` 관리자(`/admin`)로 이식한 것이다.

---

## 메뉴 구성 (대메뉴 / 서브메뉴)

사이드바 대메뉴 **`로봇팔 JetCobot`** (아이콘: `Hand`) 아래 4개 서브메뉴.

| 서브메뉴 | 경로 | 파일 | 기능 |
|---|---|---|---|
| 대시보드 | `/admin/arm` | `index.tsx` | 7축 관절 각도 · 그리퍼 · 모드 실시간 모니터링 |
| 색상 블록 집기 | `/admin/arm/color-pick` | `color-pick.tsx` | HSV 색상 감지 후 블록 집기 (빨강/초록/파랑/노랑) |
| 얼굴 추적 | `/admin/arm/face-track` | `face-track.tsx` | 얼굴 감지 → J1/J2 P제어로 팔이 추적 |
| 제스처 제어 | `/admin/arm/gesture` | `gesture.tsx` | 손 제스처(주먹/손바닥/손가락/흔들기) → 팔 동작 |

메뉴 정의 위치: `frontend/src/components/admin/AdminShell.tsx` → `NAV_GROUPS` 의 `key: "arm"` 그룹.

---

## 기능 상세

### 1. 대시보드 (`/admin/arm`)
- 7축 관절 각도(J1~J6 + 그리퍼) 게이지 실시간 표시.
- 빠른 제어: 홈 포지션 복귀 / 긴급 정지.
- 그리퍼 개폐 상태(0=열림, 100=닫힘) 시각화.
- 하드웨어 미연결 시 **Demo 모드** 배너 표시.
- WebSocket `/api/arm/ws/arm` 으로 상태 수신.

### 2. 색상 블록 집기 (`/admin/arm/color-pick`)
```python
# 핵심 로직 (OpenCV)
hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
mask = cv2.inRange(hsv, lower, upper)
contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
```
- 색상 선택 → `POST /api/arm/color-pick` → 감지 → 이동 → 집기 → 홈 복귀.
- 카메라 뷰에 감지 박스 오버레이 + 실행 로그.

### 3. 얼굴 추적 (`/admin/arm/face-track`)
- Haar Cascade 또는 DNN으로 얼굴 감지.
- 얼굴 중심과 화면 중앙의 오차를 **P 제어**로 J1/J2 관절 각도에 반영.
- 추정 거리·얼굴 중심 좌표 실시간 표시.
- `POST /api/arm/face-track/start` · `/stop`.

### 4. 제스처 제어 (`/admin/arm/gesture`)
- MediaPipe Hands로 손 landmark 21개 추출 → 제스처 분류.
- 매핑: ✊ 주먹→그리퍼 닫기, ✋ 손바닥→홈 복귀, ☝️ 1손가락→J1 +20°, ✌️ 2손가락→J1 −20°, 🤙 흔들기→긴급 정지.
- `POST /api/arm/gesture/start` · `/stop`.

---

## 현재 상태

**Demo 모드** — JetCobot 하드웨어 및 백엔드 `arm` 라우터가 아직 없어 프론트엔드가
시뮬레이션으로 동작한다. WebSocket/REST 호출은 실패해도 UI가 그대로 표시된다.

각 페이지 상단 import 한 줄만 이 프로젝트의 Shell(`@/components/admin/AdminShell`)로 교체한 것 외에는
원본 로직(API URL, WebSocket, 상태 타입)을 그대로 유지했다.

---

## 백엔드 연동 가이드 (TODO)

> 이 프로젝트의 라우터 규칙: `backend/app/routers/*.py` 에 정의 → `backend/main.py` 에서
> `app.include_router(...)` 로 등록. 기존 라우터는 `/api/...` 프리픽스 + 토큰 인증을 사용한다.

로봇팔 백엔드를 붙이려면 둘 중 하나:

1. **원본 경로 유지** — `arm.py` 를 추가하고 `prefix="/api/arm"` 로 등록.
   프론트엔드 수정 불필요 (현재 페이지가 `/api/arm/*` 를 호출).
   ```python
   # backend/main.py
   from app.routers import arm
   app.include_router(arm.router)   # prefix="/api/arm"
   ```

2. **프로젝트 규칙에 맞춤** — `prefix="/api/arm"` + 인증 deps 적용 후
   프론트 4개 파일의 `fetch("/api/arm/...")` / WS URL을 `/api/arm/...` 로 변경.

### 하드웨어 연동 포인트

| 연결 방식 | 수정 위치 (신규 `arm.py`) |
|---|---|
| ROS2 `/joint_states` 구독 | `ArmBridge._run_*` 내 TODO |
| USB 카메라 (OpenCV) | `_demo_camera_frame()` → `cv2.VideoCapture(0)` |
| MediaPipe 제스처 | `_run_gesture()` 내 TODO |
| Serial SDK | `ArmBridge.__post_init__()` (예: `/dev/ttyUSB0 @ 115200`) |

---

## WebSocket 메시지 규약

```jsonc
// 서버 → 클라이언트
{ "type": "state",  "connected": false, "demo_mode": true,
  "mode": "idle|homing|color_pick|face_track|gesture",
  "joints": [0,0,0,0,0,0,0], "gripper": 0, "detection": null }
{ "type": "camera", "frame": "<base64 png>" }
{ "type": "log",    "level": "info|warn|success", "msg": "..." }

// 클라이언트 → 서버 (대시보드 빠른 제어)
{ "action": "home" }
{ "action": "stop" }
```

원본 출처: `gazebo_fastapi` README "JetCobot 로봇팔 모듈" 섹션.
