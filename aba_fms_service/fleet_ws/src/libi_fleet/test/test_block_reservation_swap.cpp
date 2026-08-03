// 사람 차단 때 예약을 **원자적으로 갈아타는 것**이 정말 안전한가.
//
// 배경(codex 3차, 2026-08-03): 예전에는 차단 보고를 받으면 FMS 가 로봇에게 `backup` 을
// 직접 쏴서 직전 정점 A 로 물렸다. 그 이동은 **예약 체계 밖**이라 — 예약을 확인하지도
// 잡지도 않고 움직이니 — 이미 놓아 준 A 에 다른 로봇이 들어와 정면으로 만날 수 있었다.
// P0 세 건이 전부 그 하나에서 나왔다.
//
// 지금은 `fleet_node` 의 `/fms/node_block` 콜백이 이렇게 한다:
//     ① request_move(A, A) 로 A 를 **점유 claim**
//     ② GRANT 일 때만 막힌 B 를 놓고 moving=false
//     ③ 실패하면 아무것도 안 바꾼다(B 를 쥔 채 대기)
//
// 그 전부가 "`from == to` claim 은 남이 쥐고 있으면 GRANT 를 안 준다" 는 성질 위에 서
// 있다. **그 성질이 깨지면 위 순서가 통째로 위험해진다** — 로봇이 아무 노드도 안 쥔 채
// 레인에 남거나, 두 로봇이 같은 정점을 동시에 쥔다. 그래서 여기서 못 박는다.
//
// ⚠️ 되돌림 확인: `reservation_deadlock.hpp` 의 `from == to` 분기에서 소유자 검사를
//    빼면 `ClaimFailsWhenSomeoneElseHoldsIt` 가 빨개진다.
#include <gtest/gtest.h>

#include <string>

#include "libi_fleet/reservation_deadlock.hpp"

using libi_fleet::MoveDecision;
using libi_fleet::ReservationDeadlock;

namespace
{
constexpr int A = 10;   // 떠나온 정점 (되돌아갈 곳)
constexpr int B = 11;   // 사람이 막은 정점 (로봇이 향하던 곳)
constexpr int kPrio = 5;

// 로봇을 A→B 로 보내 B 를 쥔 상태를 만든다(실제 주행과 같은 순서).
void put_on_lane(ReservationDeadlock & t, const std::string & robot)
{
  ASSERT_EQ(t.request_move(robot, A, A, kPrio), MoveDecision::GRANT);   // A 확보
  ASSERT_EQ(t.request_move(robot, A, B, kPrio), MoveDecision::GRANT);   // B 예약
  t.release_node(robot, A);                                            // 출발하며 A 해제
}
}  // namespace

TEST(BlockReservationSwap, ClaimSucceedsWhenTheVertexIsFree)
{
  ReservationDeadlock t;
  put_on_lane(t, "r1");
  // A 는 아무도 안 쥐고 있다 — 되돌아갈 수 있다.
  EXPECT_EQ(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT);
}

TEST(BlockReservationSwap, ClaimFailsWhenSomeoneElseHoldsIt)
{
  ReservationDeadlock t;
  put_on_lane(t, "r1");
  // 그 사이 다른 로봇이 A 를 차지했다.
  ASSERT_EQ(t.request_move("r2", A, A, kPrio), MoveDecision::GRANT);

  EXPECT_NE(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT)
    << "남이 쥔 정점을 claim 이 내주면, 두 로봇이 같은 자리를 쥔 채 서로에게 간다";
}

TEST(BlockReservationSwap, FailedClaimLeavesTheRobotHoldingTheBlockedVertex)
{
  // ③ 실패하면 아무것도 안 바꾼다 — B 를 쥔 채 남아야 한다. B 점유가 이 레인의
  //    정면 진입을 막는 유일한 장치다(codex: "P0 방어 성공").
  ReservationDeadlock t;
  put_on_lane(t, "r1");
  ASSERT_EQ(t.request_move("r2", A, A, kPrio), MoveDecision::GRANT);
  ASSERT_NE(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT);

  // r2 가 A→B 로 오려 해도 r1 이 B 를 쥐고 있어 막힌다.
  EXPECT_NE(t.request_move("r2", A, B, kPrio), MoveDecision::GRANT)
    << "차단 로봇이 쥔 B 로 다른 로봇이 들어오면 레인에서 정면 충돌한다";
}

TEST(BlockReservationSwap, SwapReleasesTheBlockedVertexOnlyAfterTheClaim)
{
  // ①②가 순서대로 되면, A 는 이 로봇 것이고 B 는 풀린다.
  ReservationDeadlock t;
  put_on_lane(t, "r1");
  ASSERT_EQ(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT);
  t.release_node("r1", B);

  // 이제 다른 로봇이 B 를 쓸 수 있다(차단이 풀린 뒤의 정상 상태).
  EXPECT_EQ(t.request_move("r2", B, B, kPrio), MoveDecision::GRANT);
  // 그러나 A 는 여전히 r1 것이다 — 되돌아간 자리를 뺏기면 안 된다.
  EXPECT_NE(t.request_move("r2", B, A, kPrio), MoveDecision::GRANT)
    << "되돌아간 로봇이 서 있는 정점을 남에게 내주면 안 된다";
}

TEST(BlockReservationSwap, ReclaimingOwnVertexIsIdempotent)
{
  // 같은 차단 보고가 두 번 와도(재발행·중복) 상태가 흔들리면 안 된다.
  ReservationDeadlock t;
  put_on_lane(t, "r1");
  EXPECT_EQ(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT);
  EXPECT_EQ(t.request_move("r1", A, A, kPrio), MoveDecision::GRANT);
}
