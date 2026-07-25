#pragma once
#include <vector>
#include "libi_fleet/navgraph.hpp"

namespace libi_fleet
{

// 야간 보안순회(SECURITY_PATROL) 경로 생성 — [미래 auto 기본값, 현재 미사용].
//
// `security_patrol_route="auto"` 일 때만 쓰인다. 지금 운영 설정은 fleet_node 가 명시
// 루트를 `security_patrol_route` 로 주므로 이 함수는 호출되지 않는다. 야간 보안이 주간
// 순회와 **다른 경로 알고리즘**(예: 내부 스윕, 사각지대 우선)을 갖게 될 때 본문만 교체할
// seam 이다. 지금은 주간 순회와 동일 — right_hand_boundary_cycle 에 위임한다.
std::vector<int> security_patrol_boundary_cycle(const Navgraph & g);

}  // namespace libi_fleet
