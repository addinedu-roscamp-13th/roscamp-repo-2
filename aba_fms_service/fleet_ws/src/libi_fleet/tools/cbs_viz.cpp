// cbs_viz — navgraph + 로봇 start/goal 로 CBS 시간표를 만들어 JSON 으로 뱉는다.
// 애니메이션 뷰어(scripts/cbs_viewer/)가 이 JSON 을 읽어 재생한다.
//
// ROS 없이 돈다 — 계획이 맞는지 보는 데 로봇도 관제도 필요 없다. 그게 요점이다.
//
//   ros2 run libi_fleet cbs_viz --navgraph <yaml> --robot 3:17 --robot 5:2 > plan.json
//   ros2 run libi_fleet cbs_viz --navgraph <yaml> --robot 3:17 --clearance 2 --speed 0.15
//
// 옵션
//   --robot <start>:<goal>   반복 지정. 정점 인덱스.
//   --clearance <틱>         계획 여유(기본 1)
//   --speed <m/s>            로봇 순항 속도(기본 0.15)
//   --tick <초>              틱 하나의 실제 길이(기본 1.0)
//   --level <이름>           navgraph level(기본 L1)

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "libi_fleet/cbs_planner.hpp"
#include "libi_fleet/cbs_traffic.hpp"
#include "libi_fleet/navgraph.hpp"

namespace
{

struct Job { int start; int goal; };

void usage()
{
  std::fprintf(stderr,
    "사용법: cbs_viz --navgraph <yaml> --robot <start>:<goal> [--robot ...]\n"
    "        [--clearance <틱>] [--speed <m/s>] [--tick <초>] [--level <이름>]\n");
}

}  // namespace

int main(int argc, char ** argv)
{
  std::string navgraph, level = "L1";
  std::vector<Job> jobs;
  // 0 = "안 줬음". 안 준 값은 **플러그인 기본값**(실물 nav2 파라미터에서 온 값)을 그대로 쓴다.
  // 여기에 따로 기본값을 두면 뷰어와 실제가 조용히 갈라진다 — 실제로 그랬다(0.15 vs 0.07).
  int clearance = -1;
  double speed = 0.0, tick = 0.0;

  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto next = [&](const char * what) -> std::string {
      if (i + 1 >= argc) { std::fprintf(stderr, "%s 뒤에 값이 필요합니다\n", what); std::exit(2); }
      return argv[++i];
    };
    if (a == "--navgraph") { navgraph = next("--navgraph"); }
    else if (a == "--level") { level = next("--level"); }
    else if (a == "--clearance") { clearance = std::atoi(next("--clearance").c_str()); }
    else if (a == "--speed") { speed = std::atof(next("--speed").c_str()); }
    else if (a == "--tick") { tick = std::atof(next("--tick").c_str()); }
    else if (a == "--robot") {
      const std::string v = next("--robot");
      const size_t c = v.find(':');
      if (c == std::string::npos) { std::fprintf(stderr, "--robot 은 start:goal 형식\n"); return 2; }
      jobs.push_back({std::atoi(v.substr(0, c).c_str()), std::atoi(v.substr(c + 1).c_str())});
    } else if (a == "-h" || a == "--help") { usage(); return 0; }
    else { std::fprintf(stderr, "알 수 없는 인자: %s\n", a.c_str()); usage(); return 2; }
  }

  if (navgraph.empty() || jobs.empty()) { usage(); return 2; }

  libi_fleet::Navgraph g;
  if (!g.load(navgraph, level)) {
    std::fprintf(stderr, "navgraph 로드 실패: %s (level=%s)\n", navgraph.c_str(), level.c_str());
    return 1;
  }

  // navgraph → 가중 그래프.
  //
  // ⚠️ 여기서 규칙을 **다시 구현하지 않는다.** 뷰어가 보여주는 시간과 로봇이 실제로 받는
  //    시간이 갈라지면 뷰어는 거짓말을 한다. 플러그인이 쓰는 바로 그 코드를 부른다
  //    (CbsTraffic::build_graph — 회전 시간까지 navgraph 기하로 계산한다).
  if (speed > 0.0) { setenv("LIBI_CBS_SPEED_MPS", std::to_string(speed).c_str(), 1); }
  if (tick > 0.0) { setenv("LIBI_CBS_TICK_SEC", std::to_string(tick).c_str(), 1); }
  if (clearance >= 0) { setenv("LIBI_CBS_CLEARANCE", std::to_string(clearance).c_str(), 1); }
  libi_fleet::CbsTraffic model;
  const libi_fleet::TimedGraph tg = model.graph_for(g, {});
  // 실제로 적용된 값을 다시 읽어 출력에 싣는다(뷰어가 그대로 보여준다).
  speed = model.speed_mps();
  tick = model.tick_seconds();
  clearance = model.clearance();

  std::vector<int> starts, goals;
  for (const auto & j : jobs) {
    if (j.start < 0 || j.start >= g.size() || j.goal < 0 || j.goal >= g.size()) {
      std::fprintf(stderr, "정점 범위 초과: %d:%d (정점 %d개)\n", j.start, j.goal, g.size());
      return 2;
    }
    starts.push_back(j.start);
    goals.push_back(j.goal);
  }

  libi_fleet::PlanOptions opt;
  opt.clearance = clearance;
  const std::vector<libi_fleet::Route> routes = libi_fleet::cbs_plan_timed(tg, starts, goals, opt);

  // ── JSON 출력 ──────────────────────────────────────────────────────────
  std::printf("{\n");
  std::printf("  \"ok\": %s,\n", routes.empty() ? "false" : "true");
  std::printf("  \"tick_seconds\": %.3f,\n", tick);
  std::printf("  \"speed_mps\": %.3f,\n", speed);
  std::printf("  \"clearance\": %d,\n", clearance);

  std::printf("  \"vertices\": [");
  for (int v = 0; v < g.size(); ++v) {
    std::printf("%s{\"i\":%d,\"x\":%.4f,\"y\":%.4f}", v ? "," : "", v, g.vertex(v).x, g.vertex(v).y);
  }
  std::printf("],\n");

  std::printf("  \"lanes\": [");
  bool first = true;
  for (int v = 0; v < g.size(); ++v) {
    for (const auto & [w, cost] : tg[v]) {
      std::printf("%s{\"u\":%d,\"v\":%d,\"ticks\":%d}", first ? "" : ",", v, w, cost);
      first = false;
    }
  }
  std::printf("],\n");

  std::printf("  \"robots\": [");
  for (size_t i = 0; i < jobs.size(); ++i) {
    std::printf("%s\n    {\"name\":\"R%zu\",\"start\":%d,\"goal\":%d,\"route\":[",
                i ? "," : "", i + 1, jobs[i].start, jobs[i].goal);
    if (i < routes.size()) {
      for (size_t k = 0; k < routes[i].size(); ++k) {
        const auto & s = routes[i][k];
        const bool endless = s.depart >= libi_fleet::kNeverEnds;
        std::printf("%s{\"v\":%d,\"arrive\":%d,\"depart\":%s}", k ? "," : "", s.v, s.arrive,
                    endless ? "null" : std::to_string(s.depart).c_str());
      }
    }
    std::printf("]}");
  }
  std::printf("\n  ]\n}\n");

  if (routes.empty()) {
    std::fprintf(stderr, "⚠️ 해를 찾지 못했습니다 — clearance 를 줄이거나 목표를 바꿔 보세요.\n");
    return 1;
  }
  // 사람 눈으로도 확인할 수 있게 stderr 에 요약.
  for (size_t i = 0; i < routes.size(); ++i) {
    std::fprintf(stderr, "R%zu: %s\n", i + 1, libi_fleet::route_to_string(routes[i]).c_str());
  }
  return 0;
}
