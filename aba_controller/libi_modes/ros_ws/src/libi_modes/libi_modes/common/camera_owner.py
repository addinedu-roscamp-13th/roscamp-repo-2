"""`/libi/camera_select` 를 **누가 쥐고 있나** — `fsm_node` 가 손을 뗄 조건.

## 왜 필요한가 — 발행자가 둘이고 서로 다른 값을 민다

이 토픽의 발행자는 원래 `follow_node`(libi_perception) 하나라는 규칙이었다. 그런데
`fsm_node` 에 예외가 둘 생겼다 — 배달·순회에서 앞캠을 켜 두는
`PersonBlockGuard._request_camera` 와, 야간 순찰의 `CameraSelectRenew` 다. 둘 다
**항상 `"front"`** 를 주기 재발행한다(`main.py._publish_camera_select`).

`CameraSelectRenew` 독스트링은 "추종이 시작되면 `follow_node` 도 발행하는데 **둘 다
front 를 내므로 결과가 같다**" 고 적어 두고 있었다. 그 가정이 두 곳에서 깨진다:

  · **길잡이** — 세션 캠이 `back` 이다(`session.ROLE_CAMERA[GUIDE]`). 길잡이는
    `WORKING` 브랜치라 옆에서 `PersonBlockGuard` 가 같이 tick 되며 `"front"` 를 계속
    재발행한다. 즉 **처음부터** 어긋나 있다.
  · **회복 탐색** — 회복 BT 는 반대 캠을 본다(`recovery_bt.PeekPhase`). 그 구간 동안
    `follow_node` 는 반대 캠을, `fsm_node` 는 `"front"` 를 민다.

두 발행자 다 latched(TRANSIENT_LOCAL)라 **나중에 도착한 쪽이 이긴다.** `follow_node`
는 2Hz, 이쪽은 1~2초 주기 → 토픽이 앞뒤로 계속 뒤집힌다. 실기 증상: "길잡이할 때
그리고 야간순찰에서 앞뒤 카메라가 막 흔들린다"(2026-08-07).

## 규칙

**세션이 캠을 쥔 동안은 `fsm_node` 가 손을 뗀다.** 세션이 없을 때(배달·순회 평상시)는
예전 그대로 `fsm_node` 가 앞캠을 켜 둔다 — 그때는 아무도 안 쥐고 있으므로 경합이 없다.

## 왜 신선도(TTL)가 필요한가

역할 토픽은 latched 다. `follow_node` 가 **죽으면** 마지막 값(`"guide"` 등)이 영원히
남는다. 그것만 보고 손을 떼면 아무도 재발행을 안 해
`camera_select` 가 `CAMERA_SELECT_EXPIRY_SEC`(3초) 뒤 만료되고 **앞캠이 조용히 죽는다** —
그러면 `FRONT_PERSON_SIZE` 가 영영 0 이라 사람 차단 판정도 같이 죽는다.

`follow_node` 는 세션이 있는 동안 `CAMERA_SELECT_HZ`(2Hz)로 역할을 재발행하므로
(`follow_node._publish_camera`), 신선하면 살아 있다는 뜻이다. 끊기면 이쪽이 되찾는다.
"""

#: 이 역할이면 세션이 캠을 쥐고 있다. `none` 과 미수신은 아니다.
#: 문자열은 `libi_perception/session.py` 의 값 그대로다 — 다른 서비스라 import 못 한다.
CAMERA_OWNING_ROLES = ("follow", "guide", "watch", "security")

#: 역할 신선도 상한(초). `follow_node` 가 2Hz 로 재발행하므로 3초면 넉넉히 산다.
#: `config.CAMERA_SELECT_EXPIRY_SEC`(3.0)와 같은 값이다 — 그보다 길면 되찾기 전에
#: 송출기 쪽이 먼저 만료돼 앞캠이 끊기는 구간이 생긴다.
ROLE_TTL_SEC = 3.0


def session_owns_camera(role, age_sec, ttl_sec: float = ROLE_TTL_SEC) -> bool:
    """세션이 `/libi/camera_select` 를 쥐고 있나. 참이면 `fsm_node` 는 발행하지 않는다.

    `age_sec` 은 역할을 마지막으로 받은 뒤 흐른 초. `None` 은 **한 번도 못 받았다** —
    그때는 손을 떼지 않는다(ROS 배선이 없는 배포·시험에서 앞캠이 죽으면 안 된다).
    """
    if role not in CAMERA_OWNING_ROLES:
        return False
    if age_sec is None:
        return False
    return age_sec < ttl_sec
