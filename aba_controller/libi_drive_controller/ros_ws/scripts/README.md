# scripts/

| 스크립트 | 용도 |
|---|---|
| `sim.sh` | Gazebo sim — gazebo·nav2·rviz·bridge·fleet-link·fsm 6창 자동 기동 |
| `pi.sh` | 실물 로봇 — hw·nav2·fleet-link·fsm·led 5창 (브릿지는 없음, 아래 참고) |
| `fsm-bt.sh` | `libi_modes` 미션 FSM만 단독 실행 (BT 고치면서 반복할 때) |
| `image-sender.sh` | 로봇 카메라 → UDP 로 AI 서버에 영상만 전송 (추종 영상 경로 단독 검증용, 아래 참고) |
| `kill.sh` | tmux 세션 정리 |

## 실물 로봇 체크리스트 — FMS 패널에 안 보일 때

**1. `FSM_ROBOT_ID`를 반드시 지정할 것.** 기본값이 `pinky1`이라, 안 주면 다른 로봇인데도
`pinky1`로 상태를 발행해버린다 — 브릿지·패널이 찾는 이름과 어긋나서 아무것도 안 보인다.

```bash
FSM_ROBOT_ID=pinky3 ./pi.sh
```

**2. `pi.sh`엔 도메인 브릿지가 없다.** 실물은 "로봇은 무수정" 원칙이라 브릿지를
로봇이 아니라 **서버**에서 띄운다 (sim은 `sim.sh`가 bridge 창까지 자동으로 켜줌 — 이 차이 때문에
sim에서 되던 걸 그대로 실물에 옮기면 이 단계만 빠진다).

브릿지 스크립트는 여기 없다 — 서버 담당이라 `aba_fms_service/scripts/` 에만 둔다.

```bash
# 서버(FMS 백엔드가 도는 머신)의 aba_fms_service/scripts 에서
./ros-domain-bridge.sh --check   # DB 에 그 로봇 domain_id 가 있는지 먼저 확인
./ros-domain-bridge.sh           # 있으면 기동
```

`--check`에서 로봇 이름이 안 뜨면 관제 패널에서 그 로봇을 먼저 등록(IP·`domain_id`)해야 한다.

**3. 도메인 번호가 실제 로봇 셸의 `ROS_DOMAIN_ID`와 DB `domain_id`가 같은지.** 다르면
브릿지가 엉뚱한 도메인을 구독해서 로봇을 못 찾는다.

## 상태·BT(`libi_modes`) 없이 실행 — `--no-fsm`

`sim.sh`/`pi.sh` 둘 다 마지막 fsm 창을 빼고 띄우는 옵션이 있다. 주행/nav2만 붙어서
테스트할 때(FSM이 아직 없어도 되거나, BT 쪽 변경과 분리해서 확인하고 싶을 때) 쓴다.

```bash
./sim.sh --no-fsm        # gazebo·nav2·rviz·bridge·fleet-link 만 (fsm 창 없음)
./pi.sh --no-fsm     # hw·nav2·fleet-link·led 만 (fsm 창 없음)
```

FSM은 나중에 `./fsm-bt.sh`로 따로 붙이면 된다(BT만 고치면서 반복할 때도 이 조합이 편하다 —
gazebo/nav2/hw는 계속 띄워둔 채 fsm 창만 Ctrl+C 후 재기동, 또는 아예 별도 창에서
`fsm-bt.sh`).

## 상태별 LED (`pinky_led`/`state_led`) — `pi.sh` 전용

`pi.sh`에만 `led` 창이 있다(`sim.sh`엔 없음) — `pinkyled` 모듈이 `rpi_ws281x`로 LED
스트립을 직접 잡고(실행 시 자동으로 sudo 재실행) 개발 PC/시뮬에는 하드웨어 자체가 없다.
`fsm_state`(`libi_modes`가 발행) 토픽을 구독해 상태별 색상·패턴을 켠다.

```bash
./pi.sh --no-led     # led 창 없이 (LED 코드 안 쓰거나 pinky_led 의 led_server 를 대신 쓸 때)
```

⚠️ 같은 패키지의 `led_server`(수동 LED 제어)와 `state_led`를 동시에 띄우면 안 된다 — 둘 다
LED 스트립을 단독 점유한다. 다른 데서 `led_server`를 이미 띄웠다면 `--no-led`로 빼고 실행할 것.

## 관리자 추종 영상 검증 — `image-sender.sh`

관리자 추종(`libi_perception`/`follower_perception`)은 `libi_modes` BT가 프로세스를 직접
띄우는 게 아니다 — BT는 tick마다 로직만 판단할 뿐 OS 프로세스 생명주기를 관리하지 않는다.
카메라 전송·추적·명령 각각이 **미리 켜져 있어야** 하는 별도 프로세스다.

`image-sender.sh`는 그중 카메라 전송(로봇 → AI 서버, UDP) 한 구간만 먼저 검증하는 용도다
(원본은 `aba_ai_service/follower_perception/scripts/camera_sender.py`).

```bash
./image-sender.sh <AI_SERVER_IP>
```

⚠️ `picamera2`는 시스템 파이썬 패키지라 반드시 시스템 `python3`로 실행한다(venv 아님) —
스크립트가 이미 그렇게 되어 있다.

이것만으로는 "추종이 잘 되는지" 확인이 안 된다 — 영상이 도착하는지만 본다. 실제 추종
전체를 보려면 반대편에서 `perception_server.py`가 받아 추적하고 `cmd_bridge.py`가 로봇에
명령을 다시 꽂아야 한다(지금은 `aba_ai_service/follower_perception/` 의 `pi.sh`+`laptop.sh`
조합이 이 전체 경로를 담당 — **여기 있는 `pi.sh`와는 별개로 단독 실행해야 한다.** 이름이 같아
헷갈리기 쉬운데, 둘 다 같은 `bringup_robot.launch.xml`을 띄우려 해서 동시 실행 시
하드웨어가 충돌한다).

나중에 카메라 전송을 pm2로 온디맨드 시작/정지하고 싶으면(추종 시작 신호에 맞춰서만 켜기),
이 스크립트 내용을 그대로 pm2 앱 `script:`로 옮기면 된다 — `robot-hw`/`nav2`/`robot_agent`가
이미 같은 방식으로 `ecosystem.config.js`에 등록돼 있다.
