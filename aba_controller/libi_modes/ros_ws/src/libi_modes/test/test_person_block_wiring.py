"""배선 확인 — PersonBlockGuard/ShelfDockExec/BackupExec 에 실제 드라이버가 꽂혔는지.

`registry.build_branches` 가 `drivers` dict 에 들어온 값을 실제로 밀어 넣는지만 본다
(rclpy 로 무엇을 만드는지는 main.py 의 몫이라 여기 대상이 아니다). person_stop_driver·
block_fn·shelf_dock_driver·backup_driver 를 하나라도 안 꽂으면(기본값 None) leaf 는
로그만 남기고 아무 일도 안 한다 — codex 지적 P0. 되돌림 확인: 아래 registry.py 의
전달을 지우면(`person_stop_driver=drivers.get("person_stop")` 등을 빼면) 이 시험이
빨개진다.
"""
from libi_modes import registry
from libi_modes.common.person_block import PersonBlockGuard
from libi_modes.common.working_actions import BackupExec, ShelfDockExec, UnwiredDriver

from .fakes import PARAMS as BASE_PARAMS
from .fakes import FakeDriver, all_drivers


def _walk(node):
    yield node
    for child in getattr(node, "children", []):
        yield from _walk(child)


def _params():
    """person_stop_size > 0 이어야 PersonBlockGuard 가 트리에 붙는다(working.create 참고)."""
    params = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BASE_PARAMS.items()}
    params["working"] = dict(params["working"])
    params["working"]["person_stop_size"] = 209.0
    params["working"]["person_block_ttl_sec"] = 60.0
    return params


def _wired_drivers():
    drivers = all_drivers()
    drivers["person_stop"] = FakeDriver()
    drivers["person_block"] = lambda node: None
    drivers["shelf_dock"] = FakeDriver()
    drivers["backup"] = FakeDriver()
    # 앞캠을 켜는 통로(`/libi/camera_select`). 없으면 감시 leaf 가 볼 프레임 자체가 없다.
    drivers["camera_select"] = lambda which: None
    return drivers


def _working_root(drivers):
    return registry.build_branches(_params(), drivers)["WORKING"]


def test_person_block_guard_has_a_stop_driver_and_a_block_fn():
    root = _working_root(_wired_drivers())
    guards = [n for n in _walk(root) if isinstance(n, PersonBlockGuard)]
    assert len(guards) == 1, "person_stop_size > 0 인데 PersonBlockGuard 가 안 붙었다"
    assert guards[0].stop_driver is not None
    assert guards[0].block_fn is not None


def test_shelf_dock_and_backup_leaves_have_real_drivers():
    root = _working_root(_wired_drivers())
    shelf = [n for n in _walk(root) if isinstance(n, ShelfDockExec)]
    backup = [n for n in _walk(root) if isinstance(n, BackupExec)]
    assert len(shelf) == 1 and len(backup) == 1
    assert not isinstance(shelf[0].driver, UnwiredDriver)
    assert not isinstance(backup[0].driver, UnwiredDriver)


def test_unwired_drivers_are_caught_by_the_same_test():
    """되돌림 확인: 드라이버를 안 꽂으면(기본 all_drivers()) 위 시험들이 잡아야 한다."""
    root = _working_root(all_drivers())      # person_stop/person_block/shelf_dock/backup 없음
    guards = [n for n in _walk(root) if isinstance(n, PersonBlockGuard)]
    assert guards[0].stop_driver is None
    assert guards[0].block_fn is None
    shelf = [n for n in _walk(root) if isinstance(n, ShelfDockExec)]
    backup = [n for n in _walk(root) if isinstance(n, BackupExec)]
    assert isinstance(shelf[0].driver, UnwiredDriver)
    assert isinstance(backup[0].driver, UnwiredDriver)


# ── 순회 브랜치에도 붙는다 (2026-08-03) ────────────────────────────────────────
# 순회는 WorkingBranch 가 아니라 PatrolBranch/SecurityPatrolBranch 라서 예전엔 감시
# leaf 가 **tick 조차 안 됐다** — 카메라도 안 켜지고 사람도 못 봤다.
# 되돌림 확인: registry.py 에서 `camera_driver=`/`person_stop_driver=` 전달을 빼거나
# 브랜치의 guard 를 지우면 아래가 빨개진다.
#
# ⚠️ SECURITY_PATROL(야간)은 여기서 **뺐다** — night-patrol 병합 이후로는
# `PersonBlockGuard` 대신 `IntruderChase`(추종)가 그 자리를 대신하기 때문에
# (`branches/security_patrol.py` 의 `PatrolOrChase` Selector), 아래 네 가지 검증
# 전부(가드 존재·command gate·block 보고·arrive timer pause) SECURITY_PATROL 에는
# 더 이상 적용되지 않는다 — 지우지 않고, 같은 의도를 잇는 시험으로 옮겼다:
# `test_security_patrol_wiring.py::test_사람에_의한_정지는_야간에_없다`(가드 부재
# 확인) 와 `test_registry_security_patrol_drivers.py`(camera_driver·follow_driver
# 실제 전달 확인)가 SECURITY_PATROL 쪽 검증을 대신한다.

def _patrol_guards(mode, drivers=None):
    root = registry.build_branches(_params(), drivers or _wired_drivers())[mode]
    return [n for n in _walk(root) if isinstance(n, PersonBlockGuard)]


def test_patrol_branch_has_the_guard():
    guards = _patrol_guards("PATROL")
    assert len(guards) == 1, "순회에 PersonBlockGuard 가 안 붙었다"
    assert guards[0].stop_driver is not None, "정지 수단이 없으면 멈추는 척만 한다"
    assert guards[0].camera_driver is not None, "카메라를 안 켜면 볼 프레임이 없다"


def test_patrol_guard_has_no_command_gate():
    """순회 중 `ACTIVE_COMMAND` 는 None 이다 — navigate 게이트를 두면 영영 안 돈다."""
    assert _patrol_guards("PATROL")[0].require_command is None


def test_patrol_guard_reports_blocks_too():
    """순회 홉도 `on_path_request` 가 내려보내는 같은 `navigate{node,...}` 라
    `committed_node` 가 홉마다 갱신된다 — 막히면 알려야 CBS 가 경로를 다시 짠다.
    안 꽂으면 순회 중엔 아무리 오래 막혀도 재탐색이 안 일어난다."""
    assert _patrol_guards("PATROL")[0].block_fn is not None


def test_patrol_guard_pauses_the_arrive_timer():
    """정지해 있는 시간이 도착 타임아웃을 먹으면 순회가 '주행 실패'로 끊긴다."""
    assert _patrol_guards("PATROL")[0].nav_leaf is not None
