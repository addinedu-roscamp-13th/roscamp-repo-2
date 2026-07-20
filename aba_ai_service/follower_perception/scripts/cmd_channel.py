"""Tiny UDP command channel: AI server -> robot (linear_x, angular_z).

Deliberately a SEPARATE layer so the three concerns stay decoupled and swappable:
  cmd SOURCE (cmd_preview / later follower_control)  ->  TRANSPORT (this)  ->
  /cmd_vel PUBLISHER (cmd_bridge, ROS).
No ROS here. UDP + latest-wins (the receiver exposes the age for a watchdog)."""
import socket
import struct
import threading
import time

_FMT = struct.Struct("!dd")     # linear_x, angular_z


class CmdSender:
    """AI-server side. Call send(linear_x, angular_z) per frame."""

    def __init__(self, host, port):
        self._addr = (host, int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, linear_x, angular_z):
        self._sock.sendto(_FMT.pack(float(linear_x), float(angular_z)), self._addr)

    def close(self):
        self._sock.close()


class CmdReceiver:
    """Robot side. Background thread keeps only the latest command + arrival time
    so the consumer can enforce a stop-if-stale watchdog."""

    def __init__(self, port, host="0.0.0.0"):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, int(port)))
        self.port = self._sock.getsockname()[1]
        self._latest = None                 # (lin, ang, monotonic_recv_time)
        self._lock = threading.Lock()
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while self._run:
            try:
                data, _ = self._sock.recvfrom(64)
            except OSError:
                break
            if len(data) >= _FMT.size:
                lin, ang = _FMT.unpack(data[:_FMT.size])
                with self._lock:
                    self._latest = (lin, ang, time.monotonic())

    def latest(self):
        """(linear_x, angular_z, age_seconds) or None if nothing received yet."""
        with self._lock:
            v = self._latest
        if v is None:
            return None
        return v[0], v[1], time.monotonic() - v[2]

    def close(self):
        self._run = False
        try:
            self._sock.close()
        except OSError:
            pass
