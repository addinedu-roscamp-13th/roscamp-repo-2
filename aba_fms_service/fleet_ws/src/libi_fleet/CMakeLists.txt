cmake_minimum_required(VERSION 3.8)
project(libi_fleet)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(pluginlib REQUIRED)
find_package(libi_fleet_msgs REQUIRED)
find_package(rmf_fleet_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(std_srvs REQUIRED)
find_package(yaml-cpp REQUIRED)

# --- core 라이브러리 (navgraph) ---
add_library(libi_fleet_core src/navgraph.cpp src/patrol_cycle.cpp src/security_patrol_cycle.cpp)
set_property(TARGET libi_fleet_core PROPERTY POSITION_INDEPENDENT_CODE ON)  # shared 플러그인에 링크되므로 PIC
target_include_directories(libi_fleet_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(libi_fleet_core yaml-cpp)
ament_target_dependencies(libi_fleet_core rclcpp)

# --- pluginlib 플러그인 (dispatcher/traffic) ---
# [2026-07-26] reservation_deadlock.cpp 복원.
#   README 는 이걸 "기본, 실사용" 교통 플러그인이라고 적어 왔지만 포팅 중 소스가 누락됐고,
#   plugins.xml 에도 등록이 없어 **교통관제가 사실상 꺼진 채로 돌고 있었다**
#   (fleet_node 기본값이 GrantAllTraffic = 항상 GRANT, 자기 주석에 "실사용 금지"라고 적힌 것).
#   상류(arte_libi_fleet)에서 그대로 가져왔다 — traffic_base.hpp 가 양쪽 동일해 수정 없이 붙는다.
add_library(libi_fleet_plugins SHARED
  plugins/auction.cpp
  plugins/grant_all_traffic.cpp
  plugins/reservation_deadlock.cpp)
target_include_directories(libi_fleet_plugins PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(libi_fleet_plugins libi_fleet_core)
ament_target_dependencies(libi_fleet_plugins pluginlib rclcpp)
pluginlib_export_plugin_description_file(libi_fleet plugins.xml)

# --- fleet 노드 ---
add_executable(fleet_node src/fleet_node.cpp)
target_include_directories(fleet_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(fleet_node libi_fleet_core)
ament_target_dependencies(fleet_node
  rclcpp pluginlib libi_fleet_msgs rmf_fleet_msgs geometry_msgs std_srvs)

install(TARGETS libi_fleet_core libi_fleet_plugins
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin)
install(TARGETS fleet_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_patrol_cycle test/test_patrol_cycle.cpp)
  target_include_directories(test_patrol_cycle PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include)
  target_link_libraries(test_patrol_cycle libi_fleet_core)
  target_compile_definitions(test_patrol_cycle PRIVATE
    TEST_NAVGRAPH_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../../maps/library/new_map.navgraph.yaml")

  ament_add_gtest(test_security_patrol_cycle test/test_security_patrol_cycle.cpp)
  target_include_directories(test_security_patrol_cycle PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include)
  target_link_libraries(test_security_patrol_cycle libi_fleet_core)
  target_compile_definitions(test_security_patrol_cycle PRIVATE
    TEST_NAVGRAPH_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../../maps/library/new_map.navgraph.yaml")

  # [2026-07-26] cbs_planner.cpp 를 빌드 대상에 넣는다.
  #   그 전까지 이 파일은 **어떤 타깃에도 속하지 않아** colcon build 로 컴파일조차 되지 않았다.
  #   문법 오류가 있어도 아무도 몰랐다는 뜻이다. self-check main() 은 CBS_NO_MAIN 으로 끄고
  #   gtest 로 감싼다 — 알고리즘이 조용히 틀리는 것을 잡을 유일한 방법이다.
  #   (아직 TrafficBase/PlannerBase 플러그인은 아니다. 배선 계획: docs 07-26-plan/06-…)
  ament_add_gtest(test_cbs_planner test/test_cbs_planner.cpp ../planning/cbs_planner.cpp)
  target_include_directories(test_cbs_planner PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include)
  target_compile_definitions(test_cbs_planner PRIVATE CBS_NO_MAIN)
endif()

ament_package()
