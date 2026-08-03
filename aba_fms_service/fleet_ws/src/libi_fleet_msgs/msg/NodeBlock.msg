# 사람이 막았거나 도킹이 잡아 둔 정점을 알린다. 잠금과 차단이 같은 계약을 쓴다.
#
# ⚠️ ttl_sec 은 필수다. 푸는 쪽이 죽으면 그 길이 영영 막히기 때문에,
#    fleet_node 가 만료를 읽는 시점에 스스로 푼다 — RobotHold.msg 와 같은 규칙.
#
# ttl_sec <= 0 이면 **해제**다. 단, owner 가 같을 때만 그 owner 의 차단을 푼다 —
# 같은 정점을 사람 차단과 서가 도킹이 함께 잡을 수 있어, 한쪽의 해제가 남의 차단까지
# 지우면 안 된다.
int32 node          # navgraph 정점 인덱스
float32 ttl_sec     # 유효시간(초). 0 이하 = 해제. 600 초과면 600 으로 클램프
string reason       # "person" | "shelf_dock" | "" — 우선순위 판단에 쓴다
string robot        # 알린 로봇(로그·사건용). 빈 문자열 허용
string owner        # "person:pinky3" | "dock:pinky1" — 소유자별로 따로 잡고 따로 푼다
