"""사람 차단 정책. ROS·카메라 없이 규칙만 본다."""
from libi_modes.common.person_block import BLOCK, DRIVE, HALT, PersonBlockPolicy


def _p(**over):
    kw = dict(stop_size=209.0, sustain_sec=10.0, resume_grace_sec=1.0)
    kw.update(over)
    return PersonBlockPolicy(**kw)


def test_no_detection_drives():
    assert _p().update(None, True, 0.0) == DRIVE


def test_small_person_drives():
    assert _p().update(208.9, True, 0.0) == DRIVE


def test_big_person_halts_immediately():
    assert _p().update(209.0, True, 0.0) == HALT


def test_block_after_the_sustain_window():
    p = _p()
    assert p.update(300.0, True, 0.0) == HALT
    assert p.update(300.0, False, 9.9) == HALT
    assert p.update(300.0, False, 10.0) == BLOCK


def test_block_is_reported_once():
    p = _p()
    p.update(300.0, True, 0.0)
    assert p.update(300.0, False, 10.0) == BLOCK
    assert p.update(300.0, False, 11.0) == HALT


def test_passerby_never_blocks():
    p = _p()
    p.update(300.0, True, 0.0)
    p.update(None, True, 1.0)
    p.update(None, True, 2.5)          # 유예를 넘겨 확실히 비켰다
    assert p.update(300.0, True, 3.0) == HALT
    assert p.update(300.0, False, 12.0) == HALT   # 3.0 부터 다시 센다
    assert p.update(300.0, False, 13.0) == BLOCK


def test_flicker_inside_the_grace_keeps_the_timer():
    p = _p()
    p.update(300.0, True, 0.0)
    p.update(None, True, 5.0)          # 잠깐 안 보임 — 유예 안
    assert p.update(300.0, True, 5.5) == HALT
    assert p.update(300.0, False, 10.0) == BLOCK   # 0.0 기준 그대로


def test_detection_is_ignored_until_straight_driving_resumes():
    p = _p()
    p.update(300.0, True, 0.0)
    assert p.update(300.0, False, 10.0) == BLOCK
    assert p.update(300.0, False, 20.0) == DRIVE   # 회전 중 — 무장 해제
    assert p.update(300.0, True, 21.0) == HALT     # 직진 재개 — 다시 무장


def test_rearm_restarts_the_sustain_window():
    p = _p()
    p.update(300.0, True, 0.0)
    p.update(300.0, False, 10.0)
    p.update(300.0, True, 21.0)
    assert p.update(300.0, False, 30.9) == HALT
    assert p.update(300.0, False, 31.0) == BLOCK


def test_reset_clears_everything():
    p = _p()
    p.update(300.0, True, 0.0)
    p.reset()
    assert p.update(300.0, True, 100.0) == HALT
    assert p.update(300.0, False, 109.9) == HALT
    assert p.update(300.0, False, 110.0) == BLOCK


# ── 재무장 타임아웃 (2026-08-03 실기) ────────────────────────────────────────

def test_rearm_happens_even_without_straight_motion():
    """알린 뒤 로봇이 **못 움직여도** 시간이 지나면 다시 무장한다.

    실기에서 사람이 코앞이면 nav2 가 경로 자체를 못 만든다
    (`Failed to create plan with tolerance of: 0.100000`). 직진이 영영 안 돌아오니
    무장도 영영 안 되고, 판정이 DRIVE 를 돌려 **214px(임계 170) 사람 앞에서도 정지를
    안 걸었다.** 되돌림 확인: `timed_out` 조건을 지우면 이 시험이 빨개진다.
    """
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    assert p.update(300.0, True, 0.0) == HALT
    assert p.update(300.0, True, 10.0) == BLOCK          # 10초 채움 → 보고, 무장 해제
    assert p.armed is False
    # 로봇이 못 움직인다(직진 없음). 유예를 넘기면 예전엔 DRIVE 로 풀렸다.
    assert p.update(300.0, False, 12.0) == DRIVE         # 아직 재무장 시간 전
    # rearm_timeout_sec(기본 = 2 x sustain_sec = 20) 이 지나면 직진 없이도 다시 무장한다.
    assert p.update(300.0, False, 31.0) == HALT
    assert p.armed is True


def test_rearm_timeout_can_be_set_apart_from_sustain():
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0,
                          rearm_timeout_sec=3.0)
    p.update(300.0, True, 0.0)
    assert p.update(300.0, True, 10.0) == BLOCK
    assert p.update(300.0, False, 12.0) == DRIVE         # 3초 전 — 아직
    assert p.update(300.0, False, 13.5) == HALT          # 지났다 — 다시 선다


def test_straight_motion_still_rearms_immediately():
    """타임아웃을 넣었다고 원래의 '직진하면 즉시 재무장' 이 사라지면 안 된다."""
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    p.update(300.0, True, 0.0)
    assert p.update(300.0, True, 10.0) == BLOCK
    assert p.update(300.0, True, 11.0) == HALT           # 직진 → 타임아웃 전인데도 무장
    assert p.armed is True


# ── 남은 초 내보내기 (2026-08-03, 사용자 요구) ────────────────────────────────
# 관제에서 재계획이 **사람 때문인지 지연 때문인지** 구분이 안 돼 디버깅이 어려웠다.
# 판정에 쓰는 값 그 자체를 내보낸다 — 화면이 따로 계산하지 않게.

def test_seconds_to_block_is_none_before_anyone_is_seen():
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    assert p.seconds_to_block(0.0) is None


def test_seconds_to_block_counts_down_from_sustain():
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    p.update(300.0, True, 0.0)
    assert p.seconds_to_block(0.0) == 10.0
    p.update(300.0, True, 4.0)
    assert p.seconds_to_block(4.0) == 6.0


def test_seconds_to_block_is_none_after_reporting():
    """알린 뒤에는 재무장 전까지 안 센다 — 화면에 유령 카운트다운이 남으면 안 된다."""
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    p.update(300.0, True, 0.0)
    assert p.update(300.0, True, 10.0) == BLOCK
    assert p.seconds_to_block(10.0) is None


def test_seconds_to_block_clears_when_the_person_leaves():
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    p.update(300.0, True, 0.0)
    assert p.seconds_to_block(0.0) == 10.0
    p.update(None, True, 2.0)          # 비켰다 — 유예 시작
    p.update(None, True, 3.5)          # 유예를 넘겼다 — 타이머 버림
    assert p.seconds_to_block(3.5) is None


def test_seconds_to_block_never_goes_negative():
    """`update` 없이 시간만 지나도 화면에 음수가 뜨면 안 된다."""
    p = PersonBlockPolicy(stop_size=170.0, sustain_sec=10.0, resume_grace_sec=1.0)
    p.update(300.0, True, 0.0)
    assert p.seconds_to_block(99.0) == 0.0
