# 서가 도킹 실시간 시각화

`shelf_dock_lidar_viewer.py`는 로봇이 도킹할 때 **무엇을 판단에 쓰는지**를 한 화면에 보여준다. 대상 로봇을 지정하지 않으면 실행되지 않는다.

- 왼쪽: 카메라 표식 방향을 따라 `/map` PGM에 쏜 광선, 첫 점유 셀(빨강), 그 2cm 앞 정지점(노랑), AMCL 로봇 자세
- 오른쪽: 실제 `/scan` 라이다 점군(초록)과 전방 최소거리
- 아래: 현재 도킹 단계와 PGM 기준 2cm까지 남은 거리

현재 서가 도킹의 거리 제어는 `/scan`이 아니라 **카메라 표식 + `/map` PGM 광선**이다. 라이다 패널은 실시간 실제 주변 관측을 함께 확인하는 진단용이며, 둘을 같은 데이터처럼 보이게 하지 않는다.

```bash
./scripts/demo/shelf_dock_lidar_viewer/run.sh --robot pinky-3
```

`--robot`은 대상 로봇의 ROS 도메인을 고른다. `pinky-3`이면 119가 자동 선택되며, 다르면 `--domain-id <번호>`로 명시한다. 기본 토픽은 `/scan`, `/map`, `/amcl_pose`, `/shelf_dock_status`다. 라이다 설치 방향이 전방과 다르면 예를 들어 `--scan-forward-deg 90`으로 화면 전방만 보정한다. `q` 또는 `Esc`로 종료한다.
