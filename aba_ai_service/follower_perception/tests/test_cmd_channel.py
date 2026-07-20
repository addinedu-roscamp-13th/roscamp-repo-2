import time
from scripts.cmd_channel import CmdSender, CmdReceiver


def test_round_trip_and_age():
    r = CmdReceiver(0)                      # port 0 -> OS-assigned (r.port)
    s = CmdSender("127.0.0.1", r.port)
    s.send(0.15, -0.4)
    v = None
    for _ in range(100):
        v = r.latest()
        if v is not None:
            break
        time.sleep(0.005)
    r.close(); s.close()
    assert v is not None
    lin, ang, age = v
    assert abs(lin - 0.15) < 1e-6
    assert abs(ang - (-0.4)) < 1e-6
    assert 0.0 <= age < 1.0


def test_latest_none_before_any_send():
    r = CmdReceiver(0)
    assert r.latest() is None
    r.close()
