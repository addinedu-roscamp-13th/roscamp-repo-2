# 관리자 추종 제어 경로 설계 (cmd_vel 직결 + 승인/관제 분리)

**날짜:** 2026-07-20
**범위:** `aba_controller/libi_gui`, `aba_fms_service/backend` (구현) · `aba_ai_service` (결정만 기록)

## 배경 — 왜 이 문서가 필요했나

레포에 관리자 추종 구현이 **두 갈래**로 존재했다.

| | 경로 | 상태 |
|---|---|---|
| (a) "공식" | `aba_ai_service/main.py` + `detection_sink.py`(TCP:6000) → `libi_perception`(`FollowExec`, libi_modes BT 의 WORKING 브랜치) | BT 통합 설계는 있으나 추적 로직 미검증 |
| (b) 독자 | `follower_perception/`: `camera_sender.py` → `perception_server.py` → `cmd_bridge.py` 가 `/cmd_vel` 직접 발행 | YOLO/추적/재식별/LiDAR 회피까지 구현·동작, 대신 FMS/GUI/BT 어디와도 연결 없음 |

어느 쪽을 진짜 구현으로 삼을지, 그리고 제어 신호가 FMS 를 거쳐야 하는지가 정해지지 않아
GUI 의 추종 버튼이 로컬 스텁(`// ROS2-SEAM`)으로 남아 있었다.

## 결정

### 1. `follower_perception` 을 진짜 구현으로 삼는다

검증된 추적 로직을 버리지 않는다. `libi_perception`(BT)과 `aba_ai_service/main.py` 의
`detection_sink` 경로는 공식 구현 자리에서 내려놓되, **코드는 삭제하지 않는다** — 실제로
대체가 끝난 뒤에 정리한다.

### 2. 실시간 제어(Detection→cmd_vel)는 FMS/domain_bridge 를 거치지 않는다

`ai_service` ↔ 로봇 직결(TCP)을 유지한다. 근거:

- 추종 제어는 로봇에서 LiDAR 와 20Hz 로 융합되는 루프다. 매 프레임 서버 왕복을 태우면
  지연·네트워크 불안정이 그대로 주행 품질로 나온다.
- 이미 `nav2`/`fleet_cmd` 가 같은 원칙으로 돈다 — FMS 는 `goto` 같은 고수준 목표만 내리고,
  `cmd_vel` 계산은 로봇이 로컬에서 한다. 추종만 예외로 둘 이유가 없다.
- 서버가 죽어도 로봇의 안전 동작(정지·회피)은 살아있어야 한다.

FMS 가 맡는 것은 **① 시작 승인 ② 추종 중이라는 상태 관제** 둘뿐이다.

### 3. GUI 는 `robot_id` 를 셸 환경변수로 주입받는다

`libi_modes` 의 `FSM_ROBOT_ID`, `robot_agent` 의 `.env` 와 같은 관습을 따른다. `gui.sh` 가
`ROBOT_ID`/`FMS_URL` 을 넣어 기동하고, `ROS_DOMAIN_ID` 는 셸에 이미 설정된 값을 그대로 쓴다
(실물은 로봇마다 도메인이 고정이라 스크립트가 정하면 안 된다 — `laptop.sh` 와 같은 원칙).

> FMS `GET /api/robots` 로 조회하는 방식은 버렸다. 그 엔드포인트는 관리자 JWT 를 요구해서
> 부팅 스크립트가 토큰을 들고 있어야 하고, FMS 가 꺼져 있으면 GUI 자체가 안 뜬다.

## 구현

### FMS — 승인 기록(grant)

`app/routers/admin_follow.py` 는 승인 여부만 응답하고 **아무 기록도 남기지 않았다.** 이게
실제 구멍이었다: 추종 제어가 FSM 을 거치지 않으므로 **추종 중인 로봇도 FSM 상으로는 계속
IDLE/PATROL 로 보인다.** `fsm_link` 만 봐서는 관제가 추종 중인 걸 알 방법이 없다.

- `POST /request` — 기존 상태 검증(IDLE/PATROL 만, ERROR·수신끊김 거부)에 더해, **중복 승인
  거부**와 **grant 기록**(robot_id → granted_at)을 추가.
- `POST /release` — grant 삭제. 로봇 상태를 검사하지 않는다(종료는 언제나 받아준다).
- `GET /status` — 살아있는 grant 목록. 각 grant 에 `state_stale`(로봇 FSM 수신 끊김 여부)을
  `fsm_link` 에서 읽어 함께 실어 보낸다.

모듈 docstring 의 "자체 캐시를 두면 로봇의 실제 상태와 조용히 어긋난다" 와 충돌하지 않는다.
grant 는 로봇 상태의 사본이 아니라 **FMS 자신의 사실**("내가 이 로봇에게 언제 승인해줬다")이다.
로봇 상태는 여전히 `fsm_link` 에서만 읽는다.

**한계(의도적):** 프로세스가 죽어 `/release` 가 안 오면 grant 가 남는다. 만료를 걸지 않은 건
갱신 신호(heartbeat)가 없어 아무 값이나 정하면 멀쩡한 추종을 끊게 되기 때문이다. 대신
`granted_at`/`state_stale` 을 그대로 노출해 관제가 판단하도록 했다.

### libi_gui — 실제 HTTP 승인 요청

- `CMakeLists.txt`: `Qt5::Network` 추가.
- `RobotController`: `ROBOT_ID`/`FMS_URL` 을 기동 시 읽고, 기동 로그에
  `robot_id=... domain=... fms=...` 를 남긴다(어느 로봇 패널인지 확인용).
- `startAdminFollow()`: 비동기 POST → **`accepted=true` 일 때만** 추종 시작.
  거부·통신 실패 모두 시작 안 함(**fail-closed**). 연타 중복 요청은 `m_followPending` 으로 차단.
- `stopAdminFollow()`: 로컬 추종을 **먼저** 멈추고 release 를 보낸다(**fail-open**) — 관제
  서버가 죽었다고 추종을 못 멈추는 편이 더 위험하다. 보고 실패는 로그로만 남긴다.
- `gui.sh`: `./gui.sh pinky3` 형태의 런처.

## 이번 범위 밖 (다음 단계)

- **`follower_perception` 프로세스 자동 기동.** 승인 후에도 `pi.sh`/`laptop.sh` 를 사람이
  직접 실행한다. pm2 온디맨드 start/stop 으로 바꾸는 건 다음 라운드
  (`robot-hw`/`nav2`/`robot_agent` 가 이미 pm2 에 등록돼 있어 같은 방식으로 확장 가능).
- **FMS→로봇 명령 전달.** `admin_follow.py` 의 `TODO(transport)` — 승인 시 로봇 blackboard 의
  `active_command` 를 설정하는 호출은 미션 PC 도메인/브릿지 확정 후.
- **관제 프론트엔드.** `GET /status` 를 읽어 표시하는 화면은 아직 없다(API 만 준비됨).
- **`libi_gui` 의 나머지 ROS2-SEAM 스텁.** 주행·관절·상태 구독 등은 여전히 목 데이터.

## 검증 결과

| 대상 | 방법 | 결과 |
|---|---|---|
| FMS 승인 정책·grant | `pytest tests/test_admin_follow.py` (18케이스) | 통과 |
| FMS 백엔드 전체 | `pytest tests/` (106케이스) | 통과 |
| 실제 HTTP 왕복 | uvicorn 기동 후 curl 로 request→status→중복→release→재요청 | 통과 |
| GUI 승인 클라이언트 | `test_admin_follow_client` (실 FMS 상대, 9케이스) | 통과 |
| GUI 빌드 | `cmake --build` (테스트 ON/OFF 양쪽) | 통과 |
| libi_modes 회귀 | `pytest test/` (114케이스) | 통과 |

실기 검증(실제 로봇 + 카메라 + 추종 동작)은 하드웨어가 필요해 남아 있다.
