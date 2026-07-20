"""py_trees keeps ONE process-global blackboard, so without this fixture values written
by an earlier test leak into the next one and tests pass or fail depending on order."""
import py_trees
import pytest
from py_trees.common import Access

from libi_modes.blackboard import Keys

_ALL_KEYS = [v for k, v in vars(Keys).items() if not k.startswith("_") and isinstance(v, str)]


@pytest.fixture(autouse=True)
def clean_blackboard():
    py_trees.blackboard.Blackboard.clear()
    yield
    py_trees.blackboard.Blackboard.clear()


@pytest.fixture
def seed():
    """Writes blackboard values for a test. Defaults mirror a booted robot with nothing
    pending, so each test only states the fields it actually cares about."""
    client = py_trees.blackboard.Client(name="test-seed")
    for key in _ALL_KEYS:
        client.register_key(key=key, access=Access.WRITE)

    def _seed(**overrides):
        defaults = {
            Keys.NEXT_MODE: None,
            Keys.FAULT: False,
            Keys.LAST_COMMAND: None,
            Keys.ACTIVE_COMMAND: None,
            Keys.COMMAND_RECEIVED_AT: 0.0,
            Keys.UI_LAST_TOUCH_AT: 0.0,
            Keys.DOCK_RETRY_COUNT: 0,
            Keys.IS_DOCKED: False,
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            client.set(key, value)
        return client

    return _seed


@pytest.fixture
def read():
    """Reads a blackboard key after a tick."""
    client = py_trees.blackboard.Client(name="test-read")
    for key in _ALL_KEYS:
        client.register_key(key=key, access=Access.READ)

    def _read(key, default=None):
        try:
            return client.get(key)
        except KeyError:
            return default

    return _read


@pytest.fixture
def tick():
    """Sets up and ticks a tree once, returning the root's status."""

    def _tick(root, times=1):
        tree = py_trees.trees.BehaviourTree(root=root)
        tree.setup(timeout=15)
        for _ in range(times):
            tree.tick()
        return root.status

    return _tick
