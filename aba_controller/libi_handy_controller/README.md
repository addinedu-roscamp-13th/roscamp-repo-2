# libi_handy_controller

LIBI 로봇팔(Handy) 보드 컨트롤러. 한 로봇의 **팔 보드**에서 돌며, Drive Controller 의
pick/place 요청을 받아 팔을 움직이고 결과를 돌려준다. (한 로봇, 두 보드, 같은 ROS_DOMAIN_ID)

## 신호 흐름
```
구독  handy_cmd     {"id","action","object","location"}   요청 (Drive → Handy)
발행  handy_result  {"id","success","error"}              완료 (Handy → Drive)
```
- `action` ∈ {pick, place} · `object` ∈ {book, basket}
- `location` ∈ {libi_basket, collection_bin, bookshelf, table, info_desk}
- 완료(팔 동작 끝난 뒤)에 같은 `id`로 결과 발행. 실패는 예외 대신 `success=false`.

## 구조
```
handy_core.py   순수 로직 — 요청 검증 + 팔 모션 콜러블 호출 (ROS·팔 없이 테스트됨)
handy_node.py   rclpy 껍데기 — handy_cmd 구독 / handy_result 발행
```
- **팔 모션은 스텁**(`HandyCore._stub_motion`, 성공만 반환). 팔 담당자가 `motion` 콜러블을
  채운다 (pymycobot, BT 로 짜도 됨). 인터페이스는 그대로.
- 상세: 옵시디언 `2026-07-21 Handy(로봇팔) 인터페이스 요청서.md`

## 빌드·실행
```bash
cd aba_controller/libi_handy_controller/ros_ws
colcon build && source install/setup.bash
ros2 run libi_handy_controller handy_node
```

## 테스트
```bash
cd src/libi_handy_controller && PYTHONPATH=".:$PYTHONPATH" python3 -m pytest test/ -q
```

## 아직 (팔 담당자/통합)
- 실제 팔 모션 구현(스텁 → pymycobot/BT).
- Drive 측 릴레이: FMS 의 perform_action(fleet_cmd) → handy_cmd 변환 + handy_result → fleet_cmd_result.
  이때 waypoint↔semantic location(bookshelf/table…) 매핑 필요(도서 데이터 계층과 연동).
