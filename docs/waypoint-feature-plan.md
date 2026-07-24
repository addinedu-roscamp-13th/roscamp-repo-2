# Waypoint 기능 구현 계획

## 목표

FMS 관제 화면(`/admin/waypoint`)에서 `waypoint.yaml`(내비 그래프: 노드+간선)을
지도 위에서 보고 편집하고, 특정 노드로 로봇을 이동시킬 수 있게 한다.

## 요구사항 (원 요청 그대로)

1. `arte2.pgm` 배경 위에 `waypoint.yaml` 노드를 점+이름으로 표시, yaw 있으면 방향 화살표도 표시
2. 오른쪽 편집 패널 — 노드 위치/이름 변경, 간선(양방향/단방향) 연결·변경·삭제, 노드별 "~로 이동" 버튼(nav 패키지로 명령)
3. 실물(real) + 시뮬레이션(sim) 둘 다 지원
4. ROS2 통신으로 로봇의 실시간 좌표를 받아 지도에 표시
5. 지도 확대/축소 가능, 확대 시 글씨도 같이 커짐 (편집 용이성)
6. 레퍼런스: `pingdergarten`의 `app/admin-app/widgets/waypoint_map_card.py` (PGM 배경 + zoom/pan + 노드/간선 편집 상태머신 + 실시간 위치)

**추가 제약**: robot_agent(FastAPI)를 켜지 않아도 동작해야 한다 — 통신은 `fleet_link`(순수 rclpy, FastAPI 비의존)를 통한 ROS2 `fleet_cmd`만 사용.

## 아키텍처

```
FMS 프론트(React, /admin/waypoint)
  → FMS 백엔드(aba_fms_service, mission_control.py 패턴 재사용)
    → fleet_telemetry._ros_command() → ROS2 /pinky{N}/fleet_cmd 토픽
      → [실물] domain_bridge_pinky{N}.yaml → 로봇의 fleet_link(standalone, FastAPI 없음)
      → [sim]  sim 전용 도메인 등록 (FLEET_ROBOTS에 항목 추가)
        → fleet_link._dispatch()의 waypoint_get/waypoint_save/waypoint_goto
          → app/core/waypoints.py (~/.pinky/waypoint.yaml 읽기/쓰기)
          → app/core/ros_bridge.py send_nav_goal() (기존 검증된 경로)
```

## 상태 (2026-07-20 기준)

### 완료

- [x] **`aba_controller/.../robot_agent/app/core/waypoints.py`** (신규) — `locations.py`와 동일 패턴으로 `~/.pinky/waypoint.yaml` 읽기/쓰기. 홈에 없으면 패키지 기본 `params/waypoint.yaml`로 시드. `get_graph/set_graph/set_vertex/rename_vertex/delete_vertex/set_lane/delete_lane/nearest_vertex/route` 제공. `route()`는 다익스트라로 간선(lane)만 타는 최단경로 계산 (격자식, 대각선 없음).
  - 검증: ROS2 소싱 후 `python3` 인라인 테스트 — 시드(41 vertices/52 lanes), `nearest_vertex`, `route()`(경로가 100% 간선으로만 구성되는지 프로그램적으로 검증), CRUD(set/rename/set_lane/delete_lane/delete_vertex) 전부 통과.
- [x] **`aba_controller/.../robot_agent/app/core/fleet_link.py`** 수정 — `_dispatch()`에 `waypoint_get`/`waypoint_save`/`waypoint_goto`(그래프 라우팅+구간별 순차 이동) 추가.
  - 검증: `py_compile` 통과. `waypoint_goto`가 쓰는 것과 동일한 함수(`waypoints.route`, `ros_bridge`가 내부적으로 쓰는 것과 동일한 NavigateToPose 액션 경로)를 sim에서 `nav2_simple_commander` 기반 테스트 스크립트로 간접 검증(아래). **fleet_link 프로세스 자체를 통한 end-to-end(실제 fleet_cmd 토픽 경유) 호출은 아직 안 함** — 이건 8번 단계(FMS 연동)에서 자연히 같이 검증됨.
- [x] **`aba_controller/.../ros_ws/.../gz_bringup_launch.xml`** 버그 수정 — `lifecycle_nodes_nav`에 `collision_monitor` 누락돼있던 것 추가. 재빌드 완료.
- [x] **`waypoint.yaml`에 격자식 간선(lane) 자동 생성** — pgm 점유 격자 기준으로 축정렬+최근접+장애물없음 조건 만족하는 쌍만 연결, 전부 bidirectional. 41 vertices, 52 lanes, BFS로 전체 연결성 확인(고립 노드 0개).
- [x] **sim 다중 홉 주행 검증 (kill.sh→sim.sh 사이클 반복)**:
  - **Test 3**: `주차장→입구→복도-1→복도-2→복도-3→복도-5→복도-6` (7구간) 전부 `TaskResult.SUCCEEDED`, 간선 그래프 그대로 이동, yaw는 중간노드=다음노드 방향/최종노드=저장된 yaw로 처리.
  - **Test 4**: `안네데스크→...→테이블-1번-우` (13구간, 그래프 전체를 가로지르는 장거리 경로) 전부 `SUCCEEDED`. 마지막 구간에서 최종 회전(final rotation) 단계 중 `distance_remaining` 피드백이 잠깐 튀는 걸 제 테스트 스크립트의 정지-감지 휴리스틱이 오탐(false positive)해서 취소를 시도했으나, nav2 자체는 그 직전에 이미 정상 완료됨 — **실제 프로덕션 코드(`ros_bridge.nav_to()`)는 이런 인위적 타임아웃/정지-감지가 없어 이 오탐의 영향을 받지 않음.**
  - 결론: 격자 간선 기반 다중 홉 주행, 중간 경유 시 진짜 멈춤 없음(느린 구간은 있으나 결국 도착), 최종 yaw 처리 로직 모두 정상 동작 확인.

### 추가 완료

- [x] **sim 전용 ROS 도메인 분리** — `sim.sh`에 `ROS_DOMAIN_ID=90` export 추가(실물 도메인 87/88/89와 겹치지 않음). `aba_fms_service/config/domain_bridge_sim.yaml` 신규(실물 domain_bridge_pinky{N}.yaml과 동일 패턴, 90↔86, `/pinkySim/*` 접두).
- [x] **`fleet_telemetry.py`** — `FLEET_ROBOTS`에 `"127.0.0.1": {"key": "pinkySim", "prefix": "/pinkySim"}` 추가. `py_compile` 통과.
- [x] **`mission_control.py`** — `GET/PUT /api/control/waypoints`, `POST /api/control/waypoints/{name}/goto` 추가 (`_ros_command` 패턴 재사용, 다중 홉 이동 대비 `WAYPOINT_GOTO_TIMEOUT_SEC=180s`). `py_compile` 통과.
- [x] **`admin-api.ts`** — `WaypointGraph`/`WaypointLane` 타입 + `waypointsGet/waypointsSave/waypointGoto` 클라이언트 함수 추가.
- [x] **`WaypointEditor.tsx`**(신규) + **`waypoint.tsx`** — 캔버스 편집기: 지도 배경(RobotConsole과 동일 렌더링 방식 재사용) + 노드(점+이름+yaw 화살표) + 간선(선, 단방향 화살표 표시) + 실시간 로봇 위치(WS) + Ctrl+휠 zoom/pan(폰트도 sqrt(zoom)로 스케일) + 우측 패널(이름/좌표/yaw 수정, 간선 양방향 토글/삭제, "이 노드로 이동" 버튼, 노드 추가/삭제).
  - 검증: `tsc --noEmit` — 새 파일발 에러 0개(기존 베이스라인 에러 17개와 정확히 일치, 증가 없음).

### 남은 작업

1. **fleet_link 프로세스를 sim 도메인(90)에서 실제로 띄워 FMS→fleet_cmd→fleet_link 전체 경로 end-to-end 확인** (지금까지는 `ros_bridge`/`waypoints.route()`를 직접 호출해서 핵심 로직만 검증했고, 실제 fleet_cmd 토픽 왕복 + domain_bridge_sim.yaml 경유는 아직 안 함)
2. **사용자가 직접 브라우저에서 클릭 테스트** (이 세션엔 브라우저 자동화 툴이 없어 제가 직접 못 함)

### 커밋

사용자 지시대로 전체 구현 끝날 때까지 커밋 안 함 (한 번에 나중에).

## 파일 목록

| 상태 | 경로 |
|---|---|
| 완료 | `aba_controller/.../robot_agent/app/core/waypoints.py` (신규) |
| 완료 | `aba_controller/.../robot_agent/app/core/fleet_link.py` (수정) |
| 완료 | `aba_controller/.../ros_ws/.../launch/gz_bringup_launch.xml` (버그 수정) |
| 완료 | `aba_controller/.../ros_ws/scripts/sim.sh` (ROS_DOMAIN_ID=90 추가) |
| 완료 | `aba_controller/.../pinky_navigation/params/waypoint.yaml` (간선 52개 자동생성) |
| 완료 | `aba_fms_service/config/domain_bridge_sim.yaml` (신규) |
| 완료 | `aba_fms_service/backend/app/fleet_telemetry.py` (FLEET_ROBOTS sim 항목) |
| 완료 | `aba_fms_service/backend/app/routers/mission_control.py` (waypoint 엔드포인트 3개) |
| 완료 | `aba_fms_service/frontend/src/lib/admin-api.ts` (타입+클라이언트 함수) |
| 완료 | `aba_fms_service/frontend/src/components/admin/WaypointEditor.tsx` (신규) |
| 완료 | `aba_fms_service/frontend/src/routes/admin/_authed/waypoint.tsx` |
| 예정 | `aba_fms_service/frontend/src/components/admin/` 신규 편집기 컴포넌트 |
