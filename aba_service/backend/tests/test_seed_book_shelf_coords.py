"""데모 서가 좌표 배정 — 같은 서가 안에서 칸이 겹치지 않아야 한다.

겹치면 두 책이 같은 칸에 있다고 주장하고, 팔은 그중 하나를 잡을 때 반드시 틀린다.
(실물 배치를 아는 사람은 사서 화면에서 다시 입력한다 — 여기 값은 데모용이다.)
"""

from scripts.seed_books import BOOKS, _assign_shelf_coords


def test_같은_서가에서_층줄이_겹치지_않는다():
    books = [dict(b) for b in BOOKS]
    _assign_shelf_coords(books)

    seen: dict[str, set] = {}
    for b in books:
        spot = (b["tier"], b["row"])
        assert 1 <= b["tier"] <= 3 and 1 <= b["row"] <= 3, b
        assert spot not in seen.setdefault(b["zone"], set()), (b["zone"], spot)
        seen[b["zone"]].add(spot)
