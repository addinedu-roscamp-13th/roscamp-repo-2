// 재계획 **사유**를 누가 정하는가.
//
// 배경(codex 3차 P1, 2026-08-03): 예전에는 `replan_requested_ = true` 만 세우고 사유는
// `traffic_->last_demote_reason()` 에서 꺼내 썼다. 그 값은 **끈적하다** — 마지막 강등
// 사유가 계속 남는다. 그래서 fleet_node 가 스스로 건 재계획(마감 초과·차단 해제·작업
// 정리)에 **직전 강등의 사유가 그대로 붙어** 화면에 거짓 원인이 떴다.
//
// 관제에서 "이 재계획이 사람 때문인가 지연 때문인가" 를 가리려고 사유를 내보내는
// 것이므로, 그게 틀리면 통로 자체가 무의미하다.
//
// `fleet_node.cpp` 의 `service_replan_requests()` 안에 있는 선택 규칙만 떼어 본다.
// 그 함수는 `FleetNode` 내부라 직접 못 잡는다 — 규칙이 바뀌면 여기도 같이 바꿔야 한다.
#include <gtest/gtest.h>
#include <string>

namespace
{
// fleet_node.cpp 의 선택 규칙 — 원문과 **같은 순서**로 적는다.
//   const bool by_plugin = traffic_->needs_replan();
//   std::string why = by_plugin ? traffic_->last_demote_reason() : requested_why_;
//   if (why.empty()) { why = by_plugin ? "계획 강등" : "재계획 요청"; }
std::string pick_reason(bool by_plugin, const std::string & demote,
                        const std::string & requested)
{
  std::string why = by_plugin ? demote : requested;
  if (why.empty()) { why = by_plugin ? "계획 강등" : "재계획 요청"; }
  return why;
}
}  // namespace

TEST(ReplanReason, PluginDemoteUsesItsOwnReason)
{
  EXPECT_EQ(pick_reason(true, "계획 대비 지연", ""), "계획 대비 지연");
}

TEST(ReplanReason, OurRequestUsesOurReasonNotTheStickyDemote)
{
  // ⚠️ 이게 결함 그 자체다 — 옛 강등 사유가 남아 있어도 우리 사유가 이겨야 한다.
  EXPECT_EQ(pick_reason(false, "계획 로봇의 경로 이탈", "도착 마감 초과"),
            "도착 마감 초과");
  EXPECT_EQ(pick_reason(false, "계획 대비 지연", "정점 차단 해제"), "정점 차단 해제");
  EXPECT_EQ(pick_reason(false, "계획 대비 지연", "작업 정리"), "작업 정리");
}

TEST(ReplanReason, EmptyFallsBackToWhoTriggeredIt)
{
  // 사유를 아무도 안 남겼으면 최소한 **누가 걸었는지**는 구분되어야 한다.
  EXPECT_EQ(pick_reason(true, "", ""), "계획 강등");
  EXPECT_EQ(pick_reason(false, "", ""), "재계획 요청");
}

TEST(ReplanReason, StickyDemoteNeverLeaksIntoOurRequest)
{
  // 강등 사유가 무엇이든, 우리 요청에는 절대 안 붙는다.
  for (const char * d : {"계획 대비 지연", "계획 로봇의 경로 이탈", "해 없음", ""}) {
    EXPECT_EQ(pick_reason(false, d, "도착 마감 초과"), "도착 마감 초과") << "demote=" << d;
  }
}
