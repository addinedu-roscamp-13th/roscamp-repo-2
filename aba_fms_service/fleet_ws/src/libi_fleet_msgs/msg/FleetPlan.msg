# CBS 시간표. 재계획할 때마다 한 번 낸다(transient_local).
#
# ⚠️ robots 가 비어 있으면 **계획을 버린 상태**다(reason 에 이유). 받는 쪽은 그때
#    예약 표시를 지워야 한다 — 옛 예약을 계속 띄우면 화면만 거짓이 된다.
uint32 seq
float64 epoch         # fleet_node 의 steady_clock. 내부 마감 계산용 — 바깥에서 쓰지 말 것
float64 epoch_wall    # 같은 순간의 벽시계(unix 초). 예약 시각 = epoch_wall + arrive*tick_sec
float64 tick_sec
int32 drift_limit     # 몇 틱까지 늦어도 봐주는가. 화면이 "지연"과 "도착 중"을 가르는 기준
string reason         # 비어 있으면 정상. 아니면 계획을 못 세운 이유
RobotPlan[] robots
