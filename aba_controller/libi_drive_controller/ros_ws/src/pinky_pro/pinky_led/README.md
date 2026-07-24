# pinky_led

Pinky 로봇의 WS281x LED 스트립 제어 패키지.

## 노드 두 개 — 동시에 띄울 수 없다

`rpi_ws281x` 는 한 프로세스만 스트립을 점유할 수 있으므로 **아래 둘 중 하나만** 실행한다.

| 노드 | 실행 | 역할 |
|---|---|---|
| `led_server` | `ros2 run pinky_led led_server` | `set_led` / `set_brightness` 서비스로 수동 제어 (기존) |
| `state_led` | `ros2 launch pinky_led state_led.launch.xml` | FSM 상태 토픽을 구독해 자동으로 색·패턴 출력 (신규) |

## state_led

`libi_modes` 가 발행하는 상태(`std_msgs/String`)를 구독해 LED 를 바꾼다.

```bash
ros2 launch pinky_led state_led.launch.xml state_topic:=<실제_토픽명>
```

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `config_path` | `<share>/config/led_state_map.yaml` | 상태→색·패턴 매핑 파일 |
| `state_topic` | `fsm_state` | ⚠️ 미확정 — libi_modes 발행 토픽명으로 맞출 것 |
| `tick_hz` | `20.0` | 패턴 갱신 주기 |

### 상태별 표시

| 상태 | 색상 | 패턴 | 주기 |
|---|---|---|---|
| `CHARGING` | 초록 | 호흡 | 4.0s |
| `IDLE` | 흰색 | 약한 상시 점등 | — |
| `PATROL` | 파랑 | 느린 흐름 | 3.0s |
| `SECURITY_PATROL` | 보라 | 느린 깜빡임 | 2.0s |
| `INTERACTING` | 청록 | 밝은 상시 점등 | — |
| `WORKING` | 주황 | 빠른 흐름 | 0.8s |
| `RETURNING` | 노랑 | 깜빡임 | 1.0s |
| `ERROR` | 빨강 | 빠른 깜빡임 | 0.25s |
| (상태 미수신) | 흰색 | 아주 빠른 깜빡임 | 0.2s |

### 색·패턴 바꾸기

`config/led_state_map.yaml` 만 고치면 된다. 코드 수정은 필요 없다.
야간 순찰 시 조도를 낮추려면 `brightness` 를 내린다 (예: `0.3`).

제약 두 가지 — 어기면 테스트가 실패한다.
- **빨강은 `ERROR` 전용.** 다른 상태에 빨강 계열을 쓸 수 없다.
- **어떤 두 상태도 `(pattern, period_sec, level)` 조합이 같으면 안 된다.** 색각 이상
  이용자도 움직임만으로 상태를 구분할 수 있어야 하기 때문이다.

상태 토픽이 `state_timeout_sec`(기본 3초) 이상 끊기면 흰색 빠른 깜빡임으로 바뀐다.

### bringup 에 넣으려면

현재 `pinky_bringup/launch/bringup_robot.launch.xml` 에는 LED 노드가 등록되어 있지 않다.
자동 기동이 필요하면 그 파일에 아래 한 줄을 추가한다 (다른 패키지 수정이므로 담당자 확인 후).

```xml
<include file="$(find-pkg-share pinky_led)/launch/state_led.launch.xml"/>
```

## 구조

세 층으로 나뉘어 있고, 위 두 층은 ROS·하드웨어 없이 테스트된다.

| 파일 | 역할 | 테스트 가능 |
|---|---|---|
| `patterns.py` | 순수 패턴 계산 (solid/breathing/blink/flow) | ✅ 개발 PC |
| `state_led_config.py` | YAML 매핑 로드·검증 | ✅ 개발 PC |
| `led_state_model.py` | 상태 추적 + 타임아웃 판정 → 프레임 | ✅ 개발 PC |
| `state_led_node.py` | rclpy 배선 + 실제 LED 쓰기 | ❌ Pi 에서만 |

`pinkyled.py` 의 `color_wipe` / `theater_chase` / `rainbow` 계열은 내부에서 `time.sleep()`
루프를 돌기 때문에 **이 노드에서는 절대 호출하지 않는다.** ROS2 콜백에서 부르면 `rclpy.spin()`
전체가 멈춘다. 대신 매 tick 계산한 프레임을 `set_pixel()` + `show()` 로 한 번에 밀어넣는다.

## 테스트

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led
PYTHONPATH=. python3 -m pytest test/test_patterns.py test/test_state_led_config.py test/test_led_state_model.py -q
```

→ 28 passed.

`state_led_node.py` 자체는 실물 Pi 에서만 검증 가능하다 (`rpi_ws281x` 가 Pi 전용).
