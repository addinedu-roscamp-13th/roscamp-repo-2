"""`/fleet_cmd` 의 팔 인자를 `ArmTask` goal 필드로 옮긴다 — 순수 함수, ROS 없이 테스트된다.

## 왜 유도가 가능한가

관제(FMS)는 2026-07-30 시점에 아직 새 필드를 안 보낸다. 오는 것은 `{action, book, at}` 뿐이고
그중 `at` 은 **주행 정점 이름**(`문학서가`·`1번테이블`·`수거함`)이다.

그런데 **액션 이름 하나로 대상 종류와 두 장소가 결정된다** — 아래 `_MOVES` 가 그것이다.
그래서 FMS 를 고치기 전에도 중계가 goal 을 거의 다 채울 수 있다.

유도할 수 없는 것은 `tier`/`row`/`slot` 뿐이다. 그건 도서 DB 값이라(`books.shelf`) FMS 가
실어 보내야 한다 — 그때까지 `0` 으로 간다. 팔 입장에서 `0` 은 "층·줄 정보 없음"이고,
팔이 이미 시각으로 대상을 찾으므로(PBVS/YOLO) 그 상태로도 동작 자체는 가능하다.

⚠️ **모르는 값을 지어내지 않는다.** 액션 이름이 표에 없거나 놓을 곳을 정점에서 못 읽으면
`None` 을 돌려준다 — 드라이버가 그걸 실패로 처리한다. 추측해서 보내면 팔이 조용히 틀린
곳으로 간다.
"""

SHELF = "서가"
TABLE = "테이블"
DESK = "안내데스크"
BIN = "수거함"
LIBI = "리비바구니"
FLOOR = "바닥"

#: 액션 → (대상, 집는 곳, 놓는 곳).
#
# `place` 의 놓는 곳만 `None` 이다 — 테이블인지 안내데스크인지는 정점(`at`)이 정한다.
# 나머지 넷은 정점과 무관하게 고정이다(바구니 3단 교대는 리비·바닥·수거함 사이에서만 돈다).
_MOVES = {
    "pick":            ("book",   SHELF, LIBI),
    "place":           ("book",   LIBI,  None),
    "unload_to_floor": ("basket", LIBI,  FLOOR),
    "load_from_box":   ("basket", BIN,   LIBI),
    "refill_box":      ("basket", FLOOR, BIN),
}


def place_of(waypoint):
    """주행 정점 이름 → 장소 종류. 모르면 `None`.

    ⚠️ `안네데스크` 는 지도 데이터(`arte2.navgraph.yaml`)의 **실제 표기**다 — 오타지만
    고치면 정점 이름이 바뀌어 주행이 깨진다. 그래서 여기서 둘 다 받아 정규화한다.
    팔에는 올바른 표기(`안내데스크`)만 나간다.
    """
    w = (waypoint or "").strip()
    if not w:
        return None
    if w.endswith("서가"):
        return SHELF
    if w.endswith("테이블"):
        return TABLE
    if w in ("안내데스크", "안네데스크"):
        return DESK
    if w == "수거함":
        return BIN
    return None


#: 팔이 아는 장소 종류 전부. FMS 가 보낸 값을 검증할 때 쓴다.
PLACES = frozenset({SHELF, TABLE, DESK, BIN, LIBI, FLOOR})

#: 좌표 상한 — 서가가 3층 × 3줄, 리비바구니가 3칸이다(실물 하드웨어). `0` 은 "정보 없음".
MAX_COORD = 3


def to_goal_fields(args):
    """`/fleet_cmd` 의 `args` → `ArmTask.Goal` 필드 dict. 못 만들면 `None`.

    ⚠️ **FMS 가 보낸 값이 이긴다.** `object`/`from_place`/`to_place` 가 args 에 있으면
    그대로 쓰고, 없을 때만 액션 이름으로 유도한다.

    이 순서가 중요한 이유: FMS 가 새 필드를 보내기 시작한 뒤에도 유도가 이기면 **정본이
    둘**이 된다. 그러면 FMS 에서 장소를 바꿨는데 로봇이 옛 유도값으로 가는 일이 생기고,
    그건 로그만 봐서는 원인을 못 찾는 종류의 버그다.
    """
    args = args or {}
    action = str(args.get("action", "")).strip()
    move = _MOVES.get(action)
    if move is None:
        return None                      # 팔이 모르는 액션 — 지어내지 않는다

    obj, src, dst = move

    # FMS 가 명시한 값 우선. 모르는 값이면 유도값으로 떨어지지 않고 실패시킨다 —
    # 오타를 조용히 다른 장소로 바꿔 보내면 팔이 엉뚱한 데로 간다.
    given_obj = str(args.get("object", "") or "").strip()
    if given_obj and given_obj != obj:
        # ⚠️ 액션이 대상 종류를 이미 정한다(`_MOVES`). `pick`/`place` 는 도서, 나머지 셋은
        #    바구니다. 어긋난 조합(`pick` + `basket` 등)은 **어느 쪽을 믿을지 알 수 없으므로**
        #    지어내지 않고 실패시킨다 — 위장값 부활이거나 상위 버그라는 신호다.
        return None
    for key, cur in (("from_place", src), ("to_place", dst)):
        given = str(args.get(key, "") or "").strip()
        if not given:
            continue
        if given not in PLACES:
            return None
        if key == "from_place":
            src = given
        else:
            dst = given

    if dst is None:
        dst = place_of(args.get("at"))
        if dst is None:
            return None                  # 놓을 곳을 정점에서 못 읽었다

    coords = {}
    for key in ("tier", "row", "slot"):
        try:
            v = int(args.get(key) or 0)
        except (TypeError, ValueError):
            return None                  # 숫자가 아닌 좌표 — 0 으로 덮으면 조용히 다른 칸이 된다
        if not 0 <= v <= MAX_COORD:
            # ⚠️ 하드웨어가 3층 × 3줄, 바구니 3칸이다. `tier=99` 를 0 으로 깎아 보내면
            #    "정보 없음"이 되어 팔이 시각으로 아무 책이나 집는다. 범위를 벗어난 값은
            #    상위(사서 입력·API)의 버그이므로 goal 을 아예 안 만든다.
            return None
        coords[key] = v

    return {
        "action": action,
        "object": obj,
        "from_place": src,
        "to_place": dst,
        # 바구니는 전체를 드는 것이라 도서명이 없다 — FMS 가 `book:"바구니"` 를 보내도 버린다
        # (대상 종류를 도서명 필드에 위장해 넣은 값이라 팔에 넘기면 혼선만 만든다).
        "book": "" if obj == "basket" else str(args.get("book", "") or ""),
        **coords,
    }


def _self_check() -> None:
    """`python3 arm_task_map.py` 로 도는 최소 검증."""
    g = to_goal_fields({"action": "pick", "book": "코스모스", "at": "과학-인문학서가"})
    assert g["object"] == "book" and g["from_place"] == SHELF and g["to_place"] == LIBI, g
    assert g["book"] == "코스모스" and g["tier"] == 0 and g["slot"] == 0, g

    g = to_goal_fields({"action": "place", "book": "코스모스", "at": "1번테이블"})
    assert (g["from_place"], g["to_place"]) == (LIBI, TABLE), g

    g = to_goal_fields({"action": "place", "book": "코스모스", "at": "안네데스크"})
    assert g["to_place"] == DESK, "지도 오타 표기를 정규화해야 한다"

    # 바구니 3단 교대 — 정점과 무관하게 고정이고, 위장된 book 값은 버린다
    for act, src, dst in (("unload_to_floor", LIBI, FLOOR),
                          ("load_from_box", BIN, LIBI),
                          ("refill_box", FLOOR, BIN)):
        g = to_goal_fields({"action": act, "book": "바구니", "at": "수거함"})
        assert (g["object"], g["from_place"], g["to_place"]) == ("basket", src, dst), g
        assert g["book"] == "", "바구니 작업에 도서명이 남았다"

    # FMS 가 나중에 좌표를 실어 보내면 그대로 통과한다
    g = to_goal_fields({"action": "pick", "book": "x", "at": "문학서가",
                        "tier": 3, "row": 2, "slot": 1})
    assert (g["tier"], g["row"], g["slot"]) == (3, 2, 1), g

    # 모르는 것은 지어내지 않는다
    assert to_goal_fields({"action": "throw", "at": "문학서가"}) is None
    assert to_goal_fields({"action": "place", "at": "미정"}) is None
    assert to_goal_fields({"action": "place", "at": ""}) is None
    assert to_goal_fields({}) is None
    assert to_goal_fields(None) is None

    # ── FMS 가 보낸 값이 유도값을 이긴다 ──────────────────────────────────────
    g = to_goal_fields({"action": "place", "object": "book", "at": "1번테이블",
                        "to_place": DESK, "book": "x"})
    assert g["to_place"] == DESK, "FMS 가 명시한 to_place 를 무시했다"

    g = to_goal_fields({"action": "load_from_box", "object": "basket", "at": "수거함",
                        "from_place": BIN, "to_place": LIBI})
    assert (g["object"], g["from_place"], g["to_place"]) == ("basket", BIN, LIBI), g
    assert g["book"] == "", "object=basket 이면 도서명을 버려야 한다"

    # 모르는 값이 명시되면 유도로 떨어지지 않고 실패한다 (오타를 조용히 고치지 않는다)
    assert to_goal_fields({"action": "pick", "at": "문학서가", "from_place": "창고"}) is None
    assert to_goal_fields({"action": "pick", "at": "문학서가", "object": "사람"}) is None

    # 액션과 대상 종류가 어긋나면 실패한다 — 액션이 종류를 이미 정한다
    assert to_goal_fields({"action": "pick", "at": "문학서가", "object": "basket"}) is None
    assert to_goal_fields({"action": "refill_box", "at": "수거함", "object": "book"}) is None

    # 좌표는 0 또는 1~3 만이다 — 범위를 벗어나면 0 으로 깎지 않고 실패시킨다
    for bad in ({"tier": 4}, {"row": 99}, {"slot": 7}, {"tier": -1}, {"row": "삼"}):
        assert to_goal_fields({"action": "pick", "at": "문학서가", **bad}) is None, bad

    print("arm_task_map self-check OK")


if __name__ == "__main__":
    _self_check()
