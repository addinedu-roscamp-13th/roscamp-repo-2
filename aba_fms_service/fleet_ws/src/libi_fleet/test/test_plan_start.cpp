// 재계획할 때 이 로봇의 경로를 **어디서부터** 짜는가.
//
// 실기 결함(2026-08-03): 사람이 막은 정점은 곧 로봇이 향하던 `path[idx]` 인데,
// 차단 때 `moving` 을 아무도 내리지 않아 그 노드를 계획 출발점으로 줬다. CBS 가
// "로봇이 이미 거기 도착했다" 고 가정하고 그 **너머**에서 새 경로를 짜서, 첫 홉이
// 사람 뒤에서 시작했다. 로봇에 내려보내면 nav2 가 사람을 뚫으려다 실패한다:
//   GridBased plugin failed to plan from (0.55,-1.15) to (0.60,-0.68):
//     "Failed to create plan with tolerance of: 0.100000"
//
// 이 시험은 `plan_start_for` 하나만 본다. 되돌리면(= 차단 검사를 지우면) 아래가 빨개진다.
#include <gtest/gtest.h>
#include <vector>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::plan_start_for;

namespace
{
struct Out { int start{-99}; int from{-99}; bool ok{false}; };

Out run(const std::vector<int> & path, std::size_t idx, bool moving,
        const std::vector<int> & blocked)
{
  Out o;
  o.ok = plan_start_for(path, idx, moving, blocked, o.start, o.from);
  return o;
}
}  // namespace

TEST(PlanStart, StandingPlansFromWhereItStands)
{
  const auto o = run({10, 11, 12}, 1, /*moving=*/false, {});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 10);   // 서 있는 정점
  EXPECT_EQ(o.from, -1);    // 타고 있는 간선 없음
}

TEST(PlanStart, MovingPlansFromTheCommittedNode)
{
  const auto o = run({10, 11, 12}, 1, /*moving=*/true, {});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 11);   // 향해 가는 정점 — 커밋 구간 존중
  EXPECT_EQ(o.from, 10);    // 그 간선의 예산을 받으려면 출발점을 알려야 한다
}

TEST(PlanStart, BlockedCommitFallsBackToWhereTheRobotReallyIs)
{
  // ⚠️ 실기 결함 그 자체. v11 이 사람으로 막혔다 — 로봇은 v10 과 v11 사이에 서 있다.
  const auto o = run({10, 11, 12}, 1, /*moving=*/true, {11});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 10) << "차단된 커밋 노드에서 계획을 시작하면 안 된다";
  EXPECT_EQ(o.from, -1) << "끝내지 못할 간선에 예산을 주면 안 된다";
}

TEST(PlanStart, BlockedElsewhereDoesNotChangeTheStart)
{
  // 커밋 노드가 아닌 다른 정점이 막힌 것은 출발점과 무관하다 — 계획이 알아서 피한다.
  const auto o = run({10, 11, 12}, 1, /*moving=*/true, {12, 77});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 11);
  EXPECT_EQ(o.from, 10);
}

TEST(PlanStart, StandingIsUnaffectedByBlocks)
{
  // 서 있을 때는 이미 `path[idx-1]` 에서 짠다 — 차단이 그 판정을 바꿀 이유가 없다.
  const auto o = run({10, 11, 12}, 1, /*moving=*/false, {11});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 10);
  EXPECT_EQ(o.from, -1);
}

TEST(PlanStart, MidPathBlockUsesThePrecedingNode)
{
  const auto o = run({10, 11, 12, 13}, 2, /*moving=*/true, {12});
  EXPECT_TRUE(o.ok);
  EXPECT_EQ(o.start, 11);
  EXPECT_EQ(o.from, -1);
}

TEST(PlanStart, OutOfRangeIsRejectedWithoutTouchingOutputs)
{
  Out o;
  o.start = -99; o.from = -99;
  EXPECT_FALSE(plan_start_for({10, 11}, 0, true, {}, o.start, o.from));
  EXPECT_EQ(o.start, -99);
  EXPECT_EQ(o.from, -99);
  EXPECT_FALSE(plan_start_for({10, 11}, 2, true, {}, o.start, o.from));
  EXPECT_FALSE(plan_start_for({}, 1, true, {}, o.start, o.from));
  EXPECT_EQ(o.start, -99) << "거부했으면 출력에 손대지 않아야 한다";
}
