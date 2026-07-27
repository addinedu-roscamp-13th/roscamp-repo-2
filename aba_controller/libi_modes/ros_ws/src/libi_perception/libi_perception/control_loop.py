import time

import py_trees

from .recovery_bt import SearchContext, create_searching_tree, tick_tree
from .switch import FollowSwitch
from .tracking_controller import TrackingController

#: 이보다 큰 tick 간격은 공칭값으로 대체한다 (공칭 0.05s 의 10배).
_MAX_DT_SEC = 0.5


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
        self._last_tick = None

    @property
    def state(self):
        return self.switch.state

    @property
    def search_tree(self):
        """지금 도는 회복 BT. SEARCHING 이 아니면 None.

        관제 화면이 이 트리를 미션 BT 의 `FollowExec` 밑에 붙여 그린다
        (follow_node.snapshot_dict → /libi/follow_bt_snapshot).
        """
        return self._search_tree if self.switch.state == 'SEARCHING' else None

    def _start_search(self):
        lkd = self.tracker.last_direction or 1.0
        self._search_ctx = SearchContext(self.get_detection, self.publish,
                                         self.cfg, self.now, lkd=lkd)
        # Stamp the search start when SEARCHING begins, not on the tree's first tick —
        # those can be ticks apart, which would understate elapsed search time.
        self._search_ctx.start = self.now()
        self._search_tree = create_searching_tree(self._search_ctx)

    def _dt(self):
        """PID 에 넘길 실제 경과시간(초).

        예전엔 공칭값 `cfg.FRAME_DT`(0.05) 를 그냥 썼다. tick 이 밀리거나 몰려 들어오면
        적분·미분 항이 실제 시간과 어긋나 게인이 조용히 달라진다 — 튜닝이 재현되지 않는
        원인이다. 주입된 `now` 가 이미 있었는데 쓰지 않고 있었다.

        시계가 안 움직이거나(테스트 고정 시계) 크게 튀면(일시정지 후 재개) 공칭값으로
        돌아간다. 그 경우 적분항이 폭주하는 편보다 게인이 조금 어긋나는 편이 낫다.
        """
        now = self.now()
        dt = self.cfg.FRAME_DT if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        return dt if 0.0 < dt <= _MAX_DT_SEC else self.cfg.FRAME_DT

    def tick(self):
        if self.switch.state == 'TRACKING':
            det = self.get_detection()
            if det is not None:
                self.miss = 0
                if not getattr(det, 'motion_ok', True):
                    # 보이지만 가면 안 된다 — 누워 있거나, 로봇 코앞이거나, 자세를
                    # 재는 중이다. **miss 를 올리지 않는다**: 올리면 눈앞에 멀쩡히
                    # 보이는 대상을 두고 탐색 회전을 시작한다. 놓친 게 아니라
                    # 가지 않기로 한 것이다.
                    self.publish(0.0, 0.0)
                    # PID 도 리셋한다. 정지 구간 동안 적분항이 쌓이면 재개하는 순간
                    # 튀어 나간다.
                    self.tracker.reset()
                    self._last_tick = None
                else:
                    self.tracker.step(det, self.get_scan(), self._dt())
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
                # 회복 구간(수십 초) 동안 쌓인 간격을 첫 추종 tick 의 dt 로 쓰면 안 된다.
                # 리셋하면 다음 _dt() 가 공칭값으로 시작한다 — PID 도 방금 reset 됐으니 짝이 맞다.
                self._last_tick = None
            elif status == py_trees.common.Status.FAILURE:
                self.publish(0.0, 0.0)
                self.switch.search_failed()
        # ENDED: idle — the follow session is over.
