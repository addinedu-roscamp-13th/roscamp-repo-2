// 모든 경로는 노드를 하나 지날 때마다 CBS 시간표를 새로 잡아야 한다.
// 그렇지 않으면 `routes`는 현재 노드를 내보내는데 `FleetPlan`은 최초 계획에 남아 있어
// 관제의 예약 노드·지연 표가 뒤처진다.
#include <gtest/gtest.h>

#include "libi_fleet/fleet_task.hpp"

using libi_fleet::refresh_plan_after_arrival;
using libi_fleet::planner_apply_anchor_index;

TEST(PlanRefresh, WorkingTaskRefreshesWhenAnotherLegRemains)
{
  // 0→1에 도착해서 다음 목적지(2)가 남은 상태.
  EXPECT_TRUE(refresh_plan_after_arrival(false, 2, 3));
}

TEST(PlanRefresh, FinalWorkingArrivalDoesNotStartAPlanForDeletedTask)
{
  // 마지막 정점에 도착하면 task가 곧 완료·삭제된다.
  EXPECT_FALSE(refresh_plan_after_arrival(false, 3, 3));
}

TEST(PlanRefresh, PatrolAlsoRefreshesWhenAnotherLegRemains)
{
  // 순회도 다음 한 노드 계획을 즉시 새로 잡아야 화면과 실제 목표가 같다.
  EXPECT_TRUE(refresh_plan_after_arrival(true, 2, 3));
}

TEST(PlanRefresh, FinalPatrolArrivalDoesNotPlanBeforeLoopRebuild)
{
  // 마지막 도달 직후에는 아래 실행 루프가 랩을 재생성한다.
  EXPECT_FALSE(refresh_plan_after_arrival(true, 3, 3));
}

TEST(PlanRefresh, MovingPlanUsesCommittedTargetAsStaleResultAnchor)
{
  // 이동 중 결과의 첫 점은 현재 향하던 커밋 노드다.
  EXPECT_EQ(planner_apply_anchor_index(true, 3), 0u);
}

TEST(PlanRefresh, WaitingPlanUsesNextTargetAsStaleResultAnchor)
{
  // 대기 중 결과의 첫 점은 이미 서 있는 출발 노드라 두 번째 점을 본다.
  EXPECT_EQ(planner_apply_anchor_index(false, 3), 1u);
  EXPECT_EQ(planner_apply_anchor_index(false, 1), 1u);
}
