// [실험용] 교통관제 OFF — 모든 이동을 무조건 GRANT 한다(노드 예약·교착 감지 없음).
// ReservationDeadlock 을 잠깐 빼고 "같이 움직임"이 교통관제 셔플 때문인지 확인하는 용도.
// 실사용 금지: 정면충돌·후미추돌·교착이 전혀 안 막힌다.
#include <string>
#include <utility>
#include <vector>

#include <pluginlib/class_list_macros.hpp>

#include "libi_fleet/traffic_base.hpp"

namespace libi_fleet
{

class GrantAllTraffic : public TrafficBase
{
public:
  MoveDecision request_move(const std::string &, int, int, int) override
  {
    return MoveDecision::GRANT;   // 항상 허가 — 교통협상 없음
  }
  void release(const std::string &, int) override {}
  void release_node(const std::string &, int) override {}
  std::vector<std::pair<int, std::string>> occupancy() const override { return {}; }
};

}  // namespace libi_fleet

PLUGINLIB_EXPORT_CLASS(libi_fleet::GrantAllTraffic, libi_fleet::TrafficBase)
