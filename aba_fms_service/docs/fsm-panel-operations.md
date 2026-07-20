# FSM + BT 패널 운영 메모

관제 웹의 「로봇 제어 > FSM + BT」 화면(`/admin/fsm`)을 띄우고 유지하는 방법.

## 구성 요소

```
로봇 도메인 (libi_modes, sim=90 / 실기 88·89·87)
   │  /libi/fsm_state, /libi/bt_snapshot, /libi/fsm_transition_result   (로봇 -> 86)
   │  /libi/fsm_transition_request                                       (86 -> 로봇, reversed)
   ▼
ros2 domain_bridge  (FMS 서버에서 실행)
   ▼
도메인 86  ──▶  app/fsm_link.py (백그라운드 rclpy 스레드, 캐시)
                   ▼
                app/routers/fsm.py  ──WebSocket push──▶  브라우저 패널
```

## 1. 도메인 브릿지 기동

**미션 PC 를 위한 별도 도메인은 없다.** `libi_modes` 는 로봇과 **같은 도메인**에서 돈다
(sim=90, 실기는 로봇별 88/89/87). 배터리·명령이 그 도메인에 있으니 그래야 그대로 주고받는다.

그래서 FSM 토픽은 **기존 로봇 브릿지에 얹혀 있다** — 새 설정 파일이 없다.
`domain_bridge_{sim,pinky1,pinky2,pinky3}.yaml` 각각에 아래 4개가 들어 있다:

| 토픽 | 방향 |
|---|---|
| `libi/fsm_state` | 로봇 → 86 (상태 JSON) |
| `libi/bt_snapshot` | 로봇 → 86 (BT 트리 JSON) |
| `libi/fsm_transition_result` | 로봇 → 86 (전이 결과) |
| `libi/fsm_transition_request` | **86 → 로봇** (`reversed: True`) |

payload 안에 `robot_id` 가 들어 있어 서버에서 토픽을 로봇별로 나누지 않는다 —
`fsm_link` 가 `robot_id` 로 캐시를 가른다. 로봇이 여러 대여도 같은 토픽에 모인다.

```bash
source /opt/ros/jazzy/setup.bash
# 로봇별로 하나씩, 서버에서만 실행 (sim.sh 는 이 브릿지를 자동으로 띄운다)
ros2 run domain_bridge domain_bridge aba_fms_service/config/domain_bridge_pinky1.yaml
```

### 브릿지 동작 확인 (검증된 절차)

```bash
source /opt/ros/jazzy/setup.bash
ros2 daemon stop           # 오래된 discovery 캐시가 오탐을 만든다 — 먼저 정리
ros2 run domain_bridge domain_bridge aba_fms_service/config/domain_bridge_sim.yaml &

# 정방향: 로봇(90) -> FMS(86)
ROS_DOMAIN_ID=90 ros2 topic pub /libi/fsm_state std_msgs/msg/String '{data: "RELAY_TEST"}' -r 5 &
sleep 8                    # discovery 에 시간이 필요하다 — 짧으면 "not published yet" 오탐
ROS_DOMAIN_ID=86 ros2 topic echo /libi/fsm_state std_msgs/msg/String --once

# 역방향: FMS(86) -> 로봇(90)
ROS_DOMAIN_ID=86 ros2 topic pub /libi/fsm_transition_request std_msgs/msg/String '{data: "REVERSE_TEST"}' -r 5 &
sleep 8
ROS_DOMAIN_ID=90 ros2 topic echo /libi/fsm_transition_request std_msgs/msg/String --once
```

두 방향 모두 개발 머신에서 실측 확인했다(도메인 90↔86). **8초 미만으로 기다리면
discovery 가 안 끝나 실패한 것처럼 보이니 주의** — 실제로 이 함정에 한 번 걸렸다.

## 2. 환경 변수

`app/fsm_link.py` 가 읽는 값들. 기본값으로 두면 위 브릿지 설정과 맞는다.

| 변수 | 기본값 | 용도 |
|---|---|---|
| `LIBI_FSM_DOMAIN_ID` | `86` | FMS 가 구독하는 쪽 도메인 |
| `LIBI_FSM_STATE_TOPIC` | `/libi/fsm_state` | 상태 브로드캐스트 |
| `LIBI_FSM_TREE_TOPIC` | `/libi/bt_snapshot` | py_trees 스냅샷 |
| `LIBI_FSM_CMD_TOPIC` | `/libi/fsm_transition_request` | 전이 요청 발행 |
| `LIBI_FSM_RESULT_TOPIC` | `/libi/fsm_transition_result` | 전이 결과 수신 |

## 3. 링크 살아있는지 확인

```bash
source /opt/ros/jazzy/setup.bash
ROS_DOMAIN_ID=86 ros2 topic hz /libi/fsm_state
```

FMS 로그에 아래가 찍히면 구독이 시작된 것이다:

```
[fsm_link] ROS 구독 시작 (domain 86, state /libi/fsm_state)
```

대신 `[fsm_link] 비활성 — ROS2 링크 없이 진행합니다: ...` 가 찍히면 rclpy 를 못 불러온
것이다. 이 경우 서비스는 정상 기동하지만 패널은 계속 "수신 대기"만 보여준다.

## 4. 화면에 뜨는 상태 표시의 의미

| 표시 | 의미 |
|---|---|
| `수신 대기` | 해당 로봇의 상태를 한 번도 받지 못함 (브릿지 미기동/FSM 노드 미기동/로봇 ID 불일치) |
| `(수신 끊김)` | 받은 적은 있으나 10초(`FRESH_SEC`) 넘게 갱신 없음 |
| `FSM 링크에 연결할 수 없습니다` | 전이 요청 시 구독자가 0 — 브릿지가 안 떠 있다 |

## 5. 로봇 식별자

식별자 공간이 세 가지라 헷갈리기 쉽다:

| 공간 | 예시 | 쓰는 곳 |
|---|---|---|
| DB PK | `1` | `rc_robots.id`, 프론트 `useActiveRobotId()` |
| 로봇 이름 | `Pinky-1` | `rc_robots.name`, 프론트 `useActiveRobotName()` |
| **브릿지 키** | `pinky1` | domain_bridge 토픽 접두사, `fleet_status()` 응답, **FSM API 정본** |

FSM 패널은 **이름**을 보내고, 백엔드 `resolve_robot_id()` 가 브릿지 키로 바꾼다. 매핑은
`fleet_telemetry._ROBOT_NAME_TO_KEY` 를 그대로 재사용한다(복사본을 만들지 않는다).

**확정됨** — `libi_modes` 는 브릿지 키를 그대로 발행한다. `sim.sh` 는 `robot_id:=pinkySim`,
`pi.sh` 는 `robot_id:=pinky1` 을 넘긴다(`FSM_ROBOT_ID` 로 덮어쓸 수 있다).
다르게 발행하면 패널이 스냅샷을 찾지 못하므로 브릿지 접두사와 반드시 같아야 한다.

## 6. 프로덕션 서버 전제

`backend/requirements.txt` 에는 `rclpy` 가 없다. `fleet_telemetry.py` 와 마찬가지로
**시스템 ROS2 Jazzy 설치**(`/opt/ros/jazzy`)를 쓰기 때문이다. FMS 프로덕션 호스트에
ROS2 Jazzy 가 설치되어 있는지 반드시 확인할 것 — 없으면 위 "비활성" 경로로 빠진다.

## 7. 테스트

```bash
cd aba_fms_service/backend
.venv/bin/pip install -r requirements-dev.txt      # pytest (런타임 의존성 아님)
.venv/bin/python -m pytest tests/ -q
```

⚠️ **시스템 파이썬으로 돌리면 안 된다.** 시스템에는 sqlalchemy 가 없어서 라우터 테스트가
통째로 skip 되는데, 출력만 보면 통과한 것처럼 보인다.
