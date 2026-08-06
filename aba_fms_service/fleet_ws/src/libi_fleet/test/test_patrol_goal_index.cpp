// 순회에서 CBS 에 주는 목표는 **계획 출발점 바로 다음 한 정점**이어야 한다.
//
// 실기 결함(2026-08-07, pinky-3): 목표가 늘 `idx + 1` 이었다. 서 있을 때 계획 출발점은
// `path[idx-1]` 이므로(`plan_start_for` 의 !ride 갈래) 구간이 **두 홉**이 되고, CBS 가
// 중간 정점을 마음대로 고른다. 같은 비용의 다른 길이 있으면 랩 정점을 통째로 건너뛴다.
//
// arte2 는 랩이 사각형 통로라 동률이 곳곳에 있다(좌표는 arte2.navgraph.yaml):
//   v13(-0.074,-1.002) → v14(0.200,-1.385)
//     랩:  13 → 12(-0.074,-1.385) → 14   = 0.383 + 0.274 = 0.657
//     현:  13 → 10( 0.200,-1.002) → 14   = 0.274 + 0.383 = 0.657   ← 완전 동률
//   v4(예술서가) → v11(순회경로-6) 도 v5(문학서가)/v6(과학-인문학서가) 로 같은 모양
//
// 실기 로그에 `v6`, `v10` 이 찍혀 랩 밖으로 샜다. 사용자 표현:
//   "간선은 이었지만 순회경로 자체가 그렇게 편성된 건 아니다."
//
// 되돌리면(= 늘 `idx + 1`) `StandingPlansOneHopSoCbsCannotCutTheCorner` 가 빨개진다.
#include <gtest/gtest.h>
#include <vector>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::patrol_goal_index;
using libi_fleet::plan_start_for;

namespace
{
// arte2 랩의 실제 한 토막: … 11 → 13 → 12 → 14 → 10 …
const std::vector<int> kLap{11, 13, 12, 14, 10};
}  // namespace

TEST(PatrolGoalIndex, RidingAimsOnePastTheCommittedNode)
{
  // v13 을 향해 가는 중(idx=1). 출발점은 v13, 목표는 그 다음 v12 — 한 홉이다.
  int start = -1, from = -1;
  ASSERT_TRUE(plan_start_for(kLap, 1, /*moving=*/true, {}, start, from));
  EXPECT_EQ(start, 13);
  EXPECT_EQ(kLap[patrol_goal_index(1, /*moving=*/true, kLap.size())], 12);
}

TEST(PatrolGoalIndex, StandingPlansOneHopSoCbsCannotCutTheCorner)
{
  // ⚠️ 실기 결함 그 자체. v13 을 막 지나 서 있다(idx=2). 출발점은 v13.
  //    목표가 v14 면 13→12→14 와 13→10→14 가 **같은 비용**이라 CBS 가 현을 탄다.
  //    목표가 v12 면 그 홉은 랩 간선 자체라 고를 여지가 없다.
  int start = -1, from = -1;
  ASSERT_TRUE(plan_start_for(kLap, 2, /*moving=*/false, {}, start, from));
  EXPECT_EQ(start, 13) << "서 있을 때 출발점은 방금 지난 정점이다";
  EXPECT_EQ(kLap[patrol_goal_index(2, /*moving=*/false, kLap.size())], 12)
    << "출발점 바로 다음 랩 정점이어야 CBS 가 지름길로 샐 수 없다";
}

TEST(PatrolGoalIndex, BlockedCommitStillGetsTwoHopsToDetour)
{
  // 커밋 노드 v12 가 사람으로 막혔다. `plan_start_for` 가 출발점을 v13 으로 물리고,
  // `moving` 은 참 그대로라 목표는 v14 — 막힌 정점 **너머**다. 우회하라는 뜻이다.
  int start = -1, from = -1;
  ASSERT_TRUE(plan_start_for(kLap, 2, /*moving=*/true, {12}, start, from));
  EXPECT_EQ(start, 13);
  EXPECT_EQ(from, -1);
  EXPECT_EQ(kLap[patrol_goal_index(2, /*moving=*/true, kLap.size())], 14)
    << "막힌 정점을 목표로 주면 계획이 아예 안 선다";
}

TEST(PatrolGoalIndex, ClampsAtTheEndOfThePath)
{
  EXPECT_EQ(patrol_goal_index(kLap.size() - 1, /*moving=*/true, kLap.size()), kLap.size() - 1);
  EXPECT_EQ(patrol_goal_index(0, /*moving=*/true, 0u), 0u);   // 빈 경로에서 터지지 않는다
}
