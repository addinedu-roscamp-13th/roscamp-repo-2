// 마감을 넘긴 칸을 **어디까지** 찾는가.
//
// 실기 결함(2026-08-03): `fleet_node` 가 커밋 칸(`t.idx`) 하나만 검사했다. 관제의 예약
// 표는 `FleetPlan.arrive_tick >= 0` 인 **모든 칸**에 카운트다운을 찍으므로
// (`WaypointEditor.tsx:908-921`), 로봇이 아직 안 닿은 먼 칸이 0을 지나 `지연 +16.3s` 로
// 빨갛게 떠 있는데 **아무도 재계획을 안 거는** 상태가 생겼다. 실제 화면에서
// `순회경로-4 +6.3s` 와 `순회경로-5 +16.3s` 가 동시에 초과였다.
//
// 이 시험은 `first_overdue_cell` 하나만 본다. 그 함수가 `fleet_node` 에서 판정을 통째로
// 가져갔으므로, 되돌리면(= `from_idx` 한 칸만 보게 하면) 아래가 빨개진다.
#include <gtest/gtest.h>
#include <vector>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::first_overdue_cell;

namespace
{
// 편의: epoch 0, tick 1s, slack 5s 로 고정한다(실기 drift_limit=5, tick_sec=1 과 같다).
constexpr double kEpoch = 0.0;
constexpr double kTick = 1.0;
constexpr double kSlack = 5.0;

std::size_t scan(const std::vector<int> & path, const std::vector<int> & arrive,
                 std::size_t from, double now)
{
  return first_overdue_cell(path, arrive, from, kEpoch, kTick, kSlack, now);
}
}  // namespace

TEST(PlanDeadlineScan, NothingOverdueReturnsEnd)
{
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{0, 6, 12};
  // now=8 → v11 마감은 6+5=11, v12 는 12+5=17. 둘 다 안 지났다.
  EXPECT_EQ(scan(path, arrive, 1, 8.0), 3u);
}

TEST(PlanDeadlineScan, CommittedCellOverdueIsFound)
{
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{0, 6, 12};
  // now=12 → v11 마감 11 을 지났다.
  EXPECT_EQ(scan(path, arrive, 1, 12.0), 1u);
}

TEST(PlanDeadlineScan, FutureCellOverdueIsFoundEvenWhenCommittedIsNot)
{
  // ⚠️ 이게 실기 결함 그 자체다 — 예전 코드는 여기서 아무것도 못 찾았다.
  //    커밋 칸은 아직 기한 안인데 먼 칸이 이미 지난 상태를 만든다(비단조 arrive).
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{0, 30, 2};   // v11 은 여유가 크고 v12 가 이미 지났다
  // now=10 → v11 마감 35(안 지남), v12 마감 7(지남).
  EXPECT_EQ(scan(path, arrive, 1, 10.0), 2u);
}

TEST(PlanDeadlineScan, EarliestOverdueWins)
{
  const std::vector<int> path{10, 11, 12, 13};
  const std::vector<int> arrive{0, 1, 2, 3};
  // now=100 → 전부 초과. 가장 이른 칸(= from_idx)을 돌려줘야 로그가 원인에 가깝다.
  EXPECT_EQ(scan(path, arrive, 1, 100.0), 1u);
}

TEST(PlanDeadlineScan, NegativeArriveIsSkipped)
{
  // -1 = 이미 떠난 칸 · CBS 가 안 짠 순회 꼬리. 화면도 라벨을 안 붙인다.
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{-1, -1, 2};
  EXPECT_EQ(scan(path, arrive, 0, 100.0), 2u);
}

TEST(PlanDeadlineScan, AllNegativeMeansNothingToWatch)
{
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{-1, -1, -1};
  EXPECT_EQ(scan(path, arrive, 0, 1e9), 3u);
}

TEST(PlanDeadlineScan, CellsBeforeFromIdxAreIgnored)
{
  // 이미 지나온 칸은 늦었어도 재계획 사유가 아니다 — 로봇은 벌써 그 앞에 있다.
  const std::vector<int> path{10, 11, 12};
  const std::vector<int> arrive{0, 1, 900};
  EXPECT_EQ(scan(path, arrive, 2, 100.0), 3u);
}

TEST(PlanDeadlineScan, ExactlyAtDueIsNotOverdue)
{
  // 기존 `if (now_sec() <= due) return;` 을 그대로 옮겼다 — 같은 순간은 아직 아니다.
  const std::vector<int> path{10, 11};
  const std::vector<int> arrive{0, 6};
  EXPECT_EQ(scan(path, arrive, 1, 11.0), 2u);   // due = 6+5 = 11
  EXPECT_EQ(scan(path, arrive, 1, 11.001), 1u);
}

TEST(PlanDeadlineScan, PatrolTailLongerThanArriveDoesNotOverrun)
{
  // 순회는 계획보다 path 가 길다(canonical 랩 꼬리). 짧은 쪽이 경계여야 한다 —
  // 아니면 arrive 범위 밖을 읽는다.
  const std::vector<int> path{10, 11, 12, 13, 14, 15};
  const std::vector<int> arrive{-1, 2};
  EXPECT_EQ(scan(path, arrive, 1, 100.0), 1u);
  const std::vector<int> none{-1, -1};
  EXPECT_EQ(scan(path, none, 1, 100.0), 2u);   // 경계는 min(2, 6) = 2
}

TEST(PlanDeadlineScan, EmptyPlanIsSafe)
{
  EXPECT_EQ(scan({}, {}, 0, 100.0), 0u);
  const std::vector<int> path{10, 11};
  EXPECT_EQ(scan(path, {}, 5, 100.0), 0u);   // from_idx 가 경계 밖이어도 안전
}
