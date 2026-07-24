"""Drive state machine + open-loop recovery search (follower_BT).

Independent package — no ROS / py_trees / lidar / PID — so it can be swapped for
follower_control's real py_trees BT later (same behaviour). The follow controller
is INJECTED, so this stays decoupled from perception's cmd computation.

States:
    IDLE  --register (owner visible)-->  FOLLOWING
    FOLLOWING  --owner lost-->  PEEK
    PEEK  --owner reacquired-->  FOLLOWING
    PEEK  --~90 deg turned, still lost-->  SEARCHING
    SEARCHING  --owner reacquired-->  FOLLOWING
    SEARCHING  --search gives up-->  IDLE

Recovery timeline (coasting handled upstream by the alpha-beta filter):
    peek   : turn ~90 deg toward the owner's LAST-KNOWN direction (LKD), to look
             around the corner it went behind
    scan   : oscillate left/right ~45 deg
    turn   : ~180 deg
    scan   : oscillate again
    turn   : ~180 deg (back to start)
    after  : give up -> IDLE
"""
import math

# Reference tuning (canonical values -> follower_control/config.py).
SCAN_ANGLE = math.radians(45)      # +/- sweep amplitude
TURN_ANGLE = math.radians(180)
ANGULAR_SEARCH = 0.25              # rad/s (slower search rotation; was 0.5)

_Q = SCAN_ANGLE / ANGULAR_SEARCH             # time to sweep 45 deg
SCAN_SEC = 4 * _Q                             # one full 0->+45->-45->0 oscillation
TURN_SEC = TURN_ANGLE / ANGULAR_SEARCH

IDLE, FOLLOWING, PEEK, SEARCHING = "IDLE", "FOLLOWING", "PEEK", "SEARCHING"

PEEK_ANGLE = math.radians(90)      # LKD peek: turn ~90 deg toward the last direction
PEEK_SEC = PEEK_ANGLE / ANGULAR_SEARCH


def _scan_angular(t):
    """Oscillate 0 -> +45 -> -45 -> 0 across one scan window (symmetric, no LKD)."""
    if t < _Q:
        return ANGULAR_SEARCH           # 0 -> +45 (left)
    if t < 3 * _Q:
        return -ANGULAR_SEARCH          # +45 -> -45 (right)
    return ANGULAR_SEARCH               # -45 -> 0 (left)


def search_command(elapsed):
    """(angular_z, done, phase) for the recovery timeline."""
    t1 = SCAN_SEC
    t2 = t1 + TURN_SEC
    t3 = t2 + SCAN_SEC
    t4 = t3 + TURN_SEC
    if elapsed < t1:
        return _scan_angular(elapsed), False, "SCAN1"
    if elapsed < t2:
        return ANGULAR_SEARCH, False, "TURN180"
    if elapsed < t3:
        return _scan_angular(elapsed - t2), False, "SCAN2"
    if elapsed < t4:
        return ANGULAR_SEARCH, False, "TURN180B"
    return 0.0, True, "GIVEUP"


class RecoveryBehavior:
    def __init__(self):
        self._elapsed = 0.0
        self.done = False

    def reset(self):
        self._elapsed = 0.0
        self.done = False

    def update(self, dt):
        self._elapsed += dt
        ang, done, phase = search_command(self._elapsed)
        self.done = done
        return {"linear_x": 0.0, "angular_z": ang, "drive": "SEARCH", "turn": phase}


def _stop(turn):
    return {"linear_x": 0.0, "angular_z": 0.0, "drive": "STOP", "turn": turn}


class PeekBehavior:
    """LKD recovery step 1: turn ~90 deg toward the owner's last-known direction
    to look around a corner before the full symmetric search kicks in."""

    def __init__(self):
        self._elapsed = 0.0
        self._dir = 1.0
        self.done = False

    def reset(self):
        self._elapsed = 0.0
        self.done = False

    def start(self, direction):
        self._elapsed = 0.0
        self._dir = 1.0 if direction >= 0 else -1.0
        self.done = False

    def update(self, dt):
        self._elapsed += dt
        self.done = self._elapsed >= PEEK_SEC
        ang = 0.0 if self.done else self._dir * ANGULAR_SEARCH
        return {"linear_x": 0.0, "angular_z": ang, "drive": "PEEK",
                "turn": "PEEK_L" if self._dir > 0 else "PEEK_R"}


class DrivePolicy:
    """IDLE / FOLLOWING / PEEK / SEARCHING state machine.

    follow_fn (det, frame_w) -> cmd dict is injected, so the follow controller
    (cmd_preview now, follower_control later) stays swappable. On losing the owner
    (after the alpha-beta filter's coast expires upstream) it first PEEKs ~90 deg
    toward the last-known direction, then falls back to the symmetric search."""

    def __init__(self, follow_fn):
        self._follow = follow_fn
        self._search = RecoveryBehavior()
        self._peek = PeekBehavior()
        self._last_cx = 0.0
        self._frame_w = 0.0
        self.state = IDLE

    def _peek_dir(self):
        """+1 = turn left (owner was left of centre), -1 = turn right."""
        center = self._frame_w / 2.0 if self._frame_w else 0.0
        return 1.0 if self._last_cx <= center else -1.0

    def step(self, det, frame_w, dt, registered=True):
        if not registered:                                  # nothing registered
            self.state = IDLE
            self._search.reset()
            self._peek.reset()
            return self._tag(_stop("-"))
        if det is not None and getattr(det, "is_owner", False):
            self.state = FOLLOWING
            self._search.reset()
            self._peek.reset()
            self._last_cx, self._frame_w = det.cx, frame_w   # remember direction
            return self._tag(self._follow(det, frame_w))

        # registered but owner not visible -> PEEK (last dir) then SEARCH
        if self.state == FOLLOWING:                          # just lost -> begin peek
            self._peek.start(self._peek_dir())
            self.state = PEEK
        if self.state == PEEK:
            cmd = self._peek.update(dt)
            if not self._peek.done:
                return self._tag(cmd)                        # still peeking
            self.state = SEARCHING                           # peek over, still lost
            self._search.reset()
        if self.state != SEARCHING:                          # entered recovery while IDLE
            self.state = SEARCHING
            self._search.reset()
        cmd = self._search.update(dt)
        if self._search.done:                               # gave up
            self.state = IDLE
            self._peek.reset()
            cmd = _stop("GIVEUP")
        else:
            self.state = SEARCHING
        return self._tag(cmd)

    def _tag(self, cmd):
        cmd["state"] = self.state
        return cmd
