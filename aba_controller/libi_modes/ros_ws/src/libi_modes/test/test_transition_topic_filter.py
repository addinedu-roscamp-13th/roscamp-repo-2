"""StateIO._on_request_topic 의 robot_id 필터 — rclpy Node 없이 검증한다.

공유 토픽(/libi/fsm_transition_request)이 모든 로봇 도메인에 뿌려지므로, 이 필터가
robot_id 를 **정확히** 대조하지 않으면 robot_id 없는(빈/None) 요청 한 건이 전 로봇을
동시에 전이시킨다. 그 회귀를 막는다. Node 초기화는 피하려고 __new__ 로 만들고
필터가 참조하는 속성만 심는다.
"""
import json

from libi_modes.ros.state_io import StateIO


def _io(robot_id: str):
    io = StateIO.__new__(StateIO)
    io._robot_id = robot_id
    io._decided = []
    # _on_request_topic 이 참조하는 협력자만 스텁한다.
    io._decide = lambda target, force: (io._decided.append((target, force)) or (True, "IDLE", ""))
    io._result_pub = type("P", (), {"publish": lambda self, msg: None})()
    return io


def _send(io, payload: dict):
    io._on_request_topic(type("M", (), {"data": json.dumps(payload)})())


def test_empty_robot_id_is_rejected():
    """robot_id 가 "" 이면 무시 — 예전 버그(빈 값 통과 → 전 로봇 전이)의 회귀 방지."""
    io = _io("Pinky-sim-1")
    _send(io, {"id": "x", "robot_id": "", "target_state": "PATROL"})
    assert io._decided == []


def test_missing_robot_id_is_rejected():
    io = _io("Pinky-sim-1")
    _send(io, {"id": "x", "target_state": "PATROL"})  # robot_id 키 없음 → None
    assert io._decided == []


def test_other_robot_id_is_rejected():
    io = _io("Pinky-sim-1")
    _send(io, {"id": "x", "robot_id": "Pinky-sim-2", "target_state": "PATROL"})
    assert io._decided == []


def test_matching_robot_id_is_accepted():
    io = _io("Pinky-sim-1")
    _send(io, {"id": "x", "robot_id": "Pinky-sim-1", "target_state": "PATROL"})
    assert io._decided == [("PATROL", False)]
