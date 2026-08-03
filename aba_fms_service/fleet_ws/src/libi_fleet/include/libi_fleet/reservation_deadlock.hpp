#pragma once
// 교통관제 = 노드 예약 + wait-for 그래프 DFS 교착 감지.
//   · 로봇은 자기가 서 있는 노드를 점유(claim)하고, 인접 노드로 이동 요청.
//   · GRANT 시 **출발 노드와 목표 노드를 둘 다** 쥔다. 출발 노드는 호출자가
//     목표에 **실제로 닿았을 때** 놓는다(fleet_node.cpp 의 도착 분기).
//   · 출발·목표 중 하나라도 남이 쥐고 있으면 대기.
//   · 대기가 사이클을 이루면(A→B, B→A …) DFS 로 감지 → DEADLOCK 반환(호출측이 우회).
//
// ## 왜 두 끝점을 다 쥐나 — 이것이 곧 간선(레인) 예약이다
//
// 예전 주석은 "타깃을 먼저 확보한 뒤 출발 노드를 놓으니 노드예약만으로 정면충돌이
// 전부 막힌다 → 엣지예약은 두지 않는다" 였다. **그 주장에는 숨은 전제가 있었다** —
// "모든 로봇이 자기가 선 정점을 실제로 쥐고 있다". 실행층은 그걸 보장하지 않는다
// (codex 판정 2026-08-03, P0). 아래 `from` 검증 주석에 재현 순서를 적어 뒀다.
//
// 그래서 `edge_owner_` 같은 표를 따로 두는 대신 **주행 중 두 끝점을 다 쥔다.**
// 레인 A—B 를 타려면 A 나 B 중 하나는 반드시 잡아야 하므로, 두 끝점 점유가 곧
// 그 레인의 상호배제다. 표를 안 만드는 이유는 **간선 수명을 표현할 통지가 없기**
// 때문이다 — "다음 칸 허가"(request_move)는 있어도 "구간을 실제로 벗어났다"는
// 통지가 인터페이스에 없다. 도착 이벤트는 호출자에게만 있고, 그쪽은 이미
// `release_node` 로 정점을 놓는다. 그 자리를 그대로 쓴다.
//
// ⚠️ 대가: 레인을 타는 동안 **출발 정점도 잠긴다.** 그 정점을 가로질러 지나가려던
//    제3의 로봇까지 기다린다(진짜 간선 예약이면 지나갈 수 있다). arte2 는 통로가
//    대부분 차수 2 라 손해가 작다고 보고 택했다. 처리량이 문제가 되면 그때
//    `edge_owner_`(무방향 레인 키)로 올린다 — 단, 그때는 "구간 이탈" 통지를
//    인터페이스에 먼저 만들어야 한다.
//
// [2026-07-26] plugins/reservation_deadlock.cpp 에서 이 헤더로 **그대로** 옮겼다(로직 무변경).
//   이유: cbs_traffic 이 계획이 낡았을 때 되돌아갈 안전 모드로 이 클래스를 **품어서** 쓴다.
//   .cpp 안에만 있으면 링크할 수가 없어 같은 로직을 한 벌 더 쓰게 된다 — 그건 교착 판정이
//   두 곳에서 갈라진다는 뜻이라 더 위험하다.

#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "libi_fleet/traffic_base.hpp"

namespace libi_fleet
{

class ReservationDeadlock : public TrafficBase
{
public:
  MoveDecision request_move(const std::string & robot, int from, int to, int priority) override
  {
    prio_[robot] = priority;   // 로봇의 현재 우선순위 갱신

    // 현재 노드 점유(claim): from==to. 시작 노드 확보에 사용.
    if (from == to) {
      auto it = node_owner_.find(to);
      if (it == node_owner_.end() || it->second == robot) {
        node_owner_[to] = robot;
        waitfor_.erase(robot);
        return MoveDecision::GRANT;
      }
      return MoveDecision::WAIT;
    }

    // ── 출발 정점도 본다 ────────────────────────────────────────────────────
    //
    // [2026-08-03] 예전에는 `to` 만 봤다. 그래서 **자기가 선 정점을 안 쥔 로봇**이
    // 그 자리를 떠나는 허가를 받았다(codex 판정 P0. 재현 순서):
    //   ① r1 이 A→B 를 받아 B 를 쥐고 출발한다
    //   ② B 에 그냥 서 있던 r2 는 B 를 쥔 적이 없다 — claim 은 배차·순회·정지 때만
    //      하고 그 반환값마저 버린다(fleet_node.cpp:699)
    //   ③ r2 에 B→A 가 배차되면 A 만 비어 있으므로 GRANT — **같은 레인을 마주 본다**
    // 출발지를 쥔 로봇만 떠날 수 있게 하면 ②에서 끊긴다.
    //
    // ⚠️ 비어 있으면 **거부하지 않고 그 자리에서 claim 한다.** 배차 claim 이 실패했거나
    //    유령 정리로 예약이 풀린 로봇이 영영 못 움직이게 되면 안 된다.
    // ⚠️ `too_close_` 는 보지 않는다 — 출발지는 로봇이 이미 서 있는 자리다. 겹친 이웃을
    //    이유로 **빠져나가는 것**까지 막으면 그 상태에서 벗어날 길이 없다.
    auto fit = node_owner_.find(from);
    if (fit == node_owner_.end()) { node_owner_[from] = robot; }
    else if (fit->second != robot) { return wait_on(robot, fit->second); }

    // 경합 상대 찾기: 목표 노드 점유자.
    std::string blocker = owner_of_node(to, robot);

    if (blocker.empty()) {
      node_owner_[to] = robot;              // 목표 노드 예약
      waitfor_.erase(robot);
      return MoveDecision::GRANT;
    }
    return wait_on(robot, blocker);
  }

  void release(const std::string & robot, int node) override
  {
    auto it = node_owner_.find(node);
    if (it != node_owner_.end() && it->second == robot) { node_owner_.erase(it); }
    waitfor_.erase(robot);
  }

  void release_node(const std::string & robot, int node) override
  {
    auto it = node_owner_.find(node);
    if (it != node_owner_.end() && it->second == robot) { node_owner_.erase(it); }
    waitfor_.erase(robot);
  }

  std::vector<std::pair<int, std::string>> occupancy() const override
  {
    std::vector<std::pair<int, std::string>> out;
    for (const auto & kv : node_owner_) { out.emplace_back(kv.first, kv.second); }
    return out;
  }

private:
  // 남이 쥔 자리에 막혔다 → wait-for 갱신 후 사이클 검사.
  MoveDecision wait_on(const std::string & robot, const std::string & blocker)
  {
    waitfor_[robot] = blocker;
    std::vector<std::string> cyc;
    if (find_cycle(robot, cyc)) {
      // 사이클 내 최저 우선순위(동률이면 이름 큰 쪽)가 양보 = 우회.
      if (lowest_priority(cyc) == robot) {
        waitfor_.erase(robot);   // 이 로봇이 양보자 → 물러나(우회)므로 대기변 제거
        return MoveDecision::DEADLOCK;
      }
      // 우선순위 높음 → 낮은 쪽이 빠질 때까지 대기 후 직진.
    }
    return MoveDecision::WAIT;
  }

  std::string owner_of_node(int node, const std::string & self) const
  {
    auto it = node_owner_.find(node);
    if (it != node_owner_.end() && it->second != self) { return it->second; }
    // [2026-08-01] **물리적으로 겹치는 이웃도 본다.** 정점이 로봇 지름보다 가까우면
    // "다른 정점"이어도 두 대가 동시에 있을 수 없다(traffic_base.hpp 의 set_min_separation).
    // 설정하지 않으면 목록이 비어 있어 예전과 완전히 같은 동작이다.
    for (int n : too_close_to(node)) {
      auto jt = node_owner_.find(n);
      if (jt != node_owner_.end() && jt->second != self) { return jt->second; }
    }
    return "";
  }

  // waitfor_ 는 각 로봇이 정확히 1명을 기다리는 함수형 그래프.
  // start 에서 따라가 start 로 돌아오면 사이클(교착). out 에 사이클 멤버 채움.
  bool find_cycle(const std::string & start, std::vector<std::string> & out) const
  {
    out.clear();
    std::string cur = start;
    std::set<std::string> seen;
    for (;;) {
      out.push_back(cur);
      auto it = waitfor_.find(cur);
      if (it == waitfor_.end()) { return false; }        // 막다른 길 → 교착 아님
      cur = it->second;
      if (cur == start) { return true; }                 // start 복귀 → 사이클
      if (!seen.insert(cur).second) { return false; }    // start 무관한 다른 사이클
    }
  }

  int prio_of(const std::string & r) const
  {
    auto it = prio_.find(r);
    return it == prio_.end() ? 0 : it->second;
  }
  // 사이클 내 최저 우선순위 로봇(동률이면 이름이 큰 쪽) = 양보자.
  const std::string & lowest_priority(const std::vector<std::string> & cyc) const
  {
    const std::string * v = &cyc[0];
    for (const auto & r : cyc) {
      if (prio_of(r) < prio_of(*v) || (prio_of(r) == prio_of(*v) && r > *v)) { v = &r; }
    }
    return *v;
  }

  std::map<int, std::string> node_owner_;      // 노드 → 점유 로봇
  std::map<std::string, std::string> waitfor_; // 로봇 → 기다리는 상대
  std::map<std::string, int> prio_;            // 로봇 → 현재 우선순위
};

}  // namespace libi_fleet
