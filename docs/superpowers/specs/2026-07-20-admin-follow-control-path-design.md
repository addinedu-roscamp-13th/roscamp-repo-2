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
(실물은 로봇마다 도메인이 고정이라 스크립트가 정하면 안 된다 — `pi.sh` 와 같은 원칙).

> FMS `GET /api/robots` 로 조회하는 방식은 버렸다. 그 엔드포인트는 관리자 JWT 를 요구해서
> 부팅 스크립트가 토큰을 들고 있어야 하고, FMS 가 꺼져 있으면 GUI 자체가 안 뜬다.

## 구현

### FMS — 승인 기록(grant)

`app/routers/admin_follow.py` 는 승인 여부만 응답하고 **아무 기록도 남기지 않았다.** 이게
실제 구멍이었다: 추종 제어가 FSM 을 거치지 않으므로 **추종 중인 로봇도 FSM 상으로는 계속
IDLE/PATROL 로 보인다.** `fsm_link` 만 봐서는 관제가 추종 중인 걸 알 방법이 없다.

- `POST /request` — 기존 상태 검증(IDLE/PATROL 만, ERROR·수신끊김 거부)에 더해, **중복 승인
  거부**, **grant 기록**(robot_id → granted_at), **로봇을 WORKING 으로 전이**.
- `POST /release` — grant 삭제 + 로봇을 IDLE 로 복귀. 종료 자체는 언제나 받아준다.
- `GET /status` — 살아있는 grant 목록. 각 grant 에 `state_stale`(로봇 FSM 수신 끊김 여부)을
  `fsm_link` 에서 읽어 함께 실어 보낸다.

#### WORKING 전이 — 실패하면 승인을 무른다

`IDLE`/`PATROL` → `WORKING` 은 전이표의 정식 간선(`task_assigned`)이라 force 가 필요 없다
(`ACCEPTING_STATES` 가 딱 그 둘인 이유이기도 하다). 전이가 실패하면 grant 를 롤백하고 승인
자체를 거부한다(**fail-closed**) — 로봇이 IDLE 로 남으면 관제가 유휴로 보고 다른 태스크를
배차할 수 있는데 실제로는 사람을 따라다니는 중이라 충돌한다. 승인 기록과 로봇 상태는 같이
움직이거나 둘 다 안 움직여야 한다.

#### 복귀 판단은 캐시가 아니라 "FMS 가 옮겼다는 사실" 기준

해제 시 "지금 WORKING 이면 되돌린다"로 판단하면 **로봇이 WORKING 에 갇힌다.** `fsm_link`
캐시는 로봇→브릿지 지연만큼 뒤처지므로, 승인 직후 바로 해제하면 캐시엔 아직 IDLE 이 남아
있어 복귀 전이를 건너뛴다. (실 서버 검증에서 실제로 재현됐다.)

대신 **로봇이 스스로 들어간 상태**(`ERROR`/`RETURNING`/`CHARGING`)일 때만 건너뛴다 — 에러는
`error_code` 확인 없이 지우면 안 되고, 복귀·충전은 배터리가 떨어져 로봇이 알아서 하는
일이라 방해하면 안 된다. 그 외에는 복귀를 시도하고, 실패해도 `released` 는 유지한다(기록을
남겨두면 다시 추종을 시작할 수 없게 되는데, 상태가 안 돌아간 것보다 나쁘다).

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

### libi_gui — 추종 화면 (`FollowScreen.qml` + `PerceptionClient`)

승인되면 AI 서버의 `perception_server`(TCP 5007, `PERCEPTION_URL` 로 주입)에 붙어 영상을
띄우고 「등록/해제」를 보낸다. 프로토콜은 `[4바이트 빅엔디언 길이][페이로드]`이고, 페이로드는
JPEG 이거나 `LIDR <8개 정수>` 라이다 텔레메트리다 — 한 스트림에 섞여 오므로 접두사로 가른다
(가르지 않으면 라이다 프레임을 JPEG 으로 디코딩하려다 실패해 화면이 깜빡인다).

**bbox·OWNER 라벨·3등분 방향 가이드선·reid/hsv 상태값은 서버가 JPEG 안에 이미 그려서** 보낸다
(`perception_server.draw_overlay`). GUI 는 그리지 않는다 — 같은 화면을 두 곳에서 그리면 반드시
어긋난다. 덕분에 원본 `scripts/viewer.py` 와 화면 내용이 같고, 키보드 `r`/`x` 가 터치 버튼으로
바뀐 것만 다르다. 라이다 값만 별도 데이터로 오므로 영상의 3등분 구역과 같은 순서(좌/정면/우)로
아래에 표시한다.

화면 전환은 QML 이 `controller.following` 을 보고 처리한다 — `RobotController` 와
`PerceptionClient` 가 서로를 모르게 유지하려는 것이다. 스트림은 화면이 살아있는 동안만 받는다.

## 이번 범위 밖 (다음 단계)

- **`follower_perception` 프로세스 자동 기동.** 승인 후에도 그쪽 `pi.sh`/`laptop.sh` 를 사람이
  직접 실행한다. pm2 온디맨드 start/stop 으로 바꾸는 건 다음 라운드
  (`robot-hw`/`nav2`/`robot_agent` 가 이미 pm2 에 등록돼 있어 같은 방식으로 확장 가능).
- **관제 프론트엔드.** `GET /status` 를 읽어 표시하는 화면은 아직 없다(API 만 준비됨).
- **`libi_gui` 의 나머지 ROS2-SEAM 스텁.** 주행·관절·상태 구독 등은 여전히 목 데이터.

## 검증 결과

| 대상 | 방법 | 결과 |
|---|---|---|
| FMS 승인 정책·grant·WORKING 전이 | `pytest tests/test_admin_follow.py` (30케이스) | 통과 |
| FMS 백엔드 전체 | `pytest tests/` (118케이스) | 통과 |
| 실제 HTTP 왕복 | uvicorn 기동 후 curl 로 request→status→중복→release→재요청 | 통과 |
| 캐시 지연 시 복귀 전이 | 승인 직후 즉시 해제 → `WORKING`/`IDLE` 두 전이 모두 발행되는지 | 통과 (이 검증이 위 버그를 잡음) |
| GUI 승인 클라이언트 | `test_admin_follow_client` (실 FMS 상대, 9케이스) | 통과 |
| GUI 영상 스트림·라이다·명령 | `test_perception_client` (14케이스). 프레이밍은 레포의 실제 `frame_proto.send_frame` 을 쓰는 서버로 검증 | 통과 |
| 추종 화면 렌더링 | `--shots` 오프스크린 캡처 — 실시간 프레임·연결 배지·라이다 3분할 표시 확인 | 통과 |
| GUI 빌드 | `cmake --build` (테스트 ON/OFF 양쪽), `qmllint` | 통과 |
| libi_modes 회귀 | `pytest test/` (114케이스) | 통과 |

실기 검증(실제 로봇 + 카메라 + 추종 동작)은 하드웨어가 필요해 남아 있다.
