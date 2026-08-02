"""The recovery ORDER must live in tree structure, and must not have drifted from the
timeline that was running before the decomposition."""
from types import SimpleNamespace

import py_trees

from libi_perception.recovery_bt import (
    SearchContext, create_searching_tree, tick_tree,
)
from libi_perception.search_planner import search_command


def _cfg(**over):
    # ⚠️ 실제 config.py 와 같은 값을 둔다. `SEARCH_PEEK_ANGLE` 을 빼면
    # `peek_sec` 의 getattr 기본값 0 으로 떨어져 LkdPeek 구간이 통째로 사라지고,
    # 그러면 이 시험들이 **운영과 다른 타임라인**을 검증하게 된다.
    base = dict(SEARCH_HOLD_SEC=5.0, SEARCH_SWEEP_ANGLE=3.14159,
                ANGULAR_Z_SWEEP=0.55,
                ANGULAR_Z_SEARCH=0.35, SEARCH_TURN_ANGLE=3.14159,
                SEARCH_PEEK_ANGLE=1.5708)
    base.update(over)
    return SimpleNamespace(**base)


#: LkdPeek 길이 — 1.5708 / 0.35 ≈ 4.49초. 아래 시각들이 이 값에 기대고 있다.
PEEK_SEC = 1.5708 / 0.35


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Pub:
    def __init__(self):
        self.calls = []

    def __call__(self, lin, ang):
        self.calls.append((lin, ang))


# ── behaviour preserved from the pre-decomposition BT_Searching ───────────────

def test_reacquire_returns_success():
    ctx = SearchContext(get_detection=lambda: object(), publish=_Pub(),
                        cfg=_cfg(), now=_Clock())
    assert tick_tree(create_searching_tree(ctx)) == py_trees.common.Status.SUCCESS


def test_scanning_publishes_rotation_and_runs():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)                  # establishes start time
    clock.t = 20.0                   # into the first sweep (LkdPeek 4.5 + Hold 10 뒤)
    assert tick_tree(root) == py_trees.common.Status.RUNNING
    assert pub.calls[-1][1] != 0.0


def test_exhausted_returns_failure():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 10_000.0
    assert tick_tree(root) == py_trees.common.Status.FAILURE


# ── the order is really in the tree, not hidden inside one leaf ───────────────

def test_recovery_order_is_tree_structure():
    ctx = SearchContext(get_detection=lambda: None, publish=_Pub(),
                        cfg=_cfg(), now=_Clock())
    root = create_searching_tree(ctx)
    phases = [c for c in root.children if c.name == 'SearchPhases'][0]
    # [2026-07-28] 앞 5초 → 뒤 5초 → 앞 훑기(왕복+원위치) → 뒤 훑기(왕복+원위치).
    # 맹목 Turn180 과 Scan1~3 은 훑기가 흡수했다. search_planner 도 같이 바뀐다.
    # 훑기를 세 구간으로 쪼개는 이유는 recovery_bt 의 `sweep()` 주석 참고 —
    # 시간축을 leaf 안에 숨기면 search_planner 와의 동등성 대조가 불가능해진다.
    assert [c.name for c in phases.children] == [
        'LkdPeek',
        'HoldFront', 'HoldBack',
        'SweepFrontOut', 'SweepFrontAcross', 'SweepFrontHome',
        'SweepBackOut', 'SweepBackAcross', 'SweepBackHome',
        'GiveUp',
    ]
    assert phases.memory is True, 'a finished phase must not restart'
    assert root.memory is False, 'reacquire must be re-checked every tick'
    # 우선순위: 회전 래치 > 반대캠 재획득 > 정위치캠 재획득 > 탐색
    assert [c.name for c in root.children] == [
        'AlignHeading', 'PeekReacquired', 'CheckReacquired', 'SearchPhases',
    ]


def test_reacquire_interrupts_from_any_phase():
    clock, pub = _Clock(), _Pub()
    visible = {'v': False}
    ctx = SearchContext(get_detection=lambda: object() if visible['v'] else None,
                        publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    # ⚠️ **정위치 캠을 보는 구간만 고른다.** 반대 캠 구간(HoldBack·SweepBack)에서는
    #    `CheckReacquired` 가 일부러 FAILURE 를 낸다 — 등을 진 채로 추종을 재개하면
    #    앞캠 좌표로 도는 방위 PID 가 성립하지 않기 때문이다(그쪽 주석 참고).
    for t in (0.0, 6.0, 20.0):       # LkdPeek, HoldFront, SweepFront
        clock.t = t
        assert tick_tree(root) == py_trees.common.Status.RUNNING
        visible['v'] = True
        assert tick_tree(root) == py_trees.common.Status.SUCCESS, f'no interrupt at t={t}'
        visible['v'] = False


def test_giveup_stops_the_robot():
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 10_000.0
    tick_tree(root)
    assert pub.calls[-1] == (0.0, 0.0), 'giving up must halt, not leave it spinning'


# ── equivalence with the reference oracle ─────────────────────────────────────

def test_angular_output_matches_search_command_over_timeline():
    """Sweeps the whole recovery at the real 20 Hz tick and asserts the decomposed tree
    publishes bit-identical angular_z to the pre-decomposition timeline function."""
    for lkd in (1.0, -1.0):
        cfg, clock, pub = _cfg(), _Clock(), _Pub()
        ctx = SearchContext(lambda: None, pub, cfg, clock, lkd=lkd)
        root = create_searching_tree(ctx)
        mismatches = []
        t = 0.0
        while t < 40.0:          # 새 타임라인은 32.85초 — 30 으로 두면 뒤끝이 검증 밖이다
            clock.t = t
            before = len(pub.calls)
            status = tick_tree(root)
            exp_ang, exp_done = search_command(t, cfg, lkd)
            if exp_done:
                assert status == py_trees.common.Status.FAILURE, f'lkd={lkd} t={t}'
            else:
                got = pub.calls[-1][1] if len(pub.calls) > before else None
                if got is None or abs(got - exp_ang) > 1e-9:
                    mismatches.append((round(t, 2), exp_ang, got))
            t += 0.05
        assert not mismatches, f'lkd={lkd} mismatches: {mismatches[:5]}'


# ── 카메라 전환 탐색 (2026-07-27) ────────────────────────────────────────────

def _peek_ctx(role="follow", people=0, detection=None):
    """카메라 요청을 기록하는 컨텍스트."""
    clock, pub = _Clock(), _Pub()
    cams = []
    ctx = SearchContext(get_detection=lambda: detection, publish=pub, cfg=_cfg(),
                        now=clock, select_camera=cams.append,
                        peek_people=lambda: people, role=role)
    return SimpleNamespace(ctx=ctx, clock=clock, pub=pub, cams=cams,
                           root=create_searching_tree(ctx))


def _run_to(env, t, step=0.05):
    """t 초까지 20Hz 로 굴린다. 마지막 상태를 돌려준다."""
    status = None
    while env.clock.t <= t:
        status = tick_tree(env.root)
        env.clock.t = round(env.clock.t + step, 5)
    return status


def test_camera_follows_the_phase_plan():
    """LkdPeek=앞, HoldFront=앞, HoldBack=뒤, SweepFront=앞, SweepBack=뒤. 추종 기준.

    구간 길이: LkdPeek ≈4.49, Hold 5+5, 한 훑기 = 4 × (π/2 / 0.55) ≈ 11.4초.
    """
    env = _peek_ctx(role="follow")
    _run_to(env, 2.0);  assert env.cams[-1] == "front"      # LkdPeek
    _run_to(env, 7.0);  assert env.cams[-1] == "front"      # HoldFront
    _run_to(env, 12.0); assert env.cams[-1] == "back"       # HoldBack
    _run_to(env, 20.0); assert env.cams[-1] == "front"      # SweepFront
    _run_to(env, 30.0); assert env.cams[-1] == "back"       # SweepBack


# ── LKD 90° peek (2026-08-01) ────────────────────────────────────────────────

def test_peek_turns_toward_the_last_known_direction():
    """탐색 첫 구간은 **마지막 방향으로 도는 것**이다 — 서 있는 게 아니다.

    `DrivePolicy` 에 있던 `FOLLOWING --lost--> PEEK(~90°) --> SEARCHING` 을 실주행
    트리로 옮긴 것이다. 부호가 lkd 를 따라가야 왼쪽으로 사라진 사람을 오른쪽에서
    찾는 일이 없다.
    """
    for lkd, sign in ((1.0, +1), (-1.0, -1)):
        clock, pub = _Clock(), _Pub()
        cfg = _cfg()
        ctx = SearchContext(lambda: None, pub, cfg, clock, lkd=lkd)
        root = create_searching_tree(ctx)
        clock.t = 0.0
        tick_tree(root)
        clock.t = PEEK_SEC / 2.0                 # peek 구간 한가운데
        tick_tree(root)
        assert pub.calls[-1] == (0.0, sign * cfg.ANGULAR_Z_SEARCH), f'lkd={lkd}'

        clock.t = PEEK_SEC + 0.1                 # 구간을 지나면 멈춰 선다(HoldFront)
        tick_tree(root)
        assert pub.calls[-1] == (0.0, 0.0), f'lkd={lkd}: peek 뒤에는 서서 봐야 한다'


def test_guide_never_peeks():
    """길잡이는 돌지 않는다 — 목적지 방향과 사람 방향이 겹치면 무한 진동한다.

    `AlignHeading` 을 길잡이에서 뺀 것과 같은 이유다. 구간이 아예 없어야 한다.
    """
    ctx = SearchContext(lambda: None, _Pub(), _cfg(), _Clock(), role="guide")
    phases = [c for c in create_searching_tree(ctx).children
              if c.name == 'SearchPhases'][0]
    assert 'LkdPeek' not in [c.name for c in phases.children]


def test_peek_angle_zero_removes_the_phase():
    """0 이면 끄는 스위치다 — 되돌릴 수 있어야 현장에서 판단할 수 있다."""
    ctx = SearchContext(lambda: None, _Pub(), _cfg(SEARCH_PEEK_ANGLE=0.0), _Clock())
    phases = [c for c in create_searching_tree(ctx).children
              if c.name == 'SearchPhases'][0]
    assert 'LkdPeek' not in [c.name for c in phases.children]


def test_guide_home_camera_is_back():
    """길잡이는 사람이 뒤에 있는 것이 정상이다."""
    env = _peek_ctx(role="guide")
    _run_to(env, 2.0)
    assert env.cams[-1] == "back"
    _run_to(env, 7.0)
    assert env.cams[-1] == "front"          # peek 은 반대쪽


def test_peek_ignores_multiple_people():
    """여럿이면 대상을 특정할 수 없다 — 헛돌면 진짜 대상은 더 멀어진다."""
    env = _peek_ctx(role="follow", people=2)
    _run_to(env, 11.5)
    assert env.ctx.align_latched is False


def test_peek_single_person_latches_align_for_follow():
    env = _peek_ctx(role="follow", people=1)
    _run_to(env, 11.0)
    assert env.ctx.align_latched is True


def test_follow_peek_does_not_advance_the_sequence():
    """PeekPhase 가 SUCCESS 를 내면 다음 tick 에 Scan1 이 돌아 캠이 되돌아간다.
    래치 + RUNNING 이라야 상위 Selector 의 AlignHeading 이 이긴다."""
    env = _peek_ctx(role="follow", people=1)
    _run_to(env, 11.0)
    assert env.cams[-1] == "back"           # 아직 Scan1 로 안 넘어갔다


def test_align_turns_then_restores_home_camera():
    env = _peek_ctx(role="follow", people=1)
    _run_to(env, 11.0)
    assert env.ctx.align_latched is True
    turn_sec = _cfg().SEARCH_TURN_ANGLE / _cfg().ANGULAR_Z_SEARCH
    _run_to(env, 11.0 + turn_sec + 0.2)
    assert env.ctx.align_latched is False
    assert env.ctx.align_used is True
    assert "front" in env.cams              # 정위치 캠으로 되돌렸다


def test_align_happens_at_most_once_per_search():
    """돌았는데도 정위치 캠에서 못 찾으면 반대 캠에는 여전히 그 사람이 보인다.
    제한이 없으면 래치 → 회전 → 못 찾음 → 다시 래치로 **영원히 돈다.**"""
    env = _peek_ctx(role="follow", people=1)
    turn_sec = _cfg().SEARCH_TURN_ANGLE / _cfg().ANGULAR_Z_SEARCH
    _run_to(env, 11.0 + turn_sec + 0.5)
    assert env.ctx.align_used is True
    status = _run_to(env, 40.0)             # 계속 굴려도 다시 안 돈다
    assert env.ctx.align_latched is False
    assert status == py_trees.common.Status.FAILURE     # 탐색을 마치고 포기한다


def test_align_survives_losing_sight_mid_turn():
    """회전 중에는 시야가 바뀌어 재획득이 떨어진다. 우선순위가 낮으면 그 순간
    탐색 시퀀스로 되돌아간다."""
    env = _peek_ctx(role="follow", people=1)
    _run_to(env, 11.0)
    env.ctx.peek_people = lambda: 0          # 회전 중 놓침
    _run_to(env, 13.0)
    assert env.ctx.align_latched is True     # 여전히 정렬 중


def test_guide_front_camera_person_keeps_searching_for_the_back_camera():
    """앞캠 사람은 안내 재획득이 아니다 — 사람에게 다시 뒤로 가 달라고 해야 한다."""
    env = _peek_ctx(role="guide", people=1)
    status = _run_to(env, 11.5)
    assert env.ctx.align_latched is False
    assert "front" in env.cams
    assert status == py_trees.common.Status.RUNNING


def test_guide_front_camera_person_does_not_end_the_search():
    """앞캠 사람이 계속 보여도 뒷캠을 다시 보는 phase 가 살아 있어야 한다."""
    env = _peek_ctx(role="guide", people=1)
    _run_to(env, 11.5)
    _run_to(env, 20.0)
    assert "back" in env.cams
    assert env.root.status == py_trees.common.Status.RUNNING


def test_peek_publishes_zero_velocity():
    """반대 캠 관찰은 **서서** 본다. 돌면서 보면 그 사이 대상이 프레임에서 빠진다."""
    env = _peek_ctx(role="follow", people=0)
    _run_to(env, 7.0)                       # HoldBack 구간 (5~10초)
    assert env.pub.calls[-1] == (0.0, 0.0)


def test_no_camera_hook_still_runs_the_old_way():
    """카메라 없는 배포(전환 훅 미주입)에서도 탐색은 돌아야 한다."""
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub, cfg=_cfg(), now=clock)
    root = create_searching_tree(ctx)
    clock.t = 0.0
    tick_tree(root)
    clock.t = 20.0                   # 훑기 구간 (LkdPeek 4.5 + Hold 10 뒤)
    assert tick_tree(root) == py_trees.common.Status.RUNNING
    assert pub.calls[-1][1] != 0.0


def test_hold_zero_removes_both_hold_phases():
    """SEARCH_HOLD_SEC=0 이면 정지 관찰 없이 곧바로 훑는다.

    길이 0 인 구간을 남기면 `SearchPhase` 가 `elapsed >= end` 로 즉시 SUCCESS 를 내
    같은 tick 에 다음 구간으로 넘어가는데, 그 사이 카메라만 한 번 깜빡인다.
    """
    clock, pub = _Clock(), _Pub()
    ctx = SearchContext(get_detection=lambda: None, publish=pub,
                        cfg=_cfg(SEARCH_HOLD_SEC=0.0), now=clock)
    root = create_searching_tree(ctx)
    phases = [c for c in root.children if c.name == 'SearchPhases'][0]
    assert [c.name for c in phases.children] == [
        'LkdPeek',                   # 이 스위치는 Hold 와 무관하다 — 그대로 남는다
        'SweepFrontOut', 'SweepFrontAcross', 'SweepFrontHome',
        'SweepBackOut', 'SweepBackAcross', 'SweepBackHome',
        'GiveUp',
    ]
