import time

import py_trees

from .recovery_bt import SearchContext, create_searching_tree, tick_tree
from .switch import FollowSwitch
from .tracking_controller import TrackingController


class ControlLoop:
    """Runs exactly one of the two follow behaviours per tick, chosen by FollowSwitch:

      TRACKING  -> TrackingController (PID + LiDAR, numeric control)
      SEARCHING -> recovery BT (order expressed as tree structure)
      ENDED     -> nothing; the session is over

    No ROS here — the node injects get_detection / get_scan / publish, which is what lets
    the whole follow behaviour be tested without a robot.
    """

    def __init__(self, get_detection, get_scan, publish, cfg, now=time.monotonic):
        self.get_detection = get_detection
        self.get_scan = get_scan
        self.publish = publish
        self.cfg = cfg
        self.now = now
        self.switch = FollowSwitch()
        self.tracker = TrackingController(publish, cfg)
        self.miss = 0
        self._search_ctx = None
        self._search_tree = None

    @property
    def state(self):
        return self.switch.state

    def _start_search(self):
        lkd = self.tracker.last_direction or 1.0
        self._search_ctx = SearchContext(self.get_detection, self.publish,
                                         self.cfg, self.now, lkd=lkd)
        # Stamp the search start when SEARCHING begins, not on the tree's first tick —
        # those can be ticks apart, which would understate elapsed search time.
        self._search_ctx.start = self.now()
        self._search_tree = create_searching_tree(self._search_ctx)

    def tick(self):
        if self.switch.state == 'TRACKING':
            det = self.get_detection()
            if det is not None:
                self.miss = 0
                self.tracker.step(det, self.get_scan(), self.cfg.FRAME_DT)
            else:
                self.miss += 1
                self.publish(0.0, 0.0)
                if self.miss >= self.cfg.N_MISS_FRAMES:
                    self.switch.lost()
                    self._start_search()
        elif self.switch.state == 'SEARCHING':
            status = tick_tree(self._search_tree)
            if status == py_trees.common.Status.SUCCESS:
                self.switch.reacquired()
                self.miss = 0
                self.tracker.reset()
            elif status == py_trees.common.Status.FAILURE:
                self.publish(0.0, 0.0)
                self.switch.search_failed()
        # ENDED: idle — the follow session is over.
