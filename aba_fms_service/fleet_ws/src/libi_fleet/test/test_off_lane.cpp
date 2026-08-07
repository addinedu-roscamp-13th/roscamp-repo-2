// 순회 로봇이 **레인에서** 얼마나 벗어났나 — 재합류 판정의 근거.
//
// 요구(2026-08-07): 야간 추격이 끝나면 "이전 순회경로가 아니라 가장 가까운 순회경로
// 노드를 찾아서" 다시 돌아야 한다. 추격 중 로봇은 레인 밖으로 나가는데 FMS 의
// `t.path`/`t.idx` 는 그대로라, 그냥 두면 추격 전에 향하던 정점으로 되돌아간다.
//
// ⚠️ **정점까지의 거리로 재면 안 된다.** 긴 레인의 출발점에서는 목표 정점이 원래 멀어서
//    매 랩마다 "이탈" 로 읽힌다 — 그러면 랩이 계속 새로 짜여 로봇이 갈팡질팡한다.
//    아래 `레인_위에_있으면_정점이_멀어도_0` 이 그 함정을 붙든다.
#include <gtest/gtest.h>
#include <cmath>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::off_lane_distance;

namespace
{
// arte2 실제 좌표: v13(순회경로-7) → v12(순회경로-8). 세로 레인 0.383m.
constexpr double kAx = -0.074, kAy = -1.002;
constexpr double kBx = -0.074, kBy = -1.385;
}  // namespace

TEST(OffLane, 레인_위에_있으면_정점이_멀어도_0)
{
  // 막 출발했다 — 목표 정점까지 0.383m 지만 레인 위다. 이탈이 아니다.
  EXPECT_NEAR(off_lane_distance(kAx, kAy, kAx, kAy, kBx, kBy), 0.0, 1e-9);
  // 중간
  EXPECT_NEAR(off_lane_distance(kAx, (kAy + kBy) / 2, kAx, kAy, kBx, kBy), 0.0, 1e-9);
}

TEST(OffLane, 옆으로_벗어나면_그_거리다)
{
  // 레인 중간에서 옆으로 0.30m — 침입자를 쫓아 나간 모습.
  EXPECT_NEAR(off_lane_distance(kAx + 0.30, (kAy + kBy) / 2, kAx, kAy, kBx, kBy),
              0.30, 1e-9);
}

TEST(OffLane, 선분_밖은_끝점까지의_거리로_잘린다)
{
  // 레인을 지나쳐 더 갔다 — 무한 직선이 아니라 **선분**이라야 맞다.
  const double past = 0.20;
  EXPECT_NEAR(off_lane_distance(kBx, kBy - past, kAx, kAy, kBx, kBy), past, 1e-9);
  EXPECT_NEAR(off_lane_distance(kAx, kAy + past, kAx, kAy, kBx, kBy), past, 1e-9);
}

TEST(OffLane, 두_끝점이_같으면_점거리로_떨어진다)
{
  // 경로에 중복 정점이 있는 경우. 0 으로 나누면 안 된다.
  EXPECT_NEAR(off_lane_distance(kAx + 0.1, kAy, kAx, kAy, kAx, kAy), 0.1, 1e-9);
}

TEST(OffLane, 기본_임계는_레인_절반보다_크다)
{
  // ⚠️ 이 관계가 깨지면 평상시 제어 오차로 랩이 계속 새로 짜인다.
  //    arte2 최단 레인은 0.2737m(6→7) — 절반 0.1369.
  const double shortest_lane_half = 0.2737 / 2.0;
  const double kDefaultResnap = 0.25;      // fleet_node.cpp `patrol_resnap_m` 기본값
  EXPECT_GT(kDefaultResnap, shortest_lane_half);
}

// ── 순회에는 "서는 정점" 이 없다 (2026-08-07) ────────────────────────────────
//
// 예전엔 `idx + 1 >= path.size()` 하나로 판정해서 랩의 마지막 정점이 배달의 최종
// 목적지와 같은 취급을 받았다. 거기서만 선행통과가 꺼져(`reach = arrive_radius`)
// 로봇이 완전히 감속·정지한 뒤 새 목표를 받고 다시 가속했다 — 랩마다 한 번씩 주춤.
// 사용자 보고 2026-08-07: "순회가 끝나면 좀 빨리 경로를 받았으면".
//
// 되돌리면(= patrol 갈래를 지우면) `순회는_마지막_정점에서도_안_선다` 가 빨개진다.

TEST(StopNode, 배달은_최종_정점에서_선다)
{
  // 정밀 정지·완료 판정이 필요하다 — 여기서는 정확히 닿아야 한다.
  EXPECT_TRUE(libi_fleet::is_stop_node(/*patrol=*/false, 2, 3));
}

TEST(StopNode, 배달도_경유_정점에서는_안_선다)
{
  EXPECT_FALSE(libi_fleet::is_stop_node(/*patrol=*/false, 1, 3));
}

TEST(StopNode, 순회는_마지막_정점에서도_안_선다)
{
  // ⚠️ 실기 결함 그 자체. 랩은 끝나지 않으므로 마지막 정점도 **경유점**이다.
  EXPECT_FALSE(libi_fleet::is_stop_node(/*patrol=*/true, 2, 3))
    << "랩 경계에서만 완전히 정지해 한 바퀴마다 주춤한다";
  EXPECT_FALSE(libi_fleet::is_stop_node(/*patrol=*/true, 1, 3));
}
