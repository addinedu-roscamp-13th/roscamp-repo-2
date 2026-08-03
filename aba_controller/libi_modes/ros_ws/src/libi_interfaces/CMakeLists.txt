cmake_minimum_required(VERSION 3.8)
project(libi_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(action_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

set(
  MSG_FILES
  "msg/FsmState.msg"
)

set(
  SRV_FILES
  "srv/RequestTransition.srv"
)

# 로봇팔 동작. 토픽이 아니라 액션인 이유는 ArmTask.action 머리말에 있다.
# ⚠️ 액션을 넣으면 `action_msgs` 의존이 필요하다 — goal_id/상태 메시지가 거기 있다.
set(
  ACTION_FILES
  "action/ArmTask.action"
)

rosidl_generate_interfaces(
  ${PROJECT_NAME}
  ${MSG_FILES}
  ${SRV_FILES}
  ${ACTION_FILES}
  DEPENDENCIES builtin_interfaces std_msgs action_msgs
)

ament_package()
