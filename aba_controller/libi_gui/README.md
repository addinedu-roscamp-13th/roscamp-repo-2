# libi_gui

**Libi(리비)** 도서관 사서 로봇의 **온보드 터치패널 GUI** (= System Architecture 상의 `Libi GUI`, Libi Drive Board 탑재).

- **스택:** C++ / Qt 5.15 (Qt Quick · QML) / CMake
- **대상:** 풀스크린 터치 패널 (태블릿/터치 디스플레이, 입력은 단순 탭/클릭)
- **언어:** 한국어 UI

> 버전 선택 근거: 빌드 환경(Ubuntu 24.04)에 Qt 5.15 LTS가 완비(Qt6 미설치)되어, 별도 설치 없이 가장 안정적으로 빌드·실행 가능. Qt5는 `ShaderEffect` 등에서도 단순.

## 기능 (로봇 터치패널 범위)
1. 대기/홈 — 인사, **표정(감정) 얼굴**, 순찰 표시
2. 친밀감 인터랙션 — 손인사 👋 / 배꼽인사 🙇 (SR-17)
3. **길잡이** — 목적지 선택 → 안내 중 지도·남은거리·상태 (SR-11, 시나리오 문구 반영)
4. **검색** — 도서/시설 검색 → 위치 지도 표시 (SR-09)
5. **추천** — 목적·관심분야 기반 도서 추천 (SR-05)
6. 작업/안내 **상태 표시** (SR-14)
7. **비상정지** — 즉시 정지·전 명령 무시, 관리자만 해제 (SR-20)
8. **관리자 모드** (PIN) → **수동조작**: 주행(D-pad)·팔 관절·주변장치 + 로그 (SR-21)

## 빌드 & 실행
```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
./gui.sh pinky3           # 어느 로봇의 패널인지 지정해서 기동 (아래 참조)
```

### 어느 로봇의 패널인지 지정 — `gui.sh`

`./build/libi_gui` 를 직접 실행해도 화면은 뜨지만, **관리자 추종은 동작하지 않는다** —
FMS 승인 요청에 실을 `robot_id` 를 모르기 때문이다. `gui.sh` 가 그 값을 환경변수로 넣어준다.

```bash
./gui.sh pinky3                                   # robot_id 만
FMS_URL=http://192.168.0.9:9001 ./gui.sh pinky3   # 관제 서버가 원격일 때
```

| 환경변수 | 설명 |
|---|---|
| `ROBOT_ID` | FMS 승인 요청의 키. **`pi.sh` 의 `FSM_ROBOT_ID` 와 같은 값이어야 한다** — 다르면 FMS 가 "알 수 없는 로봇"으로 거부한다. |
| `FMS_URL` | 관제 서버 주소 (기본 `http://127.0.0.1:9001`). 로봇에서 띄울 땐 실제 FMS 주소로 줘야 한다. |
| `PERCEPTION_URL` | 추종 화면이 붙을 AI 서버의 `perception_server` (기본 `127.0.0.1:5007`). **로봇이 아니라 별도 머신**이므로 기본값이 맞는 경우는 거의 없다. |
| `ROS_DOMAIN_ID` | `gui.sh` 가 정하지 않는다 — 실물은 로봇마다 도메인이 고정(87/88/89)이라 셸에 이미 설정된 값을 그대로 쓴다(`pi.sh` 와 같은 원칙). GUI 가 직접 쓰진 않지만 기동 로그에 함께 찍혀서 어느 로봇 패널인지 확인할 수 있다. |

기동하면 관리자 화면 로그에 `robot_id=... domain=... fms=...` 한 줄이 남으므로, 패널이
어느 로봇 것으로 떴는지 거기서 확인하면 된다.

### 화면 캡처(검증용)
각 화면을 순회하며 PNG로 저장 (live 디스플레이 필요):
```bash
./build/libi_gui --shots /tmp/libi_shots
```

### 테스트
승인 응답에 따라 추종을 시작할지 가르는 코드와, 영상 스트림 파싱은 눈으로 읽고 넘길 수
없어 별도 테스트가 있다.

```bash
cmake -S . -B build -DLIBI_GUI_TESTS=ON && cmake --build build -j

./build/test_admin_follow_client http://127.0.0.1:9001   # FMS 가 떠 있어야 함
./build/test_perception_client 127.0.0.1:5007            # perception_server 가 떠 있어야 함
```

`LIBI_GUI_TESTS` 는 기본 OFF라, 로봇에 올리는 빌드에는 포함되지 않는다.

`perception_server` 는 Pi 없이도 띄울 수 있어서 영상 경로만 따로 확인할 수 있다:

```bash
cd aba_ai_service/follower_perception
python scripts/perception_server.py --test-pattern --port 5007   # 또는 --camera 0
```

## 구조
```
libi_gui/
├── CMakeLists.txt
├── gui.sh                   # robot_id/FMS 주소를 넣어 기동 (어느 로봇 패널인지 지정)
├── resources.qrc            # 모든 QML/JS 번들
├── src/
│   ├── main.cpp             # 엔진 + controller/perception 등록 + --shots 캡처
│   ├── RobotController.h/.cpp   # 백엔드 파사드(QObject→QML)
│   ├── PerceptionClient.h/.cpp  # AI 서버 영상 스트림 + 등록/해제 (TCP)
├── tests/                   # LIBI_GUI_TESTS=ON 일 때만 빌드
│   ├── test_admin_follow_client.cpp   # FMS 추종 승인 클라이언트
│   └── test_perception_client.cpp     # 영상 스트림 파싱 · 라이다 · 명령 전송
└── qml/
    ├── Main.qml             # 윈도우/네비/비상정지/토스트
    ├── Style.js             # 디자인 토큰(파스텔 테마)
    ├── components/          # RobotFace, BigButton, Card, MapView, TopBar ...
    └── screens/             # Home/Guide/Search/Recommend/AdminLogin/AdminControl/Follow
```

## 관리자 추종 — 유일하게 실제 통신이 붙은 경로

`startAdminFollow()` / `stopAdminFollow()` 는 목이 아니라 **FMS 와 실제 HTTP 로 통신한다**
(`POST /api/robot/admin-follow/request` · `/release`). 나머지 기능은 아직 목이다.

- **승인 없이는 시작하지 않는다.** 거부·통신 실패 모두 "시작 안 함"으로 떨어진다(fail-closed).
  관제가 모르는 추종이 도는 것이 이 승인 절차가 막으려는 상황이다.
- **종료는 반대로 fail-open** — FMS 응답을 기다리지 않고 로컬 추종을 먼저 멈춘다. 관제 서버가
  죽었다고 추종을 못 멈추는 편이 훨씬 위험하다. 해제 보고가 실패하면 로그로만 남는다.
- 추종 제어 자체(Detection→cmd_vel)는 GUI 도 FMS 도 거치지 않고 `ai_service` ↔ 로봇 직결로
  돈다. GUI 가 FMS 에 요청하는 이유는 **관제가 "이 로봇이 지금 추종 중"임을 알아야** 하기
  때문이다 — 승인 시 로봇을 WORKING 으로 옮겨 다른 태스크가 배차되지 않게 한다.

### 추종 화면 (`FollowScreen.qml` + `PerceptionClient`)

승인되면 AI 서버의 `perception_server`(TCP 5007)에 붙어 영상을 띄우고 「등록/해제」를 보낸다.

```
서버 -> GUI : [4바이트 빅엔디언 길이][페이로드]     페이로드 = JPEG 또는 "LIDR <8개 정수>"
GUI -> 서버 : "register\n" / "reset\n"
```

**bbox·OWNER 라벨·3등분 방향 가이드선·reid/hsv 상태값은 서버가 JPEG 안에 이미 그려서** 보낸다
(`perception_server.draw_overlay`). GUI 는 그리지 않는다 — 같은 화면을 두 곳에서 그리면 반드시
어긋난다. 원본 `scripts/viewer.py` 와 화면 내용이 같은 이유이기도 하다(키보드 `r`/`x` 가
터치 버튼으로 바뀐 것만 다르다).

`LIDR` 프레임은 같은 스트림에 섞여 오므로 접두사로 갈라 8방향 거리로 파싱하고, 영상의 3등분
구역과 같은 순서(좌/정면/우)로 아래에 표시한다. 서버가 `--lidar-ros` 로 떴을 때만 값이 온다.

## ROS2 연동 (TODO)
실제 시스템에서 Libi GUI 의 **주 통신 상대는 `Libi Drive Controller` (ROS2 / DDS)** 이다
(관리자 추종만 위와 같이 FMS HTTP 를 쓴다). 나머지 `RobotController` 는 동작 확인용
**목(mock) 데이터**로 구현되어 있고, ROS2 연결 지점은 `RobotController.cpp` 의
슬롯/시그널(`// ROS2-SEAM` 주석)이다. 연동 시:

- `drive()/setJointN()/setGripper()/setLed()` → cmd_vel·관절·주변장치 토픽 **publish**
- 상태 프로퍼티(`robotState/battery/guidePhase/distanceToGoal/taskStatus`) → 컨트롤러 토픽 **subscribe** 후 갱신
- `searchBooks()/recommend()/facilities()` → Drive Controller 경유 ABA Service 조회로 대체

> 문서(기획/SRS/아키텍처)에 ROS2 토픽·서비스 이름이 정의돼 있지 않아, 위 인터페이스는 GUI 요구에 맞춰 잠정 정의한 것이다. 실제 메시지 계약 확정 시 맞추면 된다.
