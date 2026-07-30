# libi_perception — LIBI 사람 추종

관리자가 터치패널에서 추종을 시작하면, 로봇이 등록된 사람을 따라간다.
`arte_libi_perception/follower_control` 에서 포팅했고, 회복 순서를 py_trees 구조로 다시 표현했다.

## 두 가지 동작 + 얇은 스위치

```
FollowSwitch
├── TRACKING   → TrackingController   PID + LiDAR 회피 → /cmd_vel   (BT 아님)
├── SEARCHING  → recovery_bt          회복 순서를 트리 구조로 표현
└── ENDED      → 아무것도 안 함        세션 종료
```

**추종 동작을 BT로 만들지 않은 이유** — 20Hz로 도는 수치 제어 루프다. PID 출력에 LiDAR
회피를 얹어 속도를 내보내는 계산이지, 트리로 표현할 만한 결정 구조가 없다.
반대로 회복은 "무엇을 어떤 순서로 시도할 것인가"라서 트리가 맞다.

## 회복 BT

```
BT_Searching (Selector, memory=False)
├── AlignHeading      반대 캠에서 찾았다 → 180° 회전 (**추종 전용**, 래치되면 최우선)
├── PeekReacquired    반대 캠에서 찾았다 → 탐색 종료 (**길잡이 전용**, 회전 안 함)
├── CheckReacquired   정위치 캠에서 재발견 → SUCCESS (어느 구간에서든 즉시 인터럽트)
└── SearchPhases (Sequence, memory=True)
    ├── HoldFront          5초  정위치 캠  정지 — 잠깐 가려진 것뿐일 수 있다
    ├── HoldBack           5초  반대 캠    정지 — **돌지 않고 뒤를 본다**
    ├── SweepFrontOut      정위치 캠  0° → +90°
    ├── SweepFrontAcross   정위치 캠  +90° → −90°
    ├── SweepFrontHome     정위치 캠  −90° → 0° (원위치)
    ├── SweepBackOut       반대 캠    같은 왕복을 뒤를 보며
    ├── SweepBackAcross    반대 캠
    ├── SweepBackHome      반대 캠    원위치
    └── GiveUp             정지 + FAILURE
```

### [2026-07-28] 탐색 시퀀스 교체 (의도적 동작 변경)

요구: **앞 5초 → 뒤 5초 → 앞 보며 좌우 훑고 원위치 → 뒤 보며 같은 왕복.**
그 사이 앞캠에 보이면 즉시 재개, 뒷캠에 잡히면 즉시 180° 회전.

`Hold`(10초) · `PeekBack`/`PeekBack2`(2초) · `Scan1~3`(4초) · `Turn180`(~9초) 이
전부 사라지고 위 여덟 구간으로 대체됐다.

**핵심은 Selector 우선순위다.** 어느 구간에 있든 매 tick 위 셋을 먼저 본다 —
그래서 "이때 앞캠에 보이면 바로 가고, 뒷캠에 보이면 180도"가 저절로 성립한다.
구간을 끝까지 돌 필요가 없다.

| | 옛 | 새 |
|---|---|---|
| 뒤를 처음 보기까지 | 10초 (Hold 뒤) | **5초** |
| 전체 소진 | ~35초 | ~33초 |
| 훑기 각속도 | 0.35 rad/s | **0.55** (`ANGULAR_Z_SWEEP`) |
| 180° 정렬 회전 | 0.35 rad/s | 0.35 (`ANGULAR_Z_SEARCH`, 그대로) |

훑기와 정렬 회전의 각속도를 **상수 두 개로 분리**했다. 훑기는 빨라야 하고(전체 시간),
정렬 회전은 대상을 놓치면 안 되므로 느려도 된다.

### 훑기를 세 구간으로 쪼갠 이유

한 노드로 합치면 시간축이 leaf 안에 숨어 `search_planner.search_command()` 와의
**동등성 대조가 불가능해진다**(`test_angular_output_matches_search_command_over_timeline`).
구간이 곧 계약이다.

원위치 복귀(`...Home`)가 마지막에 붙는 이유: 안 돌아오면 다음 탐색의 기준 방위가 매번
달라져 "어디를 이미 봤는지"를 알 수 없게 된다.

### 반대 캠 구간에서는 재획득으로 안 친다

`CheckReacquired` 는 **정위치 캠을 보고 있을 때만** SUCCESS 를 낸다. 반대 캠 구간
(`HoldBack`·`SweepBack*`)에서 잡힌 것을 "정면에서 다시 보인다"로 읽으면, 로봇이 등을
진 채로 탐색을 끝내고 그대로 추종을 재개한다 — 방위 PID 는 앞캠 좌표로 속도를 만들어서
대상이 뒤에 있으면 제어가 성립하지 않는다. 반대 캠에서 찾았을 때의 정답은
`AlignHeading`(180° 회전)이고, 그건 `PeekPhase` 가 래치를 세워 처리한다.

### 정위치 캠은 역할이 정한다

| 역할 | 정위치 캠 | 반대 캠에서 찾으면 |
|---|---|---|
| 추종 | `front` — 사람이 앞에 있는 것이 정상 | **180° 회전** 후 재개. 제어가 앞캠 기준이라 필수 |
| 길잡이 | `back` — 사람이 뒤에서 따라온다 | **회전 없음.** 보는 캠만 바꾸고 끝낸다 |

길잡이에서 돌면 목적지 방향과 사람 방향이 겹칠 때 회전 → 경로 재계획 → 다시 포착 →
재회전으로 **무한 진동한다.** 추종에서 카메라는 제어 입력이지만, 길잡이에서 카메라는
감시일 뿐이고 주행은 nav2 가 한다 — 그 차이가 이 규칙의 근거다.

**정렬은 탐색 1회당 한 번**만 한다. 돌았는데도 정위치 캠에서 못 찾으면 반대 캠에는
여전히 그 사람이 보이므로, 제한이 없으면 래치 → 회전 → 못 찾음 → 다시 래치로 영원히 돈다.

### 카메라 선택의 발행자는 하나다

회복 BT 는 `ctx.select_camera()` 로 **요청만** 한다. 실제 `/libi/camera_select` 발행은
`follow_node` 가 한다 — 발행자가 둘이면 회복 중 서로 덮어쓴다.

포팅 전에는 `SearchMotion` leaf **하나** 안에서 `search_command(elapsed)` 시간 if-체인이
전 구간을 처리했다. 순서가 함수 안에 숨어 있어서, 회복 절차를 바꾸려면 조건문을 다시
써야 했다. 지금은 노드를 옮기면 된다.

**HOLD 먼저인 이유** — 사람이 잠깐 가려졌을 뿐인데 즉시 회전하기 시작하면 고장난 것처럼
보이고, 그대로 뒀으면 유지했을 대상을 오히려 놓친다.

> `arte_libi_perception/follower_BT/recovery.py` 프로토타입에는 PEEK(마지막 방향으로 90°)
> 먼저인 다른 타임라인이 있다. 모퉁이를 돈 경우엔 그쪽이 더 낫지만, **지금 실제로 도는 것은
> HOLD 방식**이라 그대로 유지했다. PEEK 방식으로 바꾸는 것은 포팅에 슬쩍 끼워 넣을 변경이
> 아니라, 전후 비교를 갖춘 의도적인 동작 변경으로 다뤄야 한다.

**동작이 안 바뀌었다는 근거** — `search_planner.search_command()` 를 참조용으로 남겨두고,
`test_recovery_bt.py` 가 0~30초 구간을 실제 20Hz(0.05초) 간격으로 훑으며 트리가 내보내는
`angular_z` 가 그 함수와 완전히 같은지 검사한다 (LKD 양방향 모두).

## 각 상태 시간 (config.py)

| 구간 | 시간 |
|---|---|
| Hold | `SEARCH_HOLD_SEC` = 10.0s |
| Scan1 / Scan2 | `SEARCH_SCAN_SEC` = 4.0s |
| Turn180 | `SEARCH_TURN_ANGLE / ANGULAR_Z_SEARCH` ≈ 8.98s |
| 회전 속도 | `ANGULAR_Z_SEARCH` = 0.35 rad/s |

## libi_modes 와의 관계

`libi_modes` 는 이 패키지 내부를 전혀 모른다. `WORKING` 브랜치의 `FollowExec` leaf 가
`FollowSession` 의 `start()/poll()/stop()` 만 호출한다.

```
poll() → 'running'   TRACKING 또는 SEARCHING (회복 중도 추종의 일부다)
       → 'failure'   회복 소진 — 사람을 놓쳤다
       → 'success'   관리자가 중단시켰다
```

추종 세션은 스스로 "완료"되지 않는다. 그래서 `'success'` 는 "도착했다"가 아니라
"중단 지시를 받았다"는 뜻이다.

## 실행

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_modes/ros_ws
colcon build --symlink-install --packages-select libi_perception
source install/setup.bash
ros2 run libi_perception follow_node
```

파라미터 (토픽·포트를 하드코딩하지 않는다):

| 파라미터 | 기본값 |
|---|---|
| `scan_topic` | `/scan` |
| `cmd_vel_topic` | `/cmd_vel` |
| `detection_host` / `detection_port` | `0.0.0.0` / `6000` |
| `autostart` | `true` |

## ⚠️ 미결정 — 이 노드가 어디서 도는가

패키지는 미션 PC 워크스페이스(`libi_modes/ros_ws`)에 있지만, **이 노드는 `/scan` 과
`/cmd_vel` 이 필요하고 둘 다 주행 Pi 소유다.** 20Hz LiDAR 융합 회피 루프가 네트워크
홉을 건너면 안전 경로가 끊길 수 있는 링크 위에 놓인다.

토픽 이름을 전부 파라미터로 빼둔 건 이 결정을 launch 시점으로 미루기 위해서다.
실제 로봇 투입 전에 정해야 한다.

## 테스트

```bash
cd aba_controller/libi_modes/ros_ws/src/libi_perception
PYTHONPATH=. python3 -m pytest tests/ -q
```

49개. 상위 `follower_control` 36개 중 28개를 그대로 가져왔고, 교체된 8개
(`bt_searching` 3 + `state_machine` 5) 자리에 `recovery_bt` 7 + `switch` 7 이 들어갔으며
`follow_session` 7 이 새로 붙었다.
