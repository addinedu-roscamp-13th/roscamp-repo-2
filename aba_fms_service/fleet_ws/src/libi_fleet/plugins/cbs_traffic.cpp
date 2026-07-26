// CbsTraffic 의 pluginlib 등록만 담는다.
// 클래스 본문은 include/libi_fleet/cbs_traffic.hpp — 테스트가 pluginlib 로더 없이
// 직접 만들어 쓸 수 있어야 실행 게이트(시간 판정)를 검증할 수 있다.
// (ReservationDeadlock 을 헤더로 뺀 것과 같은 이유다.)
#include <pluginlib/class_list_macros.hpp>

#include "libi_fleet/cbs_traffic.hpp"

PLUGINLIB_EXPORT_CLASS(libi_fleet::CbsTraffic, libi_fleet::TrafficBase)
