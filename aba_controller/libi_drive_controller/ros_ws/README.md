# controller/drive/ros_ws build record

## 2026-06-30

Executed from `/home/robotPrj_Boilerplate/controller/drive/ros_ws`.

```bash
cd /home/robotPrj_Boilerplate/controller/drive/ros_ws
source /opt/ros/jazzy/setup.bash
colcon build
```

Build result:

```text
Summary: 11 packages finished [25.7s]
```

Package build notes:

- `pinky_bringup` finished
- `pinky_description` finished
- `pinky_emotion` finished
- `pinky_gz_sim` finished with warning: no `install` target
- `pinky_imu_bno055` finished
- `pinky_interfaces` finished
- `pinky_led` finished
- `pinky_navigation` finished
- `pinky_sensor_adc` finished
- `pinky_waypoint` finished
- `sllidar_ros2` finished

After the build, the workspace setup file was loaded:

```bash
source install/setup.bash
```

Environment check:

```text
AMENT_PREFIX_PATH includes /home/robotPrj_Boilerplate/controller/drive/ros_ws/install packages and /opt/ros/jazzy.
ROS_PACKAGE_PATH is empty.
```

## Gazebo 시뮬레이션 실행 (`scripts/sim.sh`)

`arte2` 맵 기준 Gazebo + nav2 + rviz + FMS 연동까지 tmux 창 5개로 한 번에 띄운다.

### 사전 준비

```bash
colcon build   # install/ 이 없거나 오래됐으면 먼저
sudo apt install ros-jazzy-domain-bridge   # FMS 연동(도메인 브릿지)에 필요, 없어도 gazebo/nav2/rviz 3개는 정상 동작
```

FMS 관제 화면에서 sim을 로봇으로 선택하려면, `rc_robots` 테이블에 `ip_address = 127.0.0.1`, `robot_type = pinky`인 로봇이 하나 등록돼 있어야 한다 (`aba_fms_service/backend/app/fleet_telemetry.py`의 `FLEET_ROBOTS["127.0.0.1"]` 항목과 매칭). 없으면 관리자 화면에서 하나 추가한다.

### 실행

```bash
cd scripts
./sim.sh          # 헤드리스(가제보 GUI 없이)
./sim.sh viewer   # 가제보 GUI 포함
```

tmux 창 5개가 뜬다 (`Ctrl+b 0~4` 또는 `Ctrl+b n`/`p`로 전환):

| # | 창 이름 | 역할 |
|---|---|---|
| 0 | `gazebo` | Gazebo 시뮬레이션 (arte2 월드) |
| 1 | `nav2` | `/scan` 감지 후 nav2 bringup (`gz_bringup_launch.xml`, `nav2_params_sim.yaml`) |
| 2 | `rviz` | RViz2 뷰어 |
| 3 | `bridge` | `domain_bridge` — sim 도메인 ↔ FMS 서버 도메인(86) 중계. `ros-jazzy-domain-bridge` 미설치 시 이 창만 에러, 나머지는 정상 |
| 4 | `fleet-link` | `robot_agent/scripts/run_fleet_link.py` — robot_agent(FastAPI) 없이 fleet_link만 단독 실행. FMS의 "이동" 명령이 sim까지 오려면 필수 |

**도메인 ID는 하드코딩이 아니라 현재 셸의 `ROS_DOMAIN_ID`를 그대로 쓴다** (`export ROS_DOMAIN_ID=90 && ./sim.sh`처럼 미리 지정하면 그 값 사용, 안 정해뒀으면 90 기본값). `bridge`/`fleet-link` 창도 같은 도메인으로 자동 맞춰진다.

### 종료

```bash
./kill.sh
```

tmux 세션과 gazebo/nav2/rviz/domain_bridge/fleet_link 관련 프로세스를 전부 정리한다. **테스트를 새로 할 때마다 `kill.sh`로 완전히 내린 뒤 `sim.sh`로 다시 띄우는 걸 권장** — 이전 세션의 상태(AMCL 추정치, 잔여 프로세스 등)가 다음 테스트에 영향을 주지 않게 하기 위함이다.

## 실물 로봇 실행 (`scripts/laptop.sh`)

pm2(`ecosystem.config.js`) 없이 로컬에서 직접 hw + nav2(arte2 맵) + fleet_link를 tmux 창 3개로 띄운다.

```bash
cd scripts
./laptop.sh
./kill.sh   # 종료 — sim.sh와 동일 스크립트 공유
```

| # | 창 이름 | 역할 |
|---|---|---|
| 0 | `hw` | `pinky_bringup bringup_robot.launch.xml` — 라이다/모터/IMU |
| 1 | `nav2` | `/scan` 감지 후 `pinky_navigation bringup_launch.xml map:=arte2.yaml` |
| 2 | `fleet-link` | `robot_agent/scripts/run_fleet_link.py` — robot_agent(FastAPI) 없이 fleet_link만 단독 실행 |

FMS에서 이 로봇을 선택하려면 `rc_robots`에 실제 IP로 등록돼 있어야 하고(`fleet_telemetry.py`가 DB에서 매 기동 시 읽어옴, 하드코딩 아님), `domain_bridge_pinky{N}.yaml`이 이 로봇의 실제 도메인ID로 중앙 PC에서 돌고 있어야 한다.

## CycloneDDS 전환 (Pi CPU 절감)

기본 RMW(FastDDS)의 SHM 전송 스레드(`dds.shm`)가 Pi에서 CPU를 많이 먹는다. CycloneDDS로 바꾸면 `fleet-link` 실측 기준 CPU가 크게 줄어든다(~55%→~28%, 2026-07-20 실측).

멀티캐스트가 막힌 네트워크라 CycloneDDS도 FastDDS의 `ROS_STATIC_PEERS`처럼 상대방 IP를 직접 알려줘야 한다. 별도 XML 파일 없이 `CYCLONEDDS_URI`에 설정을 문자열로 통째로 넣어서 `~/.bashrc` 한 곳에서 끝낸다 — **서로 상대방 IP만 자기 쪽에 적으면 된다:**

**로봇(laptop.sh를 실행하는 쪽) `~/.bashrc`** — 중앙 PC(FMS 서버) IP를 피어로:
```bash
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface autodetermine="true"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><Peers><Peer address="localhost"/><Peer address="192.168.0.19"/></Peers></Discovery></Domain></CycloneDDS>'
fi
```

**중앙 PC(FMS 서버, 192.168.0.19) `~/.bashrc`** — 로봇 IP를 피어로 (로봇이 여러 대면 `<Peer .../>` 추가):
```bash
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface autodetermine="true"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><Peers><Peer address="localhost"/><Peer address="172.30.1.83"/></Peers></Discovery></Domain></CycloneDDS>'
fi
```

⚠️ **한쪽만 바꾸면 서로 안 보인다** — DDS는 같은 벤더(RMW 구현체)끼리만 자동 디스커버리된다. 로봇을 CycloneDDS로 바꿨는데 중앙 PC가 FastDDS 그대로면, `domain_bridge`가 그 로봇의 `/fleet_cmd` 등을 못 찾는다.

⚠️ 환경변수를 바꿔도 **이미 떠있는 프로세스는 반영이 안 된다** — `.bashrc` 수정 후 새 셸을 열거나 `source ~/.bashrc` 하고, 관련 프로세스(FMS 백엔드, `domain_bridge_pinky{N}`, `laptop.sh`/`sim.sh`)를 재시작해야 한다.

⚠️ 로봇 IP가 바뀌면(DB `rc_robots.ip_address`) 중앙 PC `.bashrc`의 `<Peer address="...">`도 같이 갱신해야 한다 — 자동 동기화 안 됨.
