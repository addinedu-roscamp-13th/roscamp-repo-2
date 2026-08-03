"""회복 탐색의 **참조 타임라인**. 트리(`recovery_bt`)가 이것과 같은 각속도를 내야 한다.

## [2026-07-27] 의도적 동작 변경 — 뒤를 보는 방법이 바뀌었다

예전에는 뒤를 보려고 **몸을 180° 돌렸다**(`SEARCH_TURN_ANGLE / ANGULAR_Z_SEARCH`,
0.35 rad/s 기준 약 9초). 로봇에 앞뒤 카메라가 둘 다 달리면서 그 9초가 필요 없어졌다 —
**카메라 전환은 공짜다.** 그래서 `PeekBack`(정지한 채 반대 캠 관찰) 2초짜리 두 구간이
들어가고, 맹목 180° 회전은 뒤로 밀려 **사각을 위한 보루**가 됐다.

    옛  Hold 10 → Scan1 4 → Turn180 ~9 → Scan2 4                            ≈ 27초
    새  Hold 10 → Peek 2 → Scan1 4 → Peek2 2 → Scan2 4 → Turn180 ~9 → Scan3 4  ≈ 35초

## [2026-08-01] 맨 앞에 LKD 90° peek 이 붙었다 (추종 전용)

`LkdPeek ~4.5초 → HoldFront → HoldBack → SweepFront → SweepBack → GiveUp`.
근거와 되살린 경위는 `config.SEARCH_PEEK_ANGLE` 주석에 있다. 길잡이는 이 구간이
빠지므로 타임라인이 예전과 같다.
        흔한 경우(사람이 바로 뒤)는 **22초 안에** Peek 으로 끝난다.
        35초까지 가는 것은 앞뒤 화각 어디에도 안 잡힌 경우뿐이다.

## 왜 맹목 회전을 완전히 없애지 않았나

앞뒤 화각을 ±30° 로 가정하면 스캔(각 80°)과 합쳐도 사각이 두 군데(약 40°, 20°) 남는다.
그 구멍에 사람이 서 있으면 못 찾는다. **화각은 아직 실측 전이라 이 계산은 가정 위에
있다** — 실측해서 사각이 없으면 `Turn180`·`Scan3` 을 지우고 이 함수도 같이 줄인다.

이 함수는 트리의 **참조 구현**이다. 한쪽만 고치면 `test_recovery_bt` 의 동등성 검증이
거짓말을 하게 되므로 항상 같이 고친다.
"""


def peek_sec(cfg, role="follow", enabled=True) -> float:
    """LKD 90° peek 에 걸리는 시간. 길잡이는 0(안 돈다), 각도 0 이면 0(끔).

    `recovery_bt.create_searching_tree` 와 **같은 식**을 써야 한다 — 한쪽만 고치면
    아래 동등성 검증이 거짓말을 한다.

    ⚠️ [2026-08-02] `enabled=False` 면 0 이다 — **대상이 화면 가운데에서 사라졌을 때**
       호출자가 끈다. 가운데 소실은 "가려졌다"는 뜻이라 어느 쪽으로 나갔다는 정보가
       없고, 그때 마지막 회전 방향(LKD)으로 90° 를 도는 것은 **근거 없는 추측**이다.
       오히려 사람이 다시 나타났을 때 로봇이 엉뚱한 데를 보고 있게 된다.
       사용자 지시(2026-08-02): "가운데에서 사라지면 peek 가 없어도 될 것 같다."
    """
    if not enabled or role != "follow":
        return 0.0
    angle = getattr(cfg, "SEARCH_PEEK_ANGLE", 0.0)
    return (angle / cfg.ANGULAR_Z_SEARCH) if angle > 0 else 0.0


def search_command(elapsed, cfg, lkd=1.0, role="follow"):
    """경과 초 → (angular_z, 끝났나). `recovery_bt` 타임라인의 참조 구현.

    타임라인:
        LkdPeek    peek      마지막 방향으로 90° (앞캠) — **추종 전용**
        HoldFront  hold      정지 (앞캠)
        HoldBack   hold      정지 (뒷캠) — 캠 전환은 각속도로 안 나타난다
        SweepFront leg       +   (0 → +끝)
                   2*leg     −   (+끝 → −끝)
                   leg       +   (−끝 → 0, 원위치)
        SweepBack  같은 왕복을 뒷캠으로

    Hold 구간은 **정지**다(각속도 0). 카메라만 바뀌는데 그 전환은 `recovery_bt` 쪽
    관심사라 각속도만 보는 여기서는 표현하지 않는다.

    ⚠️ `LkdPeek` 뒤에는 **원위치로 안 돌아온다.** 훑기가 원위치로 복귀하는 것과는
       목적이 다르다 — peek 은 "사람이 나간 쪽을 향한다"는 것이고, 그 방위가 이후
       훑기의 새 기준이 되는 것이 맞다.
    """
    hold = cfg.SEARCH_HOLD_SEC
    leg = (cfg.SEARCH_SWEEP_ANGLE / 2.0) / cfg.ANGULAR_Z_SWEEP
    w = cfg.ANGULAR_Z_SWEEP * lkd
    peek = peek_sec(cfg, role)

    if elapsed < peek:
        return cfg.ANGULAR_Z_SEARCH * lkd, False    # 마지막 방향으로 90°

    t = elapsed - peek - 2 * hold    # peek 과 두 Hold 를 지난 뒤의 경과
    if t < 0:
        return 0.0, False           # HoldFront / HoldBack — 서서 본다

    sweep = 4 * leg                 # 한 번의 왕복+복귀
    if t >= 2 * sweep:
        return 0.0, True            # 앞뒤 훑기까지 끝났다

    u = t % sweep                   # 앞 훑기든 뒷 훑기든 모양이 같다
    if u < leg:
        return w, False             # 0 → +끝
    if u < 3 * leg:
        return -w, False            # +끝 → −끝
    return w, False                 # −끝 → 0 (원위치)
