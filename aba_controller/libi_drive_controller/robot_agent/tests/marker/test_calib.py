"""캘리브레이션 슬롯 — 잘못 짝지어도 조용히 도는 게 가장 위험하다.

주점(cx)이 회전 여부에 따라 80px 넘게 다르다. 짝이 틀리면 거리는 그럴듯하게 나오고
좌우만 흐른다. 값이 없어서 죽는 게 아니라 값이 있는데 틀린 경우라 눈치채기 어렵다.
"""
import pytest

from app.marker.calib import CALIB_FILES, load_calib

# 슬롯 → 그 파일이 캘리브된 회전각
SLOT_ROTATION = {"front": 180, "front0": 0, "back": 0}


def test_every_slot_has_a_file_that_loads():
    assert set(SLOT_ROTATION) == set(CALIB_FILES), "슬롯 목록과 회전 표가 어긋났다"
    for slot in CALIB_FILES:
        K, dist, (w, h) = load_calib(slot)
        assert K.shape == (3, 3) and K[0, 0] > 100, f"{slot} 초점거리가 이상하다"
        assert dist.size >= 4
        # 주점은 그 해상도 안에 있어야 한다. 밖이면 K 와 해상도의 짝이 틀린 것이고,
        # 그 상태로 열면 죽지 않고 거리만 배수로 틀린다(320 캘리브를 640 으로 열 때).
        assert 0 < K[0, 2] < w and 0 < K[1, 2] < h, f"{slot} 주점이 {w}x{h} 밖이다"


@pytest.mark.parametrize("slot,rotate", SLOT_ROTATION.items())
def test_matching_rotation_is_accepted(slot, rotate):
    K, _, _ = load_calib(slot, rotate)
    assert K[0, 2] > 0


@pytest.mark.parametrize("slot,rotate", [("front", 0), ("front0", 180), ("back", 180)])
def test_mismatched_rotation_is_rejected(slot, rotate):
    """--rotate 를 바꿨는데 슬롯을 안 바꾼 경우. 조용히 돌면 안 된다."""
    with pytest.raises(SystemExit) as exc:
        load_calib(slot, rotate)
    assert "rotate" in str(exc.value)


def test_rotated_and_unrotated_principal_points_actually_differ():
    """짝을 강제하는 이유가 실제로 존재하는지 — 두 파일의 cx 차이를 확인한다.

    이 차이가 작다면 위 가드는 과보호다. 실측 82px 이고 640px 폭의 13% 다.
    """
    K180, _, _ = load_calib("front", 180)
    K0, _, _ = load_calib("front0", 0)
    assert abs(K180[0, 2] - K0[0, 2]) > 50, "회전 유무로 cx 가 안 변한다 — 가드 재검토"
    assert abs(K180[0, 0] - K0[0, 0]) < 1e-6, "같은 카메라라 초점거리는 같아야 한다"


def test_unknown_slot_names_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        load_calib("옆캠")
    assert "front" in str(exc.value)
