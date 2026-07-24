cmake_minimum_required(VERSION 3.16)
project(libi_gui LANGUAGES CXX)

# Libi 로봇 온보드 터치패널 GUI (Qt5.15 / C++ / QML)
# - 통신: 실제 배포 시 Libi Drive Controller 와 ROS2(DDS) 로 연결 (현재는 RobotController 목 백엔드)
# - 주의: 이 환경엔 QtQuick.Shapes 모듈이 없어 QML 에서 사용하지 않음 (곡선은 Canvas 사용)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_INCLUDE_CURRENT_DIR ON)

# Network — 관리자 추종의 FMS 승인 요청(HTTP)에 사용
find_package(Qt5 5.15 COMPONENTS Core Gui Qml Quick QuickControls2 Network REQUIRED)

# ROS2 (rclcpp) — 로봇 FSM 연동. 빌드 전 source /opt/ros/jazzy/setup.bash (gui.sh 처리).
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

add_executable(libi_gui
    src/main.cpp
    src/RobotController.cpp
    src/RobotController.h
    src/PerceptionClient.cpp
    src/PerceptionClient.h
    src/RosLink.cpp
    src/RosLink.h
    resources.qrc
)

target_link_libraries(libi_gui PRIVATE
    Qt5::Core
    Qt5::Gui
    Qt5::Qml
    Qt5::Quick
    Qt5::QuickControls2
    Qt5::Network
)

# 우선: rclcpp/std_msgs 가 제공하는 CMake 타깃 링크
target_link_libraries(libi_gui PRIVATE rclcpp::rclcpp std_msgs::std_msgs)

# 기본 OFF — 로봇에 올리는 빌드는 이 옵션 없이 돌리므로 산출물이 달라지지 않는다.
# 켜면 FMS 추종 승인 클라이언트 테스트가 같이 빌드된다 (tests/ 주석의 실행법 참조).
option(LIBI_GUI_TESTS "Build libi_gui test executables" OFF)
if(LIBI_GUI_TESTS)
    add_executable(test_admin_follow_client
        tests/test_admin_follow_client.cpp
        src/RobotController.cpp
        src/RobotController.h
    )
    target_link_libraries(test_admin_follow_client PRIVATE Qt5::Core Qt5::Network)

    # Gui — QImage 디코딩에 필요 (QCoreApplication 으로 돌지만 이미지 타입은 QtGui 소속)
    add_executable(test_perception_client
        tests/test_perception_client.cpp
        src/PerceptionClient.cpp
        src/PerceptionClient.h
    )
    target_link_libraries(test_perception_client PRIVATE Qt5::Core Qt5::Gui Qt5::Network Qt5::Quick)
endif()
