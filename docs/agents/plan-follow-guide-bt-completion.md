# 추종·길잡이 BT 완성 + 복귀 진입부 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 로 Task 단위 실행. 각 스텝은 체크박스(`- [ ]`)로 추적한다.

**Goal:** 관리자 추종·이용자 길잡이를 하나의 py_trees 체계로 완성하고, 카메라를 BT가 필요할 때만 켜며, 복귀 진입부 5단계와 동적 장애물 회피(기본 OFF)를 붙인다.

**Architecture:** AI 서버 인지 파이프라인이 고른 주인 검출을 로봇 `libi_perception`으로 직접 보내 더미 스텁을 대체한다. 주행 제어·회복은 로봇 쪽 py_trees가 갖고, 카메라 선택은 `follow_node` 단독이 발행한다. 미션 BT는 `/fleet_cmd`로 세션 역할만 알린다.

**Tech Stack:** ROS2 Jazzy · py_trees · rclpy · Ultralytics YOLO(detect + pose) · OpenCV · Qt5/QML · nav2(KeepoutFilter) · pytest

## Global Constraints

- 입력 문서: `docs/agents/prd-follow-guide-bt-completion.md` (최종 진실). 설계 초안은 `docs/superpowers/specs/2026-07-27-follow-guide-bt-completion-design.md`(상단 개정표 참고)
- **git 조작 금지** — merge·rebase·push·main 반영은 전부 사용자가 한다. Task 커밋만 격리된 작업브랜치/worktree 안에서 허용
- **BT 노드를 추가·삭제·개명하면 `aba_fms_service/frontend/src/components/admin/btNodeFlags.ts`를 같이 고친다.** 키가 py_trees `name` 문자열 그대로다
- `Parallel` 안에서 자식 하나가 FAILURE면 즉시 전체 실패다. 새 시퀀스를 `Parallel` 안에 넣을 때 반드시 흡수 래퍼를 쓴다
- `RequestTransition()`은 `Parallel` 바깥, 브랜치 루트 `Sequence`의 마지막 자식이어야 한다
- 임계값 하드코딩 금지 — `config/params.yaml`(libi_modes) / `config.py`(libi_perception) / `constants.py`(follower_perception)
- 로봇 IP·`ROS_DOMAIN_ID`·포트는 근거 없이 바꾸지 않는다
- 기본 운용 맵은 **arte2 (1.26 × 2.16 m)**. 레인 0.151~0.601 m, 로봇 `LINEAR_X_MAX 0.12 m/s`
- 카메라 선택 값은 `"front" | "back" | "none"` 세 문자열뿐이다
- **`none` = JPEG 인코딩·UDP 송출만 중단. 캡처와 생프레임 로컬 탭은 계속 돈다**

### 테스트 실행 명령 (Task마다 해당하는 것만)

```bash
# libi_modes
cd aba_controller/libi_modes/ros_ws/src/libi_modes && PYTHONPATH=. python3 -m pytest test/ -q
# libi_perception
cd aba_controller/libi_modes/ros_ws/src/libi_perception && PYTHONPATH=. python3 -m pytest tests/ -q
# follower_perception (AI 서버)
cd aba_ai_service/follower_perception && python3 -m pytest tests -q
# aba_ai_service 루트
cd aba_ai_service && python3 -m pytest tests -q
```

---

## File Structure

| 파일 | 책임 |
|---|---|
| `aba_ai_service/follower_perception/follower_perception/posture_gate.py` (신규) | 자세 문자열 → 주행 허용 여부. ROS·YOLO 무관 순수 함수 |
| `.../follower_perception/exit_direction.py` (신규) | 스무더 속도 + bbox 위치 → 소실 방향 4분류. 순수 |
| `.../follower_perception/pose_estimator.py` (신규) | owner crop → 자세 문자열. Ultralytics 래퍼 + 기준비율 측정 |
| `.../follower_perception/pipeline.py` (수정) | 위 셋 배선, coast 게이트, epoch 부여 |
| `.../follower_perception/constants.py` (수정) | 새 임계값 |
| `aba_ai_service/detection_sink.py` (수정) | payload 신규 필드 직렬화 |
| `.../scripts/perception_server.py` (수정) | 로봇 sink 연결, camera/epoch 라벨 |
| `.../scripts/camera_sender.py` (수정) | 2장치 보유 · `camera_select` 구독 · 만료 워치독 · 생프레임 2슬롯 탭 |
| `.../scripts/frame_tap.py` (신규) | 생프레임 슬롯 쓰기/읽기 계약 (형식·시퀀스·stale) |
| `aba_controller/.../libi_perception/detection.py` (수정) | payload 신규 필드 optional 수신 |
| `.../libi_perception/control_loop.py` (수정) | `motion_ok` 게이트 |
| `.../libi_perception/recovery_bt.py` (수정) | `PeekBack`·`AlignHeading`·`Turn180`·`Scan3`, 카메라 요청 콜백 |
| `.../libi_perception/search_planner.py` (수정) | 참조 타임라인 갱신 |
| `.../libi_perception/config.py` (수정) | 새 상수 |
| `.../libi_perception/session.py` (신규) | 세션 id·역할·lease. `follow`/`guide`/`watch` 공통 |
| `.../libi_perception/follow_node.py` (수정) | 세션 라우팅, `camera_select` 발행, `requester_visible` 발행 |
| `aba_controller/.../libi_modes/common/working_actions.py` (수정) | `GuideExec` — watch 발행·거리 게이트·갈림길 확인 |
| `.../libi_modes/common/return_steps.py` (신규) | 복귀 5단계 leaf + 실패 흡수 재시도 래퍼 |
| `.../libi_modes/branches/returning.py` (수정) | 5단계 배선 |
| `.../libi_modes/registry.py` (수정) | 드라이버 주입 |
| `.../libi_modes/ros/providers.py` (수정) | `requester_visible` 신선도 TTL, 갈림길 정점 집합 |
| `.../libi_modes/config/params.yaml` (수정) | 맵 프로파일 |
| `aba_controller/libi_gui/qml/screens/GuideScreen.qml` (수정) | 등록 UI · 뒷캠 미니뷰 · 캠 라벨 |
| `aba_controller/libi_gui/src/RobotController.{h,cpp}` (수정) | watch 세션 발행·종료 |
| `aba_controller/.../robot_agent/app/core/ros_bridge.py` (수정) | goal 응답 콜백 · pending-cancel · goal 세대 |
| `aba_controller/.../robot_agent/app/core/fleet_link.py` (수정) | `BT_LAYER_ACTIONS` 확장 |
| `aba_controller/.../libi_perception/keepout_mask.py` (신규) | 통행 금지 마스크 발행 노드 |
| `.../pinky_navigation/params/nav2_params_keepout.yaml` (신규) | 필터 포함 파라미터 |
| `scripts/all/pi-all.sh` (수정) | `cam` 창 통합 · `follow-drive` 제거 · `--dyn-obstacle` |

---

## Wave 편성 (병렬 실행 계획)

> **개정** — 첫 편성은 거짓이었다. 같은 Wave 안에서 T3·T4 가 `constants.py` 를,
> T8·T10 이 `providers.py` 와 `params.yaml` 을 **동시에** 고쳤다. 병합 충돌이거나
> 한쪽이 다른 쪽 시그니처를 덮어쓴다. **T4 를 T3 에 흡수**하고, `providers.py`·
> `params.yaml` 계열을 T7 → T8 → T10 순으로 직렬화했다.

| Wave | Task | 근거 |
|---|---|---|
| **W1** | T1, T2, T3, T6 | 파일 겹침 없음. T3 가 옛 T4(소실방향)를 흡수 — 둘 다 `constants.py` 를 고쳤다 |
| **W2** | T5, T7, T11 | T5←T2,T3 / T7←T2 / T11 독립(`return_steps.py`·`returning.py`·`registry.py`) |
| **W3** | T8, T9 | T8←T7(`providers.py` 단독 소유) / T9←T7(카메라 콜백 계약). 파일 안 겹침 |
| **W4** | T10, T12 | T10←T8(`providers.py`·`params.yaml` 을 이제 단독 소유) / T12←T7 |
| **W5** | T13 | T10 이 끝나야 `GuideExec` 배선 위에 근접 정지를 얹을 수 있다 |
| **W6** | T14 | 전부 끝나야 노드 이름·플래그가 정확하다 |

**옛 T4 는 없다.** 소실 방향 분류는 T3 안에 있다.

W1·W2·W3·W4 는 worktree 격리 필수(동시 커밋이 review-package BASE..HEAD 경계를 섞는다).

---

### Task 1: robot_agent 결함 2건 — 주행 취소와 명령 수락

**Files:**
- Modify: `aba_controller/libi_drive_controller/robot_agent/app/core/ros_bridge.py`
- Modify: `aba_controller/libi_drive_controller/robot_agent/app/core/fleet_link.py:68`
- Test: `aba_controller/libi_drive_controller/robot_agent/tests/test_nav_cancel.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces: `ros_bridge.cancel_nav()` 가 실제로 목표를 취소한다. `fleet_link.BT_LAYER_ACTIONS` 에 `follow_admin`·`guide_watch`·`stop`·`follow_stop` 포함

**배경(반드시 읽을 것):** `send_nav_goal()` 이 `send_goal_async()` 결과에 `_on_goal_response` 콜백을 안 달아 `_active_goal_handle` 이 영원히 비어 있다. 그래서 `cancel_nav()` 가 **어떤 주행도 취소하지 못한다** — 배달·순회·복귀·길잡이 전부. 콜백만 달면 부족하다: goal 응답이 오기 **전에** 취소가 들어오면 여전히 놓치고, 새 goal 이 이전 핸들을 덮으면 옛 목표가 안 죽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_nav_cancel.py
class FakeGoalHandle:
    def __init__(self): self.canceled = False
    def cancel_goal_async(self): self.canceled = True; return object()

class FakeFuture:
    def __init__(self): self._cb = None; self._result = None
    def add_done_callback(self, cb): self._cb = cb
    def resolve(self, handle):
        self._result = handle
        if self._cb: self._cb(self)
    def result(self): return self._result

def test_cancel_after_goal_response_cancels(bridge, fake_action):
    fut = FakeFuture(); fake_action.next_future = fut
    bridge.send_nav_goal(1.0, 2.0, 0.0)
    gh = FakeGoalHandle(); fut.resolve(gh)
    bridge.cancel_nav()
    assert gh.canceled is True

def test_cancel_before_goal_response_still_cancels(bridge, fake_action):
    """취소가 goal 응답보다 먼저 와도 응답 시점에 취소가 적용돼야 한다."""
    fut = FakeFuture(); fake_action.next_future = fut
    bridge.send_nav_goal(1.0, 2.0, 0.0)
    bridge.cancel_nav()                 # 아직 핸들 없음
    gh = FakeGoalHandle(); fut.resolve(gh)
    assert gh.canceled is True

def test_new_goal_does_not_orphan_previous_handle(bridge, fake_action):
    f1 = FakeFuture(); fake_action.next_future = f1
    bridge.send_nav_goal(1.0, 2.0, 0.0)
    gh1 = FakeGoalHandle(); f1.resolve(gh1)
    f2 = FakeFuture(); fake_action.next_future = f2
    bridge.send_nav_goal(3.0, 4.0, 0.0)   # 이전 목표는 여기서 취소돼야 한다
    assert gh1.canceled is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_controller/libi_drive_controller/robot_agent && python3 -m pytest tests/test_nav_cancel.py -v`
Expected: FAIL — 세 개 모두. 첫 번째는 `gh.canceled is False`(콜백 미연결), 나머지는 그 상태의 파생

- [ ] **Step 3: `ros_bridge.py` 를 고친다**

```python
# send_nav_goal 안, send_goal_async 호출부
self._goal_generation += 1
gen = self._goal_generation
prev = self._active_goal_handle
if prev is not None:
    prev.cancel_goal_async()            # 새 goal 이 이전 핸들을 고아로 만들지 않는다
    self._active_goal_handle = None
fut = self._nav_action.send_goal_async(self._make_goal(x, y, yaw))
fut.add_done_callback(lambda f, g=gen: self._on_goal_response(f, g))

def _on_goal_response(self, future, generation=None):
    if generation is not None and generation != self._goal_generation:
        return                          # 낡은 세대의 응답 — 버린다
    gh = future.result()
    if gh is None or not getattr(gh, "accepted", True):
        return
    if self._cancel_pending:            # 응답보다 취소가 먼저 왔다
        self._cancel_pending = False
        gh.cancel_goal_async()
        return
    self._active_goal_handle = gh

def cancel_nav(self):
    gh = self._active_goal_handle
    if gh is None:
        self._cancel_pending = True     # 핸들이 오면 그때 취소한다
        return
    self._active_goal_handle = None
    gh.cancel_goal_async()
```

`__init__` 에 `self._goal_generation = 0`, `self._cancel_pending = False` 를 추가한다.

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `python3 -m pytest tests/test_nav_cancel.py -v`
Expected: PASS 3개

- [ ] **Step 5: `fleet_link.py:68` 한 줄을 고친다**

```python
BT_LAYER_ACTIONS = frozenset({
    "navigate", "guide",
    # BT·패널이 쓰는 세션 명령. 여기 없으면 "알 수 없는 action" 실패 결과가 먼저 나가고
    # FleetCmdDriver 가 그걸 집어 세션이 시작 즉시 끝난다.
    "follow_admin", "guide_watch", "watch", "stop", "follow_stop",
})
```

- [ ] **Step 6: 커밋**

```bash
git add aba_controller/libi_drive_controller/robot_agent/app/core/ros_bridge.py \
        aba_controller/libi_drive_controller/robot_agent/app/core/fleet_link.py \
        aba_controller/libi_drive_controller/robot_agent/tests/test_nav_cancel.py
git commit -m "fix(robot_agent): nav goal 취소 불가·세션 명령 오분류 수정"
```

---

### Task 2: 검출 payload 계약 확장

**Files:**
- Modify: `aba_ai_service/follower_perception/follower_perception/detection.py` ← **AI 측 dataclass. 빠뜨리면 Task 5 가 `TypeError` 로 죽는다**
- Modify: `aba_ai_service/detection_sink.py`
- Modify: `aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/detection.py`
- Test: `aba_ai_service/tests/test_detection_sink.py`, `aba_ai_service/follower_perception/tests/test_detection.py`, `aba_controller/.../libi_perception/tests/test_detection.py`

**Interfaces:**
- Produces: payload 신규 키 `posture`(str|None) · `motion_ok`(bool) · `camera`(str|None) · `camera_epoch`(int). **양쪽 `Detection` dataclass 에 같은 이름 필드.** 전부 optional(없으면 기존 동작)

**배경 1 — `Detection` 이 **두 개**다. 이름이 같아서 한쪽만 고치기 쉽다.**

```
aba_ai_service/follower_perception/follower_perception/detection.py   생산자 (8 필드)
aba_controller/.../libi_perception/detection.py                       소비자 (10 필드)
```
`detection_sink.detection_to_dict()` 가 앞의 것을 읽어 뒤의 것으로 보낸다. **두 dataclass 와 직렬화를 한 Task 안에서 원자적으로 바꾼다** — 나누면 중간 상태가 깨진다.

**배경 2:** 카메라 전환 순간 TCP 버퍼에 남은 이전 카메라 프레임이 새 프레임과 섞인다. `camera` 문자열만으로는 같은 카메라로 두 번 돌아온 경우를 구분 못 하므로 **단조 증가 `camera_epoch`** 를 같이 보낸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# aba_ai_service/tests/test_detection_sink.py 에 추가
def test_new_fields_serialised():
    det = _fake_detection(posture="Standing", motion_ok=True, camera="back", camera_epoch=7)
    d = detection_to_dict(det)
    assert d["posture"] == "Standing"
    assert d["motion_ok"] is True
    assert d["camera"] == "back"
    assert d["camera_epoch"] == 7

# libi_perception/tests/test_detection.py 에 추가
def test_new_fields_optional_roundtrip():
    d = {"cx": 1.0, "cy": 2.0, "area": 3.0, "bbox": [0, 0, 1, 1],
         "track_id": 5, "is_owner": True, "confidence": 0.9, "is_predicted": False}
    det = detection_from_dict(d)          # 옛 payload — 신규 키 없음
    assert det.posture is None
    assert det.motion_ok is True          # 모르면 막지 않는다(기존 동작 유지)
    assert det.camera is None
    assert det.camera_epoch == 0

def test_new_fields_read_when_present():
    d = {"cx": 1.0, "cy": 2.0, "area": 3.0, "bbox": [0, 0, 1, 1],
         "track_id": 5, "is_owner": True, "confidence": 0.9, "is_predicted": False,
         "posture": "Lying", "motion_ok": False, "camera": "front", "camera_epoch": 3}
    det = detection_from_dict(d)
    assert det.posture == "Lying" and det.motion_ok is False
    assert det.camera == "front" and det.camera_epoch == 3
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_ai_service && python3 -m pytest tests/test_detection_sink.py -v` 및
`cd aba_controller/libi_modes/ros_ws/src/libi_perception && PYTHONPATH=. python3 -m pytest tests/test_detection.py -v`
Expected: FAIL — `AttributeError: 'Detection' object has no attribute 'posture'` 등

- [ ] **Step 3: 세 곳을 고친다 — 생산자 dataclass 를 빠뜨리지 않는다**

```python
# aba_ai_service/follower_perception/follower_perception/detection.py — Detection 에 추가
    #: 자세 판정 문자열. None = 자세 모델이 안 돈다.
    posture: str | None = None
    #: 자세 + 소실방향을 합친 최종 주행 가부. 모르면 True.
    motion_ok: bool = True
    #: 이 프레임이 온 카메라("front"/"back"). None = 안 알려줬다.
    camera: str | None = None
    #: 카메라 전환마다 1 증가. 전환 순간 섞인 옛 프레임을 버리는 데 쓴다.
    camera_epoch: int = 0
```

```python
# aba_ai_service/detection_sink.py — detection_to_dict 반환 dict 에 추가
        "posture": getattr(det, "posture", None),
        "motion_ok": bool(getattr(det, "motion_ok", True)),
        "camera": getattr(det, "camera", None),
        "camera_epoch": int(getattr(det, "camera_epoch", 0) or 0),
```

```python
# libi_perception/detection.py — Detection 에 필드 추가
    #: 자세 판정. None = 판정 소스가 없다(옛 payload) — 막지 않는다.
    posture: str | None = None
    #: 자세 + 소실방향을 합친 최종 주행 가부. 모르면 True(기존 동작 유지).
    motion_ok: bool = True
    #: 이 프레임이 온 카메라. None = 안 알려줬다.
    camera: str | None = None
    #: 카메라 전환마다 1 증가. 전환 순간 섞여 들어온 옛 프레임을 버리는 데 쓴다.
    camera_epoch: int = 0

# detection_from_dict 에 추가
        posture=d.get('posture'),
        motion_ok=bool(d.get('motion_ok', True)),
        camera=d.get('camera'),
        camera_epoch=int(d.get('camera_epoch', 0) or 0),
```

- [ ] **Step 4: 통과를 확인한다**

Run: 위 두 명령
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add aba_ai_service/detection_sink.py aba_ai_service/tests/test_detection_sink.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/detection.py \
        aba_controller/libi_modes/ros_ws/src/libi_perception/tests/test_detection.py
git commit -m "feat(detection): payload 에 자세·주행가부·카메라·epoch 필드 추가"
```

---

### Task 3: 인지 게이트 두 모듈 (자세 + 소실 방향)

> **한 Task 다.** 원래 둘로 나눴다가 합쳤다 — 둘 다 `constants.py` 를 고쳐서 병렬로 돌리면
> 병합이 충돌한다. 아래 3-a·3-b 를 순서대로 하고 **커밋은 두 번**(각 모듈이 독립적으로
> 리뷰 가능하다) 한다.

#### 3-a. 자세 게이트

**Files:**
- Create: `aba_ai_service/follower_perception/follower_perception/posture_gate.py`
- Modify: `aba_ai_service/follower_perception/follower_perception/constants.py`
- Test: `aba_ai_service/follower_perception/tests/test_posture_gate.py`

**Interfaces:**
- Produces: `PostureGate(unknown_limit=UNKNOWN_STOP_FRAMES)` — `update(posture: str|None) -> bool`(주행 가능), `reset()`. 상태: 직전 허용값과 `Unknown` 연속 카운터

**배경:** `Unknown` 을 즉시 정지로 치면 안 된다. 키포인트 신뢰도 하한이 어깨 2점·골반 2점 **전부**에 걸리므로 사람이 옆으로 서 있기만 해도 `Unknown` 이 난다. 그대로 두면 정상 추종 중 계속 멈칫한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_posture_gate.py
from follower_perception.posture_gate import PostureGate

def test_standing_allows():
    g = PostureGate(unknown_limit=10)
    assert g.update("Standing") is True

def test_lying_stops_immediately():
    g = PostureGate(unknown_limit=10)
    g.update("Standing")
    assert g.update("Lying") is False

def test_calibrating_stops():
    g = PostureGate(unknown_limit=10)
    assert g.update("Calibrating") is False

def test_unknown_holds_previous_until_limit():
    g = PostureGate(unknown_limit=3)
    g.update("Standing")
    assert g.update("Unknown") is True      # 1
    assert g.update("Unknown") is True      # 2
    assert g.update("Unknown") is False     # 3 — 한계 도달

def test_unknown_counter_resets_on_standing():
    g = PostureGate(unknown_limit=3)
    g.update("Standing")
    g.update("Unknown"); g.update("Unknown")
    g.update("Standing")
    assert g.update("Unknown") is True      # 카운터가 리셋됐다

def test_unknown_after_lying_stays_stopped():
    """직전이 정지였으면 Unknown 은 그 정지를 유지한다."""
    g = PostureGate(unknown_limit=3)
    g.update("Lying")
    assert g.update("Unknown") is False

def test_none_posture_does_not_block():
    """판정 소스가 없으면(옛 payload) 막지 않는다."""
    g = PostureGate(unknown_limit=3)
    assert g.update(None) is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_ai_service/follower_perception && python3 -m pytest tests/test_posture_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'follower_perception.posture_gate'`

- [ ] **Step 3: 구현한다**

```python
# follower_perception/posture_gate.py
"""자세 문자열 → 주행 가부. ROS·YOLO 를 모른다 — 그래서 로봇 없이 시험된다.

`Unknown` 을 곧바로 정지로 치지 않는 이유: 키포인트 신뢰도 하한이 어깨 2점·골반 2점
전부에 걸려서, 사람이 옆으로 서 있기만 해도 `Unknown` 이 난다. 즉시 정지로 두면
정상 추종 중에도 계속 멈칫한다. 그래서 연속 N 프레임일 때만 정지한다.
"""
from .constants import UNKNOWN_STOP_FRAMES

STANDING, LYING, UNKNOWN, CALIBRATING = "Standing", "Lying", "Unknown", "Calibrating"


class PostureGate:
    def __init__(self, unknown_limit: int = UNKNOWN_STOP_FRAMES):
        self.unknown_limit = unknown_limit
        self._allowed = True
        self._unknown_run = 0

    def reset(self):
        self._allowed = True
        self._unknown_run = 0

    def update(self, posture) -> bool:
        if posture is None:            # 판정 소스 없음 — 막지 않는다
            self._unknown_run = 0
            self._allowed = True
        elif posture == STANDING:
            self._unknown_run = 0
            self._allowed = True
        elif posture in (LYING, CALIBRATING):
            self._unknown_run = 0
            self._allowed = False
        else:                          # UNKNOWN 및 알 수 없는 문자열
            self._unknown_run += 1
            if self._unknown_run >= self.unknown_limit:
                self._allowed = False
        return self._allowed
```

```python
# constants.py 에 추가
# 자세 게이트
UNKNOWN_STOP_FRAMES = 10   # Unknown 이 이만큼 연속되면 정지. 옆모습에서 키포인트가
                           # 자주 죽으므로 즉시 정지로 두면 추종이 계속 끊긴다.
POSE_EVERY_N_FRAMES = 1    # 자세 추론 주기. 프레임 예산 초과 시 3 으로 올린다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m pytest tests/test_posture_gate.py -v`
Expected: PASS 7개

- [ ] **Step 5: 커밋**

```bash
git add follower_perception/posture_gate.py follower_perception/constants.py tests/test_posture_gate.py
git commit -m "feat(perception): 자세 게이트 모듈 추가"
```

---

#### 3-b. 소실 방향 분류

**Files:**
- Create: `aba_ai_service/follower_perception/follower_perception/exit_direction.py`
- Modify: `aba_ai_service/follower_perception/follower_perception/constants.py`
- Test: `aba_ai_service/follower_perception/tests/test_exit_direction.py`

**Interfaces:**
- Produces: `classify_exit(bbox, velocity, frame_w, frame_h, margin_ratio=EXIT_EDGE_MARGIN_RATIO) -> str` 반환값 `"side" | "down" | "up" | "center"`, 그리고 `may_coast(direction, last_posture) -> bool`

**배경:** 이미지 좌표라 y 는 아래로 증가한다. `velocity` 는 `BBoxSmoother.velocity` 형태 `[vcx, vcy, varea]`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_exit_direction.py
from follower_perception.exit_direction import classify_exit, may_coast

W, H = 640, 480

def test_left_edge_is_side():
    assert classify_exit((0, 100, 40, 400), [-50.0, 0.0, 0.0], W, H) == "side"

def test_right_edge_is_side():
    assert classify_exit((600, 100, 639, 400), [50.0, 0.0, 0.0], W, H) == "side"

def test_bottom_edge_is_down():
    assert classify_exit((200, 200, 400, 479), [0.0, 60.0, 0.0], W, H) == "down"

def test_top_edge_is_up():
    assert classify_exit((200, 0, 400, 200), [0.0, -60.0, 0.0], W, H) == "up"

def test_area_surge_is_down_even_without_edge():
    """면적이 급증하며 사라지면 코앞이다 — 가장자리에 안 닿아도 down."""
    assert classify_exit((150, 150, 500, 400), [0.0, 5.0, 9000.0], W, H) == "down"

def test_middle_is_center():
    assert classify_exit((280, 200, 360, 300), [2.0, 1.0, 0.0], W, H) == "center"

def test_down_and_side_together_prefers_down():
    """둘 다 걸리면 안전한 쪽(정지)이 이긴다."""
    assert classify_exit((0, 300, 60, 479), [-40.0, 40.0, 0.0], W, H) == "down"

def test_coast_rules():
    assert may_coast("side", "Standing") is True
    assert may_coast("center", "Standing") is True
    assert may_coast("down", "Standing") is False
    assert may_coast("up", "Standing") is False
    assert may_coast("side", "Lying") is False       # 자세가 아니면 방향 무관 정지
    assert may_coast("side", None) is True           # 판정 소스 없음 — 막지 않는다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m pytest tests/test_exit_direction.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현한다**

```python
# follower_perception/exit_direction.py
"""대상을 놓친 첫 순간, 어느 쪽으로 사라졌는지 분류한다.

옆으로 사라진 것과 아래로 사라진 것은 의미가 정반대다. 옆은 "지나갔다"이고
아래는 "로봇 코앞이거나 쓰러졌다"이다. 위는 "누가 집어 올렸다" 또는 시야 위로 솟았다.
아래·위로 사라졌는데 마지막 위치로 계속 밀고 들어가면 그대로 들이받는다.

좌표는 이미지 좌표다 — y 는 아래로 증가한다.
"""
from .constants import EXIT_EDGE_MARGIN_RATIO, EXIT_AREA_SURGE

SIDE, DOWN, UP, CENTER = "side", "down", "up", "center"


def classify_exit(bbox, velocity, frame_w, frame_h,
                  margin_ratio: float = EXIT_EDGE_MARGIN_RATIO,
                  area_surge: float = EXIT_AREA_SURGE) -> str:
    x1, y1, x2, y2 = bbox
    vx, vy, varea = velocity[0], velocity[1], velocity[2]
    mx = frame_w * margin_ratio
    my = frame_h * margin_ratio

    at_bottom = y2 >= frame_h - my
    at_top = y1 <= my
    at_side = x1 <= mx or x2 >= frame_w - mx

    # 안전한 쪽이 먼저 이긴다 — down/up 은 정지, side/center 는 진행이므로
    # 둘 다 걸리면 정지 쪽으로 판정한다.
    if (at_bottom and vy > 0) or varea >= area_surge:
        return DOWN
    if at_top and vy < 0:
        return UP
    if at_side and abs(vx) > abs(vy):
        return SIDE
    return CENTER


def may_coast(direction: str, last_posture) -> bool:
    """예측 추종(coast)을 허용할지. 마지막 자세가 Standing 이 아니면 방향 무관 정지."""
    if last_posture is not None and last_posture != "Standing":
        return False
    return direction in (SIDE, CENTER)
```

```python
# constants.py 에 추가
# 소실 방향 판정
EXIT_EDGE_MARGIN_RATIO = 0.08   # 프레임 가장자리로 볼 비율(폭·높이 각각)
EXIT_AREA_SURGE = 8000.0        # 면적 속도(px^2/frame)가 이보다 크면 코앞으로 본다
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m pytest tests/test_exit_direction.py -v`
Expected: PASS 8개

- [ ] **Step 5: 커밋**

```bash
git add follower_perception/exit_direction.py follower_perception/constants.py tests/test_exit_direction.py
git commit -m "feat(perception): 소실 방향 분류 모듈 추가"
```

---

### Task 5: 자세 2차 추론 + 파이프라인 배선

**Files:**
- Create: `aba_ai_service/follower_perception/follower_perception/pose_estimator.py`
- Modify: `aba_ai_service/follower_perception/follower_perception/pipeline.py`
- Test: `aba_ai_service/follower_perception/tests/test_pipeline_posture.py`

**Interfaces:**
- Consumes: `PostureGate`(T3), `classify_exit`/`may_coast`(T4), payload 필드(T2)
- Produces: `PoseEstimator(model=None, every_n=POSE_EVERY_N_FRAMES)` — `classify(frame, bbox) -> str`, `recalibrate()`. `FollowerPerception.get_latest()` 가 `posture`·`motion_ok`·`camera`·`camera_epoch` 를 채운 `Detection` 반환

**배경:** 기존 검출 가중치는 `task=detect`, `names={0:'people', 1:'figure'}` 라 키포인트를 안 낸다(확인 완료). 그래서 `yolo11n-pose.pt` 를 **owner bbox crop 에만** 2차로 돌린다. 자세 기준비율은 **등록할 때마다** 새로 측정한다(약 60프레임). 그동안 `Calibrating` → 주행 금지.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_pipeline_posture.py
class FakePose:
    """crop 을 받아 미리 정해둔 자세를 돌려주는 대역."""
    def __init__(self, seq): self.seq = list(seq); self.calls = 0
    def classify(self, frame, bbox):
        v = self.seq[min(self.calls, len(self.seq) - 1)]; self.calls += 1; return v
    def recalibrate(self): self.calls = 0

def test_posture_flows_into_detection(pipeline_with_owner):
    p = pipeline_with_owner(pose=FakePose(["Standing"]))
    p.run(frame_with_person())
    det = p.get_latest()
    assert det.posture == "Standing" and det.motion_ok is True

def test_lying_sets_motion_ok_false_but_keeps_detection(pipeline_with_owner):
    """누워 있어도 '보이는' 것은 맞다 — 검출은 유지하고 주행만 막는다."""
    p = pipeline_with_owner(pose=FakePose(["Standing", "Lying"]))
    p.run(frame_with_person()); p.run(frame_with_person())
    det = p.get_latest()
    assert det is not None and det.motion_ok is False

def test_down_exit_stops_coasting(pipeline_with_owner):
    """아래로 사라지면 COAST_LIMIT 이 남아 있어도 즉시 None."""
    p = pipeline_with_owner(pose=FakePose(["Standing"]))
    p.run(frame_with_person_at_bottom())
    p.run(empty_frame())                      # 소실
    assert p.get_latest() is None

def test_side_exit_still_coasts(pipeline_with_owner):
    p = pipeline_with_owner(pose=FakePose(["Standing"]))
    p.run(frame_with_person_at_left_edge())
    p.run(empty_frame())
    assert p.get_latest() is not None         # 예측 추종 유지

def test_registration_triggers_recalibration(pipeline_with_owner):
    pose = FakePose(["Calibrating", "Standing"])
    p = pipeline_with_owner(pose=pose)
    p.run(frame_with_person())
    p.register(frame_with_person(), bbox=(10, 10, 50, 90))
    assert pose.calls == 0                    # recalibrate() 로 리셋됐다

def test_camera_epoch_increments_on_switch(pipeline_with_owner):
    p = pipeline_with_owner(pose=FakePose(["Standing"]))
    p.set_camera("front"); p.run(frame_with_person())
    e1 = p.get_latest().camera_epoch
    p.set_camera("back");  p.run(frame_with_person())
    assert p.get_latest().camera_epoch > e1
    assert p.get_latest().camera == "back"

def test_pose_skipped_when_every_n_gt_1(pipeline_with_owner):
    """주기를 낮추면 직전 판정을 유지한다."""
    pose = FakePose(["Standing", "Lying"])
    p = pipeline_with_owner(pose=pose, every_n=3)
    for _ in range(3):
        p.run(frame_with_person())
    assert pose.calls == 1                    # 3프레임에 1회
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m pytest tests/test_pipeline_posture.py -v`
Expected: FAIL — `FollowerPerception` 에 `set_camera`/`register` 훅과 자세 배선이 없다

- [ ] **Step 3: `pose_estimator.py` 를 만든다**

```python
# follower_perception/pose_estimator.py
"""owner bbox crop 에만 자세 모델을 돌린다.

전체 프레임이 아니라 crop 이라 비용이 작고, 검출·재식별은 기존 커스텀 가중치가
그대로 맡는다(그 가중치는 detect 전용이라 키포인트를 못 낸다).

기준비율(RatioCalibrator)은 **등록할 때마다** 다시 잰다. 기준은 카메라 높이·거리에
따라 달라지는데 로봇은 매번 다른 자리에서 등록하기 때문이다. 재는 동안에는
"Calibrating" 을 돌려 주행을 막는다 — 등록 직후 몇 초 서 있는 편이,
틀린 기준으로 판정하며 움직이는 것보다 낫다.
"""
import sys
from pathlib import Path

from .constants import POSE_EVERY_N_FRAMES, POSE_WEIGHTS

#: yolo_pose 저장소의 posture.py 를 그대로 쓴다. 알고리즘을 복제하지 않는다 —
#: 복제하면 임계값이 두 곳으로 갈라져 한쪽만 고쳐진다.
#:
#: ⚠️ **경로를 상대경로로 계산하지 마라.** yolo_pose 는 이 저장소 **밖**에 있는
#:    별개 저장소다(`~/personal_repo/yolo_pose`). `parents[N]` 로 짚으면
#:    `aba_project/yolo_pose` 를 가리켜 import 가 실패한다.
_YOLO_POSE_DIR = os.environ.get("LIBI_YOLO_POSE_DIR", YOLO_POSE_DIR)


def _load_posture_module():
    if not os.path.isdir(_YOLO_POSE_DIR):
        raise RuntimeError(
            f"yolo_pose 저장소를 찾지 못했다: {_YOLO_POSE_DIR}\n"
            f"  LIBI_YOLO_POSE_DIR 환경변수로 경로를 주거나 constants.YOLO_POSE_DIR 을 고쳐라.")
    if _YOLO_POSE_DIR not in sys.path:
        sys.path.insert(0, _YOLO_POSE_DIR)
    import posture
    return posture


class PoseEstimator:
    def __init__(self, model=None, every_n: int = POSE_EVERY_N_FRAMES, weights=POSE_WEIGHTS):
        self._posture = _load_posture_module()
        self._model = model                      # None 이면 첫 호출에 lazy load
        self._weights = weights
        self.every_n = max(1, int(every_n))
        self._frame = 0
        self._last = self._posture.UNKNOWN
        self._calibrator = self._posture.RatioCalibrator()

    def recalibrate(self):
        """등록 시 호출. 기준비율을 처음부터 다시 잰다.

        `RatioCalibrator` 는 기준을 한 번 확정하면 갱신하지 않는 설계다
        (posture.py 클래스 주석: "다시 재려면 새 인스턴스를 만든다").
        그래서 리셋이 아니라 **새로 만든다.**
        """
        self._calibrator = self._posture.RatioCalibrator()
        self._frame = 0
        self._last = "Calibrating"

    @property
    def calibration_progress(self):
        """(모은 프레임, 필요한 프레임). 패널의 "자세 측정 중 23/60" 표시에 쓴다."""
        return self._calibrator.progress

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self._weights)
        return self._model

    def classify(self, frame, bbox) -> str:
        self._frame += 1
        if self._frame % self.every_n != 0:
            return self._last                    # 주기 낮춤 — 직전 판정 유지
        x1, y1, x2, y2 = (int(v) for v in bbox)
        crop = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if crop.size == 0:
            self._last = self._posture.UNKNOWN
            return self._last
        res = self._ensure_model()(crop, verbose=False)
        kp = getattr(res[0], "keypoints", None) if res else None
        if kp is None or kp.xy is None or len(kp.xy) == 0:
            self._last = self._posture.UNKNOWN
            return self._last
        xy, conf = kp.xy[0], (kp.conf[0] if kp.conf is not None else None)

        # ⚠️ 실제 API 다 — 추측하지 말 것 (posture.py:112,125-135,197-224 에서 확인)
        #   · 표본 투입은 add(keypoints) 가 아니라 update(ratio) 다. ratio 는 torso_ratio() 로 만든다
        #   · 확정된 기준값 속성은 .ratio 가 아니라 .reference 다
        #   · classify_posture() 는 문자열이 아니라 (상태, 각도) **튜플**을 돌려준다.
        #     튜플을 그대로 payload 에 넣으면 PostureGate 가 전부 Unknown 으로 처리한다
        if not self._calibrator.done:
            self._calibrator.update(self._posture.torso_ratio(xy))
            self._last = "Calibrating"
            return self._last
        state, _angle = self._posture.classify_posture(
            xy, conf, ref_ratio=self._calibrator.reference)
        self._last = state
        return self._last
```

```python
# constants.py 에 추가
POSE_WEIGHTS = "yolo11n-pose.pt"   # 자세 전용 2차 모델. 검출 가중치(best.pt)는 그대로 둔다.
# yolo_pose 는 이 저장소 **밖**의 별개 저장소다. 상대경로로 짚으면 안 된다.
YOLO_POSE_DIR = "/home/ane/personal_repo/yolo_pose"   # LIBI_YOLO_POSE_DIR 로 덮어쓸 수 있다
```

**검증 명령 (구현 전에 한 번 돌려서 API 를 눈으로 확인한다):**

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "/home/ane/personal_repo/yolo_pose")
import posture, inspect
c = posture.RatioCalibrator()
print("RatioCalibrator 메서드:", [m for m in dir(c) if not m.startswith("_")])
print("classify_posture 시그니처:", inspect.signature(posture.classify_posture))
EOF
```
Expected: `['done', 'progress', 'reference', 'update']` 그리고 `classify_posture` 가
`(state, angle)` 튜플을 돌려준다는 것. 다르면 **여기서 멈추고** 실제 API 에 맞춘다.

- [ ] **Step 4: `pipeline.py` 를 배선한다**

```python
# FollowerPerception.__init__ 에 추가
        self.pose = pose or PoseEstimator()
        self.posture_gate = PostureGate()
        self.camera = None
        self.camera_epoch = 0
        self._last_posture = None
        self._exit_dir = None

    def set_camera(self, name):
        """카메라가 바뀌면 epoch 를 올리고 **추적 상태만** 비운다.

        ⚠️ `matcher.reset()` 을 부르면 **안 된다** — 그건 템플릿까지 지운다
        (`target_matcher.py:47-51`). 등록한 사람을 잊어버리므로 전환할 때마다
        재등록해야 한다. 지울 것은 track_id 잠금(`safe_id`)과 스무더뿐이다.
        """
        if name == self.camera:
            return
        self.camera = name
        self.camera_epoch += 1
        self.matcher.safe_id = None      # 이전 카메라의 track_id 를 새 프레임에 끌고 가지 않는다
        self.smoother.reset()
        self._miss = 0
        self._exit_dir = None
        self.posture_gate.reset()

    def register(self, frame, bbox):
        self.matcher.register(TargetMatcher._crop(frame, bbox))
        self.pose.recalibrate()              # 등록마다 기준비율 재측정
        self.posture_gate.reset()
```

```python
# run() — owner 확정 직후
            posture = self.pose.classify(frame, owner.bbox)
            self._last_posture = posture
            self.posture_gate.update(posture)
            self._frame_shape = frame.shape[:2]
        else:
            if self._miss == 0 and self._last_owner is not None:
                # 놓친 **첫 순간**에만 방향을 래치한다. 매 tick 다시 재면 예측이 흘러가며
                # 분류가 바뀐다.
                h, w = self._frame_shape
                self._exit_dir = classify_exit(
                    self._last_owner.bbox, self.smoother.velocity, w, h)
            self._miss += 1
```

```python
# get_latest() — coast 판정에 방향 게이트를 건다
        if self._miss == 0:
            pred = self.smoother.predict(PREDICT_DT); is_pred = False
        elif self._miss <= COAST_LIMIT and may_coast(self._exit_dir, self._last_posture):
            pred = self.smoother.predict(self._miss * FRAME_DT); is_pred = True
        else:
            return None
        ...
        return Detection(..., posture=self._last_posture,
                         motion_ok=self.posture_gate.update(self._last_posture),
                         camera=self.camera, camera_epoch=self.camera_epoch)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m pytest tests/ -q`
Expected: PASS — 기존 91개 + 신규 7개

- [ ] **Step 6: 커밋**

```bash
git add follower_perception/pose_estimator.py follower_perception/pipeline.py \
        follower_perception/constants.py tests/test_pipeline_posture.py
git commit -m "feat(perception): 자세 2차 추론과 소실방향 게이트를 파이프라인에 배선"
```

---

### Task 6: 카메라 송출 단일 프로세스 + 생프레임 탭

**Files:**
- Modify: `aba_ai_service/follower_perception/scripts/camera_sender.py`
- Create: `aba_ai_service/follower_perception/scripts/frame_tap.py`
- Modify: `aba_controller/libi_drive_controller/ros_ws/scripts/image-sender.sh`
- Test: `aba_ai_service/follower_perception/tests/test_frame_tap.py`, `tests/test_camera_select.py`

**Interfaces:**
- Produces:
  - `frame_tap.write(slot: str, frame, seq: int, stamp: float)` / `frame_tap.read(slot) -> (frame, seq, stamp) | None` / `frame_tap.cleanup()`
  - 슬롯 이름은 `"front"`, `"back"` 두 개 고정. 경로 `/dev/shm/libi_cam_<slot>`
  - `CameraSelect(expiry_sec=CAMERA_SELECT_EXPIRY)` — `set(value, stamp)`, `current(now) -> str`(만료되면 `"none"`)

**배경(중요):**
- **`none` = 인코딩·UDP 송출만 중단.** 캡처와 탭은 계속 돈다. 이 문장이 깨지면 마커 도킹이 조용히 죽는다
- **latched QoS 만으로는 부족하다.** TRANSIENT_LOCAL 은 살아 있는 발행자의 마지막 샘플만 준다 — 발행자가 죽으면 캐시도 사라지고, 송출기가 재시작하면 세션이 끝난 뒤에도 stale `front` 를 받아 **영상이 되살아난다.** 그래서 송출기 쪽에 **만료 워치독**을 둔다
- 탭은 **두 슬롯 모두 항상** 기록한다. 소비자(마커 도킹)가 슬롯을 직접 고르므로 `camera_select` 를 건드릴 필요가 없다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_frame_tap.py
import numpy as np
from scripts import frame_tap

def test_roundtrip(tmp_shm):
    f = np.zeros((4, 4, 3), dtype=np.uint8); f[0, 0] = 255
    frame_tap.write("front", f, seq=1, stamp=100.0)
    got, seq, stamp = frame_tap.read("front")
    assert seq == 1 and stamp == 100.0 and got.shape == f.shape and got[0, 0, 0] == 255

def test_slots_are_independent(tmp_shm):
    frame_tap.write("front", np.zeros((2, 2, 3), np.uint8), 1, 1.0)
    frame_tap.write("back",  np.ones((2, 2, 3), np.uint8), 2, 2.0)
    assert frame_tap.read("front")[1] == 1
    assert frame_tap.read("back")[1] == 2

def test_read_missing_slot_returns_none(tmp_shm):
    assert frame_tap.read("back") is None

def test_stale_detection_is_caller_side(tmp_shm):
    """탭은 stale 판정을 하지 않는다 — 타임스탬프를 주고 소비자가 정한다."""
    frame_tap.write("front", np.zeros((2, 2, 3), np.uint8), 1, 10.0)
    _, _, stamp = frame_tap.read("front")
    assert stamp == 10.0

# tests/test_camera_select.py
from scripts.camera_sender import CameraSelect

def test_default_is_none():
    assert CameraSelect(expiry_sec=5).current(now=0.0) == "none"

def test_set_then_current():
    cs = CameraSelect(expiry_sec=5); cs.set("front", stamp=0.0)
    assert cs.current(now=1.0) == "front"

def test_expires_to_none():
    """발행자가 죽어 갱신이 끊기면 스스로 none 으로 떨어진다.
    안 그러면 세션이 끝난 뒤에도 영상이 계속 나간다."""
    cs = CameraSelect(expiry_sec=5); cs.set("front", stamp=0.0)
    assert cs.current(now=6.0) == "none"

def test_refresh_extends():
    cs = CameraSelect(expiry_sec=5); cs.set("front", stamp=0.0); cs.set("front", stamp=4.0)
    assert cs.current(now=8.0) == "front"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_ai_service/follower_perception && python3 -m pytest tests/test_frame_tap.py tests/test_camera_select.py -v`
Expected: FAIL — 두 모듈 모두 없음

- [ ] **Step 3: `frame_tap.py` 를 만든다**

```python
# scripts/frame_tap.py
"""생프레임 로컬 탭 — 같은 Pi 안의 다른 프로세스가 카메라 장치를 열지 않고 프레임을 얻는다.

## 왜 필요한가
Pi 에서 카메라 장치를 두 프로세스가 열면 앞캠이 'Device or resource busy' 로 죽는다
(scripts/all/pi-all.sh 머리말의 실제 사고). 그래서 장치는 camera_sender 하나만 열고,
프레임이 필요한 쪽은 여기서 읽는다.

## 계약
- 슬롯은 "front", "back" 두 개다. **선택 여부와 무관하게 둘 다 항상 기록한다** —
  소비자가 원하는 쪽을 직접 고른다.
- JPEG 이 아니라 **생프레임(BGR)** 이다. 마커 코너 추정에 재압축 손실을 주지 않기 위해서다.
- 한 슬롯은 파일 하나다. 원자적 교체(임시파일 → os.replace)로 쓰므로 읽는 쪽이
  반쯤 쓰인 프레임을 보지 않는다. 락은 두지 않는다.
- **stale 판정은 소비자 몫이다.** 여기서는 `stamp`(time.monotonic)만 붙인다.
"""
import os
import tempfile

import numpy as np

SLOTS = ("front", "back")
_DIR = os.environ.get("LIBI_CAM_TAP_DIR", "/dev/shm")


def _path(slot: str) -> str:
    if slot not in SLOTS:
        raise ValueError(f"알 수 없는 슬롯: {slot} (가능: {SLOTS})")
    return os.path.join(_DIR, f"libi_cam_{slot}.npz")


def write(slot: str, frame, seq: int, stamp: float) -> None:
    path = _path(slot)
    fd, tmp = tempfile.mkstemp(dir=_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, frame=frame, seq=np.int64(seq), stamp=np.float64(stamp))
        os.replace(tmp, path)          # 원자적 — 반쯤 쓰인 프레임을 못 읽는다
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read(slot: str):
    path = _path(slot)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path) as z:
            return z["frame"], int(z["seq"]), float(z["stamp"])
    except (OSError, ValueError, KeyError):
        return None                    # 쓰는 중이었다 — 다음 tick 에 다시 읽는다


def cleanup() -> None:
    """송출기 종료 시 호출. 남은 슬롯이 stale 한 채로 보이지 않게 한다."""
    for s in SLOTS:
        try:
            os.unlink(_path(s))
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: `camera_sender.py` 를 고친다**

```python
# scripts/camera_sender.py
class CameraSelect:
    """`/libi/camera_select` 값 + 만료 워치독.

    latched QoS 만 믿으면 안 된다. TRANSIENT_LOCAL 은 **살아 있는 발행자**의 마지막
    샘플만 재전달한다 — 발행자가 죽으면 캐시도 사라지고, 반대로 송출기가 재시작하면
    세션이 이미 끝난 뒤에도 stale `front` 를 받아 영상이 되살아난다.
    그래서 갱신이 끊기면 스스로 `none` 으로 떨어진다.
    """
    def __init__(self, expiry_sec: float):
        self.expiry_sec = expiry_sec
        self._value = "none"
        self._stamp = None

    def set(self, value: str, stamp: float):
        self._value = value if value in ("front", "back", "none") else "none"
        self._stamp = stamp

    def current(self, now: float) -> str:
        if self._stamp is None or now - self._stamp > self.expiry_sec:
            return "none"
        return self._value
```

메인 루프는 이렇게 바뀐다(요지):

```python
    front_gen = open_picamera(rotate=180)
    back_gen = open_v4l2(args.back_index) if args.back_index is not None else None
    select = CameraSelect(expiry_sec=args.select_expiry)
    seq = 0
    while True:
        now = time.monotonic()
        f_front = next(front_gen, None)
        f_back = next(back_gen, None) if back_gen else None
        seq += 1
        # 탭은 **선택과 무관하게** 둘 다 항상 기록한다
        if f_front is not None: frame_tap.write("front", f_front, seq, now)
        if f_back is not None:  frame_tap.write("back",  f_back,  seq, now)

        sel = select.current(now)
        if sel == "none":
            continue                          # 인코딩·송출만 건너뛴다. 캡처·탭은 계속
        frame = f_front if sel == "front" else f_back
        if frame is None:
            continue
        send_udp(encode_jpeg(frame), host, port)
```

ROS 구독은 rclpy 를 **선택적으로** import 한다 — rclpy 가 없는 환경(개발 노트북)에서도 CLI 로 돌아가야 하기 때문이다:

```python
    try:
        import rclpy
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from std_msgs.msg import String
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        node.create_subscription(String, "/libi/camera_select",
                                 lambda m: select.set(m.data, time.monotonic()), qos)
    except ImportError:
        print("[camera_sender] rclpy 없음 — camera_select 구독 없이 CLI 인자대로 돈다")
        select.set(args.camera or "front", time.monotonic())
```

종료 시 `frame_tap.cleanup()` 을 부른다(`try/finally`).

- [ ] **Step 5: `image-sender.sh` 가 ROS 를 source 하게 한다**

```bash
# 마지막 exec 앞에 추가 — 지금은 rclpy 없이 떠서 camera_select 를 못 받는다.
[ -f /opt/ros/jazzy/setup.bash ] && source /opt/ros/jazzy/setup.bash
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python3 -m pytest tests/test_frame_tap.py tests/test_camera_select.py -v && python3 -m pytest tests -q`
Expected: PASS — 신규 8개 + 기존 전부

- [ ] **Step 7: 커밋**

```bash
git add follower_perception/scripts/frame_tap.py follower_perception/scripts/camera_sender.py \
        follower_perception/tests/test_frame_tap.py follower_perception/tests/test_camera_select.py \
        ../../aba_controller/libi_drive_controller/ros_ws/scripts/image-sender.sh
git commit -m "feat(camera): 단일 송출 프로세스 + camera_select 만료 워치독 + 생프레임 2슬롯 탭"
```

---

### Task 7: 세션 관리 + `camera_select` / `requester_visible` 발행

**Files:**
- Create: `aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception/session.py`
- Modify: `aba_controller/.../libi_perception/follow_node.py`
- Modify: `aba_controller/.../libi_perception/config.py`
- Test: `aba_controller/.../libi_perception/tests/test_session.py`

**Interfaces:**
- Consumes: T2 payload 필드
- Produces:
  - `Session(session_id, role, lease_sec)` — `role in ("follow", "guide", "watch")`
  - `SessionManager()` — `start(session_id, role, now) -> None`, `stop(session_id, now) -> bool`(id 불일치면 False), `touch(session_id, now)`, `expired(now) -> bool`, `camera_for() -> str`
  - `RemoteControl._target_session_id(cmd_id, args) -> str` — `stop-` 접두어를 벗기고, `args.session_id` 가 있으면 그걸 쓴다
  - `follow_node` 가 발행하는 토픽 **셋**:
    - `/libi/camera_select` (`std_msgs/String`, latched) — `front|back|none`
    - `/libi/requester_visible` (`std_msgs/Bool`)
    - `/libi/requester_area` (`std_msgs/Float32`) — **보일 때만** 발행. 거리 게이트(T10)의 입력

**배경:** 공용 `stop` 이 엉뚱한 세션을 닫는 문제가 있다 — `RemoteControl._active_id` 가 슬롯 하나뿐이라 패널의 watch 종료가 관리자 추종까지 끊을 수 있다. **`stop` 은 session id 가 일치할 때만 받는다.** 패널이 죽는 경우는 lease 만료로 닫는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_session.py
from libi_perception.session import SessionManager

def test_camera_by_role():
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0); assert m.camera_for() == "front"
    m.stop("a", now=1.0)
    m.start("b", "guide", now=1.0);  assert m.camera_for() == "back"
    m.stop("b", now=2.0)
    m.start("c", "watch", now=2.0, camera="back"); assert m.camera_for() == "back"

def test_no_session_is_none():
    assert SessionManager(lease_sec=60).camera_for() == "none"

def test_stop_requires_matching_id():
    """패널의 watch 종료가 관리자 추종을 끊으면 안 된다."""
    m = SessionManager(lease_sec=60)
    m.start("follow-1", "follow", now=0.0)
    assert m.stop("watch-9", now=1.0) is False
    assert m.camera_for() == "front"
    assert m.stop("follow-1", now=1.0) is True
    assert m.camera_for() == "none"

def test_lease_expiry_closes_session():
    """패널이 죽어 stop 이 안 와도 스스로 닫힌다."""
    m = SessionManager(lease_sec=10)
    m.start("w", "watch", now=0.0, camera="back")
    assert m.expired(now=11.0) is True
    m.sweep(now=11.0)
    assert m.camera_for() == "none"

def test_touch_extends_lease():
    m = SessionManager(lease_sec=10)
    m.start("w", "watch", now=0.0, camera="back")
    m.touch("w", now=8.0)
    assert m.expired(now=15.0) is False

def test_new_session_replaces_old():
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0)
    m.start("b", "guide", now=1.0)
    assert m.camera_for() == "back"
    assert m.stop("a", now=2.0) is False        # 이미 대체됨

# ── RemoteControl 쪽 (stop id 규약) ──────────────────────────────────────────
from libi_perception.follow_node import RemoteControl

def test_stop_prefix_is_stripped():
    """FleetCmdDriver.stop() 은 원 id 가 아니라 `stop-<원래id>` 를 보낸다
    (fleet_cmd_driver.py:67-71). 그대로 비교하면 BT 가 연 세션이 영영 안 닫힌다."""
    assert RemoteControl._target_session_id("stop-abc123", {}) == "abc123"

def test_explicit_session_id_wins():
    assert RemoteControl._target_session_id("stop-abc123", {"session_id": "panel-7"}) == "panel-7"

def test_plain_id_passes_through():
    assert RemoteControl._target_session_id("abc123", {}) == "abc123"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_perception && PYTHONPATH=. python3 -m pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: libi_perception.session`

- [ ] **Step 3: `session.py` 를 구현한다**

```python
# libi_perception/session.py
"""추종·길잡이·감시 세션의 수명. ROS 를 모른다.

## 왜 session id 가 필요한가
`/fleet_cmd` 의 `stop` 은 공용 명령이라 누가 보냈는지 알 수 없다. 패널이 등록 화면을
벗어나며 보낸 `stop` 이 관리자 추종까지 끊으면 안 된다. 그래서 id 가 일치할 때만 닫는다.

## 왜 lease 가 필요한가
패널 프로세스가 죽으면 `stop` 이 영영 안 온다. 그러면 카메라가 계속 켜져 있다.
갱신이 끊기면 스스로 닫힌다.
"""
from dataclasses import dataclass

_ROLE_CAMERA = {"follow": "front", "guide": "back"}


@dataclass
class Session:
    session_id: str
    role: str
    camera: str
    touched_at: float


class SessionManager:
    def __init__(self, lease_sec: float):
        self.lease_sec = lease_sec
        self._cur: Session | None = None

    def start(self, session_id: str, role: str, now: float, camera: str | None = None):
        cam = camera or _ROLE_CAMERA.get(role, "none")
        self._cur = Session(session_id, role, cam, now)

    def stop(self, session_id: str, now: float) -> bool:
        if self._cur is None or self._cur.session_id != session_id:
            return False
        self._cur = None
        return True

    def touch(self, session_id: str, now: float) -> bool:
        if self._cur is None or self._cur.session_id != session_id:
            return False
        self._cur.touched_at = now
        return True

    def expired(self, now: float) -> bool:
        return self._cur is not None and (now - self._cur.touched_at) > self.lease_sec

    def sweep(self, now: float):
        if self.expired(now):
            self._cur = None

    @property
    def role(self):
        return self._cur.role if self._cur else None

    def camera_for(self) -> str:
        return self._cur.camera if self._cur else "none"

    def override_camera(self, camera: str):
        """회복 BT 가 탐색 중 반대 캠을 요청할 때. 역할은 그대로 둔다."""
        if self._cur:
            self._cur.camera = camera

    def restore_camera(self):
        if self._cur:
            self._cur.camera = _ROLE_CAMERA.get(self._cur.role, self._cur.camera)
```

- [ ] **Step 4: `follow_node.py` 를 배선한다**

`RemoteControl` 을 확장한다:

```python
    START_ACTIONS = ("follow_admin", "guide_watch", "watch")
    STOP_ACTIONS = ("stop", "follow_stop")

    _ROLE_OF = {"follow_admin": "follow", "guide_watch": "guide", "watch": "watch"}

    def _on_cmd(self, msg):
        cmd = json.loads(msg.data)
        action = str(cmd.get("action", "")).strip()
        cmd_id = cmd.get("id")
        args = cmd.get("args") or {}
        now = time.monotonic()
        if action in self.START_ACTIONS:
            self._sessions.start(cmd_id, self._ROLE_OF[action], now,
                                 camera=args.get("camera"))
            if action == "follow_admin":
                self._session.start()          # 주행하는 세션만 제어 루프를 켠다
        elif action in self.STOP_ACTIONS:
            if self._sessions.stop(self._target_session_id(cmd_id, args), now):
                self._session.stop()

    @staticmethod
    def _target_session_id(cmd_id, args):
        """어느 세션을 닫으라는 것인가.

        ⚠️ **원 id 를 그대로 비교하면 BT 가 연 세션이 영영 안 닫힌다.**
        `FleetCmdDriver.stop()` 은 원 id 가 아니라 `stop-<원래id>` 를 보낸다
        (`fleet_cmd_driver.py:67-71`). 그래서 접두어를 벗겨서 비교한다.
        패널처럼 명시적으로 지정하는 쪽은 `args.session_id` 를 쓴다(그게 이긴다).
        """
        explicit = args.get("session_id")
        if explicit:
            return explicit
        s = str(cmd_id or "")
        return s[5:] if s.startswith("stop-") else s
```

새 발행자 두 개:

```python
        self._cam_pub = node.create_publisher(String, "/libi/camera_select", qos_latched)
        self._vis_pub = node.create_publisher(Bool, "/libi/requester_visible", 10)
        # 거리 게이트(스토리 27)의 입력. Bool 만으로는 "보이지만 멀다"를 표현할 수 없어
        # 거리 게이트가 구현할 값 자체를 못 받는다. 커스텀 msg 를 새로 만들지 않는 이유:
        # libi_interfaces 에 msg 를 더하면 colcon 재빌드가 따라붙고, 실을 값이 float 하나다.
        self._area_pub = node.create_publisher(Float32, "/libi/requester_area", 10)

    def tick(self):
        now = time.monotonic()
        self._sessions.sweep(now)                       # lease 만료 정리
        # 카메라 선택은 **갱신 주기마다 다시 낸다.** 한 번만 내면 송출기의 만료
        # 워치독이 스스로 none 으로 떨어뜨려 영상이 끊긴다.
        self._cam_pub.publish(String(data=self._sessions.camera_for()))
        if self._sessions.role in ("guide", "watch"):
            det = self._get_detection()
            visible = det is not None and det.is_owner and det.motion_ok
            self._vis_pub.publish(Bool(data=bool(visible)))
            # 안 보이면 면적을 내지 않는다. 0 을 내면 소비자가 "아주 멀다"로 읽어
            # 소실과 원거리가 구별되지 않는다.
            if visible:
                self._area_pub.publish(Float32(data=float(det.area)))
        ...
```

```python
# config.py 에 추가
SESSION_LEASE_SEC = 60.0        # 이 시간 갱신이 없으면 세션을 닫는다(패널 사망 대비)
CAMERA_SELECT_HZ = 2.0          # camera_select 재발행 주기. 송출기 만료보다 촘촘해야 한다
CAMERA_SELECT_EXPIRY = 3.0      # 송출기 쪽 만료 시간. CAMERA_SELECT_HZ 의 몇 배로 둔다
```

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest tests/ -q`
Expected: PASS — 기존 49개 + 신규 6개

- [ ] **Step 6: 커밋**

```bash
git add libi_perception/session.py libi_perception/follow_node.py libi_perception/config.py tests/test_session.py
git commit -m "feat(perception): 세션 id·lease 관리와 camera_select·requester_visible 발행"
```

---

### Task 8: `motion_ok` 게이트 + 가시성 신선도 TTL

**Files:**
- Modify: `aba_controller/.../libi_perception/control_loop.py`
- Modify: `aba_controller/.../libi_modes/libi_modes/ros/providers.py`
- Test: `aba_controller/.../libi_perception/tests/test_control_loop.py`, `.../libi_modes/test/test_providers_touch.py`

**Interfaces:**
- Consumes: `Detection.motion_ok`(T2), `/libi/requester_visible`(T7)
- Produces: `RosProviders` 가 `requester_visible` 를 **신선도 만료 시 `False`** 로 내린다

**배경(중요 — codex 가 찾은 설계 구멍):** `providers.py` 는 메시지가 올 때만 값을 갱신한다. AI 서버나 `follow_node` 가 죽어 발행이 끊기면 마지막 `True` 가 **영원히 남아** 로봇이 "요청자가 계속 보인다"고 믿고 nav2 를 계속 몬다. 그리고 **stale 을 `None` 으로 내리면 안 된다** — `None` 은 "감시 없음 → 그냥 주행"이라 정확히 반대로 간다. **`False`(정지)로 내려야 한다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# libi_perception/tests/test_control_loop.py 에 추가
def test_motion_not_ok_publishes_zero_but_stays_tracking():
    """보이는데 가면 안 되는 상태 — 놓친 게 아니므로 SEARCHING 으로 가지 않는다."""
    published = []
    det = make_detection(motion_ok=False)
    loop = ControlLoop(lambda: det, lambda: [], lambda l, a: published.append((l, a)),
                       cfg=config, now=fake_clock())
    loop.tick()
    assert published[-1] == (0.0, 0.0)
    assert loop.state == "TRACKING"

def test_motion_not_ok_does_not_increment_miss():
    det = make_detection(motion_ok=False)
    loop = ControlLoop(lambda: det, lambda: [], lambda l, a: None, cfg=config, now=fake_clock())
    for _ in range(config.N_MISS_FRAMES + 5):
        loop.tick()
    assert loop.state == "TRACKING"          # 정지일 뿐 소실이 아니다

# libi_modes/test/test_providers_touch.py 에 추가
#
# ⚠️ 실제 API 다 — 추측하지 말 것 (providers.py:49-58, 207-220 에서 확인)
#   · 조회 API 는 snapshot() 이 아니라 **as_dict()** 이고, 값이 아니라 **람다**를 담는다
#   · 생성자에 now/ttl 인자가 지금은 **없다** — 이 Task 에서 추가한다
class _Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t

def _providers(clock):
    return RosProviders(node=FakeNode(), now_fn=clock, requester_ttl_sec=2.0)

def test_requester_visible_goes_false_when_stale():
    """발행이 끊기면 마지막 True 가 남으면 안 된다 — 로봇이 영원히 몰고 간다."""
    clock = _Clock(); p = _providers(clock)
    clock.t = 0.0; p._on_requester(Bool(data=True))
    clock.t = 0.5;  assert p.as_dict()["requester_visible"]() is True
    clock.t = 10.0; assert p.as_dict()["requester_visible"]() is False

def test_requester_visible_none_when_never_published():
    """한 번도 안 왔으면 None — '감시 없음'이라 길잡이는 그냥 주행한다(기존 계약)."""
    clock = _Clock()
    assert _providers(clock).as_dict()["requester_visible"]() is None

def test_requester_area_expires_to_none():
    """면적도 같은 TTL 을 탄다. stale 한 면적으로 거리 판정을 하면 안 된다."""
    clock = _Clock(); p = _providers(clock)
    clock.t = 0.0; p._on_requester_area(Float32(data=900.0))
    clock.t = 0.5;  assert p.as_dict()["requester_area"]() == 900.0
    clock.t = 10.0; assert p.as_dict()["requester_area"]() is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: 두 워크스페이스의 해당 테스트
Expected: FAIL — `motion_ok` 미반영, stale 시 `True` 유지

- [ ] **Step 3: `control_loop.py` 를 고친다**

```python
    def tick(self):
        if self.switch.state == 'TRACKING':
            det = self.get_detection()
            if det is not None:
                self.miss = 0
                if not getattr(det, 'motion_ok', True):
                    # 보이지만 가면 안 된다(누워 있음 / 코앞 / 자세 미측정).
                    # 놓친 게 아니므로 miss 를 올리지 않는다 — 올리면 멀쩡히 보이는
                    # 대상을 두고 탐색을 시작한다.
                    self.publish(0.0, 0.0)
                else:
                    self.tracker.step(det, self.get_scan(), self._dt())
            else:
                ...
```

- [ ] **Step 4: `providers.py` 에 TTL 을 넣는다**

```python
# __init__
        self._requester_visible = None      # None = 감시가 안 돌고 있다
        self._requester_stamp = None
        self._requester_ttl = requester_ttl_sec

# _on_requester
    def _on_requester(self, msg):
        self._requester_visible = bool(msg.data)
        self._requester_stamp = self._now()
        if self._requester_visible:
            self._requester_seen_at = self._requester_stamp

# snapshot 의 람다
        "requester_visible": self._requester_visible_fresh,

    def _requester_visible_fresh(self):
        """신선하지 않으면 **False**(정지)다. None(감시 없음)이 아니다 —
        None 으로 내리면 GuideExec 이 '감시 없음 → 그냥 주행'으로 읽어 정반대가 된다."""
        if self._requester_stamp is None:
            return None                     # 한 번도 안 왔다 — 감시 자체가 없다
        if self._now() - self._requester_stamp > self._requester_ttl:
            return False
        return self._requester_visible
```

`params.yaml` 에 `working.requester_ttl_sec: 2.0` 을 추가한다.

- [ ] **Step 5: 통과를 확인한다**

Run: 두 워크스페이스 전체 테스트
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git commit -am "fix(follow/guide): motion_ok 정지 게이트와 requester_visible 신선도 TTL"
```

---

### Task 9: 회복 BT 카메라 전환

**Files:**
- Modify: `aba_controller/.../libi_perception/recovery_bt.py`
- Modify: `aba_controller/.../libi_perception/search_planner.py`
- Modify: `aba_controller/.../libi_perception/config.py`
- Test: `aba_controller/.../libi_perception/tests/test_recovery_bt.py`

**Interfaces:**
- Consumes: `SearchContext` 에 `select_camera(name)`, `home_camera`(str), `peek_camera`(str), `role`(str) 추가
- Produces: 트리 `Hold → PeekBack → Scan1 → PeekBack2 → Scan2 → Turn180 → Scan3 → GiveUp`, 그리고 `AlignHeading`(추종 전용)

**배경:**
- 뒤를 보려고 9초를 돌리던 것을 **카메라 전환(공짜)** 으로 바꾼다
- `PeekBack` 은 **사람 검출만** 본다(재식별 게이트 통과 요구 없음). 앞뒤 카메라는 렌즈·노출이 달라 크로스카메라 재식별을 못 믿는다. 신원 확인은 회전 후 정위치 카메라에서 한다
- **사람이 정확히 한 명일 때만** 반응한다
- **`AlignHeading` 은 추종 전용이다.** 길잡이에서 180° 돌면, 목적지 방향이 사람 방향과 겹칠 때 회전 → 경로 재계획 → 다시 앞캠 포착 → 재회전으로 **무한 진동**한다. 길잡이는 **보는 카메라만 바꾸고 계속 간다**
- `Turn180`+`Scan3` 은 앞뒤 화각으로 안 덮이는 **사각을 위한 보루**다. 화각 실측 후 사각이 없으면 제거한다
- **의도적 동작 변경이다.** 기존 테스트가 참조 함수와의 완전 일치를 검사하므로 참조 함수도 같이 갱신한다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_recovery_bt.py — 기존 동등성 테스트를 새 타임라인으로 갱신하고 아래를 추가
def test_camera_sequence_over_timeline(ctx_recorder):
    """구간마다 요청한 카메라가 계획대로 바뀐다."""
    ctx = ctx_recorder(role="follow")           # home=front
    tree = create_searching_tree(ctx)
    seen = []
    for t in frange(0.0, 26.0, 0.05):
        ctx.clock = t
        tick_tree(tree)
        seen.append((round(t, 2), ctx.camera))
    assert cam_at(seen, 5.0) == "front"         # Hold
    assert cam_at(seen, 11.0) == "back"         # PeekBack
    assert cam_at(seen, 14.0) == "front"        # Scan1
    assert cam_at(seen, 17.0) == "back"         # PeekBack2
    assert cam_at(seen, 20.0) == "front"        # Scan2

def test_peek_ignores_multiple_people(ctx_recorder):
    ctx = ctx_recorder(role="follow", peek_people=2)
    tree = create_searching_tree(ctx)
    run_until(tree, ctx, 12.0)
    assert ctx.align_latched is False            # 여럿이면 반응하지 않는다

def test_peek_single_person_latches_align_for_follow(ctx_recorder):
    ctx = ctx_recorder(role="follow", peek_people=1)
    tree = create_searching_tree(ctx)
    run_until(tree, ctx, 12.0)
    assert ctx.align_latched is True

def test_guide_never_rotates_on_peek(ctx_recorder):
    """길잡이는 회전하지 않는다 — 보는 캠만 바꾸고 탐색을 끝낸다."""
    ctx = ctx_recorder(role="guide", peek_people=1)   # home=back, peek=front
    tree = create_searching_tree(ctx)
    status = run_until(tree, ctx, 12.0)
    assert ctx.align_latched is False
    assert ctx.camera == "front"                      # 찾은 쪽으로 고정
    assert status == Status.SUCCESS

def test_guide_peek_does_not_fall_through_to_next_phase(ctx_recorder):
    """회귀 방지 — PeekPhase 가 SUCCESS 를 돌려주면 memory=True Sequence 가
    다음 phase(Scan1)로 넘어가 카메라가 정위치로 되돌아가고 결국 GiveUp 까지 간다.
    래치 + RUNNING 이라야 상위 Selector 의 PeekReacquired 가 이긴다."""
    ctx = ctx_recorder(role="guide", peek_people=1)
    tree = create_searching_tree(ctx)
    run_until(tree, ctx, 12.0)
    for _ in range(40):                               # 2초 더 굴려도
        tick_tree(tree); ctx.clock += 0.05
    assert ctx.camera == "front"                      # 정위치(back)로 안 돌아간다
    assert tree.status == Status.SUCCESS              # GiveUp 으로 안 떨어진다

def test_follow_peek_latches_without_advancing_sequence(ctx_recorder):
    """추종도 같다 — Peek 은 래치만 세우고, 회전은 AlignHeading 이 한다."""
    ctx = ctx_recorder(role="follow", peek_people=1)
    tree = create_searching_tree(ctx)
    run_until(tree, ctx, 12.0)
    assert ctx.align_latched is True
    assert ctx.camera == "back"                       # 아직 Scan1 로 안 넘어갔다

def test_align_survives_losing_sight_mid_turn(ctx_recorder):
    """회전 중 시야가 바뀌어 재획득이 떨어져도 탐색으로 되돌아가지 않는다."""
    ctx = ctx_recorder(role="follow", peek_people=1)
    tree = create_searching_tree(ctx)
    run_until(tree, ctx, 12.0)
    ctx.detection = None                               # 회전 중 놓침
    run_for(tree, ctx, 2.0)
    assert ctx.align_latched is True                   # 여전히 정렬 중

def test_turn180_and_scan3_run_after_scan2(ctx_recorder):
    ctx = ctx_recorder(role="follow", peek_people=0)
    tree = create_searching_tree(ctx)
    seen = record_angular(tree, ctx, 0.0, 36.0)
    assert any(abs(a) > 0 for a in seen[int(22.5 / 0.05):int(30 / 0.05)])   # Turn180 구간
    assert final_status(tree) == Status.FAILURE                              # GiveUp
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest tests/test_recovery_bt.py -v`
Expected: FAIL — 새 노드·컨텍스트 필드 없음. 기존 동등성 테스트도 FAIL(의도한 변경)

- [ ] **Step 3: `recovery_bt.py` 를 고친다**

```python
class SearchContext:
    def __init__(self, get_detection, publish, cfg, now, lkd=1.0,
                 select_camera=None, role="follow", peek_people=None):
        ...
        #: 카메라를 바꿔 달라고 요청한다. None 이면(단독 시험) 아무것도 안 한다.
        self.select_camera = select_camera or (lambda name: None)
        self.role = role
        #: 반대 캠에 몇 명 보이나. 신원은 안 본다 — 크로스카메라 재식별을 못 믿기 때문이다.
        self.peek_people = peek_people or (lambda: 0)
        self.align_latched = False

    @property
    def home_camera(self):
        return "front" if self.role == "follow" else "back"

    @property
    def peek_camera(self):
        return "back" if self.role == "follow" else "front"


class CameraPhase(SearchPhase):
    """구간에 들어갈 때 카메라를 요청하는 SearchPhase."""
    def __init__(self, ctx, name, begin, end, angular_fn, camera):
        super().__init__(ctx, name, begin, end, angular_fn)
        self.camera = camera

    def update(self):
        self.ctx.select_camera(self.camera)
        return super().update()


class PeekPhase(CameraPhase):
    """반대 캠으로 정지 관찰. 사람이 **정확히 한 명**이면 래치만 세운다.

    여럿이면 대상을 특정할 수 없어 반응하지 않는다 — 서가 사이 다른 이용자에게
    반응해 헛돌지 않기 위해서다.

    ⚠️ **여기서 SUCCESS 를 돌려주면 안 된다.** 이 노드는 `SearchPhases`
    (`Sequence(memory=True)`) 안에 있어서, SUCCESS 는 "재획득했다"가 아니라
    **"다음 phase 로 넘어가라"** 는 뜻이다. 다음 tick 에 `Scan1` 이 돌아 카메라가
    정위치로 되돌아가고, 결국 `GiveUp` 까지 간다.
    그래서 래치만 세우고 RUNNING 을 돌려준다 — 상위 Selector 가 `memory=False` 라
    다음 tick 에 위쪽 노드(`AlignHeading` / `PeekReacquired`)가 먼저 평가되어 이긴다.
    """
    def update(self):
        self.ctx.select_camera(self.camera)
        if self.ctx.peek_people() == 1:
            if self.ctx.role == "follow":
                self.ctx.align_latched = True      # 몸을 돌려야 제어가 된다
            else:
                # 길잡이는 회전하지 않는다(목적지 방향과 사람 방향이 겹치면 무한 진동).
                # 보는 캠을 이쪽으로 고정한 채 탐색을 끝낸다.
                self.ctx.peek_reacquired = True
                self.ctx.peek_camera_locked = self.camera
            return Status.RUNNING
        return super().update()


class PeekReacquired(py_trees.behaviour.Behaviour):
    """길잡이가 반대 캠에서 찾았을 때 **탐색 자체를 끝낸다.**

    `SearchPhases` 위(=우선순위 높음)에 두는 이유는 위 `PeekPhase` 주석과 같다.
    성공시키는 순간 카메라는 찾은 쪽으로 고정된 채 남는다 — 그 사람을 계속 보려면
    그래야 하고, 길잡이는 주행을 nav2 가 하므로 캠이 뒤든 앞이든 상관없다.
    """
    def __init__(self, ctx):
        super().__init__(name='PeekReacquired')
        self.ctx = ctx

    def update(self):
        if not getattr(self.ctx, 'peek_reacquired', False):
            return Status.FAILURE
        self.ctx.select_camera(self.ctx.peek_camera_locked)
        self.ctx.publish(0.0, 0.0)      # 탐색 회전을 확실히 멈춘다
        return Status.SUCCESS


class AlignHeading(py_trees.behaviour.Behaviour):
    """반대 캠에서 찾았을 때 180° 돌아 대상을 정위치 캠으로 옮긴다. **추종 전용.**

    Selector 의 맨 앞에 둔다. 래치되면 매 tick 이긴다 — 회전 중에는 시야가 바뀌어
    재획득 판정이 떨어지므로, 우선순위가 낮으면 탐색 시퀀스로 되돌아가 버린다.
    """
    def __init__(self, ctx):
        super().__init__(name='AlignHeading')
        self.ctx = ctx
        self._t0 = None

    def update(self):
        if not self.ctx.align_latched:
            return Status.FAILURE
        if self._t0 is None:
            self._t0 = self.ctx.now()
        turn_sec = self.ctx.cfg.SEARCH_TURN_ANGLE / self.ctx.cfg.ANGULAR_Z_SEARCH
        if self.ctx.now() - self._t0 >= turn_sec:
            self.ctx.align_latched = False
            self._t0 = None
            self.ctx.select_camera(self.ctx.home_camera)
            return Status.SUCCESS
        self.ctx.publish(0.0, self.ctx.cfg.ANGULAR_Z_SEARCH)
        return Status.RUNNING


def create_searching_tree(ctx):
    cfg = ctx.cfg
    turn_sec = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH
    home, peek = ctx.home_camera, ctx.peek_camera
    spec = [
        ('Hold',      cfg.SEARCH_HOLD_SEC, lambda: 0.0,                              home),
        ('PeekBack',  cfg.SEARCH_PEEK_SEC, lambda: 0.0,                              peek),
        ('Scan1',     cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * ctx.lkd,   home),
        ('PeekBack2', cfg.SEARCH_PEEK_SEC, lambda: 0.0,                              peek),
        ('Scan2',     cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * -ctx.lkd,  home),
        ('Turn180',   turn_sec,            lambda: cfg.ANGULAR_Z_SEARCH,             home),
        ('Scan3',     cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * ctx.lkd,   home),
    ]
    phases, offset = [], 0.0
    for name, duration, angular_fn, cam in spec:
        klass = PeekPhase if name.startswith('Peek') else CameraPhase
        phases.append(klass(ctx, name, offset, offset + duration, angular_fn, cam))
        offset += duration

    body = py_trees.composites.Sequence(
        name='SearchPhases', memory=True, children=phases + [GiveUp(ctx)])
    # 우선순위 순서가 곧 규칙이다. memory=False 라 매 tick 위에서부터 다시 본다.
    #   AlignHeading    래치되면 회전이 끝날 때까지 무조건 이긴다(회전 중 시야가 바뀌어
    #                   재획득이 떨어져도 탐색으로 안 돌아간다) — 추종 전용
    #   PeekReacquired  길잡이가 반대 캠에서 찾았다 → 탐색 종료 — 길잡이 전용
    #   CheckReacquired 정위치 캠에서 다시 보인다 → 탐색 종료
    #   SearchPhases    위 셋 다 아니면 탐색을 이어간다
    return py_trees.composites.Selector(
        name='BT_Searching', memory=False,
        children=[AlignHeading(ctx), PeekReacquired(ctx), CheckReacquired(ctx), body])
```

```python
# config.py 에 추가
SEARCH_PEEK_SEC = 2.0     # 반대 캠 정지 관찰 구간. 장치 개폐 폴백이면 4.0 으로 올린다.
```

- [ ] **Step 4: `search_planner.py` 참조 타임라인을 갱신한다**

`search_command(elapsed, lkd)` 의 시간 경계를 새 spec 과 동일하게 고친다. 이 함수는 트리 동작의 **참조 구현**이라 어긋나면 동등성 테스트가 거짓말을 한다. 파일 머리에 근거 주석을 남긴다:

```python
# [2026-07-27] 타임라인 변경 — Turn180(9초 회전)으로 뒤를 보던 것을 카메라 전환
# (PeekBack 2초 × 2)으로 대체하고, Turn180+Scan3 은 앞뒤 화각으로 안 덮이는 사각의
# 보루로 뒤로 옮겼다. libi_perception/README.md 의 "의도적 동작 변경" 절차를 따랐다.
```

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest tests/ -q`
Expected: PASS 전부

- [ ] **Step 6: 커밋**

```bash
git commit -am "feat(recovery): 카메라 전환 탐색 + 추종 전용 AlignHeading + 사각 보루"
```

---

### Task 10: `GuideExec` — 감시 세션·거리 게이트·갈림길 확인

**Files:**
- Modify: `aba_controller/.../libi_modes/common/working_actions.py`
- Modify: `aba_controller/.../libi_modes/ros/providers.py`
- Modify: `aba_controller/.../libi_modes/config/params.yaml`
- Test: `aba_controller/.../libi_modes/test/test_guide_exec.py`

**Interfaces:**
- Consumes: `/libi/requester_visible`(T7·T8), `Detection.area` 계열 값을 provider 가 `requester_area` 로 노출
- Produces: `GuideExec(..., watch_driver, far_area_min, junction_vertices, junction_hold_sec)`

**배경:** 지금 `/libi/requester_visible` 발행자가 없어 값이 `None` 이고, `GuideExec` 은 그걸 "감시 없음"으로 읽어 **사람을 놓쳐도 계속 간다**. 감시 세션을 켜는 쪽이 필요하다. 그리고 "보이나"만으로는 부족하다 — 10m 뒤에 있어도 보이면 계속 간다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# test/test_guide_exec.py 에 추가 (기존 7개는 그대로 통과해야 한다)
def test_starts_watch_session_on_first_tick(guide_env):
    env = guide_env()
    env.tick()
    assert env.watch_driver.started is True

def test_stops_watch_session_on_release(guide_env):
    env = guide_env(arrived=True)
    env.tick()
    assert env.watch_driver.stopped is True

def test_far_requester_halts(guide_env):
    """보이지만 너무 멀면 멈춘다 — '보이나'만 보면 10m 뒤에 있어도 계속 간다."""
    env = guide_env(visible=True, requester_area=100.0, far_area_min=500.0)
    env.tick()
    assert env.stop_driver.started is True
    assert env.status == Status.RUNNING

def test_near_requester_drives(guide_env):
    env = guide_env(visible=True, requester_area=900.0, far_area_min=500.0)
    env.tick()
    assert env.stop_driver.started is False

def test_junction_holds_briefly(guide_env):
    """갈림길에서만 짧게 선다. 모든 노드에서 서면 0.3m마다 멈춘다."""
    env = guide_env(visible=True, at_vertex="분류함", junction_vertices={"분류함"},
                    junction_hold_sec=1.0)
    env.tick()
    assert env.stop_driver.started is True
    env.advance(1.5)
    env.tick()
    assert env.driver.started is True          # 유지 시간이 지나면 다시 간다

def test_non_junction_does_not_hold(guide_env):
    env = guide_env(visible=True, at_vertex="순회경로-3", junction_vertices={"분류함"})
    env.tick()
    assert env.stop_driver.started is False

def test_requester_area_unknown_does_not_halt(guide_env):
    """면적을 모르면(옛 payload) 거리 게이트를 걸지 않는다."""
    env = guide_env(visible=True, requester_area=None, far_area_min=500.0)
    env.tick()
    assert env.stop_driver.started is False
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_controller/libi_modes/ros_ws/src/libi_modes && PYTHONPATH=. python3 -m pytest test/test_guide_exec.py -v`
Expected: FAIL — 신규 인자·동작 없음

- [ ] **Step 3: `GuideExec` 을 확장한다**

```python
    def __init__(self, driver, arrive_tolerance, arrive_resend_sec, arrive_timeout_sec,
                 lost_grace_sec, lost_timeout_sec, stop_driver=None, watch_driver=None,
                 far_area_min=0.0, junction_vertices=frozenset(), junction_hold_sec=0.0,
                 name=None, now_fn=time.monotonic):
        ...
        #: 뒷캠 감시 세션을 켜고 끈다. 없으면 감시가 안 돌아 사람을 놓쳐도 계속 간다.
        self.watch_driver = watch_driver
        self.far_area_min = far_area_min
        self.junction_vertices = junction_vertices
        self.junction_hold_sec = junction_hold_sec
        self._watching = False
        self._junction_until = None

    def update(self):
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            self._release_watch()
            self._halted = False
            return super().update()

        if not self._watching and self.watch_driver is not None:
            self.watch_driver.start()
            self._watching = True

        lost = self._lost_for()
        if lost >= self.lost_timeout_sec:
            self._halt(); return self._give_up()
        if lost >= self.lost_grace_sec:
            self._halt(); return Status.RUNNING

        # 보이지만 멀다 — 멈추고 기다린다.
        area = bb.get(self.blackboard, Keys.REQUESTER_AREA)
        if area is not None and self.far_area_min > 0 and area < self.far_area_min:
            self._halt(); return Status.RUNNING

        # 갈림길에서만 짧게 확인한다.
        if self._junction_hold_active():
            self._halt(); return Status.RUNNING

        if self._halted:
            self._halted = False
            self._sent_at = None
        return super().update()

    def _junction_hold_active(self) -> bool:
        if self.junction_hold_sec <= 0:
            return False
        v = bb.get(self.blackboard, Keys.AT_VERTEX)
        now = self._now()
        if v in self.junction_vertices and self._junction_until is None:
            self._junction_until = now + self.junction_hold_sec
        if self._junction_until is None:
            return False
        if now >= self._junction_until:
            self._junction_until = None
            return False
        return True

    def _release_watch(self):
        if self._watching and self.watch_driver is not None:
            self.watch_driver.stop()
        self._watching = False

    def _release(self, status):
        self._release_watch()
        return super()._release(status)
```

`blackboard.py` 에 `REQUESTER_AREA = "requester_area"`, `AT_VERTEX = "at_vertex"` 를 추가하고 `topics2bb` 매핑과 `providers` 에 노출한다. 갈림길 정점 집합은 provider 가 navgraph 를 읽어 **레인이 3개 이상 붙은 정점**으로 만든다.

```yaml
# params.yaml — working 아래
    # 요청자가 이보다 작게 보이면(멀면) 멈춘다. 0 이면 거리 게이트 끔.
    # arte2 기준 실측 후 조정한다.
    guide_far_area_min: 0
    # 갈림길에서 잠깐 서서 확인하는 시간(초). 0 이면 끔.
    # **모든 노드가 아니라 갈림길만** — arte2 레인이 0.151~0.601m라 모든 노드에서 서면
    # 0.3m마다 멈춘다.
    guide_junction_hold_sec: 1.0
    # requester_visible 신선도. 이보다 오래 갱신이 없으면 False(정지)로 본다.
    requester_ttl_sec: 2.0
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest test/ -q`
Expected: PASS — 기존 91개 + 신규 7개

- [ ] **Step 5: 커밋**

```bash
git commit -am "feat(guide): 감시 세션 기동·거리 게이트·갈림길 확인"
```

---

### Task 11: 복귀 5단계 + 실패 흡수 재시도

**Files:**
- Create: `aba_controller/.../libi_modes/common/return_steps.py`
- Modify: `aba_controller/.../libi_modes/branches/returning.py`
- Modify: `aba_controller/.../libi_modes/registry.py`
- Modify: `aba_controller/.../libi_modes/main.py` (입구 좌표 파라미터 · `return_entrance`/`return_rotate` 드라이버 · 팔 홈복귀 제거)
- Modify: `aba_controller/.../libi_modes/ros/providers.py` (`robot_pose` 에 `yaw` 추가)
- Modify: `aba_controller/.../libi_modes/ros/fleet_cmd_driver.py` (`YawGoalDriver` 추가)
- Test: `aba_controller/.../libi_modes/test/test_return_navigation.py`

**Interfaces:**
- Consumes: `robot_pose = {"x","y","yaw"}` — **이 Task 에서 `yaw` 를 추가한다**(지금은 x·y 뿐)
- Produces:
  - leaf: `GoToParkingEntrance`, `FaceParking`, `GoToParking`, `TurnAround`, `AlignDock`
  - 데코레이터: `AbsorbFailure(child, retry_max)`
  - 드라이버 키: `return_entrance`(goal), `return_dock`(기존), `return_rotate`(`YawGoalDriver`)
  - `returning.create(params, *, entrance_driver, dock_driver, rotate_driver)` — **시그니처 변경**(기존 `create(params, arm_driver, dock_driver)`)

> ⚠️ **회전에 새 `/cmd_vel` 발행자를 만들지 않는다.** 같은 좌표에 목표 yaw 만 바꾼 `goal` 을
> 보내면 nav2 가 제자리 회전으로 처리한다. 이 시스템은 `/cmd_vel` 중재자가 없어서
> 발행자를 늘리는 것 자체가 위험이다(PRD "알려진 위험").

**배경(중요 — codex 가 찾은 결함):** 기존 `ReturnNavigation` 은 `Parallel` 안에서 **FAILURE 대신 RUNNING + fault** 를 유지하도록 일부러 짜여 있다. py_trees `Parallel` 은 정책과 무관하게 자식 하나가 FAILURE 면 즉시 실패하므로, FAILURE 를 내면 형제 `FaultDetected` 가 SUCCESS 를 낼 tick 조차 없이 브랜치가 죽는다. **5단계로 쪼개면 그 보호가 사라진다** — 그래서 각 단계 실패를 흡수하는 래퍼가 필요하다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# test/test_return_navigation.py 에 추가
def test_steps_run_in_order(return_env):
    env = return_env()
    names = env.run_to_completion()
    assert names == ["GoToParkingEntrance", "FaceParking", "GoToParking",
                     "TurnAround", "AlignDock"]

def test_step_failure_never_propagates_failure(return_env):
    """어느 단계가 실패해도 Parallel 을 죽이면 안 된다."""
    env = return_env(fail_at="GoToParking")
    status = env.tick()
    assert status != Status.FAILURE

def test_retry_then_fault_on_exhaustion(return_env):
    env = return_env(fail_at="GoToParking", retry_max=3)
    for _ in range(4):
        env.tick()
    assert env.blackboard.fault is True
    assert env.last_status == Status.RUNNING     # FAILURE 가 아니다

def test_fault_reaches_error_through_watchdog(return_env):
    """fault 를 세운 같은 tick 에 FaultDetected 가 ERROR 를 예약해야 한다."""
    env = return_env(fail_at="GoToParking", retry_max=1, whole_branch=True)
    env.tick(); env.tick()
    assert env.blackboard.next_mode == "ERROR"

def test_camera_is_none_during_return(return_env):
    env = return_env()
    env.tick()
    assert env.camera_requests == ["none"]

def test_success_sets_charging(return_env):
    env = return_env()
    env.run_to_completion()
    assert env.blackboard.next_mode == "CHARGING"

def test_face_and_align_are_separate_leaves(return_env):
    """ArUco 로직만 나중에 갈아끼우도록 자리를 분리해 둔다."""
    env = return_env()
    assert env.leaf("FaceParking") is not env.leaf("AlignDock")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest test/test_return_navigation.py -v`
Expected: FAIL — `return_steps` 모듈 없음

- [ ] **Step 3: `return_steps.py` 를 만든다**

```python
# libi_modes/common/return_steps.py
"""복귀 5단계와, 그 실패를 흡수하는 래퍼.

## 왜 흡수 래퍼가 필요한가
이 시퀀스는 `Parallel(SuccessOnOne)` 안에 들어간다. py_trees 의 Parallel 은 **정책과
무관하게 자식 하나가 FAILURE 를 내면 즉시 실패한다.** 그러면 형제인 `FaultDetected` 가
SUCCESS 를 낼 tick 조차 없이 브랜치가 죽어 ERROR 전이가 영영 일어나지 않는다.
기존 `ReturnNavigation` 이 "FAILURE 대신 RUNNING + fault" 를 유지한 이유가 그것이다.
5단계로 쪼개면서 그 보호를 잃지 않도록, 각 단계를 이 래퍼로 감싼다.
"""
import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class AbsorbFailure(py_trees.decorators.Decorator):
    """자식의 FAILURE 를 RUNNING 으로 바꾸고 재시도한다. 재시도를 다 쓰면 fault 를
    세우고 그래도 **RUNNING** 을 돌려준다 — FAILURE 는 Parallel 을 죽인다."""

    def __init__(self, child, retry_max: int, name: str | None = None):
        super().__init__(name=name or f"Absorb[{child.name}]", child=child)
        self.retry_max = retry_max
        self._tries = 0

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.WRITE)

    def update(self):
        status = self.decorated.status
        if status != Status.FAILURE:
            return status
        self._tries += 1
        if self._tries >= self.retry_max:
            self.blackboard.set(Keys.FAULT, True)
        return Status.RUNNING


class _NavStep(py_trees.behaviour.Behaviour):
    """좌표로 몰고, 실좌표 거리로 도착을 판정한다.

    명령 수락 응답(ack)을 도착으로 쓰지 않는다 — `send_nav_goal()` 은 완료를 기다리지
    않으므로 ack 는 "주문을 받았다"이지 "도착했다"가 아니다.
    """
    def __init__(self, name, driver, target_key, tolerance, timeout_sec, now_fn):
        super().__init__(name=name)
        self.driver, self.target_key = driver, target_key
        self.tolerance, self.timeout_sec, self._now = tolerance, timeout_sec, now_fn
        self._sent_at = None

    def update(self):
        target = bb.get(self.blackboard, self.target_key)
        if target is None:
            return Status.FAILURE
        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        if pose and _dist(pose, target) <= self.tolerance:
            self._sent_at = None
            return Status.SUCCESS
        if self._sent_at is None:
            self.driver.start(); self._sent_at = self._now()
        elif self._now() - self._sent_at >= self.timeout_sec:
            self._sent_at = None
            return Status.FAILURE
        return Status.RUNNING


class _YawStep(py_trees.behaviour.Behaviour):
    """제자리 yaw 회전. **새 `/cmd_vel` 발행자를 만들지 않는다.**

    같은 x·y 에 목표 yaw 만 다른 `goal` 을 보내면 nav2(RegulatedPurePursuit)가 제자리
    회전으로 처리한다. libi_modes 는 지금 `/cmd_vel` 을 발행하지 않는데, 그걸 새로
    만들면 `/cmd_vel` 발행자가 하나 더 늘어난다 — 중재자(twist_mux)가 없는 이 시스템에서
    발행자를 늘리는 것은 그 자체로 위험이다(PRD "알려진 위험").
    """

    def __init__(self, name, driver, yaw_fn, tolerance_rad, timeout_sec, now_fn):
        super().__init__(name=name)
        self.driver, self.yaw_fn = driver, yaw_fn
        self.tolerance_rad, self.timeout_sec, self._now = tolerance_rad, timeout_sec, now_fn
        self._sent_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)

    def update(self):
        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        target = self.yaw_fn(pose)
        if target is None:
            return Status.FAILURE
        if pose is not None and pose.get("yaw") is not None:
            if abs(_wrap(target - pose["yaw"])) <= self.tolerance_rad:
                self._sent_at = None
                return Status.SUCCESS
        if self._sent_at is None:
            self.driver.start(target); self._sent_at = self._now()
        elif self._now() - self._sent_at >= self.timeout_sec:
            self._sent_at = None
            return Status.FAILURE
        return Status.RUNNING


def _wrap(a):
    """각도를 (-pi, pi] 로 감는다. 안 감으면 179°와 -179° 가 358° 차이로 보인다."""
    import math
    return (a + math.pi) % (2 * math.pi) - math.pi


class GoToParkingEntrance(_NavStep):
    """주차장 **입구** 정점으로 주행. navgraph 의 `주차장입구`(0.6005, -0.0333)."""


class GoToParking(_NavStep):
    """주차장 정점으로 주행. 기존 `return_dock` 드라이버가 쓰던 좌표 그대로."""


class FaceParking(_YawStep):
    """주차장 쪽을 바라보게 회전. **나중에 앞캠 ArUco 로 갈아끼울 자리다.**

    지금은 좌표 기반이다 — 현재 pose 와 주차장 좌표로 `atan2` 하면 각도가 나온다.
    nav2 가 방금 그 AMCL 로 입구까지 왔으므로, 같은 추정으로 각도만 내는 건 더 쉬운
    문제다. 마커가 정말 필요해지는 곳은 마지막 몇 cm(`AlignDock`)다.
    """
    def __init__(self, driver, parking_xy, tolerance_rad, timeout_sec, now_fn):
        import math
        super().__init__("FaceParking", driver,
                         yaw_fn=lambda pose: (
                             None if pose is None else
                             math.atan2(parking_xy[1] - pose["y"], parking_xy[0] - pose["x"])),
                         tolerance_rad=tolerance_rad, timeout_sec=timeout_sec, now_fn=now_fn)


class TurnAround(_YawStep):
    """180° 회전 — 충전 단자가 뒤에 있어 후면으로 도킹한다."""
    def __init__(self, driver, tolerance_rad, timeout_sec, now_fn):
        import math
        super().__init__("TurnAround", driver,
                         yaw_fn=lambda pose: (
                             None if pose is None or pose.get("yaw") is None else
                             _wrap(pose["yaw"] + math.pi)),
                         tolerance_rad=tolerance_rad, timeout_sec=timeout_sec, now_fn=now_fn)


class AlignDock(py_trees.behaviour.Behaviour):
    """정렬. **나중에 뒷캠 ArUco 로 갈아끼울 자리다.** 지금은 즉시 SUCCESS 한다.

    자리를 비워 두는 이유: 로직만 교체하면 되고 트리 배선은 안 바뀐다.
    `btNodeFlags.ts` 에 `unwired` 로 표시해 화면이 이 사실을 드러내게 한다(Task 14).
    """
    def __init__(self, name="AlignDock"):
        super().__init__(name=name)

    def update(self):
        return Status.SUCCESS
```

**`providers.py` 에 yaw 를 추가한다** — `_YawStep` 의 도착 판정에 필요하다. 지금 `robot_pose` 는 x·y 뿐이다(`providers.py:187`).

```python
    def _on_pose(self, msg):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        # yaw 만 필요하다. 쿼터니언 → yaw 는 z·w 만으로 나온다(roll·pitch 는 평면 주행에서 0).
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._robot_pose = {"x": float(p.x), "y": float(p.y), "yaw": float(yaw)}
```

**드라이버·좌표를 `main.py` 에 배선한다.** 지금은 복귀 목표가 `return_dock` **하나뿐**이라 입구가 없다.

```python
        # 주차장 **입구** 좌표. navgraph 의 `주차장입구` 정점.
        entrance_x = self.declare_parameter("entrance_x", 0.6005).value
        entrance_y = self.declare_parameter("entrance_y", -0.0333).value
        entrance_yaw = self.declare_parameter("entrance_yaw", 3.1415).value
        ...
            "return_entrance": FleetCmdDriver(
                self, "goal",
                args_fn=lambda: {"x": entrance_x, "y": entrance_y, "yaw": entrance_yaw},
            ).bind(cmd_pub),
            # 제자리 회전 — 같은 좌표에 yaw 만 바꿔 보낸다. start(yaw) 로 목표를 받는다.
            "return_rotate": YawGoalDriver(
                FleetCmdDriver(self, "goal").bind(cmd_pub),
                pose_fn=lambda: bb.get(self._bb, Keys.ROBOT_POSE)),
        }
```

`YawGoalDriver` 는 `start(target_yaw)` 를 받아 현재 x·y + 목표 yaw 로 `goal` 을 내는 얇은 래퍼다(`ros/fleet_cmd_driver.py` 옆에 둔다).

**`registry.build_branches` 와 `returning.create` 시그니처를 바꾼다.**

```python
# registry.py
        "RETURNING": returning.create(
            params,
            entrance_driver=drivers["return_entrance"],
            dock_driver=drivers["return_dock"],
            rotate_driver=drivers["return_rotate"]),
```

**팔 홈복귀 삭제**(사용자 결정): `drivers["return_arm"]` 주입과 `main.py` 의 부팅 팔 홈복귀 호출을 지운다. `ArmHomeDriver` 클래스 자체는 남긴다 — 팔 로봇이 붙는 날 되살릴 자리다. 삭제 근거를 주석으로 남긴다.

- [ ] **Step 4: `returning.py` 를 배선한다**

```python
def create(drivers, params):
    steps = py_trees.composites.Sequence(name="ReturnSteps", memory=True, children=[
        AbsorbFailure(GoToParkingEntrance(...), params["dock_retry_max"]),
        AbsorbFailure(FaceParking(...),          params["dock_retry_max"]),
        AbsorbFailure(GoToParking(...),          params["dock_retry_max"]),
        AbsorbFailure(TurnAround(...),           params["dock_retry_max"]),
        AbsorbFailure(AlignDock(...),            params["dock_retry_max"]),
        SetNextMode("CHARGING"),
    ])
    return py_trees.composites.Sequence(name="ReturningBranch", memory=False, children=[
        IsMode("RETURNING"),
        py_trees.composites.Parallel(
            name="ReturnAndWatch",
            policy=py_trees.common.ParallelPolicy.SuccessOnOne(),
            children=[steps, exit_watchdog("ReturnExit", [FaultDetected()])]),
        RequestTransition(),
    ])
```

복귀 진입 시 `camera_select = none` 을 요청한다(세션을 만들지 않으므로 `follow_node` 가 자동으로 `none` 을 낸다 — 별도 발행을 추가하지 않는다. 테스트는 그 사실을 검사한다).

**팔 홈 복귀는 삭제한다**(사용자 결정). `registry.py` 에서 `arm_driver` 주입을 제거하고, 삭제 근거를 주석으로 남긴다.

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest test/ -q`
Expected: PASS 전부. 특히 `test_tree.py` 의 구조 불변식이 깨지지 않았는지 확인

- [ ] **Step 6: 커밋**

```bash
git commit -am "feat(returning): 복귀 5단계 분리 + 실패 흡수 재시도 래퍼"
```

---

### Task 12: `libi_gui` 길잡이 화면

**Files:**
- Modify: `aba_controller/libi_gui/qml/screens/GuideScreen.qml`
- Modify: `aba_controller/libi_gui/src/RobotController.h`, `RobotController.cpp`
- Test: `aba_controller/libi_gui/tests/test_panel_link.cpp` (확장)

**Interfaces:**
- Consumes: `RosLink::publishFleetCmd(json)`(기존), `PerceptionClient`(기존 등록 프로토콜)
- Produces: `RobotController::startGuideRegistration()`, `cancelGuideRegistration()`, `guideRegPhase`(QString: `idle|registering|calibrating|ready`), `currentCameraLabel`(QString)

**배경(등록 데드락):** 등록하려면 카메라가 필요한데 카메라는 세션이 켜고 세션은 등록 후 시작된다. 게다가 등록 시점의 미션 상태는 `INTERACTING` 이라 `WORKING` 브랜치의 `GuideExec` 은 **tick 되지도 않는다.** 그래서 **패널이 `/fleet_cmd{watch}` 를 직접 발행**해 감시 세션을 연다.

**등록은 앞카메라로 한다** — 이용자는 패널을 누르므로 등록 시점에 로봇 앞에 있다. 뒷카메라에는 안 잡힌다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```cpp
// tests/test_panel_link.cpp 에 추가
TEST(PanelLink, GuideRegistrationOpensWatchSessionWithFrontCamera) {
    FakeRosLink link; RobotController c(&link);
    c.startGuideRegistration();
    ASSERT_EQ(link.lastAction(), "watch");
    ASSERT_EQ(link.lastArg("camera"), "front");   // 이용자는 패널 앞에 있다
    ASSERT_FALSE(link.lastId().isEmpty());
}

TEST(PanelLink, LeavingRegistrationStopsSameSession) {
    FakeRosLink link; RobotController c(&link);
    c.startGuideRegistration();
    const QString id = link.lastId();
    c.cancelGuideRegistration();
    ASSERT_EQ(link.lastAction(), "stop");
    ASSERT_EQ(link.lastId(), id);                 // 자기 세션만 닫는다
}

TEST(PanelLink, StartGuideSendsGuideAfterRegistration) {
    FakeRosLink link; RobotController c(&link);
    c.startGuideRegistration();
    c.onRegistrationConfirmed();
    c.startGuide("문학서가");
    ASSERT_EQ(link.lastAction(), "guide");
}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd aba_controller/libi_gui && cmake -S . -B build && cmake --build build -j && ./build/test_panel_link`
Expected: FAIL — 메서드 없음

- [ ] **Step 3: `RobotController` 를 확장한다**

`startGuideRegistration()` 이 `{"action":"watch","id":<uuid>,"args":{"camera":"front"}}` 를 발행하고 `m_watchSessionId` 를 보관한다. `cancelGuideRegistration()` 은 `{"action":"stop","id":m_watchSessionId}` 를 보낸다. 화면이 살아 있는 동안 lease 갱신을 위해 같은 `watch` 를 주기적으로 재발행한다(`follow_node` 의 `SESSION_LEASE_SEC` 보다 촘촘하게).

- [ ] **Step 4: `GuideScreen.qml` 을 고친다**

흐름을 넷으로 나눈다:

```
목적지 선택 → 이용자 등록(앞캠 영상 + 탭) → 자세 측정 → 출발
                                              안내 중: 지도 + 뒷캠 미니뷰
```

- 등록 화면은 `FollowScreen.qml` 의 등록 UI(`image://perception/frame` + 탭)를 그대로 재사용한다. **새 통신 경로를 만들지 않는다**
- `guideRegPhase === "calibrating"` 이면 **"자세 측정 중"** 을 표시한다. 등록 직후 몇 초 안 움직이는 이유가 화면에 드러나야 한다
- 안내 중에는 지도 + 뒷캠 미니뷰를 같이 그린다
- 어느 화면이든 **현재 캠 라벨**을 얹는다. 캠이 바뀌면 시점이 뒤집혀 보이므로 라벨이 없으면 보는 사람이 혼란스럽다

- [ ] **Step 5: 통과를 확인한다**

Run: `cmake --build build -j && ./build/test_panel_link && ./build/test_domain`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git commit -am "feat(gui): 길잡이 등록 흐름과 뒷캠 미니뷰·캠 라벨"
```

---

### Task 13: 동적 장애물 (기본 OFF) + 런처 정리

**Files:**
- Create: `aba_controller/.../libi_perception/keepout_mask.py` (정책 + 발행 노드)
- Create: `aba_controller/.../pinky_navigation/params/nav2_params_keepout.yaml`
- Modify: `aba_controller/.../libi_modes/common/working_actions.py` — **`GuideExec` 에 근접 정지 배선**
- Modify: `aba_controller/.../libi_modes/config/params.yaml`
- Modify: `scripts/all/pi-all.sh`
- Test: `aba_controller/.../libi_perception/tests/test_keepout_mask.py`,
  `aba_controller/.../libi_modes/test/test_guide_exec.py` (근접 정지 통합)

**Interfaces:**
- Consumes: `Keys.REQUESTER_AREA`(T7 발행 → T8 provider → blackboard), `GuideExec` 의 `stop_driver`(기존)
- Produces:
  - `KeepoutPolicy(near_area_max, wait_sec, ttl_sec, fan_deg, fan_range_m, footprint_radius)` — `update(area, pose, now) -> ("drive"|"halt"|"mask", mask_or_None)`, `active_mask(now)`
  - `keepout_mask` 노드 — `/costmap_filter_info`(`nav2_msgs/CostmapFilterInfo`, latched) + `/keepout_mask`(`nav_msgs/OccupancyGrid`, latched) 발행
  - `GuideExec(..., near_area_max)` — 근접 시 `stop_driver` 로 실제 정지

> ⚠️ **정책 모듈만 만들면 아무 일도 안 일어난다.** 첫 판에서 이 Task 는 `KeepoutPolicy`
> 단위테스트만 있고 소비자가 없었다 — 테스트는 통과하는데 실제 주행은 한 번도 멈추지
> 않는다(스토리 42~44 미구현). **배선까지가 이 Task 다.**

**배선 세 갈래**

```
① 근접 정지 (BT)        GuideExec 이 requester_area >= near_area_max 면 stop_driver.start()
                        → mission_stop → nav2 취소. 10초 대기는 GuideExec 이 센다
② 마스크 발행 (ROS)     keepout_mask 노드가 10초 초과 시 로봇 앞 부채꼴을 OccupancyGrid 로
                        발행. 로봇 현재 footprint 는 **반드시 제외**한다
③ 재계획 (BT)           GuideExec 이 `_sent_at = None` 으로 되돌려 goal 을 다시 낸다.
                        nav2 는 새 마스크가 반영된 costmap 으로 우회 경로를 만든다
```

**기본 OFF 를 지키는 방법**: `near_area_max: 0`(끔)이 기본값이다. 런처가 `--dyn-obstacle` 을
받으면 `nav2_params_keepout.yaml` 을 쓰고 마스크 노드를 띄우며, `params.yaml` 오버라이드로
`near_area_max` 를 실제 값으로 올린다. **끄면 코드 경로가 첫 `if` 에서 끝난다.**

**배경:**
- nav2 는 **costmap 에 있는 것만** 피한다. 관측 소스가 LiDAR 하나뿐이라 카메라로만 보이는 것은 없는 것과 같다
- **장애물 레이어에 직접 주입하면 안 된다** — `clearing: True` 라 레이저가 통과한 칸을 비운다. 스캔 평면에 없는 물체 표시는 다음 스캔에 지워진다
- **통행 금지 필터(KeepoutFilter)** 를 쓴다. 마스크는 레이트레이싱이 못 지운다
- **마스크는 로봇 현재 footprint 를 절대 포함하지 않는다.** 포함하면 컨트롤러가 탈출 궤적까지 막아 로봇이 갇힌다
- **기본 OFF.** 켜고 끄는 게 쉬워야 문제가 났을 때 즉시 되돌린다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_keepout_mask.py
from libi_perception.keepout_mask import KeepoutPolicy

def test_far_person_drives():
    p = KeepoutPolicy(near_area_max=5000, wait_sec=10, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    assert p.update(area=100, pose=(0, 0, 0), now=0)[0] == "drive"

def test_near_person_halts_immediately():
    p = KeepoutPolicy(near_area_max=5000, wait_sec=10, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    assert p.update(area=9000, pose=(0, 0, 0), now=0)[0] == "halt"

def test_mask_only_after_wait():
    p = KeepoutPolicy(near_area_max=5000, wait_sec=10, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    p.update(area=9000, pose=(0, 0, 0), now=0)
    assert p.update(area=9000, pose=(0, 0, 0), now=5)[0] == "halt"
    assert p.update(area=9000, pose=(0, 0, 0), now=11)[0] == "mask"

def test_person_leaving_before_wait_never_masks():
    """지나가는 사람 때문에 지도가 더러워지지 않는다."""
    p = KeepoutPolicy(near_area_max=5000, wait_sec=10, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    p.update(area=9000, pose=(0, 0, 0), now=0)
    p.update(area=100, pose=(0, 0, 0), now=3)
    assert p.update(area=9000, pose=(0, 0, 0), now=11)[0] == "halt"   # 타이머 재시작

def test_mask_excludes_robot_footprint():
    """로봇이 마스크 안에 갇히면 탈출 궤적까지 막힌다."""
    p = KeepoutPolicy(near_area_max=5000, wait_sec=0, ttl_sec=20,
                      fan_deg=180, fan_range_m=0.5, footprint_radius=0.10)
    _, mask = p.update(area=9000, pose=(0.0, 0.0, 0.0), now=1)
    assert not mask.contains(0.0, 0.0)
    assert not mask.contains(0.05, 0.0)      # footprint 반경 안

def test_mask_expires():
    p = KeepoutPolicy(near_area_max=5000, wait_sec=0, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    p.update(area=9000, pose=(0, 0, 0), now=1)
    assert p.active_mask(now=25) is None

def test_disabled_when_near_area_max_zero():
    """기본 OFF — 첫 if 에서 끝난다."""
    p = KeepoutPolicy(near_area_max=0, wait_sec=10, ttl_sec=20,
                      fan_deg=60, fan_range_m=0.5, footprint_radius=0.06)
    assert p.update(area=999999, pose=(0, 0, 0), now=0)[0] == "drive"
```

**그리고 `GuideExec` 배선 테스트** (`libi_modes/test/test_guide_exec.py`):

```python
def test_near_person_halts_guide(guide_env):
    """근접 정지가 실제로 nav 를 멈춘다 — 정책 모듈만 만들면 아무 일도 안 일어난다."""
    env = guide_env(visible=True, requester_area=9000.0, near_area_max=5000.0)
    env.tick()
    assert env.stop_driver.started is True
    assert env.status == Status.RUNNING

def test_near_gate_off_by_default(guide_env):
    env = guide_env(visible=True, requester_area=9000.0, near_area_max=0.0)
    env.tick()
    assert env.stop_driver.started is False

def test_resumes_after_person_clears(guide_env):
    env = guide_env(visible=True, requester_area=9000.0, near_area_max=5000.0)
    env.tick()
    env.set(requester_area=100.0)
    env.tick()
    assert env.driver.started is True      # goal 을 다시 낸다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest tests/test_keepout_mask.py -v`
Expected: FAIL — 모듈 없음

- [ ] **Step 3: 구현하고 nav2 파라미터를 만든다**

`nav2_params_keepout.yaml` 은 `nav2_params.yaml` 을 복사한 뒤 두 costmap 에 필터를 얹는다:

```yaml
      plugins: ["obstacle_layer", "inflation_layer"]
      filters: ["keepout_filter"]
      keepout_filter:
        plugin: "nav2_costmap_2d::KeepoutFilter"
        enabled: True
        filter_info_topic: "/costmap_filter_info"
```

**파일을 두 벌로 두는 이유**: 마스크 발행 노드가 없을 때 필터가 어떻게 구는지에 의존하지 않기 위해서다. 런처가 `--dyn-obstacle` 유무로 고른다.

- [ ] **Step 4: `pi-all.sh` 를 고친다**

세 가지를 한다.

1. `cam-front` / `cam-back` 두 창 → **`cam` 한 창**(단일 프로세스, `--back <n>` 은 뒷캠 장치 인덱스 지정용으로 유지, CSI 오인 방지 검사도 유지)
2. **`follow-drive` 창을 제거한다.** 추종 제어가 로봇 쪽 `libi_perception` 으로 옮겨가 UDP:6002 경로가 은퇴한다. 남겨 두면 그 브리지가 20Hz 로 정지 명령을 계속 쏴서 새 PID 와 `/cmd_vel` 을 다툰다(중재자가 없어 마지막 메시지가 이긴다)
3. `--dyn-obstacle` 플래그(**기본 꺼짐**). 켜면 `nav2_params_keepout.yaml` 을 쓰고 마스크 발행 노드 창을 띄운다. `DYN_OBSTACLE_FAN_DEG`·`DYN_OBSTACLE_TTL`·`DYN_OBSTACLE_NEAR_AREA` 를 env 로 받는다

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONPATH=. python3 -m pytest tests/ -q` 및 `bash -n scripts/all/pi-all.sh`
Expected: PASS · 문법 오류 없음

- [ ] **Step 6: 커밋**

```bash
git commit -am "feat(nav): 통행금지 마스크(기본 OFF)와 런처 카메라/추종 경로 정리"
```

---

### Task 14: 문서 · BT 플래그 갱신

**Files:**
- Modify: `aba_fms_service/frontend/src/components/admin/btNodeFlags.ts`
- Modify: `aba_controller/libi_modes/README.md`
- Modify: `aba_controller/.../libi_perception/README.md`
- Modify: `aba_ai_service/README.md`

**배경:** `btNodeFlags.ts` 의 키는 py_trees `name` **문자열 그대로**다. 갱신하지 않으면 범례 숫자가 0으로 떨어지고 관제 화면이 **조용히 거짓**이 된다.

- [ ] **Step 1: 신규·개명 노드를 모아 확인한다**

Run:
```bash
grep -rhoP "name=['\"]\K[A-Za-z0-9_\[\]]+" \
  aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes \
  aba_controller/libi_modes/ros_ws/src/libi_perception/libi_perception | sort -u
```
Expected: `PeekBack`, `PeekBack2`, `Scan3`, `AlignHeading`, `GoToParkingEntrance`, `FaceParking`, `GoToParking`, `TurnAround`, `AlignDock`, `ReturnSteps`, `Absorb[...]` 가 목록에 보임

- [ ] **Step 2: `btNodeFlags.ts` 를 고친다**

- 사라진 노드 키(`ReturnNavigation`)를 지운다
- 신규 노드 키를 추가한다. 스텁인 것에는 근거를 `file:line` 주석으로 남긴다:
  - `FaceParking: "partial"` — 좌표 기반 회전만. ArUco 미배선 (`return_steps.py:<line>`)
  - `AlignDock: "unwired"` — 즉시 SUCCESS. ArUco 미배선 (`return_steps.py:<line>`)
- **`GuideExec: "partial"` 을 해제한다** — `/libi/requester_visible` 발행자가 생겼다

- [ ] **Step 3: README 세 개를 고친다**

| 파일 | 내용 |
|---|---|
| `libi_modes/README.md` | 복귀 브랜치 5단계 그림, 팔 홈복귀 삭제와 그 근거, `GuideExec` 의 감시 세션 계약 |
| `libi_perception/README.md` | 회복 타임라인 변경(뒤를 보려고 돌던 9초 → 카메라 전환 2초 × 2, 사각 보루로 `Turn180`+`Scan3`), **의도적 동작 변경 절차를 따랐다는 근거**, 카메라 선택 계약, 세션 id·lease |
| `aba_ai_service/README.md` | 인지 파이프라인이 로봇 검출 채널을 먹인다(더미 스텁 은퇴), UDP 포트 단일화(6003 폐지), 생프레임 2슬롯 탭 계약 |

- [ ] **Step 4: 화면이 실제로 맞는지 확인한다**

Run: `cd aba_fms_service/frontend && npm run lint && npm run build`
Expected: 통과

- [ ] **Step 5: 커밋**

```bash
git commit -am "docs: BT 노드 플래그와 README 3종 갱신"
```

---

## 요구사항 커버리지 자체체크 (PRD User Stories → Task)

| 스토리 | Task | 상태 |
|---|---|---|
| 1~3 카메라 송출 단일화·미사용 시 정지 | T6, T13 | covered |
| 4 캠 라벨 | T12 | covered |
| 5 늦게 뜬 송출기도 올바른 카메라 | T6(만료 워치독) + T7(주기 재발행) | covered |
| 6~7 생프레임 탭, `none` 에서도 동작 | T6 | covered |
| 8~9 등록 시 기준 재측정·화면 표시 | T5, T12 | covered |
| 10~11 누움 정지·`Unknown` 내성 | T3, T5, T8 | covered |
| 12~15 소실 방향 4종 | T4, T5 | covered |
| 16 추종이 실제로 시작 | T1 | covered |
| 17 놓치면 우선 정지 | T9(Hold) | covered |
| 18~19 뒷캠 재획득 회전·여럿이면 무반응 | T9 | covered |
| 20~21 사각 보루·끝내 못 찾으면 종료 | T9 | covered |
| 22~24 목적지 선택·등록·패널 앞에서 등록 | T12 | covered |
| 25~26 뒷캠 확인·뒤처지면 대기 | T7, T10 | covered |
| 27 너무 멀면 정지 | T10 | covered |
| 28 갈림길 확인 | T10 | covered |
| 29 앞질러도 안내 계속 | T9(길잡이 무회전) | covered |
| 30 지도 + 뒷캠 미니뷰 | T12 | covered |
| 31 오래 없으면 종료 | 기존 `lost_timeout_sec` | covered |
| 32 멈출 때 실제로 정지 | T1(취소 수정) | covered |
| 33~34 등록 화면 카메라 켜짐·이탈 시 꺼짐 | T7(세션), T12 | covered |
| 35~41 복귀 5단계·카메라 없음·재시도·고장 | T11 | covered |
| 42~47 동적 장애물 · 기본 OFF · 토글 | T13 | covered |
| 48~49 BT 그림 일치·`GuideExec` 플래그 해제 | T14 | covered |
| 50 지도 축척 프로파일 | T10(params), T13(env) | covered |

**missing 없음.**

## 실기 검증 (6단계에서 실제로 잰다)

소프트웨어로 선행 검증한 뒤 남는 것만 실기로 간다.

| 재는 것 | 결과에 따른 설계 변경 |
|---|---|
| 앞뒤 카메라 동시 open | 불가하면 장치 개폐 폴백 + `SEARCH_PEEK_SEC` 2.0 → 4.0 |
| `picamera2` + `rclpy` 동시 import | 불가하면 로컬 UDP 제어 경로로 폴백 |
| 카메라 화각 | 사각이 없으면 `Turn180`·`Scan3` 제거 |
| 자세 추론 프레임 예산 | 초과하면 `POSE_EVERY_N_FRAMES` 1 → 3 |
| LiDAR 와 카메라 검출 중복도 | 완전히 겹치면 근접 정지 제거 |
| 통행 금지 부채꼴이 통로를 막는지 | 막으면 `DYN_OBSTACLE_FAN_DEG` 축소 |
| 취소 수정이 실제 nav2 목표를 취소하는지 | — |
