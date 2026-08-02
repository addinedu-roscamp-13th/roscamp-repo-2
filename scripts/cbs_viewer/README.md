# cbs_viewer — CBS 시간표 애니메이션

`libi_fleet::CbsTraffic` 가 만든 **시간표**가 정말 무충돌인지 눈으로 보는 도구.
ROS 노드도 로봇도 관제도 필요 없다 — navgraph 파일과 start:goal 만 있으면 된다.

```bash
./run.sh --robot 0:15 --robot 15:0            # 두 대 정면 교차
./run.sh --robot 0:15 --robot 15:0 --robot 6:3
./run.sh --robot 0:15 --robot 15:0 --clearance 3   # 여유를 벌리면 어떻게 달라지나
./run.sh --robot 0:15 --no-open               # 파일만 만들고 안 엶
```

정점 번호를 모르면 아무거나 한 번 열어 보면 지도에 번호가 찍혀 있다.

## 화면

| 영역 | 보는 것 |
|---|---|
| 지도 | navgraph 정점·레인. 점선 = 각 로봇의 계획 경로. 원 = 로봇(틱 시간에 맞춰 이동) |
| 간트 | 로봇별 **정점 점유 구간**. 같은 정점에서 막대가 겹치면 충돌이다 |
| 판정 | 뷰어가 계획을 **독립적으로 다시 검사**한 결과. 플래너 내부 판정을 믿지 않는다 |
| 시계 | `t = 12.4틱 (12.4s)  R1:v9  R2:이동중` |

## 왜 이게 검증이 되나

플래너가 쓰는 `find_conflict` 와 뷰어의 검사는 **다른 코드**다. 같은 함수로 만들고 같은
함수로 검사하면 그 함수의 버그를 영원히 못 잡는다(테스트 `NoTimedConflict` 도 같은 이유로
따로 짰다). 뷰어가 "충돌 0"이라고 하면 최소한 두 구현이 동의한 것이다.

## 틱은 실제 시간이다

간선 소요 = `ceil(레인 길이 ÷ 속도 ÷ 틱길이)` 틱. 그래서 화면의 초는 실제 주행 초다.
`--speed`(기본 0.15 m/s), `--tick`(기본 1.0초)으로 바꾼다.

이 계산은 플러그인(`plugins/cbs_traffic.cpp` 의 `build_graph`)과 **같은 규칙**이어야
화면과 실제가 일치한다. 한쪽만 고치면 안 된다.

## 실제 로봇에 적용할 때

```bash
# fleet_node 기동 시
ros2 run libi_fleet fleet_node --ros-args ... -p traffic_plugin:=libi_fleet::CbsTraffic

# 이미 떠 있으면 런타임 교체 (로봇이 멈춰 있을 때)
ros2 service call /fms/set_plugins libi_fleet_msgs/srv/SetPlugins \
  '{traffic: "libi_fleet::CbsTraffic"}'
```

튜닝은 환경변수(플러그인은 노드 파라미터를 못 받는다):

| 변수 | 기본 | 뜻 |
|---|---|---|
| `LIBI_CBS_TICK_SEC` | 1.0 | 틱 하나의 실제 길이(초) |
| `LIBI_CBS_SPEED_MPS` | 0.15 | 로봇 순항 속도 — 간선 소요 계산에 쓴다 |
| `LIBI_CBS_CLEARANCE` | 1 | 계획에서 미리 벌려 두는 여유 틱 |
| `LIBI_CBS_SLACK` | 10 | 계획보다 이만큼 일찍 와도 통과 |
| `LIBI_CBS_DRIFT_LIMIT` | 10 | 이만큼 밀리면 시간표를 버리고 반응형으로 |

예약 시각 허용 오차는 **양쪽 대칭 ±10초**다(`SLACK` = 이른 쪽, `DRIFT_LIMIT` = 늦은 쪽).
`fleet_node` 의 `plan_deadline_slack`(재계획 트리거)도 같은 10초다 — 셋 중 하나만 바꾸면
"교통 계층은 통과시키는데 fleet_node 는 마감 초과로 재계획" 같은 어긋남이 생긴다.

## 한계 (알고 쓰는 것)

- 순회(patrol) task 는 계획에 넣지 않는다. canonical 랩 순서를 CBS 최단경로가 깨기 때문.
  순회 로봇은 물리 점유 안전망이 막는다 — 안전하지만 계획이 그만큼 보수적이다.
- 뷰어는 **계획**을 재생한다. 실제 주행 로그 재생은 아직 없다(다음 단계).
- 재계획은 새 배차 때만 돈다. 주행 중 지연은 `DRIFT_LIMIT` 를 넘으면 반응형으로 강등된다.
