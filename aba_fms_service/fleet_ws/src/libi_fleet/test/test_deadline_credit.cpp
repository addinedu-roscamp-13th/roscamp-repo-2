// 정점에 도달했을 때 **어느 간선**에 마감 준수 크레딧을 주는가.
//
// 실기 결함(codex 적대적 검토 2026-08-02): `fleet_node` 가 `t.idx++` 를 **먼저** 한 뒤
//   note_deadline_kept(t.path[t.idx - 1], t.path[t.idx]);
// 를 불러서 세 가지가 한꺼번에 틀어져 있었다.
//   ① 크레딧이 방금 지나온 간선이 아니라 앞으로 갈 간선에 붙는다
//   ② `missed_idx` 는 도달 **전** 인덱스로 찍히는데 올린 값과 비교해 절대 안 맞는다
//   ③ 마지막 정점에서 `path[idx]` 가 범위 밖 접근(UB)
//
// 이 시험은 `traversed_edge_to_credit` 하나만 본다. 그 함수가 `fleet_node` 에서 그
// 판정을 통째로 가져갔으므로, 되돌리면(= 올린 인덱스를 넘기면) 아래가 빨개진다.
#include <gtest/gtest.h>
#include <vector>
#include "libi_fleet/fleet_task.hpp"

using libi_fleet::traversed_edge_to_credit;

namespace
{
// 정점 4개짜리 경로. 7 → 4 → 5 → 11.
const std::vector<int> kPath = {7, 4, 5, 11};
}  // namespace

// ① 크레딧은 **방금 지나온** 간선에 붙는다.
TEST(DeadlineCredit, CreditsTheEdgeJustTraversed) {
  int from = -1, to = -1;
  // path[2](=5) 에 도달했다 → 지나온 간선은 4→5 다. 5→11 이 아니다.
  ASSERT_TRUE(traversed_edge_to_credit(kPath, /*arrived_idx=*/2, /*missed_from=*/-1, /*missed_to=*/-1, from, to));
  EXPECT_EQ(from, 4);
  EXPECT_EQ(to, 5);
  // 옛 코드는 여기서 (5, 11) 을 줬다 — 아직 가 보지도 않은 간선의 벌점을 지웠다.
}

// ② 늦었던 **간선**과 맞춰야 한다. 그 간선으로 늦게 온 것에는 크레딧이 없다.
TEST(DeadlineCredit, NoCreditWhenThatEdgeWasAlreadyLate) {
  int from = -1, to = -1;
  // 지나온 간선은 4→5. 늦었다고 표시된 것도 4→5 → 크레딧 없음.
  EXPECT_FALSE(traversed_edge_to_credit(kPath, /*arrived_idx=*/2, /*missed_from=*/4, /*missed_to=*/5, from, to));
  EXPECT_EQ(from, -1) << "false 를 돌려주면서 출력을 건드렸다";
  EXPECT_EQ(to, -1);
}

// 다른 간선에서 늦은 것은 이 간선의 크레딧을 막지 않는다.
TEST(DeadlineCredit, LatenessOnAnotherEdgeDoesNotBlockThisOne) {
  int from = -1, to = -1;
  // 지나온 간선은 4→5, 늦었던 것은 7→4 → 서로 다르니 크레딧을 준다.
  ASSERT_TRUE(traversed_edge_to_credit(kPath, /*arrived_idx=*/2, /*missed_from=*/7, /*missed_to=*/4, from, to));
  EXPECT_EQ(from, 4);
  EXPECT_EQ(to, 5);
}

// ⚠️ **인덱스로 비교하면 못 잡고 간선으로 비교해야 잡히는 경우.**
//
// 재계획이 적용되면 `t.idx` 가 1 로 되감긴다. 옛 코드는 그 인덱스를 들고 비교해서,
// 값이 우연히 겹치면 **엉뚱한 간선**을 "이미 늦었다" 로 보고 크레딧을 건너뛰었다.
// 여기서는 인덱스가 같아도(둘 다 1) 간선이 다르면 크레딧이 나가야 한다.
TEST(DeadlineCredit, SameIndexDifferentEdgeStillGetsCredit) {
  int from = -1, to = -1;
  // path[1] 도달 → 지나온 간선 7→4. 늦었던 것은 10→9(다른 랩의 같은 인덱스라고 하자).
  ASSERT_TRUE(traversed_edge_to_credit(kPath, /*arrived_idx=*/1, /*missed_from=*/10, /*missed_to=*/9, from, to));
  EXPECT_EQ(from, 7);
  EXPECT_EQ(to, 4);
}

// ③ 마지막 정점 — 옛 코드가 `path[path.size()]` 를 읽던 자리.
TEST(DeadlineCredit, LastNodeStaysInRange) {
  int from = -1, to = -1;
  // 마지막 정점(11, idx=3)에 도달 → 지나온 간선 5→11. 범위 밖 접근이 없어야 한다.
  ASSERT_TRUE(traversed_edge_to_credit(kPath, /*arrived_idx=*/3, /*missed_from=*/-1, /*missed_to=*/-1, from, to));
  EXPECT_EQ(from, 5);
  EXPECT_EQ(to, 11);
  // 그리고 **그 너머**를 물으면 조용히 거절한다(옛 코드가 UB 를 내던 입력).
  EXPECT_FALSE(traversed_edge_to_credit(kPath, /*arrived_idx=*/4, -1, -1, from, to));
  EXPECT_FALSE(traversed_edge_to_credit(kPath, /*arrived_idx=*/99, -1, -1, from, to));
}

// 출발 정점(idx=0)에는 지나온 간선이 없다.
TEST(DeadlineCredit, StartNodeHasNoTraversedEdge) {
  int from = -1, to = -1;
  EXPECT_FALSE(traversed_edge_to_credit(kPath, /*arrived_idx=*/0, -1, -1, from, to));
}

// 빈 경로에서도 죽지 않는다.
TEST(DeadlineCredit, EmptyPathIsSafe) {
  int from = -1, to = -1;
  EXPECT_FALSE(traversed_edge_to_credit({}, 0, -1, -1, from, to));
  EXPECT_FALSE(traversed_edge_to_credit({}, 1, -1, -1, from, to));
}
