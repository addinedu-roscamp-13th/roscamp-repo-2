// 새 계획 앞에 **진행 중인 한 칸**을 살려 붙일지.
//
// 실기 결함(2026-08-07, pinky-3 순회): `t.moving` 하나만 보고 붙였다. 통과 직후
// 스냅샷은 `moving=false` 라 `plan_start_for` 가 **서 있는 정점**을 출발점으로 주는데,
// CBS 워커가 도는 사이 GRANT 가 와서 `moving=true` 로 뒤집히면 그 정점이 한 번 더
// 앞에 붙는다. `np = [v8, v8, v7 …]` 에 `idx = 1` 이라 **방금 지난 v8 이 다시 목표**다.
//
//   [P-pinky-3] pinky-3 선행통과 v8       ← 하이라이트가 다음 노드로
//   [P-pinky-3] pinky-3 시간표 재계획 → 11 nodes
//   [P-pinky-3] pinky-3 통과     v8       ← 1.5s 뒤, 자기 노드로 되감김
//
// 두 번째가 `선행통과` 가 아니라 `통과` 로 찍히는 것이 지문이다 — 그 라벨은
// `0.5 × lane <= arrive_radius_`, 즉 앞뒤 정점이 같을 때만 나온다(fleet_node.cpp:1567).
//
// 되돌리면(= `moving` 만 보게 하면) `StalePlanFromWhereItStandsIsNotDoubled` 가 빨개진다.
#include <gtest/gtest.h>
#include <vector>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::should_prepend_commit_head;

TEST(PlanCommitHead, StandingRobotKeepsThePlanAsIs)
{
  // 아직 안 떠났다 — 계획이 그대로 실행 경로다.
  EXPECT_FALSE(should_prepend_commit_head({8, 7, 4}, /*moving=*/false, 8));
}

TEST(PlanCommitHead, RidingPlanGetsTheCommittedEdgeBack)
{
  // 스냅샷도 이동 중이었다 → 계획이 **커밋 정점** v7 에서 시작한다. 로봇이 떠나온
  // v8 을 앞에 붙여야 `idx = 1` 이 v7 을 가리킨다.
  EXPECT_TRUE(should_prepend_commit_head({7, 4, 5}, /*moving=*/true, 8));
}

TEST(PlanCommitHead, StalePlanFromWhereItStandsIsNotDoubled)
{
  // ⚠️ 실기 결함 그 자체. 통과 직후 스냅샷이라 계획이 **서 있는 정점** v8 에서
  //    시작했는데, 결과가 올 때는 GRANT 로 moving 이 참이 돼 있다.
  EXPECT_FALSE(should_prepend_commit_head({8, 7, 4}, /*moving=*/true, 8))
    << "이미 그 정점에서 시작하는 계획에 같은 정점을 또 붙이면 목표가 되감긴다";
}

TEST(PlanCommitHead, EmptyPlanFallsBackToPrepending)
{
  // 붙일 게 없으면 판단 근거가 없다 — 예전대로 붙인다(뒤의 크기 검사가 걸러 낸다).
  EXPECT_TRUE(should_prepend_commit_head({}, /*moving=*/true, 8));
  EXPECT_FALSE(should_prepend_commit_head({}, /*moving=*/false, 8));
}
