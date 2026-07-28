"""배터리 자동 전이 끄기 — 임계를 **닿지 않는 값으로** 바꾼다.

왜 필요한가: 배터리 값이 못 믿을 상태(센서 이상)면 로봇이 순회 도중 제멋대로
RETURNING 으로 빠져 다른 어떤 기능도 검증할 수 없다. 실측 2026-07-28.

노드를 지우지 않고 임계만 바꾸는 이유는 `_apply_battery_auto` 독스트링 참고 —
BT 구조가 그대로여야 관제 화면이 같은 그림을 그린다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from libi_modes.common.battery_check import BatteryCheck  # noqa: E402


class _FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


class _FakeNode:
    """`_apply_battery_auto` 가 쓰는 것만 흉내낸다 — rclpy 없이 돈다."""

    def __init__(self, battery_auto=True):
        self._battery_auto = battery_auto
        self._logger = _FakeLogger()

    def declare_parameter(self, name, default):
        assert name == "battery_auto"
        return type("P", (), {"value": self._battery_auto})()

    def get_logger(self):
        return self._logger


def _apply(node, params):
    # 언바운드 메서드를 가짜 노드에 붙여 부른다 — rclpy Node 를 실제로 만들지 않고
    # 이 한 메서드만 시험한다(노드 생성은 ROS 그래프·파라미터 서버를 다 끌고 온다).
    from libi_modes.main import FsmNode
    FsmNode._apply_battery_auto(node, params)


def _params():
    return {"battery": {"ready": 40, "charged": 80, "low": 15}}


@pytest.fixture(autouse=True)
def _clear_env():
    old = os.environ.pop("LIBI_BATTERY_AUTO", None)
    yield
    if old is not None:
        os.environ["LIBI_BATTERY_AUTO"] = old


def test_default_leaves_thresholds_alone():
    p = _params()
    _apply(_FakeNode(battery_auto=True), p)
    assert p["battery"] == {"ready": 40, "charged": 80, "low": 15}


def test_param_false_makes_low_unreachable():
    """0% 여도 RETURNING 이 안 떠야 한다 — 배터리는 음수가 될 수 없다."""
    p = _params()
    _apply(_FakeNode(battery_auto=False), p)
    assert BatteryCheck("<=", p["battery"]["low"], "RETURNING").threshold == -1.0
    assert not (0.0 <= p["battery"]["low"])


def test_param_false_blocks_idle_auto_patrol():
    p = _params()
    _apply(_FakeNode(battery_auto=False), p)
    assert not (100.0 >= p["battery"]["charged"])


def test_param_false_still_lets_charging_exit():
    """ready 까지 막으면 도킹하는 순간 CHARGING 에 갇힌다 — 여기는 **항상 참**이어야."""
    p = _params()
    _apply(_FakeNode(battery_auto=False), p)
    assert 0.0 >= p["battery"]["ready"]


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", ""])
def test_env_off_values(val):
    os.environ["LIBI_BATTERY_AUTO"] = val
    p = _params()
    _apply(_FakeNode(battery_auto=True), p)      # param 은 켜져 있어도 env 가 이긴다
    assert p["battery"]["low"] == -1.0


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_env_on_values_keep_defaults(val):
    os.environ["LIBI_BATTERY_AUTO"] = val
    p = _params()
    _apply(_FakeNode(battery_auto=False), p)     # env 가 param 을 되살린다
    assert p["battery"]["low"] == 15


def test_warns_when_disabled():
    """조용히 끄면 나중에 '왜 복귀를 안 하지' 로 돌아온다."""
    node = _FakeNode(battery_auto=False)
    _apply(node, _params())
    assert any("배터리" in w for w in node._logger.warnings)


def test_missing_battery_section_is_created():
    p = {}
    _apply(_FakeNode(battery_auto=False), p)
    assert p["battery"]["low"] == -1.0
