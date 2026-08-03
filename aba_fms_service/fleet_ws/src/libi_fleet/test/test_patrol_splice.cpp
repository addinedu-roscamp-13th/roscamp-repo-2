// 순회 재계획 결과 뒤에 canonical 랩을 이어 붙이는 지점(patrol_tail_index)을 잡는다.
//
// 실기 증상: "순회 경로가 계속 막 바뀐다 / 반시계로 돌라고 했는데 지 멋대로 돈다".
// 원인: 꼬리 시작을 `idx + 2` 로 고정했는데, 순회 목표는 다른 순회 로봇과 겹치면
//       랩을 따라 뒤로 밀린다(fleet_node replan_all_routes 의 taken_goals 분기).
//       그러면 밀린 만큼의 정점이 계획 구간과 꼬리에 **두 번** 들어간다.
// 순회 로봇이 하나뿐이면 목표가 안 밀려 절대 안 드러난다 — 1대 시험이 초록이었던 이유다.
#include <gtest/gtest.h>
#include <set>
#include <vector>
#include "libi_fleet/patrol_cycle.hpp"

using libi_fleet::patrol_tail_index;

namespace
{
// 실기 순회 루프(arte2, CCW). start_patrol 이 만드는 path 모양: 진입점 + 랩 + 루프 닫기.
const std::vector<int> kLap = {7, 4, 5, 11, 13, 12, 14, 10, 9, 8, 7};

// fleet_node 가 계획 결과에 꼬리를 잇는 것과 같은 조립.
// plan = CBS 가 돌려준 구간(마지막이 계획 목표), 반환 = 새 t.path.
std::vector<int> splice(const std::vector<int> & path, std::size_t idx,
                        const std::vector<int> & plan)
{
  std::vector<int> np = plan;
  for (std::size_t k = patrol_tail_index(path, idx, plan.back()); k < path.size(); ++k) {
    np.push_back(path[k]);
  }
  return np;
}
}  // namespace

// 목표가 안 밀린 평상시 — 옛 동작(idx+2)과 결과가 같아야 한다. 회귀 방지.
TEST(PatrolSplice, UnshiftedGoalKeepsOldBehaviour) {
  const std::size_t idx = 3;                     // path[3] = 11, 목표는 path[4] = 13
  const std::vector<int> plan = {5, 11, 13};     // start=path[2], goal=path[4]
  EXPECT_EQ(patrol_tail_index(kLap, idx, plan.back()), idx + 2);
  EXPECT_EQ(splice(kLap, idx, plan),
            (std::vector<int>{5, 11, 13, 12, 14, 10, 9, 8, 7}));
}

// 목표가 겹쳐 뒤로 밀린 경우 — 정점이 두 번 들어가면 안 된다.
// 옛 코드(idx+2)면 12, 14 가 중복돼 `… 12 14 10 | 12 14 10 …` 이 된다.
TEST(PatrolSplice, ShiftedGoalDoesNotDuplicateLapNodes) {
  const std::size_t idx = 4;                     // path[4] = 13
  // path[5]=12 가 남의 목표라 path[7]=10 까지 밀렸다고 하자.
  const std::vector<int> plan = {11, 13, 12, 14, 10};
  EXPECT_EQ(patrol_tail_index(kLap, idx, plan.back()), 8u);   // 10 의 다음 = 9

  const std::vector<int> got = splice(kLap, idx, plan);
  EXPECT_EQ(got, (std::vector<int>{11, 13, 12, 14, 10, 9, 8, 7}));

  // 이 조립은 랩 중간(11)에서 시작해 루프 닫는 7 로 끝나므로 중복이 하나도 없어야 한다.
  // 옛 코드(idx+2 고정)면 12·14 가 두 번 들어가 size 가 2 커진다.
  std::set<int> uniq(got.begin(), got.end());
  EXPECT_EQ(got.size(), uniq.size()) << "랩 정점이 두 번 들어갔다";
}

// 계획 목표가 랩에 없으면(비정상) 옛 동작으로 안전하게 되돌아간다.
TEST(PatrolSplice, UnknownGoalFallsBackToIdxPlusTwo) {
  EXPECT_EQ(patrol_tail_index(kLap, 4, /*plan_goal=*/999), 6u);
}

// 목표가 현재 위치보다 **뒤에** 있어도(랩이 닫혀 같은 정점이 두 번 있음) idx 앞쪽에서
// 찾아야 한다 — 뒤를 집으면 꼬리가 통째로 잘려 순회가 조기 종료된다.
TEST(PatrolSplice, SearchesForwardFromCurrentIndex) {
  // 7 은 path[0] 과 path[10] 두 곳에 있다. idx=4 에서 찾으면 뒤쪽(10)을 집어야 한다.
  EXPECT_EQ(patrol_tail_index(kLap, 4, 7), 11u);
  EXPECT_EQ(patrol_tail_index(kLap, 0, 7), 1u);
}
