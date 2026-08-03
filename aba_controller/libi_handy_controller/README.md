# libi_handy_controller

LIBI 로봇팔(Handy) 보드 컨트롤러. 한 로봇의 **팔 보드**에서 돌며, 주행 보드(Drive)의
요청을 받아 팔을 움직이고 결과를 돌려준다. (한 로봇, 두 보드, 같은 `ROS_DOMAIN_ID`)

---

## ⚠️ 통합 전에 반드시 읽을 것 — 관제·주행 쪽은 다 들어갔다 (2026-07-30)

계약 확정 당일 저녁에 **크로스 서비스 작업 4건을 전부 구현했다.** 팔 담당자는 아래를
전제로 액션 서버만 만들면 된다.

| 서비스 | 무엇 | 어디 |
|---|---|---|
| `libi_interfaces` | `ArmTask.action` 정의 | `libi_modes/ros_ws/src/libi_interfaces/action/ArmTask.action` |
| `libi_modes` (주행) | `/fleet_cmd` → `arm_task` goal 중계, 결과를 `/fleet_cmd_result` 로 | `libi_modes/arm_task_map.py` · `ros/handy_action_driver.py` |
| `aba_fms_service` | leg 에 `object`/`from_place`/`to_place`/`tier`/`row`/`slot` | `app/fleet_orchestrator.py` · `app/fleet_dispatch_bridge.py` |
| `aba_service` | `books.tier`/`row` 컬럼 + 사서 입력칸 + 주문에 실어 보내기 | `app/models.py` · `frontend/.../books.tsx` · `app/routers/{ops,delivery}.py` |

대상 종류를 **`book:"바구니"` 문자열로 위장**하던 것은 없어졌다 — 이제 `object` 가
`book`/`basket` 을 직접 말한다.

### ⚠️ 팔을 붙이는 날 켜야 하는 스위치 — `LIBI_ARM_VIA_BT=1`

`/fleet_cmd{perform_action}` 은 **두 프로세스가 듣는다** — 주행 BT(`libi_modes`)와
`robot_agent` 의 `fleet_link`. FMS 는 `/fleet_cmd_result` 를 **처음 받은 것**으로 다리를
닫으므로, 둘이 다 답하면 **팔이 아직 움직이는 중에 다음 주행이 시작된다**(팔을 뻗은 채
로봇이 간다). 그래서 env 하나로 답할 쪽을 정한다.

| `LIBI_ARM_VIA_BT` | 팔 동작 | 답하는 쪽 |
|---|---|---|
| `0` (기본, 지금) | 안 함 — BT 가 스텁으로 통과시킨다 | `robot_agent` (즉시 성공) |
| `1` (팔 붙은 뒤) | `arm_task` 액션으로 실제 중계 | **BT** — 액션 result 를 받은 뒤 |

⚠️ **두 프로세스에 같은 값을 줘야 한다.** 한쪽만 켜면 둘이 답하거나(다리가 일찍 닫힘)
아무도 안 답한다(FMS 에 다리 타임아웃이 없어 주문이 영원히 안 닫힘).

### ⚠️ 여전히 유효한 함정 두 개

1. **`fleet_dispatch_bridge` 는 `leg.params` 의 키를 하나하나 명시적으로 복사한다.**
   지금 9개(`action`·`at`·`book`·`object`·`from_place`·`to_place`·`tier`·`row`·`slot`)를
   복사한다. **새 키를 추가하면 그쪽도 고쳐야 한다 — 안 고치면 값이 조용히 사라진다.**
2. **`place` 의 `to_place` 는 FMS 가 비워 보낸다.** 목적지가 `테이블` 인지 `안내데스크` 인지는
   정점의 정체를 알아야 정해지고 FMS 코어는 정점을 번호로만 다룬다. 주행 로봇 중계
   (`arm_task_map.place_of()`)가 `at` 정점 이름에서 유도한다. **팔이 받는 goal 은 항상
   다 채워져 있다** — 팔 쪽에서 신경 쓸 것은 없고, 정점 이름이 규칙에서 벗어나면
   goal 이 아예 안 나간다(조용히 틀린 곳에 놓는 것보다 낫다).

### 남은 것 — 데이터 입력 (코드 아님)

기존 도서 12권의 `shelf` 값은 **전부 `"셋째 줄"`** 이다. 3층인지 3번째 줄인지 코드로 알 수
없어 **자동 변환하지 않는다** — `0` 으로 두고 사서가 화면에서 다시 입력한다(12권이라 2분).
추측 변환은 팔이 조용히 틀린 칸을 잡게 만든다. 그때까지는 `tier`/`row` 가 `0` 으로 나가고
팔이 시각으로 찾는 경로를 탄다.

---

## 인터페이스 — 액션 (2026-07-30 확정)

**토픽이 아니라 ROS 2 액션이다.** 아래 `handy_cmd`/`handy_result` 토픽 골격은 이 결정
이전에 작성된 것이라 **액션 서버로 교체해야 한다.**

```
액션 이름 : arm_task
타입      : libi_interfaces/action/ArmTask   ← 아직 정의 파일 없음. 만들어야 한다
```

### Goal

| 필드 | 타입 | 값 |
|---|---|---|
| `action` | string | `pick` `place` `unload_to_floor` `load_from_box` `refill_box` |
| `object` | string | `book` · `basket` |
| `from_place` | string | `서가` `테이블` `안내데스크` `수거함` `리비바구니` `바닥` |
| `to_place` | string | 같은 6종 |
| `book` | string | 도서명. 바구니면 `""` |
| `tier` | uint8 | 서가 층 `1~3` (아래부터 1). 해당 없으면 `0` |
| `row` | uint8 | 서가 줄 `1~3` (마주 봤을 때 왼쪽부터 1). 해당 없으면 `0` |
| `slot` | uint8 | 리비바구니 칸 `1~3`. 해당 없으면 `0` |

`tier`/`row` 는 **서가에만**, `slot` 은 **리비바구니에만** 쓰인다. 바구니 액션 3가지는
바구니 전체를 드는 것이라 `book`·`tier`·`row`·`slot` 이 전부 비어 있다.

`from` 이 아니라 `from_place` 인 이유: `from` 은 파이썬 예약어라 ROS 2 가 생성한 Python
클래스에서 속성 이름으로 쓸 수 없다.

### Result / Feedback

```
Result   : bool ok,  string msg          # 실패 종류는 나누지 않는다
Feedback : string phase                  # approach grasp lift move release
```

취소는 액션 표준 상태(`CANCELED`)로 표현한다.

### 왜 토픽이 아니라 액션인가

취소·중복방어·진행보고·서버감지를 **직접 구현하지 않기 위해서**다. 2026-07-30 하루에
관제 명령이 조용히 사라지는 경로 3개를 고쳤고 그중 2개가 정지 전파·명령 유실이었다 —
전부 "요청/결과 상관관계와 취소를 손으로 구현한 층"에서 났다. 상세는 옵시디언
`presen/final/14 로봇팔 통합 - 토픽 대신 액션.md`.

### ⚠️ 안전 자세 계약

> **성공·실패·취소 어느 쪽으로 끝나도 팔은 안전 자세로 정리하고 물체를 놓거나 원위치한다.**

주행 로봇은 결과를 받으면 **팔이 접혔고 빈 손이라고 가정하고** 바로 출발한다. 팔 상태를
물어보지 않는다(`holding`/`stowed` 필드를 일부러 두지 않았다). `finally` 에서 지킨다.

### ⚠️ `MultiThreadedExecutor` 필수

`execute_callback` 이 십수 초 블로킹하는 동안 cancel 을 받으려면 별도 callback group +
`MultiThreadedExecutor` 여야 한다. 기본 executor 면 정지가 동작이 끝난 뒤에야 처리된다.

---

## 구조 (현재 — 토픽 기반, 교체 대상)

```
handy_core.py   순수 로직 — 요청 검증 + 팔 모션 콜러블 호출 (ROS·팔 없이 테스트됨)
handy_node.py   rclpy 껍데기 — handy_cmd 구독 / handy_result 발행   ← 액션 서버로 교체
```

- **팔 모션은 스텁**(`HandyCore._stub_motion`, 성공만 반환). 팔 담당자가 `motion` 콜러블을 채운다.
- `handy_core.py` 의 검증 어휘가 **바뀌었다** — `LOCATIONS`(`libi_basket`/`bookshelf`/…
  영문 5개)를 위 Goal 표의 **한글 6종**으로 교체해야 한다. 기존 목록에는 `바닥`이 없었다.
- ⚠️ **`handy_cmd` 를 발행하는 코드가 레포에 없다.** 이 노드는 지금 아무것도 받지 못한다.

## 빌드·실행

```bash
cd aba_controller/libi_handy_controller/ros_ws
colcon build && source install/setup.bash
ros2 run libi_handy_controller handy_node
```

## 테스트

```bash
cd src/libi_handy_controller && PYTHONPATH=".:$PYTHONPATH" python3 -m pytest test/ -q
```

## DDS 설정 — 안 맞추면 통신이 아예 안 된다

멀티캐스트가 차단된 공유기 환경이라 **도메인만 맞춰도 서로 안 보인다.**

| 변수 | 값 |
|---|---|
| `ROS_DOMAIN_ID` | **119** (주행 로봇과 같은 값) |
| `RMW_IMPLEMENTATION` | **`rmw_cyclonedds_cpp`** |
| `CYCLONEDDS_URI` | 정적 피어 XML — `localhost` + 주행 Pi IP + 관제 노트북 IP |

⚠️ **양방향이다.** 주행 Pi 쪽 피어 목록에도 팔 보드 IP 를 넣어야 한다 — 주행 로봇은
`.env` 의 `*_IP` 값으로 XML 을 자동 생성하므로(`scripts/_load_env.sh`) `.env` 에 한 줄 추가.

증상이 고약하다: `ros2 topic list`·`node list` 는 정상으로 보이는데 **메시지가 0건 건너간다.**
의심되면 `tr '\0' '\n' < /proc/<pid>/environ | grep -E "ROS_DOMAIN_ID|RMW_|CYCLONEDDS"`.

---

## 아직 (팔 담당자)

- 실제 팔 모션 구현 (스텁 → pymycobot / BT)
- `handy_node.py` 를 **액션 서버로 교체**
- `libi_interfaces` 에 `ArmTask.action` 정의 추가 (`ACTION_FILES` + `action_msgs` 의존)
- 기존 로직(`~/Downloads/basket/`) 이전 시 걸리는 것:
  `basket_action.py` 가 **import 시점에** 시리얼을 잡는다(재시작 시 포트 충돌),
  `grab_basket()` 이 `sleep` 합계 **17초 블로킹**,
  `jetcobot_basket.py` 의 `LOCAL_PC_IP` 하드코딩

## 상세 문서 (옵시디언)

- `프로젝트/arte/2026-07-30 로봇팔 인터페이스 - 신호 대응표.md` — 스키마 · 도서 · 바구니 · 골격코드 · 참고사항
- `프로젝트/arte/presen/final/14 로봇팔 통합 - 토픽 대신 액션.md` — 왜 액션인가
- `프로젝트/arte/2026-07-21 Handy(로봇팔) 인터페이스 요청서.md` — 최초 요청서(필드 이름이 다르다, 위 표가 정본)
