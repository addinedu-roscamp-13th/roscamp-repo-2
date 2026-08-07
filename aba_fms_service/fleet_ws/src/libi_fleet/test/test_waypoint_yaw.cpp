// 실제 지도(arte2)에서 **정점이 선언한 방향**이 맞나.
//
// `fleet_node.cpp` 는 경로의 마지막 점 yaw 를 이렇게 정한다:
//     각 점 yaw = 다음 점으로 향하는 방향(atan2)
//     마지막 점만, 그 정점이 yaw 를 선언했으면 그것으로 덮는다 (`last.has_yaw`)
// 즉 "도착해서 어느 쪽을 보고 설까" 는 **여기 적힌 값이 전부**다.
//
// ⚠️ navgraph 는 `scripts/gen_arte2_navgraph.py` 가 `waypoint.yaml` 에서 **생성**한다.
//    navgraph 만 고치면 다음 재생성에서 조용히 되돌아간다. 빨개지면 `waypoint.yaml` 을
//    고치고 재생성했는지부터 본다.
//
// 이름으로 찾는 이유: 정점 **인덱스는 삽입순서라 재생성 때 밀린다**. `fms_service.sh` 가
// 런타임에 이름을 해석하는 것과 같은 이유다(그 스크립트의 `resolve_route` 주석).
// `Navgraph` 에는 이름 조회가 없으므로 여기서는 yaml 을 직접 읽는다.
#include <gtest/gtest.h>
#include <cmath>
#include <string>
#include <yaml-cpp/yaml.h>

namespace
{
constexpr double kPi = 3.1415;

const YAML::Node & vertices()
{
  static YAML::Node v =
    YAML::LoadFile(TEST_ARTE2_NAVGRAPH_PATH)["levels"]["L1"]["vertices"];
  return v;
}

// 이름이 `want` 인 정점의 meta. 없으면 빈 노드.
YAML::Node meta_of(const std::string & want)
{
  for (const auto & v : vertices()) {
    if (v.size() < 3 || !v[2].IsMap()) { continue; }
    const YAML::Node & m = v[2];
    if (m["name"] && m["name"].as<std::string>() == want) { return m; }
  }
  return YAML::Node(YAML::NodeType::Undefined);
}
}  // namespace

TEST(WaypointYaw, 수거함은_뒤를_대고_선다)
{
  // ⚠️ 요구 2026-08-07: "사서 UI 에서 수거 작업지시 내리면 수거함으로 이동해?
  //    그리고 yaw 는 3.14 를 봐?" — 확인해 보니 **0.0 이었다.**
  //
  //    수거는 로봇이 수거함에 **뒤를 대고** 바구니를 3단 교대로 바꾼다
  //    (`fleet_orchestrator.decompose_collection`). 충전소와 같은 후면 접근이다.
  //    0.0(= +x, 벽 쪽)이면 로봇이 벽을 보고 서서 팔이 수거함에 안 닿는다.
  const YAML::Node m = meta_of("수거함");
  ASSERT_TRUE(m.IsDefined()) << "수거함 정점이 지도에 없다";
  ASSERT_TRUE(m["yaw"]) << "yaw 선언이 없으면 진행 방향이 그대로 남는다(has_yaw=false)";
  EXPECT_NEAR(m["yaw"].as<double>(), kPi, 1e-3);
}

TEST(WaypointYaw, 충전소도_뒤를_대고_선다)
{
  // 같은 후면 접근이다 — 수거함 값의 근거이자, 이 시험이 지도를 제대로 읽고 있다는 확인.
  const YAML::Node m = meta_of("충전소");
  ASSERT_TRUE(m.IsDefined());
  ASSERT_TRUE(m["yaw"]);
  EXPECT_NEAR(m["yaw"].as<double>(), kPi, 1e-3);
}

TEST(WaypointYaw, 벽쪽_전시물은_벽을_보고_선다)
{
  // 대비군 — 모든 정점을 3.14 로 만든 게 아니라는 확인. 미술작품은 **보러 가는** 곳이라
  // 앞을 향한다(+x, 벽 쪽). 수거함만 뒤를 대는 것이 의도다.
  const YAML::Node m = meta_of("미술작품");
  ASSERT_TRUE(m.IsDefined());
  ASSERT_TRUE(m["yaw"]);
  EXPECT_NEAR(m["yaw"].as<double>(), 0.0, 1e-3);
}
