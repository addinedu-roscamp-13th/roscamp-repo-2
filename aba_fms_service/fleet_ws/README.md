# fleet_ws — 배차 · 교통 FMS 코어

`arte_libi_fleet` 레포에서 **FMS 두뇌만** 가져온 ROS2(C++) 워크스페이스다.
"누가 이 일을 할지"(배차)와 "주행 중 서로 안 부딪히게"(교통)를 담당한다.

```
aba_fms_service/
├── backend/     FastAPI — 웹 API, FSM+BT 패널, 로봇 텔레메트리
├── frontend/    React SPA
└── fleet_ws/    ← 여기. C++ ROS2 워크스페이스 (배차·교통)
```

`backend/` **안이 아니라 형제 폴더**다. C++ ament_cmake 워크스페이스라 파이썬 패키지 안에 두면
빌드·배포 단위가 섞인다.

## 빌드 · 실행

```bash
source /opt/ros/jazzy/setup.bash
cd aba_fms_service/fleet_ws
colcon build
colcon test --packages-select libi_fleet && colcon test-result --verbose   # 3 tests

source install/setup.bash
ros2 run libi_fleet fleet_node --ros-args \
  -p navgraph_file:=$PWD/maps/library/new_map.navgraph.yaml
```

기동하면 아래 서비스가 뜬다 (실측 확인):

| 서비스 | 용도 |
|---|---|
| `/fms/submit_task` | 태스크 투입 → 배차 |
| `/fms/set_plugins` | 배차·교통 알고리즘 런타임 교체 |
| `/fms/set_robot_mode` | 로봇 모드 변경 (⚠️ 아래 「충돌」 참고) |
| `/fms/set_battery` | 로봇 배터리 값 설정 (sim 용) |
| `/fms/reload_navgraph` | 정점 편집 반영 |

**필요 의존성은 이미 전부 설치돼 있다** — `rmf_fleet_msgs` 3.3.1, `pluginlib`, `libyaml-cpp-dev`.

## 무엇을 가져왔고 무엇을 뺐나

| | |
|---|---|
| **가져온 것** | `libi_fleet`(fleet_node, navgraph, patrol_cycle, 플러그인 2종), `libi_fleet_msgs`, `config/algo_params.yaml`, `maps/library/` |
| **뺀 것** | `service/aba_service`(FastAPI) — `aba_fms_service/backend`가 이미 그 역할 |
| | `controller/`(drive·handy) — `aba_controller/`에 이미 있음 |
| | `libi_gui` — `aba_controller/libi_gui`에 이미 있음 |
| | `demos/`, `scripts/`(sim slotcar 등) — 필요해지면 그때 추가 |

원본 레포는 손대지 않았다. 여기 있는 건 복사본이며, 상류가 바뀌면 수동으로 반영해야 한다.

## 다른 컴포넌트와의 층 구분

```
libi_fleet (여기)          누가 할지 · 어디로 갈지    배차 Auction / 교통 CbsTraffic
     │ Navigate / PerformAction (action)
     ▼
libi_modes (미션 PC)       그 로봇이 어떻게 행동할지   8상태 FSM + 상태별 BT
     │ cmd_vel / 팔 명령
     ▼
robot_agent · ros_ws       실제 하드웨어
```

## 플러그인 (배차·교통) — `fleet_node --ros-args -p dispatcher_plugin:=… -p traffic_plugin:=…`

런타임 교체는 `/fms/set_plugins`, 부팅 지정은 위 파라미터. `plugins.xml` 참고.

| base | 플러그인 | 설명 |
|---|---|---|
| DispatcherBase | `libi_fleet::Auction` | 배차 — Dijkstra 최저 경로비용 입찰 승리 (기본) |
| TrafficBase | `libi_fleet::CbsTraffic` | 교통 — CBS + 가중 Space-Time A* 시간표 계획 + 실행 게이트 (**기본**). 계획이 밀리면 `ReservationDeadlock` 반응형으로 자동 강등 |
| TrafficBase | `libi_fleet::ReservationDeadlock` | 교통 — 노드 예약 + wait-for DFS 교착감지 (반응형 폴백, 단독 사용도 가능) |
| TrafficBase | `libi_fleet::GrantAllTraffic` | ⚠️ **진단 전용** — 항상 GRANT, 충돌회피·교착감지 **없음**. 실사용 금지 |

`GrantAllTraffic` 은 "명령 하나에 로봇이 다 같이 움직이는" 현상이 교통관제 셔플 때문인지
가리려고 만든 도구다. traffic 을 이걸로 껐는데도 다 움직였다 → **원인은 교통관제가 아니라
서버측 fleet-wide 순찰(`patrol` 파라미터, 기본 `true`)** 임이 드러났다. idle 로봇까지
외곽 루프를 무한 순찰하므로, "배차한 로봇만 움직이게" 하려면 `-p patrol:=false` 로 띄운다
(교통관제·존과는 별개 레이어).

**명령 인터페이스는 이미 맞물린다.** `libi_fleet`은 로봇당 Drive와만 통신하며
`navigate` · `perform_action`을 보내는데, `libi_modes`의 `WorkingBranch`가 정확히 그
`active_command` 값들을 dispatch한다 (`NavigationExec`는 `{navigate, dock}`,
`ArmExec`는 `{perform_action}`). 붙일 때 인터페이스를 새로 만들 필요는 없다.

## 상태 어휘 — `libi_modes`에 맞춤 (완료)

**상태의 소유자는 `libi_modes`다.** FMS는 그 값을 관측할 뿐 자기만의 모드 어휘를 두지 않는다.
두 벌을 두면 "FMS는 IDLE이라 믿는데 로봇은 WORKING"인 상황이 조용히 생긴다.

원본(`arte_libi_fleet`)은 `PATROL|IDLE|STOP|CHARGE` 4종이었고, 다음과 같이 대응시켰다:

| 원본 | → `libi_modes` | 이유 |
|---|---|---|
| `STOP` | `ERROR` | "스스로 움직이지 않는다" — 다른 로봇이 우회할 장애물로 표시 |
| `CHARGE` | `RETURNING` | 충전소로 복귀 중 (교통 우선순위 tier 2) |
| `PATROL` | `PATROL` | 이름·뜻 그대로 |
| `IDLE` | `IDLE` | 이름·뜻 그대로 |
| — | `WORKING` `INTERACTING` `SECURITY_PATROL` `CHARGING` | 원본에 없던 상태 — 전부 배차 후보에서 제외 |

FMS는 8종 상태에서 판단에 필요한 것만 **파생**한다 (`fleet_node.cpp`):

```cpp
is_dispatchable(state)  // state ∈ {IDLE, PATROL}  → 배차 후보
is_immobile(state)      // state == ERROR          → 영구 장애물, 우회 대상
mode_of(robot) == "RETURNING"  // 교통 우선순위 tier 2
```

배차 후보를 `{IDLE, PATROL}`로 한정한 근거는 INSTRUCTION.md의 배차 우선순위(1순위 PATROL,
2순위 IDLE)와 각 상태의 이탈 금지 규칙이다 — `INTERACTING`(응대 중), `SECURITY_PATROL`(야간 근무),
`RETURNING`/`CHARGING`(충전 동선), `ERROR`(고장)는 새 일을 받으면 안 된다.

**실측 확인** — 8종 전부 `ok=True`, 옛 어휘 `STOP`/`CHARGE`와 오타는 `bad_mode`로 거부.

> ⚠️ `/fms/set_robot_mode`는 **sim·디버그용**이다. 운영 중 상태 변경은 FSM 패널의
> `RequestTransition` 경로로 해야 한다. 이 서비스로 직접 바꾸면 로봇의 실제 FSM과 어긋난다.

## 아직 안 붙인 것

- **`backend` ↔ `fleet_node` 브리지** — FastAPI에서 `/fms/submit_task`를 호출하는 경로가 아직 없다.
  `backend/app/fleet_telemetry.py`가 쓰는 임베디드 rclpy 노드 패턴을 그대로 따르면 된다
  (백그라운드 스레드 + 자체 `rclpy.Context` + 도메인 지정).
- **도메인 배치** — `fleet_node`를 어느 `ROS_DOMAIN_ID`에서 돌릴지 미정.
  FMS 텔레메트리는 86, 미션 PC는 미정(`__MISSION_DOMAIN__`) 상태다.
- **기존 `fleet_coordinator.py`(근접 자동정지, 450줄)와의 역할 정리** — `ReservationDeadlock` 교통
  플러그인이 같은 문제를 더 정교하게 푼다(노드 예약 + 데드락 DFS + 우선순위 사다리).
  둘 다 살려두면 서로 반대로 명령할 수 있다.
- **`libi_modes` → FMS 상태 전달 경로** — 지금은 `/fms/set_robot_mode`로 외부에서 밀어 넣어야 한다.
  로봇이 발행하는 `FsmState`를 `fleet_node`가 직접 구독해 `robot_mode_`를 갱신하도록 붙이면
  수동 개입 없이 동기화된다. (`RobotState.msg`에 상태 필드를 추가하거나 `FsmState`를 구독)
