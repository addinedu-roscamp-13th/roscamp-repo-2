# PinkyPro 로봇 관리 서버

PinkyPro 주행 로봇 플릿 관제와 관리자 패널을 제공하는 프로젝트입니다. React 프론트엔드, FastAPI 중앙 백엔드, ROS2 `domain_bridge`, 로봇 온보드 `robot_agent`로 구성됩니다.

주행 관제의 기본 통신은 ROS2입니다. 로봇 FastAPI는 카메라, LCD, LED, 부저, 도킹/라인 같은 온보드 기능과 ROS2 링크 장애 시 HTTP 폴백 용도로 남겨둡니다.

## 프로젝트 구조

```text
fms_service/
├── backend/             중앙 FastAPI + MariaDB + JWT + ROS2 fleet telemetry
│   ├── app/
│   │   ├── fleet_telemetry.py       ROS2 상태 캐시 + fleet_cmd 명령 링크
│   │   ├── fleet_coordinator.py     로봇 간 근접 자동정지 코디네이터
│   │   ├── fleet_link_robot.py      로봇 robot_agent에 배포되는 ROS2 링크 모듈
│   │   └── routers/                 인증, 로봇, 관제, 주차, 카메라 API
│   ├── main.py
│   ├── ecosystem.config.js          pm2: backend + bridge-pinky1/2/3
│   ├── requirements.txt
│   ├── start.sh
│   └── stop.sh
├── config/
│   ├── domain_bridge_pinky{1,2,3}.yaml  ⚠️ 손으로 만든 구본(88/89/87) — 안 쓴다
│   ├── domain_bridge.template.yaml      실제로 쓰는 틀
│   └── generated/                       gen_domain_bridges.py 가 DB 기준으로 생성
├── scripts/
│   ├── gen_domain_bridges.py        .env → rc_robots 반영 후, DB 기준 브릿지 생성/기동
│   └── deploy_fleet_link.py         로봇 3대 fleet_link 배포/검증
├── frontend/                        React 19 + TanStack Router + shadcn/ui + Vite
└── desktop_gui/                    PyQt desktop GUI
```

## 서버 정보

| 항목 | 값 |
|------|-----|
| 중앙 서버 IP | 192.168.0.19 |
| 프론트엔드 | http://192.168.0.19:8080 |
| 백엔드 API | http://192.168.0.19:9001 |
| 어드민 로그인 | http://192.168.0.19:8080/admin/login |
| API 문서 | http://192.168.0.19:9001/docs |
| 기본 계정 | admin / admin1234 |

## 시스템 구조

```mermaid
flowchart LR
  subgraph Browser["관리자 브라우저"]
    UI["React Admin UI<br/>/admin/fleet /admin/fms /admin/parking"]
  end

  subgraph Server["중앙 서버 192.168.0.19"]
    NGINX["nginx<br/>frontend/dist :8080"]
    API["FastAPI bot-ai-backend<br/>:9001"]
    DB[(MariaDB<br/>labi / rc_*)]
    FT["fleet_telemetry.py<br/>ROS2 domain 86<br/>상태 캐시 + 명령 결과 대기"]
    FC["fleet_coordinator.py<br/>근접 자동정지"]
    B1["bridge-pinky1<br/>88 ↔ 86"]
    B2["bridge-pinky2<br/>89 ↔ 86"]
    B3["bridge-pinky3<br/>87 ↔ 86"]
  end

  subgraph P1["Pinky-1 192.168.0.28 / ROS_DOMAIN_ID=88"]
    R1A["robot_agent FastAPI<br/>:9001"]
    R1F["fleet_link.py"]
    R1ROS["ROS2 nav2/amcl/map/battery"]
  end

  subgraph P2["Pinky-2 192.168.0.42 / ROS_DOMAIN_ID=89"]
    R2A["robot_agent FastAPI<br/>:9001"]
    R2F["fleet_link.py"]
    R2ROS["ROS2 nav2/amcl/map/battery"]
  end

  subgraph P3["Pinky-3 192.168.0.2 / ROS_DOMAIN_ID=87"]
    R3A["robot_agent FastAPI<br/>:9001"]
    R3F["fleet_link.py"]
    R3ROS["ROS2 nav2/amcl/map/battery"]
  end

  UI -->|"HTTP API"| NGINX
  UI -->|"HTTP API :9001"| API
  API <--> DB
  API <--> FT
  FC <--> FT

  FT <--> B1
  FT <--> B2
  FT <--> B3

  B1 <--> R1ROS
  B2 <--> R2ROS
  B3 <--> R3ROS

  R1ROS <--> R1F
  R2ROS <--> R2F
  R3ROS <--> R3F

  R1F --> R1A
  R2F --> R2A
  R3F --> R3A

  API -. "HTTP 폴백 / 카메라 / LCD / LED / 부저 / 도킹" .-> R1A
  API -. "HTTP 폴백 / 카메라 / LCD / LED / 부저 / 도킹" .-> R2A
  API -. "HTTP 폴백 / 카메라 / LCD / LED / 부저 / 도킹" .-> R3A
```

### 통신 역할 분리

| 구분 | 기본 경로 | 설명 |
|------|----------|------|
| 주행 상태 | ROS2 topic → `domain_bridge` → `fleet_telemetry` | `amcl_pose`, `map`, `plan`, `battery`, `fleet_status`, `fleet_costmaps`를 중앙 서버가 캐시한다. |
| 주행 명령 | 중앙 `/pinkyN/fleet_cmd` → bridge reversed → 로봇 `/fleet_cmd` | `goal`, `goto`, `home`, `mission_*`, `schedule_*`, `slam_*`, 위치 저장/삭제를 ROS2 JSON 명령으로 보낸다. |
| 명령 결과 | 로봇 `/fleet_cmd_result` → bridge → 중앙 `/pinkyN/fleet_cmd_result` | UUID로 요청/응답을 상관시켜 FastAPI 요청에 응답한다. |
| HTTP 폴백 | 중앙 FastAPI → 로봇 `controller/drive/robot_agent :9001` | ROS2 링크가 끊기거나 구독자가 0이면 기존 HTTP 경로로 즉시 폴백한다. |
| 주변장치/온보드 기능 | 중앙/프론트 → 로봇 FastAPI | 카메라, LCD, LED, 부저, ArUco/라인 도킹은 로봇 `robot_agent`가 담당한다. |

## 실행 방법

### 백엔드

```bash
cd /home/robotPrj_Boilerplate/fms_service/backend
./start.sh
./stop.sh
tail -f /tmp/pinky_api.log
ss -tlnp | grep 9001
```

수동 개발 실행:

```bash
cd /home/robotPrj_Boilerplate/fms_service/backend
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001 --reload
```

### 프론트엔드

```bash
cd /home/robotPrj_Boilerplate/fms_service/frontend
npm install
npm run dev
nohup npm run dev > /tmp/pinky_front.log 2>&1 &
tail -f /tmp/pinky_front.log
```

## 배포 및 운영 (nginx + pm2)

현재 배포 서버(192.168.0.19)에는 프론트엔드를 nginx 정적 서빙, 백엔드를 pm2로
프로세스 관리하도록 구성되어 있으며 **리부팅 시 자동 시작**된다.

### 프론트엔드 — nginx 정적 서빙

- 접속 주소: **http://192.168.0.19:8080**
- 설정 파일: `/etc/nginx/conf.d/bot-ai-frontend.conf`
- 서빙 루트: `frontend/dist` (`vite build` 산출물, TanStack Router SPA)
- SPA 딥링크는 `try_files ... /index.html` 로 폴백 처리.
- API는 dist가 `VITE_ADMIN_API_URL=http://192.168.0.19:9001` 로 빌드돼 있어
  브라우저가 백엔드(:9001)로 **직접 호출**한다(nginx는 정적 파일만 서빙).

```bash
# 프론트 재빌드 후 반영
cd frontend && npm run build          # dist 갱신 (nginx reload 불필요)
sudo nginx -t && sudo systemctl reload nginx   # 설정 변경 시에만
```

> 포트 80은 default.conf(phpmyadmin), 3000은 다른 서비스가 사용 중이라
> 프론트는 8080을 사용한다.

### 백엔드 — pm2 프로세스 관리

- 프로세스 이름: `bot-ai-backend` (uvicorn `main:app` `0.0.0.0:9001`)
- 브릿지 프로세스: `bridge-pinky1`, `bridge-pinky2`, `bridge-pinky3`
- 설정 파일: `backend/ecosystem.config.js`
- 부팅 자동시작: systemd `pm2-ane.service` (enabled) → `pm2 resurrect`

```bash
cd backend && pm2 start ecosystem.config.js   # 최초 기동
pm2 save                                       # 현재 프로세스 목록 저장(부팅 복구용)

pm2 list                       # 상태 확인
pm2 logs bot-ai-backend        # 실시간 로그
pm2 restart bot-ai-backend     # 재시작
pm2 restart bridge-pinky1 bridge-pinky2 bridge-pinky3
pm2 stop bot-ai-backend        # 중지
```

> ⚠️ 실행 옵션이나 프로세스 구성을 바꾼 뒤에는 반드시 **`pm2 save`** 를 실행해야
> 리부팅 후에도 동일한 상태로 복구된다.

## 기능 요약

### 관리 기능

- 로그인과 JWT 인증
- 대시보드 통계
- 관리자 목록 CRUD

### PinkyPro 로봇 제어

주행/FMS 제어는 ROS2 fleet link가 기본 경로입니다. 로봇 FastAPI는 주변장치와 온보드 기능, 그리고 ROS2 장애 시 폴백 경로를 담당합니다.

| 기능 | 기본 경로 | 설명 |
|------|----------|------|
| 위치/지도/경로/배터리 | ROS2 | 중앙 서버가 topic을 구독해 캐시 |
| goal/goto/home | ROS2 `fleet_cmd` | 실패 시 HTTP `/api/goal`, `/api/goto`, `/api/home` 폴백 |
| mission/schedule | ROS2 `fleet_cmd` | 시작/정지 모두 ROS2 우선 |
| 위치 저장/삭제 | ROS2 `fleet_cmd` | DB 위치를 명령 payload에 포함 |
| 근접 자동정지 | ROS2 `mission_stop` | coordinator가 캐시 상태를 읽고 정지 명령 |

### 로봇 FastAPI 담당 기능

| 기능 | 설명 |
|------|------|
| LCD 표정 | 8종 감정 GIF 애니메이션 |
| LCD 이미지 | PNG, JPG, GIF 업로드 후 DB 목록에 저장하고 다시 표시 |
| LCD 텍스트 | 한글 포함 텍스트, 폰트, 크기, 색상, 배경 설정 |
| 폰트 관리 | TTF, OTF 업로드 및 삭제 |
| LED 제어 | 전체 색상, 개별 픽셀, 밝기 |
| 센서 | 초음파, 배터리, IR, IMU |
| 모터 | 직접 수동 제어와 도킹/라인 보조 |

### PinkyPro 부저

| 기능 | 설명 |
|------|------|
| 멜로디 | `엘리제를 위하여`, `학교종이 땡땡땡` 재생과 정지 |
| 효과음 | 벨, 비프, 알람, 성공, 오류 프리셋 |

### PinkyPro 카메라

| 기능 | 설명 |
|------|------|
| 라이브 뷰 | 320px 기준 뷰어, 스냅샷 자동 갱신 |
| 상태 정보 | 밝기, 움직임, 윤곽 밀도, 카메라 상태 |

### 아르코 마커 정밀 주차 (`/admin/parking`, 2026-07-06)

로봇 온보드 도킹 라우터: `backend/app/routers/aruco_dock_robot.py`
→ 로봇 `controller/drive/robot_agent/app/routers/aruco_dock.py` 로 배포 (경로 `/api/admin/robot/dock/*`)

| 단계 | 동작 |
|------|------|
| 접근 | 전진 펄스 + 마커 중심 호밍(중앙 ±8% 데드밴드 밖 비례 조향). 조향은 전진의 20% 이내(바퀴 비율 최대 1.5:1) — **제자리 회전 없음** |
| 축 접근 | 캘리브레이션 pose 로 마커 법선 계산 → 축이탈 >8cm 면 마커 정면 35cm **가상 경유점**을 향해 조향(마커 방위각 ±12° 제한) → 축 위로 올라타 일직선 진입 |
| 근접 사전 정렬 | 벽 ~20cm(마커가 시야에서 빠지기 전)에서 저속 펄스 회전으로 몸통을 마커 중심에 정렬 |
| 벽 정지 | 초음파 목표거리(기본 2cm) 연속 2회 도달 시 정지. 블라인드 존(<2cm) 음수값은 '초근접'으로 클램프 |
| 최종 정렬 | 벽 도달 후 마커가 보이면 몸통을 마커 정면(±ex 0.10)으로 맞춘 뒤 완료 (최대 6초) |
| 후면(rear) | 완료 시 설정 시간만큼 몸통 회전 |

안전장치: 마커 상실 시 그 자리 정지(회전 재탐색 없음, 15초 초과 안전 정지) ·
박치기 감시(전진 2.5초 무진전 → 근접이면 완료, 아니면 `blocked` 정지) ·
8px 미만 유령 검출 필터 · 초음파 스파이크 디바운스.

**카메라 캘리브레이션**: 5cm 마커 × 실측 50cm 로 초점거리 역산(fx=471px, HFOV 54°, 거리오차 ~3%).
파일: 로봇 `controller/drive/robot_agent/config/camera_calib.npz` (`camera_matrix`, `dist_coeffs`).
관제 페이지는 `MARKER_LEN_M`(0.05m)을 detect/start 에 전달 — 마커 재인쇄 시 `parking.tsx` 상수 갱신.

관제 표시: 중앙 오차(ex) · 각도(방위각, +우/-좌) · 거리(m) · 정면각(yaw, 0°=일직선) · 축이탈(m).
주의: 조향 극성 `_STEER_SIGN`(+1)은 2026-07-06 실주행 보정값 — 방향이 반대로 돌면 이 상수만 반전.

### 마커 1차 + 라인 2차 주차 (`/admin/parking`, 2026-07-07)

로봇 온보드 라인 라우터: `backend/app/routers/line_dock_robot.py`
→ 로봇 `controller/drive/robot_agent/app/routers/line_dock.py` 로 배포 (경로 `/api/admin/robot/line/*`)

| API | 동작 |
|-----|------|
| `GET /line/detect` | 현재 카메라 하단 ROI에서 흰 테이프 검출. 모터 미동작 튜닝용 |
| `POST /line/start` | 흰 테이프 중심을 추종하며 저속 전진, 초음파 목표 거리에서 정지 |
| `POST /line/stop` | 모터 정지 및 nav2 재개 |
| `GET /line/status` | 라인 검출/오프셋/벽거리/모터 텔레메트리 |

관제 `/admin/parking`은 기본적으로 **1차 마커 + 2차 라인** 순서로 동작한다.
ArUco로 먼저 주차 구역에 진입한 뒤, 흰 테이프가 보이면 라인 추종으로 마지막 구간을 미세조정한다.
`/line/detect`는 마커 주차 중에도 같이 폴링해서 라인 유무를 확인한다.
라인 검출은 화면 하단 ROI의 흰 픽셀 무게중심을 사용하므로 조명 변화가 크면 `thresh`, `roi_top`,
`min_area_px`를 `/line/detect`로 먼저 맞춘다.

> 하드웨어 제어는 `sudo -n python3 app/hardware/*.py` 형태로 직접 호출합니다.

## 환경 변수

### 프론트엔드

```env
VITE_ADMIN_API_URL=http://192.168.0.19:9001
```

### 백엔드

```bash
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=1440
DATABASE_URL=mysql+aiomysql://user:password@127.0.0.1:3306/labi
OLLAMA_URL=http://127.0.0.1:11434
```

## 기술 스택

### 프론트엔드

- React 19
- TanStack Router
- TanStack Query
- shadcn/ui + Tailwind v4
- Vite

### 백엔드

- FastAPI 0.115
- SQLAlchemy 2.0 async + aiomysql
- MariaDB
- bcrypt
- python-jose
- python-multipart
- uvicorn
- ROS2 Jazzy + domain_bridge

### 하드웨어 라이브러리

- pinkylib v1.5
- pinky_lcd
- Pillow
- rpi_ws281x
