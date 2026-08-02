import time

import py_trees

from . import session as sess
from .pid import angle_deadzone
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
                 select_camera=None, peek_people=None, role="follow", log=None):
        self.get_detection = get_detection
        self.get_scan = get_scan
        self.publish = publish
        self.cfg = cfg
        self.now = now
        #: 회복 BT 가 반대 캠을 보려고 요청할 통로. 주입 안 하면 예전처럼 회전만으로 찾는다.
        self.select_camera = select_camera
        self.peek_people = peek_people
        self.role = role
        #: 진단 로그 통로. 주입 안 하면 조용하다(테스트는 그대로 돈다).
        self._log = log or (lambda _msg: None)
        #: 이번 코스팅 에피소드를 이미 찍었나 — 20Hz 로 매번 찍으면 로그가 묻힌다.
        self._coast_logged = False
        self.switch = FollowSwitch()
        self.tracker = TrackingController(publish, cfg)
        self.miss = 0
        #: 이 세션에서 **대상을 한 번이라도 잡았나.**
        #: 잃어버리려면 먼저 가지고 있어야 한다 — 등록 전에는 검출이 계속 None 이라
        #: 이게 없으면 세션을 연 지 2초(N_MISS_FRAMES 40 @20Hz) 만에 회복 BT 가 돌아
        #: 아무도 등록하지 않았는데 로봇이 혼자 돌며 앞뒤 캠을 번갈아 켠다(실측 2026-07-28).
        self._acquired = False
        self._search_ctx = None
        self._search_tree = None
        self._last_tick = None
        #: 마지막으로 **실제로 본** 대상이 화면 가운데(방위 정지 구간 안)에 있었나.
        #:
        #: 회복 탐색의 첫 단계 `LkdPeek` 는 "마지막으로 돌던 방향으로 90° 돌아본다" 인데,
        #: 대상이 가운데에서 사라졌으면 그건 **어느 쪽으로 나간 게 아니라 가려진 것**이다.
        #: 그때 LKD 로 도는 것은 근거 없는 추측이고, 사람이 다시 나타났을 때 로봇이
        #: 엉뚱한 데를 보고 있게 된다. 사용자 지시(2026-08-02): "가운데에서 사라지면
        #: peek 가 없어도 될 것 같다."
        #:
        #: 기본 False = "아직 아무것도 못 봤다" → peek 를 켠 채로 둔다(기존 동작).
        self._last_centered = False
        #: 안내에서 예측 bbox 를 처음 받은 시각. `GUIDE_COAST_SEC` 유예를 잰다.
        #: 진짜 검출이 오거나 검출이 끊기면 None 으로 되감는다.
        self._guide_coast_since = None

    @property
    def state(self):
        return self.switch.state

    def _is_guide(self) -> bool:
        """안내 역할인가.

        ⚠️ `watch` 는 **뺀다.** 등록 중에는 사람이 패널 화면을 보고 있으므로 카메라
        전환을 앞당길 이유가 없다.
        """
        return self.role == sess.GUIDE

    def _filtered_detection(self):
        """검출의 **유일한** 출입구. TRACKING 분기와 회복 트리(`SearchContext`)가
        **반드시 이 함수를 통해서만** 검출을 봐야 한다 — 원본 `self.get_detection`
        을 어느 한쪽이라도 직접 부르면 그쪽은 예측 bbox 를 걸러내지 못한다.

        추종은 코스팅이 제어의 연속성을 만들어 주므로 그대로 통과시킨다.

        ## 안내(guide)는 예측 bbox 를 **짧게만** 받는다

        예전에는 안내에서 예측 bbox 를 통째로 거부했다 — 즉 코스팅이 **0초**였다.
        이유는 있었다: 가시성 발행(`follow_node.requester_visible`)이 이미 예측을
        거부하므로 여기서 안 거르면 한 프로세스에 "놓쳤다"가 두 개가 되고, 더 나쁜 건
        회복 중에도 예측 bbox 가 재획득으로 읽혀 SEARCHING 이 즉시 TRACKING 으로
        되돌아가는 것이었다.

        ⚠️ [2026-08-02] 그런데 0초는 너무 짧다. 안내는 요청자가 뒤에서 따라오는
        구조라 **잠깐 가려지는 일이 추종보다 오히려 잦다**(로봇이 앞서고 사람이 뒤,
        서가·기둥이 계속 사이를 지난다). `GUIDE_COAST_SEC` 만큼 허용한다 —
        추종(`COAST_LIMIT`)과 **같은 1.4초**다.

        시간으로 재는 이유: 제어 루프(20Hz)와 검출(17fps)의 주기가 달라 **같은 예측
        프레임을 여러 번 볼 수 있다.** 프레임 수를 세면 그만큼 짧아진다.
        """
        det = self.get_detection()
        if det is None:
            self._guide_coast_since = None
            return None
        if not (self._is_guide() and getattr(det, 'is_predicted', False)):
            self._guide_coast_since = None      # 진짜로 보였다 — 유예를 되감는다
            return det

        # ⚠️ **유예는 TRACKING 에서만 준다. 회복(SEARCHING) 중에는 절대 안 준다.**
        #
        #   회복 트리의 `CheckReacquired` 도 이 함수를 통해 검출을 본다. 여기서
        #   예측 bbox 를 통과시키면 **예측만으로 재획득 판정이 나서** SEARCHING 이
        #   즉시 TRACKING 으로 되돌아간다 — 로봇은 사람을 찾았다고 믿지만 실제로는
        #   아무도 안 보인다. 그 상태로 다시 놓치고, 무한히 오간다.
        #
        #   코스팅의 뜻은 "보고 있던 것을 잠깐 이어서 민다" 이지 "못 본 것을 봤다고
        #   치자" 가 아니다. 그래서 이미 놓쳤다고 판정한 뒤(SEARCHING)에는 안 준다.
        if self.switch.state != 'TRACKING':
            self._guide_coast_since = None
            return None

        # 안내 + 예측 + 추종 중 — 유예 안이면 통과, 넘으면 "안 보인다"로 친다.
        now = self.now()
        if self._guide_coast_since is None:
            self._guide_coast_since = now
        limit = getattr(self.cfg, "GUIDE_COAST_SEC", 0.0)
        return det if (now - self._guide_coast_since) <= limit else None

    @property
    def search_tree(self):
        """지금 도는 회복 BT. SEARCHING 이 아니면 None.

        관제 화면이 이 트리를 미션 BT 의 `FollowExec` 밑에 붙여 그린다
        (follow_node.snapshot_dict → /libi/follow_bt_snapshot).
        """
        return self._search_tree if self.switch.state == 'SEARCHING' else None

    def rotation_granted(self):
        """길잡이 대기가 끝나 **회복 회전을 허가받았다** — 여기서 탐색을 시작한다.

        길잡이의 소실 처리는 세 단계다(사용자 스펙 2026-08-02):

            [0, coast)          α-β 로 계속 간다   ← `_filtered_detection`
            [coast, +wait)      정지하고 기다린다   ← 트리 없이 SEARCHING (0 발행)
            [coast+wait, …)     회복 BT            ← 여기서 트리를 만든다

        대기 구간의 길이는 `GuideExec` 이 쥔다(`guide_wait_sec`). 그쪽이 nav2 를
        끊고 다 기다린 뒤 `guide_watch{allow_rotate:true}` 로 알려 준다.

        ⚠️ **대기 중에 트리를 돌리면 안 된다.** 바퀴는 `_publish_for_role` 이
           삼키므로 로봇은 안 돌지만 **트리의 시계는 흐른다** — 한 라운드(32.8초)의
           앞부분이 아무 일도 못 하고 지나간다. 실측으로 실제 회전은 13초뿐이었다.

        ⚠️ **`guide_watch` 재발행 때 부르면 안 된다.** 그 명령은 lease 갱신으로
           10초마다 다시 오고, 그때마다 여기를 부르면 탐색이 영영 처음으로 되감긴다
           (`RemoteControl._on_cmd` 의 "같은 역할이면 인자만 갱신" 분기가 막으려던
           바로 그 버그다). 허가가 **False → True 로 바뀌는 순간**에만 부른다.

        ⚠️ `SEARCHING` 이 아니면 아무것도 안 한다. 추종 중(TRACKING)이나 이미 끝난
           뒤(ENDED)에 탐색을 세우면 없던 회복이 생긴다.
        """
        if self.switch.state != 'SEARCHING':
            return
        self._build_search()

    def _start_search(self):
        """소실 판정 직후. **길잡이는 여기서 트리를 만들지 않는다.**

        길잡이는 `GuideExec` 이 대기(`guide_wait_sec`)를 마치고 회전을 허가할 때
        비로소 탐색이 시작된다 — `rotation_granted` 참고. 그때까지는 `_search_tree`
        가 None 이고, tick 은 0 을 발행하며 서서 기다린다.

        추종·등록감시는 예전 그대로 즉시 탐색한다. 그쪽은 바퀴가 처음부터 자기 것이라
        기다릴 이유가 없다.
        """
        if self._is_guide():
            self._search_tree = None
            return
        self._build_search()

    def _build_search(self):
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
                                         initial_camera=prev_camera,
                                         # 가운데에서 사라졌으면 LKD peek 를 끈다
                                         # (근거: `_last_centered` 주석)
                                         peek=not self._last_centered)
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
                # ⚠️ [2026-08-02] **코스팅이 실제로 도는지 로그로 드러낸다.**
                #
                #   "α-β 가 안 걸린다"를 몇 시간 쫓았는데, 화면(HUD)이 로봇 명령이
                #   아니라 AI 서버 미리보기라 판단 근거가 없었다. 파이프라인만 따로
                #   돌려 보면 예측은 정상으로 나왔다. 남은 미지는 **런타임**이므로
                #   여기서 한 줄 남긴다 — 다음 재현에서 즉시 갈린다:
                #     · 이 줄이 안 뜨면  → 예측이 로봇까지 안 온다(전송·수신 문제)
                #     · 뜨는데 안 움직이면 → 하류(자세 게이트·라이다)가 막는 것이다
                #   에피소드당 한 번만 찍는다. 20Hz 로 매 tick 찍으면 로그가 묻힌다.
                if getattr(det, 'is_predicted', False):
                    if not self._coast_logged:
                        self._coast_logged = True
                        self._log(f'α-β 코스팅 시작 — 예측 bbox 로 추종 유지 '
                                  f'(cx={det.cx:.0f} area={det.area:.0f} '
                                  f'motion_ok={getattr(det, "motion_ok", True)} '
                                  f'posture={getattr(det, "posture", None)})')
                elif self._coast_logged:
                    self._coast_logged = False
                    self._log('α-β 코스팅 끝 — 실검출 복귀')
                # "사라질 때 가운데였나" 를 PID 와 **같은 기준**으로 기록한다.
                # 예측(coast) 프레임도 포함한다 — 코스팅은 마지막으로 본 위치에서
                # 이어지는 것이라 그 사이 화면 위치가 판정의 근거로 유효하다.
                self._last_centered = abs(
                    self.cfg.IMAGE_WIDTH / 2.0 - det.cx) <= angle_deadzone(self.cfg)
                # 자세 게이트는 ``사람에게 다가가는`` 추종 전용 안전 규칙이다.
                # guide 는 nav2 가 목적지로 주행하고 이 루프는 뒷카메라 감시만 한다.
                # 뒤따르는 사람의 자세로 nav2 를 멈추면 안 된다. 역할은 AI 파이프라인이
                # 모르므로, 역할을 가진 이 경계에서만 게이트를 적용한다.
                if (not self._is_guide()) and not getattr(det, 'motion_ok', True):
                    # 보이지만 가면 안 된다 — 누워 있거나, 로봇 코앞이거나, 자세를
                    # 재는 중이다. **miss 를 올리지 않는다**: 올리면 눈앞에 멀쩡히
                    # 보이는 대상을 두고 탐색 회전을 시작한다. 놓친 게 아니라
                    # 가지 않기로 한 것이다.
                    #
                    # ⚠️ [2026-08-01] **측면만은 방위를 살린다 — 원래 스펙으로 되돌린 것이다.**
                    #
                    #   `posture_gate._STOP_NOW` 는 (Lying, Calibrating, Side) 를 모두
                    #   같은 "완전 정지"로 묶는데, 2026-07-26 설계는 측면을 따로 뒀다:
                    #       "전진 금지 │ 방위 PID 만 유지(yaw) — 사람을 화면에 붙잡되
                    #        다가가지 않는다. 사람이 옆을 보는 건 흔하다(책 보기, 대화).
                    #        그때마다 추종이 끊기면 쓸모가 없다. **'거리 판단만 못 믿겠다'
                    #        이지 '사람을 놓쳤다'가 아니다.**"
                    #
                    #   실제로 **따라가는 사람은 대부분 등이나 옆을 보인다.** 완전 정지로
                    #   묶으면 화면은 FOLLOWING 인데 바퀴는 0 인 상태가 계속된다
                    #   (실측 2026-08-01: "화면엔 OWNER 가 잡히는데 안 움직인다").
                    #
                    #   측면에서 못 믿는 것은 **거리(√area)** 뿐이다 — 몸을 돌리면 폭이
                    #   줄어 실제보다 멀어 보인다. 방위(cx)는 그 영향을 안 받으므로
                    #   그대로 쓴다. 누움·기준측정은 예전대로 완전 정지다.
                    if getattr(det, 'posture', None) == 'Side':
                        self.tracker.step(det, self.get_scan(), self._dt(),
                                          forward_blocked=True)
                    else:
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
                    self._start_search()
        elif self.switch.state == 'SEARCHING':
            if self._search_tree is None:
                # 길잡이 대기 구간 — 아직 회전을 허가받지 못했다. 서서 기다린다.
                #
                # ⚠️ **여기서도 재획득을 봐야 한다.** 평소엔 회복 트리의
                #    `CheckReacquired` 가 그 일을 하는데 대기 중에는 트리가 없다.
                #    안 보면 사람이 다시 나타나도 `SEARCHING` 에 갇히고, 그 뒤 허가가
                #    안 오면(= 다시 보여서 `GuideExec` 이 허가를 안 켠다) **영영 못
                #    빠져나온다.** 이 세션은 그 뒤로 회복도 추종도 못 한다.
                if self._filtered_detection() is not None:
                    self.switch.reacquired()
                    self.miss = 0
                    self.tracker.reset()
                    self._last_tick = None
                self.publish(0.0, 0.0)
                return
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
