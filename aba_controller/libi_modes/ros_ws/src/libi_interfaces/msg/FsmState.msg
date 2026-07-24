# libi_modes 가 주기 발행하는 현재 상태 브로드캐스트.
#
# active_branch 는 libi_modes.registry.BRANCH_ORDER 의 한 원소와 같아야 한다
# (2단계 웹에서 '상태 -> 표시할 서브트리' 선택에 그대로 쓰인다).
builtin_interfaces/Time stamp
string robot_id
string current_state
string active_branch
string error_code        # ERROR 상태일 때만 채워진다. 그 외에는 빈 문자열.
float32 battery_percent
bool is_docked
