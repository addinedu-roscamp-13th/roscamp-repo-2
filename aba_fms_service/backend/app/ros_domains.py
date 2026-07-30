"""서버(관제)가 쓰는 ROS 2 도메인 — **단일 출처**.

## 왜 이 파일이 생겼나 (2026-07-30)

로봇 pinky-3 의 `/cmd_vel` 발행자가 **2개**였다. twist_mux 하나여야 하는데
(`pinky_bringup/config/twist_mux.yaml` 이 불변식으로 못박아 뒀다) 두 번째가
`fastapi_ros_bridge` 였고, 그건 **이 서버의 `ros_bridge.py`** 였다.

경로는 이렇다:

    루트 .env:67  ROS_DOMAIN_ID=119      ← "실물 로봇용" 값 하나
      → scripts/_load_env.sh 가 실어 준다
      → backend/start.sh 가 그걸 부른다
      → uvicorn 프로세스 전체가 119
      → ros_bridge.py 의 `rclpy.init()` 이 **도메인 인자 없이** 불려서 119 를 그대로 탐
      → 서버 노드가 로봇 도메인에 올라가 /cmd_vel 을 직접 발행

형제 셋(`fleet_link`·`fsm_link` ·`fleet_telemetry`)은 각자 **자기 Context 에 도메인을
명시**해서 무사했다. `ros_bridge` 만 안 하고 있었다. 그런데 그 셋도 기본값 `86` 을
**따로따로** 적어 둬서, 한 곳만 고치면 조용히 어긋나는 구조였다.

그래서 값을 여기 하나로 모은다. **서버 도메인을 바꾸려면 이 파일(또는 아래 환경변수)
하나만 고친다.**

## 규칙

- 서버는 **로봇 도메인에 절대 올라가지 않는다.** 로봇 도메인끼리의 왕래는 domain_bridge
  가 한다(`config/domain_bridge.template.yaml` 의 `to_domain` 이 이 값과 같아야 한다).
- 그래서 `rclpy.init()` 을 **도메인 없이 부르지 않는다.** 셸의 `ROS_DOMAIN_ID` 가
  무엇이든 서버 노드는 이 값에 뜬다 — 그게 위 사고를 구조적으로 불가능하게 만든다.

## 값

[2026-07-30] **86 → 111.** 86 은 로봇 도메인대(117~119)와 멀지 않고, 무엇보다 이번처럼
셸 값이 새어 들어오면 구별이 안 됐다. 111 로 확정한다.

노브는 `LIBI_SERVER_DOMAIN_ID` 다. 노드별로 더 좁게 덮고 싶으면 각 모듈의
`LIBI_FLEET_DOMAIN_ID` / `LIBI_FSM_DOMAIN_ID` / `LIBI_TELEMETRY_DOMAIN_ID` 를 쓴다
(그쪽이 우선한다).

⚠️ 이 값을 바꾸면 **같이 바꿔야 하는 곳**이 있다. 안 바꾸면 브릿지가 서버를 못 찾고,
   증상은 "관제 화면에 로봇이 안 뜬다"로만 나타난다(에러 없음):
     · `aba_fms_service/config/domain_bridge.template.yaml` 의 `to_domain`
     · `aba_fms_service/config/generated/*.yaml` (템플릿으로 다시 생성하거나 직접)
"""
import os

#: 서버(관제) 도메인. 로봇 도메인(117~119)과 절대 겹치면 안 된다.
SERVER_DOMAIN_ID = int(os.environ.get("LIBI_SERVER_DOMAIN_ID", "111"))


def domain_for(env_key: str) -> int:
    """노드별 override 를 허용하되, 없으면 서버 도메인으로 떨어진다.

    예: `domain_for("LIBI_FLEET_DOMAIN_ID")`
    """
    raw = os.environ.get(env_key)
    return int(raw) if raw else SERVER_DOMAIN_ID
