"""`0:03-0:33` 같은 구간 문자열 파싱. 벤치와 캘리브가 함께 쓴다.

구간을 사람이 지정하는 이유(캘리브): 자동 판별은 **순환**이다 — 서 있는지 알려면
기준 비율이 필요한데, 기준 비율을 재려고 서 있는 구간을 찾는 중이다. 게다가
기준을 누운 구간에서 재면 투영 단축 항이 죽고 화면 각도만 쓰는 것과 같아진다.
조용히 틀린 채로 돈다.
"""


def _to_sec(s):
    parts = s.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"시각 형식이 아닙니다: {s!r} (예: 33 · 0:33 · 1:02:03)")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"시각 형식이 아닙니다: {s!r} (예: 33 · 0:33 · 1:02:03)") from None
    sec = 0.0
    for v in vals:
        sec = sec * 60.0 + v
    return sec


def parse_segment(text):
    """`"0:03-0:33"` -> `(3.0, 33.0)` 초."""
    if "-" not in text:
        raise ValueError(f"구간은 시작-끝 형식입니다: {text!r} (예: 0:03-0:33)")
    a, b = text.rsplit("-", 1)
    start, end = _to_sec(a), _to_sec(b)
    # `end == start` 는 합법이다 — "그 순간 하나"를 가리킨다(예: "0:32-0:32").
    # 라벨 초안(segments_draft.md)이 실제로 이 형태를 쓴다. `frame_in_segment` 는
    # 양 끝을 포함하는 `start <= t <= end` 라서, 이 구간은 정확히 그 시각에 걸리는
    # 프레임(보통 하나, 없을 수도 있음)만 고른다. 여기서 막을 것은 **역전**뿐이다.
    if end < start:
        raise ValueError(f"끝이 시작보다 앞일 수 없습니다: {text!r}")
    return start, end


def parse_labeled(text):
    """`"front=0:03-0:33"` -> `("front", 3.0, 33.0)`."""
    if "=" not in text:
        raise ValueError(f"label=시작-끝 형식입니다: {text!r} (예: front=0:03-0:33)")
    label, rest = text.split("=", 1)
    start, end = parse_segment(rest)
    return label.strip(), start, end


def frame_in_segment(frame_idx, fps, segment):
    """0-기반 프레임 번호가 구간 안인가. 양 끝 포함."""
    if segment is None:
        return False
    t = frame_idx / float(fps if fps else 15.0)
    return segment[0] <= t <= segment[1]
