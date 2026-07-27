# aba_ai_service

비전 AI 서버. **서버가 두 개 들어 있고 서로 무관하다** — 헷갈리기 쉬운 지점이라 먼저 정리한다.

| 스크립트 | 실행되는 것 | 경로 | 쓰임 |
|---|---|---|---|
| `scripts/ai-server.sh` | `follower_perception/scripts/perception_server.py` | UDP:6001 → **TCP:5007** | **관리자 추종.** YOLO+ReID 로 사람을 추적하고 영상을 libi_gui 추종 화면에 보낸다. |
| `scripts/relay-stub.sh` | `main.py` | UDP:9000 → TCP:9010 / 6000 | 관제용 릴레이 stub. 아직 더미 추론(`owner` 항상 None)이라 추종은 못 한다. |

### 디렉터리

```
follower_perception/   인지 파이프라인 (YOLO 검출 · ReID · 추적) + perception_server
follower_BT/           추종 주행 정책 (IDLE/FOLLOWING/SEARCHING 상태기, DrivePolicy)
main.py                릴레이 stub
```

`follower_BT` 는 `follower_perception` 의 **형제 디렉터리여야 한다** —
`perception_server.py` 가 `<부모>/follower_BT` 를 `sys.path` 에 넣어 import 한다.
둘 다 `arte_libi_perception` 레포에서 복사해온 것이라 옮길 때 같이 움직여야 한다.

추종을 돌리려면 **`ai-server.sh`** 다. `relay-stub.sh` 쪽은 "추종 구현이 두 갈래"였던 시절의
내려놓은 경로이며, 코드는 남겨두되 진입점 이름을 갈라 두었다
(`docs/superpowers/specs/2026-07-20-admin-follow-control-path-design.md` 참고).

## 관리자 추종 실행

```
Pi 카메라(앞/뒤 한 프로세스) ──UDP:6001──▶ perception_server
                                            best.pt + ReID        (검출·추적)
                                            yolo11n-pose crop     (자세 판정)
                                            ├─TCP:5007──▶ libi_gui 화면 (추종·길잡이 등록)
                                            └─TCP:6000──▶ 로봇 libi_perception  ← --robot-host
                                              └ ControlLoop(PID+LiDAR) → /cmd_vel
```

### [2026-07-27] 바뀐 것

**로봇 검출 채널이 실물로 연결됐다.** `--robot-host <로봇IP>` 를 주면 주인 검출을
로봇의 `libi_perception` 으로 직접 보낸다. 이 채널이 없으면 로봇의 회복 BT 는 더미
스텁(`main.py`)만 받아 **진짜 검출을 한 번도 못 본다.**

**주행 명령 경로(`--drive-host` / UDP:6002)는 은퇴했다.** 추종 제어가 로봇 쪽으로
옮겨갔다. `pi-all.sh` 의 `follow-drive` 창도 없앴다 — 남겨 두면 그 브리지가 명령이
없을 때도 20Hz 로 정지 명령을 쏴서 새 PID 와 `/cmd_vel` 을 다툰다(중재자가 없어
마지막에 도착한 메시지가 이긴다).

**카메라 송출이 한 프로세스로 합쳐졌다.** 앞뒤를 둘 다 열어두고 `/libi/camera_select`
(BT 가 발행)에 따라 **선택된 것만** 인코딩해 UDP:6001 하나로 보낸다. 포트 6003 은 폐지.
`none` 은 **인코딩·송출만** 중단이고 캡처와 생프레임 로컬 탭(`/dev/shm/libi_cam_{front,back}`)
은 계속 돈다 — 탭까지 멈추면 복귀 중 도는 마커 도킹이 프레임을 못 얻어 조용히 죽는다.

**자세 판정이 붙었다.** 검출 가중치(`best.pt`)는 `task=detect` 라 키포인트를 못 내므로,
owner bbox crop 에만 `yolo11n-pose` 를 2차로 돌린다. 판정 로직은 `~/personal_repo/yolo_pose`
의 `posture.py` 를 import 해서 쓴다(복제하면 임계값이 두 곳으로 갈라진다).
끄려면 `--no-pose`, 프레임 예산을 넘기면 `--pose-every-n 3`.

```bash
./scripts/ai-server.sh --robot-host 192.168.0.31 --camera-label front
```

**AI 서버(별도 머신)에서**
```bash
./scripts/ai-server.sh                       # 로봇 영상을 UDP 로 받는다
./scripts/ai-server.sh --test-pattern        # 로봇 없이 확인
./scripts/ai-server.sh --camera 0            # 이 머신 웹캠으로
./scripts/ai-server.sh --drive-host <PI_IP>  # 추종 명령까지 로봇에 보낼 때
```

**로봇(Pi)에서** — 카메라 영상 전송
```bash
aba_controller/libi_drive_controller/ros_ws/scripts/image-sender.sh <AI서버IP>
```

**로봇 터치패널에서**
```bash
PERCEPTION_URL=<AI서버IP>:5007 FMS_URL=http://<FMS서버IP>:9001 \
  aba_controller/libi_gui/gui.sh pinky3
```

뷰어 포트(5007)는 기본적으로 모든 인터페이스에 열린다(`--bind 0.0.0.0`). 로봇 터치패널이
다른 머신이라 `127.0.0.1` 로 묶으면 아예 못 붙는다 — 원래는 뷰어가 같은 노트북에서 도는
전제였다(`follower_perception/laptop.sh` 가 `viewer 127.0.0.1 5007`). 같은 머신에서만 쓸
거라면 `--bind 127.0.0.1` 로 좁힐 수 있다.

`--drive-host` 로 실제 주행까지 시키려면 로봇에서 `cmd_bridge.py` 도 떠 있어야 한다
(`follower_perception/pi.sh` 가 bringup·카메라·cmd_bridge 를 한 번에 띄우지만, 그건
`libi_drive_controller/ros_ws/scripts/pi.sh` 와 **동시에 못 쓴다** — 같은
`bringup_robot.launch.xml` 을 서로 띄우려 해서 하드웨어가 충돌한다).

## 설치

`perception_server` 는 `torch` + `ultralytics` 가 필요하다. 없으면 `ai-server.sh` 가
설치 안내를 내고 멈춘다.

```bash
python3 -m pip install -r follower_perception/requirements.txt
```

사람 검출 가중치는 `follower_perception/weights/best.pt` 에 함께 커밋돼 있다
(`.gitignore` 의 `*.pt` 규칙에 예외를 뒀다 — 이 파일이 없으면 파이프라인이 아예 안 돈다).

## 테스트

세 서브트리를 **각각** 돌린다. 한 번에 모으면 `tests` 패키지 이름이 셋 다 같아서 pytest 가
엉뚱한 모듈을 import 한다(`--import-mode=importlib` 로 바꿔도 이번엔 경로 의존 때문에 더 깨진다).

```bash
(cd follower_perception && python3 -m pytest tests -q)   # 91
(cd follower_BT         && python3 -m pytest tests -q)   #  8
python3 -m pytest tests -q                               #  8
```
