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

find_package(Qt5 5.15 COMPONENTS Core Gui Qml Quick QuickControls2 REQUIRED)

add_executable(libi_gui
    src/main.cpp
    src/RobotController.cpp
    src/RobotController.h
    resources.qrc
)

target_link_libraries(libi_gui PRIVATE
    Qt5::Core
    Qt5::Gui
    Qt5::Qml
    Qt5::Quick
    Qt5::QuickControls2
)
