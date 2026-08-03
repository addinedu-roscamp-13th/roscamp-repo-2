# libi_modes — LIBI 미션 FSM · BT

LIBI(도서관 배달·수거 모바일 매니퓰레이터)의 **미션 레벨 상태 기계**다.
8개 상태와 그 사이 전이를 **별도 FSM 라이브러리 없이 py_trees 하나로** 구현한다.

> ## ⚠️ 트리를 바꾸면 같이 갱신할 것
>
> 이 트리는 관제 화면(`/admin/fsm` → **BT 흐름**)에 실시간으로 그려진다.
> 노드를 추가·삭제·**개명**하거나 배선을 바꿨으면 아래도 같이 고친다.
> 안 고치면 코드는 멀쩡한데 **화면만 조용히 거짓**이 된다.
>
> | 바꾼 것 | 같이 갱신 |
> |---|---|
> | 노드 추가·삭제·개명 | `aba_fms_service/frontend/src/components/admin/btNodeFlags.ts` — 키가 py_trees `name` **문자열 그대로**다. 이름이 바뀌면 플래그가 조용히 안 붙는다(범례 숫자 0으로 드러남) |
> | 구현·배선 상태 | 같은 파일. `unwired`(로직은 있는데 부를 통로 없음) / `partial`(일부만 동작) / `unreachable`(진입 불가). 근거를 `file:line` 주석으로 남긴다 |
> | 트리 구조·전이 규칙 | 이 README 의 브랜치 설명과 전이 박스 |
> | 다른 프로세스의 하위 BT | `libi_modes/ros/state_io.py` 의 `_GRAFT_POINT` — 그 leaf 밑에 접붙인다 |
>
> 화면에 그려지는 **노드 성격**(Sequence / Selector / Parallel/정책)은
> `state_io._kind` 가 만들어 스냅샷의 `kind` 필드로 나간다. 새 제어노드를 쓰면 거기도 본다.
>
> **이 레포의 BT 는 둘이다**
> - 여기 미션 BT(103노드) — `/libi/bt_snapshot` 으로 발행
> - `../libi_modes/ros_ws/src/libi_perception/recovery_bt.py` 추종 회복 BT — 별도 프로세스라
>   `/libi/follow_bt_snapshot` 으로 내보내 미션 BT 의 `FollowExec` **밑에 접붙인다**
> - (`aba_ai_service/follower_BT/` 는 py_trees 가 아니라 자체 상태기계다 — 대상 아님)
>
> **회복 BT 탐색 순서** (`SearchPhases`, 2026-08-01 기준)
>
> ```
> LkdPeek → HoldFront → HoldBack → SweepFront{Out,Across,Home}
>         → SweepBack{Out,Across,Home} → GiveUp
> ```
>
> `LkdPeek` 은 마지막 관측 방향(LKD)으로 ~90° 꺾는 4.5초 구간이며 **추종 전용**이다
> (길잡이는 `peek_sec` 이 0 이라 구간 자체가 빠진다 — 돌면 목적지 방향과 겹쳐 무한
> 진동한다). `config.SEARCH_PEEK_ANGLE = 0` 이면 추종에서도 사라진다.
>
> ⚠️ 이 타임라인은 `search_planner.search_command()` 가 **참조 구현**이라 한쪽만 고치면
> `test_recovery_bt` 의 동등성 검증이 거짓말을 한다. 항상 같이 고친다.

```
aba_controller/libi_modes/ros_ws/src/libi_modes/
├── libi_modes/
│   ├── tree.py                   루트 조립 (Parallel[Topics2BB, Priorities])
│   ├── registry.py               상태 → 브랜치 매핑 + 전이 테이블 (단일 진실 공급원)
│   ├── blackboard.py             blackboard 키 상수 + 안전 read 헬퍼
│   ├── common/                   모든 브랜치가 공유하는 leaf
│   └── branches/                 상태별 브랜치 8개
├── config/params.yaml            임계값 (40 / 80 / 15 등)
└── test/                         303 tests
```

> **패키지 이름 주의** — 초기 작업 지시서 초안에는 `libi_bt`로 적혀 있으나, 실제 패키지명은 **`libi_modes`**다.
> 상태(1단계)와 브랜치(4단계)가 1:1이라 한 패키지가 둘 다 담는다.

## 실행 위치

미션 PC(주행 Pi·팔 Pi와 별개 머신)에서 돈다. FMS(도메인 86)와는 `ros2 domain_bridge`로 연결한다.
**미션 PC의 `ROS_DOMAIN_ID`는 아직 미정** — 정해지면 `aba_fms_service/config/`에 브릿지 설정을 추가한다.

```bash
cd aba_controller/libi_modes/ros_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
PYTHONPATH=src/libi_modes python3 -m pytest src/libi_modes/test/ -q
```

---

# 1단계 — FSM

## 전이 박스

```
[*]              -> RETURNING        : boot
RETURNING        -> CHARGING         : docked
CHARGING         -> IDLE             : battery_charged [battery >= 40%]

IDLE             -> PATROL           : patrol_request (auto [battery >= 80% && is_docked] / manual)
IDLE             -> WORKING          : task_assigned
IDLE             -> SECURITY_PATROL  : security_patrol_request

PATROL           -> WORKING          : task_assigned
PATROL           -> INTERACTING      : ui_touch
PATROL           -> IDLE             : stop_request

INTERACTING      -> PATROL           : ui_idle_timeout / ui_close
INTERACTING      -> WORKING          : task_assigned
INTERACTING      -> IDLE             : stop_request

WORKING          -> PATROL           : task_done / task_failed
WORKING          -> IDLE             : stop_request

SECURITY_PATROL  -> IDLE             : security_patrol_complete / stop_request

{ IDLE, PATROL, SECURITY_PATROL } -> RETURNING : battery_low [battery <= 15% && !is_docked]

(any)            -> ERROR            : fault
ERROR            -> IDLE             : recovered
```

상태 **8종**, 간선 **18개**.
`{ ... } -> RETURNING`과 `(any) -> ERROR`는 그룹 전이이며, 다이어그램에서는 composite state 경계에서 나가는 단일 화살표로 표기한다.

이 표는 코드에서 `registry.TRANSITIONS`로 그대로 들고 있다. FMS 패널의 상태 다이어그램도 이 값을 읽어 그린다 — **별도 매핑 테이블을 만들지 않는다.**

## 상태 목록

| 상태 | 정의 |
|---|---|
| `CHARGING` | 충전소에 도킹하여 충전 중 |
| `IDLE` | 대기 상태. 충전 완료 후 명령 대기, 또는 정지 명령으로 멈춘 상태 |
| `PATROL` | 도서관 내부를 순회하며 작업 요청을 대기 |
| `SECURITY_PATROL` | 영업 외 시간 침입 감지 순찰 |
| `INTERACTING` | 이용자가 로봇 터치패널(libi_gui)을 조작 중 |
| `WORKING` | FMS로부터 배정받은 `task_id`를 수행 중 |
| `RETURNING` | 충전소로 복귀 및 도킹 시도 |
| `ERROR` | 고장, 비상정지 등 복구가 필요한 상태 |

## IDLE 이탈 조건

`IDLE`에서 `PATROL` 또는 `SECURITY_PATROL`로 전이하는 상황은 다음 세 가지다.

| # | 조건 |
|---|---|
| 1 | **배터리가 특정 수치 이상일 경우 (80%)** — 도킹된 상태에서만 적용 (`is_docked`) |
| 2 | **FMS로부터 명령이 들어온 경우** — `PATROL` 상태의 로봇이 없다면 `IDLE` 상태의 로봇에게 요청한다 |
| 3 | **사용자의 명령이 들어올 경우** |

### 조건 2의 배차 우선순위

```
1순위: PATROL 상태 로봇
2순위: IDLE 상태 로봇   ← PATROL 상태 로봇이 없을 때만
```

### 배터리 임계값

| 파라미터 | 값 | 용도 |
|---|---|---|
| `BATTERY_READY` | 40% | `CHARGING -> IDLE`. 이 시점부터 태스크 배정을 받을 수 있다 |
| `BATTERY_CHARGED` | 80% | `IDLE -> PATROL` 자동 전이. 순회 시작 |
| `BATTERY_LOW` | 15% | `RETURNING` 복귀 트리거 |

40~80% 구간에서는 `IDLE` 상태로 도킹된 채 충전을 계속하며, 이 구간에서도 FMS 태스크는 수락한다.

> **주의** — `IDLE -> PATROL`의 배터리 자동 전이에는 `is_docked` 가드가 필수다.
> `stop_request`로 진입한 `IDLE`은 도킹되어 있지 않아 배터리가 80%에 도달할 수 없으므로,
> 가드가 없으면 해당 로봇이 영구히 `IDLE`에 머문다. 이 경우는 조건 3(사용자 명령)으로만 이탈한다.

`IDLE`은 두 경로로 진입하고, 각각 배터리가 반대 방향으로 움직인다:

| 진입 경로 | 도킹 | 배터리 | 이탈 |
|---|---|---|---|
| `CHARGING`에서 | O | 상승 | 80% 도달 시 자동 `PATROL` |
| `stop_request`로 | X | 하강 | `resume_request`로만 |

그래서 두 배터리 검사 **양쪽 모두** 도킹 가드가 필요하다.
`docked=True`가 없으면 정지한 로봇이 영구히 갇히고, `docked=False`가 없으면 충전 중인 로봇이 불필요하게 복귀를 시도한다.

---

## 구현 방침 — 모든 전이를 py_trees로 구성

별도 FSM 라이브러리(SMACH, YASMIN, `transitions` 등)를 사용하지 않는다.
**위 18개 전이를 전부 py_trees 트리로 구현한다.**

현재 상태는 blackboard 변수 `current_mode`로 유지하고, 트리는 그 값에 따라 분기한다.
루트는 우선순위 Selector로 구성하며, **위에 있을수록 높은 우선순위**를 갖는다.

```
Root (Parallel, SuccessOnAll)
├── Topics2BB          배터리 · UI 이벤트 · FMS 명령을 blackboard로 수집
└── Priorities (Selector, memory=False)
    ├── ErrorBranch
    ├── ReturningBranch
    ├── ChargingBranch
    ├── WorkingBranch
    ├── InteractingBranch
    ├── SecurityPatrolBranch
    ├── PatrolBranch
    ├── IdleBranch
    └── Running()          # 어느 브랜치도 매칭되지 않을 때 트리를 살려둠
```

### 상태와 BT의 대응 관계

**각 브랜치가 곧 해당 상태의 BT다.** 상태가 전이되면 그 상태에 대응하는 브랜치(서브트리)가 실행된다.

| 상태 | 대응 브랜치 | 브랜치 내부 동작 |
|---|---|---|
| `ERROR` | `ErrorBranch` | 정지, 복구 대기 |
| `RETURNING` | `ReturningBranch` | 로봇팔 홈 복귀 → 충전소 주행 → 도킹 |
| `CHARGING` | `ChargingBranch` | 충전 대기 |
| `WORKING` | `WorkingBranch` | 커맨드 수신 → 주행 / 로봇팔 동작 |
| `INTERACTING` | `InteractingBranch` | 인터록 체결 → 타임아웃 감시 |
| `SECURITY_PATROL` | `SecurityPatrolBranch` | 순찰 경로 주행 → 침입 감지·녹화·알림 |
| `PATROL` | `PatrolBranch` | 순회 경로 주행 |
| `IDLE` | `IdleBranch` | 정지 유지, 명령 대기 |

### 구현 규칙

| 항목 | 규칙 |
|---|---|
| 우선순위 Selector | `memory=False` — 매 tick마다 상위 조건을 재평가해야 선점이 동작한다 |
| 동작 실행용 Sequence | `memory=True` — 진행 중인 시퀀스를 처음부터 다시 실행하지 않는다 |
| 브랜치 루트 Sequence | `memory=False` |
| 상태 진입 액션 | 각 브랜치 Sequence의 첫 자식으로 배치 |
| 중단 처리 | 직접 작성한 Behaviour마다 `terminate(new_status)`를 구현 |
| 블로킹 금지 | `update()` 내부에서 블로킹 호출 금지. tick 전체가 정지한다 |
| 임계값 | 하드코딩 금지. `config/params.yaml`에서 주입 |
| LED 제어 | **브랜치에서 하지 않는다.** LED 패키지가 상태 토픽을 구독하여 처리한다 |

### ⚠️ Parallel 안에서는 FAILURE가 곧 중단이다

py_trees의 `Parallel`은 **자식 하나라도 FAILURE를 반환하면 즉시 실패한다.** `SuccessOnOne` 정책은
"언제 성공하는가"만 정하며, 실패하는 형제를 무해하게 만들지 않는다.

그래서 이탈 조건 `Selector`를 `Parallel` 안에 그대로 넣으면 **평상시(이탈 조건 없음 → FAILURE)마다
옆에서 돌던 주행이 매 tick 중단된다.** `common/watchdog.py`의 `exit_watchdog()`가 끝에 `Running()`을
붙여 이 FAILURE를 RUNNING으로 바꾼다.

같은 이유로 `ReturnNavigation`은 도킹 재시도가 소진돼도 FAILURE가 아니라 **RUNNING을 반환하고
`fault`만 세운다.** FAILURE를 내면 형제인 `FaultDetected`가 SUCCESS를 낼 기회도 없이 Parallel이
먼저 죽어서 ERROR 전이가 영영 일어나지 않는다.

> 이 두 가지는 설계 문서만 보고는 드러나지 않았고, 실제로 테스트를 돌려서 발견했다.
> `test_tree.py::test_watchdogs_inside_parallels_end_with_running`이 재발을 막는다.

### 주의

- `stop_request`처럼 **명시적 전이**는 우선순위만으로 표현되지 않는다. blackboard의 `next_mode`를 갱신하는 방식으로 처리한다
- `WORKING` / `INTERACTING` 중에는 `battery_low`로 복귀하지 않는다 — 두 브랜치에 `BatteryCheck` leaf 자체가 없다
- **`RETURNING` 진입 시 로봇팔 홈 자세 복귀**를 반드시 먼저 수행한다. 기동 직후에는 팔 자세를 알 수 없다

## 의도적으로 두지 않은 간선

| 없는 간선 | 근거 |
|---|---|
| `PATROL -> SECURITY_PATROL` | 야간 순찰 진입은 `IDLE`에서만 수행한다 |
| `SECURITY_PATROL -> PATROL` | 순찰 완료 후 `IDLE`로 복귀하여 15분 주기 야간 루프를 형성한다 |
| `CHARGING -> PATROL` 등 직접 이탈 | 충전소 이탈 판단을 `IDLE` 한 곳에 집중한다 |
| `WORKING -> RETURNING` (battery_low) | 배차 단계에서 배터리를 고려하므로 작업 중 복귀하지 않는다 |
| `INTERACTING -> RETURNING` (battery_low) | 이용자 응대 중 이탈을 금지한다 |
| `RETURNING -> IDLE` (stop_request) | 15% 미만에서 세우면 충전소에 도달하지 못하고 방전된다 |
| `ERROR -> RETURNING` (battery_low) | 고장 원인을 모르는 채 자율 주행을 재개하면 위험하다 |

이 "없는 간선"들은 `test_tree.py`에서 역방향으로 검증한다 — 나중에 leaf를 하나 추가하다 조용히 생기는 것을 막는다.

### 야간 동작 루프

```
IDLE --(15분 타이머)--> SECURITY_PATROL --(1회 순찰 완료)--> IDLE
```

> **미결정** — `IdleBranch`에는 타이머 leaf가 없다. 15분 주기로 `security_patrol_request`를
> 발행하는 주체(스케줄러)가 아직 설계되지 않았다. 현재 구조는 외부에서 그 명령이 들어오는 것을 전제한다.

---

# 4단계 — BT

상태 하나당 브랜치 하나. 브랜치는 `branches/<state>.py`의 `create()`로 분리한다.

## 브랜치 공통 규약

| 규약 | 내용 |
|---|---|
| 노출 함수 | `create()` 하나만 |
| 루트 이름 | `XxxBranch` |
| 첫 자식 | 반드시 `IsMode("XXX")` |
| 전이 | `RequestTransition()`으로만. 직접 상태 변경 금지 |
| 루트 타입 | **항상 `Sequence`.** 동작형은 두 번째 자식에 `Parallel`을 둔다 |
| `memory` | 브랜치 루트는 항상 `False` |

### 공통 노드

| 노드 | 역할 |
|---|---|
| `IsMode(mode)` | `current_mode`가 인자와 같으면 SUCCESS |
| `RequestTransition()` | `next_mode` 값으로 `current_mode`를 갱신하고 `next_mode`를 비운다 |
| `FaultDetected()` | `fault` 발생 시 `next_mode = ERROR` 기록 후 SUCCESS |
| `BatteryCheck(op, threshold, next_mode, require_docked=None)` | 배터리 조건 충족 시 `next_mode` 기록 후 SUCCESS |
| `CommandListener(mapping)` | 매핑된 명령 수신 시 `next_mode` 기록 + 명령 소비 후 SUCCESS |
| `CommandTimeout(sec)` | 커맨드 무수신이 이어지면 `next_mode = ERROR` |
| `SetNextMode(mode)` | 무조건 SUCCESS + 고정 `next_mode` 기록 |
| `UiSessionTimer(sec)` | 무입력 타임아웃 감시 + 인터록 체결/해제 |
| `DriverAction` | start/poll/stop 하드웨어 클라이언트 래퍼 (주행·팔 액션의 베이스) |
| `exit_watchdog(name, conds)` | `Parallel` 안에서 쓰는 이탈 조건 Selector (끝에 `Running()`) |

### 이탈 조건 leaf 규약

**모든 이탈 조건 leaf는 "조건 판정 + `next_mode` 기록"을 함께 수행한다.**
조건 충족 시 `next_mode`를 쓰고 SUCCESS, 미충족 시 FAILURE를 반환한다.
전이는 브랜치 마지막의 `RequestTransition()`이 일괄 처리한다.

`next_mode`와 `last_command`는 **쓴 쪽이 소비한다.** 비우지 않으면 다음 tick에 같은 전이가 재발화한다.

### 브랜치 공통 골격

```
XxxBranch (Sequence, memory=False)
├── IsMode("XXX")            # 가드
├── <판정부>                  # 대기형은 Selector, 동작형은 Parallel
└── RequestTransition()      # 전이 — 항상 루트의 마지막 자식
```

`RequestTransition()`은 **반드시 `Parallel` 바깥, 루트 `Sequence`의 마지막 자식**에 둔다.
`Parallel` 안에 두면 동작 노드가 먼저 완료됐을 때 전이가 실행되지 않아 같은 상태를 반복한다.
`test_tree.py::test_no_request_transition_nested_inside_a_parallel`이 이를 강제한다.

### 이탈 조건 우선순위

```
1. FaultDetected      (최우선)
2. BatteryCheck       (배터리 부족)
3. CommandListener    (명령)
4. 기타 조건
```

### fault 검사 배치

`fault -> ERROR`는 전역 Guard로 빼지 않고 **8개 브랜치 각각에 포함한다.**
브랜치가 자기 이탈 조건을 스스로 갖는다는 규칙이 예외 없이 유지된다.
유일한 예외는 `ERROR` 자신 — 이미 ERROR이므로 자기 자신으로 전이할 필요가 없다.

### 트리 죽음 방지

브랜치가 FAILURE를 반환하면 상위 Selector가 다음 브랜치를 평가하는데, 다른 브랜치는 `IsMode`에서 전부 FAILURE다.
따라서 **`Priorities` Selector 맨 끝에 `Running()`을 하나 둔다.** 브랜치마다 넣지 않는다.

---

## 브랜치 설계

### 1. CHARGING

```
ChargingBranch (Sequence, memory=False)
├── IsMode("CHARGING")
├── Selector(memory=False)
│   ├── FaultDetected                           # fault → ERROR
│   └── BatteryCheck(">=", 40, "IDLE")          # 40% 도달 → IDLE
└── RequestTransition()
```

배터리가 40% 미만이고 `fault`도 없으면 `Selector`가 FAILURE를 반환해 브랜치가 끊긴다.
결과적으로 40%에 도달할 때까지 대기하는 동작이 성립한다. 별도 대기 노드는 필요 없다.

이 Selector는 `Parallel` 안이 아니라 루트 Sequence 바로 아래에 있으므로 `exit_watchdog`을 쓰지 않는다.
여기서는 FAILURE가 "이 브랜치는 이번 tick에 아무것도 안 한다"는 올바른 의미다.

**신규 leaf: 없음.**

### 2. IDLE

```
IdleBranch (Sequence, memory=False)
├── IsMode("IDLE")
├── Selector(memory=False)
│   ├── FaultDetected
│   ├── BatteryCheck("<=", 15, "RETURNING", require_docked=False)   # 도킹 안 된 채 방전 → 복귀
│   ├── CommandListener                                             # 명령 수신
│   └── BatteryCheck(">=", 80, "PATROL", require_docked=True)       # 도킹 상태 충전 완료 → 순회
└── RequestTransition()
```

**`CommandListener` 매핑**

| 수신 명령 | `next_mode` |
|---|---|
| `task_assigned` | `WORKING` |
| `security_patrol_request` | `SECURITY_PATROL` |
| `resume_request` | `PATROL` |

정지한 로봇은 `resume_request`로만 이탈한다.

### 3. PATROL

```
PatrolBranch (Sequence, memory=False)
├── IsMode("PATROL")
├── Parallel(SuccessOnOne)
│   ├── PatrolNavigation                        # 순회 주행 — 계속 RUNNING
│   └── exit_watchdog(...)                      # 이탈 조건 + 끝에 Running()
│       ├── FaultDetected
│       ├── BatteryCheck("<=", 15, "RETURNING")
│       └── CommandListener
└── RequestTransition()
```

`PatrolNavigation`은 드라이버가 "success"를 줘도 RUNNING으로 바꿔 반환한다 — 순회는 무한 루프라
"한 바퀴 돌았다"가 끝을 의미하지 않는다. 이탈 감시가 SUCCESS를 내면 `Parallel`이 종료되고
`terminate()`로 모터가 정지한다.

**`CommandListener` 매핑**: `task_assigned` → `WORKING`, `ui_touch` → `INTERACTING`, `stop_request` → `IDLE`

**신규 leaf: `PatrolNavigation`**

### 4. SECURITY_PATROL

```
SecurityPatrolBranch (Sequence, memory=False)
├── IsMode("SECURITY_PATROL")
├── Parallel(SuccessOnOne)
│   ├── Sequence(memory=True)                   # 순찰 1회
│   │   ├── SecurityPatrolNavigation            # 주행 + 침입 감지·녹화·알림
│   │   └── SetNextMode("IDLE")
│   └── exit_watchdog(...)
│       ├── FaultDetected
│       ├── BatteryCheck("<=", 15, "RETURNING")
│       └── CommandListener                     # stop_request 만
└── RequestTransition()
```

침입 감지 후 처리(녹화 · 저장 · 관리자 알림, SR-19)는 `SecurityPatrolNavigation` 드라이버 내부 로직으로 둔다.

**`PATROL`과의 차이**

| 항목 | `PATROL` | `SECURITY_PATROL` |
|---|---|---|
| 주행 노드 | 계속 RUNNING (무한 순회) | 1회 완료 시 SUCCESS |
| 완료 후 | (없음) | `SetNextMode("IDLE")` |
| `CommandListener` | `task_assigned` / `ui_touch` / `stop_request` | `stop_request`만 |

야간 운영 중이므로 이용자 터치나 태스크 배정을 받지 않는다.

**신규 leaf: `SecurityPatrolNavigation`**

### 5. INTERACTING

```
InteractingBranch (Sequence, memory=False)
├── IsMode("INTERACTING")
├── Parallel(SuccessOnOne)
│   ├── UiSessionTimer(20)                      # 무입력 20초 감시 + 인터록
│   └── exit_watchdog(...)
│       ├── FaultDetected
│       └── CommandListener
└── RequestTransition()
```

**`CommandListener` 매핑**: `ui_close` → `PATROL`, `task_assigned` → `WORKING`, `stop_request` → `IDLE`

**`BatteryCheck`를 두지 않는 이유** — 전이 박스에서 `INTERACTING -> RETURNING`을 의도적으로 제외했다.
`INTERACTING`은 `WORKING`과 함께 배터리 검사가 없는 두 브랜치다.

**인터록** — ⚠️ **이 트리는 인터록을 강제하지 않는다.** 예전엔 `UiSessionTimer`가
`drive_lock`/`arm_lock`을 체결한다고 적혀 있었지만, 그 두 키를 읽는 production 코드는
레포 전체에 하나도 없었다(2026-07-26 전수 확인). 그래서 두 키를 삭제했다.

"응대 중엔 움직이지 않는다"를 실제로 보장하는 것은 **브랜치 우선순위**다 — `Priorities`가
Selector라 한 tick에 브랜치 하나만 돌고, `INTERACTING`이 잡히면 다른 브랜치의 액션 leaf는
애초에 tick되지 않는다. blackboard 불리언은 그 보장에 관여한 적이 없다.

FMS가 `/fleet_cmd`로 직접 미는 주행까지 막는 **교차 프로세스 인터록**이 필요하면 로봇 쪽
`fleet_link`에서 막아야 한다. blackboard 값은 그 프로세스에 닿지 않는다.

**신규 leaf: `UiSessionTimer`**

### 6. WORKING

태스크 시퀀싱은 **task_adapter가 관리**한다. 브랜치는 수신한 커맨드 하나를 수행할 뿐,
자신이 이송 중인지 분류 중인지 알지 못한다.

```
WorkingBranch (Sequence, memory=False)
├── IsMode("WORKING")
├── Parallel(SuccessOnOne)
│   ├── Selector(memory=False)                  # 커맨드 실행부
│   │   ├── NavigationExec                      # navigate / dock
│   │   ├── GuideExec                           # guide — 요청자를 보며 주행
│   │   ├── ArmExec                             # perform_action
│   │   ├── FollowExec                          # follow_admin
│   │   └── Running("AwaitingCommand")          # 커맨드 없음 — 대기
│   └── exit_watchdog(...)
│       ├── FaultDetected
│       ├── CommandTimeout(120)
│       └── CommandListener
└── RequestTransition()
```

`Running("AwaitingCommand")`는 **반드시 dispatch Selector의 마지막**이어야 한다 — 항상 성공하므로
그 뒤에 놓인 핸들러는 영원히 도달하지 않는다.

**`CommandListener` 매핑**: `task_done`/`task_failed` → `PATROL`, `stop_request` → `IDLE`

`stop_request`로 이탈할 때는 FMS에 `task_cancelled`를 보고한다. 보고하지 않으면 해당 태스크가
RMF에 잔존하여 재배차되지 않는다. (보고 자체는 이 트리 밖 — blackboard에 `stop_request`를 넣는 쪽의 책임)

**`BatteryCheck`를 두지 않는 이유** — FMS가 배차 단계에서 잔여 배터리와 태스크 소요량을 고려하여
할당하므로, 작업 중에는 배터리로 이탈하지 않는다.

**`CommandTimeout`이 필요한 이유** — task_adapter가 죽거나 통신이 끊기면 `Running()`에서 영구히
대기하게 된다. `WORKING`은 배터리 이탈 경로도 없으므로, 이 노드가 없으면 복구 불가 상태에 갇힌다.

**서브스테이트 대응**

| RMF 커맨드 | 실행 노드 |
|---|---|
| `navigate()` / `dock()` | `NavigationExec` |
| `perform_action()` | `ArmExec` |
| (대기) | `Running()` |

**신규 leaf: `NavigationExec`, `ArmExec`, `CommandTimeout`**

> `ArmExec` 내부의 파지·적재 서브트리는 **작성 예정**이다. 현재는 `driver`가 그 자리를 대신한다.

#### 길잡이 — `GuideExec` (2026-07-27)

터치패널에서 이용자가 목적지를 고르면 로봇이 **데려다 준다**. 주행 자체는 `NavigationExec`과
같지만, 안내는 **혼자 도착하면 실패**라는 점이 다르다 — 안내받는 사람을 두고 먼저 가버리면
목적지에 닿아도 아무것도 안내하지 못한 것이다.

```
액션 계층 (기존 navigate 와 같은 규칙)
  FMS  →  /fleet_cmd {action:"guide", args:{x,y,yaw}}   ← BT 층. GuideExec 소유
            providers: active_command="guide", nav_target={x,y,yaw}
  BT   →  /fleet_cmd {action:"goal",  args:{x,y,yaw}}   ← 실행 층. fleet_link → nav2
```

`guide`를 `navigate`로 뭉뚱그리면 **안 된다.** Selector 앞의 `NavigationExec`이 먼저 집어가
`GuideExec`은 한 번도 안 돌고, 요청자를 놓쳐도 아무도 멈추지 않는다.

**세 갈래**

| 요청자 | 동작 |
|---|---|
| 보인다 | 평소대로 주행 |
| 잠깐 안 보인다 (`guide_lost_grace_sec` 이내) | 계속 주행 — 서가 뒤로 한 발 들어간 정도로 끊기면 못 쓴다 |
| 유예를 넘겨 안 보인다 | **멈추고 기다린다.** `mission_stop` → `ros_bridge.cancel_nav()` |
| `guide_lost_timeout_sec` 을 넘겨도 안 온다 | FAILURE — 안내 종료 |

멈출 때 `mission_stop`을 **실제로 보내야 한다**. 안 보내고 RUNNING 만 돌려주면 화면은
"기다리는 중"인데 nav2는 계속 달려 로봇이 사람을 두고 간다.

**요청자 가시성 계약** — `/libi/requester_visible` (`std_msgs/Bool`).
`providers`가 구독해 `requester_visible` / `requester_seen_at`(마지막으로 보인 monotonic 시각)로
내보낸다. 시각은 **보였을 때만** 갱신한다 — 안 보이는 동안 계속 덮으면 "얼마나 오래 안 보였나"가
항상 0이 되어 영영 멈추지 않는다.

> **[2026-07-27 수정됨]** 아래 결함은 고쳤다 — `send_nav_goal()` 에 응답 콜백을 달고,
> 응답보다 취소가 먼저 오는 경우와 새 goal 이 이전 핸들을 덮는 경우까지 다룬다
> (`robot_agent/app/core/nav_goal_tracker.py`). 원래 설명은 아래에 남겨 둔다.
>
> ⚠️ **정지가 실제로는 안 먹는다 (확인된 결함, 길잡이만의 문제 아님).**
> `mission_stop` → `mission.stop_mission()` → `ros_bridge.cancel_nav()` 까지는 가지만,
> `cancel_nav()` 가 취소하는 `_active_goal_handle` 은 채워지지 않는다 — `send_nav_goal()` 이
> `send_goal_async()` 결과에 `_on_goal_response` 콜백을 달지 않기 때문이다
> (`ros_bridge.py:486` vs `:494,512`). 그래서 `goal` 로 나가는 **모든 주행**(배달·순회·복귀)이
> 취소되지 않는다. `robot_agent` 소유라 실기 확인 없이 손대지 않았다 — 고칠 때 한 줄이다:
> `send_goal_async(...).add_done_callback(self._on_goal_response)`.

> **[2026-07-27] 발행자가 생겼다.** `libi_perception` 의 `follow_node` 가 `guide`/`watch`
> 역할일 때 `/libi/requester_visible`(Bool)과 `/libi/requester_area`(Float32)를 낸다.
> `btNodeFlags.ts` 의 `GuideExec: "partial"` 도 해제했다.
>
> 값이 `None`이면 여전히 "감시 없음"으로 읽고 그냥 주행한다(감시 없는 배포에서 길잡이가
> 통째로 죽는 것보다 낫다). 다만 **한 번이라도 받은 뒤 끊기면 `False`(정지)로 내린다** —
> `None`으로 내리면 AI 서버가 죽었을 때 로봇이 사람 없이 계속 몰고 간다.
>
> 그리고 `GuideExec` 은 이제 셋을 더 본다:
>   · **거리 게이트** — 보이지만 너무 멀면(`guide_far_area_min`) 멈춰 기다린다
>   · **근접 정지** — 앞을 막을 만큼 가까우면 멈춘다(`guide_near_area_max`, 기본 꺼짐)
>   · **갈림길 확인** — 레인 3개 이상 붙은 정점에서만 잠깐 선다. 모든 노드에서 서면
>     arte2 레인이 0.151~0.601m 라 1~5초마다 멈춰 안내가 계속 끊긴다

테스트: `test/test_guide_exec.py` (분류 분리·유예·실제 정지·재개·포기·도착·감시없음)

### 7. RETURNING

```
ReturningBranch (Sequence, memory=False)
├── IsMode("RETURNING")
├── Parallel(SuccessOnOne)
│   ├── BackCamOn                               # 뒷캠을 선택 상태로 유지 (절대 안 끝남)
│   ├── ReturnSteps (Sequence, memory=True)
│   │   ├── ReturnOrSkip (Selector, memory=False)
│   │   │   ├── AlreadyDocked                   # 충전소에 놓인 채 부팅 → 전부 건너뜀
│   │   │   └── ReturnDriveSteps (Sequence, memory=True)
│   │   │       ├── Absorb[GoToParkingEntrance] # ① 충전소통로 정점으로 주행
│   │   │       ├── Absorb[FaceApproachYaw]     # ② 지금 헤딩에서 180° 회전
│   │   │       ├── Absorb[ReleaseNav]          # ③ nav2 목표 해제 (바퀴를 넘기기 전)
│   │   │       ├── Absorb[ArucoApproach]       # ④ 뒷캠 ArUco 로 6cm ← **다른 저장소**
│   │   │       ├── Absorb[DockNudge]           # ⑤ 개루프 3cm 후진
│   │   │       └── Absorb[DockSettle]          # ⑥ 안정화 → is_docked 선언
│   │   └── SetNextMode("CHARGING")
│   └── exit_watchdog([FaultDetected])
└── RequestTransition()
```


### [2026-07-30] `BackCamOn` — 왜 절대 끝나지 않나

`camera_sender` 는 **선택되지 않은 캠을 8틱에 한 번만** 잡는다(`STANDBY_EVERY=8`, 15fps →
0.53초 = **1.9Hz**). 복귀 중엔 추종 세션이 없어 뒷캠이 그 상태고, 그 프레임으로 12Hz 시각
서보를 돌리면 HOMING(0.10m/s)에서 프레임 사이 5.3cm 를 간다 — 못 쓴다.
`guide_watch{camera:back}` 를 내면 `follow_node` 가 `camera_select` 를 back 으로 발행해
(그 토픽의 발행자는 follow_node 하나라는 규칙) 매 프레임 15fps 가 된다.

⚠️ 이 leaf 가 **SUCCESS 를 내면 `Parallel(SuccessOnOne)` 이 그 순간 복귀를 끝낸다.**
FAILURE 는 정책과 무관하게 Parallel 을 죽인다. 그래서 어느 쪽도 안 낸다 — 세션 수명은
Parallel 이 끝날 때 `terminate(INVALID)` 가 닫는다. 도킹이 성공하든 fault 로 빠지든
나갈 때 뒷캠 선택이 풀리는, 정확히 원하는 수명이다.

### [2026-07-30] `UndockOrSkip` — 도킹 자세에서 나가기

**PATROL·WORKING·SECURITY_PATROL 의 `IsMode` 바로 뒤, 주행을 내기 전**에 있다.

```
PatrolBranch (Sequence, memory=False)
├── IsMode("PATROL")
├── UndockOrSkip (Selector, memory=False)
│   ├── UndockNotNeeded            # 도킹 아님 또는 이미 나옴 → 통과 (평소)
│   └── GiveUp[Undock]             # 15cm 명령 · 10cm 이동을 pose 로 확인
└── Parallel(...)                  # ← Undock 이 RUNNING 인 동안 여긴 tick 되지 않는다
```

⚠️ **`Absorb` 가 아니라 `GiveUp` 이다.** `AbsorbFailure` 는 재시도를 소진하면 fault 를 세우고
**RUNNING 을 유지**한다. 그게 맞는 이유는 그쪽이 `Parallel(SuccessOnOne)` **안**에 있고 형제
`FaultDetected` 가 같은 tick 에 그 fault 를 본다는 것인데, 이 게이트는 **주행 Parallel 앞**에
있어서 RUNNING 을 유지하면 뒤 Parallel 이 영영 tick 되지 않는다 — fault 를 볼 노드가 없어
PATROL 에 멈춘 채 무한 재시도한다. 그래서 소진 시 **SUCCESS** 를 낸다(성공이 아니라
"fault 를 볼 수 있는 곳까지 흐름을 보내려고" 통과시키는 것이다).

왜 필요한가: `nav2_params.yaml` 의 `inscribed 0.088 < inflation_radius 0.09` 라 **벽에서
9cm 안쪽은 전부 cost 253(통행불가)** 이다. 도킹이 끝나면 로봇 중심이 정확히 그 경계(9cm)에
서고, AMCL 오차와 격자가 얹히면 대부분 안쪽으로 떨어져 **global planner 가 시작 격자에서
경로를 아예 못 만든다.** 물리적으로 못 나가는 게 아니라 지도상 판정이므로, nav2 를 안 쓰고
먼저 빠져나오면 사라진다.

⚠️ **CHARGING·IDLE 에 넣으면 안 된다.** `cmd_vel_dock` 은 priority 120, FSM 잠금은 150,
`MOTION_LOCKED_STATES = {IDLE, INTERACTING, ERROR, CHARGING}` 이다 — 잠긴 상태에 두면
조용히 아무 일도 안 일어난다.

⚠️ **세 브랜치 모두 `undock_gate` 를 필수 인자로 받는다.** 기본값을 안 준 이유: 배선을
빠뜨리면 그 경로로 나갈 때 nav2 가 "경로 없음"으로 실패하는데, 증상이 도킹과 멀리 떨어져
나타나 원인을 못 찾는다. 조립 단계에서 터지는 편이 낫다.

⚠️ **래치는 `DockSettle` 이 푼다.** 브랜치 루트가 `memory=False` 라 래치 없이는 매 tick 다시
민다. 그렇다고 게이트가 전이를 보고 풀면 안 된다 — 게이트는 **브랜치마다 별개 인스턴스**라
PATROL→WORKING 으로 옮길 때 그쪽 인스턴스가 남의 래치를 지운다. 도킹이 실제로 끝나는
한 곳에서만 푼다.

⚠️ `Undock` 은 **시간이 아니라 실제 이동량**으로 판정한다. `NudgeDriver` 는 시간 기반이라
바퀴가 헛돌아도 성공하는데, 그러면 nav2 실패가 한참 뒤에 나타난다. 이동량은 **시작 헤딩에
투영**해서 센다 — 시작점 대비 직선거리로 재면 옆으로 밀린 거리와 AMCL 재국소화 점프까지
전진으로 세어, 벽에서 안 나왔는데 나왔다고 판정한다.

### [2026-07-31] 명령 거리 ≠ 판정 거리

실측: 다른 상태로 갈 때마다 로봇이 **앞으로 세 번** 나갔다.

```
드라이버 : undock_distance_m 를 개루프로 명령
Undock  : pose 로 잰 이동량 ≥ undock_distance_m 여야 성공   ← 여유가 0
```

개루프 실이동은 명령값보다 **항상 조금 적다**(가속 램프·바퀴 슬립·모터 데드밴드). 첫 시도가
판정선에 못 미치면 `GiveUp` 이 재시도하는데, 재시도는 `initialise()` 에서 **기준점을 새로
잡으므로** 매번 처음부터 전체 거리를 다시 요구한다. 그래서 세 번 밀었고 실제로는 30cm 를 갔다.

명령과 판정을 분리해서 고쳤다(`main.py` 가 드라이버에만 여유를 더한다):

```
명령 = undock_distance_m(0.10) + undock_slip_margin_m(0.05) = 15cm
판정 = undock_distance_m                                     = 10cm   ← 안전에 필요한 값
```

여유를 0 으로 되돌리면 세 번 밀기가 그대로 돌아온다.
### [2026-07-27] 한 leaf → 5단계

예전에는 `ReturnNavigation` 하나가 팔 홈복귀·주행·도킹을 통째로 했다. 어디서 실패했는지
화면에 안 보였고, 정밀 정렬(ArUco)을 붙일 자리도 없었다.

**각 단계는 `AbsorbFailure` 로 감싼다.** `Parallel` 은 자식 하나가 FAILURE 를 내면
**정책과 무관하게** 즉시 실패하므로, 감싸지 않으면 형제 `FaultDetected` 가 그 fault 를
ERROR 전이로 바꿀 tick 조차 없이 브랜치가 죽는다. 재시도를 다 쓰면 fault 를 세우고
그래도 RUNNING 을 낸다(기존 `ReturnNavigation` 이 지키던 성질 그대로다).

**`AlreadyDocked`** — 부팅 상태가 `RETURNING` 이라, 충전소에 놓인 채 켜면 5단계가 그대로
돌아 입구까지 나갔다 되돌아온다. 15% 미만이면 도달 못 할 수도 있다.

**회전은 새 `/cmd_vel` 발행자를 만들지 않는다.** 같은 좌표에 목표 yaw 만 다른 `goal` 을
보내면 nav2 가 제자리 회전으로 처리한다.

### [2026-07-30] 뒷캠 ArUco 정밀 주차로 재편

**없앤 것: `GoToParking`(nav2 로 주차장 정점) · `TurnAround`(180°).** ②가 이제 "주차장을
바라본다"가 아니라 **등이 충전소를 향하도록 돌린다.** 그러면 뒷캠이 마커를 보므로 180° 를
따로 돌 필요가 없고, 충전소 정점까지 nav2 로 가는 구간은 ④의 ArUco 접근이 대신한다.
**AMCL 오차가 충전 단자 폭보다 큰 구간을 nav2 에게 맡기지 않는 것**이 재편의 요점이다.

#### [2026-07-31] ②의 목표각 — 절대각도, 좌표 계산도 아닌 **상대 180°**

세 번 바꿨고 앞의 둘은 현장에서 틀렸다.

| 시도 | 왜 틀렸나 |
|---|---|
| 고정 절대각(정점 yaw + 180°) | ①은 **x·y 만 보고** 성공한다. 실제 헤딩이 그 정점 yaw 라는 보장이 없다 — 상황마다 다른 곳을 봤다 |
| 충전소 좌표에서 `atan2` | 로봇이 충전소와 거의 일직선에 서면 방위각이 작은 오차에도 크게 튄다. 38cm 앞에서 y 가 2cm 어긋나면 3°, 가까울수록 심해진다 |
| **`wrap_angle(pose.yaw + π)`** | ①이 만들어 준 자세를 그대로 이어받는다. 그 자세가 충전소를 보는 자세이므로 반대편이 정확히 등을 진다 |

`params.yaml` 의 `returning.approach_yaw_rad` 에 값을 넣으면 그 절대각이 이긴다(현장 보정용).
기본값은 `null` — 상대 180° 다.

⚠️ 목표각은 `_YawStep` 이 **한 번만** 정한다. 매 tick 다시 계산하면 돌면서 목표도 같이
움직여 영원히 안 닿는다.

| 단계 | 하는 일 | 실패하면 |
|---|---|---|
| ② `FaceApproachYaw` | 상대 180° 회전. 자세를 모르면 시작 안 함 | timeout(60s) → 흡수·재시도 |
| ③ `ReleaseNav` | `/fleet_cmd{stop}` → `cancel_nav()`. 끊을 목표 없으면 무동작 | fleet_link 무응답 |
| ④ `ArucoApproach` | `/fleet_cmd{dock_action}` 왕복. **구현은 다른 저장소** | `aruco_timeout_sec`(180s) |
| ⑤ `DockNudge` | `cmd_vel_dock` 정속 발행 (거리÷속도 초) | 드라이버가 스스로 끝냄 |
| ⑥ `DockSettle` | `settle_sec`(1s) 대기 후 SUCCESS | 없음 (시간뿐) |

⚠️ **③ ReleaseNav 를 빼면 안 된다** (codex 리뷰 2026-07-30). `_GoalStep` 은 **x·y 거리만**
보고 성공하고, 성공한 단계는 `terminate` 에서 `stop` 을 안 보낸다 — 즉 ①이 끝나도 입구
goal 은 살아 있다. 예전엔 바로 뒤 `GoToParking` 이 새 goal 로 **선점**해 줬는데 그 단계를
없애면서 선점 주체가 사라졌다. 특히 로봇이 이미 입구에 접근 각도로 서 있으면 ②도 goal 을
한 번도 안 보내고 성공해서, **죽은 nav2 목표가 ArUco 접근과 바퀴를 두고 다툰다.**

⚠️ **④에 답하는 쪽은 하나여야 한다.** 기본 액션 이름은 `aruco_dock` 이고, 지금은
`robot_agent` 의 `app/core/marker_dock.py` 가 답한다(2026-07-30 이식, 2026-07-31 실기 성공).
`"dock"` 으로 바꾸면 fleet_link 가 잡아 `park_dock`(라인 트레이싱, **실물 미검증**)이 대신
돌고 성공까지 답한다 — 뒷캠 ArUco 인 줄 알고 있는데 다른 알고리즘이 도는, 가장 나쁜 종류의
조용한 실패다. 둘이 동시에 답하면 먼저 온 결과로 ⑤가 시작된다 — 팔에서 이미 밟은 함정이다
(CLAUDE.md `LIBI_ARM_VIA_BT`).

### [2026-07-31] `/is_docked` 를 **BT 가 선언한다**

위치로 판정하던 `dock_confirm.py` 가 `pi.sh` 에서 빠졌고(`pi.sh:203`), 그 기본 정점 이름
`주차장` 도 navgraph 에서 없어졌다. 즉 **아무도 그 토픽을 발행하지 않고 있었다.** 결과:

- `AlreadyDocked` 가 영원히 실패 → 충전소에 놓인 채 켜도 입구까지 나갔다 온다
- `UndockNotNeeded` 가 None 을 "도킹 아님"으로 읽어 **탈출을 건너뛴다** → nav2 "경로 없음"

위치로 추정하는 대신 **개루프 후진이 끝난 사실로 선언**한다(사용자 결정).

```
DockSettle 성공  →  DOCK_DECLARED = True
                    ↓  state_io 가 /is_docked (Bool, latched) 로 발행
                    ↓  providers 가 되받아 IS_DOCKED 를 채움
Undock 성공      →  DOCK_DECLARED = False
```

⚠️ **블랙보드의 `IS_DOCKED` 에 직접 쓰면 안 된다.** `providers.as_dict()` 가 매 tick 그 키를
구독값으로 덮어쓴다 — 써 봐야 다음 tick 에 지워진다. 그래서 선언용 키를 따로 두고 토픽을
한 바퀴 돌린다. 덤으로 `sim_battery`·FMS 같은 다른 구독자도 같은 값을 본다.

⚠️ **내리는 쪽이 핵심이다.** 안 내리면 다음 복귀에서 `AlreadyDocked` 가 낡은 True 를 보고
①~⑥을 통째로 건너뛴다 — 로봇은 도서관 한복판에 선 채 CHARGING 을 선언한다.

⚠️ 선언 전(None)에는 **아무것도 발행하지 않는다.** 모르는 것을 False 로 단정하면 그것도
판정이 된다.

⚠️ `dock_confirm.py` 를 되살리면 **같은 토픽에 발행자가 둘**이 된다(위치 판정 ↔ BT 선언).
서로 덮어쓴다.

### [2026-07-31] CHARGING 전이가 유지 시간에 막혀 복귀가 ①부터 재시작했다

실측 증상: 도킹이 완벽히 끝났는데 로봇이 충전소에서 **다시 나가** 입구로 갔다.

```
패널로 RETURNING 강제  →  hold_until = now + manual_hold_sec(300초)
도킹 완료(300초 안)    →  SetNextMode("CHARGING")
RequestTransition      →  _held("CHARGING") = True  →  FAILURE
루트 Sequence 실패      →  다음 tick 에 IsMode("RETURNING") 여전히 참
                        →  ReturnAndWatch 재초기화 → ReturnSteps 가 ①부터
```

유지 시간은 "로봇이 사람의 결정을 스스로 되돌리는 것"을 막으려고 있다. 그런데 **도킹은 이미
일어난 물리적 사실**이다. 그래서 `_ALWAYS_ALLOWED` 에 `CHARGING` 을 넣었다(ERROR·RETURNING 과
같은 이유 — 고장과 저전압도 기다릴 수 없다).

⚠️ 대안으로 "`DockSettle` 이 `hold_until` 을 지운다"가 있었는데 **더 넓다.** hold 를 통째로
지우면 사람이 누른 의도가 CHARGING 뿐 아니라 IDLE·PATROL 전이에도 다 풀린다. 지금 방식은
target 이 CHARGING 인 전이 하나만 뚫는다.

⚠️ **④의 취소 계약이 아직 없다.** timeout(180s) 뒤 BT 가 내는 `stop` 은 fleet_link 에서
nav2 만 끊는다 — 외부 ArUco 접근은 안 멈춘다. 그 상태로 `AbsorbFailure` 가 재시도하면
이전 접근과 새 접근이 겹친다. 외부 노드를 붙일 때 cancel + ack 를 같이 정할 것.

⚠️ **⑤는 `/cmd_vel` 을 직접 안 낸다.** twist_mux 의 `dock` 입력(`cmd_vel_dock`,
priority 120)으로 보낸다 — 비상정지(255)·hold(160)에는 지고 추종(100)·nav2(50)는 이긴다.
직접 발행 금지는 twist_mux.yaml 이 정한 것이고, 그 문이 있어서 주차 중에도 비상정지가
통한다. `RETURNING` 은 `MOTION_LOCKED_STATES` 에 없어 잠금(150)이 안 걸린다 —
그 목록에 `RETURNING` 을 넣으려는 사람은 이 입력이 같이 막힌다는 것을 먼저 봐야 한다.

⚠️ **⑤의 거리는 실측 보정 대상이다.** 개루프라 바퀴 슬립·모터 데드밴드만큼 어긋난다.
평지에서 ⑤만 한 번 돌려 자로 재고 `nudge_distance_m`/`nudge_speed_mps` 를 맞춘다.
(nav2 의 `regulated_linear_scaling_min_speed` 가 0.08 이라 그 아래는 못 미더운 구간이다)

**`AlignDock` → `DockSettle`.** 옛 노드는 `is_docked` 를 기다렸는데, 그 신호는 주차장
정점 반경 0.12m 판정(`dock_confirm.py`)이라 ④⑤를 지난 뒤에는 **이미 참이라 아무것도
검증하지 못했다.** 검증하는 척하는 게이트는 없느니만 못해서 시간 대기로 바꿨다(사용자
결정). 진짜 접촉 확인(충전 전류·전압 상승 등)이 생기면 **거기가 그 신호를 넣을 자리다.**

⚠️ **접점이 안 붙어도 CHARGING 을 선언한다.** `ChargingBranch` 의 이탈 조건은 fault 또는
`battery >= ready(40)` 뿐이다(`branches/charging.py`). 즉 충전이 실제로 안 되면 40% 에
영영 못 닿고, CHARGING 은 `MOTION_LOCKED_STATES` 라 바퀴도 잠긴 채 **방전까지 간다.**
"일정 시간 배터리가 안 오르면 fault" 감시는 아직 없다 — 넣는다면 `ChargingBranch` 다.

⚠️ **로봇팔 홈 복귀는 없앴다**(사용자 결정 2026-07-27 — 이 로봇에 팔이 없다).
**팔이 달린 로봇을 복귀시키기 전에 이 결정을 재검토해야 한다** — 팔이 펼쳐진 채
주행하면 서가에 부딪힌다. `ArmHomeDriver` 와 `main._boot_arm_home()` 자리는 남겨 뒀다.

**`CommandListener`를 두지 않는 이유** — 복귀 중인 로봇은 배터리가 15% 미만이다. 이 상태에서
`stop_request`로 세우면 충전소에 도달하지 못하고 방전된다.

**`ReturnNavigation` 내부 처리**

| 항목 | 처리 |
|---|---|
| 로봇팔 홈 복귀 | 주행 시작 전 반드시 수행. 기동 직후에는 팔 자세를 알 수 없다 |
| 도킹 재시도 | 최대 3회 (SR-18). 소진 시 `fault` 세우고 **RUNNING 유지** → 같은 tick에 `FaultDetected`가 ERROR로 |

기동 시 `[*] -> RETURNING`으로 진입하므로, 이 브랜치가 **부팅 직후 가장 먼저 실행되는 브랜치**다.

**신규 leaf: `ReturnNavigation`**

### 8. ERROR

```
ErrorBranch (Sequence, memory=False)
├── IsMode("ERROR")
├── CommandListener                             # recovered → IDLE
└── RequestTransition()
```

**공통 규약의 유일한 예외** — `ERROR`는 8개 브랜치 중 **`FaultDetected`를 두지 않는 유일한 브랜치**다.

**자동 이탈 경로를 두지 않는 이유** — `ERROR` 상태의 로봇은 **스스로 움직이지 않는다.**
배터리가 15% 미만이 되어도 `RETURNING`으로 가지 않는다. 고장 원인을 알 수 없는 상태에서
자율 주행을 재개하면 위험하기 때문이다.

**수동 조작 (SR-21)** — 브랜치에 주행 노드가 없다는 것 자체가 "자율 주행 금지"를 의미한다.
수동 조작은 BT 바깥의 teleop 경로로 처리한다.

**신규 leaf: 없음.**

---

## 로봇팔 서브트리 (`ArmExec` 내부)

> **작성 예정.** 8개 브랜치 설계 완료 후 착수한다.
> 대상: `perform_action` 수신 시 실행되는 파지 · 적재 트리. 전략 폴백(TOP → SIDE → 재인식)과 파지 검증을 포함한다.

## 관리자 추종 (`FollowExec`)

> **작성 예정.** `libi_perception` 패키지와 함께 붙인다.
> `WORKING` 브랜치의 dispatch Selector에 `NavigationExec` / `ArmExec`와 나란히 세 번째 형제로 들어간다.

---

## 테스트

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_modes/ros_ws/src/libi_modes
PYTHONPATH=. python3 -m pytest test/ -q
```

| 파일 | 내용 |
|---|---|
| `test_blackboard.py` | 키 유일성, 미설정 키 안전 read |
| `test_common_leaves.py` | 공통 leaf 개별 동작, 소비(consume) 규약 |
| `test_branches.py` | 전이 박스 간선별 1테스트 + 교착 시나리오 |
| `test_tree.py` | 우선순위·구조 불변식, **없어야 할 간선** 역방향 검증, 부팅 시퀀스 |

구조 불변식 테스트가 특히 중요하다 — `RequestTransition` 위치, Selector `memory`, `Parallel` 안
watchdog의 trailing `Running()`은 어기면 **조용히** 오동작하므로 테스트로 못박아 둔다.
