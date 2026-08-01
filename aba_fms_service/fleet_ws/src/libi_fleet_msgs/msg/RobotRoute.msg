# 로봇 한 대의 남은 경로(월드 좌표). FleetRoutes 의 원소.
#
# 시간표(FleetPlan)와 달리 **매 틱 갱신된다.** 계획이 없어도(반응형 교통) 경로는 있다.
string robot
float64[] xs
float64[] ys
