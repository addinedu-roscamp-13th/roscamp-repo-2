# scripts/ — 최상위 런처

서비스별로 흩어진 실행 명령을, **어디서 실행하는지(pi/laptop)** 로 나눠 한곳에 모았다.
각 스크립트는 얇은 위임 래퍼다 — 실제 로직은 기존 서비스 스크립트에 그대로 있고, 여기서는
**IP 를 `.env` 에서 채우고, 로봇 이름을 인자로 받고, 실행 위치를 레포 루트로 고정**한다.

## 왜 이 폴더인가

- **실행 위치 무관.** 모든 스크립트가 자기 위치를 역산해 `REPO_ROOT` 를 잡고 `cd` 한다
  (`_common.sh`). 아무 데서 실행해도 되고, "다른 폴더에서 돌리면 경로 에러" 가 사라진다.
- **로봇 지정은 인자로.** `FSM_ROBOT_ID=pinky3 ...` 대신 `./pi.sh pinky3`.
- **IP 는 `.env` 한곳.** `LAPTOP_IP`, `PINKY{1,2,3}_IP` (루트 `.env`, `.env.example` 참고).

폴더는 **실행 위치**로 나뉜다: `all/`(한 방 기동) · `drive-pi/`(로봇 주행 보드) ·
`handy-pi/`(로봇 팔 보드) · `laptop/`(관제 서버) · `ui/`(웹 UI).
(로봇은 한 대지만 보드가 둘 — 같은 `ROS_DOMAIN_ID`)

**평소에는 `all/` 만 쓴다.** 아래 폴더별 스크립트는 `all/` 이 안에서 부르는 부품이고,
하나씩 따로 띄우는 건 디버깅할 때뿐이다 — 손으로 조합하면 은퇴한 경로를 같이 띄우기 쉽다
(아래 "⚠️ UDP:6002 추종 주행 경로는 은퇴했다" 참고).

## `.env` 준비

```bash
cp .env.example .env    # 레포 루트에서
# LAPTOP_IP / PINKY{N}_IP 를 실제 IP 로 채운다
```

> CycloneDDS 피어도 이 `.env` 에서 만들어진다 — 아래 참고.

## CycloneDDS 피어 — `.env` 의 `*_IP` 가 곧 피어다

공유기가 멀티캐스트를 막아 DDS 디스커버리가 자동으로 안 되므로 **정적 피어**로 서로를
찾게 한다. 예전에는 그 목록이 `ros_ws/cyclonedds.xml` 과 각 머신 `~/.bashrc` 두 곳에
손으로 박혀 있었다. 로봇을 늘리면 **IP 를 안 넣은 머신만 에러 없이 서로 안 보였다.**

지금은 `scripts/_load_env.sh` 가 `.env` 에서 만든다:

- `*_IP` 로 끝나는 값(`LAPTOP_IP`·`PINKY{N}_IP`)이 전부 피어가 된다. `localhost` 는 항상 포함.
- 옛 망 머신이 아직 필요하면 아무 이름이나 `*_IP` 로 넣으면 된다 — 예: `OLDFMS_IP=192.168.0.19`.
- **셸/`~/.bashrc` 가 이미 `CYCLONEDDS_URI` 를 정했으면 그 값이 이긴다.** 그 경우 `.env` 에만
  있고 그 값에 없는 IP 를 찾아 경고를 찍는다:

  ```
  [env] ⚠ 셸의 CYCLONEDDS_URI 에 없는 .env IP: 172.30.1.81 — 그 머신과는 DDS 가 조용히 안 붙는다.
  ```

  완전 자동으로 가려면 각 머신 `~/.bashrc` 의 `CYCLONEDDS_URI` export 를 지운다(한 번만).
  pinky-3 에는 이미 적용했다 — bashrc 가 `.env` 에서 뽑아 쓴다.

여전히 유효한 두 가지: **`localhost` 는 필수**(같은 머신 안 nav2·fleet_link·bringup 디스커버리),
**모든 머신이 같은 RMW(cyclonedds)** 여야 한다. 피어는 "찾아갈 주소"일 뿐이고 실제 통신
여부는 `ROS_DOMAIN_ID` 가 정한다.

### 필요 패키지

CycloneDDS 통신에 꼭 필요한 것(**모든 머신**):

```bash
sudo apt install -y ros-jazzy-rmw-cyclonedds-cpp   # RMW (librmw_cyclonedds_cpp.so 제공)
```

브릿지를 서버/노트북에서 돌린다면:

```bash
sudo apt install -y ros-jazzy-domain-bridge        # ros-domain-bridge.sh / sim.sh 브릿지
```

스크립트가 쓰는 나머지(없으면 각 스크립트가 설치 안내를 찍는다):

| 용도 | 설치 |
|---|---|
| tmux 세션 | `sudo apt install -y tmux` (마우스는 아래 설정 필요) |
| colcon 빌드 | `sudo apt install -y python3-colcon-common-extensions` |
| 백엔드 venv | `sudo apt install -y python3-venv` |
| 프론트 빌드 | Node.js (`nvm` 권장, 또는 `sudo apt install -y nodejs npm`) |
| Pi 카메라(image-sender) | `picamera2` (라즈베리파이 OS 기본 포함, 없으면 `sudo apt install -y python3-picamera2`) |
| 추종 인지(ai-server) | `pip install -r aba_ai_service/follower_perception/requirements.txt` (torch·ultralytics, 용량 큼) |

> **tmux 마우스**: tmux 는 기본이 마우스 OFF 라 설치만 해서는 휠·클릭이 안 먹는다.
> `~/.tmux.conf` 에 아래를 넣어야 창 클릭/휠 스크롤이 된다(새 세션부터 적용, 이미 뜬 세션은
> `tmux source-file ~/.tmux.conf`). 마우스 ON 이면 텍스트 복사는 `Shift` 누른 채 드래그.
>
> ```bash
> set -g mouse on
> set -g history-limit 100000   # 휠로 되돌려 볼 로그 넉넉히
> ```

## 사전 점검 — 없으면 설치하거나, 안내한다

`_common.sh` 가 준비 상태를 확인한다. **가벼운 건 자동으로 준비**하고, **무거운/불확실한 건
정확한 해결 명령을 찍고 멈춘다**. 스크립트가 알 수 없는 에러로 죽지 않는다.

| 상황 | 동작 |
|---|---|
| colcon 미빌드(`install/` 없음) | `ensure_built` → 자동 `colcon build` (colcon/ROS 없으면 설치 안내) |
| 프론트 `node_modules` 없음 | `ensure_npm` → 자동 `npm install` (npm 없으면 설치 안내) |
| 백엔드 `.venv` 없음 | `ensure_venv` → 자동 생성 + `requirements.txt` 설치 |
| 도구 없음(tmux·colcon·npm·python3) | `need_cmd` → 설치 명령 안내 후 종료 |
| 파이썬 모듈 없음(rclpy 등) | `need_py_module` → 설치 안내 후 종료 |

서비스별 예외:
- **fms 백엔드**는 `backend/start.sh` 가 자체적으로 `.venv` 를 만들고 설치한다.
- **추종 서버**(`ai-server.sh`)의 `torch`/`ultralytics` 는 용량이 커서 자동 설치하지 않고,
  없으면 `pip install -r follower_perception/requirements.txt` 를 안내한다.

## all/ — 한 방 기동 (평소에는 이것만)

기동 3개 · 정리 3개, **짝으로만** 기억하면 된다.

| 기동 | 정리 | 어디서 | 하는 일 |
|---|---|---|
| `libi_server.sh` | `kill-libi_server.sh` | 노트북 | **로봇과 무관한 것만** — DB + 도메인 브릿지 + fleet_node + 관제 UI(:9001/:9002) + 도서관 웹(:8000/:3000). 로봇이 몇 대든 **한 번만**. <br>⚠️ [2026-08-05] kill 이 **:9001 FMS 백엔드까지 내린다**(기본). 로봇 텔레메트리·명령 링크도 같이 끊기니, 로봇을 돌려둔 채 UI 만 껐다 켜려면 `--keep-api`. |
| `libi_laptop.sh --robot <이름>` | `kill-libi_laptop.sh [이름]` | 노트북 | **로봇 한 대분** — AI 추종 서버 + 터치패널. 세션 `libi_laptop_<key>`. 로봇 대수만큼 부른다. 정리는 이름을 주면 그 로봇만, 안 주면 전부. |
| `libi_pi.sh --robot <이름>` | `kill-libi_pi.sh` | 로봇 | 주행 스택(`pi.sh`) + 카메라 송출 + `libi_perception`(추종/길잡이) 을 tmux 세션 `pinky_pi` 하나에. `--back <n>` 뒷캠, `--dyn-obstacle` 동적 장애물(기본 꺼짐). 모르는 플래그는 `pi.sh` 로 위임. |

```bash
[노트북]  ./scripts/all/libi_server.sh                                        # 먼저, 한 번만
          ./scripts/all/libi_laptop.sh --robot pinky-3 --domain-id 119
[로봇]    ./scripts/all/libi_pi.sh     --robot pinky-3 --domain-id 119 --back 4

# 로봇 2대 이상 — libi_server.sh 는 그대로 두고 로봇 것만 늘린다
[노트북]  ./scripts/all/libi_laptop.sh --robot pinky-1 --domain-id 117
          ./scripts/all/libi_laptop.sh --robot pinky-2 --domain-id 118
```

### 도메인은 표가 정한다 — `--domain-id` 는 덮어쓸 때만

```
pinky-1 → 117    pinky-2 → 118    pinky-3 → 119        (규칙: 116 + 번호)
```

IP 는 DHCP 라 자주 바뀌지만 **도메인은 배선처럼 고정**이라 코드에 박아 뒀다
(`_common.sh` 의 `robot_domain`). 두 런처가 로봇 이름에서 자동으로 고른다.

| 우선순위 | |
|---|---|
| ① `--domain-id` | 명시하면 그 값. DB 와 다르면 경고(브릿지는 DB 값으로 열린다) |
| ② 표 | 실물 `pinky-<N>` 만. `pinky-sim-2` 같은 이름은 표 밖이다(sim 은 90/91/92) |
| ③ DB `rc_robots.domain_id` | 표 밖 이름일 때. 노트북 런처만 — Pi 는 DB 를 못 볼 수 있다 |
| 그래도 없으면 | 죽는다 |

⚠️ 예전에 없앤 "기본값"과는 다르다. 그건 `.env` 의 `ROS_DOMAIN_ID`(=119) **하나를 모든
로봇에** 쓰는 것이라 2대째부터 반드시 틀렸다. 이 표는 로봇마다 다른 값이라 그 실패가 없다.
로봇 Pi 의 셸 `ROS_DOMAIN_ID` 와 DB 값도 같아야 한다 — 셋 중 하나만 어긋나면 조용히 안 붙는다.

⚠️ 순서가 있다: `libi_laptop.sh` 의 터치패널은 :9001 / :8000 이 안 열려 있으면 그 자리에서
죽는다(`ui/libi_gui.sh` 의 check_reachable). 그 둘을 띄우는 건 `libi_server.sh` 다.

로봇 이름은 관제 DB(`rc_robots.name`)에 등록된 값과 **정확히 같아야** 한다 — 다르면
`fleet_node` 가 못 알아보고 배차해도 안 움직인다.

### AI 수신 포트는 로봇 번호에서 나온다

`perception_server` 의 수신 포트는 노트북에 한 벌뿐이라, 2대를 동시에 추종하면 두 번째가
bind 에서 죽는다. `robot_ports`(`_common.sh`)가 로봇마다 10 씩 띄우고, **로봇 쪽
`libi_pi.sh` 도 같은 함수로 계산**하므로 서로 값을 넘길 필요가 없다.

| 로봇 | 영상 UDP | 뷰어 TCP |
|---|---|---|
| pinky-1 | 6001 | 5007 |
| pinky-2 | 6011 | 5017 |
| pinky-3 | 6021 | 5027 |

⚠️ 로봇이 한 대뿐이던 시절 pinky-3 은 6001/5007 이었다. 스크립트끼리는 어긋나지 않지만
**손으로 `nc`/브라우저를 열 때는 바뀐 포트**를 봐야 한다. `VIDEO_PORT=... VIEWER_PORT=...`
로 덮어쓰면 그 값이 이긴다.

## ⚠️ UDP:6002 추종 주행 경로는 은퇴했다

추종 제어가 **AI 서버 → 로봇 `libi_perception`** 으로 옮겨가면서,
`AI서버 ──UDP:6002──▶ cmd_bridge ──▶ /cmd_vel` 경로는 쓰지 않는다.

**은퇴한 것을 띄우면 로봇이 끊긴다.** `cmd_bridge.py` 는 워치독이라 명령이 없어도
**20Hz 로 정지(0,0)를 계속 발행**한다. `/cmd_vel` 에는 중재자(twist_mux)가 없어
**마지막에 도착한 메시지가 이긴다** — nav2 가 10Hz 로 주행 명령을 내도 그 사이에 0 이
두 번 덮어써서, 모터가 0.05초 굴렀다 서기를 반복한다.

```bash
# 증상 확인
ros2 topic info /cmd_vel -v | grep -i "publisher count"   # 2 이상이면 경합
ros2 topic hz /cmd_vel                                     # ~30Hz (정상은 nav2 단독 10Hz)
ros2 topic echo /cmd_vel                                   # 0,0 사이에 명령이 띄엄띄엄

# 정리
pkill -f cmd_bridge.py        # 또는 ./scripts/all/kill-libi_pi.sh
```

해당 스크립트: `drive-pi/follow-drive.sh`(로봇) · `laptop/ai_follower_service.sh`(노트북,
`--drive-host` 를 붙여 UDP:6002 로 쏜다). **둘 다 띄우지 않는다.**

## drive-pi/ — 로봇 주행 보드에서 실행

> 평소에는 `all/libi_pi.sh` 가 이걸 다 부른다. 아래는 하나씩 떼어 볼 때만.

| 스크립트 | 하는 일 |
|---|---|
| `pi.sh <pinky>` | 주행 스택 전체(hw·nav2·fleet-link·fsm·led) tmux 기동. 안 빌드면 colcon build. `--no-fsm`/`--no-led` 위임. |
| `image-sender.sh [AI_IP]` | 카메라 → UDP 로 AI 서버 전송(추종 영상). IP 안 주면 `LAPTOP_IP`. |
| ~~`follow-drive.sh`~~ | **은퇴.** UDP:6002 → `/cmd_vel`(cmd_bridge). 띄우면 nav2 와 `/cmd_vel` 을 다퉈 주행이 끊긴다 — 위 경고 참고. |
| `kill.sh` | 주행 스택(tmux `pinky_pi`) + 고아 노드 정리 (기존 ros_ws/scripts/kill.sh 위임). `cmd_bridge.py` 도 이름으로 쓸어담는다. |

```bash
./drive-pi/pi.sh pinky3            # 주행 스택
./drive-pi/image-sender.sh         # 카메라 전송 (별 터미널)
```

⚠️ `image-sender.sh` 는 `aba_ai_service` 서브트리가 로봇 체크아웃에 있어야 한다
(주행만 할 거면 `pi.sh` 만으로 충분). 없으면 스크립트가 이유를 알려준다.

## handy-pi/ — 로봇 팔 보드에서 실행

| 스크립트 | 하는 일 |
|---|---|
| `handy.sh` | `libi_handy_controller` 노드 기동(handy_cmd 구독 / handy_result 발행). 안 빌드면 colcon build. |

```bash
./handy-pi/handy.sh                # 팔 보드. Drive 보드와 같은 ROS_DOMAIN_ID
```

⚠️ 팔 모션은 현재 스텁 — 팔 담당자가 채운다(옵시디언 인터페이스 요청서). Drive 보드와
**같은 `ROS_DOMAIN_ID`** 여야 통신된다(한 로봇, 두 보드).

## laptop/ — 노트북/서버에서 실행

| 스크립트 | 하는 일 |
|---|---|
| `fms_service.sh` | 도메인 브릿지 + fleet_node + 상태 어댑터(tmux 세션 `libi_fms`). 관제 백엔드/프론트는 안 띄움 — `ui/fms.sh` 로 따로. |
| ~~`ai_follower_service.sh <pinky>`~~ | **은퇴한 경로.** `--drive-host` 를 붙여 UDP:6002 로 주행 명령을 쏜다 — 로봇 `cmd_bridge` 가 받아 `/cmd_vel` 을 다툰다. 인지 서버는 `all/libi_laptop.sh` 가 `--drive-host` 없이 띄운다. |
| `sim.sh [viewer\|--no-fsm\|--no-rviz]` | Gazebo 시뮬 전체(로봇 없이 검증). `./sim.sh viewer` = GUI 포함. |
| `kill.sh` | `libi_fms` 세션 + sim·브릿지·ROS 고아 정리. 관제 백엔드/프론트는 안 건드림 — `backend/stop.sh` 로 따로. |

```bash
./fms_service.sh               # 브릿지 + fleet_node + 어댑터 (관제 백엔드/프론트 제외)
# 인지 서버는 ./scripts/all/libi_laptop.sh 로 띄운다 (--drive-host 없이)
```

## ui/ — 웹 UI (노트북/서버에서 실행)

백엔드+프론트엔드를 tmux 로 함께 띄운다. 실행법은 각 서비스 문서 방식 그대로
(fms=`backend/start.sh`+`npm run dev`, 도서관=`backend/run.sh`+`npm run dev`).
기동하면 `urls` 창에 접속 주소(Local/Network)를 찍어준다.

| 스크립트 | 하는 일 |
|---|---|
| `fms.sh` | 관제(FMS) 백엔드(:9001) + 프론트(:9002). 콘솔 `http://<ip>:9002/` (로그인 후 `/admin/…`). |
| `library.sh` | 도서관 웹 백엔드(:8000) + 프론트(:3000). **회원 `/`, 사서 `/admin`**. |
| `libi_gui.sh <robot> --domain-id <n>` | libi_gui 를 그 로봇 것으로. `PERCEPTION_URL`/`FMS_URL` 을 `LAPTOP_IP` 로 채움. |
| `kill.sh [fms\|library\|gui]` | 인자 없으면 셋 다(관제·도서관·터치패널), 주면 그것만. (FMS 백엔드 데몬은 공유라 안 건드림) |

```bash
./fms.sh                  # 관제 UI
./library.sh              # 도서관 웹 (회원/사서)
./libi_gui.sh pinky3      # 터치패널 (테스트)
./kill.sh                 # 전부 정리 (터치패널 포함)
./kill.sh library         # 도서관만
./kill.sh gui             # 터치패널만
```

관제 백엔드/프론트(:9001/:9002)는 `laptop/fms_service.sh` 가 아니라 이 `fms.sh` 가 띄운다 —
`laptop/fms_service.sh` 는 브릿지·fleet_node·어댑터만 담당한다. 백엔드 데몬 중지는
`aba_fms_service/backend/stop.sh`.

## 추종 전체를 굴리는 순서

```
[노트북]  ./scripts/all/libi_server.sh                                   # 브릿지 + fleet_node + UI (한 번만)
          ./scripts/all/libi_laptop.sh --robot pinky-3 --domain-id 119   # 인지 서버 + 패널
[로봇]    ./scripts/all/libi_pi.sh     --robot pinky-3 --domain-id 119   # 주행 + 카메라 + libi_perception
[패널]    관리자 로그인 → 「관리자 추종」 → 「등록」   (패널은 libi_laptop.sh 가 띄운다)
```

**주행 스택을 내릴 필요가 없다.** 예전에는 추종이 `/cmd_vel` 을 직접 밀어서 nav2 와
다퉜지만, 지금은 추종 제어가 로봇 `libi_perception` 안에 있고 **미션 BT 가 세션을 열고
닫는다** — 추종 상태가 아니면 속도를 아예 발행하지 않는다. `libi_pi.sh` 가 둘을 한 세션에
같이 띄우는 것이 정상 구성이다.
