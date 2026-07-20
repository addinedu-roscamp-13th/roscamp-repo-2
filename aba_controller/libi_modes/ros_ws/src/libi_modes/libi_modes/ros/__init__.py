"""ROS2 접합부.

`libi_modes` 의 나머지(브랜치·leaf·트리)는 rclpy 를 전혀 모른다 — 그래서 로봇 없이
pytest 로 전부 검증할 수 있다. 이 패키지만 rclpy 에 의존하며, 트리가 필요로 하는
두 가지 구멍을 실제 ROS 로 메운다:

    providers  구독한 토픽 값을 Topics2BB 가 읽을 콜러블로
    drivers    액션 leaf 의 start/poll/stop 을 /fleet_cmd 왕복으로
"""
