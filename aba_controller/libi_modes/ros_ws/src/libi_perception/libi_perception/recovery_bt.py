"""Recovery behaviour tree — the search ORDER lives here as tree structure.

    BT_Searching (Selector, memory=False)
    ├── CheckReacquired          owner visible again -> SUCCESS (interrupts any phase)
    └── SearchPhases (Sequence, memory=True)
        ├── LkdPeek              turn ~90 deg toward the last-known direction (follow only)
        ├── Hold                 stay still, give the owner a moment to reappear
        ├── Scan1                sweep toward the last-known direction
        ├── Turn180              turn around
        ├── Scan2                sweep the other way
        └── GiveUp               stop and report FAILURE

This replaces a single `SearchMotion` leaf that hid the whole timeline inside a
time-indexed if-chain. Same motion, but the order is now readable and editable as tree
structure — reordering recovery is moving a node, not rewriting a conditional.

Holding still first is deliberate: a robot that starts spinning the instant a person is
briefly occluded looks broken and can throw away a target it would otherwise have kept.

Each phase owns an absolute [begin, end) window measured from the shared ctx.start, with
the builder accumulating offsets from per-phase durations. Per-phase timers started on
initialise() would NOT be equivalent — on a sparse or jumped tick they advance only one
phase and let total recovery time drift. tests/test_recovery_bt.py pins the equivalence
against search_planner.search_command() across the full timeline at the real 20 Hz.
"""
import py_trees
from py_trees.common import Status

from .search_planner import peek_sec
from .session import GUIDE, ROLE_CAMERA


class SearchContext:
    """Injected dependencies for the searching tree (no ROS).

    `select_camera` / `peek_people` / `role` 은 카메라 전환 탐색을 위해 추가됐다.
    주입하지 않으면(단독 시험·카메라 없는 배포) 전환 요청은 아무 일도 안 하고
    반대 캠 관찰은 항상 "아무도 없음"으로 읽혀, 예전과 같은 순수 회전 탐색이 된다.
    """

    def __init__(self, get_detection, publish, cfg, now, lkd=1.0,
                 select_camera=None, peek_people=None, role="follow",
                 initial_camera=None, peek=True):
        self.get_detection = get_detection
        self.publish = publish
        self.cfg = cfg
        self.now = now
        self.lkd = lkd
        #: LKD peek(마지막 방향으로 90°)를 할지. 대상이 **화면 가운데에서** 사라졌으면
        #: 호출자가 False 를 준다 — 어느 쪽으로 나갔다는 정보가 없어 추측이 되기 때문이다.
        #: 근거는 `search_planner.peek_sec` 머리말.
        self.peek = bool(peek)
        self.start = None
        #: 카메라를 바꿔 달라는 요청. 실제 발행은 follow_node 가 한다
        #: (camera_select 발행자는 하나뿐이라는 규칙).
        self._select_camera = select_camera or (lambda _name: None)
        #: 반대 캠에 사람이 몇 명 보이나. **신원은 안 본다** — 앞뒤 캠은 렌즈·노출이
        #: 달라 크로스카메라 재식별을 못 믿는다. 신원 확인은 회전 후 정위치 캠에서 한다.
        self.peek_people = peek_people or (lambda: 0)
        self.role = role
        #: 지금 어느 캠을 보고 있나. 새 탐색(재시작 아님)은 정위치 캠이 맞다.
        #: 재시작이면 호출자가 **이전 탐색이 실제로 남겨 둔 캠**을 넘긴다 —
        #: 안 넘기면 여기가 `home_camera` 라고 낙관적으로 가정하는데, 소진된
        #: 회복은 보통 `peek` 캠에서 끝나므로 그 가정이 틀린다(2026-08-01 codex).
        self._camera_now = (initial_camera if initial_camera in ("front", "back")
                            else self.home_camera)
        #: 추종에서 반대 캠에 잡혔다 → 몸을 돌려야 제어가 된다.
        self.align_latched = False
        #: 이번 탐색에서 이미 한 번 돌았나. **탐색 1회당 정렬 1회**로 막는다 —
        #: 돌았는데도 정위치 캠에서 못 찾으면 반대 캠에는 여전히 그 사람이 보이므로,
        #: 제한이 없으면 래치 → 회전 → 못 찾음 → 다시 래치로 **영원히 돈다.**
        #: 한 번 돌아서 안 되면 그 사람이 대상이 아니었던 것이니 탐색을 계속한다.
        self.align_used = False
        #: 길잡이에서 반대 캠에 잡혔다 → 회전 없이 보는 캠만 그쪽으로 두고 끝낸다.
        self.peek_reacquired = False
        self.peek_camera_locked = None

    def select_camera(self, name):
        """카메라 전환 요청 + 지금 보는 캠 기록."""
        self._camera_now = name
        self._select_camera(name)

    def camera_now(self):
        return self._camera_now

    @property
    def home_camera(self):
        """정위치 캠. 추종·야간순찰은 사람이 앞에, 길잡이는 뒤에 있는 것이 정상이다.

        ⚠️ [2026-08-07] 예전엔 `"front" if role == "follow" else "back"` 이라
           **`security` 를 길잡이로 쳤다.** 야간순찰 세션은 앞캠인데
           (`session.ROLE_CAMERA[SECURITY] == "front"`) 대상을 놓치는 순간 탐색이
           `select_camera("back")` 을 20Hz 로 내서, 침입자가 앞에 있는데 뒷캠을 봤다.
           `IntruderChase.CameraSelectRenew` 가 1Hz 로 내는 `"front"` 와 싸워서 지는
           것이라 로그에도 안 남는다(그 잎 독스트링의 "둘 다 front 를 내므로 결과가
           같다"는 가정이 정확히 여기서 깨진다).

           역할→캠은 `session.ROLE_CAMERA` 가 정본이다. 여기서 다시 안 적는다.
        """
        return ROLE_CAMERA.get(self.role, "front")

    @property
    def peek_camera(self):
        """반대 캠. 정위치의 반대라는 뜻이라 `home_camera` 에서 유도한다."""
        return "back" if self.home_camera == "front" else "front"


class CheckReacquired(py_trees.behaviour.Behaviour):
    """SUCCESS the moment the owner is visible again.

    Sits above the phase sequence in a memory=False Selector, so it is re-evaluated every
    tick and can cut recovery short from any phase — that is what makes the interrupt
    work regardless of how far through the timeline the robot has got.
    """

    def __init__(self, ctx):
        super().__init__(name='CheckReacquired')
        self.ctx = ctx

    def update(self):
        # ⚠️ **정위치 캠을 보고 있을 때만** 재획득으로 친다.
        #
        # 반대 캠을 보는 구간(HoldBack·SweepBack)에서 잡힌 것을 "정면에서 다시 보인다"로
        # 읽으면, 로봇이 등을 진 채로 탐색을 끝내고 그대로 추종을 재개한다. 방위 PID 는
        # 앞캠 좌표로 속도를 만들므로 대상이 뒤에 있으면 제어가 성립하지 않는다.
        # 반대 캠에서 찾았을 때의 정답은 `AlignHeading`(180° 회전)이고, 그건 `PeekPhase`
        # 가 래치를 세워 처리한다.
        if self.ctx.camera_now() != self.ctx.home_camera:
            return Status.FAILURE
        if self.ctx.get_detection() is not None:
            return Status.SUCCESS
        return Status.FAILURE


class SearchPhase(py_trees.behaviour.Behaviour):
    """Publishes a fixed angular velocity while elapsed time is inside [begin, end).

    SUCCESS once the window has passed, which advances the memory=True Sequence to the
    next phase. `angular_fn` is a callable so lkd is read at tick time rather than baked
    in at build time.
    """

    def __init__(self, ctx, name, begin, end, angular_fn):
        super().__init__(name=name)
        self.ctx = ctx
        self.begin = begin
        self.end = end
        self.angular_fn = angular_fn

    def initialise(self):
        if self.ctx.start is None:
            self.ctx.start = self.ctx.now()

    def update(self):
        elapsed = self.ctx.now() - self.ctx.start
        if elapsed >= self.end:
            return Status.SUCCESS
        self.ctx.publish(0.0, self.angular_fn())
        return Status.RUNNING


class CameraPhase(SearchPhase):
    """구간에 들어갈 때 이 구간이 볼 카메라를 요청하는 SearchPhase."""

    def __init__(self, ctx, name, begin, end, angular_fn, camera):
        super().__init__(ctx, name, begin, end, angular_fn)
        self.camera = camera

    def update(self):
        self.ctx.select_camera(self.camera)
        return super().update()


class PeekPhase(CameraPhase):
    """반대 캠으로 **서서** 관찰한다. 사람이 정확히 한 명이면 래치만 세운다.

    ## 왜 SUCCESS 를 돌려주면 안 되나

    이 노드는 `SearchPhases`(`Sequence(memory=True)`) 안에 있다. 거기서 SUCCESS 는
    "재획득했다"가 아니라 **"다음 phase 로 넘어가라"**는 뜻이다. SUCCESS 를 내면 다음
    tick 에 `Scan1` 이 돌아 카메라가 정위치로 되돌아가고, 결국 `GiveUp` 까지 간다.
    그래서 래치만 세우고 RUNNING 을 돌려준다 — 상위 Selector 가 `memory=False` 라
    다음 tick 에 위쪽 노드(`AlignHeading` / `PeekReacquired`)가 먼저 평가되어 이긴다.

    ## 여럿이면 반응하지 않는 이유

    대상을 특정할 수 없다. 서가 사이 다른 이용자에게 반응해 9초를 헛돌면, 그 사이
    진짜 대상은 더 멀어진다.
    """

    def update(self):
        self.ctx.select_camera(self.camera)
        if self.ctx.peek_people() == 1:
            # 길잡이만 예외다 — 주행을 nav2 가 하므로 돌면 안 된다(`AlignHeading` 머리말).
            # 야간순찰은 추종과 제어가 같아서(캠이 제어 입력) 같이 돈다.
            if self.ctx.role != GUIDE:
                if self.ctx.align_used:
                    # 이미 한 번 돌아 봤는데 정위치 캠에서 못 찾았다. 반대 캠에는
                    # 여전히 누군가 보이지만 그 사람은 대상이 아니었다 — 다시 돌면
                    # 영원히 제자리에서 돈다. 탐색을 그냥 이어간다.
                    return super().update()
                self.ctx.align_latched = True
            else:
                # 길잡이가 앞캠에서 사람을 본 것은 "다시 뒤로 이동해 달라"고
                # 화면에 알릴 상태이지 재획득이 아니다. 성공으로 끝내면 카메라가
                # 앞에 고정되어 뒷캠으로 돌아온 사람을 다시 볼 수 없고, 그 True 가
                # GuideExec 에 전달되면 nav2 도 재개한다. 탐색 타임라인은 계속해서
                # 다음(뒷캠 포함) phase 로 진행한다.
                self.ctx.publish(0.0, 0.0)
                return super().update()
            self.ctx.publish(0.0, 0.0)
            return Status.RUNNING
        return super().update()


class PeekReacquired(py_trees.behaviour.Behaviour):
    """길잡이가 반대 캠에서 찾았다 → **탐색 자체를 끝낸다.**

    `SearchPhases` 보다 위(우선순위 높음)에 둔다. 이유는 `PeekPhase` 주석과 같다.
    성공시키는 순간 카메라는 찾은 쪽으로 고정된 채 남는다 — 그 사람을 계속 보려면
    그래야 하고, 길잡이는 주행을 nav2 가 하므로 캠이 앞이든 뒤든 상관없다.
    """

    def __init__(self, ctx):
        super().__init__(name='PeekReacquired')
        self.ctx = ctx

    def update(self):
        if not self.ctx.peek_reacquired:
            return Status.FAILURE
        if self.ctx.peek_camera_locked:
            self.ctx.select_camera(self.ctx.peek_camera_locked)
        self.ctx.publish(0.0, 0.0)      # 탐색 회전을 확실히 멈춘다
        return Status.SUCCESS


class AlignHeading(py_trees.behaviour.Behaviour):
    """반대 캠에서 찾았을 때 180° 돌아 대상을 정위치 캠으로 옮긴다. **추종 전용.**

    ## 왜 추종에만 있나

    추종에서 카메라는 **제어 입력**이다 — 방위·거리 PID 가 앞캠 좌표로 속도를 만든다.
    대상이 앞에 없으면 제어 자체가 불가능하므로 회전이 필수다.
    길잡이에서 카메라는 **감시일 뿐**이고 주행은 nav2 가 좌표로 한다. 거기서 돌면
    목적지 방향과 사람 방향이 겹칠 때 무한 진동한다.

    ## 왜 Selector 맨 앞인가

    래치되면 회전이 끝날 때까지 매 tick 이겨야 한다. 회전 중에는 시야가 바뀌어
    재획득 판정이 떨어지는데, 우선순위가 낮으면 그 순간 탐색 시퀀스로 되돌아간다.
    """

    def __init__(self, ctx):
        super().__init__(name='AlignHeading')
        self.ctx = ctx
        self._t0 = None

    def update(self):
        if not self.ctx.align_latched:
            return Status.FAILURE
        if self._t0 is None:
            self._t0 = self.ctx.now()
        turn_sec = self.ctx.cfg.SEARCH_TURN_ANGLE / self.ctx.cfg.ANGULAR_Z_SEARCH
        if self.ctx.now() - self._t0 >= turn_sec:
            self.ctx.align_latched = False
            self.ctx.align_used = True      # 이번 탐색에서는 더 돌지 않는다
            self._t0 = None
            self.ctx.select_camera(self.ctx.home_camera)
            self.ctx.publish(0.0, 0.0)
            return Status.SUCCESS
        self.ctx.publish(0.0, self.ctx.cfg.ANGULAR_Z_SEARCH)
        return Status.RUNNING


class GiveUp(py_trees.behaviour.Behaviour):
    """Terminal phase: halt the robot and fail the tree so the caller ends the session.

    Publishing a zero command here matters — reaching the end of recovery without it
    would leave the last rotation command standing and the robot spinning.
    """

    def __init__(self, ctx):
        super().__init__(name='GiveUp')
        self.ctx = ctx

    def update(self):
        self.ctx.publish(0.0, 0.0)
        return Status.FAILURE


def sweep_leg_sec(cfg) -> float:
    """훑기 한 구간(중앙 → 한쪽 끝)에 걸리는 시간.

    `SEARCH_SWEEP_ANGLE` 이 훑는 **전체 폭**이라 한쪽 끝까지는 그 절반이다.
    왕복+복귀는 이 값의 4배(= 총 회전량 2×폭)다.
    """
    return (cfg.SEARCH_SWEEP_ANGLE / 2.0) / cfg.ANGULAR_Z_SWEEP


def create_searching_tree(ctx):
    """탐색 순서를 트리 구조로 세운다. 참조 구현은 `search_planner.search_command()` 다.

    타임라인이 바뀌면 **둘 다** 고쳐야 한다 — 한쪽만 고치면 동등성 검증이 거짓말을 한다.
    """
    cfg = ctx.cfg
    hold = cfg.SEARCH_HOLD_SEC
    w = cfg.ANGULAR_Z_SWEEP
    leg = sweep_leg_sec(cfg)            # 중앙 → 한쪽 끝
    # LKD 90° (추종 전용, 0 이면 구간이 빠진다). `ctx.peek` 는 '가운데에서
    # 사라졌으면 끈다' — 근거는 `search_planner.peek_sec` 머리말.
    peek_turn = peek_sec(cfg, ctx.role, getattr(ctx, 'peek', True))
    home, peek = ctx.home_camera, ctx.peek_camera

    def sweep(prefix, camera):
        """좌우로 훑고 **원위치로 돌아오는** 세 구간.

        한 노드로 합치지 않는 이유: `search_planner.search_command()` 와 각속도
        타임라인을 1:1 로 대조하는 동등성 시험이 있는데(test_recovery_bt), 시간축을
        leaf 안에 숨기면 그 대조가 불가능해진다. 구간이 곧 계약이다.

        원위치 복귀가 마지막에 붙는 이유: 안 돌아오면 다음 탐색의 기준 방위가 매번
        달라져 "어디를 이미 봤는지"를 알 수 없게 된다.
        """
        return [
            (f'{prefix}Out',    leg,     lambda: w * ctx.lkd,  camera),   # 0 → +끝
            (f'{prefix}Across', 2 * leg, lambda: -w * ctx.lkd, camera),   # +끝 → -끝
            (f'{prefix}Home',   leg,     lambda: w * ctx.lkd,  camera),   # -끝 → 0
        ]

    spec = [
        # (이름, 길이, 각속도, 이 구간이 볼 카메라)
        #
        # 사람이 나간 쪽부터 본다 — α-β coast 가 이미 유예를 줬으므로, 여기 왔다는 건
        # "잠깐 가렸다"가 아니라 어딘가로 나갔다는 뜻이다. 근거는 config 주석 참고.
        # 길잡이는 `peek_turn` 이 0 이라 이 구간이 통째로 빠진다(길이 0 → 트리에서 제외).
        ('LkdPeek', peek_turn, lambda: cfg.ANGULAR_Z_SEARCH * ctx.lkd, home),
        # 앞을 보며 잠깐 기다린다 — 사람이 그냥 다시 나타나는 경우가 제일 흔하다.
        ('HoldFront', hold, lambda: 0.0, home),
        # 그 다음 **바로 뒤를 본다.** 캠 전환은 공짜라 돌 필요가 없다.
        # 여기서 한 명이 잡히면 `PeekPhase` 가 래치를 세우고, 다음 tick 에 Selector
        # 맨 위의 `AlignHeading` 이 이겨 180° 회전으로 넘어간다.
        ('HoldBack', hold, lambda: 0.0, peek),
        # 정지 관찰로 못 찾았으면 훑는다. 앞뒤 각각 좌우로 훑고 원위치로 돌아온다.
        *sweep('SweepFront', home),
        *sweep('SweepBack', peek),
    ]
    phases, offset = [], 0.0
    #: 반대 캠을 보는 구간은 `PeekPhase` 여야 한다 — 거기서 잡힌 대상은 재획득이 아니라
    #  **회전 대상**이다(그대로 추종하면 로봇이 등을 진 채로 따라간다).
    peek_names = {'HoldBack', 'SweepBackOut', 'SweepBackAcross', 'SweepBackHome'}
    for name, duration, angular_fn, camera in spec:
        if duration <= 0:
            continue                    # 길이를 0 으로 두면 그 구간이 사라진다
        klass = PeekPhase if name in peek_names else CameraPhase
        phases.append(klass(ctx, name, offset, offset + duration, angular_fn, camera))
        offset += duration

    body = py_trees.composites.Sequence(
        name='SearchPhases', memory=True, children=phases + [GiveUp(ctx)],
    )
    # 우선순위 순서가 곧 규칙이다. memory=False 라 매 tick 위에서부터 다시 본다.
    #   AlignHeading     래치되면 회전이 끝날 때까지 무조건 이긴다 — 추종 전용
    #   PeekReacquired   반대 캠에서 찾았다(길잡이) → 탐색 종료
    #   CheckReacquired  정위치 캠에서 다시 보인다 → 탐색 종료
    #   SearchPhases     위 셋 다 아니면 탐색을 이어간다
    return py_trees.composites.Selector(
        name='BT_Searching', memory=False,
        children=[AlignHeading(ctx), PeekReacquired(ctx), CheckReacquired(ctx), body],
    )


def tick_tree(root):
    root.tick_once()
    return root.status
