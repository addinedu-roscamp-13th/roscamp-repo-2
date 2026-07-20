"""Recovery behaviour tree — the search ORDER lives here as tree structure.

    BT_Searching (Selector, memory=False)
    ├── CheckReacquired          owner visible again -> SUCCESS (interrupts any phase)
    └── SearchPhases (Sequence, memory=True)
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


class SearchContext:
    """Injected dependencies for the searching tree (no ROS)."""

    def __init__(self, get_detection, publish, cfg, now, lkd=1.0):
        self.get_detection = get_detection
        self.publish = publish
        self.cfg = cfg
        self.now = now
        self.lkd = lkd
        self.start = None


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


def create_searching_tree(ctx):
    cfg = ctx.cfg
    turn_sec = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH
    spec = [
        ('Hold', cfg.SEARCH_HOLD_SEC, lambda: 0.0),
        ('Scan1', cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * ctx.lkd),
        ('Turn180', turn_sec, lambda: cfg.ANGULAR_Z_SEARCH),
        ('Scan2', cfg.SEARCH_SCAN_SEC, lambda: cfg.ANGULAR_Z_SEARCH * -ctx.lkd),
    ]
    phases, offset = [], 0.0
    for name, duration, angular_fn in spec:
        phases.append(SearchPhase(ctx, name, offset, offset + duration, angular_fn))
        offset += duration

    body = py_trees.composites.Sequence(
        name='SearchPhases', memory=True, children=phases + [GiveUp(ctx)],
    )
    return py_trees.composites.Selector(
        name='BT_Searching', memory=False, children=[CheckReacquired(ctx), body],
    )


def tick_tree(root):
    root.tick_once()
    return root.status
