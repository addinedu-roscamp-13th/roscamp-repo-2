# scripts/ — 최상위 런처

서비스별로 흩어진 실행 명령을, **어디서 실행하는지(pi/laptop)** 로 나눠 한곳에 모았다.
각 스크립트는 얇은 위임 래퍼다 — 실제 로직은 기존 서비스 스크립트에 그대로 있고, 여기서는
**IP 를 `.env` 에서 채우고, 로봇 이름을 인자로 받고, 실행 위치를 레포 루트로 고정**한다.

## 왜 이 폴더인가

- **실행 위치 무관.** 모든 스크립트가 자기 위치를 역산해 `REPO_ROOT` 를 잡고 `cd` 한다
  (`_common.sh`). 아무 데서 실행해도 되고, "다른 폴더에서 돌리면 경로 에러" 가 사라진다.
- **로봇 지정은 인자로.** `FSM_ROBOT_ID=pinky3 ...` 대신 `./pi.sh pinky3`.
- **IP 는 `.env` 한곳.** `LAPTOP_IP`, `PINKY{1,2,3}_IP` (루트 `.env`, `.env.example` 참고).

폴더는 **실행 위치**로 나뉜다: `drive-pi/`(로봇 주행 보드) · `handy-pi/`(로봇 팔 보드) ·
`laptop/`(관제 서버) · `ui/`(웹 UI). (로봇은 한 대지만 보드가 둘 — 같은 `ROS_DOMAIN_ID`)

## `.env` 준비

```bash
cp .env.example .env    # 레포 루트에서
# LAPTOP_IP / PINKY{N}_IP 를 실제 IP 로 채운다
```

> CycloneDDS 피어는 여기서 자동 구성하지 않는다 — 각 머신 `~/.bashrc` 에서 직접 관리한다
> (아래 "CycloneDDS 피어" 참고).

## CycloneDDS 피어 (`~/.bashrc`)

공유기가 멀티캐스트를 막아 DDS 디스커버리가 자동으로 안 되므로, **정적 피어**로 서로를
찾게 한다. **각 머신**(laptop·로봇들)의 `~/.bashrc` 에 아래를 넣는다:

```bash
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
  export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
  export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface autodetermine="true"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><Peers><Peer address="localhost"/><Peer address="172.30.1.10"/><Peer address="172.30.1.83"/></Peers></Discovery></Domain></CycloneDDS>'
fi
```

핵심 4가지:

1. **피어 = `localhost` + 통신할 모든 머신 IP.** 위 예시는 laptop(`172.30.1.10`) + pinky3
   (`172.30.1.83`). 로봇이 늘면 `<Peer address="새IP"/>` 를 한 줄씩 더 넣는다.
2. **모든 머신이 같은 목록**이면 된다 — 자기 IP 가 목록에 있어도 무해하니 머신마다 다르게
   만들 필요가 없다. (pi 냐 laptop 이냐 구분 안 함)
3. **`localhost` 는 빼지 말 것** — 같은 머신 안의 프로세스(nav2·fleet_link·bringup)끼리
   디스커버리하는 데 필요하다.
4. **모든 머신이 같은 RMW(cyclonedds)** 여야 서로 보인다. 하나라도 FastDDS 면 같은
   도메인이어도 안 잡힌다.

새 머신을 추가하면 → **모든 머신의 목록에** 그 IP 를 넣고 각자 `source ~/.bashrc`(또는 새 셸).
피어는 "찾아갈 주소"일 뿐, 실제로 통신할지는 `ROS_DOMAIN_ID`(같은 도메인끼리만 매칭)가 정한다.

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

## drive-pi/ — 로봇 주행 보드에서 실행

| 스크립트 | 하는 일 |
|---|---|
| `pi.sh <pinky>` | 주행 스택 전체(hw·nav2·fleet-link·fsm·led) tmux 기동. 안 빌드면 colcon build. `--no-fsm`/`--no-led` 위임. |
| `image-sender.sh [AI_IP]` | 카메라 → UDP 로 AI 서버 전송(추종 영상). IP 안 주면 `LAPTOP_IP`. |
| `follow-drive.sh` | AI 서버의 명령을 받아 `/cmd_vel` 발행(cmd_bridge). 기본 `--port 6002 --flip-180`, `--no-flip` 로 뒤집기 해제. |
| `kill.sh` | 주행 스택(tmux `pinky_pi`) + 고아 노드 정리 (기존 ros_ws/scripts/kill.sh 위임) |

```bash
./drive-pi/pi.sh pinky3            # 주행 스택
./drive-pi/image-sender.sh         # 카메라 전송 (별 터미널)
./drive-pi/follow-drive.sh         # 추종 주행 브릿지 (별 터미널)
```

⚠️ `image-sender.sh` / `follow-drive.sh` 는 `aba_ai_service` 서브트리가 로봇 체크아웃에
있어야 한다(주행만 할 거면 `pi.sh` 만으로 충분). 없으면 스크립트가 이유를 알려준다.

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
| `ai_follower_service.sh <pinky>` | 추종 인지 서버 기동 + `--drive-host` 를 그 로봇 IP 로. `--test-pattern` 등 위임. |
| `sim.sh [viewer\|--no-fsm\|--no-rviz]` | Gazebo 시뮬 전체(로봇 없이 검증). `./sim.sh viewer` = GUI 포함. |
| `kill.sh` | `libi_fms` 세션 + sim·브릿지·ROS 고아 정리. 관제 백엔드/프론트는 안 건드림 — `backend/stop.sh` 로 따로. |

```bash
./fms_service.sh               # 브릿지 + fleet_node + 어댑터 (관제 백엔드/프론트 제외)
./ai_follower_service.sh pinky3   # 추종 인지 서버 (cmd_vel -> pinky3)
```

## ui/ — 웹 UI (노트북/서버에서 실행)

백엔드+프론트엔드를 tmux 로 함께 띄운다. 실행법은 각 서비스 문서 방식 그대로
(fms=`backend/start.sh`+`npm run dev`, 도서관=`backend/run.sh`+`npm run dev`).
기동하면 `urls` 창에 접속 주소(Local/Network)를 찍어준다.

| 스크립트 | 하는 일 |
|---|---|
| `fms.sh` | 관제(FMS) 백엔드(:9001) + 프론트(:9002). 콘솔 `http://<ip>:9002/` (로그인 후 `/admin/…`). |
| `library.sh` | 도서관 웹 백엔드(:8000) + 프론트(:3000). **회원 `/`, 사서 `/admin`**. |
| `libi_gui.sh <pinky>` | libi_gui 를 그 로봇 것으로. `PERCEPTION_URL`/`FMS_URL` 을 `LAPTOP_IP` 로 채움. |
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
[노트북]  ./laptop/fms_service.sh
[노트북]  ./laptop/ai_follower_service.sh pinky3
[로봇]    ./drive-pi/image-sender.sh    # 영상
[로봇]    ./drive-pi/follow-drive.sh    # 주행 브릿지
[패널]    ./ui/libi_gui.sh pinky3   → 관리자 로그인 → 「관리자 추종」 → 「등록」
```

`pi.sh`(주행) 와 추종용 `image-sender.sh`/`follow-drive.sh` 는 같은 bringup 을 놓고 다투므로
동시 실행하지 않는다 — 추종 테스트 중에는 nav2/FSM 주행 스택을 내려둔다.
