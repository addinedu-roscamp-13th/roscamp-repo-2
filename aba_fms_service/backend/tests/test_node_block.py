"""차단 정점 대장. 시계를 주입해 만료만 본다."""
from app.node_block import NodeBlockRegistry


def test_empty_registry_has_nothing():
    assert NodeBlockRegistry().active(0.0) == []


def test_blocked_node_is_active_inside_the_ttl():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0)
    assert r.active(59.9) == [9]


def test_blocked_node_expires():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0)
    assert r.active(60.0) == []


def test_zero_ttl_releases_immediately():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0)
    r.set(9, 0.0, now=1.0)
    assert r.active(1.0) == []


def test_resetting_the_ttl_extends_the_block():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0)
    r.set(9, 60.0, now=50.0)
    assert r.active(80.0) == [9]


def test_multiple_nodes_are_sorted():
    r = NodeBlockRegistry()
    r.set(14, 60.0, now=0.0)
    r.set(9, 60.0, now=0.0)
    assert r.active(1.0) == [9, 14]


def test_expired_entries_are_dropped_from_state():
    r = NodeBlockRegistry()
    r.set(9, 10.0, now=0.0, owner="person:pinky3", reason="person")
    assert r.reason_of(9) == "person"       # 만료 전에는 남아 있다
    r.active(20.0)                          # 여기서 만료 걷힘
    assert r.reason_of(9) == ""             # 지워졌다


def test_reason_is_kept_while_active():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0, reason="person")
    assert r.reason_of(9) == "person"


def test_clear_drops_everything():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0)
    r.clear()
    assert r.active(1.0) == []


def test_two_owners_on_the_same_node_both_hold_it():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0, owner="person:pinky3")
    r.set(9, 60.0, now=0.0, owner="dock:pinky1")
    assert r.active(1.0) == [9]
    assert r.owners_of(9) == ["dock:pinky1", "person:pinky3"]


def test_releasing_one_owner_leaves_the_other_block():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0, owner="person:pinky3")
    r.set(9, 60.0, now=0.0, owner="dock:pinky1")
    r.set(9, 0.0, now=1.0, owner="person:pinky3")
    assert r.active(1.0) == [9]
    assert r.owners_of(9) == ["dock:pinky1"]


def test_node_is_free_only_when_every_owner_released():
    r = NodeBlockRegistry()
    r.set(9, 60.0, now=0.0, owner="person:pinky3")
    r.set(9, 60.0, now=0.0, owner="dock:pinky1")
    r.set(9, 0.0, now=1.0, owner="person:pinky3")
    assert r.active(1.0) == [9]
    r.set(9, 0.0, now=1.0, owner="dock:pinky1")
    assert r.active(1.0) == []


def test_expired_since_reports_each_node_once():
    r = NodeBlockRegistry()
    r.set(9, 10.0, now=0.0)
    assert r.expired_since(20.0) == [9]
    assert r.expired_since(21.0) == []
