"""속도 명령 워치독 — 명령이 끊기면 모터를 세운다.

## 왜 별도 모듈인가

`bringup.py` 는 `dynamixel_sdk`(로봇 전용)를 import 한다. 판정을 거기 두면 하드웨어가
없는 곳에서는 시험조차 못 한다. 순수 판정만 떼어 두면 어디서든 돌릴 수 있다.

## 왜 필요한가

`twist_callback` 은 메시지가 올 때만 RPM 을 쓴다. 워치독이 없으면 **상위 노드가 죽는
순간 로봇이 마지막 속도로 계속 굴러간다.** 30Hz 타이머는 odom 만 발행했다.

2026-07-28 에 `/cmd_vel` 발행자를 twist_mux 하나로 모으면서(중재자 도입) 이 위험이
커졌다. 예전엔 발행자가 10개라 하나 죽어도 다른 게 계속 밀었지만, 이제 twist_mux 가
**단일 실패점**이다. 이 워치독이 그 짝이다.
"""

#: 이 시간 동안 새 속도 명령이 없으면 모터를 세운다 (초).
#
# twist_mux 의 입력 timeout 과 **같은 값**이어야 한다
# (pinky_bringup/config/twist_mux.yaml). 다르면 두 층이 서로 다른 순간에 "발행자가
# 죽었다"고 판단해, 그 사이 구간의 동작이 설명되지 않는다.
CMD_VEL_TIMEOUT_SEC = 0.5


def command_expired(now_sec, last_cmd_sec, timeout=CMD_VEL_TIMEOUT_SEC):
    """마지막 명령이 너무 오래됐나. 한 번도 못 받았으면(None) 만료가 아니다.

    한 번도 못 받은 상태는 "정지 명령을 보내야 하는 상황"이 아니다 — 로봇은 애초에
    안 움직이고 있고, 부팅 직후 매 tick 정지 명령을 쏘면 시리얼만 낭비한다.

    경계는 **배타적**이다. 정확히 timeout 만큼 지난 것은 아직 살아 있다 — 20Hz 제어에서
    한 프레임 늦은 것을 죽은 것으로 치면 정상 주행이 매번 끊긴다.
    """
    if last_cmd_sec is None:
        return False
    return (now_sec - last_cmd_sec) > timeout
