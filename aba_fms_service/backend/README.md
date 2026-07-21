# Labi Bot Backend

FastAPI 기반 중앙 관리자 API 서버입니다. MariaDB와 SQLAlchemy async를 사용하며, 주행 로봇 상태/명령은 ROS2 fleet link를 기본 경로로 처리합니다. 로봇 온보드 FastAPI는 주변장치와 HTTP 폴백 역할로 남아 있습니다.

## 스택

| 항목 | 내용 |
|------|------|
| 런타임 | Python 3.12 |
| 프레임워크 | FastAPI 0.115 |
| DB | MariaDB (`labi`, `rc_*` 테이블) |
| ORM | SQLAlchemy 2.0 async |
| 인증 | JWT + bcrypt |
| 서버 | Uvicorn |

## 디렉토리 구조

```text
backend/
├── main.py                  # 앱 진입점, 라우터 등록, 시드
├── requirements.txt
└── app/
    ├── config.py            # 환경변수 / 설정
    ├── database.py          # SQLAlchemy 엔진, 세션
    ├── models.py            # ORM 모델
    ├── schemas.py           # Pydantic 스키마
    ├── security.py          # 비밀번호 해시, JWT
    ├── deps.py              # FastAPI 의존성
    ├── fleet_telemetry.py   # ROS2 상태 캐시 + fleet_cmd 명령 링크
    ├── fleet_coordinator.py # 로봇 간 근접 자동정지
    ├── fleet_link_robot.py  # 로봇 robot_agent에 배포되는 온보드 ROS2 링크
    ├── hardware/            # 중앙 서버 로컬 하드웨어 제어 스크립트
    └── routers/
        ├── auth.py          # 로그인, 현재 관리자
        ├── dashboard.py     # 대시보드 통계
        ├── users.py         # 관리자 CRUD
        ├── robot.py         # LCD, LED, 센서, 모터, 부저
        ├── camera.py        # 카메라 snapshot, status, analysis
        └── dev.py           # 테이블, ERD
```

## 빠른 시작

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001
```

첫 실행 시 MariaDB 테이블을 확인하고 기본 superadmin 계정이 없으면 생성합니다.

| 항목 | 값 |
|------|----|
| 기본 계정 | `admin` / `admin1234` |
| API 베이스 | `http://192.168.1.4:9001` |
| API 문서 | `http://192.168.1.4:9001/docs` |

## 주요 API

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/health` | 헬스체크 | 불필요 |
| POST | `/api/auth/login` | 로그인, JWT 반환 | 불필요 |
| GET | `/api/auth/me` | 내 정보 조회 | Bearer |
| GET | `/api/dashboard/stats` | 대시보드 통계 | Bearer |
| GET | `/api/users` | 관리자 목록 | Bearer |
| POST | `/api/users` | 관리자 추가 | superadmin |
| PUT | `/api/users/{id}` | 관리자 수정 | Bearer |
| DELETE | `/api/users/{id}` | 관리자 삭제 | superadmin |
| GET | `/api/robot/lcd/images` | LCD 이미지 목록 | Bearer |
| POST | `/api/robot/lcd/image` | LCD 이미지 업로드 | Bearer |
| POST | `/api/robot/lcd/image/select` | LCD 이미지 표시 | Bearer |
| DELETE | `/api/robot/lcd/images/{name}` | LCD 이미지 삭제 | Bearer |
| GET | `/api/robot/buzzer/status` | 부저 재생 상태 | Bearer |
| POST | `/api/robot/buzzer/melody/play` | 멜로디 재생 | Bearer |
| POST | `/api/robot/buzzer/melody/stop` | 멜로디 정지 | Bearer |
| GET | `/api/robot/sensor/ir` | IR 센서 읽기 | Bearer |
| GET | `/api/robot/sensor/imu` | IMU 센서 읽기 | Bearer |
| POST | `/api/robot/motor/move` | 모터 이동 | Bearer |
| POST | `/api/robot/motor/stop` | 모터 정지 | Bearer |
| GET | `/api/robot/camera/snapshot` | 최신 카메라 이미지 | token query |
| GET | `/api/robot/camera/status` | 카메라 상태 | Bearer |
| GET | `/api/robot/camera/analysis` | 카메라 분석값 | Bearer |
| GET | `/api/control/state` | 주행 로봇 상태, ROS2 캐시 우선 | Bearer |
| GET | `/api/control/telemetry` | ROS2 링크 진단 | Bearer |
| POST | `/api/control/goal` | 목표 좌표 이동, ROS2 명령 우선 | Bearer |
| POST | `/api/control/goto` | 저장된 구역명으로 이동, ROS2 명령 우선 | Bearer |
| POST | `/api/control/mission/stop` | 미션 정지, ROS2 명령 우선 | Bearer |
| GET | `/api/dev/tables` | DB 테이블 스키마 | Bearer |
| GET | `/api/dev/erd` | ERD 데이터 | Bearer |

## 구역 이동 예시 (`goto`)

주행 로봇을 저장된 구역(A~H 등)으로 보내는 표준 호출.

> ⚠️ **`robot_id` 는 DB `rc_robots.id` 값이다.** 등록 순서상 id 1=CentralServer,
> 2=JetCobot-1 이므로 **"주행로봇 1" 은 `robot_id=3`**, 주행로봇 2=4, 주행로봇 3=5.
> 구역명도 DB(`rc_robot_locations.name`)에 저장된 그대로("C" 등)를 쓴다.

| 항목 | 값 |
|------|-----|
| 메서드/경로 | `POST /api/control/goto` |
| 쿼리 | `robot_id`(필수), `nav_port`(선택, 기본 9001) |
| 본문 | `{ "name": "<구역명>" }` |
| 인증 | `Authorization: Bearer <관리자 토큰>` |

동작: 중앙 서버가 DB에서 해당 로봇의 IP와 구역 좌표(x·y·yaw)를 찾아, ROS2 `fleet_cmd`
(실패 시 로봇 `:9001` HTTP)로 명령을 전달 → 로봇 nav2가 주행한다.

```bash
# 1) 로그인해서 JWT 발급
TOKEN=$(curl -s -X POST http://192.168.1.4:9001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<관리자ID>","password":"<PW>"}' | jq -r '.access_token')

# 2) 주행로봇 1(robot_id=3) → C 구역으로 이동
curl -X POST 'http://192.168.1.4:9001/api/control/goto?robot_id=3' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"C"}'
```

어드민 화면(`/admin/dev/api-docs`)에서 호출하면 로그인 세션 토큰이 자동으로 붙으므로
`robot_id=3` 과 본문 `{"name":"C"}` 만 채우면 된다.

## ROS2 fleet link 메모

- 중앙 서버는 ROS2 domain 86에서 `/pinky{1,2,3}/*` 토픽을 구독합니다.
- `bridge-pinky1/2/3`가 각 로봇 domain과 중앙 domain 86을 연결합니다.
- 주행 명령은 `/pinkyN/fleet_cmd`로 발행하고, 로봇의 `/fleet_cmd_result` 응답을 기다립니다.
- ROS2 링크가 끊기면 기존 로봇 `controller/drive/robot_agent :9001` HTTP API로 즉시 폴백합니다.

## 환경변수

```bash
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=1440
DATABASE_URL=mysql+aiomysql://user:password@127.0.0.1:3306/labi
OLLAMA_URL=http://127.0.0.1:11434
```

## 서비스 등록 예시

```ini
[Unit]
Description=Labi Bot Admin API
After=network.target

[Service]
User=pinky
WorkingDirectory=/home/robotPrj_Boilerplate/fms_service/backend
ExecStart=/home/robotPrj_Boilerplate/fms_service/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001
Restart=always

[Install]
WantedBy=multi-user.target
```
