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
> - 여기 미션 BT(75노드) — `/libi/bt_snapshot` 으로 발행
> - `../libi_modes/ros_ws/src/libi_perception/recovery_bt.py` 추종 회복 BT — 별도 프로세스라
>   `/libi/follow_bt_snapshot` 으로 내보내 미션 BT 의 `FollowExec` **밑에 접붙인다**
> - (`aba_ai_service/follower_BT/` 는 py_trees 가 아니라 자체 상태기계다 — 대상 아님)

```
aba_controller/libi_modes/ros_ws/src/libi_modes/
├── libi_modes/
│   ├── tree.py                   루트 조립 (Parallel[Topics2BB, Priorities])
│   ├── registry.py               상태 → 브랜치 매핑 + 전이 테이블 (단일 진실 공급원)
│   ├── blackboard.py             blackboard 키 상수 + 안전 read 헬퍼
│   ├── common/                   모든 브랜치가 공유하는 leaf
│   └── branches/                 상태별 브랜치 8개
├── config/params.yaml            임계값 (40 / 80 / 15 등)
└── test/                         91 tests
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

> ⚠️ **정지가 실제로는 안 먹는다 (확인된 결함, 길잡이만의 문제 아님).**
> `mission_stop` → `mission.stop_mission()` → `ros_bridge.cancel_nav()` 까지는 가지만,
> `cancel_nav()` 가 취소하는 `_active_goal_handle` 은 채워지지 않는다 — `send_nav_goal()` 이
> `send_goal_async()` 결과에 `_on_goal_response` 콜백을 달지 않기 때문이다
> (`ros_bridge.py:486` vs `:494,512`). 그래서 `goal` 로 나가는 **모든 주행**(배달·순회·복귀)이
> 취소되지 않는다. `robot_agent` 소유라 실기 확인 없이 손대지 않았다 — 고칠 때 한 줄이다:
> `send_goal_async(...).add_done_callback(self._on_goal_response)`.

> ⚠️ **아직 이 토픽을 발행하는 쪽이 없다.** 값이 `None`이면 `GuideExec`은 "감시 없음"으로 읽고
> 그냥 주행한다(감시 없는 배포에서 길잡이가 통째로 죽는 것보다 낫다). 즉 **지금은 사람을
> 놓쳐도 계속 간다.** 발행할 쪽은 `libi_perception`이다 — `DetectionReceiver.latest()`가 이미
> "지금 보이나"를 알고 있다. 그래서 `btNodeFlags.ts`에 `GuideExec: "partial"`로 적혀 있다.

테스트: `test/test_guide_exec.py` (분류 분리·유예·실제 정지·재개·포기·도착·감시없음)

### 7. RETURNING

```
ReturningBranch (Sequence, memory=False)
├── IsMode("RETURNING")
├── Parallel(SuccessOnOne)
│   ├── Sequence(memory=True)                   # 복귀 1회
│   │   ├── ReturnNavigation                    # 팔 홈 복귀 → 충전소 주행 → 도킹 (3회 재시도)
│   │   └── SetNextMode("CHARGING")
│   └── exit_watchdog([FaultDetected])
└── RequestTransition()
```

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
