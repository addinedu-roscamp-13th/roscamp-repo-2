"""회복 탐색의 **참조 타임라인**. 트리(`recovery_bt`)가 이것과 같은 각속도를 내야 한다.

## [2026-07-27] 의도적 동작 변경 — 뒤를 보는 방법이 바뀌었다

예전에는 뒤를 보려고 **몸을 180° 돌렸다**(`SEARCH_TURN_ANGLE / ANGULAR_Z_SEARCH`,
0.35 rad/s 기준 약 9초). 로봇에 앞뒤 카메라가 둘 다 달리면서 그 9초가 필요 없어졌다 —
**카메라 전환은 공짜다.** 그래서 `PeekBack`(정지한 채 반대 캠 관찰) 2초짜리 두 구간이
들어가고, 맹목 180° 회전은 뒤로 밀려 **사각을 위한 보루**가 됐다.

    옛  Hold 10 → Scan1 4 → Turn180 ~9 → Scan2 4                            ≈ 27초
    새  Hold 10 → Peek 2 → Scan1 4 → Peek2 2 → Scan2 4 → Turn180 ~9 → Scan3 4  ≈ 35초
        흔한 경우(사람이 바로 뒤)는 **22초 안에** Peek 으로 끝난다.
        35초까지 가는 것은 앞뒤 화각 어디에도 안 잡힌 경우뿐이다.

## 왜 맹목 회전을 완전히 없애지 않았나

앞뒤 화각을 ±30° 로 가정하면 스캔(각 80°)과 합쳐도 사각이 두 군데(약 40°, 20°) 남는다.
그 구멍에 사람이 서 있으면 못 찾는다. **화각은 아직 실측 전이라 이 계산은 가정 위에
있다** — 실측해서 사각이 없으면 `Turn180`·`Scan3` 을 지우고 이 함수도 같이 줄인다.

이 함수는 트리의 **참조 구현**이다. 한쪽만 고치면 `test_recovery_bt` 의 동등성 검증이
거짓말을 하게 되므로 항상 같이 고친다.
"""


def search_command(elapsed, cfg, lkd=1.0):
    """경과 초 → (angular_z, 끝났나).

    Peek 구간은 **정지**다(각속도 0). 카메라만 반대쪽으로 바뀌는데, 그 전환은
    `recovery_bt` 쪽 관심사라 각속도만 보는 여기서는 표현하지 않는다.
    """
    hold = cfg.SEARCH_HOLD_SEC
    scan = cfg.SEARCH_SCAN_SEC
    peek = getattr(cfg, "SEARCH_PEEK_SEC", 0.0)
    turn = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH

    t_hold_end = hold
    t_peek1_end = t_hold_end + peek
    t_scan1_end = t_peek1_end + scan
    t_peek2_end = t_scan1_end + peek
    t_scan2_end = t_peek2_end + scan
    t_turn_end = t_scan2_end + turn
    t_scan3_end = t_turn_end + scan

    if elapsed < t_hold_end:
        return 0.0, False
    if elapsed < t_peek1_end:
        return 0.0, False                       # 반대 캠 관찰 — 서서 본다
    if elapsed < t_scan1_end:
        return cfg.ANGULAR_Z_SEARCH * lkd, False
    if elapsed < t_peek2_end:
        return 0.0, False
    if elapsed < t_scan2_end:
        return cfg.ANGULAR_Z_SEARCH * -lkd, False
    if elapsed < t_turn_end:
        return cfg.ANGULAR_Z_SEARCH, False      # 사각 보루 — 맹목 180° 회전
    if elapsed < t_scan3_end:
        return cfg.ANGULAR_Z_SEARCH * lkd, False
    return 0.0, True
