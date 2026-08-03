"""ShelfDockExec / BackupExec — 위임 leaf 계약만 본다."""
import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys
from libi_modes.common.working_actions import BackupExec, ShelfDockExec


class FakeDriver:
    def __init__(self, result="running"):
        self.result = result
        self.starts = 0
        self.stops = 0

    def start(self, args=None):
        self.starts += 1

    def poll(self):
        return self.result

    def stop(self):
        self.stops += 1


def _leaf(cls, driver):
    leaf = cls(driver)
    leaf.setup()
    bb = py_trees.blackboard.Client(name="t")
    bb.register_key(key=Keys.ACTIVE_COMMAND, access=Access.WRITE)
    return leaf, bb


def test_shelf_dock_ignores_other_commands():
    leaf, bb = _leaf(ShelfDockExec, FakeDriver())
    bb.set(Keys.ACTIVE_COMMAND, "navigate")
    assert leaf.update() == Status.FAILURE


def test_shelf_dock_runs_its_own_command():
    driver = FakeDriver()
    leaf, bb = _leaf(ShelfDockExec, driver)
    bb.set(Keys.ACTIVE_COMMAND, "shelf_dock")
    assert leaf.update() == Status.RUNNING
    assert driver.starts == 1


def test_shelf_dock_success_clears_the_slot():
    leaf, bb = _leaf(ShelfDockExec, FakeDriver(result="success"))
    bb.set(Keys.ACTIVE_COMMAND, "shelf_dock")
    assert leaf.update() == Status.SUCCESS
    assert bb.get(Keys.ACTIVE_COMMAND) is None


def test_shelf_dock_failure_clears_the_slot():
    leaf, bb = _leaf(ShelfDockExec, FakeDriver(result="failure"))
    bb.set(Keys.ACTIVE_COMMAND, "shelf_dock")
    assert leaf.update() == Status.FAILURE
    assert bb.get(Keys.ACTIVE_COMMAND) is None


def test_shelf_dock_stops_when_the_slot_is_taken():
    driver = FakeDriver()
    leaf, bb = _leaf(ShelfDockExec, driver)
    bb.set(Keys.ACTIVE_COMMAND, "shelf_dock")
    leaf.update()
    bb.set(Keys.ACTIVE_COMMAND, "navigate")
    leaf.update()
    assert driver.stops == 1


def test_backup_handles_only_backup():
    leaf, bb = _leaf(BackupExec, FakeDriver())
    bb.set(Keys.ACTIVE_COMMAND, "shelf_dock")
    assert leaf.update() == Status.FAILURE
    bb.set(Keys.ACTIVE_COMMAND, "backup")
    assert leaf.update() == Status.RUNNING


def test_the_two_leaves_do_not_overlap():
    assert ShelfDockExec(FakeDriver()).handles & BackupExec(FakeDriver()).handles == set()
