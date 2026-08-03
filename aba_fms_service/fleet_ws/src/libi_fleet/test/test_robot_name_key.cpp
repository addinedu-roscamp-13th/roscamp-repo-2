// 로봇 이름 표기 차이를 흡수하는가.
//
// 배경(실기 2026-08-03): `/fms/node_block` 의 `robot` 은 **브릿지 키**(`pinky3`)로 오고,
// `fleet_node` 의 task 는 `RmfRobotState.name`(`pinky-3`)을 쓴다. 원문 비교였던
// `t.robot != m->robot` 이 **한 번도 안 맞아** 사람 차단 후퇴가 통째로 죽어 있었다:
//   · `moving=false` 가 안 내려가 1초짜리 목표 재전송이 **사람 쪽으로 계속** 나갔고
//   · 예약도 직전 정점으로 안 바뀌었다
// 화면에는 재계획된 경로가 그려지는데(`publish_routes` 는 매 틱 무조건 낸다) 로봇은
// 서 있었다 — 사용자가 본 그 증상이다.
//
// 실기 로그 그대로:
//   [block] 정점 8 차단 60s 사유=person owner=person:pinky3 로봇=pinky3
//   [P-pinky-3] pinky-3 → v8 (GRANT)
//
// ⚠️ 되돌림 확인: `fleet_task.hpp` 의 `norm_robot_name` 이 `s` 를 그대로 돌려주게 하면
//    `BridgeKeyMatchesRmfName` 이 빨개진다.
#include <gtest/gtest.h>

#include <string>

#include "libi_fleet/fleet_task.hpp"

using libi_fleet::norm_robot_name;

// 이게 결함 그 자체다 — 이 둘이 같아야 차단 보고가 task 를 찾는다.
TEST(RobotNameKey, BridgeKeyMatchesRmfName)
{
  EXPECT_EQ(norm_robot_name("pinky3"), norm_robot_name("pinky-3"));
  EXPECT_EQ(norm_robot_name("pinky1"), norm_robot_name("pinky-1"));
  EXPECT_EQ(norm_robot_name("PINKY_2"), norm_robot_name("pinky-2"));
}

// 그렇다고 **다른 로봇까지 같아지면** 안 된다 — 남의 차단으로 내 로봇을 세운다.
TEST(RobotNameKey, DifferentRobotsStayDifferent)
{
  EXPECT_NE(norm_robot_name("pinky-1"), norm_robot_name("pinky-2"));
  EXPECT_NE(norm_robot_name("pinky-3"), norm_robot_name("pinky-33"));
  EXPECT_NE(norm_robot_name("handy-1"), norm_robot_name("pinky-1"));
}

TEST(RobotNameKey, EmptyAndPunctuationOnly)
{
  EXPECT_EQ(norm_robot_name(""), "");
  EXPECT_EQ(norm_robot_name("---"), "");
  // 빈 이름끼리는 같아지지만, 그건 애초에 로봇 이름이 아니다 — 호출측이 거른다.
}

TEST(RobotNameKey, Idempotent)
{
  const std::string k = norm_robot_name("pinky-3");
  EXPECT_EQ(norm_robot_name(k), k);
}
