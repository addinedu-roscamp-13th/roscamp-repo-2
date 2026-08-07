"""서가 정밀 도킹 — 지도기반 LOS 교차점 추정.

카메라가 서가 표식의 **방향**을 주고, 지도가 그 방향의 **거리**를 준다. 둘을 합치면
서가 표면의 맵 좌표가 나오고, 거기서 이동량을 계산해 개루프로 간다.

    ① navgraph 가 정한 서가 방향으로 회전
    ② 오른쪽으로 조금 더 회전 (표식이 화면에 들어오게)
    ③ 앞캠 프레임에서 초록 표식 중점 픽셀 u
    ④ bearing θ = atan2(u - cx, fx)          ← 320 기준 K
    ⑤ /map 을 (yaw - θ) 방향으로 레이캐스트 → 첫 점유 셀 = 특징점
    ⑥ 삼각형 분해 → 회전·직진·회전
    ⑦ 서가 쪽으로 1cm 못 미쳐 멈춘다
    ⑧ 정점·간선 잠금 해제
    ⑨ 실행

## ⚠️ bearing 의 부호

`bearing_rad` 는 화면 **오른쪽**을 양수로 준다. 로봇 기준으로도 오른쪽이고, 오른쪽은
yaw 가 **줄어드는** 방향이므로 레이 방향은 `yaw - θ` 다. 부호를 뒤집으면 거리는
그럴듯한데 좌우만 반대로 흐른다 — `app/marker/calib.py` 가 경고하는 바로 그 증상이다.

## 왜 계산과 실행이 나뉘어 있나

계산은 로봇 없이 시험할 수 있고 실행은 아니다. 이 파일의 `plan_dock` 은 순수 함수라
합성 프레임·합성 격자로 전부 시험된다.
"""
from __future__ import annotations

import json
import math
import threading
import time

from app.shelf.bearing import bearing_rad, scale_k
from app.shelf.geometry import axis_aligned_moves, wrap_pi
from app.shelf.geometry import TURN
from app.shelf.green_marker import centroid_u, centroid_uv
from app.shelf.raycast import first_occupied

#: 서가 정점에서 서가를 마주 보는 방향(rad, map 프레임).
#: 화면(관제 UI, 90° CCW 회전) 기준으로 문학서가는 왼쪽, 과학-인문학서가는 오른쪽이다.
#: ⚠️ `arte2.navgraph.yaml` 의 같은 정점 `yaw` 와 **반드시 같은 값**이어야 한다.
SHELF_YAW = {
    "문학서가": 1.5708,
    "과학-인문학서가": -1.5708,
    #: 실측 전 임시값(2026-08-05) — 문학서가와 같은 +Y 방향으로 가정. 실기에서
    #: 다르면 여기만 고친다(waypoint.yaml/navgraph 는 아직 이 셋을 yaw 없이 둔다).
    "예술서가": 1.5708,
}

#: 서가를 마주 본 뒤 추가로 도는 각. 양수 = 왼쪽. 표식을 화각 안에 넣기 위한 값이다.
EXTRA_TURN_RAD = 0.3491

#: 도킹을 마쳤을 때의 자세. 두 서가가 같다 — 팔이 같은 조건에서 일하게.
FINAL_YAW_RAD = 3.1416

#: 서가에 닿지 않도록 못 미쳐 멈추는 거리(m).
#:
#: ⚠️ **이건 로봇 표면이 아니라 로봇 원점(base_footprint)에서 잰 거리다.** PGM
#: 레이캐스트(`first_occupied`)가 AMCL pose(=원점)에서 쏘기 때문이다. 그래서 로봇
#: 반지름을 빼고 남는 게 실제 틈이다:
#:
#:     실제 틈 = CLEARANCE_M - robot_radius(0.06, nav2_params.yaml:304)
#:
#: 2026-08-05까지 0.02(2cm)였는데, 그건 원점이 벽에서 2cm — 즉 **로봇 몸통이 벽
#: 4cm 안으로 파고든 자리**를 목표로 삼고 있었다는 뜻이다(실기에서 벽 판정이 실제
#: 서가 면보다 뒤에 잡혀 사고가 안 났을 뿐이다).
#:
#: 0.09 = 반지름 0.06 + 실제 틈 3cm. 도킹을 마치면 `FINAL_YAW_RAD`(180°)로 제자리
#: 회전하는데, 로봇이 원형(robot_radius)으로 모델링돼 있어 **회전해도 쓸고 가는
#: 반경은 그대로 0.06** — 즉 이 값이 회전 중에도 그대로 유지되는 최소 틈이다.
#:
#: ## [2026-08-05] 0.07 → 0.09 — 실측 오차가 실여유보다 컸다
#:
#: 0.07 은 실여유가 **1cm** 뿐이었다. 그런데 이 거리는 전부 **PGM 기준**이고, PGM 은
#: SLAM 당시 스냅샷 + 2cm 격자라 실제 벽과 어긋난다. 그날 뷰어에 계측을 붙여 처음으로
#: 숫자를 봤다(`scripts/demo/shelf_dock_lidar_viewer`, 19:48 실기):
#:
#:     LAT PLAN    PGM 20.0cm / 라이다 14.9cm  → diff -5.1cm   ← 실제 벽이 더 가깝다
#:     FINAL PLAN  PGM 19.0cm / 라이다 21.9cm  → diff +2.9cm
#:
#: **오차가 부호까지 뒤집히고 크기가 실여유의 몇 배다.** 1cm 설계로는 PGM 이 조금만
#: 낙관적이어도 몸통이 서가에 닿는다. 3cm 로 올려 그 폭을 흡수한다.
#:
#: ⚠️ 근본 해결은 아니다 — 거리 판정을 여전히 PGM 으로만 한다. 실제 벽을 보려면
#: `/scan` 을 판정에 넣어야 한다(뷰어는 이미 둘을 나란히 로그에 남긴다).
#:
#: ⚠️ 팔이 붙는 날 다시 본다 — 2cm 멀어진 만큼 팔의 도달 거리가 줄어든다. 팔이 못
#: 닿으면 이 값이 아니라 **접근 자세/팔 사거리** 쪽에서 맞춘다(닿으려고 실여유를
#: 도로 1cm 로 깎지 않는다).
#:
#: ⚠️ nav2 `inflation_radius` 도 0.09(같은 파일:315)다. 이제 정지 지점이 그 경계와
#: 같아졌지만 AMCL 오차(±2~3cm)면 안쪽으로 떨어지므로, 빠져나올 때 복귀 다리가
#: 필요한 건 그대로다(`decompose_delivery` 의 backup 다리 주석 — undock 게이트는
#: 주차장 도크만 보므로 서가에선 안 돈다).
#:
#: ## [2026-08-07] 0.09 → 0.088 → 0.084 — 사용자 지시로 두 번 좁힘
#:
#: 같은 날 2mm, 이어서 4mm. 실여유 3cm → 2.8cm → **2.4cm**. 팔 도달 거리를 벌기 위한
#: 조정이다.
#:
#: ⚠️ 이 값은 **제자리 회전의 안전 여유이기도 하다.** 도킹을 마치면 옆구리가 서가를
#: 보고, 빠져나갈 때 90° 를 제자리에서 돈다 — 그때 몸 끝단이 이 거리 안으로 들어오면
#: 닿는다. **2026-08-07 실기에서 3cm 일 때 이미 꽁무늬가 닿았다.** 그때는 회전 방향을
#: 뒤집어(`shelf/geometry.py` `retreat_moves`) 꽁무늬 대신 코가 서가를 보게 해서
#: 피했지, 여유를 늘려서 피한 게 아니다. 즉 지금 2.4cm 는 **코 쪽 돌출에만 기대고 있다.**
#:
#: ⚠️ 여기서 더 줄이기 전에 **회전 중 코가 쓸고 가는 반경을 실측한다.** 그 값이 이
#: 거리를 넘으면 회전 방향을 뒤집어도 못 피한다 — 그때는 `CLEARANCE_M` 이 아니라
#: `FINAL_YAW_RAD`(옆구리로 세우는 자세) 쪽을 봐야 한다.
CLEARANCE_M = 0.084

#: 레이캐스트 최대 사거리(m). 서가는 20cm 안쪽이라 넉넉하다.
MAX_RANGE_M = 1.0

#: 현장 비교용: 카메라 내부보정 K 없이 영상 정중앙을 기준으로 한다.
#: 중앙정렬 뒤 광선은 현재 AMCL yaw 그대로 쏜다.
USE_CAMERA_CALIBRATION = False

#: 초록 테이프 중점 오차의 PID 비주얼 서보 설정.
MARKER_CENTER_TOL_PX = 5.0
MARKER_CENTER_STABLE_FRAMES = 30
#: HSV 중점의 조명·마스크 노이즈를 줄이는 EMA 저역통과필터 계수.
MARKER_CENTER_LPF_ALPHA = 0.35
#: 실측(2026-08-05): marker_not_found(못 봄) 없이 매 프레임 계속 찾았는데도
#: marker_timeout(못 붙잡음) 이 났다 — 필터 안 거친 미분항이 픽셀 노이즈에
#: 그대로 반응해 ±5px 안에서 30프레임 연속을 못 버텼을 가능성이 크다(codex 리뷰도
#: 같은 지점을 지적함). 위치(filtered_u)처럼 미분항도 EMA 로 눌러 노이즈에 덜
#: 흔들리게 한다.
MARKER_SERVO_DERIV_LPF_ALPHA = 0.35
#: 마커를 "못 봤다"고 포기하기까지 버티는 시간.
#:
#: 실측 이력(2026-08-05) — 이 값 때문에 재관측(CENTER2)이 두 번 즉사했다:
#:   1. 유예가 아예 없던 판(딱 한 프레임만 놓쳐도 그 자리서 실패). frame_empty/
#:      frame_stale 은 FRAME_STALE_SEC 유예를 주는데 "못 찾음"만 없었다 → 0.4초 도입.
#:   2. 그 0.4초도 짧았다. **실패 순간(0.2s old) 화면 캡처를 보니 카메라는 이미 서가를
#:      보고 있었고 화면이 회전 잔상으로 뭉개져 있었다** — 직전 회전이 끝난 뒤 카메라
#:      프레임(UDP 경유라 지연이 있다)이 아직 회전 중 장면이었고, 그 흐린 프레임에서
#:      마커를 못 찾자 0.4초 만에 죽었다. 자세는 맞았는데 "보이기 전에" 포기한 것이다.
#: 그래서 넉넉히 잡는다. 이 시간 동안은 아래 `marker_search_angular()` 로 좌우를
#: 훑으므로 가만히 기다리기만 하는 게 아니다.
MARKER_LOST_GRACE_SEC = 10.0
#: 마커를 놓쳤을 때 훑는 각속도(rad/s)와 편도 폭(rad). 폭은 EXTRA_TURN_RAD(0.3491,
#: "표식을 화각에 넣기 위한 추가 회전")와 같은 자릿수로 둔다 — 그 각도만큼 어긋나서
#: 화각을 벗어나는 게 실측된 실패 모양이었으니, 그 정도는 훑어야 다시 잡는다.
MARKER_SEARCH_ANG = 0.15
MARKER_SEARCH_HALF_SPAN_RAD = 0.35
#: 실측(2026-08-05, 실기 영상 2회): err 가 ±5px(MARKER_CENTER_TOL_PX) 안까지 들어와도
#: (-4.7px, -10.3px 관측) stable_frames 가 2 정도 찍었다가 0으로 도로 튄다 — 경계
#: 근처에서 들락날락하다 10초 안에 30프레임 연속을 못 채우고 marker_timeout 이 났다.
#: D항 필터로도 못 없앤 잔여 지터가 있는 걸로 보여 우선 여유 시간을 늘린다.
#: ⚠️ MARKER_LOST_GRACE_SEC(탐색 시간)보다 넉넉히 커야 한다 — 안 그러면 훑다가
#: 마커를 되찾은 순간 곧바로 전체 시간이 끝나 정렬할 시간이 없다.
MARKER_SERVO_TIMEOUT_SEC = 30.0
MARKER_SERVO_HZ = 15.0
MARKER_SERVO_KP = 0.45
MARKER_SERVO_KI = 0.02
MARKER_SERVO_KD = 0.08
MARKER_SERVO_MAX_ANG = 0.12

#: 지도 좌표 폐루프(옆축) 설정. AMCL 투영 오차가 이 값 안에 연속으로 들어와야
#: 다음 단계로 간다. 명령은 cmd_vel_dock 으로만 나가므로 Nav2와 충돌하지 않는다.
#: 실측(2026-08-05, 서보잉 자체는 정상, LAT MOVE 에서 반복 pose_stale): AMCL 은
#: `update_min_d`(nav2_params.yaml, 0.02m) 이상 움직여야만 새 pose 를 낸다. 예전
#: 값 0.01m 은 이보다 작아서, 필요한 옆축 이동이 2cm 미만(실측 0.1cm/2.1cm)이면
#: AMCL 이 원리상 다시는 갱신을 안 낸다 — 아무리 기다려도 SENSOR_STATE_STALE_SEC
#: 을 넘겨 pose_stale 로 죽는다(센서 문제가 아니라 못 만족하는 조건이었다). 이후
#: 카메라 재중앙정렬(CENTER2)이 잔여 옆축 오차를 다시 잡아주므로, 이 축의 목표는
#: AMCL 이 실제로 확인할 수 있는 값이면 충분하다 — update_min_d 보다 여유 있게 크게.
MAP_AXIS_TOL_M = 0.025
MAP_AXIS_STABLE_TICKS = 5
MAP_AXIS_TIMEOUT_SEC = 15.0
MAP_AXIS_KP = 0.8
MAP_AXIS_MAX_LINEAR_MPS = 0.06
MAP_AXIS_HEADING_KP = 1.2
MAP_AXIS_MAX_ANG = 0.20

#: 마지막 서가 법선축 접근은 카메라·PGM을 매 tick 재관측한다. PGM 격자 해상도보다
#: 지나치게 작은 종료 오차는 의미가 없으므로 clearance 뒤 5 mm 창만 허용한다.
FINAL_APPROACH_TOL_M = 0.005
FINAL_APPROACH_STABLE_TICKS = 3
FINAL_APPROACH_TIMEOUT_SEC = 20.0
FINAL_APPROACH_KP = 0.8
FINAL_APPROACH_MAX_LINEAR_MPS = 0.05
SENSOR_STATE_STALE_SEC = 0.75
#: 실측(2026-08-05): center_marker_pid()가 정렬 확인을 위해
#: MARKER_CENTER_STABLE_FRAMES(30)프레임 연속 정지해야 한다(15Hz 기준 ~2초).
#: AMCL은 로봇이 안 움직이면 새 pose 를 안 낸다 — 그 정지 구간 직후에도
#: SENSOR_STATE_STALE_SEC(0.75초) 기준으로 재면 매번 "오래됐다"로 걸린다
#: (센서/네트워크 문제가 아니라 설계상 시간 불일치). 정렬 직후 확인에만
#: 이 값을 쓴다 — 실제 이동 중 안전 정지(옆축/최종 PID 루프)는
#: SENSOR_STATE_STALE_SEC 그대로 둔다.
POST_CENTER_STALE_SEC = 3.0
#: nav2_params.yaml 의 AMCL `update_min_d`(:44)/`update_min_a`(:33). AMCL 은 로봇이
#: **이만큼 움직여야/돌아야** 새 pose 를 낸다 — 그보다 조금 움직인 동안 pose 가
#: "안 오는" 건 고장이 아니라 설계대로다. 두 값이 아래 `blind_travel_stale_sec()`
#: 의 기준이 된다. (실기 파라미터 복제 — 그쪽이 바뀌면 여기도 같이 본다.)
AMCL_UPDATE_MIN_D_M = 0.02
AMCL_UPDATE_MIN_A_RAD = 0.02

#: AMCL 이 조용한 동안 odom 으로 이어 붙여도 되는 한도(거리/각도).
#:
#: 2026-08-05 에 `amcl_stale` 을 네 번 고쳤는데 5/11 · 10/11 · 11/11 단계로 자리만
#: 옮겨 다녔다. 넷 다 "**언제/무엇으로** 신선도를 재나" 를 만졌지, 전제를 안 봤다 —
#: `/amcl_pose` 는 **이벤트 토픽**이다. 위 두 값만큼 움직여야 나오므로, "최근
#: 메시지가 있나" 로 "지금 어디 있나" 를 판정하는 건 원리상 성립하지 않는다.
#:
#: 실제로 LAT MOVE 실패 경로(`turn_to_map_yaw`)에서 4차 수정은 여유를 **하나도**
#: 안 줬다: `max(0.75, blind_travel_stale_sec(MAP_YAW_ANG=0.4, ..., 0.02))` 는
#: `max(0.75, 0.05)` = 0.75초 — 빠르게 돌수록 거리 기준이 더 짧아져 옛 바닥값이
#: 그대로 남는다.
#:
#: AMCL 의 map→odom 보정은 갱신 **사이에는 상수**다(그게 그 변환의 정의다). 그러니
#: `현재 map 자세 = 마지막 AMCL fix ⊕ 그 뒤 odom 증분` 이 정확하다. 자세를 못 아는
#: 진짜 경우는 **odom 이 끊긴 것**(이미 `odom_is_fresh()` 가 따로 본다) 하나뿐이다.
#:
#: 이 한도는 "AMCL 이 진짜 죽었다" 를 놓치지 않기 위한 것뿐이다. 도킹 옆축 이동이
#: 실측 10~15cm 이고 AMCL 은 정상이면 2cm 마다 오므로 정상 동작에서는 안 걸린다.
#: **AMCL 을 안 쓰겠다는 게 아니다** — 매 tick AMCL 을 그대로 읽고, 갱신과 갱신
#: 사이의 빈 구간만 odom 으로 메운다. 그 구간이 이 거리를 넘으면 AMCL 이 정말
#: 이상한 것이므로 그때는 예전처럼 실패한다.
#:
#: **각도 한도는 일부러 없다.** 처음엔 1.0rad 로 뒀는데 시험이 바로 잡았다 — 도킹은
#: 옆축 회전 90°, 마지막 자세 회전 최대 180°(FINAL_YAW_RAD) 를 도므로 그 한도가
#: 정상 회전 도중에 걸려 **같은 버그를 되심는 값**이었다. 그리고 회전 뒤엔 반드시
#: 이동이 따라오므로 AMCL 이 정말 죽었으면 아래 거리 한도가 어차피 잡는다.
#:
#: **시간 한도도 일부러 없다** — 정지 중엔 AMCL 이 안 오는 게 정상이고 그때
#: dead reckoning 오차는 0 이다(그게 이 버그의 절반이었다).
AMCL_DEAD_RECKON_MAX_M = 0.30

#: map 프레임 절대 yaw 로 도는 닫힌 루프 회전 설정.
#:
#: 실측(2026-08-05): 예전엔 회전을 전부 `Move(TURN, 상대각)` 으로 냈다 — 목표는 map
#: 절대 자세인데 실행은 **odom 적분 상대 회전**이라, 그 사이 오차가 그대로 남는다.
#: 회전이 여러 번(서가 방향 → 옆축 방향 → 서가 방향 → 최종 자세) 이어지면서 누적돼
#: 카메라가 엉뚱한 데를 봤다("다른 거 보고 갔어"). 매 tick AMCL yaw 를 다시 읽어
#: 목표까지 닫는다.
#:
#: ⚠️ `MAP_YAW_TOL_RAD` 는 `AMCL_UPDATE_MIN_A_RAD` 보다 커야 한다 — AMCL 이 그보다
#: 잘게는 알려주지 않으므로, 작게 잡으면 **원리상 만족 못 하는 조건**이 된다
#: (MAP_AXIS_TOL_M 이 update_min_d 보다 작아 pose_stale 이 반복됐던 사고와 같은 모양).
MAP_YAW_TOL_RAD = 0.05
MAP_YAW_STABLE_TICKS = 3
MAP_YAW_TIMEOUT_SEC = 20.0
#: 회전 각속도(rad/s). **P 제어로 줄이지 않고 고정값으로 낸다.**
#:
#: 실측(2026-08-05): 처음엔 P 제어(KP=1.2, 하한 0.08)로 짰는데 **로봇이 아예 안 돌았다**
#: — 목표에 가까워질수록 명령이 0.08 rad/s 까지 떨어지는데 그 속도로는 바퀴가 정지마찰을
#: 못 이긴다. 안 돌면 AMCL 이 새 pose 를 안 내고, 그러면 오차도 안 줄어 20초 타임아웃까지
#: 그대로 서 있었다.
#:
#: 그래서 이 레포에서 **실제로 돌던 값**을 그대로 쓴다 — `MoveExecutor` 의
#: `turn_speed=0.4`(`app/core/backup_runner.py:102`). 그쪽도 P 제어가 아니라 고정 속도
#: bang-bang 이다. 한 tick(1/MARKER_SERVO_HZ)에 0.4/15 ≈ 0.027 rad 도는데 이는
#: `MAP_YAW_TOL_RAD`(0.05)보다 작아, 허용오차 안에서 멈출 수 있다(오버슛으로 영영
#: 왔다갔다 하지 않는다).
MAP_YAW_ANG = 0.4
#: 목표가 현재와 **정확히 반대편**일 때 방향을 고정할 폭(rad).
#: `wrap_pi` 범위가 `[-π, π)` 라 그 경계에서는 오차 부호가 AMCL 잡음으로 뒤집힌다.
#: 이 폭 안에서는 좌우 회전량이 사실상 같으므로(차이 ≤ 2×이 값) 어느 쪽을 골라도
#: 손해가 없다 — 대신 늘 같은 쪽으로 고정해 재현 가능하게 만든다.
#: 한 tick 회전량(0.4/15≈0.027rad)보다 넉넉히 커야 경계를 한 번에 벗어난다.
MAP_YAW_ANTIPODE_MARGIN_RAD = 0.10
#: 회전이 끝난 **뒤** 카메라 프레임이 그 자세를 담을 때까지 기다리는 시간.
#: 실측(2026-08-05): 회전 직후 프레임은 UDP 지연 + 모션 블러로 아직 회전 중 장면이라,
#: 그걸로 마커를 찾으면 엉뚱한 걸 잡거나 못 찾고 죽는다. 이 시간 **이후에 찍힌**
#: 프레임만 탐지에 쓴다(`center_marker_pid(not_before=...)`).
TURN_SETTLE_SEC = 0.6
#: GUI 로그에 최종 PGM 거리를 갱신하는 최대 주기. 제어 주기(15Hz)를 그대로
#: 기록하면 관리자 로그가 넘치므로, 관측 자체는 계속 쓰되 화면 보고만 제한한다.
DOCK_STATUS_UPDATE_SEC = 0.5

#: 현장 확인용: PID 중앙 정렬 뒤 전진하지 않고 도킹을 종료한다.
#: 테이프 정렬을 확인한 뒤 False로 돌려 전체 도킹을 재개한다.
VISUAL_SERVO_ONLY = False


def visual_servo_angular_z(error: float, integral: float, derivative: float) -> float:
    """정규화한 테이프 중점 오차(-1..1)의 PID 각속도 명령.

    화면 오른쪽 오차는 로봇을 오른쪽(음의 yaw)으로 돌려야 하므로 음수 부호를
    붙인다. 적분·미분항은 프레임 간 실제 시간으로 계산하는 호출자가 준다.
    """
    command = -(MARKER_SERVO_KP * float(error)
                + MARKER_SERVO_KI * float(integral)
                + MARKER_SERVO_KD * float(derivative))
    return max(-MARKER_SERVO_MAX_ANG, min(MARKER_SERVO_MAX_ANG, command))


def ema(prev: float | None, raw: float, alpha: float) -> float:
    """1차 지수이동평균 저역통과필터. `prev` 없으면(첫 샘플) `raw` 그대로."""
    return raw if prev is None else alpha * raw + (1.0 - alpha) * prev


def marker_search_angular(lost_for_sec: float) -> float:
    """마커를 놓친 지 `lost_for_sec` 초일 때 낼 탐색 각속도(rad/s).

    실측(2026-08-05): 못 찾으면 그 자리에 **가만히 서서** 유예 시간만 세다가
    죽었다 — "찾으러 가지도 않고 못 찾았다고 한다". 화각을 조금 벗어난 게 원인인
    실패였으니 좌우로 훑어야 다시 잡는다.

    놓친 자리를 중심으로 ±`MARKER_SEARCH_HALF_SPAN_RAD` 를 왕복하는 **삼각파**다.
    한 주기를 다 돌면 적분값이 0 — 즉 원래 보던 방향으로 돌아온다. 한 방향으로
    계속 도는 방식이면 못 찾았을 때 로봇이 영영 엉뚱한 데를 보고 끝난다.
    """
    quarter = MARKER_SEARCH_HALF_SPAN_RAD / MARKER_SEARCH_ANG   # 중앙 → 한쪽 끝
    phase = lost_for_sec % (4.0 * quarter)
    outbound = phase < quarter or phase >= 3.0 * quarter
    return MARKER_SEARCH_ANG if outbound else -MARKER_SEARCH_ANG


def shelf_axes(shelf: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """`(법선축, 옆축)` 단위벡터. 옆축은 서가 표면과 평행한 왼쪽 방향이다."""
    yaw = SHELF_YAW[shelf]
    normal = (math.cos(yaw), math.sin(yaw))
    lateral = (-math.sin(yaw), math.cos(yaw))
    return normal, lateral


def axis_projection(x: float, y: float, axis: tuple[float, float]) -> float:
    """맵 좌표를 `axis` 위의 스칼라 좌표로 투영한다."""
    return float(x) * axis[0] + float(y) * axis[1]


def is_pose_fresh(at: float | None, now: float, stale_sec: float) -> bool:
    """마지막 수신 시각 `at` 이 `now` 기준 `stale_sec` 안이면 신선하다.
    `at` 이 아예 없으면(아직 한 번도 안 옴) 당연히 신선하지 않다."""
    return at is not None and (now - at) <= stale_sec


def blind_travel_stale_sec(speed_mps: float, cap_sec: float,
                           max_blind_m: float = AMCL_UPDATE_MIN_D_M) -> float:
    """지금 속도로 `max_blind_m` 를 지나는 데 걸리는 시간 = pose 없이 버텨도 되는 한도.

    **왜 시간이 아니라 거리로 재나** — AMCL 은 로봇이 `update_min_d`(=`max_blind_m`)
    만큼 움직여야 새 pose 를 낸다. 그러니 고정된 시간으로 신선도를 재면 **느리게 갈수록
    억울하게 걸린다.** 실측(2026-08-05, 9/11 단계까지 다 통과한 뒤 FINAL MOVE 에서만
    amcl_stale): 마지막 접근은 6cm 를 PID 로 좁히며 끝에선 4mm/s 까지 느려지는데,
    그 속도로 2cm 를 가려면 5초가 걸린다 — AMCL 이 그 5초 동안 조용한 건 정상인데
    0.75초 기준이 매번 먼저 걸렸다. 게다가 걸리면 멈춰서 기다리므로 **안 움직여서
    AMCL 이 영영 안 오는** 교착이었다(MAP_AXIS_TOL_M 이 update_min_d 보다 작아
    생겼던 사고와 같은 모양이다).

    거리로 재면 그 모순이 사라진다 — 빠르면 짧게, 느리면 길게, 멈춰 있으면 `cap_sec`
    까지. 뜻은 "pose 없이 `max_blind_m` 이상은 못 간다" 이고, 그게 AMCL 이 어차피
    보장하는 최선의 해상도다.
    """
    speed = abs(float(speed_mps))
    if speed <= 1e-6:
        return cap_sec           # 안 움직이면 옛 pose 도 여전히 맞다(로봇이 그 자리다)
    return min(cap_sec, max_blind_m / speed)


def map_pose_from_odom(amcl_pose, odom_at_fix, odom_now,
                       max_m: float = AMCL_DEAD_RECKON_MAX_M):
    """마지막 AMCL fix 에 그 뒤 odom 증분을 얹은 **현재** map 자세. 한도 밖이면 `None`.

    `T_map_base(지금) = T_map_base(fix) · T_odom_base(fix)⁻¹ · T_odom_base(지금)`
    — 가운데 `T_map_odom = T_map_base(fix) · T_odom_base(fix)⁻¹` 이 AMCL 이 갱신
    사이에 붙들고 있는 바로 그 상수 보정이다. 그래서 AMCL 이 조용해도 자세는
    정확히 안다(정지 중이면 증분이 0 이라 fix 그대로 나온다).

    한도(`AMCL_DEAD_RECKON_MAX_*` 머리말)는 AMCL 이 정말 죽은 경우만 걸러낸다.
    """
    if amcl_pose is None or odom_at_fix is None or odom_now is None:
        return None
    ax, ay, ayaw = amcl_pose
    ox, oy, oyaw = odom_at_fix
    nx, ny, nyaw = odom_now
    dx, dy = float(nx) - float(ox), float(ny) - float(oy)
    dyaw = wrap_pi(float(nyaw) - float(oyaw))
    if math.hypot(dx, dy) > max_m:
        return None
    c, s = math.cos(ayaw - oyaw), math.sin(ayaw - oyaw)
    return (ax + dx * c - dy * s, ay + dx * s + dy * c, wrap_pi(ayaw + dyaw))


def resolve_map_pose(amcl_state: dict, odom_state: dict, now: float,
                     stale_sec: float = SENSOR_STATE_STALE_SEC):
    """`current_amcl()` 의 판정 본체. ROS 없이 그대로 시험할 수 있게 여기 둔다.

    ⚠️ **이 함수가 실기에서 도는 바로 그 코드다.** 예전엔 판정이 `_run()` 안의
    클로저 안에만 있어서 시험이 닿질 못했고, `amcl_stale` 네 번의 수정이 전부
    실기에서만 드러났다. 시험이 사본을 때리면 사본의 가정을 검증할 뿐이다.

    AMCL 이 여전히 주인이다 — 위치의 절대 기준은 언제나 마지막 AMCL fix 이고,
    odom 은 그 fix 이후의 **증분만** 얹는다. AMCL 이 새로 오면 즉시 그쪽으로 갈린다.

    ⚠️ **신선해도 이어 붙인다.** 처음엔 "기준 안이면 원본 그대로" 로 뒀는데
    도킹 한 판 재현 시험이 잡았다 — 그 원본은 최대 `stale_sec`(0.75초) 묵은 값이라
    그 사이 이동분이 통째로 오차가 된다(실측 재현: **정확히 2cm**, AMCL 이 2cm 마다
    내니까). 이어 붙인 값은 같은 fix 에 실제 이동분을 더한 것이라 언제나 원본보다
    정확하다. 그래서 갈래를 없애고 항상 이어 붙인다 — 경로도 하나로 줄어든다.
    """
    pose = amcl_state.get("pose")
    if pose is None:
        return None                       # AMCL 을 한 번도 못 받았다
    if is_pose_fresh(odom_state.get("at"), now, SENSOR_STATE_STALE_SEC):
        composed = map_pose_from_odom(pose, amcl_state.get("odom"),
                                      odom_state.get("pose"))
        if composed is not None:
            return composed
    # 이어 붙일 수 없다(odom 끊김 · fix 시점 odom 없음 · 한도 초과).
    # 그래도 AMCL 원본이 기준 안이면 그것만이라도 쓴다 — 옛 동작 그대로.
    return pose if is_pose_fresh(amcl_state.get("at"), now, stale_sec) else None


def bounded_pid_linear(error_m: float, kp: float, max_speed: float) -> float:
    """거리 오차 P항을 안전 속도 범위로 제한한다.

    목표를 지나친 경우에도 부호를 유지해 천천히 되돌린다. 호출자는 항상 최신
    AMCL/PGM 관측을 다시 읽으므로, 이 함수는 상태를 저장하지 않는 것이 안전하다.
    """
    raw = float(kp) * float(error_m)
    limit = abs(float(max_speed))
    return max(-limit, min(limit, raw))


def map_heading_error(target_yaw: float, current_map_yaw: float) -> float:
    """map 프레임 목표 방위와 AMCL 방위의 최단 각도 오차."""
    return wrap_pi(float(target_yaw) - float(current_map_yaw))


def map_turn_angular(target_yaw: float, current_map_yaw: float,
                     ang: float = MAP_YAW_ANG, tol: float = MAP_YAW_TOL_RAD,
                     antipode_margin: float = MAP_YAW_ANTIPODE_MARGIN_RAD) -> float:
    """이 tick 에 낼 각속도. 허용오차 안이면 `0.0`.

    `turn_to_map_yaw()` 의 판정 본체다 — 그 함수는 `_run()` 안 클로저라 시험이 못
    닿는다(`resolve_map_pose` 와 같은 이유로 밖으로 뺐다). **회전은 언제나 map
    절대 좌표 기준이다**: 매 tick 목표와 현재 AMCL yaw 의 최단 오차만 보고,
    그동안 얼마나 돌았는지는 안 센다. 그래서 중간에 밀려도 그 tick 에 회수된다.

    P 제어로 줄이지 않는다 — `MAP_YAW_ANG` 주석 참고(느려지면 정지마찰을 못 이긴다).

    ⚠️ **반대편(|오차|≈π) tie-break** — `wrap_pi` 의 범위는 `[-π, π)` 라, 목표가
    현재와 정확히 반대편이면 오차가 `-π` 쪽으로 접힌다. 그 경계에서는 AMCL 이
    몇 mrad 만 흔들려도 부호가 뒤집혀 **좌우가 tick 마다 갈릴 수 있다**(codex 지적,
    2026-08-05). 좌우 어느 쪽이든 회전량은 같으므로 어느 쪽을 골라도 손해가 없다 —
    대신 **항상 같은 쪽(양수, 왼쪽)** 으로 고정해서 재현 가능하게 만든다.
    현재 서가 배치에서는 시작 오차가 ±1.2~1.9rad 라 여기 안 걸리지만, 정점이
    늘거나 시작 자세가 달라지면 걸리는 자리다.
    """
    error = map_heading_error(target_yaw, current_map_yaw)
    if abs(error) <= tol:
        return 0.0
    if abs(error) >= math.pi - antipode_margin:
        return abs(float(ang))          # 경계에서는 부호를 AMCL 잡음에 맡기지 않는다
    return math.copysign(float(ang), error)


def dock_status_payload(shelf: str, phase: str, **fields) -> str:
    """GUI가 구독하는 도킹 상태 JSON. ROS·Qt 없이 형식을 단위 시험할 수 있다."""
    body = {"event": "shelf_dock", "shelf": str(shelf), "phase": str(phase), **fields}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def ray_yaw(robot_yaw: float, bearing: float) -> float:
    """레이를 쏠 방향. 화면 오른쪽(양수 bearing)은 yaw 를 줄인다."""
    return wrap_pi(float(robot_yaw) - float(bearing))


def camera_center_and_bearing(u: float, k_calib, calib_width: int, frame_width: int) -> tuple[float, float]:
    """영상 중앙 기준과 지도 광선 보정각.

    현장 비교 모드에서는 K를 전혀 쓰지 않는다. 테이프를 영상 정중앙에 PID로
    맞췄으므로 bearing은 0이고 광선은 AMCL yaw 방향으로 간다.
    """
    if not USE_CAMERA_CALIBRATION:
        return float(frame_width) / 2.0, 0.0
    fx, _fy, cx, _cy = scale_k(k_calib, calib_width, frame_width)
    return cx, bearing_rad(u, fx, cx)


def plan_dock(shelf: str, robot_pose, frame, grid, k_calib, frame_width: int,
              calib_width: int = 640):
    """도킹 이동 계획. `(moves, info)` 또는 `(None, info)`.

    `robot_pose` 는 `(x, y, yaw)`. 이 함수를 부르기 전에 로봇은 이미
    `SHELF_YAW[shelf] + EXTRA_TURN_RAD` 자세로 서 있어야 한다.
    """
    info: dict = {"shelf": shelf}
    if shelf not in SHELF_YAW:
        info["error"] = f"unknown shelf: {shelf}"
        return None, info

    u = centroid_u(frame)
    if u is None:
        info["error"] = "marker not found"
        return None, info
    info["u_px"] = u

    _cx, bearing = camera_center_and_bearing(u, k_calib, calib_width, frame_width)
    info["bearing_rad"] = bearing

    rx, ry, ryaw = robot_pose
    yaw = ray_yaw(ryaw, bearing)
    info["ray_yaw_rad"] = yaw

    hit = first_occupied(grid, rx, ry, yaw, max_m=MAX_RANGE_M)
    if hit is None:
        info["error"] = "raycast found no wall"
        return None, info
    (hx, hy), dist = hit
    info["hit_xy"] = (hx, hy)
    info["hit_dist_m"] = dist

    # 충돌점은 서가 표면이다. 서가를 보는 방향의 반대로 1cm 물러난 지점을
    # 목표로 잡고, x축 뒤 y축 순서로 접근한다.
    approach_x = hx - CLEARANCE_M * math.cos(SHELF_YAW[shelf])
    approach_y = hy - CLEARANCE_M * math.sin(SHELF_YAW[shelf])
    info["approach_xy"] = (approach_x, approach_y)
    moves = axis_aligned_moves(rx, ry, ryaw, approach_x, approach_y,
                               final_yaw=FINAL_YAW_RAD)
    info["moves"] = [(m.kind, m.value) for m in moves]
    return moves, info


# ══════════════════════════════════════════════════════════════════════════
# 실행부 — 위 `plan_dock` 은 순수 함수로 그대로 둔다. 여기서부터는 ROS 로 실제로
# 로봇을 돌린다. 구조는 `app/core/marker_dock.py` 를 그대로 따른다: 전용 노드 +
# `spin_once`, `/dev/shm` 프레임 탭, `cmd_vel_dock` 발행, 취소는 구독 콜백이 세대
# 번호로 세우고 이 루프가 tick 마다 읽는다.
# ══════════════════════════════════════════════════════════════════════════

#: 앞캠 640x480 보정값을 이 프레임 폭(320)에 맞춰 `scale_k` 가 스케일한다.
FRONT_CAM_K_640 = (609.15651744, 607.39537016, 278.17496904, 250.36175645)

#: 이보다 오래된 앞캠 프레임은 못 믿는다 (marker_dock.FRAME_STALE_SEC 와 같은 값·이유).
#
# ⚠️ 이 값과 비교하는 "지금"은 **`time.monotonic()`** 이어야 한다 — 프레임 탭의
#    stamp 가 그 시계다(`aba_ai_service/follower_perception/scripts/frame_tap.py`
#    `write()`). `time.time()`(epoch) 과 섞으면 두 시계의 기준점이 달라 차이가
#    억 단위로 벌어져서 **항상 stale** 로 판정된다 — 표식을 영영 못 보고 도킹이
#    매번 frame_stale 로 실패한다(2026-08-04 리뷰 P0, 실측 재현. `marker_dock.py`
#    는 원래부터 `time.monotonic()` 을 쓴다).
FRAME_STALE_SEC = 0.4

#: 센서/프레임을 기다리는 상한(초).
SENSOR_WAIT_SEC = 5.0
FRAME_WAIT_SEC = 2.0

#: `/libi/camera_select` 재발행 주기(초) — 송출기 만료(3.0초, `libi_perception.config.
#: CAMERA_SELECT_EXPIRY_SEC`)의 절반 이하. `libi_modes/common/person_block.py` 의
#: `_CAMERA_RENEW_SEC` 와 같은 값·같은 이유.
#
# ⚠️ 왜 여기서 또 보내나 — `PersonBlockGuard`(주행 중 앞캠 요청)는
#    `active_command == "navigate"` 일 때만 동작한다. 도킹 중엔 `active_command`
#    가 `"shelf_dock"` 이라 그 가드가 멈추고, **아무도 앞캠을 안 잡는다.**
#    `camera_sender` 는 선택 안 된 캠을 8틱에 한 번(≈1.9Hz)만 보므로 `FRAME_STALE_SEC`
#    (0.4초) 판정에 늘 걸린다(2026-08-04 리뷰 P1).
CAMERA_RENEW_SEC = 1.0


def _should_renew_camera(last_sent_at: float | None, now: float,
                         renew_sec: float = CAMERA_RENEW_SEC) -> bool:
    """`/libi/camera_select` 를 다시 보낼 때가 됐나 — 순수 함수(`person_block.py` 의
    `_request_camera` 와 같은 판단을 ROS 없이 시험하려고 분리했다)."""
    return last_sent_at is None or (now - last_sent_at) >= renew_sec


def _wait_for_fresh_frame(read_tap_fn, now_fn, sleep_fn, deadline_sec: float,
                          stale_sec: float, on_tick=lambda: None):
    """`read_tap_fn()` 이 주는 `(frame, seq, stamp)` 중 신선한 것을 기다린다. 없으면
    `None`.

    순수 함수 — `read_tap_fn`/`now_fn`/`sleep_fn` 을 주입받아 ROS 없이 시험한다.

    ⚠️ `now_fn` 과 `stamp` 는 **같은 시계**여야 한다(위 `FRAME_STALE_SEC` 주석).
    """
    deadline = now_fn() + deadline_sec
    while now_fn() < deadline:
        on_tick()
        got = read_tap_fn()
        if got is not None:
            frame, _seq, stamp = got
            if now_fn() - stamp <= stale_sec:
                return frame
        sleep_fn(0.05)
    return None

_lock = threading.Lock()
_running = False
_gen = 0                #: 도킹을 시작할 때마다 오른다
_cancel_gen = -1         #: 취소를 요청받은 세대


def request_cancel() -> bool:
    """진행 중인 서가 도킹을 끊는다. `fleet_link` 의 **구독 콜백**에서 부른다."""
    global _cancel_gen
    with _lock:
        if not _running:
            return False
        _cancel_gen = _gen
        return True


def _cancelled(my_gen: int) -> bool:
    with _lock:
        return _cancel_gen == my_gen


def is_running() -> bool:
    with _lock:
        return _running


def unlock_payload(args: dict) -> dict | None:
    """`args["node"]` 로 **즉시 해제**(`ttl_sec=0`) `/libi/node_block` payload 를 만든다.

    순수 함수 — 발행은 호출자(ROS 층)의 몫이다. `node` 가 없으면 `None` 을 돌려주고,
    호출자는 발행을 건너뛴 채 경고를 남겨야 한다 — 조용히 넘어가면 그 서가 정점이
    사람이 걸어 둔 TTL 만료까지 잠긴 채로 남는다.

    수신측 계약: `NodeBlockRegistry.set`(`aba_fms_service/backend/app/node_block.py`)
    — `ttl_sec <= 0` 은 이 owner(`reason`)의 차단만 푼다.
    """
    node = args.get("node")
    if node is None:
        return None
    return {"node": int(node), "ttl_sec": 0.0, "reason": "shelf_dock"}


def _release_lock_before_moving(args: dict, publish_fn, warn_fn) -> tuple[int | None, float | None]:
    """`plan_dock` 의 성공/실패를 **아예 모른 채** 잠금 해제를 시도한다.

    `_run` 이 이 함수를 `plan_dock` 결과를 보기 **전에** 한 번만 호출한다 — 그래서
    "성공 경로"/"실패 경로"가 따로 없다. 이 함수 자체가 성공/실패 분기를 안 가지므로
    실패 경로에서 빠뜨릴 방법이 구조적으로 없다(2026-08-04 리뷰 P0-Important 대응).

    `publish_fn(payload)` / `warn_fn(msg)` 을 주입받아 ROS 없이 시험한다. `(unlocked_
    node, unlocked_at)` — 둘 다 `None` 이면 못 보낸 것(그때는 `warn_fn` 이 이미
    불렸다).
    """
    unlock = unlock_payload(args)
    if unlock is None:
        warn_fn(f"[서가도킹] args 에 node 가 없어 잠금 해제를 못 보낸다: {args.get('shelf')}")
        return None, None
    publish_fn(unlock)
    return unlock["node"], time.time()


def run_shelf_dock(args: dict) -> tuple[bool, int, dict, str]:
    """`shelf_dock` 명령의 실행 진입점. `(ok, status, data, msg)` — fleet_link 계약.

    순서: 서가 yaw 로 회전 → `EXTRA_TURN_RAD` 추가 회전 → 앞캠 프레임·`/map`·`/amcl_pose`
    로 `plan_dock` → **`plan_dock` 성공/실패와 무관하게 잠금 해제를 먼저 알리고**
    (결과가 아니라 별도 토픽 `/libi/node_block` — 정밀 이동이 실제로 시작하는 순간
    알려야 한다. 결과 payload 에 얹으면 `MoveExecutor.run()` 이 끝날 때까지 몇
    초~수십 초를 FMS 가 모른 채 통행을 계속 막고, `plan_dock` 실패 시엔 아예 안
    풀려 TTL 만료까지 잠긴다) → 접근 이동 실행 →
    `record_outbound(moves, heading, final_yaw)`.

    `args["node"]`(서가 정점 번호)가 있어야 잠금 해제를 보낼 수 있다 — 없으면
    경고만 남기고 도킹 자체는 계속 진행한다(`unlock_payload` 참고).
    """
    args = args or {}
    shelf = str(args.get("shelf") or "").strip()
    if shelf not in SHELF_YAW:
        return False, 400, {"docked": False}, f"unknown shelf: {shelf}"

    global _running, _gen
    with _lock:
        if _running:
            return False, 409, {"docked": False}, "서가 도킹이 이미 진행 중이다"
        _gen += 1
        my_gen = _gen
        _running = True
    try:
        return _run(shelf, my_gen, args)
    finally:
        with _lock:
            _running = False


def _run(shelf: str, my_gen: int, args: dict) -> tuple[bool, int, dict, str]:
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
    from nav_msgs.msg import OccupancyGrid, Odometry
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    from app.core import fleet_link
    from app.core.backup_runner import record_return_targets
    from app.core.marker_dock import read_tap
    from app.core.ros_bridge import quat_to_yaw
    from app.shelf.raycast import Grid

    ctx = fleet_link.get_context()
    if ctx is None:
        return False, 503, {"docked": False}, "fleet_link ROS context 가 아직 없다"

    node = rclpy.create_node("shelf_dock_exec", context=ctx)
    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    pub = node.create_publisher(Twist, "cmd_vel_dock", 10)
    # 이동 시작 "전" 잠금 해제 알림 — 결과 payload 와 분리된 별도 채널(위 docstring
    # 참고). FMS 가 이미 구독 중인 로봇발 채널을 그대로 쓴다(새 토픽을 안 만든다).
    node_block_pub = node.create_publisher(String, "/libi/node_block", 10)
    # 관리자 패널은 이 토픽만 읽는다. 제어 루프의 Python 로그를 SSH로 긁지 않는다.
    dock_status_pub = node.create_publisher(String, "shelf_dock_status", 20)
    # 도킹 내내 앞캠을 선택 상태로 유지 — `PersonBlockGuard` 는 navigate 중에만
    # 이 토픽을 갱신하므로 도킹 중(active_command=shelf_dock)엔 우리가 직접 잡아야
    # 한다(위 `CAMERA_RENEW_SEC` 주석, 2026-08-04 리뷰 P1). 구독자가 요구하는 QoS
    # (depth=1·RELIABLE·TRANSIENT_LOCAL, `libi_modes/main.py` 의 `_CAMERA_SELECT_QOS`
    # 와 같은 값)를 맞춘다 — 안 맞으면 /map 과 같은 이유로 메시지가 영영 안 간다.
    camera_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                            durability=DurabilityPolicy.TRANSIENT_LOCAL)
    camera_select_pub = node.create_publisher(String, "/libi/camera_select", camera_qos)
    camera_state: dict = {"sent_at": None}
    log = node.get_logger()

    map_state: dict = {}
    amcl_state: dict = {}
    odom_state: dict = {}

    def _on_map(msg) -> None:
        map_state["grid"] = Grid(
            data=list(msg.data), width=msg.info.width, height=msg.info.height,
            resolution=msg.info.resolution,
            origin_x=msg.info.origin.position.x, origin_y=msg.info.origin.position.y)
        map_state["at"] = time.monotonic()

    def _on_amcl(msg) -> None:
        p = msg.pose.pose
        amcl_state["pose"] = (p.position.x, p.position.y, quat_to_yaw(p.orientation))
        amcl_state["at"] = time.monotonic()
        # 이 fix 와 짝이 되는 odom 자세를 같이 남긴다 — 둘의 차이가 map→odom 보정이고,
        # 다음 fix 가 올 때까지 그걸로 자세를 이어 붙인다(`map_pose_from_odom` 참고).
        #
        # ponytail: 짝을 **받은 시각** 기준으로 맞춘다(메시지 stamp 로 보간하지 않는다).
        # AMCL 처리 지연만큼 기준점이 밀리는데, 0.4rad/s 회전이면 100ms 지연 = 약 2°다.
        # 그래도 옛 코드보다 항상 낫다 — 옛 코드는 같은 지연을 안은 pose 를 **전혀
        # 안 굴리고** 그대로 썼다. 이 각도가 문제가 되면 header.stamp 로 odom 을
        # 보간하면 된다(그때 tf2 로 map→odom 을 직접 읽는 쪽이 더 깔끔하다).
        amcl_state["odom"] = odom_state.get("pose")

    def _on_odom(msg) -> None:
        p = msg.pose.pose.position
        odom_state["pose"] = (p.x, p.y, quat_to_yaw(msg.pose.pose.orientation))
        odom_state["at"] = time.monotonic()

    # ⚠️ `/map` 은 절대경로("/map") + TRANSIENT_LOCAL + RELIABLE + depth 1 이어야 한다.
    #    기본 VOLATILE 로 두면 발행자(TRANSIENT_LOCAL)와 QoS 가 안 맞아 메시지를
    #    영영 못 받는다(mismatch — 구독은 되지만 콜백이 한 번도 안 불린다).
    map_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(OccupancyGrid, "/map", _on_map, map_qos)
    # AMCL도 /map처럼 마지막 자세를 TRANSIENT_LOCAL로 보존한다. 도킹은
    # Nav2가 멈춘 직후 시작하므로 기본 VOLATILE 구독이면 새 자세가 올 때까지
    # 기다리다 센서 preflight가 실패할 수 있다.
    node.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", _on_amcl, map_qos)
    node.create_subscription(Odometry, "/odom", _on_odom, 10)

    def spin() -> None:
        for _ in range(4):
            executor.spin_once(timeout_sec=0.0)
        # 도킹 시작부터 끝까지(센서 대기 포함) 앞캠을 계속 선택 상태로 유지한다.
        # `spin()` 은 이 함수 전체에서 tick 마다 불리므로 여기 얹는 게 가장 확실하다.
        now = time.monotonic()
        if _should_renew_camera(camera_state["sent_at"], now):
            camera_select_pub.publish(String(data="front"))
            camera_state["sent_at"] = now

    def report(phase: str, **fields) -> None:
        dock_status_pub.publish(String(data=dock_status_payload(shelf, phase, **fields)))

    def publish(lin: float, ang: float) -> None:
        t = Twist()
        t.linear.x = float(lin)
        t.angular.z = float(ang)
        pub.publish(t)

    def pose_fn():
        # MoveExecutor 가 tick 마다 이 함수를 부른다 — 그 부작용으로 실행 주기(페이싱)와
        # ROS spin 을 겸한다(marker_dock 의 spin()+sleep(period) 과 같은 역할).
        time.sleep(0.02)
        spin()
        return odom_state.get("pose", (0.0, 0.0, 0.0))

    def cancel_fn() -> bool:
        return _cancelled(my_gen)

    def odom_is_fresh() -> bool:
        at = odom_state.get("at")
        return at is not None and time.monotonic() - at <= SENSOR_STATE_STALE_SEC

    def motion_cancel() -> bool:
        # MoveExecutor는 odom 적분으로 진행을 판정한다. odom이 끊긴 상태에서
        # 마지막 twist를 계속 내보내지 않도록, 각 tick 전에 안전 중단한다.
        return cancel_fn() or not odom_is_fresh()

    def motion_failure_reason(why: str) -> str:
        if why == "canceled" and not cancel_fn() and not odom_is_fresh():
            return "odom_stale"
        return why

    #: 서가 정점 잠금 해제 상태. 두 곳에서 부르지만(정밀 이동 직전 · finish) 발행은
    #: 한 번만 나가야 한다 — 두 번 보내도 수신측(NodeBlockRegistry)은 멱등이지만,
    #: `unlocked_at` 이 뒤 호출로 덮여 "언제 풀었나" 기록이 흐려진다.
    unlocked = {"node": None, "at": None, "done": False}

    def ensure_lock_released() -> None:
        """서가 정점 잠금을 **한 번만** 푼다(멱등). 실제 발행은
        `_release_lock_before_moving()` 이 하고, 여기서는 중복만 막는다."""
        if unlocked["done"]:
            return
        unlocked["done"] = True
        n, at = _release_lock_before_moving(
            args, publish_fn=lambda payload: node_block_pub.publish(String(data=json.dumps(payload))),
            warn_fn=log.warning)
        unlocked["node"], unlocked["at"] = n, at

    def current_amcl(stale_sec: float = SENSOR_STATE_STALE_SEC):
        """현재 map 자세. `stale_sec` 은 이제 **원본을 그대로 쓸지**만 정한다.

        기준을 넘으면 실패가 아니라 `map_pose_from_odom()` 으로 이어 붙인다 —
        `/amcl_pose` 가 안 오는 건 고장이 아니라 이벤트 토픽의 설계라서다
        (`AMCL_DEAD_RECKON_MAX_M` 머리말: 이 한 줄이 5·10·11 단계에서 돌아가며
        나던 `amcl_stale` 을 전부 없앤다). 진짜로 모르는 경우만 `None` 이다 —
        AMCL 을 한 번도 못 받았거나, odom 이 끊겼거나, AMCL 없이 이어 붙인
        거리가 한도를 넘었을 때.
        """
        spin()
        return resolve_map_pose(amcl_state, odom_state, time.monotonic(), stale_sec)

    def wait_for_fresh_amcl(stale_sec: float = POST_CENTER_STALE_SEC):
        """`current_amcl()` 한 번만 보고 죽지 않는다. 실측(2026-08-05): 옆축 시작 전
        (`side_start_pose`)·최종축 시작 전(`robot_pose`, 첫/재관측 둘 다)에서 각각
        따로 "그 순간 딱 지나서 stale" 사고가 났다 — 세 자리 다 같은 모양이라
        (POST_CENTER_STALE_SEC 로 봐도 하필 그 tick 에 막 지났으면 그대로 죽었다)
        한 곳에 묶는다. `stale_sec` 동안은 **새 pose 를 기다려 보고**, 그래도 안 오면
        마지막 pose 를 그대로 받아들인다.

        ⚠️ 안 오는 걸 고장으로 보면 안 된다. 이 함수를 부르는 자리는 전부 로봇이
        **정지해 있는** 순간이고(중앙정렬 확인 직후 등), AMCL 은 로봇이 안 움직이면
        새 pose 를 안 내는 게 정상이다. 그리고 그때의 마지막 pose 는 여전히 맞다 —
        로봇이 그 자리에 있으니까. 예전엔 여기서 `None` 을 내서 amcl_stale 로 죽었다
        (실측 2026-08-05: LAT MOVE 직전 반복 실패). `blind_travel_stale_sec` 이
        "속도 0 이면 한도를 cap 까지 연다" 고 한 것과 같은 판단이다."""
        deadline = time.monotonic() + stale_sec
        while True:
            pose = current_amcl(stale_sec)
            if pose is not None:
                return pose
            if time.monotonic() >= deadline:
                return current_amcl(blind_travel_stale_sec(0.0, MAP_AXIS_TIMEOUT_SEC))
            time.sleep(1.0 / MARKER_SERVO_HZ)

    def turn_to_map_yaw(target_yaw: float) -> tuple[bool, str, float]:
        """**map 프레임 절대 yaw** 로 닫힌 루프 회전. `(ok, why, 끝난 시각)`.

        예전엔 `Move(TURN, 상대각)` 으로 냈다 — 목표는 map 절대 자세인데 실행은 odom
        적분 상대 회전이라 그 사이 오차가 남고, 회전이 이어지며 누적됐다(실측
        2026-08-05: "다른 거 보고 갔어"). 여기서는 **매 tick AMCL yaw 를 다시 읽어**
        남은 오차만큼만 돌린다 — 중간에 밀려도 그 tick 에 바로 반영된다.

        AMCL 은 `update_min_a` 만큼 돌아야 새 pose 를 내므로, 신선도 한도도 **각속도
        기준**으로 잡는다(`blind_travel_stale_sec` 머리말 — 직선과 같은 산수다).
        """
        deadline = time.monotonic() + MAP_YAW_TIMEOUT_SEC
        stable = 0
        last_ang = 0.0
        while time.monotonic() < deadline:
            # `motion_cancel()` 을 쓴다(그냥 `cancel_fn()` 이 아니라) — odom 이 끊긴 채
            # twist 를 계속 내보내지 않기 위한 안전 정지가 여기 들어 있다. 예전
            # `mover.run(..., cancel=motion_cancel)` 이 지키던 계약을 그대로 잇는다.
            if motion_cancel():
                publish(0.0, 0.0)
                return False, motion_failure_reason("canceled"), time.monotonic()
            pose = current_amcl(max(SENSOR_STATE_STALE_SEC,
                                    blind_travel_stale_sec(last_ang, MAP_YAW_TIMEOUT_SEC,
                                                           AMCL_UPDATE_MIN_A_RAD)))
            if pose is None:
                publish(0.0, 0.0)
                return False, "amcl_stale", time.monotonic()
            # 판정은 `map_turn_angular()` 하나가 한다(그쪽이 시험되는 코드다).
            ang = map_turn_angular(target_yaw, pose[2])
            if ang == 0.0:
                stable += 1
                if stable >= MAP_YAW_STABLE_TICKS:
                    publish(0.0, 0.0)
                    return True, "", time.monotonic()
                publish(0.0, 0.0)
                last_ang = 0.0
                time.sleep(1.0 / MARKER_SERVO_HZ)
                continue
            stable = 0
            publish(0.0, ang)
            last_ang = ang
            time.sleep(1.0 / MARKER_SERVO_HZ)
        publish(0.0, 0.0)
        return False, "turn_timeout", time.monotonic()

    def finish(ok: bool, status: int, data: dict, msg: str):
        # ⚠️ **여기서 반드시 한 번 더 잠금을 푼다.** `_release_lock_before_moving()` 은
        # `plan_dock` 성공 뒤에야 불리므로, 그보다 앞에서 죽으면(센서 없음, 첫 회전
        # 실패, CENTER1 marker_not_found, …) 해제가 아예 안 나가고 서가 정점이
        # SHELF_DOCK_TTL_SEC(180초) 만료까지 잠긴 채 남는다 — 실측(2026-08-05):
        # "도착했는데 노드 안 사라지고 지연시간에 남냐". finish() 는 성공·실패 통틀어
        # **유일한 출구**라 여기 두면 빠질 경로가 구조적으로 없다.
        # ensure_lock_released() 가 멱등이라 이미 풀었으면 아무 일도 안 한다.
        # ROS 노드를 지우기 **전에** 불러야 발행이 실제로 나간다.
        ensure_lock_released()
        report("completed" if ok else "failed", status=status, message=msg)
        for _ in range(5):
            publish(0.0, 0.0)
            time.sleep(0.02)
        executor.remove_node(node)
        node.destroy_node()
        return ok, status, data, msg

    # ⚠️ 여기부터는 **예상 못 한 예외도 정상 실패로 닫는다.**
    # 실측(2026-08-05, UnboundLocalError 로 도킹이 통째로 죽음): 예외가 그냥 위로
    # 올라가면 세 가지가 한꺼번에 잘못된다 —
    #   1. `finish()` 를 안 거쳐 "failed" 보고가 안 나가 화면이 'started' 에 멈춘다
    #   2. 서가 정점 잠금이 안 풀려 TTL(180초)까지 남는다
    #   3. FMS 는 결과를 실패로 처리해 `task_failed` 를 보내고, 로봇은 PATROL 로
    #      강제 전이된다 — 사용자에겐 "하다가 갑자기 순회로 빠진다" 로 보인다
    # 잡아서 `finish()` 로 보내면 셋 다 정상 경로를 탄다(잠금 해제 + 실패 보고 +
    # 노드 정리). 예외를 삼키는 게 아니라 **사유를 실어 실패로 닫는 것**이다.
    try:
        report("started", clearance_m=CLEARANCE_M)

        # ── 센서 대기 ────────────────────────────────────────────────────────────
        deadline = time.monotonic() + SENSOR_WAIT_SEC
        while not ("grid" in map_state and "pose" in amcl_state and "pose" in odom_state) \
                and time.monotonic() < deadline:
            spin()
            time.sleep(0.05)
        missing = [n for n, ok in (("/map", "grid" in map_state),
                                  ("/amcl_pose", "pose" in amcl_state),
                                  ("/odom", "pose" in odom_state)) if not ok]
        if missing:
            return finish(False, 503, {"docked": False},
                          f"센서가 안 들어온다: {', '.join(missing)}")

        pose_before = amcl_state["pose"]

        # ①② 서가 방향 + 표식을 화각에 넣는 추가각까지 **한 번에** map 절대 yaw 로 간다.
        # 예전엔 상대 회전 두 번(SHELF_YAW 로 한 번, EXTRA_TURN_RAD 만큼 또 한 번)이라
        # 두 번의 odom 오차가 그대로 쌓였다 — 이제 목표 자세 하나로 닫는다.
        ok, why, turned_at = turn_to_map_yaw(SHELF_YAW[shelf] + EXTRA_TURN_RAD)
        if not ok:
            return finish(False, 499 if why == "canceled" else 502, {"docked": False},
                          f"서가 방향 회전 실패: {why}")

        # 회전 중 spin 이 amcl_state 를 계속 갱신했다 — 최신값을 그대로 쓴다(D14: AMCL
        # 오차를 1:1 로 받아들이기로 한 결정. 실측 오차는 아래 로그로 남긴다).
        robot_pose = amcl_state["pose"]

        # ③/⑤ 테이프 중앙정렬은 옆축 이동 전과 후에 각각 수행한다. 두 관측 사이에
        # 이동했으므로 두 번째 결과만 최종 PGM 광선의 기준으로 쓴다.
        def center_marker_pid(phase: str, not_before: float | None = None):
            report("marker_centering", stage=phase)
            frame = None
            trace = []
            last_report_at = 0.0
            last_seq = None
            last_new_at = time.monotonic()
            last_seen_at = time.monotonic()
            prev_error = None
            filtered_u = None
            filtered_derivative = None
            integral = 0.0
            prev_t = None
            stable = 0
            deadline = time.monotonic() + MARKER_SERVO_TIMEOUT_SEC
            while time.monotonic() < deadline:
                spin()
                if cancel_fn():
                    return None, trace, "canceled"
                got = read_tap("front")
                now = time.monotonic()
                if got is None:
                    if now - last_new_at > FRAME_STALE_SEC:
                        return None, trace, "frame_empty"
                    publish(0.0, 0.0)
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                frame, seq, stamp = got
                if now - stamp > FRAME_STALE_SEC:
                    return None, trace, "frame_stale"
                if seq == last_seq:
                    publish(0.0, 0.0)
                    if now - last_new_at > FRAME_STALE_SEC:
                        return None, trace, "frame_not_updating"
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                last_seq, last_new_at = seq, now
                if not_before is not None and stamp < not_before:
                    # 회전이 끝나기 **전에 찍힌** 프레임이다. 카메라는 UDP 를 거쳐 오므로
                    # 회전 직후에도 한동안 회전 중 장면(+모션 블러)이 들어온다 — 그걸로
                    # 마커를 찾으면 화각에 걸친 엉뚱한 초록을 잡거나(실측 2026-08-05:
                    # "다른 거 보고 갔어") 못 찾고 죽는다. 새 자세가 담긴 프레임만 쓴다.
                    publish(0.0, 0.0)
                    last_seen_at = now      # 기다린 시간은 "마커를 놓친" 시간이 아니다
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                # hint_u=filtered_u: 후보가 여럿이면 직전 프레임과 가까운 쪽을 우선한다
                # (실측 2026-08-05: hint 없이 "가장 큰 놈"만 보면 stable_frames 가
                # 22/30까지 갔다가 다른 후보로 갈아타서 한 프레임 만에 0으로 튀었다).
                uv = centroid_uv(frame, hint_u=filtered_u)
                if uv is None:
                    # 실측(2026-08-05): 한 프레임만 놓쳐도 그 자리서 죽었고(유예 도입),
                    # 그 유예(0.4초)조차 짧아 **회전 잔상이 남은 프레임**에서 못 찾은 것만
                    # 으로 즉사했다(MARKER_LOST_GRACE_SEC 주석의 실패 캡처). 이제 넉넉히
                    # 기다리되, 그동안 **가만히 있지 않고 좌우로 훑는다** — 화각을 조금
                    # 벗어난 게 실측된 실패 모양이라 기다리기만 하면 영영 못 찾는다.
                    lost_for = now - last_seen_at
                    if lost_for > MARKER_LOST_GRACE_SEC:
                        publish(0.0, 0.0)
                        return None, trace, "marker_not_found"
                    publish(0.0, marker_search_angular(lost_for))
                    # 훑는 동안 자세가 크게 바뀌므로 서보 상태를 버린다 — 특히 hint_u
                    # (filtered_u)를 들고 있으면 훑고 나서 엉뚱한 옛 위치에 가까운 후보를
                    # 고른다. 되찾으면 cold start 로 다시 잡는 게 맞다.
                    filtered_u = None
                    filtered_derivative = None
                    integral = 0.0
                    prev_error = None
                    prev_t = None
                    stable = 0
                    if now - last_report_at >= DOCK_STATUS_UPDATE_SEC:
                        report("marker_centering", stage=phase, searching=True,
                               lost_for_sec=round(lost_for, 1),
                               grace_sec=MARKER_LOST_GRACE_SEC)
                        last_report_at = now
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                last_seen_at = now
                u, v = uv
                cx, _bearing = camera_center_and_bearing(u, FRONT_CAM_K_640, 640, frame.shape[1])
                raw_u = float(u)
                filtered_u = ema(filtered_u, raw_u, MARKER_CENTER_LPF_ALPHA)
                error = (filtered_u - float(cx)) / (frame.shape[1] / 2.0)
                dt = (now - prev_t) if prev_t is not None else (1.0 / MARKER_SERVO_HZ)
                dt = min(max(dt, 1e-3), 0.5)
                integral = max(-1.0, min(1.0, integral + error * dt))
                raw_derivative = 0.0 if prev_error is None else (error - prev_error) / dt
                filtered_derivative = ema(filtered_derivative, raw_derivative, MARKER_SERVO_DERIV_LPF_ALPHA)
                angular_z = visual_servo_angular_z(error, integral, filtered_derivative)
                aligned = abs(filtered_u - float(cx)) <= MARKER_CENTER_TOL_PX
                stable = stable + 1 if aligned else 0
                trace.append((raw_u, filtered_u, error, angular_z, stable))
                publish(0.0, 0.0 if aligned else angular_z)
                prev_error, prev_t = error, now
                if now - last_report_at >= DOCK_STATUS_UPDATE_SEC:
                    # 실측(2026-08-05): 이 단계는 최대 10초 동안 화면에 아무 값도 안 남아서
                    # "안 보인다" 와 "봤는데 못 붙잡는다" 를 구분할 방법이 없었다 — 실시간으로
                    # marker_error_px/stable 을 남겨서 다음엔 바로 구분되게 한다.
                    report("marker_centering", stage=phase,
                           marker_error_px=round(filtered_u - float(cx), 1),
                           marker_row_px=round(v, 1),
                           stable_frames=stable, tol_px=MARKER_CENTER_TOL_PX)
                    last_report_at = now
                if stable >= MARKER_CENTER_STABLE_FRAMES:
                    log.info(f"[서가도킹][{phase}] 테이프 중앙정렬 완료 yaw_map={amcl_state["pose"][2]:.4f}")
                    return frame, trace, ""
                time.sleep(1.0 / MARKER_SERVO_HZ)
            return None, trace, "marker_timeout"

        def move_lateral_axis_pid(target: float, axis: tuple[float, float],
                                   initial_stale_sec: float = SENSOR_STATE_STALE_SEC):
            # codex 리뷰(2026-08-05)로 잡힘: 호출 직전(`side_start_pose = current_amcl(
            # POST_CENTER_STALE_SEC)`)에 완화된 기준으로 통과시켜놓고, 여기 첫 줄이 바로
            # 기본 0.75초로 되돌아가 같은 정지 구간에서 다시 걸렸다 — 그 사이 실제 이동은
            # 아직 없었다(TURN 도 이 함수 안에서 나간다).
            #
            # ⚠️ 2차 실측(2026-08-05, LAT MOVE 에서 amcl_stale 반복): 호출자 기준
            # (POST_CENTER_STALE_SEC=3초)을 물려받아도 여전히 죽었다. 여기 도달할 때
            # 로봇은 CENTER1 정지 확인(~2초) + LAT PLAN 계산 + 잠금 해제까지 지나
            # **3초 넘게 서 있는** 경우가 흔한데, 정지 중엔 AMCL 이 새 pose 를 안 내는
            # 게 정상이고 마지막 pose 는 여전히 맞다(로봇이 그 자리다). 아직 아무
            # 이동도 안 냈으므로 `blind_travel_stale_sec` 의 "속도 0" 규칙을 그대로
            # 적용한다 — 루프 안(실제 이동 중) 판정은 그 tick 의 속도로 따로 잰다.
            pose = current_amcl(max(initial_stale_sec,
                                    blind_travel_stale_sec(0.0, MAP_AXIS_TIMEOUT_SEC)))
            if pose is None:
                return False, "amcl_stale", []
            error = target - axis_projection(pose[0], pose[1], axis)
            heading = math.atan2(axis[1], axis[0]) + (math.pi if error < 0.0 else 0.0)
            odom = odom_state.get("pose")
            if odom is None:
                return False, "odom_missing", []
            # 옆축 방향으로도 map 절대 yaw 로 닫아서 돈다(상대 회전이면 여기 오차가
            # 그대로 옆이동 방향 오차가 된다).
            ok, why, _at = turn_to_map_yaw(heading)
            if not ok:
                return False, why, []
            trace = []
            stable = 0
            last_linear = 0.0
            deadline = time.monotonic() + MAP_AXIS_TIMEOUT_SEC
            while time.monotonic() < deadline:
                if cancel_fn():
                    publish(0.0, 0.0)
                    return False, "canceled", trace
                # 직전 tick 에 낸 속도로 "얼마나 눈 감고 갔나" 를 재서 기준을 정한다
                # (blind_travel_stale_sec 머리말 참고). odom 은 움직임과 무관하게 계속
                # 오므로 그대로 엄격하게 본다 — 그게 진짜 "센서 끊김" 신호다.
                pose = current_amcl(max(SENSOR_STATE_STALE_SEC,
                                        blind_travel_stale_sec(last_linear, MAP_AXIS_TIMEOUT_SEC)))
                odom = odom_state.get("pose")
                odom_at = odom_state.get("at")
                if pose is None or odom is None or odom_at is None or time.monotonic() - odom_at > SENSOR_STATE_STALE_SEC:
                    publish(0.0, 0.0)
                    return False, "pose_stale", trace
                error = target - axis_projection(pose[0], pose[1], axis)
                stable = stable + 1 if abs(error) <= MAP_AXIS_TOL_M else 0
                linear = bounded_pid_linear(error, MAP_AXIS_KP, MAP_AXIS_MAX_LINEAR_MPS)
                angular = max(-MAP_AXIS_MAX_ANG, min(MAP_AXIS_MAX_ANG,
                    MAP_AXIS_HEADING_KP * map_heading_error(heading, pose[2])))
                trace.append((pose[0], pose[1], error, linear, angular, stable))
                if stable >= MAP_AXIS_STABLE_TICKS:
                    publish(0.0, 0.0)
                    return True, "", trace
                publish(linear, angular)
                last_linear = linear
                time.sleep(1.0 / MARKER_SERVO_HZ)
            publish(0.0, 0.0)
            return False, "lateral_timeout", trace

        def final_forward_pid(grid, initial_stale_sec: float = SENSOR_STATE_STALE_SEC):
            # 실측 이력(2026-08-05) — 이 함수의 amcl_stale 판정이 세 번 실기에서 걸렸다:
            #   1. FAILED at step 10/11(직전 단계 다 OK): 호출 직전 `current_amcl(
            #      POST_CENTER_STALE_SEC)` 로 재관측 정지 확인을 통과시켜놓고, 루프 첫
            #      tick 이 바로 기본 0.75초로 같은 타임스탬프를 다시 쟀다.
            #   2. codex 반박(P1, 같은 스레드): "첫 tick만" 완화하면 두 번째 tick 도 여전히
            #      같은(더 늙은) 타임스탬프를 보게 돼 실패를 한 틱 미룬 것뿐이었다.
            #   3. 예술서가 실주문 첫 근접 성공 케이스: 그 완화(진입 스냅샷과 다른 새 pose
            #      가 올 때까지 유지)를 넣은 뒤에도, LAT/CENTER 다 통과하고 FINAL MOVE 도
            #      한참 전진한 뒤 마지막 APPROACH(11/11)에서 또 amcl_stale 이 났다 —
            #      한 번 fresh 해진 뒤로는 쭉 기본 0.75초로 돌아갔다.
            #   4. **9/11 단계까지 다 통과한 뒤 여기서만** amcl_stale(2026-08-05, 예술서가
            #      실주문, FINAL PLAN 이 "13cm 벽, 6cm 전진" 까지 낸 다음). 여기서 진짜
            #      원인이 드러났다 — 위 1~3 은 다 "언제 쟀나" 를 만졌지만, 문제는 **무엇으로
            #      쟀나** 였다. 마지막 6cm 는 PID 로 좁히며 끝에선 4mm/s 까지 느려지는데,
            #      AMCL 은 `update_min_d`(2cm) 를 움직여야 pose 를 낸다 — 그 속도면 5초에
            #      한 번이다. 0.75초 기준이 먼저 걸리고, 걸리면 멈춰 기다리니 **안 움직여서
            #      AMCL 이 영영 안 오는 교착**이 된다. MAP_AXIS_TOL_M 이 update_min_d 보다
            #      작아서 났던 사고와 정확히 같은 모양이다(못 만족하는 조건).
            # 그래서 시간이 아니라 **거리**로 잰다 — `blind_travel_stale_sec()` 머리말 참고.
            # 1~3 의 단발성 지터 대비 유예(`initial_stale_sec`)는 그대로 둔다: 걸리면 멈추고,
            # 멈추면 옛 pose 가 다시 유효해지므로(속도 0 → 한도 = cap) 곧바로 회복한다.
            trace = []
            # GUI 진행 로그도 제어 루프와 같은 기준 시계에서 제한한다. 이 값이 없으면
            # 첫 PGM 관측 때 상태 보고 구문이 NameError 로 끝나며, 안전 정지만 하고
            # 도킹 자체는 실패한다.
            last_report_at = 0.0
            last_seq = None
            last_new_at = time.monotonic()
            last_seen_at = time.monotonic()
            last_good_amcl_at = time.monotonic()
            last_linear = 0.0
            prev_error = None
            filtered_u = None
            integral = 0.0
            prev_t = None
            stable = 0
            deadline = time.monotonic() + FINAL_APPROACH_TIMEOUT_SEC
            while time.monotonic() < deadline:
                spin()
                if cancel_fn():
                    publish(0.0, 0.0)
                    return False, "canceled", trace
                got = read_tap("front")
                now = time.monotonic()
                if got is None:
                    if now - last_new_at > FRAME_STALE_SEC:
                        publish(0.0, 0.0)
                        return False, "frame_empty", trace
                    publish(0.0, 0.0)
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                frame_now, seq, stamp = got
                if now - stamp > FRAME_STALE_SEC:
                    publish(0.0, 0.0)
                    return False, "frame_stale", trace
                if seq == last_seq:
                    publish(0.0, 0.0)
                    if now - last_new_at > FRAME_STALE_SEC:
                        return False, "frame_not_updating", trace
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                last_seq, last_new_at = seq, now
                # 직전 tick 에 낸 속도로 한도를 정한다 — 느리게 갈수록 AMCL 이 드물게 오는
                # 게 정상이다(머리말 4번, blind_travel_stale_sec 참고).
                pose = current_amcl(max(SENSOR_STATE_STALE_SEC,
                                        blind_travel_stale_sec(last_linear, FINAL_APPROACH_TIMEOUT_SEC)))
                if pose is None:
                    # 단발성 지연은 유예를 준다(위 머리말 3번 실측 참고) — 계속
                    # (initial_stale_sec 동안) 못 받을 때만 진짜 실패로 본다.
                    if now - last_good_amcl_at > initial_stale_sec:
                        publish(0.0, 0.0)
                        return False, "amcl_stale", trace
                    publish(0.0, 0.0)
                    time.sleep(1.0 / MARKER_SERVO_HZ)
                    continue
                last_good_amcl_at = now
                # ── 여기서 마커는 **선택**이다(2026-08-05, 실기 지적으로 바로잡음) ──────
                # 이 단계에서 마커가 하는 일은 좌우 보정 하나뿐이다. 거리 판정에는 아예
                # 안 쓴다 — `USE_CAMERA_CALIBRATION=False`(:65) 라 아래
                # `camera_center_and_bearing()` 이 **bearing 을 항상 0 으로** 돌려주고
                # (:282-283), 광선은 결국 AMCL yaw 그대로 나간다. 그런데도 예전 코드는
                # 매 tick 마커를 **필수**로 요구해 못 찾으면 멈췄다가 실패했다.
                #
                # 서가에 붙을수록 마커가 화각을 벗어나는 건 **정상**이다(카메라가 판에
                # 가까워질수록 시야각 밖으로 밀린다) — "다 정렬해 놨는데 마지막에 안
                # 보인다고 포기" 하던 게 그 결과였다. 직전 CENTER2 가 ±5px 안으로 맞춰
                # 놨으니, 남은 십여 cm 를 직진하는 동안 보정이 없어도 된다.
                # 못 보면 좌우 보정만 끄고 계속 간다.
                uv = centroid_uv(frame_now, hint_u=filtered_u)
                marker_seen = uv is not None
                if marker_seen:
                    last_seen_at = now
                    u, v = uv
                cx, bearing = camera_center_and_bearing(
                    u if marker_seen else 0.0, FRONT_CAM_K_640, 640, frame_now.shape[1])
                current_ray_yaw = ray_yaw(pose[2], bearing)
                hit = first_occupied(grid, pose[0], pose[1], current_ray_yaw, max_m=MAX_RANGE_M)
                if hit is None:
                    publish(0.0, 0.0)
                    return False, "raycast_no_wall", trace
                _hit_xy, distance = hit
                if marker_seen:
                    raw_u = float(u)
                    filtered_u = raw_u if filtered_u is None else (
                        MARKER_CENTER_LPF_ALPHA * raw_u
                        + (1.0 - MARKER_CENTER_LPF_ALPHA) * filtered_u)
                    image_error = (filtered_u - float(cx)) / (frame_now.shape[1] / 2.0)
                    dt = (now - prev_t) if prev_t is not None else (1.0 / MARKER_SERVO_HZ)
                    dt = min(max(dt, 1e-3), 0.5)
                    integral = max(-1.0, min(1.0, integral + image_error * dt))
                    derivative = 0.0 if prev_error is None else (image_error - prev_error) / dt
                    angular = visual_servo_angular_z(image_error, integral, derivative)
                    prev_error, prev_t = image_error, now
                else:
                    # 마커가 안 보이는 동안은 좌우 보정을 **끈다**(그냥 직진). 적분항을
                    # 들고 있으면 마지막 오차로 계속 돌아 오히려 틀어지므로 같이 비운다.
                    raw_u = image_error = None
                    angular = 0.0
                    integral = 0.0
                    prev_error = prev_t = None
                remaining = distance - CLEARANCE_M
                stable = stable + 1 if remaining <= FINAL_APPROACH_TOL_M else 0
                linear = 0.0 if stable else max(0.0, bounded_pid_linear(
                    remaining, FINAL_APPROACH_KP, FINAL_APPROACH_MAX_LINEAR_MPS))
                trace.append((distance, remaining, raw_u, filtered_u, image_error, linear, angular, stable))
                if now - last_report_at >= DOCK_STATUS_UPDATE_SEC:
                    # 마커를 못 보는 동안에도 진행 보고는 계속 낸다(거리는 마커와 무관하게
                    # 나온다) — 화면에 값이 끊기면 "멈췄나" 로 오해된다. marker_* 는 그때만 뺀다.
                    marker_fields = ({"marker_error_px": round(filtered_u - float(cx), 1),
                                      "marker_row_px": round(v, 1)} if marker_seen else
                                     {"marker_seen": False})
                    report("final_progress", pgm_distance_m=round(distance, 3),
                           remaining_to_clearance_m=round(max(0.0, remaining), 3),
                           linear_mps=round(linear, 3),
                           marker_bearing_rad=round(bearing, 4),
                           ray_yaw_rad=round(current_ray_yaw, 4),
                           **marker_fields)
                    last_report_at = now
                if stable >= FINAL_APPROACH_STABLE_TICKS:
                    publish(0.0, 0.0)
                    return True, "", trace
                publish(linear, angular)
                last_linear = linear
                time.sleep(1.0 / MARKER_SERVO_HZ)
            publish(0.0, 0.0)
            return False, "final_timeout", trace

        first_frame, first_center_trace, why = center_marker_pid(
            "초기 중앙정렬", not_before=turned_at + TURN_SETTLE_SEC)
        if first_frame is None:
            return finish(False, 499 if why == "canceled" else 502,
                          {"docked": False, "center_trace": first_center_trace},
                          f"초기 비주얼 서보 실패: {why}")

        report("initial_marker_centered", frames=len(first_center_trace))

        if VISUAL_SERVO_ONLY:
            return finish(False, 409, {"docked": False, "servo_centered": True,
                                       "center_trace": first_center_trace},
                          "PID 비주얼 서보 중앙 정렬 확인 완료(테스트 모드)")

        # 첫 PGM 관측은 옆축 목표만 만든다. 이 목록을 실행하지 않는다.
        # center_marker_pid() 가 방금 정지 상태로 정렬을 확인했다(POST_CENTER_STALE_SEC 주석
        # 참고) — AMCL 이 그동안 새 pose 를 안 냈을 수 있으니 여기만 더 관대하게 본다.
        robot_pose = wait_for_fresh_amcl(POST_CENTER_STALE_SEC)
        grid = map_state.get("grid")
        if robot_pose is None or grid is None:
            return finish(False, 503, {"docked": False}, "옆축 PID 전 AMCL 또는 map 이 오래됐다")
        moves, first_info = plan_dock(shelf, robot_pose, first_frame, grid, FRONT_CAM_K_640, first_frame.shape[1])
        if moves is None:
            return finish(False, 502, {"docked": False, **first_info},
                          f"첫 PGM 관측 실패: {first_info.get("error")}")

        # 정밀 이동이 실제로 시작하는 순간 알린다(왜 결과에 안 얹는지는 run_shelf_dock
        # 머리말 참고). finish() 도 같은 걸 부르지만 멱등이라 여기서 이미 풀었으면
        # 거기선 아무 일도 안 한다.
        ensure_lock_released()
        unlocked_node, unlocked_at = unlocked["node"], unlocked["at"]
        normal_axis, lateral_axis = shelf_axes(shelf)
        lateral_target = axis_projection(*first_info["approach_xy"], lateral_axis)
        lateral_error = lateral_target - axis_projection(robot_pose[0], robot_pose[1], lateral_axis)
        report("lateral_plan_ready", planned_lateral_m=round(abs(lateral_error), 3),
               pgm_distance_m=round(first_info["hit_dist_m"], 3),
               ray_yaw_rad=round(first_info["ray_yaw_rad"], 4))
        report("lateral_start", target_m=round(lateral_target, 3),
               initial_error_m=round(lateral_error, 3),
               planned_lateral_m=round(abs(lateral_error), 3))
        # 위 확인 이후 plan_dock()/락 해제만 지나왔다(추가 이동 없음) — 같은 정지 구간이라
        # 여기도 POST_CENTER_STALE_SEC 을 쓴다.
        side_start_pose = wait_for_fresh_amcl(POST_CENTER_STALE_SEC)
        if side_start_pose is None:
            return finish(False, 503, {"docked": False}, "옆축 PID 시작 전 AMCL 이 오래됐다")
        ok, why, lateral_trace = move_lateral_axis_pid(lateral_target, lateral_axis,
                                                        initial_stale_sec=POST_CENTER_STALE_SEC)
        if not ok:
            return finish(False, 499 if why == "canceled" else 502,
                          {"docked": False, "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
                           "first_observation": first_info, "lateral_trace": lateral_trace},
                          f"옆축 AMCL PID 실패: {why}")

        report("lateral_complete", final_error_m=round(lateral_trace[-1][2], 3) if lateral_trace else 0.0)

        # 옆축 이동 후에는 첫 프레임/첫 광선을 절대 재사용하지 않는다.
        lateral_pose = current_amcl()
        if lateral_pose is None:
            return finish(False, 503, {"docked": False}, "옆축 PID 뒤 AMCL 이 오래됐다")

        # ④ 재관측 전 서가 방향으로 다시 회전. move_lateral_axis_pid() 는 이동 내내 옆축
        # 방향(서가와 거의 수직)을 보고 있다가 그 자세로 끝난다 — 서가 정면이 아니다.
        # 실측(2026-08-05): 이 turn 이 없어서 옆축 이동 직후 카메라가 서가 옆 흰 벽을
        # 보고 있었고 재관측이 매번 marker_not_found 로 죽었다(스크린샷으로 확인).
        # 처음 접근 때와 같은 목표 자세(SHELF_YAW+EXTRA_TURN_RAD)로 되돌린다.
        ok, why, turned_at = turn_to_map_yaw(SHELF_YAW[shelf] + EXTRA_TURN_RAD)
        if not ok:
            return finish(False, 499 if why == "canceled" else 502,
                          {"docked": False, "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
                           "first_observation": first_info, "lateral_trace": lateral_trace},
                          f"재관측 전 서가 방향 회전 실패: {why}")

        second_frame, second_center_trace, why = center_marker_pid(
            "재관측 중앙정렬", not_before=turned_at + TURN_SETTLE_SEC)
        if second_frame is None:
            return finish(False, 499 if why == "canceled" else 502,
                          {"docked": False, "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
                           "first_observation": first_info, "lateral_trace": lateral_trace,
                           "recenter_trace": second_center_trace},
                          f"재관측 비주얼 서보 실패: {why}")
        report("reobserve_marker_centered", frames=len(second_center_trace))
        # 재관측 중앙정렬도 똑같이 멈춰 서서 확인한다 — 같은 이유로 관대한 기준을 쓴다.
        robot_pose = wait_for_fresh_amcl(POST_CENTER_STALE_SEC)
        if robot_pose is None:
            return finish(False, 503, {"docked": False}, "재관측 뒤 AMCL 이 오래됐다")
        _unused_moves, final_info = plan_dock(shelf, robot_pose, second_frame, grid, FRONT_CAM_K_640, second_frame.shape[1])
        if _unused_moves is None:
            return finish(False, 502, {"docked": False, **final_info},
                          f"재관측 PGM 실패: {final_info.get("error")}")

        # 최종축은 고정 거리 명령이 아니다. 새 프레임의 테이프 중점 PID와 새 AMCL+PGM
        # 거리로 매 tick 제어하고, 서가 표면 2 cm 앞에서만 종료한다.
        planned_forward_m = max(0.0, float(final_info["hit_dist_m"]) - CLEARANCE_M)
        report("final_plan_ready", pgm_distance_m=round(final_info["hit_dist_m"], 3),
               planned_forward_m=round(planned_forward_m, 3), clearance_m=CLEARANCE_M,
               ray_yaw_rad=round(final_info["ray_yaw_rad"], 4))
        report("final_start", clearance_m=CLEARANCE_M,
               planned_forward_m=round(planned_forward_m, 3))
        ok, why, final_trace = final_forward_pid(grid, initial_stale_sec=POST_CENTER_STALE_SEC)
        # ⑦ 마지막 자세 회전 — **이게 여태 아예 없었다**(2026-08-05 실측 지적).
        # `plan_dock()` 이 만든 이동 목록에 FINAL_YAW_RAD 회전이 들어 있지만 그 목록은
        # 실행하지 않는다(`_unused_moves`) — 즉 계획만 있고 실행이 없었다. 여기서 map
        # 절대 yaw 로 돌려 두 서가가 같은 최종 자세로 끝나게 한다(팔이 같은 조건에서
        # 일하도록 — FINAL_YAW_RAD 주석).
        final_turn_ok, final_turn_why = True, ""
        if ok:
            final_turn_ok, final_turn_why, _at = turn_to_map_yaw(FINAL_YAW_RAD)
        pose_after = current_amcl() or pose_fn()
        if ok:
            # FMS가 backup 을 명시적으로 보낼 때만, 최종축 → 옆축의 역순 체크포인트를
            # AMCL로 재계산해 돌아간다. 단순 거리 합산 복귀를 쓰지 않는다.
            #
            # `retreat_yaw` 는 서가를 벗어나는 첫 회전의 **map 절대 목표**다. 체크포인트
            # 두 점의 atan2 로 구하면 그 둘이 `hit_dist − CLEARANCE_M`(수 cm) 밖에 안
            # 떨어져 있어 AMCL 잡음에 방위가 휘둘린다 — 서가 법선은 이미 아는 값이니
            # 추정하지 않는다(`shelf/geometry.py` `retreat_moves` 의 `face_yaw`).
            record_return_targets([(lateral_pose[0], lateral_pose[1]),
                                   (side_start_pose[0], side_start_pose[1])],
                                  retreat_yaw=SHELF_YAW[shelf])
        payload = {
            "docked": ok, "shelf": shelf, "clearance_m": CLEARANCE_M,
            "first_observation": first_info, "lateral_target_m": lateral_target,
            "lateral_trace": lateral_trace, "recenter_trace": second_center_trace,
            "final_observation": final_info, "final_trace": final_trace,
            "pose_before": pose_before, "pose_after": pose_after,
            "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
            "final_yaw_rad": FINAL_YAW_RAD, "final_turn_ok": final_turn_ok,
        }
        if not ok:
            return finish(False, 499 if why == "canceled" else 502, payload,
                          f"최종 PGM+비주얼 PID 접근 실패: {why}")
        if not final_turn_ok:
            # 접근은 끝났지만 마지막 자세가 안 맞으면 팔이 못 집는다 — 성공으로 닫지 않는다.
            return finish(False, 499 if final_turn_why == "canceled" else 502, payload,
                          f"최종 자세 회전 실패: {final_turn_why}")
        return finish(True, 200, payload, "")
    except Exception as exc:  # noqa: BLE001
        log.exception("[서가도킹] 예상 못 한 예외")
        return finish(False, 500, {"docked": False}, f"도킹 중 예외: {exc!r}")
