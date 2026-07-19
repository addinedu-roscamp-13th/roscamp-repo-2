# ⚠️ 필독 — RMW는 CycloneDDS로 통일한다

> **이 도메인의 모든 머신은 반드시 CycloneDDS(`rmw_cyclonedds_cpp`)를 써야 한다.**
> 하나라도 FastDDS로 남으면 그 머신과는 통신이 끊기거나 불안정해진다. **전부-아니면-전무.**

---

## 왜 (배경)

Raspberry Pi에서 기본 RMW인 **FastDDS의 공유메모리 전송 스레드(`dds.shm`)가 CPU를 크게
잡아먹는다** — nav2·fleet_link·bringup 같은 **같은 Pi 안 노드끼리**의 통신
(costmap·/tf·/scan) 오버헤드가 대부분이다. CycloneDDS는 ARM에서 idle CPU가 훨씬 낮다.
그래서 RMW를 CycloneDDS로 교체한다.

## 대상 머신 (전부 해야 함)

| 머신 | ROS 노드 | 조치 |
|---|---|---|
| **로봇 Pi** (대수만큼) | hw(bringup·lidar), nav2, fleet_link | 설치 + 설정 ✅(이 repo가 처리) |
| **중앙 PC `192.168.0.19`** (FMS 서버 + 조작 통합) | `bridge_pinky*`(fleet_cmd 중계), rviz, teleop_twist_keyboard, nav2_web_bridge_tf 등 | **설치 + 설정 필요** |

> FMS 서버와 조작(rviz/teleop)은 보통 **같은 중앙 PC 한 대**다.
> `fleet_cmd`(명령)가 이 PC에서 오므로, **여기가 FastDDS로 남으면 로봇이 명령을 못 받는다.** 반드시 같이 바꿀 것.

---

## 1) 설치 (모든 머신에서)

```bash
sudo apt update && sudo apt install -y ros-jazzy-rmw-cyclonedds-cpp
```

설치되면 이 repo의 스크립트들이 **자동으로** CycloneDDS를 쓴다(아래 가드 참고).
설치 안 된 머신은 자동으로 FastDDS로 폴백하므로, 설치 전까지는 기존과 동일하게 동작한다.

## 2) 이 repo에서 설정된 위치 (이미 배선됨)

`if [ -f .../librmw_cyclonedds_cpp.so ]` 가드로, **설치돼 있을 때만** 켜진다:

| 파일 | 커버 대상 |
|---|---|
| `ros_source.sh` | **프로덕션(pm2): robot-hw / nav2 / robot_agent** — 가장 중요 |
| `scripts/laptop.sh` | 실물 로컬 테스트 (hw/nav2/fleet_link tmux 3창) |
| `scripts/sim.sh` | 시뮬레이션 |
| `~/.bashrc` | 대화형 터미널·수동 `ros2` CLI·`kill.sh`의 daemon 정리 |

각 위치는 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` 와 `CYCLONEDDS_URI`(아래)를 export 한다.

## 3) ⚠️ 정적 피어 (멀티캐스트 차단 대응) — 놓치기 쉬움

이 네트워크는 공유기가 **멀티캐스트를 막아서**, 서버와의 디스커버리를 유니캐스트
정적 피어로 해왔다(`ROS_STATIC_PEERS=192.168.0.19`, FastDDS 용).
**CycloneDDS는 `ROS_STATIC_PEERS`를 (버전에 따라) 무시**하므로, 대신
**`cyclonedds.xml`**(같은 폴더)로 피어를 지정한다. 위 스크립트들이 이 파일을
`CYCLONEDDS_URI`로 물려준다.

> **머신/로봇을 추가하면 그 IP를 `cyclonedds.xml`의 `<Peers>`에 넣어야** 서로 보인다.
> `localhost` 피어는 같은 Pi 안 프로세스끼리 디스커버리에 필수 — 지우지 말 것.

---

## 다른 머신에 적용하기 (복붙용) — 중앙 PC(FMS+조작)

이 repo가 없는 머신(중앙 PC `192.168.0.19` = FMS 서버 + 조작 통합)은 아래 3단계만 하면 된다.

### ① 설치
```bash
sudo apt update && sudo apt install -y ros-jazzy-rmw-cyclonedds-cpp
```

### ② `~/cyclonedds.xml` 생성 (표준 위치, 하드코딩 없음)
아래를 `~/cyclonedds.xml`로 저장. `<Peers>`의 IP는 **전부 예시**다 — **각 머신의 실제 IP로 교체**할 것.
`<Peers>`에는 이 머신이 통신할 **다른 모든 머신 IP**를 넣는다(자기 자신은 빼도 됨, `localhost`는 필수).

> ⚠️ **IP는 DHCP로 바뀔 수 있다.** 각 머신 IP는 `hostname -I` 로 확인하고, 가능하면
> 공유기에서 **고정 IP(DHCP 예약)**로 잡아둘 것. IP가 바뀌면 이 `<Peers>`도 갱신해야 통신된다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config">
  <Domain id="any">
    <General>
      <Interfaces><NetworkInterface autodetermine="true"/></Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <Peers>
        <Peer address="localhost"/>          <!-- 같은 호스트 내부 프로세스 (필수, 고정) -->
        <Peer address="192.168.0.10"/>       <!-- 예시: 로봇(Pi) IP → 실제 값으로 교체 -->
        <Peer address="192.168.0.19"/>       <!-- 예시: FMS 서버 IP → 실제 값으로 교체 -->
        <!-- <Peer address="192.168.0.XX"/>   다른 로봇/PC 등 필요한 만큼 추가 -->
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
```

### ③ `~/.bashrc` 맨 아래에 이 블록 붙여넣기 (모든 머신 동일 — 하드코딩 없음)
```bash
# ── RMW: CycloneDDS 통일 (설치 시 자동, 없으면 FastDDS 폴백) ──
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    [ -f "$HOME/cyclonedds.xml" ] && export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
fi
# 도메인은 로봇과 반드시 일치시킬 것 (예: export ROS_DOMAIN_ID=119)
```
그리고 **새 터미널을 열거나** `source ~/.bashrc` 후, 그 머신의 ROS 노드(서버 브리지·rviz 등)를 **재기동**한다.

> 💡 `$HOME/cyclonedds.xml` + 위 `.bashrc` 블록은 **모든 머신에서 글자 그대로 동일**하다
> (경로 하드코딩 없음). 머신마다 다른 건 `cyclonedds.xml`의 `<Peers>` 목록뿐.

---

## 여러 대(멀티 로봇)일 때

이 시스템은 **도메인으로 로봇을 격리**하고 **`domain_bridge`로 FMS 서버에 연결**한다.

```
 로봇1(pinky1, 도메인 A)  ─┐
 로봇2(pinky2, 도메인 B)  ─┤   각 로봇 도메인 ↔ 서버 도메인(86)
 로봇3(pinky3, 도메인 119)─┘   을 domain_bridge 가 중계
                                      │
                              FMS 서버(192.168.0.19, 도메인 86)
                                - 로봇 수만큼 domain_bridge 인스턴스
                                  (bridge_pinky3_119 처럼)
```

### 핵심 원칙 2가지
1. **격리는 도메인이 한다** — 로봇마다 `ROS_DOMAIN_ID`가 다르다(각 로봇 `~/.bashrc`가 단일 출처).
   도메인이 다르면 피어로 연결돼 있어도 서로의 ROS 그래프가 안 보인다(그래서 로봇끼리 안 섞임).
2. **연결(디스커버리)은 피어가 한다** — 멀티캐스트가 막혀서, 통신할 상대 IP를 `<Peers>`에 넣어야 붙는다.

### 역할별 `cyclonedds.xml` `<Peers>` 설정
| 머신 | ROS_DOMAIN_ID | Peers 에 넣을 것 |
|---|---|---|
| **각 로봇(Pi)** | 자기 도메인(예: 119) | `localhost` + **중앙 PC IP** (로봇끼리는 불필요) |
| **중앙 PC** (FMS+조작) | 프로세스별로 다름 — FMS백엔드=86, rviz/teleop=그 로봇 도메인, domain_bridge=양쪽 | `localhost` + **모든 로봇 IP** |

> 중앙 PC 한 대가 **여러 도메인의 노드를 동시에** 돌린다(FMS는 86, rviz·teleop은 로봇 도메인).
> 그래도 `cyclonedds.xml`은 **하나(모든 로봇 IP + localhost)** 면 충분하다 — 격리는 도메인이 하고,
> 피어는 프로세스가 어느 도메인이든 공통으로 쓰인다.

> 💡 `<Peers>`에 **여분의 IP가 있어도 무해**하다(다른 도메인이면 매칭이 안 될 뿐).
> 그래서 **모든 IP를 넣은 하나의 `cyclonedds.xml`을 전 머신에 공용**으로 써도 된다 —
> 격리는 어차피 `ROS_DOMAIN_ID`가 하니까. 단 **도메인 값만은 머신마다 정확히** 맞출 것.

> ⚠️ 로봇 추가 시: ① 새 로봇에 고유 도메인 지정(`~/.bashrc`) → ② 새 로봇 `cyclonedds.xml`에
> 서버 IP peer → ③ **서버**의 `cyclonedds.xml`에 새 로봇 IP peer 추가 + 새 로봇용 domain_bridge 기동.

## 4) 적용 후 검증 (반드시 확인)

```bash
# (a) 노드가 실제로 CycloneDDS를 쓰는지
ros2 doctor --report | grep -i middleware      # rmw_cyclonedds_cpp 여야 함
# 또는 실행 프로세스 환경 직접 확인
tr '\0' '\n' < /proc/$(pgrep -f component_container_isolated|head -1)/environ | grep RMW

# (b) 서버(192.168.0.19)와 디스커버리 되는지 — 제일 중요
ros2 topic echo /fleet_cmd_result             # FMS 명령/응답 오가나
ros2 node list | grep bridge_pinky3           # 서버 브리지 노드가 보이나

# (c) CPU 확인 — dds.shm 스레드가 사라지고 nav2 container CPU가 내렸나
top -H -p $(pgrep -f component_container_isolated|head -1)
```

**(b)가 안 되면** 중앙 PC(또는 다른 로봇) 중 아직 FastDDS인 머신이 있거나, 피어 설정이 빠진 것.
그 머신도 위 1~3을 적용할 것.

## 5) 되돌리기 (문제 시)

모든 머신에서 CycloneDDS 설치를 제거하거나, 위 스크립트의 export를 주석 처리하면
가드가 꺼져 자동으로 FastDDS로 복귀한다. **되돌릴 때도 전부-아니면-전무** — 한쪽만
바꾸면 통신이 깨진다.

---
_최종 수정: 2026-07-20 — Pi FastDDS SHM CPU 부하 대응_
