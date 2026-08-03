"""회귀 시험 — registry.py 가 SECURITY_PATROL 잎에 실제 드라이버를 물리는가.

Task 9 가 `security_patrol.py` 에 `CameraSelectRenew(camera_driver=...)` 를 넣었지만
`registry.py` 의 `security_patrol.create(...)` 호출에는 `camera_driver` 인자 자체가
없었다 — `drivers` 에 `"camera_select"` 항목을 채워 넘겨도 registry.py 가 그 값을
전달하지 않으면 잎은 항상 `camera_driver=None` 을 받는다. 그러면 `/libi/camera_select`
가 재발행되지 않아 카메라가 꺼지고 야간 침입 추종이 통째로 조용히 죽는다
(checklist 13, C2). 이 시험은 registry.py 의 배선 자체를 확인한다 — `main.py` 가
실제 `"camera_select"` 드라이버를 채워 넣는지는 별도 관심사라 여기서는 fakes 에
직접 채워 넣는다.
"""
import py_trees

from libi_modes import registry
from libi_modes.common.intruder_chase import CameraSelectRenew, IntruderChase
from test.fakes import PARAMS, all_drivers


def _security_patrol_root():
    drivers = all_drivers()
    # main.py 가 실제로 채우는 값 — registry.py 는 이 키를 읽어 넘겨야 한다.
    drivers["camera_select"] = lambda value: None
    branches = registry.build_branches(PARAMS, drivers)
    return branches["SECURITY_PATROL"]


def test_야간_추종_잎들이_실제로_드라이버를_받는다():
    root = _security_patrol_root()

    camera_leaf = next(n for n in root.iterate() if isinstance(n, CameraSelectRenew))
    assert camera_leaf.camera_driver is not None, (
        "registry.py 가 security_patrol.create() 에 camera_driver 를 안 넘긴다 — "
        "/libi/camera_select 가 재발행되지 않아 카메라가 꺼진다")

    chase_leaf = next(n for n in root.iterate() if isinstance(n, IntruderChase))
    assert chase_leaf.follow_driver is not None, (
        "follow_driver 배선이 깨졌다 (Task 9 회귀)")


def test_camera_select_가_없는_구성에서도_안_죽는다():
    """드라이버 사전에 아예 camera_select 가 없는(구형) 조립도 여전히 유효한 트리를 낸다."""
    drivers = all_drivers()
    branches = registry.build_branches(PARAMS, drivers)
    root = branches["SECURITY_PATROL"]
    camera_leaf = next(n for n in root.iterate() if isinstance(n, CameraSelectRenew))
    assert camera_leaf.camera_driver is None
    assert isinstance(root, py_trees.behaviour.Behaviour)
