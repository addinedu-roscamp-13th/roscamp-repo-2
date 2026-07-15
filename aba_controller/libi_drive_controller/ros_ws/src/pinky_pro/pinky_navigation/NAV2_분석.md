# Pinky Navigation (Nav2) 소스 분석

> 대상: `pinky_navigation` 패키지
> 실행 명령:
> ```
> ros2 launch pinky_navigation bringup_launch.xml map:=/home/robotPrj/rosPkg/mymap123.yaml
> ```
> 분석일: 2026-06-30
> 핵심 요약: 이 시스템은 **Nav2 (Navigation2) 스택**이며, 작은 맵(1m×2m)과
> 아주 작은 로봇(반지름 6cm)에 맞춰 튜닝되어 있다.
> 경로 추종 제어는 **PID가 아니라 Regulated Pure Pursuit (RPP)** 이다.

---

## 1. 전체 자율주행 파이프라인 (데이터 흐름)

```
[목표점 지정 (RViz/웹)]
        │
        ▼
   bt_navigator  ──── Behavior Tree로 전체 흐름 지휘 (계획→추종→실패시 복구)
        │
        ├─►  planner_server   : "어디로 갈지" 전역 경로 생성 (NavFn)
        │           ▲ global_costmap (맵 + 장애물)
        │
        ├─►  controller_server: "어떻게 따라갈지" 지역 주행 제어 (RPP)
        │           ▲ local_costmap (실시간 LiDAR)
        │           │
        │           └─► cmd_vel_nav (목표 속도)
        │
        ├─►  behavior_server  : 막히면 복구 동작 (제자리회전/후진/대기)
        │
        ▼
  velocity_smoother  : 속도를 부드럽게 가공 → cmd_vel (최종)
        │
        ▼
   [모터 드라이버 / 펌웨어] ← 여기서 휠 속도 제어
```

동시에 `amcl`(파티클 필터)이 LiDAR로 맵 안에서 **로봇의 현재 위치를 추정**한다
(`map → odom` TF 발행). 위치추정(localization)과 주행(navigation)이 함께 돌아가는 게 핵심.

### launch 구조
- `bringup_launch.xml` : 전체 진입점. nav2 컨테이너 + localization + navigation 포함.
  - `localization_launch.xml` → `map_server`, `amcl`
  - `navigation_launch.xml`  → `controller_server`, `planner_server`,
    `behavior_server`, `bt_navigator`, `waypoint_follower`, `velocity_smoother`
- 기본 파라미터 파일: `params/nav2_params.yaml`
- `use_composition: True` → 모든 노드를 단일 컨테이너(`nav2_container`)에 적재.

---

## 2. AMCL — 위치 추정 (`nav2_params.yaml:1-48`)

맵 안에서 "내가 지금 어디 있나"를 LiDAR 스캔과 파티클 필터로 추정.

| 파라미터 | 값 | 의미 |
|---|---|---|
| `robot_model_type` | `DifferentialMotionModel` | 차동구동(좌우 바퀴) 로봇 모델 |
| `max_particles` / `min_particles` | 2000 / 500 | 위치 후보(파티클) 개수 |
| `recovery_alpha_fast/slow` | 0.1 / 0.001 | **위치를 잃으면 자동 재추정**(납치 복구). 원래 0이었는데 켬 |
| `update_min_d / a` | 0.15m / 0.1rad | 이만큼 움직여야 위치 갱신 |
| `set_initial_pose` + `initial_pose(0,0,0)` | True | 켤 때 **맵 원점(홈)에 로봇이 있다고 가정** → 부팅 즉시 위치 인식 |

> ⚠️ 즉, 로봇을 항상 **맵 제작 시작 지점(홈)** 에 놓고 켜야 위치가 맞는다.
> 다른 곳을 홈으로 하려면 그 지점의 맵 좌표로 `initial_pose`의 x, y, yaw를 바꾼다.

---

## 3. Planner — 전역 경로 생성 (`nav2_params.yaml:256-265`)

```yaml
GridBased:
  plugin: "nav2_navfn_planner::NavfnPlanner"
  tolerance: 0.5
  use_astar: false        # → Dijkstra 사용
  allow_unknown: true     # 미탐색 영역도 지나갈 수 있음
```

- **NavFn (Dijkstra)** 알고리즘으로 출발점→목표점 최단 경로를 격자(grid) 위에서 계산.
- `tolerance: 0.5` → 목표에 정확히 못 닿으면 0.5m 이내 가장 가까운 점까지 경로 생성.
- `global_costmap` 기준 1Hz로 갱신.

---

## 4. Controller — 경로 추종 제어 (★ 핵심, `nav2_params.yaml:77-160`)

**PID가 아니라 Regulated Pure Pursuit (RPP)**. 20Hz로 동작.

### PID vs RPP 차이

| | PID 제어 | Regulated Pure Pursuit (현재 설정) |
|---|---|---|
| 원리 | 목표값-현재값의 **오차(error)** 에 P·I·D 게인을 곱해 제어 | 경로 위 앞쪽 한 점(**lookahead point / carrot**)을 보고 향하는 곡률을 기하학적으로 계산 |
| 튜닝 파라미터 | Kp, Ki, Kd | `lookahead_dist`, `desired_linear_vel`, `max_angular_vel` 등 |
| 적분/미분항 | 있음 | 없음 |

### 속도/가속 제한
| 파라미터 | 값 | 의미 |
|---|---|---|
| `desired_linear_vel` | 0.10 m/s | 평상시 목표 전진 속도 (매우 느림 — 작은 로봇) |
| `max_angular_vel` | 0.7 rad/s | 최대 회전 속도 |
| `max_linear_accel/decel` | 0.3 / 0.5 | 가속/감속 한계 |

### 경로 추종 (Pure Pursuit 핵심)
| 파라미터 | 값 | 의미 |
|---|---|---|
| `lookahead_dist` | 0.20m | 경로상 **앞쪽 목표점(carrot)** 까지 거리. 이 점을 향해 곡선 주행 |
| `min/max_lookahead_dist` | 0.10 / 0.30 | 속도에 따라 lookahead 가변 |
| `use_velocity_scaled_lookahead_dist` | true | 빠르면 멀리, 느리면 가까이 봄 |

> 💡 주석에 따르면 원래 0.6/0.3/0.9였는데 맵이 너무 작아 "전방 충돌"로 판정돼
> 멈추는 문제가 있어 **1/3 수준으로 축소**했다.

### 회전 우선 로직 (`use_rotate_to_heading`)
이 로봇 동작의 가장 큰 특징:
- `rotate_to_heading_min_angle: 0.35` (약 20°) → 가야 할 방향과 머리 방향 차이가
  20°를 넘으면 **전진을 멈추고 제자리에서 먼저 회전**.
- `rotate_to_heading_angular_vel: 0.7` 로 회전.
- `use_rotate_to_heading_threshold: 0.15` 이내로 정렬되면 다시 전진.
- `allow_reversing: false` → 후진은 안 함.
- 목표 도착 시 `allow_final_rotation` → 마지막에 목표 방향(yaw)으로 고개 맞춤.

→ 결과적으로 **"멈춰서 돌고 → 직진 → 멈춰서 돌고"** 식의 깔끔한 주행 패턴.

### 안전 감속 (Regulated 부분 = RPP의 "R")
| 파라미터 | 값 | 의미 |
|---|---|---|
| `use_approach_velocity_scaling` | true | 목표 근처에서 감속 |
| `min_approach_linear_velocity` | 0.05 | 접근 시 최소 속도 |
| `use_cost_regulated_linear_velocity_scaling` | true | **장애물에 가까우면 자동 감속** |
| `regulated_linear_scaling_min_radius` | 0.20 | 곡률 반경이 작으면(급커브) 감속 |
| `cost_scaling_dist` | 0.10 | 장애물 0.1m 이내부터 감속 시작 |

### 도착 판정 (Goal / Progress checker)
- `general_goal_checker` (SimpleGoalChecker): `xy_goal_tolerance: 0.05`(5cm),
  `yaw_goal_tolerance: 0.15`(약 8.5°) 이내면 도착으로 인정.
- `progress_checker` (SimpleProgressChecker): 10초 안에 0.15m 못 움직이면 실패 처리.

---

## 5. Costmap — 장애물 인식 (`nav2_params.yaml:163-240`)

| 항목 | local (실시간) | global (전체) |
|---|---|---|
| 기준 프레임 | odom | map |
| 크기 | 3×3m 롤링윈도우 | 맵 전체 |
| 갱신 | 5Hz | 1Hz |
| 레이어 | voxel + inflation | static + obstacle + inflation |

- `robot_radius: 0.06` → 로봇을 반지름 6cm 원으로 간주.
- `resolution: 0.02` → 2cm 격자 (작은 로봇이라 고해상도).
- `inflation_radius: 0.08`, `cost_scaling_factor: 5.0` → 장애물 주변 8cm를 "위험구역"으로
  부풀려 **벽에 밀착 주행 방지** (주석상 튜닝됨).
- LiDAR(`/scan`)로 장애물 마킹, 최대 2.5m까지 인식.

---

## 6. Behavior — 복구 동작 (`nav2_params.yaml:276-302`)

길이 막히거나 추종 실패 시 bt_navigator가 호출:
- `spin` (제자리 회전), `backup` (후진), `drive_on_heading` (직진),
  `wait` (대기), `assisted_teleop`.

---

## 7. Velocity Smoother — 최종 속도 가공 (`nav2_params.yaml:315-328`)

컨트롤러가 낸 `cmd_vel_nav`를 받아 부드럽게 만든 뒤 최종 `cmd_vel`로 발행
(`navigation_launch.xml:85-86`의 remap).
- `max_velocity: [0.25, 0.0, 1.5]` → [전진, 횡(0=차동구동 불가), 회전] 한계.
- `feedback: OPEN_LOOP` → odom 피드백 없이 직전 명령 기준으로 부드럽게.

---

## 정리

| 단계 | 담당 | 알고리즘 |
|---|---|---|
| 어디 있나 | amcl | 파티클 필터 (AMCL) |
| 어디로 갈까 | planner | NavFn / Dijkstra |
| 어떻게 갈까 | controller | **Regulated Pure Pursuit (PID 아님)** |
| 막히면 | behavior | spin/backup/wait |
| 속도 다듬기 | velocity_smoother | open-loop 스무딩 |

전체적으로 **"매우 작은 로봇 + 좁은 맵"** 에 맞춰 속도(0.1m/s)·lookahead·inflation을
모두 줄이고, **회전 우선 + 장애물 근접 자동 감속**으로 안전하게 도는 것이 이 설정의 특징.

> 실제 휠 단위 PID는 이 패키지가 아니라 모터 드라이버/펌웨어 계층에 있다
> (cmd_vel → 바퀴 RPM 변환 단계).
