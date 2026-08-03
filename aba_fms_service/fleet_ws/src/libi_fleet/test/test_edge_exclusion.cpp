// 레인(간선) 상호배제 — 두 로봇이 같은 구간을 마주 보고 탈 수 있나.
//
// 배경(codex 설계 판정 2026-08-03, P0): `reservation_deadlock.hpp` 머리말은
// "타깃을 먼저 확보한 뒤 출발 노드를 놓으니 노드예약만으로 정면충돌이 다 막힌다"
// 고 적어 왔다. **그 주장에는 숨은 전제가 있었다** — "모든 로봇이 자기가 선 정점을
// 실제로 쥐고 있다". 실행층은 그걸 보장하지 않는다:
//   · claim 은 배차·순회·정지 때만 하고(`fleet_node.cpp:699` `:2026` `:2056` `:2126`)
//     그 **반환값을 버린다** — WAIT 여도 task 는 만들어진다
//   · 그냥 서 있던(idle) 로봇은 아무 정점도 안 쥔다
// 그래서 B 에 서 있지만 B 를 안 쥔 로봇이 B→A 를 요청하면, 예전 코드는 A 만 보고
// GRANT 를 줬다. r1 이 A→B 를 타는 중이면 **같은 레인을 마주 본다.**
//
// 고친 방법은 표(`edge_owner_`)를 새로 두는 것이 아니라 두 가지다:
//   ① `request_move` 가 **출발 정점 소유도 검증**한다(비어 있으면 그 자리에서 claim)
//   ② 호출자가 출발 정점을 **도착할 때** 놓는다(예전엔 출발 순간에 놓았다)
//      → 주행 내내 두 끝점을 다 쥔다 = 그 레인의 상호배제
//
// ⚠️ 되돌림 확인 (실측 2026-08-03)
//    · ①을 지우면(`auto fit = node_owner_.find(from)` 블록) 2개가 빨개진다:
//      `LeavingAVertexYouDoNotOwnIsRefused`, `AFreeSourceIsClaimedNotRefused`.
//      나머지는 초록으로 남는데 **그게 맞다** — 그것들은 ②(두 끝점 보유)가 지키는
//      성질이라 ①과 독립이다.
//    · ②는 `fleet_node.cpp` 의 **호출 규율**이라 게이트 단위 시험으로 못 잡는다.
//      여기서는 "그 규율이 지켜졌을 때 레인이 정말 잠기는가" 만 못 박는다
//      (`SourceStaysLockedUntilArrival`). ②의 되돌림 확인은 `scripts/cbs_sim` 의
//      다중 로봇 최소거리 측정으로 한다.
#include <gtest/gtest.h>

#include <string>

#include "libi_fleet/reservation_deadlock.hpp"

using libi_fleet::MoveDecision;
using libi_fleet::ReservationDeadlock;

namespace
{
constexpr int A = 1;
constexpr int B = 2;
constexpr int C = 3;
constexpr int kPrio = 5;

// 지금 실행층 규율: 출발지를 쥔 채로 목표를 잡고, **놓지 않는다**(도착 때 놓는다).
void depart(ReservationDeadlock & t, const std::string & robot, int from, int to)
{
  ASSERT_EQ(t.request_move(robot, from, from, kPrio), MoveDecision::GRANT);
  ASSERT_EQ(t.request_move(robot, from, to, kPrio), MoveDecision::GRANT);
}
}  // namespace

// codex 재현 순서 그 자체. 예전 코드는 여기서 GRANT 를 줬다.
TEST(EdgeExclusion, LeavingAVertexYouDoNotOwnIsRefused)
{
  ReservationDeadlock t;
  depart(t, "r1", A, B);
  t.release_node("r1", A);   // 예전 실행층처럼 출발 즉시 놓았다고 치자

  // r2 는 물리적으로 B 에 서 있지만 B 를 쥔 적이 없다(idle 이었다).
  EXPECT_NE(t.request_move("r2", B, A, kPrio), MoveDecision::GRANT)
    << "안 쥔 정점을 떠나는 허가를 주면, 그 자리로 오고 있던 로봇과 정면으로 만난다";
}

// 두 끝점을 다 쥐면 그 레인은 양방향 모두 막힌다.
TEST(EdgeExclusion, OpposingTraversalIsRefusedBothWays)
{
  ReservationDeadlock t;
  depart(t, "r1", A, B);   // r1 이 A 와 B 를 다 쥔 채 레인 위에 있다

  EXPECT_NE(t.request_move("r2", B, A, kPrio), MoveDecision::GRANT) << "B→A 역주행";
  EXPECT_NE(t.request_move("r2", A, B, kPrio), MoveDecision::GRANT) << "A→B 후미추돌";
}

// 출발 정점은 **도착할 때까지** 잠긴다 — 이것이 레인 보호의 전부다.
TEST(EdgeExclusion, SourceStaysLockedUntilArrival)
{
  ReservationDeadlock t;
  depart(t, "r1", A, B);

  // 제3의 로봇이 그 레인의 출발 정점으로 들어오려 한다.
  ASSERT_EQ(t.request_move("r3", C, C, kPrio), MoveDecision::GRANT);
  EXPECT_NE(t.request_move("r3", C, A, kPrio), MoveDecision::GRANT)
    << "r1 이 아직 A—B 위에 있는데 A 를 내주면 레인 입구에서 만난다";

  // r1 이 B 에 닿아 A 를 놓으면(실행층 도착 분기) 그때 열린다.
  t.release_node("r1", A);
  EXPECT_EQ(t.request_move("r3", C, A, kPrio), MoveDecision::GRANT);
}

// ⚠️ 대가를 못 박는다 — 진짜 간선 예약이면 A 를 **가로질러** 지나갈 수 있지만
//    두 끝점 점유는 그것까지 막는다. 의도된 보수성이다(헤더 머리말의 ⚠️).
TEST(EdgeExclusion, CrossingTrafficThroughTheSourceAlsoWaits)
{
  ReservationDeadlock t;
  depart(t, "r1", A, B);
  ASSERT_EQ(t.request_move("r3", C, C, kPrio), MoveDecision::GRANT);
  EXPECT_NE(t.request_move("r3", C, A, kPrio), MoveDecision::GRANT);
}

// 자기 정점을 안 쥔 로봇을 **거부하지 말고 claim 시킨다** — 안 그러면 배차 claim 이
// 실패했거나 유령 정리로 예약이 풀린 로봇이 영영 못 움직인다.
TEST(EdgeExclusion, AFreeSourceIsClaimedNotRefused)
{
  ReservationDeadlock t;
  EXPECT_EQ(t.request_move("r1", A, B, kPrio), MoveDecision::GRANT)
    << "출발지가 비어 있으면 그 자리에서 잡고 진행해야 한다";

  // 그리고 실제로 잡혔어야 한다 — 안 그러면 검증이 이름뿐이다.
  EXPECT_NE(t.request_move("r2", A, A, kPrio), MoveDecision::GRANT);
}

// 정면 대치는 예전처럼 교착으로 풀린다 — 출발지 검증이 그 장치를 안 깬다.
TEST(EdgeExclusion, HeadOnStillResolvesToDeadlock)
{
  ReservationDeadlock t;
  ASSERT_EQ(t.request_move("r1", A, A, 9), MoveDecision::GRANT);
  ASSERT_EQ(t.request_move("r2", B, B, 1), MoveDecision::GRANT);

  EXPECT_EQ(t.request_move("r1", A, B, 9), MoveDecision::WAIT);
  EXPECT_EQ(t.request_move("r2", B, A, 1), MoveDecision::DEADLOCK)
    << "낮은 우선순위가 양보(우회)해야 대치가 풀린다";
}

// 남이 쥔 출발지에 막힌 것도 교착 사이클에 참여해야 한다 — WAIT 로만 두면
// 서로를 영원히 기다린다.
TEST(EdgeExclusion, BlockedOnTheSourceJoinsTheDeadlockGraph)
{
  ReservationDeadlock t;
  depart(t, "r1", A, B);        // r1: A, B 소유
  // r2 는 B 에 서 있다고 믿지만 B 는 r1 것이다. 그리고 r1 이 다음에 원하는 C 를 쥔다.
  ASSERT_EQ(t.request_move("r2", C, C, 1), MoveDecision::GRANT);

  EXPECT_EQ(t.request_move("r1", B, C, 9), MoveDecision::WAIT);   // r1 → r2 대기
  EXPECT_EQ(t.request_move("r2", B, A, 1), MoveDecision::DEADLOCK)
    << "출발지(B)가 r1 것이라 막혔고, r1 은 r2 를 기다린다 — 사이클이다";
}
