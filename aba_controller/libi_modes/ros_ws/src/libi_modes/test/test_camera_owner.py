"""`/libi/camera_select` 를 누가 쥐고 있나 — `fsm_node` 가 손을 뗄 조건.

실기 결함(2026-08-07): 길잡이와 야간순찰에서 **앞뒤 카메라가 계속 뒤집혔다.**
이 토픽의 발행자가 둘이고 서로 다른 값을 밀기 때문이다.

    follow_node  : 세션 캠 (길잡이=back, 회복 peek 구간=반대 캠)   2Hz + 변화 즉시
    fsm_node     : 항상 "front"                                    1~2초 주기

둘 다 latched(TRANSIENT_LOCAL)라 나중에 도착한 쪽이 이긴다.
`CameraSelectRenew` 독스트링은 "둘 다 front 를 내므로 결과가 같다" 고 적어 뒀는데,
길잡이는 세션 캠이 `back` 이라 **처음부터** 그 가정이 깨져 있었다.

규칙: **세션이 캠을 쥔 동안은 `fsm_node` 가 손을 뗀다.**
되돌리면(= 늘 발행) `길잡이_세션이_있으면...` 이 빨개진다.
"""
import pytest

from libi_modes.common.camera_owner import ROLE_TTL_SEC, session_owns_camera


@pytest.mark.parametrize("role", ["follow", "guide", "watch", "security"])
def test_세션이_있으면_fsm_node_가_손을_뗀다(role):
    assert session_owns_camera(role, age_sec=0.1) is True


def test_길잡이_세션이_있으면_앞캠을_덮어쓰지_않는다():
    """증상 그 자체 — 길잡이 세션 캠은 `back` 인데 fsm_node 가 `front` 를 밀었다."""
    assert session_owns_camera("guide", age_sec=0.0) is True


def test_세션이_없으면_예전대로_발행한다():
    """배달·순회 평상시. 아무도 안 쥐고 있으므로 경합이 없다 —
    여기서 손을 떼면 앞캠이 아예 안 켜지고 `FRONT_PERSON_SIZE` 가 영영 0 이 된다."""
    assert session_owns_camera("none", age_sec=0.0) is False
    assert session_owns_camera(None, age_sec=0.0) is False
    assert session_owns_camera("", age_sec=0.0) is False


def test_한_번도_못_받았으면_손을_떼지_않는다():
    """ROS 배선이 없는 배포·시험. 모름을 '쥐고 있다' 로 읽으면 앞캠이 죽는다."""
    assert session_owns_camera("guide", age_sec=None) is False


def test_역할이_낡으면_되찾는다():
    """⚠️ 토픽이 latched 라 `follow_node` 가 **죽으면** 마지막 값이 영원히 남는다.
    그것만 보고 손을 떼면 아무도 재발행을 안 해 `camera_select` 가 3초 뒤 만료되고
    **앞캠이 조용히 죽는다.** 신선할 때만 양보한다."""
    assert session_owns_camera("guide", age_sec=ROLE_TTL_SEC - 0.01) is True
    assert session_owns_camera("guide", age_sec=ROLE_TTL_SEC) is False
    assert session_owns_camera("guide", age_sec=99.0) is False


def test_TTL_은_송출기_만료보다_짧아야_한다():
    """`config.CAMERA_SELECT_EXPIRY_SEC` 가 3.0 이다. 이보다 길면 되찾기 전에
    송출기가 먼저 만료돼 앞캠이 끊기는 구간이 생긴다."""
    assert ROLE_TTL_SEC <= 3.0
