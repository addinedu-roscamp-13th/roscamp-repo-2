#include "libi_fleet/security_patrol_cycle.hpp"

#include "libi_fleet/patrol_cycle.hpp"

namespace libi_fleet
{

std::vector<int> security_patrol_boundary_cycle(const Navgraph & g)
{
  // 위임: 지금은 주간 순회와 동일한 우수법 경계순회. 보안 전용 알고리즘 도입 시 여기만 교체.
  return right_hand_boundary_cycle(g);
}

}  // namespace libi_fleet
