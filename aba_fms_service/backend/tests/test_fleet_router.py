"""fleet 라우터의 계약 테스트 — 상수 목록과 요청 검증.

엔드포인트는 fleet_link 를 감싸기만 하므로 동작은 test_fleet_link.py 가 잡는다.
여기서는 '패널이 렌더링에 쓰는 값이 진짜 정의처에서 오는지'와 입력 검증을 본다.
"""
import pytest

fleet_router = pytest.importorskip(
    "app.routers.fleet",
    reason="라우터는 FastAPI/SQLAlchemy 가 있는 환경에서만 import 된다 (backend/.venv)",
)

from app.fsm_model import STATES  # noqa: E402


def test_plugin_lists_match_plugins_xml():
    """SetPlugins.srv 주석에 적힌 예시 이름(GreedyCost/EdgeNodeLock 등)은 구버전이라
    믿으면 안 된다. plugins.xml 에 실제로 등록된 것만 노출한다."""
    import pathlib
    # tests/ -> backend/ -> aba_fms_service/
    plugins_xml = (
        pathlib.Path(__file__).resolve().parents[2]
        / "fleet_ws/src/libi_fleet/plugins.xml"
    )
    text = plugins_xml.read_text()
    for name in fleet_router.DISPATCHER_PLUGINS + fleet_router.TRAFFIC_PLUGINS:
        assert f'type="{name}"' in text, f"{name} 이 plugins.xml 에 없습니다"


def test_states_are_not_redefined_in_the_router():
    """상태 어휘의 소유자는 libi_modes 다. 라우터가 자기 목록을 들고 있으면 안 된다."""
    import inspect
    source = inspect.getsource(fleet_router)
    for state in ("SECURITY_PATROL", "INTERACTING", "RETURNING", "CHARGING"):
        assert f'"{state}"' not in source, f"{state} 이 라우터에 하드코딩됐습니다"


def test_meta_would_serve_the_libi_modes_state_set():
    assert set(STATES) == {
        "ERROR", "RETURNING", "CHARGING", "WORKING",
        "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE",
    }


def test_submit_task_requires_a_dropoff():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        fleet_router.SubmitTaskRequest(dropoff="")


def test_submit_task_defaults_to_auction():
    """robot 을 비우면 dispatcher 가 고른다 — 경매를 테스트하는 기본 경로."""
    body = fleet_router.SubmitTaskRequest(dropoff="7")
    assert body.robot == ""
    assert body.arm_actions == 0


def test_battery_is_bounded_to_percent():
    from pydantic import ValidationError
    fleet_router.SetBatteryRequest(robot="pinky1", value=0.0)
    fleet_router.SetBatteryRequest(robot="pinky1", value=100.0)
    for bad in (-1.0, 101.0):
        with pytest.raises(ValidationError):
            fleet_router.SetBatteryRequest(robot="pinky1", value=bad)


def test_arm_actions_is_bounded():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        fleet_router.SubmitTaskRequest(dropoff="3", arm_actions=-1)
