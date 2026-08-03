# 로봇 한 대의 시간표. FleetPlan 의 원소.
string robot
int32[] path          # 정점 인덱스 (fleet_node navgraph 기준)
int32[] arrive_tick   # path 와 같은 길이. -1 = 마감 없음(이미 떠난 칸)
# 정점의 월드 좌표. **인덱스→좌표 표를 받는 쪽이 따로 들 필요를 없앤다** —
# 화면이 든 waypoint.yaml 과 정점 순서가 같다는 보장이 없어서다.
float64[] xs
float64[] ys
