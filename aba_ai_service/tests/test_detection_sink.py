"""The robot-bound detection channel. Its payload contract has to match exactly what
libi_perception.detection.detection_from_dict() reads, or following silently gets nothing."""
import json
import socket
import threading

from detection_sink import RobotDetectionSink, detection_to_dict


class _FakeRobot:
    """Stands in for libi_perception's TcpDetectionSource."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.lines = []
        self._ready = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self._sock.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self.lines.append(json.loads(line.decode()))
                        self._ready.set()

    def wait(self, timeout=2.0):
        assert self._ready.wait(timeout), "no payload arrived"
        self._ready.clear()


class _Det:
    cx, cy, area = 320.0, 240.0, 10000.0
    bbox = (1.0, 2.0, 3.0, 4.0)
    track_id, is_owner, confidence, is_predicted = 7, True, 0.91, False


def test_detection_to_dict_matches_robot_contract():
    """받는 쪽(libi_perception.detection_from_dict)이 읽는 키와 정확히 같아야 한다.

    키를 더할 때 이 테스트가 빨개지는 건 정상이다 — 계약이 바뀌었다는 뜻이므로
    **양쪽 dataclass 를 같이 고쳤는지** 확인하고 나서 여기를 갱신한다.
    """
    d = detection_to_dict(_Det())
    assert set(d) == {"cx", "cy", "area", "bbox", "track_id",
                      "is_owner", "confidence", "is_predicted",
                      "posture", "motion_ok", "camera", "camera_epoch"}


def test_bbox_is_a_list_so_it_survives_json():
    """A tuple becomes a list on the wire anyway — sending one keeps both sides honest."""
    assert detection_to_dict(_Det())["bbox"] == [1.0, 2.0, 3.0, 4.0]


def test_detection_to_dict_passes_through_none():
    assert detection_to_dict(None) is None


def test_sink_delivers_newline_delimited_json():
    robot = _FakeRobot()
    RobotDetectionSink("127.0.0.1", robot.port).send({"cx": 1.0})
    robot.wait()
    assert robot.lines[-1] == {"cx": 1.0}


def test_sink_sends_null_for_no_owner():
    robot = _FakeRobot()
    RobotDetectionSink("127.0.0.1", robot.port).send(None)
    robot.wait()
    assert robot.lines[-1] is None


def test_sink_sends_successive_frames_on_one_connection():
    robot = _FakeRobot()
    sink = RobotDetectionSink("127.0.0.1", robot.port)
    for i in range(3):
        assert sink.send({"cx": float(i)}) is True
        robot.wait()
    assert [line["cx"] for line in robot.lines] == [0.0, 1.0, 2.0]


def test_sink_does_not_raise_when_robot_absent():
    """A dead robot link must not take the inference loop down with it."""
    sink = RobotDetectionSink("127.0.0.1", 1)     # nothing listening
    assert sink.send({"cx": 1.0}) is False        # reports failure, does not raise


def test_sink_recovers_after_the_robot_comes_back():
    robot = _FakeRobot()
    sink = RobotDetectionSink("127.0.0.1", robot.port)
    sink.send({"cx": 1.0})
    robot.wait()
    sink._close_locked()                          # simulate a dropped link
    assert sink.send({"cx": 2.0}) is True         # reconnects on the next send


# ── 신규 필드 (자세·주행가부·카메라·epoch) ────────────────────────────────────

class _RichDet(_Det):
    posture, motion_ok, camera, camera_epoch = "Standing", True, "back", 7


def test_new_fields_are_serialised():
    d = detection_to_dict(_RichDet())
    assert d["posture"] == "Standing"
    assert d["motion_ok"] is True
    assert d["camera"] == "back"
    assert d["camera_epoch"] == 7


def test_old_producer_without_new_fields_still_serialises():
    """이 함수는 테스트 대역도 받는다 — 없는 속성으로 죽으면 안 된다."""
    d = detection_to_dict(_Det())
    assert d["posture"] is None
    assert d["motion_ok"] is True
    assert d["camera"] is None
    assert d["camera_epoch"] == 0


def test_motion_ok_false_survives_serialisation():
    class _Stopped(_Det):
        motion_ok = False
    assert detection_to_dict(_Stopped())["motion_ok"] is False


def test_new_fields_survive_json_roundtrip():
    """생산자 → JSON → 소비자 계약이 실제로 이어지는지. 한쪽만 고치면 여기서 깨진다."""
    payload = json.loads(json.dumps(detection_to_dict(_RichDet())))
    assert payload["camera"] == "back" and payload["camera_epoch"] == 7
