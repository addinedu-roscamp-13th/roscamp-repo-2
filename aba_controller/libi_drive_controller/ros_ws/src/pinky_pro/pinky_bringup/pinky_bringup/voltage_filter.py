"""전압 표본 필터 — 모터 부하로 주저앉는 순간값을 그대로 내보내지 않는다.

## 왜 필요한가 (실측 2026-07-30, 5초 주기 표본)

    6.78 → 6.35 → 6.55 → 6.69 → 6.76 → 6.64 → 6.78 → 6.64 → 5.88  (V)

같은 배터리에서 0.9V 가 튄다. 원인은 ADC 노이즈가 **아니다** — 모터가 가속하는 순간의
전압 강하다. `pinky_battery.get_voltage()` 가 ADC 를 20번 읽어 평균내지만, 그 20번은
`wait_time=0.001` 이라 **수십 ms 안에** 끝난다. 수백 ms 짜리 부하 강하는 그 평균에
그대로 들어온다.

이 튀는 값이 `battery/percent` 로 나가면 관제 화면의 배터리가 춤추고, 자동 복귀 임계
(15%)를 순간값 하나로 넘길 수 있다.

## 이동평균이 아니라 중앙값(median)인 이유

실측 표본의 저점은 **고립돼 있다** — 6.35V 와 5.88V 가 각각 한 표본씩이고 앞뒤는 정상이다.
연속 저점이 없다. 그런 단일 이상치에는 두 필터가 이렇게 다르다(창 5).

| | 이상치 1개 반영 | 진짜 지속 하락 |
|---|---|---|
이동평균 | 값의 1/5 만큼 끌려간다 (**-0.162V**) | 통과 |
**중앙값** | **완전히 버린다** (-0.050V) | 통과 (표본 절반이 낮아지면 중앙값도 내려간다) |

즉 중앙값은 **이상치만 버리고 진짜 하락은 숨기지 않는다.**

이동평균의 "낮게 틀린다"는 성질은 안전해 보이지만, 실제로는 **이상치 하나가 게이지를
흔드는 것**이다 — 자동 복귀가 무작위로 일찍 튄다. 그건 보수성이 아니라 노이즈다.
그리고 이 값의 목적은 SoC 추정이고 SoC 는 **무부하 전압에 가깝다**(관측 표본의 높은 쪽).
정확도는 `최대값 > 중앙값 > 이동평균`, 보수성은 그 반대다. 중앙값이 균형점이다.

**보수성은 게이지를 왜곡해서 얻지 않는다.** 필요하면 임계에 넣는다 — 예: "15% 이하가
연속 3회일 때만 복귀". 그러면 표시값은 정확하게 두고 판단만 보수적으로 분리된다.

바꾸려면 아래 `median` → `mean` 한 단어다.

## [2026-07-30] 중앙값만으로는 게이지가 계속 떨렸다 — 사표대(deadband)를 더했다

창 5(=25초)로 걸러도 실측이 `6.68 / 6.69 / 6.71 / 6.73 / 6.75` 사이를 오갔다. 전압만 보면
±0.035V 라 작아 보이지만, **퍼센트 눈금이 1.3V 폭**이라(`pinky_battery.battery_percentage`
의 6.5~7.8V) 그 0.07V 가 **5.4%p** 로 증폭된다. 게이지가 흔들린 진짜 이유는 필터가 약해서가
아니라 **눈금이 좁아서**다.

그래서 두 가지를 같이 걸었다:

1. **창을 `2k+1` 로 잡는다** (아래 "창 크기를 정하는 규칙").
2. **사표대**: 중앙값이 `deadband` 만큼 벌어지기 전에는 **직전 출력을 그대로 유지**한다.

⚠️ 사표대는 **직전 출력과 비교**한다(직전 중앙값이 아니다). 그래서 0.01V 씩 천천히
내려가는 진짜 방전도 누적이 `deadband` 를 넘는 순간 따라 내려간다 — **하락을 숨기지 않고
지연시킬 뿐**이다. 직전 중앙값과 비교하면 그때 영구히 못 따라간다(그건 버그다).

`deadband=0.0`(기본)이면 예전과 완전히 같다.

## 창 크기를 정하는 규칙 — `window = 2k + 1`

중앙값의 성질이다: **창이 `2k+1` 이면 길이 `k` 이하의 연속 이상치는 정확히 버려지고,
`k+1` 이상 지속되면 통과한다.** 이상치가 `k` 개면 정상 표본이 `k+1` 개로 과반이라
중앙값이 정상 쪽에 남고, `k+1` 개가 되는 순간 과반이 뒤집히기 때문이다.

그래서 노브는 창 크기가 아니라 **`reject_run` = k = "몇 표본까지 지속되는 강하를 순간으로
볼 것인가"** 다. 창은 거기서 유도한다. 예전에 창을 5, 11 처럼 직접 적었는데 그건 근거 없는
숫자였다 — k 로 적으면 무엇을 버리는지가 값에 그대로 드러난다.

    k=0 → 창 1  (필터 없음)
    k=1 → 창 3
    k=2 → 창 5
    k=3 → 창 7   ← 기본. 모터 가속 강하가 3표본(=15초)까지 이어져도 버린다

이 성질은 아래 `_self_check` 가 **k 를 바꿔가며 실제로 확인한다** — k 개는 버리고
k+1 개는 통과하는지. 규칙을 주석으로만 적어 두면 창을 손댈 때 조용히 깨진다.

## 노브

`reject_run`(k) 는 튜닝 값이다. 5초 주기에서 k=3 이면 창 7 = 35초다.
크게 잡으면 더 매끄럽지만 진짜 하락을 늦게 본다 — 실물에서 맞춘다.
`deadband` 는 "이만큼 안 움직이면 안 움직인 것으로 친다"는 폭이다. 퍼센트로 환산하면
`deadband / 1.3 * 100` %p 다 (0.03V ≈ 2.3%p).
"""
from collections import deque
from statistics import median


class VoltageFilter:
    """표본을 모아 중앙값을 돌려준다. `None`(I2C 실패) 표본은 버린다.

    `reject_run`(k) 개까지의 연속 이상치를 버린다. 창은 `2k+1` 로 유도된다 — 왜 그런지는
    이 파일 머리말의 "창 크기를 정하는 규칙" 참고.
    """

    def __init__(self, reject_run: int = 3, deadband: float = 0.0):
        if reject_run < 0:
            raise ValueError(f"reject_run 은 0 이상이어야 한다: {reject_run}")
        if deadband < 0:
            raise ValueError(f"deadband 는 0 이상이어야 한다: {deadband}")
        self.reject_run = reject_run
        self.window = 2 * reject_run + 1
        self.deadband = float(deadband)
        self._buf: deque[float] = deque(maxlen=self.window)
        self._out: float | None = None

    def push(self, voltage) -> float | None:
        """표본 하나를 넣고 필터값을 돌려준다.

        `None` 은 **표본이 아니다** — 창에 넣지 않는다. I2C 가 한 번 실패했다고 전압이
        0 인 것은 아니므로, 넣으면 없는 강하를 만들어낸다.
        아직 유효 표본이 하나도 없으면 `None`(모른다)을 돌려준다.
        """
        if voltage is not None:
            self._buf.append(float(voltage))
            m = median(self._buf)
            if self._out is None or abs(m - self._out) >= self.deadband:
                self._out = m
        return self._out

    def value(self) -> float | None:
        return self._out

    def __len__(self) -> int:
        return len(self._buf)


def _self_check() -> None:
    """`python3 voltage_filter.py` 로 도는 최소 검증. 프레임워크 없이."""
    f = VoltageFilter(reject_run=2)                   # 창 5
    assert f.window == 5
    assert f.value() is None, "표본 없으면 모른다(None)"
    assert len(f) == 0

    # 실측 표본 그대로 — 마지막 5.88V 한 방에 끌려가지 않아야 한다
    for v in (6.78, 6.35, 6.55, 6.69, 6.76):
        f.push(v)
    before = f.value()
    after = f.push(5.88)
    assert abs(before - 6.69) < 1e-9, before          # 정렬 시 중앙 = 6.69
    assert after >= 6.55, f"순간 강하에 끌려갔다: {after}"

    # 지속적 하락은 통과해야 한다 — 이게 안 되면 필터가 저전압을 숨긴다
    for v in (5.9, 5.9, 5.9, 5.9, 5.9):
        f.push(v)
    assert abs(f.value() - 5.9) < 1e-9, f.value()

    # ── 창 = 2k+1 규칙: k 개는 버리고 k+1 개는 통과한다 ────────────────────
    #
    # 이 파일 머리말이 주장하는 성질을 **k 를 바꿔가며 실제로 확인**한다. 창 크기를
    # 손대면 여기서 깨진다 — 규칙을 주석으로만 두면 조용히 틀린다.
    for k in (1, 2, 3, 4):
        base, dip = 7.0, 5.5

        run_k = VoltageFilter(reject_run=k)
        assert run_k.window == 2 * k + 1, run_k.window
        for _ in range(run_k.window):                 # 창을 정상값으로 채운다
            run_k.push(base)
        for _ in range(k):                            # k 개짜리 순간 강하
            run_k.push(dip)
        assert run_k.value() == base, \
            f"k={k}: 길이 {k} 이하의 강하는 버려야 하는데 {run_k.value()} 가 나왔다"

        run_k1 = VoltageFilter(reject_run=k)
        for _ in range(run_k1.window):
            run_k1.push(base)
        for _ in range(k + 1):                        # k+1 개면 지속으로 본다
            run_k1.push(dip)
        assert run_k1.value() == dip, \
            f"k={k}: 길이 {k + 1} 의 지속 하락을 놓쳤다 ({run_k1.value()})"

    # None(I2C 실패)은 창을 오염시키지 않는다
    g = VoltageFilter(reject_run=1)                   # 창 3
    g.push(7.0)
    assert g.push(None) == 7.0
    assert len(g) == 1, "None 이 표본으로 들어갔다"
    assert VoltageFilter(reject_run=0).push(6.0) == 6.0   # k=0 → 창 1 = 필터 없음

    # ── 사표대 ──────────────────────────────────────────────────────────
    # 실측한 떨림 폭(6.68~6.75)이 사표대 안이면 게이지가 **한 번도 안 움직여야** 한다.
    d = VoltageFilter(reject_run=0, deadband=0.1)   # 창 1 이라 중앙값 = 마지막 표본
    assert d.push(6.70) == 6.70                     # 첫 값은 잡을 기준이 없으니 그대로 통과
    for v in (6.68, 6.75, 6.69, 6.73):
        assert d.push(v) == 6.70, f"사표대 안인데 움직였다: {v} -> {d.value()}"

    # 사표대를 넘으면 따라간다
    assert d.push(6.55) == 6.55

    # ⚠️ 핵심: 사표대보다 **작은 걸음의 지속 하락**도 결국 따라가야 한다.
    #    직전 '중앙값'과 비교하면 여기서 영원히 6.55 에 붙어 저전압을 숨긴다.
    e = VoltageFilter(reject_run=0, deadband=0.1)
    e.push(7.00)
    for v in (6.98, 6.96, 6.94, 6.92):          # 걸음 0.02 < 사표대 0.1 — 아직 붙어 있다
        e.push(v)
    assert e.value() == 7.00, f"사표대 안인데 움직였다: {e.value()}"
    assert e.push(6.85) == 6.85, "누적 하락이 사표대를 넘었는데 안 따라갔다"

    try:
        VoltageFilter(reject_run=-1)
    except ValueError:
        pass
    else:
        raise AssertionError("음수 reject_run 은 거부해야 한다")

    try:
        VoltageFilter(deadband=-0.1)
    except ValueError:
        pass
    else:
        raise AssertionError("음수 deadband 는 거부해야 한다")

    print("voltage_filter self-check OK")


if __name__ == "__main__":
    _self_check()
