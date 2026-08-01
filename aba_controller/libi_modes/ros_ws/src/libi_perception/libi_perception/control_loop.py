import time

import py_trees

from . import session as sess
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

    def __init__(self, get_detection, get_scan, publish, cfg, now=time.monotonic,
                 select_camera=None, peek_people=None, role="follow"):
        self.get_detection = get_detection
        self.get_scan = get_scan
        self.publish = publish
        self.cfg = cfg
        self.now = now
        #: 회복 BT 가 반대 캠을 보려고 요청할 통로. 주입 안 하면 예전처럼 회전만으로 찾는다.
        self.select_camera = select_camera
        self.peek_people = peek_people
        self.role = role
        self.switch = FollowSwitch()
        self.tracker = TrackingController(publish, cfg)
        self.miss = 0
        #: 이 세션에서 **대상을 한 번이라도 잡았나.**
        #: 잃어버리려면 먼저 가지고 있어야 한다 — 등록 전에는 검출이 계속 None 이라
        #: 이게 없으면 세션을 연 지 2초(N_MISS_FRAMES 40 @20Hz) 만에 회복 BT 가 돌아
        #: 아무도 등록하지 않았는데 로봇이 혼자 돌며 앞뒤 캠을 번갈아 켠다(실측 2026-07-28).
        self._acquired = False
        self._search_ctx = None
        #: 이번 소실 에피소드에서 재시작한 횟수. **재시작은 1회만 허용한다.**
        #:
        #: `guide_watch` 는 진짜 안내(GuideExec, 45초 종결자 있음)뿐 아니라 복귀·도킹이
        #: 뒷캠을 고정하려고 빌려 쓰는 세션(`BackCamOn`, 종결자 **없음** — Parallel 이
        #: 끝날 때까지 절대 안 끝난다)에도 걸린다. 무제한 재시작을 두면 그 세션에서
        #: 캠이 도킹 내내 앞뒤로 튀어 시각 서보가 죽는다(2026-08-01 최종 리뷰 발견).
        #: 1회로 캡하면 진짜 안내는 45초 종결자가 여전히 실질 권한을 쥐고(첫 라운드
        #: ~32.8초 + 재시작 1회로 45초를 넘기기 충분하다), 빌려 쓴 세션은 최대
        #: 2라운드(~65.6초) 뒤 ENDED 로 정착한다.
        self._search_restarts = 0
        self._search_tree = None
        self._last_tick = None

    @property
    def state(self):
        return self.switch.state

    def _is_guide(self) -> bool:
        """안내 역할인가.

        ⚠️ `watch` 는 **뺀다.** 안내에는 `GuideExec` 의 45초 종료 판정자가 있지만
        등록감시에는 없다. 또 등록 중에는 사람이 패널 화면을 보고 있으므로 카메라
        전환을 앞당길 이유가 없다.
        """
        return self.role == sess.GUIDE

    def _filtered_detection(self):
        """검출의 **유일한** 출입구. TRACKING 분기와 회복 트리(`SearchContext`)가
        **반드시 이 함수를 통해서만** 검출을 봐야 한다 — 원본 `self.get_detection`
        을 어느 한쪽이라도 직접 부르면 그쪽은 예측 bbox 를 걸러내지 못한다.

        안내에서는 예측 bbox 를 '보인다' 로 치지 않는다. 가시성 발행
        (`follow_node.requester_visible`)이 이미 거부하므로, 여기서 안 거르면
        한 프로세스 안에 "놓쳤다" 가 두 개가 된다 — 정지(0.5초)와 탐색 진입(4초)이
        어긋나는 것보다 나쁜 건, 회복 중에도 예측 bbox 가 재획득으로 읽혀
        SEARCHING 이 즉시 TRACKING 으로 되돌아가는 것이다.

        추종은 코스팅이 제어의 연속성을 만들어 주므로 건드리지 않는다.
        """
        det = self.get_detection()
        if det is not None and self._is_guide() and getattr(det, 'is_predicted', False):
            return None
        return det

    @property
    def search_tree(self):
        """지금 도는 회복 BT. SEARCHING 이 아니면 None.

        관제 화면이 이 트리를 미션 BT 의 `FollowExec` 밑에 붙여 그린다
        (follow_node.snapshot_dict → /libi/follow_bt_snapshot).
        """
        return self._search_tree if self.switch.state == 'SEARCHING' else None

    def _start_search(self):
        lkd = self.tracker.last_direction or 1.0
        # 재시작이면(이전 컨텍스트가 있으면) 그게 실제로 남겨 둔 캠을 물려준다.
        # 안 넘기면 새 컨텍스트가 home_camera 라고 낙관적으로 가정하는데, 소진된
        # 회복은 peek 캠에서 끝나는 경우가 있어 그 가정이 틀릴 수 있다.
        prev_camera = (self._search_ctx.camera_now()
                       if self._search_ctx is not None else None)
        self._search_ctx = SearchContext(self._filtered_detection, self.publish,
                                         self.cfg, self.now, lkd=lkd,
                                         select_camera=self.select_camera,
                                         peek_people=self.peek_people,
                                         role=self.role,
                                         initial_camera=prev_camera)
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
            det = self._filtered_detection()
            if det is not None:
                self.miss = 0
                self._acquired = True
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
                # **아직 한 번도 못 잡았으면 탐색하지 않는다.** 등록 전에는 검출이 계속
                # None 이라, 이 검사가 없으면 세션을 연 직후 회복 BT 가 돌아 로봇이 혼자
                # 돌기 시작한다 — 사람은 아직 화면에서 자기를 등록하는 중이다.
                # 여기서는 계속 정지 명령만 내며 등록을 기다린다.
                if self.miss >= self.cfg.N_MISS_FRAMES and self._acquired:
                    self.switch.lost()
                    self._search_restarts = 0
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
                if self._is_guide() and self._search_restarts < 1:
                    # 안내는 여기서 끝내지 않는다. `ENDED` 에는 빠져나오는 길이
                    # 없어서(switch._TRANSITIONS 의 restart 를 안내 경로에서 아무도
                    # 안 부른다) 사람이 돌아와도 재획득 판정이 죽는다. 회복
                    # 타임라인(≈32.8초)이 guide_lost_timeout_sec(45초)보다 짧아
                    # 그 구간이 실제로 밟힌다.
                    #
                    # ⚠️ `switch.restart()` 를 쓰면 안 된다 — ENDED 에서만 합법이고
                    #    TRACKING 으로 돌려보낸다. SEARCHING 을 유지한 채 탐색만
                    #    새로 시작해야 `CheckReacquired` 가 계속 돈다.
                    #
                    # 종료 판정 권한은 `GuideExec` 의 guide_lost_timeout_sec 이 쥔다.
                    # 타임라인은 통째로 반복한다 — 45초라 어차피 1.4바퀴다.
                    #
                    # 재시작은 1회로 캡한다 — 근거는 __init__ 의 _search_restarts 설명.
                    self._search_restarts += 1
                    self._start_search()
                else:
                    self.switch.search_failed()
        # ENDED: idle — the follow session is over.
