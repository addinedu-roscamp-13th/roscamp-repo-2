import py_trees
import pytest
from py_trees.common import Access

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


def test_keys_are_unique_strings():
    values = [v for k, v in vars(Keys).items() if not k.startswith("_") and isinstance(v, str)]
    assert len(values) == len(set(values)), "duplicate blackboard key values"


def test_expected_keys_present():
    expected = {
        "CURRENT_MODE", "NEXT_MODE", "FAULT", "BATTERY_PERCENT", "IS_DOCKED",
        "LAST_COMMAND", "UI_LAST_TOUCH_AT",
        "ACTIVE_COMMAND", "COMMAND_RECEIVED_AT", "DOCK_RETRY_COUNT", "ERROR_CODE",
    }
    assert expected.issubset(set(vars(Keys).keys()))


def test_safe_get_returns_default_for_unset_key():
    """Raw py_trees get() raises KeyError on a never-written key; branches tick from boot
    before Topics2BB has populated anything, so the safe wrapper must absorb that."""
    client = py_trees.blackboard.Client(name="probe")
    client.register_key(key=Keys.BATTERY_PERCENT, access=Access.READ)

    with pytest.raises(KeyError):
        client.get(Keys.BATTERY_PERCENT)

    assert bb.get(client, Keys.BATTERY_PERCENT) is None
    assert bb.get(client, Keys.BATTERY_PERCENT, default=99) == 99


def test_safe_get_returns_value_when_set():
    client = py_trees.blackboard.Client(name="probe2")
    client.register_key(key=Keys.BATTERY_PERCENT, access=Access.WRITE)
    client.set(Keys.BATTERY_PERCENT, 55.0)
    assert bb.get(client, Keys.BATTERY_PERCENT) == 55.0
