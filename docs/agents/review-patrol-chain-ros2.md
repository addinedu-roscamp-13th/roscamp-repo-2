# 순찰 체인 ros2/nav2 리뷰 — 2026-07-26

리뷰 범위: `aba_controller/libi_modes`(순찰 관련부) · `aba_fms_service/fleet_ws/src/libi_fleet` ·
`scripts/laptop/*` · `aba_fms_service/scripts/robot_state_adapter.py` ·
`aba_fms_service/backend/app/fleet_link.py` · `fleet_dispatch_bridge.py` — 읽기는 전체,
수정은 Task 1~7이 이미 건드린 파일 안에서만(Global Constraints 고정).
리뷰 렌즈: `ros2-engineering-skills`(nodes-executors / communication / lifecycle-components /
debugging) + `nav2-navigation-skill`.

## 요약

수정 1건 / 보고만 9건 / 확인했고 문제 없음 7건.

Task 1~7이 이미 다룬 항목(어댑터 무증상 대기, 이중 shutdown, 시그널 트랩, pid 신뢰,
0대/stale 경고, sim.sh 주석, nav2 launch 경로 해석)과 이미 알려진
`CMakeLists.txt:79`(`cbs_planner.cpp` 경로 오류)는 새 발견으로 다시 보고하지 않는다.

## 수정한 것 (Task 1~7 범위 안)

| # | 파일 | 문제 | 조치 |
|---|---|---|---|
| 1 | `scripts/laptop/robot-link.sh:117-123` | `_is_adapter_pid()` — 정의만 있고 아무도 호출하지 않는 죽은 코드. 자기 주석은 "진단·테스트용"이라 적어 뒀지만 `scripts/laptop/tests/test_robot_link_lifecycle.sh`는 이 함수를 쓰지 않고 자체 `adapter_pid()`/`adapter_alive()`를 따로 만들어 쓴다(레포 전체 grep으로 호출부 0건 확인). | 함수 삭제(7줄). `_adapter_pids_for_key()`는 `stop_by_key()`가 계속 쓰므로 그대로 둠. 삭제 후 `bash -n` 구문 검사 통과, 세 테스트 모두 재실행해 회귀 없음 확인(아래 "실행한 명령" 참고). |

## 보고만 한 것 (범위 밖 — 사용자 결정 필요)

| # | 파일 | 문제 | 왜 범위 밖인가 | 권고 |
|---|---|---|---|---|
| 1 | `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/main.py:278-288` | 순찰을 실제로 실행하는 미션 FSM 노드(`FsmNode`)의 `main()`이 `robot_state_adapter.py`와 **같은 이중 shutdown 경합**에 절반만 방어돼 있다. `finally`의 `if rclpy.ok(): rclpy.shutdown()` 가드는 있어 "이미 종료된 컨텍스트에 shutdown 재호출" 크래시는 막지만, `except KeyboardInterrupt:` 하나만 잡고 `ExternalShutdownException`/`RCLError`/`AttributeError`(robot_state_adapter.py 주석에 실측 기록된 세 가지)는 잡지 않는다. `rclpy.spin()`이 그중 하나를 던지면 `finally`는 안전하게 실행되지만 예외 자체는 그대로 다시 던져져, **정상적인 SIGTERM 종료(FMS 재시작·pm2 재기동·수동 정지)마다 트레이스백이 찍히고 프로세스가 비정상 종료 코드로 끝난다.** 순찰을 도는 BT 자체가 이 노드이므로 셋 중 가장 중요도가 높다. Task 8 기계 점검(`grep -q ExternalShutdownException`)에서 실제로 "미처리"로 걸림. | `main.py`는 Task 1~7이 건드린 파일 목록에 없다(`robot_state_adapter.py`만 허용). libi_modes는 다른 담당자 서비스. | `robot_state_adapter.py`의 `log_shutdown()` + `except Exception: if rclpy.ok(): raise` 패턴을 그대로 이식. |
| 2 | `aba_controller/libi_drive_controller/scripts/path_request_driver.py:276-284` | 순찰 체인의 **주행 폴백 경로**(`LIBI_NAV_VIA_BT=0`일 때 fleet_node의 `/robot_path_requests`를 직접 nav2로 넘기는 노드)의 `main()`은 가드가 **전혀 없는** 원본 패턴(`except KeyboardInterrupt: pass` → `finally: rclpy.shutdown()`)이다. robot_state_adapter.py Task 2 수정 전과 동일한 모양 — "이미 종료된 컨텍스트에 shutdown 재호출" 크래시까지 그대로 노출돼 있다. | `aba_controller/libi_drive_controller/scripts/`는 허용 목록 밖(로봇 온보드, 다른 담당). | 같은 패턴 이식. 우선순위는 낮음 — 기본 경로가 아니고(`NAV_VIA_BT` 기본 켜짐) 수동으로만 띄우는 폴백이지만, 켜는 순간 이 결함을 그대로 물려받는다. |
| 3 | `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/{pinky_led,pinky_lcd_server,pinky_emotion,pinky_buzzer,pinky_bringup,pinky_navigation,pinky_waypoint}/**`, `aba_controller/libi_drive_controller/scripts/{dock_confirm,sim_battery,set_initial_pose}.py`, `aba_controller/libi_drive_controller/robot_agent/app/core/ros_node.py`, `aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/follow_node.py`, `aba_controller/libi_handy_controller/.../handy_node.py` | Task 8 브리핑이 지정한 기계 점검(`grep -rl rclpy.shutdown() ... \| grep -v ExternalShutdownException`)에 전부 걸린다 — 같은 미가드 shutdown 모양. | 전부 로봇 온보드(`aba_controller/**`) 주변장치/보조 노드로 허용 목록 밖. 순찰 체인 자체(주행·FSM·상태 어댑터)에는 직접 관여하지 않는다(LED/LCD/부저/표정/배터리시뮬/초기위치/팔로우 등). | 저우선순위 — 이 노드들이 죽는다고 순찰이 멈추지는 않는다. 다만 같은 결함이 이렇게 넓게 퍼져 있다는 것 자체가, `rclpy.spin()` 보일러플레이트를 만들 때 이 가드를 공용 헬퍼로 빼는 게 낫다는 신호다(예: `ros_env.py`처럼 표준 라이브러리만 쓰는 공용 모듈 하나). |
| 4 | `aba_fms_service/fleet_ws/src/libi_fleet/include/libi_fleet/fleet_task.hpp:49` | `constexpr int kStuckTicks = 100;` — 정의만 있고 코드에서 전혀 참조되지 않는다(레포 전체 grep, 이 정의 줄 자체를 빼면 0건). `fleet_node.cpp:88-93`의 주석이 "원래 상수 `kStuckTicks`로 항상 켜져 있었는데 sim에서 오탐이 나 런타임 파라미터(`stuck_ticks_`, 기본 0)로 바꿨다"고 정확히 설명하고 있어 — 상수 자체는 그 리팩터 이후 남은 잔재다. | `fleet_task.hpp`는 허용 목록 밖(`fleet_node.cpp`만 승인, 그것도 관측성 2종 범위 한정). | `kStuckTicks` 삭제. 값(100, ≈15s@150ms)이 필요하면 `fleet_node.cpp`의 `stuck_ticks_` 파라미터 기본값 주석에 참고값으로만 남기면 충분. |
| 5 | `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/ros/state_io.py:107,121` | `typed_state_topic="fsm_state_typed"`로 타입 있는 `FsmState` 메시지를 매 tick 발행하지만, 레포 전체(빌드 산출물·venv 제외)에 이 토픽의 구독자가 **하나도 없다**. 파일 상단 docstring은 "타입 있는 `FsmState`도 같이 내보내지만(같은 도메인의 **타입 소비자용**)"이라고 적어, 소비자가 존재하는 것처럼 서술한다 — 사실과 다른 주석(체크리스트 #6)이면서 동시에 죽은 출력(#5)이다. | `state_io.py`는 Task 1~7이 건드리지 않았다. | 실제 타입 소비자를 붙이거나, 없다면 docstring에서 "현재 소비자 없음(예비)"로 정정. 지우는 것도 방법이지만 도메인 브릿지를 안 타는 same-domain 전용 출력이라 향후 C++ 소비자(예: fleet_node가 JSON 파싱 대신 타입 구독으로 갈아탈 때)를 겨냥한 의도적 선행 배선일 수 있어 판단은 담당자 몫. |
| 6 | `aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py:51`, `aba_fms_service/backend/app/fsm_model.py:103` | 전이표(둘 다 "single source of truth"라고 스스로 주장하고 FMS 패널이 그대로 그린다)에 `("SECURITY_PATROL", "IDLE", "security_patrol_complete / stop_request")`가 있다. 그런데 `security_patrol_complete`를 발행하는 코드가 레포 어디에도 없다 — `branches/security_patrol.py`의 `_COMMAND_MAP = {"stop_request": "IDLE"}`(딱 하나)와 그 파일 docstring "does NOT end after one lap"이 실제 동작이다. 관제 패널을 보는 사람은 이 라벨을 보고 "한 바퀴 돌면 자동으로 끝난다"고 오해할 수 있다. | `registry.py`·`fsm_model.py` 둘 다 허용 목록 밖(그리고 두 파일을 항상 같이 고쳐야 하는 쌍이라 더더욱 범위 밖 단독 수정이 위험). | 라벨에서 `security_patrol_complete /` 부분을 빼서 `stop_request`만 남기거나, 정말 "한 바퀴 완료" 신호를 넣을 계획이면 구현 후 라벨을 맞춘다. |
| 7 | `aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp` (`on_submit`, `on_timer`, `Auction::assign`) | `robots_`가 만료되지 않는다는 사실 자체는 Task 5가 경고로 다뤘지만, **경고만 남기고 판단 로직은 그대로**다. `on_timer`의 도착 판정(`d = hypot(r.x-tv.x, r.y-tv.y)`)과 `on_submit`/`Auction::assign`의 배차 판단은 여전히 `robots_[robot].x/y`를 무조건 신뢰한다. stale 로봇이 마침 어떤 노드 반경 안에서 멈춘 상태로 소식이 끊기면, 그 노드에서 "도착"으로 오판해 다음 노드로 넘어가거나(로봇은 실제로 그 자리에 없는데) task를 완료 처리할 수 있다. 경매도 stale 로봇을 그대로 후보에 포함시켜 낙찰시킬 수 있다(`is_dispatchable`만 보고 `last_state_at_`은 안 봄). | `fleet_node.cpp`는 이번 Task에서 "관측성 2종"만 승인된 범위 — 배차·도착 판정 로직 변경은 다른 담당자 결정 필요. | stale 로봇을 `on_submit`의 경매 후보·`on_timer`의 자동 순회 재부여에서 제외하거나, 최소한 도착 판정에서 `last_state_at_`이 `kRobotStaleSec`을 넘긴 로봇은 "도착 아님"으로 동결하는 안전장치를 다음 담당 Task로 제안. |
| 8 | `aba_fms_service/config/domain_bridge.template.yaml:56-59` (및 `config/generated/domain_bridge_*.yaml` 동일 주석) | `robot_path_requests` 항목 주석이 "로봇 쪽에서 `scripts/path_request_driver.py`가 이걸 받아 nav2 `NavigateToPose`로 옮긴다"고만 적혀 있다. 그런데 `path_request_driver.py` 자신의 최신 docstring(1~15행)은 이제 그게 **기본 경로가 아니라 BT 우회 폴백**이라고 분명히 밝힌다(기본은 FMS 백엔드 `fleet_link.py`가 같은 토픽을 도메인 86에서 직접 구독해 `/fleet_cmd{navigate}`로 BT에 넘긴다). yaml 주석만 읽으면 로봇이 지금도 이 경로로 직접 움직인다고 오해하기 쉽다. | `config/domain_bridge.template.yaml`·`config/generated/*`는 허용 목록 밖이며 자동 생성 파일(직접 실행 금지 주석이 파일 맨 위에 있음)이라 손대는 것 자체가 부적절. | `gen_domain_bridges.py`의 템플릿 소스에서 이 주석에 "폴백(기본 비활성)" 한 줄만 추가. 우선순위 낮음(동작에는 영향 없음, 순수 문서 정합성). |
| 9 | `aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp` 생성자(`declare_parameter<...>` 15곳) | 파라미터는 전부 선언돼 있고(선언 없이 쓰는 값 없음) 범위 설명도 주석으로 상세하지만, `rcl_interfaces::msg::ParameterDescriptor`로 `FloatingPointRange`/`IntegerRange`를 지정한 곳은 하나도 없다. 예: `arrive_radius`에 음수나 `prefetch_radius`에 `NaN`을 런타임에 줘도 파라미터 서버가 거부하지 않고 그대로 들어가 `on_timer`의 거리 판정을 망가뜨릴 수 있다. | `fleet_node.cpp` 승인 범위(관측성 2종)를 벗어남 — 파라미터 검증 로직 추가는 범위 밖. | 낮은 우선순위. 런치 스크립트로만 값을 주는 내부 파라미터라 실사용 리스크는 작지만, 다음에 `fleet_node.cpp`를 만질 담당자가 `ParameterDescriptor` range를 붙이는 김에 정리할 만한 항목으로 남긴다. |

**참고 — 이미 알려진 항목(새 발견 아님):** `aba_fms_service/fleet_ws/src/libi_fleet/CMakeLists.txt:79`가
`ament_add_gtest(test_cbs_planner test/test_cbs_planner.cpp ../planning/cbs_planner.cpp)`로
`../planning/cbs_planner.cpp`를 참조하지만 실제 파일은 `planning/cbs_planner.cpp`(패키지 루트
기준, `..` 없이)에 있다. 경로 확인 결과 여전히 어긋나 있음을 재확인했다 — `BUILD_TESTING=ON`
설정이 계속 실패한다. 이미 보고된 사안이라 여기서는 존재만 재확인하고 고치지 않는다.

## 확인했고 문제 없던 것

**QoS 매칭 — 체인 전체를 발행/구독 양쪽 다 추적했다.**
- `amcl_pose`: nav2 AMCL(로봇) → domain_bridge(퍼블리셔 QoS 자동 매칭, `TRANSIENT_LOCAL`/`RELIABLE`) →
  `robot_state_adapter.py:44-49`의 `LATCHED_QOS`(`TRANSIENT_LOCAL`/`RELIABLE`/depth 10)와
  `libi_modes/ros/providers.py:41-46`의 `_LATCHED`(`TRANSIENT_LOCAL`/`RELIABLE`/depth 1, 같은 도메인 직접
  구독) 둘 다 정확히 맞춘다. 둘 다 이유를 주석으로 남겨 놨다(맞추지 않으면 "구독은 되는데
  아무 메시지도 안 옴"이 되는 이유까지).
- `/robot_state`: `robot_state_adapter.py:86`(기본 QoS depth 10 = RELIABLE/VOLATILE) ↔
  `fleet_node.cpp:191-193`(기본 QoS depth 10) — 양쪽 다 기본값이라 호환.
- `/libi/fsm_state`: `state_io.py:118`(기본 depth 10) → domain_bridge(자동 매칭) →
  `fleet_node.cpp:216-218`(기본 depth 10) — 호환.
- `/robot_path_requests`: `fleet_node.cpp:194`가 명시적으로 `rclcpp::QoS(10).reliable()`을 쓰고,
  `fleet_link.py:561`(FMS 백엔드, 같은 도메인 86, 브릿지 없음)과 `path_request_driver.py:106`(로봇
  도메인, 브릿지 역방향 경유)이 둘 다 기본 QoS(depth 10 = RELIABLE)로 받는다 — 호환.
- `battery/percent`: `battery_publisher.py`(기본 depth 10) → domain_bridge → `robot_state_adapter.py:84`,
  `providers.py:78` 둘 다 기본 depth 10 — 호환.
- `/fleet_cmd`·`/fleet_cmd_result`: 양쪽 다 기본 QoS로 통일돼 있어 QoS 불일치로 인한 유실 경로는
  없다.

**콜백/타이머 안 블로킹 — 순찰 체인에 동기 서비스 호출이나 sleep이 없다.**
- `fleet_node.cpp:on_timer`(150ms 주기)는 맵/벡터 연산과 Dijkstra(작은 navgraph, 유계)뿐이고 I/O나
  서비스 호출이 없다.
- `robot_state_adapter.py:_tick`(2Hz)은 캐시된 pose/battery를 읽어 발행만 한다.
- `fleet_link.py:_call`(`app/fleet_link.py:333-352`)은 `async_send_request` + `threading.Event` 대기
  패턴을 쓴다 — `spin_until_future_complete`를 executor 콜백 안에서 쓰지 않는다는 이 스킬의
  핵심 규칙(`references/communication.md` §3, `references/nodes-executors.md` §3 "Critical pattern")을
  정확히 지킨다. 주석도 "future.result()를 곧장 블로킹하면 어느 스레드가 spin하는지에 따라
  굳는다"고 정확히 이유를 남겨 뒀다.
- `fleet_cmd_driver.py`(`start()`/`poll()`/`stop()`)도 비동기 발행 + id 매칭 폴링만 하고 절대
  블로킹하지 않는다는 것을 자체 docstring이 명시하고 실제 구현도 그렇다.

**"조용한 실패"의 다른 형제를 찾기 위해 libi_modes 리프 전수 확인.**
`battery_check.py`, `is_mode.py`, `command_listener.py`, `request_transition.py`,
`fault_detected.py`, `command_timeout.py`, `watchdog.py`, `return_navigation.py`,
`working_actions.py`(`NavigationExec`/`ArmExec`/`UnwiredDriver`/`FollowExec`)를 전부 읽었다.
전부 실패 시 로그를 남기거나 상태(next_mode=ERROR 등)를 바꾸고, RUNNING으로 무한정 눌러앉는
경로가 없다(`CommandTimeout`이 WORKING의 유일한 탈출구임을 스스로 문서화). 이번 사고와 같은
"프로세스는 살아 있는데 로그도 상태변화도 없다" 모양의 새 사례는 못 찾았다 — 위 report-only
#1(main.py 이중 shutdown)과 #7(stale robots_)이 가장 근접한 사촌이다.

**fleet_ws C++ 플러그인 로직 — `auction.cpp`, `grant_all_traffic.cpp`, `reservation_deadlock.cpp`,
`navgraph.cpp`, `patrol_cycle.cpp`.** 배차(완주 가능성 관문), 교착 감지(DFS wait-for 사이클),
경계 순회 생성(우수법) 모두 실패 시 빈 문자열/빈 벡터를 명확히 반환하고 호출부가 그 값을
확인한다(`on_submit`의 `if (robot.empty())`, `make_patrol_path`의 `if (path.size() < 2) return`
등). `GrantAllTraffic`은 "실사용 금지" 주석이 달린 디버그 전용 플러그인임을 스스로 밝힌다.

**Task 1~7이 이번에 건드린 파일 자체(`robot-link.sh`, `kill.sh`, `sim.sh`, `robot_state_adapter.py`,
`fleet_node.cpp`, `driving.py`, `ros_env.py`)를 다시 검토** — 로직 자체에 새 결함은 없었다.
`_is_adapter_pid()` 삭제(수정 #1)를 빼면 전부 정상. 세 테스트 스위트가 수정 전/후 모두
통과한다(아래 실행 기록).

**libi_modes 파라미터 선언 — main.py(`FsmNode`), 각 브랜치.** `declare_parameter` 없이 파라미터를
읽는 곳이 없다. `disabled_branches`처럼 알 수 없는 값이 들어오면 경고 후 무시하는 방어도
있다(`main.py:_resolve_disabled_branches`).

## 실행한 명령

```bash
cd /home/ane/personal_repo/aba_project

# Step 6 — 이중 shutdown 패턴 기계 점검 (브리핑 원문)
for f in $(grep -rl "rclpy.shutdown()" aba_fms_service/scripts/ aba_controller/ --include="*.py" | grep -v "\.venv"); do
  grep -q "ExternalShutdownException" "$f" || echo "미처리: $f"
done

# Step 4 — 범위 안 수정(_is_adapter_pid 삭제) 전/후 회귀 확인
./scripts/laptop/tests/test_robot_link_lifecycle.sh; echo "rc=$?"
./scripts/laptop/tests/test_nav2_command_resolves.sh; echo "rc=$?"
aba_fms_service/backend/.venv/bin/python -m pytest aba_fms_service/backend/tests/test_robot_state_adapter_shutdown.py -q

# Step 5 — 변경 범위 확인 (커밋하지 않음)
git status --short && git diff --stat
```

결과: 셸 테스트 둘 다 `rc=0`(수정 전/후 동일), pytest `2 passed`. `git diff --stat`은
`driving.py`·`fleet_node.cpp`·`robot_state_adapter.py`·`kill.sh`·`robot-link.sh`·`sim.sh` 6개
파일만 보여주며 전부 Global Constraints의 허용 목록 안이다.
