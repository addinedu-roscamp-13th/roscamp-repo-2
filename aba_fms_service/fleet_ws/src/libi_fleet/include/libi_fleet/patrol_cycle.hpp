#pragma once
#include <vector>
#include "libi_fleet/navgraph.hpp"

namespace libi_fleet
{

// "오른쪽 우선 → 아래 우선"(우수법 경계 순회)으로 그래프 외곽을 도는 canonical 방향 루프를
// 계산한다. 시작 = 최상단(y 최대), 동률 시 최좌측(x 최소). 시작 진행 방향 = +x(오른쪽).
// 각 노드에서 들어온 방향 기준 가장 오른쪽으로 꺾는 이웃을 택한다(막다른 곳에서만 되돌아감).
// 반환: 방문 노드열(시작 노드로 닫지 않음). 사이클 형성 실패 시 빈 벡터.
std::vector<int> right_hand_boundary_cycle(const Navgraph & g);

// route(정점 인덱스 열)의 shoelace signed area × 2 (월드 좌표, y 위쪽 기준).
//   > 0 → 반시계(CCW),  < 0 → 시계(CW),  == 0 → 방향 없음(직선/점).
// 순회 방향을 CCW 로 고정할 때 winding 판정에 쓴다.
double signed_area_2x(const Navgraph & g, const std::vector<int> & route);

// 순회 재계획 결과(계획 구간) 뒤에 canonical 랩 꼬리를 이어 붙일 **시작 인덱스**.
//
// 순회는 랩 전체를 계획하지 않고 앞쪽 한 구간만 CBS 에 맡긴 뒤, 그 뒤를 원래 랩으로
// 이어 붙인다. 이을 위치는 **계획이 실제로 도달한 정점(plan_goal)의 다음**이다.
//
// ⚠️ 이걸 `idx + 2` 로 고정하면 안 된다. 순회 목표는 보통 `idx+1` 이지만 다른 순회
//    로봇과 목표가 겹치면 랩을 따라 **더 뒤로 밀린다**. 그때 그 사이 정점들이 계획
//    구간과 꼬리에 두 번 들어가 랩이 뒤죽박죽이 된다(순회 로봇이 둘 이상일 때만 발생).
//
// plan_goal 을 path[idx..] 에서 못 찾으면 옛 동작(idx+2)으로 되돌린다.
std::size_t patrol_tail_index(const std::vector<int> & path, std::size_t idx, int plan_goal);

}  // namespace libi_fleet
