// ReservationDeadlock 의 pluginlib 등록만 담는다.
// [2026-07-26] 클래스 본문은 include/libi_fleet/reservation_deadlock.hpp 로 **그대로** 옮겼다
//   (로직 무변경). cbs_traffic 이 계획이 낡았을 때의 안전 모드로 이 클래스를 품어서 쓰는데,
//   .cpp 안에만 있으면 링크가 안 돼 교착 판정을 한 벌 더 쓰게 되기 때문이다.
#include <pluginlib/class_list_macros.hpp>

#include "libi_fleet/reservation_deadlock.hpp"

PLUGINLIB_EXPORT_CLASS(libi_fleet::ReservationDeadlock, libi_fleet::TrafficBase)
