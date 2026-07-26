# 고객용 UI 개편 + LiBi bot 기능 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회원용 웹 UI를 "찾기 → 알아보기 → 요청하기 → 확인하기"가 끊기지 않는 흐름으로 다듬고, LiBi bot이 회원 기능을 도구 호출로 대신 실행하게 한다.

**Architecture:** 기존 데이터 패턴(`useEffect` + `fetch`)을 유지하는 최소 변경 방식. 화면 간 중복은 공용 컴포넌트(`BookRow`, `BookDetailSheet`)와 순수 함수(`bookStatus`)로만 줄인다. 백엔드는 세 곳만 손댄다 — 도서 응답에 `unavailable` 노출, 인기 도서 엔드포인트 신설, 요청 이력 삭제 엔드포인트 신설. 도서 상세는 별도 라우트가 아니라 바텀시트라서 단건 조회 API가 필요 없다.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + pytest (backend) / React 19 + TanStack Router/Start + Tailwind v4 + shadcn/ui(vaul drawer, sonner) + Ollama `qwen3:1.7b` tool calling (frontend)

## Global Constraints

- 작업 대상은 `aba_service`뿐이다. `aba_controller`·`aba_fms_service`·`aba_ai_service`는 절대 건드리지 않는다.
- 커밋 메시지에 `Co-Authored-By` 트레일러를 넣지 않는다.
- 머지·리베이스·푸시·브랜치 반영은 사용자가 한다. 작업 브랜치/워크트리 내부의 Task 커밋만 허용된다.
- 브랜치: `feat/customer-ui-revamp` (from `dev` @ 840a359).
- 백엔드 테스트 실행: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/ -q`
- 프론트 게이트(테스트 러너 없음): `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build` — 셋 다 통과해야 완료다.
- 프론트 dev 서버는 `bun --bun run dev`로 띄운다 (Node 18로는 안 된다).
- 백엔드 `/api/books` 목록 상한은 `limit ≤ 200`이다. 이 값을 넘겨 요청하면 422가 나고, 프론트가 그걸 빈 배열로 삼켜 왔다.
- 도서 카테고리는 `literature | art | science | humanities | kids` 다섯 가지뿐이다.
- 지원 언어는 `KR | EN | ZH | VI` 네 가지다. 새 문구는 네 언어 모두 채운다.
- 요청 자리(테이블) 화이트리스트는 `테이블-1번-상/좌/우`, `테이블-2번-하/좌/우` 여섯 개다.
- 대여 요청의 픽업 지점 문자열은 `안네데스크` (원본 오타 그대로여야 waypoint를 찾는다).
- UI 구현 시 `frontend-design` 스킬을 사용한다. 실동작 확인은 `run` 스킬로 앱을 띄워 스크린샷을 남긴다.

---

## File Structure

**백엔드 (변경)**
- `aba_service/backend/app/schemas.py` — `BookOut`에 `unavailable` 필드 추가
- `aba_service/backend/app/routers/books.py` — `_to_out`에 `unavailable` 채우기, `GET /api/books/popular` 추가, `GET /api/books/{book_id}` 추가, 목록에 `zone` 필터 추가
- `aba_service/backend/app/routers/delivery.py` — `DELETE /api/member/requests/{id}` 추가
- `aba_service/backend/app/main.py` — OCR warmup 스레드 제거

**백엔드 (신규 테스트)**
- `aba_service/backend/tests/test_books_catalog.py`
- `aba_service/backend/tests/test_request_delete.py`

**프론트 (신규)**
- `src/lib/book-status.ts` — 도서 가용 상태 판정 순수 함수 (한 가지 책임)
- `src/lib/use-debounced.ts` — 입력 디바운스 훅
- `src/components/BookRow.tsx` — 도서 한 줄 표시. 검색·서제스트·지도·요청·추천이 공유
- `src/components/BookDetailSheet.tsx` — 도서 상세 바텀시트
- `src/lib/libi-tools.ts` — LiBi bot이 쓸 회원 기능 도구 정의 + 실행기
- `src/components/BotConfirmCard.tsx` — 변경형 도구 실행 전 확인/수정 카드

**프론트 (변경)**
- `src/lib/books-api.ts` — `unavailable` 정규화, `fetchPopular` 추가
- `src/lib/member.ts` — `deleteRequest` 추가
- `src/routes/__root.tsx` — sonner `<Toaster/>` 마운트
- `src/routes/search.tsx` · `home.tsx` · `map.tsx` · `recommend.tsx` · `request.tsx` · `me.tsx` · `settings.tsx` · `index.tsx` · `chat.tsx`
- `src/components/LibraryMap.tsx` — 도면 톤 마감
- `src/lib/use-speech.ts` — 죽은 `speak()` 제거

**프론트 (삭제)**
- `src/routes/scan.tsx` · `src/routes/ocr.tsx`

**자산**
- `aba_service/frontend/public/map/arte3.png` — `arte3.pgm`에서 재생성

---

## Wave 편성

| Wave | Task | 병렬 |
|---|---|---|
| 1 | T1(books) · T2(delivery) · T3(main) | 3개 병렬 (파일 무교차) |
| 2 | T4(lib 기반) | 단독 |
| 3 | T5(공용 컴포넌트 + `/request` search 계약 + Toaster) | 단독 |
| 4 | T6 · T7 · T8 · T9 · T10 · T11 · T13 | 7개 병렬 (라우트별 파일 무교차) |
| 5 | T12(라우트 삭제 + 설정 정리) | **단독** — `routeTree.gen.ts` 재생성이 걸려 있어 라우트 편집이 전부 끝난 뒤에 |
| 6 | T14(도구 레이어) → T15(챗봇 연결) | 순차 |
| 7 | T16(문서/노트) | 단독 |

**Wave 4 파일 소유권** (같은 파일을 두 Task 가 건드리지 않는다):

| Task | 소유 파일 |
|---|---|
| T6 | `routes/search.tsx` |
| T7 | `routes/home.tsx`, `routes/index.tsx` |
| T8 | `routes/map.tsx` |
| T9 | `routes/recommend.tsx` |
| T10 | `routes/request.tsx` |
| T11 | `routes/me.tsx`, `lib/member.ts` |
| T13 | `components/LibraryMap.tsx`, `lib/map-waypoints.ts`, `public/map/arte3.png` |

**`routeTree.gen.ts` 는 생성물이다.** Wave 4 의 어떤 Task도 이 파일을 커밋하지 않는다(`git add` 에 넣지 않는다). 라우트 파일이 삭제되는 T12 에서 한 번만 재생성해 커밋한다. 각 Task 는 **파일을 명시해서** `git add` 한다 — `git add -A` 는 옆 worktree 의 작업까지 끌어온다.

---

### Task 1: 도서 API 확장 (unavailable · 인기 · 단건 조회 · zone 필터)

**Files:**
- Modify: `aba_service/backend/app/schemas.py:73-88`
- Modify: `aba_service/backend/app/routers/books.py:37-60`, `books.py:78`(앞에 삽입), `books.py:108-122`
- Test: `aba_service/backend/tests/test_books_catalog.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `BookOut.unavailable: bool` (JSON 키 `unavailable`)
  - `GET /api/books/popular?category=<cat>&limit=<n>` → `list[BookOut]`, 대출 횟수 내림차순
  - `GET /api/books/{book_id}` → `BookOut`, 없으면 404. **딥링크/새로고침 복구용** — 위저드가 `?bookId=` 로 진입했을 때 카탈로그 전체를 받지 않고 한 건만 가져온다
  - `GET /api/books?zone=A&zone=B` → 그 정점들에 꽂힌 도서만. **지도 구역 조회용** — 200권 상한과 무관하게 정확해진다

**경로 순서 주의:** `/{book_id}` 는 반드시 `/popular`·`/recommend` **뒤에** 선언한다. 앞에 두면 `/api/books/popular` 가 `book_id="popular"` 로 잡힌다.

- [ ] **Step 1: 실패하는 테스트 작성**

`aba_service/backend/tests/test_books_catalog.py`:

```python
"""인기 도서(대출 횟수 랭킹)와 도서 응답의 `unavailable` 노출 검증.

랭킹의 근거 데이터는 `cb_loans` 뿐이다. 대출이 한 건도 없어도 화면은 떠야 하므로
빈 목록이 아니라 '순서만 무의미한 목록'이 나와야 한다.
"""

from datetime import datetime, timedelta

from app.models import Loan
from tests.conftest import make_book


def _lend(db_session, member, book, times: int) -> None:
    """같은 책을 `times` 번 대출한 이력을 만든다."""
    for _ in range(times):
        db_session.add(
            Loan(
                member_id=member.id,
                book_id=book.id,
                status="returned",
                due_at=datetime.now() + timedelta(days=7),
            )
        )
    db_session.commit()


def test_popular_orders_by_loan_count(client, db_session, member):
    cold = make_book(db_session, title="아무도 안 빌린 책")
    warm = make_book(db_session, title="가끔 빌리는 책")
    hot = make_book(db_session, title="제일 많이 빌린 책")
    _lend(db_session, member, warm, 2)
    _lend(db_session, member, hot, 5)

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    titles = [b["title"]["KR"] for b in res.json()]
    assert titles.index(hot.title_kr) < titles.index(warm.title_kr)
    assert titles.index(warm.title_kr) < titles.index(cold.title_kr)


def test_popular_respects_limit(client, db_session):
    for i in range(15):
        make_book(db_session, title=f"책{i}")

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    assert len(res.json()) == 10


def test_popular_filters_by_category(client, db_session):
    make_book(db_session, title="문학책")
    kid = make_book(db_session, title="그림책")
    kid.category = "kids"
    db_session.commit()

    res = client.get("/api/books/popular?category=kids&limit=10")

    assert res.status_code == 200
    assert [b["title"]["KR"] for b in res.json()] == ["그림책"]


def test_popular_without_any_loan_still_returns_books(client, db_session):
    make_book(db_session, title="대출 이력 없는 책")

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    assert len(res.json()) == 1


def test_book_response_exposes_unavailable(client, db_session):
    make_book(db_session, title="훼손된 책", unavailable=True)

    res = client.get("/api/books?limit=10")

    assert res.status_code == 200
    assert res.json()[0]["unavailable"] is True


def test_book_response_keeps_existing_fields(client, db_session):
    make_book(db_session, title="정상 책")

    body = client.get("/api/books?limit=10").json()[0]

    assert body["inStock"] is True
    assert body["unavailable"] is False
    assert body["zone"] == "문학-1"


def test_get_single_book(client, db_session):
    row = make_book(db_session, title="한 권만")

    res = client.get(f"/api/books/{row.id}")

    assert res.status_code == 200
    assert res.json()["title"]["KR"] == "한 권만"


def test_get_missing_book_is_404(client, db_session):
    res = client.get("/api/books/99999")

    assert res.status_code == 404


def test_popular_path_is_not_swallowed_by_single_book_route(client, db_session):
    """`/popular` 가 `book_id='popular'` 로 잡히면 안 된다 — 경로 선언 순서 회귀 방지."""
    make_book(db_session, title="아무 책")

    res = client.get("/api/books/popular?limit=5")

    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_list_filters_by_zone(client, db_session):
    make_book(db_session, title="문학책", zone="문학-1")
    make_book(db_session, title="과학책", zone="과학-1")

    res = client.get("/api/books?zone=문학-1&limit=50")

    assert res.status_code == 200
    assert [b["title"]["KR"] for b in res.json()] == ["문학책"]


def test_list_accepts_multiple_zones(client, db_session):
    make_book(db_session, title="문학1", zone="문학-1")
    make_book(db_session, title="문학2", zone="문학-2")
    make_book(db_session, title="과학1", zone="과학-1")

    res = client.get("/api/books?zone=문학-1&zone=문학-2&limit=50")

    assert res.status_code == 200
    assert sorted(b["title"]["KR"] for b in res.json()) == ["문학1", "문학2"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/test_books_catalog.py -q`
Expected: FAIL — `/api/books/popular` 가 404, `unavailable` KeyError, zone 필터가 무시돼 전부 반환.

- [ ] **Step 3: `BookOut`에 필드 추가**

`aba_service/backend/app/schemas.py` 의 `BookOut` 에서 `in_stock` 바로 아래에 추가:

```python
    in_stock: bool = Field(serialization_alias="inStock")
    unavailable: bool = False
```

- [ ] **Step 4: `_to_out`에서 채우기**

`aba_service/backend/app/routers/books.py` 의 `_to_out` 에서 `in_stock` 다음 줄에 추가:

```python
        in_stock=bool(b.in_stock),
        unavailable=bool(b.unavailable),
```

- [ ] **Step 5: 인기 도서 엔드포인트 추가**

`books.py` 상단 import 에 `Loan` 을 더한다:

```python
from ..models import Book, Loan
```

`@router.get("/recommend", ...)` 정의 **바로 위**에 추가한다(경로 매칭 순서상 `""` 보다 앞이면 된다):

```python
@router.get("/popular", response_model=list[BookOut])
def popular(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    """대출 횟수 기준 인기 도서.

    랭킹 근거는 `cb_loans` 뿐이다. 대출 이력이 없는 책도 0회로 함께 나오게
    outer join 한다 — 시드 직후처럼 이력이 비어 있어도 화면이 비지 않아야 한다.
    동점은 (대출가능 우선, 최근 입고 우선)으로 안정적으로 갈라 매번 같은 순서를 준다.
    """
    counts = (
        select(Loan.book_id.label("book_id"), func.count(Loan.id).label("cnt"))
        .group_by(Loan.book_id)
        .subquery()
    )
    stmt = (
        select(Book)
        .outerjoin(counts, counts.c.book_id == Book.id)
        .order_by(
            func.coalesce(counts.c.cnt, 0).desc(),
            Book.in_stock.desc(),
            Book.id.desc(),
        )
        .limit(limit)
    )
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    return [_to_out(b) for b in db.scalars(stmt).all()]
```

- [ ] **Step 5b: 목록에 zone 필터 추가**

`list_books` 시그니처와 본문을 고친다. 지도가 구역의 정점 이름들로 거를 수 있게 **반복 파라미터**를 받는다:

```python
@router.get("", response_model=list[BookOut])
def list_books(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    zone: list[str] | None = Query(default=None, description="서가 정점 이름. 여러 번 줄 수 있다"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search / list catalog books (title, author, summary, tags).

    `zone` 은 지도 화면이 쓴다. 예전에는 전체 목록을 받아 클라이언트에서 걸렀는데,
    상한(200)을 넘는 장서에서는 뒤쪽 책이 통째로 빠진다 — 거르는 일을 DB 에 맡긴다.
    """
    stmt = select(Book)
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    if zone:
        stmt = stmt.where(Book.zone.in_(zone))
    if q and q.strip():
        stmt = _keyword_filter(stmt, q)
    stmt = stmt.order_by(Book.in_stock.desc(), Book.id.desc()).limit(limit)
    return [_to_out(b) for b in db.scalars(stmt).all()]
```

- [ ] **Step 5c: 단건 조회 추가**

`list_books` **뒤에**(파일 끝) 추가한다. 경로 변수 라우트는 고정 경로들보다 뒤에 있어야 `/popular` 를 삼키지 않는다:

```python
@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)):
    """도서 1권.

    상세 시트는 목록이 들고 있는 객체를 그대로 쓰므로 이 엔드포인트가 필요 없다.
    필요한 곳은 **딥링크/새로고침 복구**다 — `/request?bookId=123` 으로 바로 들어오면
    화면에 아무 목록도 없어서 id 로 한 건만 가져와야 한다.
    """
    row = db.get(Book, book_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "도서를 찾을 수 없습니다")
    return _to_out(row)
```

`books.py` 상단 import 에 `HTTPException`, `status` 를 더한다:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/test_books_catalog.py -q`
Expected: PASS (11 passed)

- [ ] **Step 7: 전체 회귀 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/ -q`
Expected: 기존 테스트 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add aba_service/backend/app/schemas.py aba_service/backend/app/routers/books.py aba_service/backend/tests/test_books_catalog.py
git commit -m "feat(books): expose unavailable, add popular/single-book endpoints and zone filter"
```

---

### Task 2: 요청 이력 삭제 엔드포인트

**Files:**
- Modify: `aba_service/backend/app/routers/delivery.py` (파일 끝에 추가)
- Test: `aba_service/backend/tests/test_request_delete.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces: `DELETE /api/member/requests/{request_id}` → 204. 남의 것/없는 것은 404, 승인 대기 중인 것은 409.

**설계 메모:** 일괄 삭제 엔드포인트는 만들지 않는다. 요청 목록은 최대 30건이라 프론트에서 삭제 가능한 항목을 순차 호출하면 충분하다. 접수 기록을 지워도 FMS가 로봇을 제어하므로 진행 중인 배달 자체는 멈추지 않는다 — 409로 막는 이유는 사용자가 진행 알림을 잃지 않게 하기 위해서다.

- [ ] **Step 1: 실패하는 테스트 작성**

`aba_service/backend/tests/test_request_delete.py`:

```python
"""요청 이력 삭제 — 남의 기록은 못 지우고, 승인 대기 중인 건은 못 지운다."""

from app.models import DeliveryRequest, Member
from app.member_security import create_member_token
from app.security import hash_password
from tests.conftest import make_book


def _request_row(db_session, member, book, *, approval="APPROVED", task_id="t-1"):
    row = DeliveryRequest(
        member_id=member.id,
        book_id=book.id,
        kind="read",
        pickup=book.zone,
        dropoff="테이블-1번-상",
        fms_task_id=task_id,
        approval=approval,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_delete_own_finished_request(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book)

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204
    assert db_session.get(DeliveryRequest, row.id) is None


def test_delete_rejected_request(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, approval="REJECTED", task_id="")

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204


def test_cannot_delete_pending_approval(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, approval="PENDING_APPROVAL", task_id="")

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 409
    assert db_session.get(DeliveryRequest, row.id) is not None


def test_cannot_delete_other_members_request(client, db_session, member, member_auth, fms):
    other = Member(
        username="other", full_name="남", hashed_password=hash_password("pw"), is_active=True
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    book = make_book(db_session)
    row = _request_row(db_session, other, book)

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 404
    assert db_session.get(DeliveryRequest, row.id) is not None


def test_delete_missing_request_is_404(client, member_auth, fms):
    res = client.delete("/api/member/requests/99999", headers=member_auth)

    assert res.status_code == 404


def test_delete_requires_auth(client, db_session, member, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book)

    res = client.delete(f"/api/member/requests/{row.id}")

    assert res.status_code == 401
```

- [ ] **Step 2: 실패 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/test_request_delete.py -q`
Expected: FAIL — 405 Method Not Allowed (경로에 DELETE 핸들러 없음)

- [ ] **Step 3: 엔드포인트 구현**

`aba_service/backend/app/routers/delivery.py` 파일 끝에 추가:

```python
@router.delete("s/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_request(
    request_id: int,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """내 요청 이력 1건 삭제.

    라우터 prefix 때문에 실제 경로는 `/api/member/requests/{id}` 다.

    남의 요청은 **존재 여부도 알려주지 않는다**(403 이 아니라 404). 승인 대기 중인
    요청은 사서 승인 큐에 걸려 있으므로 막는다 — 회원이 지워도 사서 화면에는 남아
    양쪽이 어긋난다.
    """
    row = db.get(DeliveryRequest, request_id)
    if row is None or row.member_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요청을 찾을 수 없습니다")
    if row.approval == PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "사서 승인을 기다리는 요청은 삭제할 수 없습니다"
        )
    db.delete(row)
    db.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/test_request_delete.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 전체 회귀 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add aba_service/backend/app/routers/delivery.py aba_service/backend/tests/test_request_delete.py
git commit -m "feat(member): allow deleting finished delivery request history"
```

---

### Task 3: OCR 모델 예열 제거

**Files:**
- Modify: `aba_service/backend/app/main.py:78-83`

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (부팅 동작만 변경)

- [ ] **Step 1: 현재 코드 확인**

Run: `sed -n '70,90p' aba_service/backend/app/main.py`
Expected: `threading.Thread(target=ocr.warmup, name="ocr-warmup", daemon=True).start()` 가 보인다.

- [ ] **Step 2: warmup 호출과 그에만 쓰이는 import 제거**

`main.py` 에서 warmup 스레드를 띄우는 줄과 그 위의 설명 주석을 지우고, 그 자리에 이유를 남긴다:

```python
    # OCR 예열은 하지 않는다 — 회원 UI에서 스캔/OCR 진입점을 걷어내 호출자가 없다.
    # 엔드포인트(`routers/ocr.py`)는 로봇팔·사서 기능이 나중에 쓸 수 있어 남겨 둔다.
```

`threading` import 가 이 용도로만 쓰였다면 함께 제거한다. 다른 곳에서 쓰고 있으면 남긴다.

- [ ] **Step 3: 앱이 뜨는지 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -c "from app.main import app; print(len(app.routes), 'routes')"`
Expected: 예외 없이 라우트 개수 출력

- [ ] **Step 4: 전체 테스트 회귀 확인**

Run: `cd aba_service/backend && env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add aba_service/backend/app/main.py
git commit -m "chore(backend): stop preloading EasyOCR model at startup"
```

---

### Task 4: 프론트 공용 기반 (상태 판정 · 디바운스 · API 클라이언트)

**Files:**
- Create: `aba_service/frontend/src/lib/book-status.ts`
- Create: `aba_service/frontend/src/lib/use-debounced.ts`
- Modify: `aba_service/frontend/src/lib/books-api.ts:89-125` (CatalogBook / fetchCatalog), 파일 끝

**Interfaces:**
- Consumes: `GET /api/books`, `GET /api/books/popular` (Task 1)
- Produces:
  - `type BookAvailability = "available" | "borrowed" | "blocked"`
  - `bookAvailability(book: { inStock: boolean; unavailable?: boolean }): BookAvailability`
  - `AVAILABILITY_LABEL: Record<BookAvailability, string>` — 짧은 라벨
  - `availabilitySentence(a: BookAvailability, zone: string, shelf: string): string` — 상세 시트용 문장
  - `useDebounced<T>(value: T, ms?: number): T`
  - `CatalogBook` 에 `unavailable: boolean` 추가
  - `fetchPopular(params?: { category?: BookCategory | null; limit?: number }): Promise<CatalogBook[]>`
  - `fetchBook(bookId: number): Promise<CatalogBook | null>`
  - `fetchCatalogResult(params): Promise<{ ok: boolean; rows: CatalogBook[] }>` — **실패와 "결과 없음"을 구분한다.** 기존 `fetchCatalog` 은 이 함수를 감싼 얇은 껍데기가 된다
  - `fetchCatalog` 이 `zone?: string[]` 를 받는다

- [ ] **Step 1: 상태 판정 함수 작성**

`src/lib/book-status.ts`:

```typescript
/**
 * 도서 가용 상태 — 화면 어디서나 같은 기준으로 말하기 위한 단일 판정.
 *
 * DB 에는 두 플래그가 따로 있다.
 * - `in_stock`  : 지금 서가에 있는가 (false = 대출 중)
 * - `unavailable`: 훼손·분실로 사서가 대출 불가 처리했는가 (`in_stock` 과 별개)
 *
 * `unavailable` 이 우선이다 — 서가에 꽂혀 있어도 사서가 막아뒀으면 빌릴 수 없다.
 */
export type BookAvailability = "available" | "borrowed" | "blocked";

export function bookAvailability(book: {
  inStock: boolean;
  unavailable?: boolean;
}): BookAvailability {
  if (book.unavailable) return "blocked";
  return book.inStock ? "available" : "borrowed";
}

export const AVAILABILITY_LABEL: Record<BookAvailability, string> = {
  available: "배치중",
  borrowed: "대출 중",
  blocked: "대출 불가",
};

/** 상세 시트에서 쓰는 한 문장 설명. */
export function availabilitySentence(
  a: BookAvailability,
  zone: string,
  shelf: string,
): string {
  switch (a) {
    case "available":
      return `지금 ${zone} 서가 ${shelf}에 배치돼 있어요.`;
    case "borrowed":
      return "현재 대출 중이에요. 예약해 두면 반납될 때 알려드려요.";
    case "blocked":
      return "훼손·분실로 사서가 대출을 막아둔 도서예요.";
  }
}
```

- [ ] **Step 2: 디바운스 훅 작성**

`src/lib/use-debounced.ts`:

```typescript
import { useEffect, useState } from "react";

/**
 * 값이 `ms` 동안 그대로일 때만 흘려보낸다.
 *
 * 검색창은 글자 하나마다 `/api/books` 를 때리고 있었다. 서제스트가 붙으면 그 부담이
 * 홈 화면까지 번지므로, 호출 자체를 늦춘다.
 */
export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}
```

- [ ] **Step 3: `books-api.ts` 확장**

`CatalogBook` 인터페이스에 필드 추가:

```typescript
export interface CatalogBook extends Book {
  /** 요청·예약·위시리스트 API 에 넣을 숫자 id. */
  bookId: number;
  /** 훼손·분실로 사서가 대출 불가 처리했는지 (`inStock` 과 별개). */
  unavailable: boolean;
}
```

`RawBook` 에 필드 추가:

```typescript
interface RawBook extends Omit<Book, "inStock"> {
  in_stock?: boolean;
  inStock?: boolean;
  unavailable?: boolean;
}
```

`fetchCatalog` 을 **실패를 감추지 않는 버전 위에 얹는다.** 기존 시그니처는 그대로 두어 호출처가 안 깨지게 하고, 실패 여부가 필요한 화면(지도)만 새 함수를 쓴다:

```typescript
/** 한 곳에서만 하는 정규화 — 백엔드는 snake, 화면은 camel 을 쓴다. */
function normalize(rows: RawBook[]): CatalogBook[] {
  return rows.map((b) => ({
    ...(b as unknown as Book),
    bookId: Number(b.id),
    inStock: b.in_stock ?? b.inStock ?? false,
    unavailable: b.unavailable ?? false,
  }));
}

export interface CatalogQuery {
  category?: BookCategory | null;
  q?: string | null;
  /** 서가 정점 이름. 지도 구역 조회가 쓴다. */
  zone?: string[] | null;
  limit?: number;
}

/**
 * 카탈로그 조회 — **실패와 "결과 없음"을 구분해서** 돌려준다.
 *
 * 예전에는 어떤 실패든 `[]` 로 뭉개서, 지도 화면이 422 를 받고도 "등록된 책이
 * 없습니다" 를 띄웠다. 사용자에게 보이는 경로는 이 함수를 써야 한다.
 */
export async function fetchCatalogResult(
  params: CatalogQuery = {},
): Promise<{ ok: boolean; rows: CatalogBook[] }> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  for (const z of params.zone ?? []) qs.append("zone", z);
  // 백엔드 상한은 200 이다. 넘기면 422 가 난다.
  qs.set("limit", String(Math.min(params.limit ?? 100, 200)));
  try {
    const res = await fetch(`${API_BASE}/api/books?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return { ok: false, rows: [] };
    return { ok: true, rows: normalize((await res.json()) as RawBook[]) };
  } catch {
    return { ok: false, rows: [] };
  }
}

/** 실패를 빈 목록으로 삼키는 얇은 껍데기. 실패 표시가 필요 없는 화면만 쓴다. */
export async function fetchCatalog(params: CatalogQuery = {}): Promise<CatalogBook[]> {
  return (await fetchCatalogResult(params)).rows;
}
```

기존 `fetchCatalog` 본문은 위 두 함수로 **대체**한다(중복 정의를 남기지 않는다).

도서 1권 조회를 추가한다 — 딥링크/새로고침 복구용:

```typescript
/** 도서 1권. 없거나 실패하면 null. */
export async function fetchBook(bookId: number): Promise<CatalogBook | null> {
  try {
    const res = await fetch(`${API_BASE}/api/books/${bookId}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return null;
    return normalize([(await res.json()) as RawBook])[0] ?? null;
  } catch {
    return null;
  }
}
```

파일 끝에 인기 도서 조회를 추가:

```typescript
/** 대출 횟수 기준 인기 도서. 실패하면 []. */
export async function fetchPopular(
  params: { category?: BookCategory | null; limit?: number } = {},
): Promise<CatalogBook[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  qs.set("limit", String(Math.min(params.limit ?? 10, 50)));
  try {
    const res = await fetch(`${API_BASE}/api/books/popular?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return [];
    return normalize((await res.json()) as RawBook[]);
  } catch {
    return [];
  }
}
```

- [ ] **Step 4: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit`
Expected: 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add aba_service/frontend/src/lib/book-status.ts aba_service/frontend/src/lib/use-debounced.ts aba_service/frontend/src/lib/books-api.ts
git commit -m "feat(frontend): add book availability helper, debounce hook, popular fetch"
```

---

### Task 5: 공용 도서 행 · 상세 바텀시트 · 토스트 마운트

**Files:**
- Create: `aba_service/frontend/src/components/BookRow.tsx`
- Create: `aba_service/frontend/src/components/BookDetailSheet.tsx`
- Modify: `aba_service/frontend/src/routes/__root.tsx`
- Modify: `aba_service/frontend/src/routes/request.tsx` — **search 계약만** 선언(위저드 본체는 T10)

**Interfaces:**
- Consumes: `bookAvailability` · `AVAILABILITY_LABEL` · `availabilitySentence` (Task 4), `CatalogBook` (Task 4), `Drawer*` (`components/ui/drawer.tsx`), `useI18n` (`lib/i18n`)
- Produces:
  - `<BookRow book={CatalogBook} onSelect={(b: CatalogBook) => void} showStatus?: boolean trailing?: ReactNode />`
  - `<BookDetailSheet book={CatalogBook | null} onOpenChange={(open: boolean) => void} onReserve={(b: CatalogBook) => void} />`
  - `/request` 가 `?bookId=<number>` 를 받는다 (T10 이 이 계약 위에 위저드를 얹는다)
  - 전역 `<Toaster />` 마운트 — 이후 어느 화면이든 `toast()` 를 쓸 수 있다

**왜 search 계약이 여기 있나:** 상세 시트가 `navigate({ to: "/request", search: { bookId } })` 로 타입 검사를 받는다. 계약이 T10 에만 있으면 T5 단계에서 `tsc` 가 깨진다 — 계약(3줄)을 먼저 세우고 위저드는 나중에 올린다.

- [ ] **Step 1: 공용 도서 행 작성**

`src/components/BookRow.tsx`:

```tsx
import { MapPin } from "lucide-react";
import type { ReactNode } from "react";

import { AVAILABILITY_LABEL, bookAvailability } from "@/lib/book-status";
import type { CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";

/**
 * 도서 한 줄 — 검색·서제스트·지도 구역·요청 목록·추천이 모두 이걸 쓴다.
 *
 * 목록에서는 상태 뱃지를 **띄우지 않는다**(요구사항). 상태는 눌러서 열리는 상세 시트가
 * 문장으로 설명한다. 다만 요청 화면처럼 "지금 고를 수 있나"가 곧 조작인 화면은
 * `showStatus` 로 켤 수 있다.
 */
export function BookRow({
  book,
  onSelect,
  showStatus = false,
  trailing,
}: {
  book: CatalogBook;
  onSelect?: (book: CatalogBook) => void;
  showStatus?: boolean;
  trailing?: ReactNode;
}) {
  const { lang } = useI18n();
  const availability = bookAvailability(book);

  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3 shadow-card">
      <button
        type="button"
        onClick={() => onSelect?.(book)}
        disabled={!onSelect}
        className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-default"
      >
        <span
          className={`flex size-14 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${book.color} text-3xl`}
        >
          {book.cover}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-foreground">
            {book.title[lang]}
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            {book.author}
          </span>
          <span className="mt-1 flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-primary">
              <MapPin className="size-3" />
              {book.zone} · {book.shelf}
            </span>
            {showStatus && (
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                  availability === "available"
                    ? "bg-emerald-100 text-emerald-700"
                    : availability === "borrowed"
                      ? "bg-stone-200 text-stone-600"
                      : "bg-destructive/10 text-destructive"
                }`}
              >
                {AVAILABILITY_LABEL[availability]}
              </span>
            )}
          </span>
        </span>
      </button>
      {trailing}
    </div>
  );
}

/** 목록을 불러오는 동안 보여줄 자리표시자. */
export function BookRowSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3">
      <div className="size-14 shrink-0 animate-pulse rounded-xl bg-muted" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 상세 바텀시트 작성**

`src/components/BookDetailSheet.tsx`:

```tsx
import { useNavigate } from "@tanstack/react-router";
import { MapPin } from "lucide-react";

import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { availabilitySentence, bookAvailability } from "@/lib/book-status";
import type { CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";

/**
 * 도서 상세 — 별도 라우트가 아니라 바텀시트다.
 *
 * 목록이 이미 들고 있는 도서 객체를 그대로 받는다. `summary`·`for_whom` 이 목록
 * 응답에 이미 들어 있어서 단건 조회 API 가 필요 없다.
 *
 * 요청으로 넘어갈 때 `?bookId=` 를 달아 보내면 요청 위저드가 1단계를 채운 채
 * 2단계에서 시작한다 — 방금 고른 책을 또 고르게 하지 않는다.
 */
export function BookDetailSheet({
  book,
  onOpenChange,
  onReserve,
}: {
  book: CatalogBook | null;
  onOpenChange: (open: boolean) => void;
  /** 대출 중인 책의 예약. 주지 않으면 예약 버튼 대신 안내만 뜬다. */
  onReserve?: (book: CatalogBook) => void;
}) {
  const { lang } = useI18n();
  const navigate = useNavigate();
  if (!book) return null;

  const availability = bookAvailability(book);
  const tags = book.forWhom?.[lang] ?? [];

  return (
    <Drawer open={book !== null} onOpenChange={onOpenChange}>
      <DrawerContent className="max-h-[85vh]">
        <DrawerHeader className="text-left">
          <div className="flex items-start gap-3">
            <span
              className={`flex size-20 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br ${book.color} text-4xl`}
            >
              {book.cover}
            </span>
            <div className="min-w-0 flex-1">
              <DrawerTitle className="text-base leading-snug">
                {book.title[lang]}
              </DrawerTitle>
              <DrawerDescription className="mt-0.5">
                {book.author}
              </DrawerDescription>
              <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                <MapPin className="size-3" />
                {book.zone} · {book.shelf}
              </span>
            </div>
          </div>
        </DrawerHeader>

        <div className="overflow-y-auto px-4 pb-6">
          <p
            className={`rounded-xl px-3 py-2 text-xs font-semibold ${
              availability === "available"
                ? "bg-emerald-500/10 text-emerald-700"
                : availability === "borrowed"
                  ? "bg-muted text-muted-foreground"
                  : "bg-destructive/10 text-destructive"
            }`}
          >
            {availabilitySentence(availability, book.zone, book.shelf)}
          </p>

          {book.summary?.[lang] ? (
            <p className="mt-4 text-sm leading-relaxed text-foreground">
              {book.summary[lang]}
            </p>
          ) : null}

          {tags.length > 0 ? (
            <div className="mt-4">
              <p className="text-[11px] font-bold uppercase tracking-wide text-primary">
                이런 분께
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {tags.map((k) => (
                  <span
                    key={k}
                    className="rounded-full bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-accent-foreground"
                  >
                    {k}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {/* 상태마다 갈 곳이 다르다.
              - 배치중  : 요청 위저드로 (책이 이미 골라진 채)
              - 대출 중 : **요청으로 보내지 않는다.** 로봇이 없는 책을 찾으러 가면 안 되므로
                          여기서 바로 예약한다.
              - 대출 불가: 아무 데도 못 간다. 이유만 위에 이미 적혀 있다. */}
          {availability === "available" ? (
            <button
              onClick={() => {
                onOpenChange(false);
                void navigate({
                  to: "/request",
                  search: { bookId: book.bookId },
                });
              }}
              className="mt-6 h-12 w-full rounded-2xl bg-primary text-sm font-bold text-primary-foreground"
            >
              이 책 요청하기 →
            </button>
          ) : availability === "borrowed" && onReserve ? (
            <button
              onClick={() => {
                onReserve(book);
                onOpenChange(false);
              }}
              className="mt-6 h-12 w-full rounded-2xl bg-secondary text-sm font-bold text-secondary-foreground"
            >
              예약하기
            </button>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
```

- [ ] **Step 2b: 예약 호출 헬퍼**

시트를 여는 화면마다 예약 처리를 베끼지 않도록, 같은 파일에 작은 헬퍼를 둔다:

```tsx
/** 시트에서 바로 예약한다. 결과는 토스트로 알린다. */
export async function reserveFromSheet(book: CatalogBook): Promise<void> {
  try {
    await memberApi.reserve(book.bookId);
    toast.success("예약했습니다. 반납되면 알려드릴게요");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "예약하지 못했습니다");
  }
}
```

`import { memberApi } from "@/lib/member";` 와 `import { toast } from "sonner";` 를 더한다. 시트를 쓰는 화면은 `onReserve={(b) => void reserveFromSheet(b)}` 로 넘긴다.

- [ ] **Step 2c: `/request` search 계약 선언**

`src/routes/request.tsx` 의 라우트 정의에만 손댄다. **위저드 본체는 T10 에서 만든다.**

```tsx
import { z } from "zod";

// 쿼리스트링은 문자열로 들어온다 — `z.number()` 는 통과하지 못한다.
const requestSearchSchema = z.object({
  bookId: z.coerce.number().int().positive().optional(),
});

export const Route = createFileRoute("/request")({
  validateSearch: requestSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — 도서 요청" }] }),
  component: RequestPage,
});
```

- [ ] **Step 3: 토스트 마운트**

`src/routes/__root.tsx` 에 sonner Toaster 를 추가한다. import 를 더하고:

```tsx
import { Toaster } from "@/components/ui/sonner";
```

루트가 렌더하는 최상위 요소 안, 기존 children/Outlet 바로 뒤에 넣는다:

```tsx
<Toaster position="top-center" richColors />
```

- [ ] **Step 4: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit`
Expected: 오류 없음. Step 2c 에서 search 계약을 먼저 세웠으므로 시트의 `navigate` 가 타입 검사를 통과한다.

- [ ] **Step 5: 커밋**

```bash
git add aba_service/frontend/src/components/BookRow.tsx aba_service/frontend/src/components/BookDetailSheet.tsx aba_service/frontend/src/routes/__root.tsx aba_service/frontend/src/routes/request.tsx
git commit -m "feat(frontend): add shared book row, detail sheet with reserve, request search contract"
```

---

### Task 6: 검색 화면 개편

**Files:**
- Modify: `aba_service/frontend/src/routes/search.tsx` (전면)

**Interfaces:**
- Consumes: `BookRow`·`BookRowSkeleton`·`BookDetailSheet` (Task 5), `useDebounced` (Task 4), `fetchCatalog` (Task 4)
- Produces: 없음

- [ ] **Step 1: 화면 구조를 다음 순서로 바꾼다**

1. 검색 입력 + 음성 버튼 (지금 그대로 유지)
2. **점선 CTA 2장** (「도서 요청하기」·「LiBi에게 물어보기」) — 지금은 맨 아래에 있다. 검색바 바로 아래로 올린다
3. 카테고리 칩 — `all | literature | art | science | humanities | kids`, `/recommend` 와 같은 모양
4. 결과 목록 — `BookRow` 사용, 뱃지 없음, 최대 높이 안에서 스크롤
5. 클릭 시 `BookDetailSheet`

- [ ] **Step 2: 상태와 데이터 로딩 교체**

```tsx
const [query, setQuery] = useState(q ?? "");
const [cat, setCat] = useState<Cat>("all");
const [results, setResults] = useState<CatalogBook[]>([]);
const [loading, setLoading] = useState(false);
const [picked, setPicked] = useState<CatalogBook | null>(null);
const debounced = useDebounced(query, 250);

useEffect(() => {
  let cancelled = false;
  setLoading(true);
  void fetchCatalog({
    q: debounced.trim() || null,
    category: cat === "all" ? null : cat,
    limit: 100,
  })
    .then((rows) => {
      if (!cancelled) setResults(rows);
    })
    .finally(() => {
      if (!cancelled) setLoading(false);
    });
  return () => {
    cancelled = true;
  };
}, [debounced, cat]);
```

- [ ] **Step 3: 결과 목록을 10권 + 스크롤로 바꾼다**

```tsx
{/* 10권까지는 그대로 보이고, 넘치면 이 상자 안에서만 스크롤된다.
    페이지 전체가 결과로 길어지면 아래 안내가 화면 밖으로 밀려난다. */}
<div className="mt-4 max-h-[62vh] space-y-2 overflow-y-auto pr-1">
  {loading ? (
    <>
      <BookRowSkeleton />
      <BookRowSkeleton />
      <BookRowSkeleton />
    </>
  ) : results.length === 0 ? (
    <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
      검색 결과가 없습니다.
    </p>
  ) : (
    results.map((b) => (
      <BookRow key={b.id} book={b} onSelect={setPicked} />
    ))
  )}
</div>
```

- [ ] **Step 4: 상세 시트 연결**

컴포넌트 반환부 끝(AppShell 안)에 추가:

```tsx
<BookDetailSheet
  book={picked}
  onOpenChange={(open) => !open && setPicked(null)}
  onReserve={(b) => void reserveFromSheet(b)}
/>
```

- [ ] **Step 5: 죽은 코드 제거**

파일 하단의 `function MiniMap({ zoneId }: { zoneId: string })` 전체를 지운다. 호출처가 한 곳도 없다.

- [ ] **Step 6: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 7: 커밋**

```bash
git add aba_service/frontend/src/routes/search.tsx
git commit -m "feat(search): move CTAs up, add category chips, debounce, detail sheet"
```

---

### Task 7: 홈 검색 서제스트 + 온보딩 음성 시작

**Files:**
- Modify: `aba_service/frontend/src/routes/home.tsx:89-106` 및 주변
- Modify: `aba_service/frontend/src/routes/index.tsx:21-31`

**Interfaces:**
- Consumes: `BookRow`·`BookDetailSheet`·`reserveFromSheet` (Task 5), `useDebounced`·`fetchCatalog` (Task 4)
- Produces: `/home` 이 `?listen=true` 를 받는다

**왜 온보딩이 여기 붙나:** 온보딩의 「음성으로 시작」을 실제로 동작하게 하려면 `home.tsx` 에 search 스키마와 자동 시작 효과를 넣어야 한다. `home.tsx` 를 두 Task 가 나눠 고치면 같은 wave 에서 충돌한다 — 이 파일의 주인이 한 Task 여야 한다.

- [ ] **Step 1: 서제스트 상태 추가**

```tsx
const [suggest, setSuggest] = useState<CatalogBook[]>([]);
const [picked, setPicked] = useState<CatalogBook | null>(null);
const debounced = useDebounced(query, 250);

useEffect(() => {
  const term = debounced.trim();
  if (!term) {
    setSuggest([]);
    return;
  }
  let cancelled = false;
  void fetchCatalog({ q: term, limit: 30 }).then((rows) => {
    if (!cancelled) setSuggest(rows);
  });
  return () => {
    cancelled = true;
  };
}, [debounced]);
```

- [ ] **Step 2: 검색폼 아래에 드롭다운 렌더**

기존 `<form>` 을 감싸는 요소를 `relative` 로 두고, 폼 바로 뒤에 추가한다:

```tsx
{suggest.length > 0 && (
  /* 10권까지 보이고 넘치면 목록 안에서 스크롤된다 — 아래 퀵메뉴가 밀리지 않게. */
  <div className="mt-2 max-h-[52vh] space-y-2 overflow-y-auto rounded-2xl border border-border bg-card p-2 shadow-card">
    {suggest.map((b) => (
      <BookRow key={b.id} book={b} onSelect={setPicked} />
    ))}
  </div>
)}
```

- [ ] **Step 3: 상세 시트 연결**

```tsx
<BookDetailSheet
  book={picked}
  onOpenChange={(open) => !open && setPicked(null)}
  onReserve={(b) => void reserveFromSheet(b)}
/>
```

- [ ] **Step 4: 기존 음성 동작은 그대로 둔다**

`useSpeechRecognition` 을 쓰는 히어로 마이크와, 인식이 끝나면 `/search` 로 보내는 `useEffect` 는 손대지 않는다.

- [ ] **Step 5: 온보딩에서 넘어오면 바로 듣기 시작**

`home.tsx` 에 search 스키마를 더한다:

```tsx
import { z } from "zod";

const homeSearchSchema = z.object({
  // 쿼리스트링은 문자열이라 coerce 가 필요하다.
  listen: z.coerce.boolean().optional(),
});

export const Route = createFileRoute("/home")({
  validateSearch: homeSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — 홈" }] }),
  component: Home,
});
```

컴포넌트에서 최초 진입 때 한 번만 시작한다:

```tsx
const { listen } = Route.useSearch();
useEffect(() => {
  if (listen && supported) start();
  // 최초 진입에서 한 번만 — 이후에는 사용자가 직접 켜고 끈다.
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);
```

- [ ] **Step 6: 온보딩 버튼이 거짓말을 하지 않게 고친다**

`index.tsx` 의 `goHome` 은 마이크 권한만 받고 넘어간다. 홈에 의사를 전달한다:

```tsx
const goHome = async (startListening: boolean) => {
  if (startListening && typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia) {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
    } catch {
      /* 권한을 거부해도 텍스트로는 계속 쓸 수 있어야 한다 */
    }
  }
  // 버튼 이름이 「음성으로 시작」인데 권한만 받고 끝나면 거짓말이다.
  navigate({ to: "/home", search: startListening ? { listen: true } : {} });
};
```

- [ ] **Step 7: 실동작 확인 (스크린샷)**

Run: 앱 `/` 에서 「음성으로 시작」 클릭
Expected: 홈으로 넘어가면서 즉시 듣기가 시작된다(마이크 버튼이 듣는 상태). 스크린샷을 남긴다.

- [ ] **Step 8: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 9: 커밋**

```bash
git add aba_service/frontend/src/routes/home.tsx aba_service/frontend/src/routes/index.tsx
git commit -m "feat(home): search suggestions and real voice start from onboarding"
```

---

### Task 8: 지도 화면 — 구역 도서 표시 버그 수정

**Files:**
- Modify: `aba_service/frontend/src/routes/map.tsx:29-46`, `map.tsx:84-112`

**Interfaces:**
- Consumes: `BookRow`·`BookDetailSheet` (Task 5), `fetchCatalog` (Task 4)
- Produces: 없음

**근본 원인 (두 겹):**
1. `fetchCatalog({ limit: 300 })` 이 백엔드 상한 200 을 넘겨 422 를 받았다.
2. `books-api.ts` 가 **어떤 실패든 `[]` 로 뭉갰다.** 그래서 오류가 "등록된 책이 없습니다" 로 보였다.

1번만 고치면 장서가 200 권을 넘는 순간 다시 조용히 빈다 — 앞의 200 권만 받아 클라이언트에서 걸렀기 때문이다. **거르는 일을 서버로 옮기고**(Task 1 의 `zone` 필터), **실패를 실패로 표시한다**(Task 4 의 `fetchCatalogResult`).

- [ ] **Step 1: 로딩 로직 수정**

```tsx
useEffect(() => {
  if (!zone) {
    setBooks([]);
    return;
  }
  let cancelled = false;
  setLoading(true);
  setFailed(false);
  // 구역에 속한 정점 이름으로 **서버가** 거른다. 예전처럼 전체를 받아
  // 클라이언트에서 거르면 장서가 상한(200)을 넘는 순간 뒤쪽 책이 통째로 빠진다.
  void fetchCatalogResult({ zone: zone.members, limit: 200 })
    .then(({ ok, rows }) => {
      if (cancelled) return;
      setFailed(!ok);          // 빈 결과와 실패는 다른 것이다
      setBooks(rows);
    })
    .finally(() => !cancelled && setLoading(false));
  return () => {
    cancelled = true;
  };
}, [zone]);
```

`const [failed, setFailed] = useState(false);` 를 상태에 추가하고, import 를 `fetchCatalogResult` 로 바꾼다.

- [ ] **Step 2: 목록을 공용 행으로 교체하고 실패를 구분해 알린다**

```tsx
{loading ? (
  <div className="mt-2 space-y-2">
    <BookRowSkeleton />
    <BookRowSkeleton />
  </div>
) : failed ? (
  <p className="mt-2 rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
    도서 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
  </p>
) : books.length === 0 ? (
  <p className="mt-2 rounded-xl bg-muted px-3 py-2 text-xs text-muted-foreground">
    등록된 책이 없습니다
  </p>
) : (
  <div className="mt-2 space-y-2">
    {books.map((b) => (
      <BookRow key={b.id} book={b} onSelect={setPicked} />
    ))}
  </div>
)}
```

- [ ] **Step 3: 상세 시트 연결**

`const [picked, setPicked] = useState<CatalogBook | null>(null);` 를 추가하고, 반환부 끝에:

```tsx
<BookDetailSheet
  book={picked}
  onOpenChange={(open) => !open && setPicked(null)}
  onReserve={(b) => void reserveFromSheet(b)}
/>
```

- [ ] **Step 4: 실동작 확인 (스크린샷)**

Run: 앱을 띄우고 `/map` 에서 「문학」 구역을 탭한다.
Expected: 그 서가의 도서 목록이 실제로 나온다. 스크린샷을 남긴다.

이어서 실패 경로도 확인한다: 백엔드를 잠깐 내리고 같은 구역을 탭한다.
Expected: "등록된 책이 없습니다" 가 아니라 **"불러오지 못했습니다"** 가 뜬다.

- [ ] **Step 5: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 6: 커밋**

```bash
git add aba_service/frontend/src/routes/map.tsx
git commit -m "fix(map): request within backend limit so zone books actually load"
```

---

### Task 9: 추천 화면 — 대출 횟수 Top10

**Files:**
- Modify: `aba_service/frontend/src/routes/recommend.tsx` (전면)

**Interfaces:**
- Consumes: `fetchPopular` (Task 4), `BookRow`·`BookDetailSheet` (Task 5)
- Produces: 없음

- [ ] **Step 1: 데이터 소스를 인기 도서로 교체**

`fetchBooks` / `BOOKS` mock 대신:

```tsx
const [books, setBooks] = useState<CatalogBook[]>([]);
const [loading, setLoading] = useState(true);
const [picked, setPicked] = useState<CatalogBook | null>(null);

useEffect(() => {
  let alive = true;
  setLoading(true);
  void fetchPopular({ category: cat === "all" ? null : cat, limit: 10 })
    .then((rows) => alive && setBooks(rows))
    .finally(() => alive && setLoading(false));
  return () => {
    alive = false;
  };
}, [cat]);
```

`import { BOOKS, type Book } from "@/lib/mock-data";` 와 `fetchBooks` import 를 제거한다.

- [ ] **Step 2: 제목 문구 고정**

```tsx
<h1 className="text-balance text-xl font-bold leading-snug text-foreground">
  🔥 지금 도서관에서 가장 핫한 책
</h1>
<p className="mt-1 text-xs text-muted-foreground">대출 횟수 기준 Top 10</p>
```

기존 `tr("hotTitle")` 사용은 없앤다(문구가 요구사항으로 고정됐다).

- [ ] **Step 3: 목록을 순위 + 공용 행으로**

아코디언(펼치면 요약이 나오는 구조)을 없애고, 순위 뱃지 + `BookRow` + 클릭 시 상세 시트로 바꾼다. 설명은 상세 시트가 이미 보여주므로 여기서 중복 표시하지 않는다.

```tsx
<ol className="mt-5 space-y-2">
  {loading ? (
    <>
      <BookRowSkeleton />
      <BookRowSkeleton />
      <BookRowSkeleton />
    </>
  ) : books.length === 0 ? (
    <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
      아직 대출 기록이 없어요.
    </p>
  ) : (
    books.map((b, i) => (
      <li key={b.id} className="flex items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-accent text-sm font-black text-accent-foreground">
          {i + 1}
        </span>
        <div className="min-w-0 flex-1">
          <BookRow book={b} onSelect={setPicked} />
        </div>
      </li>
    ))
  )}
</ol>
```

- [ ] **Step 4: 상세 시트 연결**

```tsx
<BookDetailSheet
  book={picked}
  onOpenChange={(open) => !open && setPicked(null)}
  onReserve={(b) => void reserveFromSheet(b)}
/>
```

- [ ] **Step 5: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 6: 커밋**

```bash
git add aba_service/frontend/src/routes/recommend.tsx
git commit -m "feat(recommend): rank by loan count and cap every tab at top 10"
```

---

### Task 10: 요청 화면 3단계 위저드

**Files:**
- Modify: `aba_service/frontend/src/routes/request.tsx` (전면)

**Interfaces:**
- Consumes: `BookRow` (Task 5), `useDebounced`·`fetchCatalog`·`fetchBook` (Task 4), `memberApi`·`TABLES` (`lib/member`), `/request` search 계약 (Task 5 Step 2c)
- Produces: 3단계 위저드. `?bookId=` 로 들어오면 2단계에서 시작한다 — LiBi 도구(Task 14)도 이 경로를 쓴다.

- [ ] **Step 1: search 계약 확인**

Task 5 Step 2c 에서 이미 선언돼 있어야 한다:

```tsx
const requestSearchSchema = z.object({
  bookId: z.coerce.number().int().positive().optional(),
});
```

없으면 여기서 추가한다. **`z.number()` 를 쓰면 안 된다** — 쿼리스트링은 문자열로 들어와 파싱에 실패하고, 딥링크가 조용히 무시된다.

- [ ] **Step 2: 단계 상태 도입**

```tsx
type Step = 1 | 2 | 3;

const { bookId } = Route.useSearch();
const [step, setStep] = useState<Step>(1);
const [picked, setPicked] = useState<CatalogBook | null>(null);
const [lastRequest, setLastRequest] = useState<DeliveryRequestOut | null>(null);

// 상세 시트나 LiBi 가 `?bookId=` 를 달고 보내면 1단계는 이미 끝난 셈이다.
// 한 건만 조회한다 — 예전 계획처럼 카탈로그 200 권을 받아 뒤지면 장서가 늘 때 못 찾는다.
useEffect(() => {
  if (!bookId) return;
  let cancelled = false;
  void fetchBook(bookId).then((hit) => {
    if (cancelled) return;
    if (!hit) {
      toast.error("그 책을 찾지 못했어요. 다시 골라주세요");
      return;                       // 1단계에 머문다
    }
    if (hit.unavailable) {
      // 훼손·분실은 요청도 예약도 안 된다. 고르게 두면 제출에서 막혀 헛수고다.
      setPicked(null);
      toast.error("훼손·분실로 대출이 막힌 도서예요");
      return;
    }
    if (!hit.inStock) {
      // 대출 중인 책이 배달 요청으로 새면 로봇이 없는 책을 찾으러 간다.
      // 1단계에 남겨 예약 버튼을 쓰게 한다.
      setPicked(null);
      setQuery(hit.title.KR);
      toast.info("대출 중인 도서예요. 예약으로 신청해 주세요");
      return;
    }
    setPicked(hit);
    setStep(2);
  });
  return () => {
    cancelled = true;
  };
}, [bookId]);
```

- [ ] **Step 3: 1단계 — 책 고르기**

기존 검색 + 목록을 유지하되 `BookRow` 로 교체하고, 목록 검색어에 `useDebounced` 를 건다. 각 행의 `trailing` 에 선택/예약 버튼을 넣는다:

```tsx
<BookRow
  key={b.id}
  book={b}
  showStatus
  trailing={
    b.inStock && !b.unavailable ? (
      <button
        onClick={() => {
          setPicked(b);
          setStep(2);
        }}
        className="shrink-0 rounded-full bg-secondary px-3 py-1.5 text-xs font-bold text-secondary-foreground"
      >
        선택
      </button>
    ) : b.unavailable ? (
      <span className="shrink-0 rounded-full bg-muted px-3 py-1.5 text-[11px] font-bold text-muted-foreground">
        대출 불가
      </span>
    ) : (
      <button
        onClick={() => void reserve(b)}
        className="shrink-0 rounded-full bg-muted px-3 py-1.5 text-xs font-bold text-muted-foreground"
      >
        예약
      </button>
    )
  }
/>
```

- [ ] **Step 4: 하단 고정 네비게이션 바 추가**

요구사항(R22)이 **좌측 하단**이라고 못박았으므로, 「다음」이 왼쪽에 오고 단계 표시가 오른쪽으로 간다.

```tsx
{/* 「다음」은 좌측 하단이다(요구사항). spacer 를 버튼 **뒤에** 둬야 왼쪽에 붙는다. */}
<div className="sticky bottom-0 -mx-5 mt-6 flex items-center gap-2 border-t border-border bg-card px-5 py-3">
  {step === 1 && (
    <button
      disabled={!picked}
      onClick={() => setStep(2)}
      className="rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-primary-foreground disabled:opacity-40"
    >
      다음 →
    </button>
  )}
  {/* 3단계는 접수가 끝난 종착점이다 — 뒤로 가면 같은 요청을 또 낼 수 있으므로
      「이전」을 두지 않고 「새 요청」만 준다. */}
  {step === 2 && (
    <button
      onClick={() => setStep(1)}
      className="rounded-xl border border-border px-4 py-2.5 text-sm font-semibold text-muted-foreground"
    >
      ← 이전
    </button>
  )}
  {step === 3 && (
    <button
      onClick={() => {
        setPicked(null);
        setLastRequest(null);
        setStep(1);
        void navigate({ to: "/request", search: {} });  // ?bookId= 를 지운다
      }}
      className="rounded-xl border border-border px-4 py-2.5 text-sm font-semibold text-muted-foreground"
    >
      새 요청 하기
    </button>
  )}
  <div className="flex-1" />
  <span className="text-xs text-muted-foreground">{step} / 3</span>
</div>
```

- [ ] **Step 5: 2단계 — 수령 방법**

기존 `ModeCard` 두 장과 테이블 선택 그리드를 그대로 쓴다. 제출 버튼을 「요청 완료」로 바꾸고, 성공하면 3단계로 넘어간다:

```tsx
const submit = async () => {
  if (!picked) return;
  setBusy(true);
  try {
    const res =
      mode === "read"
        ? await memberApi.requestRead(picked.bookId, table)
        : await memberApi.requestBorrow(picked.bookId);
    toast.success(
      mode === "read"
        ? `«${res.book_title}» 을(를) ${res.dropoff} 로 가져다 드릴게요`
        : `«${res.book_title}» 대여를 신청했습니다. 사서 승인 후 안내데스크로 가져다 놓을게요`,
    );
    setLastRequest(res);
    // 같은 책으로 두 번 제출되지 않게 선택을 비운다. 3단계는 종착점이다.
    setPicked(null);
    await loadMine();
    setStep(3);
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "요청하지 못했습니다");
  } finally {
    setBusy(false);
  }
};
```

`import { toast } from "sonner";` 를 추가하고, 기존 `notice`/`error` 상태와 그 렌더링 블록은 제거한다. 제출 버튼은 `disabled={busy || !picked}` 로 둔다 — 연타로 두 건이 들어가지 않게.

- [ ] **Step 6: 3단계 — 접수 결과 + 내 요청**

기존 「내 요청」 섹션을 3단계 전용 화면으로 옮긴다. 맨 위에 방금 접수된 건을 요약해 보여준다(접수번호·자리·상태):

```tsx
{lastRequest && (
  <div className="rounded-2xl border-2 border-primary/40 bg-primary-soft/40 p-4">
    <p className="text-sm font-bold text-foreground">접수됐어요</p>
    <p className="mt-1 text-xs text-muted-foreground">
      #{lastRequest.id} · «{lastRequest.book_title}» ·{" "}
      {lastRequest.kind === "borrow" ? "대여" : "열람"} · {lastRequest.dropoff} ·{" "}
      {APPROVAL_LABEL[lastRequest.approval]}
    </p>
  </div>
)}
```

그 아래에 내 요청 목록과 「내 정보에서 전체 보기」(→ `/me`) 링크를 둔다. **3단계에는 제출 버튼이 없다.**

- [ ] **Step 7: 실동작 확인 (스크린샷)**

네 경로를 다 확인한다:
1. `/search` → **배치중** 책 클릭 → 「이 책 요청하기」 → `/request` 2단계, 책 선택됨
2. `/search` → **대출 중** 책 클릭 → 시트에 「예약하기」가 뜨고, 요청 화면으로 안 넘어감
3. 브라우저 주소창에 `/request?bookId=<배치중 책 id>` 직접 입력 → 2단계로 진입(새로고침 복구)
4. 2단계에서 제출 → 3단계 접수 결과 → **「이전」 버튼이 없고** 「새 요청 하기」만 있음

Expected: 각각 스크린샷을 남긴다. 특히 3에서 `z.coerce` 가 없으면 1단계에 머무르므로 회귀 확인 지점이다.

- [ ] **Step 8: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 9: 커밋**

```bash
git add aba_service/frontend/src/routes/request.tsx
git commit -m "feat(request): turn into a 3-step wizard with preselected book support"
```

---

### Task 11: 내 정보 요약 대시보드 + 이력 삭제

**Files:**
- Modify: `aba_service/frontend/src/lib/member.ts` (memberApi 에 메서드 추가)
- Modify: `aba_service/frontend/src/routes/me.tsx` (전면)

**Interfaces:**
- Consumes: `DELETE /api/member/requests/{id}` (Task 2)
- Produces: `memberApi.deleteRequest(id: number): Promise<void>`

- [ ] **Step 1: API 클라이언트에 삭제 추가**

`src/lib/member.ts` 의 `memberApi` 객체 안, `requests` 아래에 추가:

```typescript
  /** 요청 이력 1건 삭제. 승인 대기 중인 건은 409 가 온다. */
  deleteRequest: (id: number) =>
    call<void>(`/api/member/requests/${id}`, { method: "DELETE" }),
```

- [ ] **Step 2: 요약 타일 렌더**

탭 버튼 묶음을 지우고 그 자리에 카운터 타일 네 개를 넣는다:

```tsx
<div className="mt-4 grid grid-cols-4 gap-2">
  {[
    { label: "대출", n: loans.filter((l) => l.status !== "returned").length },
    { label: "요청", n: requests.length },
    { label: "예약", n: reservations.length },
    { label: "읽고싶은", n: wishlist.length },
  ].map((s) => (
    <div
      key={s.label}
      className="rounded-2xl border border-border bg-card p-3 text-center shadow-card"
    >
      <div className="text-xl font-black text-primary">{s.n}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">{s.label}</div>
    </div>
  ))}
</div>
```

- [ ] **Step 3: 급한 것만 위로 올린다**

연체 · 반납 임박 · 사서 승인 대기 세 가지를 계산해 타일 바로 아래에 배너로 띄운다. 기존 연체/임박 배너 로직을 재사용하고 승인 대기를 더한다:

```tsx
const pendingApproval = requests.filter((r) => r.approval === "PENDING_APPROVAL");
```

- [ ] **Step 4: 섹션을 접이식으로 바꾼다**

`components/ui/accordion.tsx` 가 이미 있으므로 그것을 쓴다. 섹션 네 개(대출 현황 · 요청 현황 · 예약 · 읽고 싶은 책)를 `Accordion type="multiple"` 로 감싸고, 각 트리거에 제목과 개수를 함께 보여준다. 기본으로 펼칠 섹션은 없다(요약이 먼저 보여야 한다).

- [ ] **Step 5: 요청 이력 삭제 UI**

요청 현황 섹션의 각 항목에 삭제 버튼을 붙인다:

```tsx
const removeRequest = async (id: number) => {
  try {
    await memberApi.deleteRequest(id);
    toast.success("요청 이력을 지웠습니다");
    await load();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "지우지 못했습니다");
  }
};
```

섹션 헤더 옆에는 일괄 정리 버튼을 둔다. 승인 대기 건은 서버가 409 로 막으므로 애초에 목록에서 빼고 호출한다:

```tsx
// ponytail: 일괄 삭제 API 대신 순차 호출. 목록이 최대 30건이라 충분하다.
// 건수가 늘면 백엔드에 일괄 삭제를 만든다.
const clearFinished = async () => {
  const targets = requests.filter((r) => r.approval !== "PENDING_APPROVAL");
  if (targets.length === 0) return;
  for (const r of targets) {
    try {
      await memberApi.deleteRequest(r.id);
    } catch {
      /* 한 건 실패해도 나머지는 계속 지운다 */
    }
  }
  toast.success(`요청 이력 ${targets.length}건을 정리했습니다`);
  await load();
};
```

- [ ] **Step 6: 실동작 확인 (스크린샷)**

Run: 로그인 후 `/me` 진입
Expected: 상단 카운터 네 개와 급한 것 배너가 보이고, 요청 섹션을 펼쳐 삭제가 동작한다. 스크린샷을 남긴다. 네 섹션 모두 실제 데이터가 나오는지 확인한다(PRD User Story 40).

- [ ] **Step 7: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 8: 커밋**

```bash
git add aba_service/frontend/src/lib/member.ts aba_service/frontend/src/routes/me.tsx
git commit -m "feat(me): summary dashboard with collapsible sections and history cleanup"
```

---

### Task 12: 스캔·OCR 진입점 제거, 설정 정리, 죽은 코드 정리

**Files:**
- Delete: `aba_service/frontend/src/routes/scan.tsx`, `aba_service/frontend/src/routes/ocr.tsx`
- Modify: `aba_service/frontend/src/routes/settings.tsx:139-171`, `settings.tsx:203-205`
- Modify: `aba_service/frontend/src/lib/use-speech.ts:114-122`
- Regenerate + commit: `aba_service/frontend/src/routeTree.gen.ts`

**Interfaces:**
- Consumes: 없음
- Produces: 없음

**⚠️ 이 Task 는 Wave 5 에서 혼자 돈다.** `routeTree.gen.ts` 는 라우트 파일 전체에서 생성되는 공용 산출물이라, Wave 4 의 라우트 편집이 전부 끝난 뒤에 한 번만 재생성해야 머지 충돌이 안 난다. 온보딩 음성 수정(`index.tsx`·`home.tsx`)은 **T7 이 가져갔다** — `home.tsx` 를 두 Task 가 나눠 고치면 충돌한다.

- [ ] **Step 1: 설정에서 스캔/인식 도구 섹션 삭제**

`settings.tsx` 의 `<Section title="스캔 / 인식 도구" icon={ScanLine}>` 블록 전체를 지운다. 그로 인해 쓰이지 않게 된 import(`ScanLine`, `ScanText`, `ChevronRight`, `Link`)도 함께 지운다. **언어 섹션과 공유/QR 섹션은 그대로 둔다.**

- [ ] **Step 2: Lovable 서명 문구 제거**

```tsx
<p className="mt-8 text-center text-[11px] text-muted-foreground">
  LiBi v0.1
</p>
```

- [ ] **Step 3: 라우트 파일 삭제**

```bash
git rm aba_service/frontend/src/routes/scan.tsx aba_service/frontend/src/routes/ocr.tsx
```

`src/routeTree.gen.ts` 는 dev 서버/빌드가 자동 재생성한다. 손으로 고치지 말고 빌드로 갱신한 뒤, **이 Task 에서만** 커밋한다.

- [ ] **Step 4: 죽은 `speak()` 제거**

`src/lib/use-speech.ts` 파일 끝의 `export function speak(...)` 전체를 지운다. 호출처가 한 곳도 없다(`grep -rn "speak(" src/` 로 확인).

- [ ] **Step 5: 실동작 확인 (스크린샷)**

Run: 앱에서 `/settings` 진입
Expected: AI 모델 · 언어 · 공유/QR 세 섹션만 남는다. 스크린샷을 남긴다.

- [ ] **Step 6: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음, `routeTree.gen.ts` 에서 `/scan`·`/ocr` 항목이 사라짐

- [ ] **Step 7: 커밋**

`git add -A` 를 쓰지 않는다 — 옆 worktree 나 아직 안 합쳐진 작업까지 끌어온다. 파일을 명시한다:

```bash
git add aba_service/frontend/src/routes/settings.tsx \
        aba_service/frontend/src/lib/use-speech.ts \
        aba_service/frontend/src/routeTree.gen.ts
git commit -m "chore(frontend): drop scan/OCR entry points and dead speech synthesis"
```

(삭제한 두 라우트는 Step 3 의 `git rm` 으로 이미 스테이지돼 있다.)

---

### Task 13: 지도 배경 재생성 + 구역 박스 도면 마감

**Files:**
- Create/Replace: `aba_service/frontend/public/map/arte3.png`
- Modify: `aba_service/frontend/src/components/LibraryMap.tsx:115-122`(TONE), `LibraryMap.tsx:267-303`(렌더)
- Modify: `aba_service/frontend/src/lib/map-waypoints.ts:1-3`(주석)

**Interfaces:**
- Consumes: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_navigation/map/arte3.pgm` (읽기 전용)
- Produces: 없음

**참고 이미지:** `/home/ane/Downloads/libi_library_map_color.png` — 굵은 네이비 벽, 파스텔 라운드 박스, 한글 라벨, 옅은 배경. **레이아웃이 아니라 톤만 참고한다.** 구역 내용은 `waypoint.yaml` 정점 그대로다.

- [ ] **Step 1: 배경 PNG 재생성**

`arte3.pgm`(P5, 63×108)을 8배 확대해 `public/map/arte3.png` 로 저장한다. 점유(어두운 픽셀)는 네이비 `#1e2a4a`, 자유공간은 크림 `#f9f8f5`, 미지영역은 투명으로 둔다. 확대는 **최근접 이웃**으로 해서 벽 경계가 흐려지지 않게 한다.

이 이미지 생성은 codex 에 위임할 수 있다:

```bash
codex exec --dangerously-bypass-approvals-and-sandbox "Read the PGM at aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_navigation/map/arte3.pgm (P5, 63x108). Upscale 8x with nearest-neighbour to 504x864 and write a PNG to aba_service/frontend/public/map/arte3.png. Map occupied cells (dark) to #1e2a4a, free space to #f9f8f5, unknown to transparent. Use Pillow. Do not change any other file."
```

- [ ] **Step 2: 생성 결과 확인**

Run: `python3 -c "from PIL import Image; im=Image.open('aba_service/frontend/public/map/arte3.png'); print(im.size, im.mode)"`
Expected: `(504, 864) RGBA`

- [ ] **Step 3: 배경 렌더 파라미터 조정**

`LibraryMap.tsx` 의 배경 `<img>` 에서 `opacity-25` 를 `opacity-60` 으로 올리고 `[image-rendering:pixelated]` 는 유지한다. 벽이 도면처럼 보여야 하므로 너무 옅으면 안 된다.

- [ ] **Step 4: 구역 박스 톤 교체**

`TONE` 을 참고 이미지 팔레트에 맞춘다. 파스텔 배경 + 진한 테두리 + 같은 계열의 진한 글자:

```tsx
const TONE: Record<string, string> = {
  pink: "bg-rose-100/90 border-rose-300 text-rose-900",
  amber: "bg-amber-100/90 border-amber-300 text-amber-900",
  sky: "bg-sky-100/90 border-sky-400 text-sky-900",
  violet: "bg-violet-100/90 border-violet-300 text-violet-900",
  emerald: "bg-emerald-100/90 border-emerald-300 text-emerald-900",
  stone: "bg-stone-100/90 border-stone-400 text-stone-700",
};
```

박스 버튼의 클래스에서 `rounded-lg` → `rounded-md`, `border` → `border-2` 로 바꾸고, 활성 상태에 그림자를 더한다.

- [ ] **Step 5: 좌표 계산은 건드리지 않는다**

`buildZones`·`rotate`·`separate` 는 그대로 둔다. `arte3.yaml` 과 `arte2.yaml` 은 `origin`·`resolution`·크기가 모두 같아서 기존 정규화 좌표가 그대로 유효하다.

- [ ] **Step 6: 주석 갱신**

`map-waypoints.ts` 상단 주석의 `arte2.yaml` 을 `arte3.yaml` 로 고친다. `LibraryMap.tsx` 상단 주석의 `arte2` 언급도 `arte3` 으로 고친다.

- [ ] **Step 7: 실동작 확인 (스크린샷)**

Run: 앱에서 `/map` 진입
Expected: 벽이 또렷한 도면으로 보이고 구역 박스가 참고 이미지 톤으로 나온다. 스크린샷을 남긴다.

- [ ] **Step 8: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 9: 커밋**

```bash
git add aba_service/frontend/public/map/arte3.png aba_service/frontend/src/components/LibraryMap.tsx aba_service/frontend/src/lib/map-waypoints.ts
git commit -m "feat(map): regenerate arte3 background and restyle zone boxes as a floor plan"
```

---

### Task 14: LiBi 도구 레이어

**Files:**
- Create: `aba_service/frontend/src/lib/libi-tools.ts`

**Interfaces:**
- Consumes: `memberApi`(`deleteRequest` 포함 — Task 11)·`getToken`·`TABLES`·`APPROVAL_LABEL` (`lib/member`), `fetchCatalog`·`fetchPopular`·`fetchBook` (Task 4), `bookAvailability`·`availabilitySentence` (Task 4), `buildZones` (`components/LibraryMap`)
- Produces:
  - `LIBI_TOOLS` — Ollama `tools` 파라미터에 그대로 넣는 배열
  - `type ToolName` — 도구 이름 유니온
  - `type PendingCall = { name: ToolName; args: Record<string, unknown>; book: CatalogBook | null; sentence: string }`
  - `prepareTool(name: string, rawArgs: unknown): Promise<PrepareResult>` — 검증 + 정본 해석까지 끝낸 결과
  - `type PrepareResult = { kind: "run"; result: ToolResult } | { kind: "confirm"; pending: PendingCall } | { kind: "choose"; books: CatalogBook[]; text: string } | { kind: "error"; text: string }`
  - `runPending(pending: PendingCall): Promise<ToolResult>`
  - `type ToolResult = { ok: boolean; text: string; books?: CatalogBook[] }`

**설계 원칙 (codex 지적 반영)**
1. **모델이 준 인자는 신뢰하지 않는다.** 도구마다 zod 스키마로 검증하고, 실패하면 실행도 확인 카드도 없이 되묻는다.
2. **확인 카드는 정본을 보여준다.** 모델이 말한 제목이 아니라 **해석된 실제 도서**(제목·저자·서가)를 띄운다. 애매하면 확인 카드를 만들지 않고 후보를 고르게 한다.
3. **결과는 접수번호·자리·상태를 포함한다** (R73).

- [ ] **Step 1: 도구 목록 정의**

조회형(즉시 실행): `search_books`, `book_detail`, `popular_books`, `my_loans`, `my_requests`, `my_reservations`, `my_wishlist`, `where_is_zone`
변경형(확인 필요): `request_read`, `request_borrow`, `reserve_book`, `cancel_reservation`, `add_wishlist`, `remove_wishlist`, `extend_loan`, `delete_request`

```typescript
export type ToolName =
  | "search_books" | "book_detail" | "popular_books"
  | "my_loans" | "my_requests" | "my_reservations" | "my_wishlist"
  | "where_is_zone"
  | "request_read" | "request_borrow" | "reserve_book"
  | "cancel_reservation" | "add_wishlist" | "remove_wishlist"
  | "extend_loan" | "delete_request";

/** 되돌리기 어려운 도구 — 실행 전에 사용자 확인을 받는다.
 *  모델이 1.7B 라 "예약"과 "대여 신청"을 헷갈릴 수 있다. */
export const NEEDS_CONFIRM: Set<ToolName> = new Set([
  "request_read", "request_borrow", "reserve_book",
  "cancel_reservation", "add_wishlist", "remove_wishlist",
  "extend_loan", "delete_request",
]);

/** 로그인이 필요한 도구. 비로그인이면 실행하지 않고 안내한다. */
const NEEDS_LOGIN: Set<ToolName> = new Set([
  ...NEEDS_CONFIRM,
  "my_loans", "my_requests", "my_reservations", "my_wishlist",
]);
```

- [ ] **Step 2: 인자 스키마 + Ollama tool 정의 (16개 전부)**

zod 스키마 하나로 **런타임 검증과 tool JSON 스키마를 동시에** 만든다. 두 벌을 따로 쓰면 반드시 어긋난다.

```typescript
import { z } from "zod";

const CATEGORY = z.enum(["literature", "art", "science", "humanities", "kids"]);
const TABLE = z.enum([
  "테이블-1번-상", "테이블-1번-좌", "테이블-1번-우",
  "테이블-2번-하", "테이블-2번-좌", "테이블-2번-우",
]);
const TITLE = z.string().trim().min(1);
const ZONE_LABEL = z.string().trim().min(1);

/** 도구 하나의 정의. `schema` 가 곧 검증기이자 문서다. */
type ToolDef = {
  name: ToolName;
  description: string;
  schema: z.ZodObject<z.ZodRawShape>;
  /** 인자 하나하나의 사람말 설명 — JSON 스키마에 그대로 실린다. */
  argDocs: Record<string, string>;
};

const TOOL_DEFS: ToolDef[] = [
  // ── 조회형 ──────────────────────────────────────────────
  {
    name: "search_books",
    description: "도서관 장서를 제목·저자·내용으로 검색한다. 사용자가 책을 찾을 때 쓴다.",
    schema: z.object({ query: TITLE, category: CATEGORY.optional() }),
    argDocs: { query: "검색어", category: "분야 (선택)" },
  },
  {
    name: "book_detail",
    description: "특정 책의 줄거리, 서가 위치, 지금 빌릴 수 있는지를 알려준다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "책 제목" },
  },
  {
    name: "popular_books",
    description: "대출 횟수 기준 인기 도서를 알려준다. '요즘 뭐가 인기야' 같은 질문에 쓴다.",
    schema: z.object({ category: CATEGORY.optional() }),
    argDocs: { category: "분야 (선택)" },
  },
  {
    name: "my_loans",
    description: "지금 내가 빌린 책과 반납 예정일을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_requests",
    description: "내가 넣은 배달·대여 요청의 진행 상황을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_reservations",
    description: "내가 예약해 둔 책 목록을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_wishlist",
    description: "내가 읽고 싶다고 담아 둔 책 목록을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "where_is_zone",
    description: "도서관 구역(문학·예술·과학·인문학·유아·테이블·안내데스크·화장실·입구)이 어디인지 알려준다.",
    schema: z.object({ zone_label: ZONE_LABEL }),
    argDocs: { zone_label: "구역 이름 (예: 문학, 과학, 테이블 1)" },
  },
  // ── 변경형 (확인 카드 필요) ─────────────────────────────
  {
    name: "request_read",
    description:
      "책을 회원이 앉은 테이블로 배달 요청한다(열람). 대출이 아니고 사서 승인도 필요 없으며, 로봇이 바로 움직인다.",
    schema: z.object({ book_title: TITLE, table: TABLE }),
    argDocs: { book_title: "요청할 책 제목", table: "받을 자리" },
  },
  {
    name: "request_borrow",
    description:
      "책을 대여 신청한다. 사서 승인 후 로봇이 안내데스크로 가져다 놓고, 대출 확정은 사서가 한다. 관외 반출이라 열람과 다르다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "대여 신청할 책 제목" },
  },
  {
    name: "reserve_book",
    description: "지금 대출 중인 책을 예약한다. 반납되면 알림을 받는다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "예약할 책 제목" },
  },
  {
    name: "cancel_reservation",
    description: "내가 예약한 책의 예약을 취소한다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "예약을 취소할 책 제목" },
  },
  {
    name: "add_wishlist",
    description: "책을 읽고 싶은 목록에 담는다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "담을 책 제목" },
  },
  {
    name: "remove_wishlist",
    description: "읽고 싶은 목록에서 책을 뺀다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "뺄 책 제목" },
  },
  {
    name: "extend_loan",
    description: "빌린 책의 반납일을 7일 미룬다. 연장은 대출 1건당 1회만 가능하다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "연장할 책 제목" },
  },
  {
    name: "delete_request",
    description: "끝났거나 반려된 내 요청 이력을 지운다. 사서 승인을 기다리는 건은 지울 수 없다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "이력을 지울 요청의 책 제목" },
  },
];
```

zod 스키마를 Ollama 가 읽는 JSON 스키마로 옮기는 변환기를 둔다. 우리가 쓰는 타입은 문자열·enum·optional 셋뿐이라 짧다:

```typescript
function toJsonSchema(def: ToolDef) {
  const shape = def.schema.shape;
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const [key, value] of Object.entries(shape)) {
    const optional = value instanceof z.ZodOptional;
    const inner = optional ? (value as z.ZodOptional<z.ZodTypeAny>).unwrap() : value;
    properties[key] =
      inner instanceof z.ZodEnum
        ? { type: "string", enum: inner.options, description: def.argDocs[key] }
        : { type: "string", description: def.argDocs[key] };
    if (!optional) required.push(key);
  }
  return { type: "object", properties, required };
}

export const LIBI_TOOLS = TOOL_DEFS.map((d) => ({
  type: "function",
  function: {
    name: d.name,
    description: d.description,
    parameters: toJsonSchema(d),
  },
}));

const BY_NAME = new Map(TOOL_DEFS.map((d) => [d.name, d]));
```

- [ ] **Step 3: 제목 → 도서 해석 (모호하면 실행하지 않는다)**

모델은 제목만 말한다. **정확히 하나로 좁혀지지 않으면 절대 임의로 고르지 않는다** — 예전 설계의 `rows[0]` 폴백은 `/api/books` 정렬이 `in_stock DESC, id DESC` 라서 "아무 재고 있는 책"을 집어 엉뚱한 책을 배달시킬 수 있었다.

```typescript
type Resolution =
  | { kind: "one"; book: CatalogBook }
  | { kind: "many"; books: CatalogBook[] }
  | { kind: "none" };

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, "");

/**
 * 제목으로 도서를 특정한다.
 *
 * 1순위: 정규화 완전일치가 **정확히 하나**  → 그 책
 * 그 외: 후보를 돌려주고 사용자가 고르게 한다(모호), 또는 없음
 */
async function resolveBook(title: string): Promise<Resolution> {
  const rows = await fetchCatalog({ q: title, limit: 20 });
  if (rows.length === 0) return { kind: "none" };

  const target = norm(title);
  const exact = rows.filter((b) =>
    Object.values(b.title).some((t) => norm(t) === target),
  );
  if (exact.length === 1) return { kind: "one", book: exact[0] };
  if (exact.length > 1) return { kind: "many", books: exact };
  if (rows.length === 1) return { kind: "one", book: rows[0] };
  return { kind: "many", books: rows.slice(0, 10) };
}
```

`cancel_reservation`·`remove_wishlist`·`extend_loan`·`delete_request` 는 **카탈로그가 아니라 내 목록**에서 찾아야 한다(내가 안 빌린 책을 연장할 수는 없다). 같은 규칙의 전용 해석기를 둔다:

```typescript
/** 내 목록(대출·예약·위시·요청) 안에서 제목으로 항목을 찾는다. */
function pickMine<T>(rows: T[], title: string, titleOf: (r: T) => string): T[] {
  const target = norm(title);
  const exact = rows.filter((r) => norm(titleOf(r)) === target);
  if (exact.length > 0) return exact;
  return rows.filter((r) => norm(titleOf(r)).includes(target));
}
```

- [ ] **Step 4: 준비 단계 — 검증 · 로그인 확인 · 정본 해석**

챗봇은 `runTool` 을 직접 부르지 않는다. **`prepareTool` 한 곳**을 통과시킨다. 여기서 막히면 확인 카드도 안 만든다.

```typescript
export type ToolResult = { ok: boolean; text: string; books?: CatalogBook[] };

export type PendingCall = {
  name: ToolName;
  args: Record<string, unknown>;
  /** 해석된 **정본** 도서. 확인 카드는 모델이 말한 제목이 아니라 이걸 보여준다. */
  book: CatalogBook | null;
  sentence: string;
};

export type PrepareResult =
  | { kind: "run"; result: ToolResult }
  | { kind: "confirm"; pending: PendingCall }
  | { kind: "choose"; books: CatalogBook[]; text: string }
  | { kind: "error"; text: string };

export async function prepareTool(
  name: string,
  rawArgs: unknown,
): Promise<PrepareResult> {
  const def = BY_NAME.get(name as ToolName);
  if (!def) return { kind: "error", text: "제가 할 수 있는 일이 아니에요." };

  // 모델이 준 인자는 신뢰하지 않는다. 빠지거나 형식이 틀리면 되묻는다.
  const parsed = def.schema.safeParse(rawArgs ?? {});
  if (!parsed.success) {
    return { kind: "error", text: "어떤 책인지(또는 어느 자리인지) 다시 말씀해 주시겠어요?" };
  }
  const args = parsed.data as Record<string, unknown>;

  if (NEEDS_LOGIN.has(def.name) && getToken() === null) {
    return { kind: "error", text: "이 기능은 로그인이 필요해요. 상단의 「로그인」을 눌러 주세요." };
  }

  // 책을 다루는 도구는 **실행 전에** 정본을 확정한다.
  let book: CatalogBook | null = null;
  if (typeof args.book_title === "string") {
    const r = await resolveBook(args.book_title);
    if (r.kind === "none") return { kind: "error", text: `«${args.book_title}» 은(는) 찾지 못했어요.` };
    if (r.kind === "many") {
      return {
        kind: "choose",
        books: r.books,
        text: "어떤 책을 말씀하시는 걸까요? 눌러서 골라 주세요.",
      };
    }
    book = r.book;
  }

  if (!NEEDS_CONFIRM.has(def.name)) {
    return { kind: "run", result: await runTool(def.name, args, book) };
  }
  return {
    kind: "confirm",
    pending: { name: def.name, args, book, sentence: describeCall(def.name, args, book) },
  };
}

/** 확인 카드에서 「네」를 눌렀을 때. 카드가 인자를 고쳤을 수 있으므로 다시 검증한다. */
export async function runPending(pending: PendingCall): Promise<ToolResult> {
  const def = BY_NAME.get(pending.name);
  if (!def) return { ok: false, text: "알 수 없는 요청이에요." };
  const parsed = def.schema.safeParse(pending.args);
  if (!parsed.success) return { ok: false, text: "요청 내용이 올바르지 않아요." };
  return runTool(pending.name, parsed.data as Record<string, unknown>, pending.book);
}
```

- [ ] **Step 5: 실행기 — 16개 전부**

```typescript
async function runTool(
  name: ToolName,
  args: Record<string, unknown>,
  book: CatalogBook | null,
): Promise<ToolResult> {
  try {
    switch (name) {
      // ── 조회형 ────────────────────────────────────────
      case "search_books": {
        const books = await fetchCatalog({
          q: String(args.query),
          category: (args.category as BookCategory | undefined) ?? null,
          limit: 10,
        });
        return {
          ok: true,
          books,
          text: books.length ? `${books.length}권 찾았어요.` : "그런 책은 못 찾았어요.",
        };
      }
      case "book_detail": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const a = bookAvailability(book);
        return {
          ok: true,
          books: [book],
          text: `«${book.title.KR}» — ${book.author}. ${book.summary.KR || "소개가 아직 없어요."} ${availabilitySentence(a, book.zone, book.shelf)}`,
        };
      }
      case "popular_books": {
        const books = await fetchPopular({
          category: (args.category as BookCategory | undefined) ?? null,
          limit: 10,
        });
        return {
          ok: true,
          books,
          text: books.length ? "요즘 많이 빌려 가는 책이에요." : "아직 대출 기록이 없어요.",
        };
      }
      case "my_loans": {
        const rows = await memberApi.loans();
        const open = rows.filter((l) => l.status !== "returned");
        if (open.length === 0) return { ok: true, text: "지금 빌린 책이 없어요." };
        return {
          ok: true,
          text: open
            .map((l) => `«${l.book.title}» — ${l.overdue ? `연체 ${-l.days_left}일` : `반납 D-${l.days_left}`}`)
            .join("\n"),
        };
      }
      case "my_requests": {
        const rows = await memberApi.requests();
        if (rows.length === 0) return { ok: true, text: "넣어 둔 요청이 없어요." };
        return {
          ok: true,
          text: rows
            .map((r) => `#${r.id} «${r.book_title}» ${r.kind === "borrow" ? "대여" : "열람"} · ${r.dropoff} · ${r.status ?? APPROVAL_LABEL[r.approval]}`)
            .join("\n"),
        };
      }
      case "my_reservations": {
        const rows = await memberApi.reservations();
        const alive = rows.filter((r) => r.status !== "cancelled");
        if (alive.length === 0) return { ok: true, text: "예약해 둔 책이 없어요." };
        return { ok: true, text: alive.map((r) => `«${r.book.title}» — ${r.status}`).join("\n") };
      }
      case "my_wishlist": {
        const rows = await memberApi.wishlist();
        if (rows.length === 0) return { ok: true, text: "담아 둔 책이 없어요." };
        return { ok: true, text: rows.map((w) => `«${w.book.title}» — ${w.book.author}`).join("\n") };
      }
      case "where_is_zone": {
        const label = String(args.zone_label).trim();
        const zone = buildZones().find(
          (z) => norm(z.label) === norm(label) || norm(label).includes(norm(z.label)),
        );
        if (!zone) return { ok: false, text: `«${label}» 구역은 못 찾았어요.` };
        return { ok: true, text: `${zone.label} — ${zone.desc} (정점: ${zone.members.join(", ")})` };
      }

      // ── 변경형 ────────────────────────────────────────
      case "request_read": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        if (book.unavailable) return { ok: false, text: "훼손·분실로 대출이 막힌 도서예요." };
        if (!book.inStock) return { ok: false, text: "지금 대출 중이라 예약만 가능해요." };
        const res = await memberApi.requestRead(book.bookId, String(args.table));
        return {
          ok: true,
          text: `접수 #${res.id} — «${res.book_title}» 을(를) ${res.dropoff} 로 가져다 드릴게요. (${APPROVAL_LABEL[res.approval]})`,
        };
      }
      case "request_borrow": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        if (book.unavailable) return { ok: false, text: "훼손·분실로 대출이 막힌 도서예요." };
        if (!book.inStock) return { ok: false, text: "지금 대출 중이라 예약만 가능해요." };
        const res = await memberApi.requestBorrow(book.bookId);
        return {
          ok: true,
          text: `접수 #${res.id} — «${res.book_title}» 대여를 신청했어요. 사서 승인 후 ${res.dropoff} 에서 받으실 수 있어요. (${APPROVAL_LABEL[res.approval]})`,
        };
      }
      case "reserve_book": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const res = await memberApi.reserve(book.bookId);
        return { ok: true, text: `«${res.book.title}» 예약했어요 (${res.status}). 반납되면 알려드릴게요.` };
      }
      case "cancel_reservation": {
        const rows = await memberApi.reservations();
        const hits = pickMine(rows.filter((r) => r.status !== "cancelled"), String(args.book_title), (r) => r.book.title);
        if (hits.length === 0) return { ok: false, text: "그 책은 예약 목록에 없어요." };
        if (hits.length > 1) return { ok: false, text: "같은 제목이 여러 건이에요. 「내 정보」에서 취소해 주세요." };
        await memberApi.cancelReservation(hits[0].id);
        return { ok: true, text: `«${hits[0].book.title}» 예약을 취소했어요.` };
      }
      case "add_wishlist": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const res = await memberApi.addWishlist(book.bookId);
        return { ok: true, text: `«${res.book.title}» 을(를) 읽고 싶은 목록에 담았어요.` };
      }
      case "remove_wishlist": {
        const rows = await memberApi.wishlist();
        const hits = pickMine(rows, String(args.book_title), (w) => w.book.title);
        if (hits.length === 0) return { ok: false, text: "그 책은 목록에 없어요." };
        if (hits.length > 1) return { ok: false, text: "같은 제목이 여러 건이에요. 「내 정보」에서 빼 주세요." };
        await memberApi.removeWishlist(hits[0].id);
        return { ok: true, text: `«${hits[0].book.title}» 을(를) 목록에서 뺐어요.` };
      }
      case "extend_loan": {
        const rows = await memberApi.loans();
        const hits = pickMine(rows.filter((l) => l.status !== "returned"), String(args.book_title), (l) => l.book.title);
        if (hits.length === 0) return { ok: false, text: "그 책은 빌린 목록에 없어요." };
        if (hits.length > 1) return { ok: false, text: "같은 제목이 여러 건이에요. 「내 정보」에서 연장해 주세요." };
        if (!hits[0].can_extend) return { ok: false, text: "이미 연장했거나 연장할 수 없는 대출이에요." };
        const res = await memberApi.extendLoan(hits[0].id);
        return { ok: true, text: `«${res.book.title}» 반납일을 ${res.due_at.slice(0, 10)} 로 미뤘어요.` };
      }
      case "delete_request": {
        const rows = await memberApi.requests();
        const hits = pickMine(rows, String(args.book_title), (r) => r.book_title);
        if (hits.length === 0) return { ok: false, text: "그 요청은 없어요." };
        if (hits.length > 1) return { ok: false, text: "같은 제목이 여러 건이에요. 「내 정보」에서 지워 주세요." };
        if (hits[0].approval === "PENDING_APPROVAL") {
          return { ok: false, text: "사서 승인을 기다리는 요청은 지울 수 없어요." };
        }
        await memberApi.deleteRequest(hits[0].id);
        return { ok: true, text: `요청 #${hits[0].id} 이력을 지웠어요.` };
      }
    }
  } catch (err) {
    return { ok: false, text: err instanceof Error ? err.message : "처리하지 못했어요." };
  }
}
```

`switch` 가 `ToolName` 16개를 전부 덮으므로 `default` 를 두지 않는다 — 도구를 새로 넣고 케이스를 빠뜨리면 `tsc` 가 잡아 준다.

- [ ] **Step 6: 확인 문장 생성기**

**모델이 말한 제목이 아니라 해석된 정본 제목**을 쓴다 — 이래야 사용자가 "내가 말한 책이 아니네"를 알아챌 수 있다.

```typescript
/** 확인 카드에 띄울 문장. 열람과 대여의 차이를 문구로 드러낸다. */
export function describeCall(
  name: ToolName,
  args: Record<string, unknown>,
  book: CatalogBook | null,
): string {
  // 정본이 있으면 저자·서가까지 붙여 다른 책과 헷갈리지 않게 한다.
  const t = book ? `«${book.title.KR}»(${book.author} · ${book.zone})` : `«${args.book_title}»`;
  switch (name) {
    case "request_read":
      return `${t} 을(를) ${args.table} 자리로 가져다 드릴까요? 사서 승인 없이 로봇이 바로 움직여요.`;
    case "request_borrow":
      return `${t} 대여를 신청할까요? 사서 승인 후 안내데스크에서 받으실 수 있어요.`;
    case "reserve_book":
      return `${t} 을(를) 예약할까요? 반납되면 알려드려요.`;
    case "cancel_reservation":
      return `${t} 예약을 취소할까요?`;
    case "add_wishlist":
      return `${t} 을(를) 읽고 싶은 목록에 담을까요?`;
    case "remove_wishlist":
      return `${t} 을(를) 목록에서 뺄까요?`;
    case "extend_loan":
      return `${t} 반납일을 7일 미룰까요? 연장은 1회만 가능해요.`;
    case "delete_request":
      return `${t} 요청 이력을 지울까요? 되돌릴 수 없어요.`;
    default:
      return "이대로 진행할까요?";
  }
}
```

(조회형은 확인 카드를 타지 않으므로 `default` 로 남긴다.)

- [ ] **Step 7: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit`
Expected: 오류 없음. `runTool` 의 `switch` 에 케이스가 빠지면 반환 타입 불일치로 컴파일이 깨진다.

- [ ] **Step 8: 커밋**

```bash
git add aba_service/frontend/src/lib/libi-tools.ts
git commit -m "feat(libi): add validated member-function tool layer for the bot"
```

---

### Task 15: 챗봇에 도구 호출 연결 + 확인 카드

**Files:**
- Create: `aba_service/frontend/src/components/BotConfirmCard.tsx`
- Modify: `aba_service/frontend/src/routes/chat.tsx` (Ollama 호출부 + 메시지 렌더)

**Interfaces:**
- Consumes: `LIBI_TOOLS`·`prepareTool`·`runPending`·`PendingCall`·`ToolName` (Task 14), `BookRow`·`BookDetailSheet` (Task 5), `TABLES` (`lib/member`)
- Produces: 없음

**기존 로봇 정규식 처리:** `tryParseRobotCommand` 함수 자체와 `/api/robot/execute` 호출은 **고치지 않는다.** 다만 **실행 순서를 뒤집는다** — 지금은 정규식이 LLM 보다 먼저 돌아서, `chat.tsx:81` 의 `/(정지|멈춰|멈춤|스톱|stop)/i` 가 "예약을 **정지**해줘" 를 로봇 정지 명령으로 삼켜 버린다. "앞으로"·"소리"도 마찬가지다. LLM 도구를 먼저 태우고, **모델이 아무 도구도 고르지 않았을 때만** 정규식으로 내려간다.

- [ ] **Step 1: 확인 카드 컴포넌트 작성**

`src/components/BotConfirmCard.tsx`:

```tsx
import { useState } from "react";

import { TABLES } from "@/lib/member";
import type { ToolName } from "@/lib/libi-tools";

/**
 * 변경형 도구를 실행하기 전에 띄우는 카드.
 *
 * 모델이 작아 "예약"과 "대여 신청"을 헷갈릴 수 있다. 사용자가 **고쳐서** 실행할 수
 * 있어야 다시 말하지 않는다 — 자리와 요청 종류는 여기서 바꾼다.
 */
export function BotConfirmCard({
  pending,
  onConfirm,
  onCancel,
}: {
  pending: PendingCall;
  onConfirm: (name: ToolName, args: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const { name, args, book, sentence } = pending;
  const [table, setTable] = useState(String(args.table ?? TABLES[0].value));
  const [kind, setKind] = useState<ToolName>(name);

  const isDelivery = name === "request_read" || name === "request_borrow";

  return (
    <div className="rounded-2xl border-2 border-primary/40 bg-primary-soft/40 p-3">
      {/* 모델이 말한 제목이 아니라 **실제로 해석된 책**을 보여준다.
          이게 없으면 엉뚱한 책이 잡혀도 사용자가 알아챌 방법이 없다. */}
      {book && (
        <div className="mb-2 flex items-center gap-2 rounded-xl bg-card p-2">
          <span className={`flex size-10 items-center justify-center rounded-lg bg-gradient-to-br ${book.color} text-xl`}>
            {book.cover}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-xs font-bold text-foreground">{book.title.KR}</span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {book.author} · {book.zone} · {book.shelf}
            </span>
          </span>
        </div>
      )}
      <p className="text-sm font-semibold text-foreground">{sentence}</p>

      {isDelivery && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button
            onClick={() => setKind("request_read")}
            className={`rounded-xl border-2 p-2 text-left text-xs ${
              kind === "request_read"
                ? "border-primary bg-card font-bold text-primary"
                : "border-border bg-card text-muted-foreground"
            }`}
          >
            📖 자리로 받기
          </button>
          <button
            onClick={() => setKind("request_borrow")}
            className={`rounded-xl border-2 p-2 text-left text-xs ${
              kind === "request_borrow"
                ? "border-primary bg-card font-bold text-primary"
                : "border-border bg-card text-muted-foreground"
            }`}
          >
            🧾 대여 신청
          </button>
        </div>
      )}

      {kind === "request_read" && (
        <div className="mt-2 grid grid-cols-3 gap-1.5">
          {TABLES.map((t) => (
            <button
              key={t.value}
              onClick={() => setTable(t.value)}
              className={`rounded-lg border py-1.5 text-[10px] font-medium ${
                table === t.value
                  ? "border-primary bg-card text-primary"
                  : "border-border bg-card text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <button
          onClick={() =>
            // 대여로 바꾸면 table 인자는 스키마에 없다 — 넘기지 않는다.
            onConfirm(kind, kind === "request_read" ? { ...args, table } : { ...args, table: undefined })
          }
          className="h-10 flex-1 rounded-xl bg-primary text-xs font-bold text-primary-foreground"
        >
          네, 진행할게요
        </button>
        <button
          onClick={onCancel}
          className="h-10 rounded-xl border border-border px-4 text-xs font-semibold text-muted-foreground"
        >
          아니요
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 대화 이력을 유지한다**

지금은 `chat.tsx:219` 가 **시스템 메시지 + 현재 사용자 메시지만** 보낸다. 그래서 "사피엔스 찾아줘" → "이거 예약해줘" 에서 *이거* 가 뭔지 모델이 알 방법이 없다. 이력을 상태로 들고 매번 함께 보낸다.

```tsx
type ChatTurn =
  | { role: "system" | "user" | "assistant"; content: string }
  | { role: "assistant"; content: string; tool_calls: unknown[] }
  | { role: "tool"; content: string };

// 화면에 그리는 Msg[] 와 별개로, 모델에 보낼 원본 이력을 따로 둔다.
// 화면용에는 카드·스켈레톤 같은 표시 전용 항목이 섞여 있어 그대로 못 보낸다.
const historyRef = useRef<ChatTurn[]>([{ role: "system", content: SYSTEM_PROMPT }]);
```

보낼 때는 마지막 N 턴만 자른다 — 1.7B 컨텍스트가 40k 라 무한히 쌓으면 앞이 잘려 나간다.

```tsx
// ponytail: 최근 12턴 고정 윈도. 요약이 필요할 만큼 길어지면 그때 붙인다.
const MAX_TURNS = 12;
const messages = [
  historyRef.current[0],
  ...historyRef.current.slice(1).slice(-MAX_TURNS),
];
```

- [ ] **Step 3: 스트리밍을 유지한 채 tool_calls 를 모은다**

Ollama 는 `stream: true` 에서도 `message.tool_calls` 를 청크로 내려준다. 스트리밍을 끄면 1.7B 가 생성하는 동안 무응답 구간이 생기므로 **기존 스트리밍 UX 를 유지**한다.

> 구현 전에 context7 으로 Ollama `/api/chat` 의 `tools` + `stream` 동작을 확인한다. 스트리밍 중 tool_calls 가 오지 않는 버전이면, **그때만** `stream: false` 로 내리고 "요청을 확인하는 중..." 상태를 띄운다.

```tsx
body: JSON.stringify({
  model,
  messages,
  stream: true,
  tools: LIBI_TOOLS,
}),
```

청크 루프에서 텍스트와 도구 호출을 함께 누적한다:

```tsx
let text = "";
const toolCalls: { function?: { name?: string; arguments?: unknown } }[] = [];

for await (const chunk of readNdjson(response)) {
  const delta = chunk?.message?.content;
  if (delta) {
    text += delta;
    updatePendingMessage(text);      // 기존 스트리밍 표시 그대로
  }
  const calls = chunk?.message?.tool_calls;
  if (Array.isArray(calls)) toolCalls.push(...calls);
  if (chunk?.done) break;
}
```

- [ ] **Step 4: 도구 호출을 처리하고 결과를 모델에 돌려준다**

모델이 도구를 고르면, **결과를 이력에 넣고 한 번 더 물어** 자연스러운 답을 받는다. 이 왕복이 없으면 다회차 대화가 성립하지 않는다.

```tsx
if (toolCalls.length > 0) {
  // 모델이 여러 개를 고르면 첫 변경형 하나만 다룬다 — 확인 카드가 하나뿐이라
  // 동시에 두 건을 실행하면 사용자가 무엇에 동의했는지 알 수 없다.
  const call = toolCalls[0];
  const prepared = await prepareTool(String(call.function?.name), call.function?.arguments);

  if (prepared.kind === "error") {
    pushBotMessage(prepared.text);
    return;
  }
  if (prepared.kind === "choose") {
    // 제목이 여러 권에 걸린다 — 임의로 고르지 않고 카드로 보여주고 멈춘다.
    pushBotMessage(prepared.text, prepared.books);
    return;
  }
  if (prepared.kind === "confirm") {
    setPendingCall(prepared.pending);
    pushBotMessage(prepared.pending.sentence);
    return;
  }
  await finishToolTurn(call, prepared.result);
  return;
}

// 도구를 하나도 안 골랐을 때만 로봇 하드웨어 정규식으로 내려간다.
// (순서가 반대면 "예약을 정지해줘" 가 로봇 정지로 샌다)
const robot = tryParseRobotCommand(userText);
if (robot) {
  await executeRobotCommand(robot);
  return;
}
pushBotMessage(text);
```

도구 결과를 이력에 넣고 후속 답변을 받는 부분:

```tsx
async function finishToolTurn(call: unknown, result: ToolResult) {
  historyRef.current.push(
    { role: "assistant", content: "", tool_calls: [call] },
    { role: "tool", content: result.text },
  );
  // 결과 문장과 카드는 바로 보여주고,
  pushBotMessage(result.text, result.books);
  // 모델에게 한 번 더 물어 자연스러운 마무리를 받는다(도구 없이).
  const follow = await askOllama({ messages: buildMessages(), tools: undefined });
  if (follow.trim()) appendBotMessage(follow);
}
```

- [ ] **Step 4b: 진행 중에는 입력을 잠근다**

지금 입력창은 응답 대기 중에도 열려 있어(`chat.tsx:825`), 연속으로 보내면 두 응답이 경쟁하며 `pendingCall` 을 덮어쓴다. 확인 카드가 떠 있는 동안에도 마찬가지다.

```tsx
const busy = sending || pendingCall !== null;
// <textarea disabled={busy} /> 와 전송 버튼 disabled={busy || !input.trim()}
```

`const [pendingCall, setPendingCall] = useState<PendingCall | null>(null);` 를 상태에 더한다.

- [ ] **Step 4: 확인 카드 렌더와 확정 실행**

메시지 목록 아래에 렌더한다:

```tsx
{pendingCall && (
  <BotConfirmCard
    pending={pendingCall}
    onCancel={() => {
      setPendingCall(null);
      pushBotMessage("알겠어요, 취소했어요.");
    }}
    onConfirm={async (name, args) => {
      const confirmed = { ...pendingCall, name, args };
      setPendingCall(null);
      const result = await runPending(confirmed);
      pushBotMessage(result.text, result.books);
    }}
  />
)}
```

- [ ] **Step 5: 도구가 돌려준 책을 카드로 보여준다**

봇 메시지에 `books` 가 있으면 `BookRow` 로 렌더하고, 누르면 `BookDetailSheet` 를 연다. 기존 `books` 렌더 블록이 이미 있으므로 그 마크업을 `BookRow` 로 교체한다.

- [ ] **Step 6: 실동작 확인 (스크린샷 — R5 검증 포함)**

앱을 띄우고 로그인한 뒤 LiBi bot 에 순서대로 넣는다:
1. `과학책 추천해줘` → 실제 DB 책이 카드로 나오는가
2. `사피엔스 있어?` → 검색 도구가 불리는가
3. `사피엔스 테이블 1번으로 가져다줘` → 확인 카드가 뜨는가, **해석된 책의 저자·서가가 카드에 보이는가**, 자리를 바꿀 수 있는가
4. 확인 → 접수번호·자리·상태가 답변에 나오는가, `/me` 요청 현황에 실제로 뜨는가
5. `내 대출 뭐 있어?` → 실제 대출 목록이 나오는가
6. `없는책12345 찾아줘` → 지어내지 않고 "못 찾았다"고 하는가
7. **다회차**: `사피엔스 찾아줘` → 이어서 `이거 읽고 싶은 책에 담아줘` → 앞 턴의 책이 담기는가 (C1 회귀 지점)
8. **정규식 충돌**: `예약을 정지해줘` → 로봇 정지 명령이 나가지 않고 대화로 처리되는가 (C3 회귀 지점)
9. **연타**: 응답 대기 중 전송 버튼이 잠기는가, 확인 카드가 떠 있을 때도 잠기는가 (H6 회귀 지점)

Expected: 각 단계 스크린샷을 남긴다. 지어내기·호출 실패가 보이면 그 지점만 고친다.

- [ ] **Step 7: 게이트 통과 확인**

Run: `cd aba_service/frontend && npm run lint && npx tsc --noEmit && npm run build`
Expected: 오류 없음

- [ ] **Step 8: 커밋**

```bash
git add aba_service/frontend/src/components/BotConfirmCard.tsx aba_service/frontend/src/routes/chat.tsx
git commit -m "feat(libi): let the bot run member functions via tool calls with a confirm card"
```

---

### Task 16: 요구사항 노트 정리 (Prismic + 결정사항 반영)

**Files:**
- Modify: `/home/ane/Documents/Obsidian/Ros2/프로젝트/arte/2026-07-25customerUI 수정사항.md`
- Delete: `/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725153600.png` 외 5장

**Interfaces:**
- Consumes: 없음 (레포 밖 작업)
- Produces: 없음

**주의:** 이 Task 는 git 레포 밖 파일을 다룬다. 커밋 대상이 아니다.

- [ ] **Step 1: 스크린샷 6장을 Prismic 에 업로드**

대상: `153600` · `153937` · `154448` · `154731` · `154838` · `155014`. `prismic` 스킬을 써서 각각의 CDN URL 을 받는다.

- [ ] **Step 2: 노트의 임베드를 CDN URL 로 교체**

`![[Pasted image 20260725153600.png]]` → `![설명](<CDN URL>)` 형식으로 바꾼다. 각 이미지에 그 항목이 무엇을 가리키는지 alt 텍스트를 넣는다.

- [ ] **Step 3: 원본 png 삭제**

```bash
rm "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725153600.png" \
   "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725153937.png" \
   "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725154448.png" \
   "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725154731.png" \
   "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725154838.png" \
   "/home/ane/Documents/Obsidian/Ros2/Pasted image 20260725155014.png"
```

`Pasted image 20260725153913.png` 는 노트가 참조하지 않는다. 지우지 말고 남긴다.

- [ ] **Step 4: 확정 내용 추가**

노트 아래에 「확정 사항 (2026-07-25)」 섹션을 붙이고 다음을 적는다.
- 각 항목이 어떻게 결정됐는지 (음성 유지 / OCR·바코드 폐기 / 상세는 모달 / 순위는 대출 횟수 / 위저드 3단계 / LiBi bot tool-calling 등)
- 산출물 경로: `docs/agents/prd-customer-ui-revamp.md`, `docs/agents/plan-customer-ui-revamp.md`
- 다음 사이클로 미룬 것: 로봇 하드웨어 명령 확장, LiBi bot 음성 입력

- [ ] **Step 5: 확인**

Run: `ls /home/ane/Documents/Obsidian/Ros2/Pasted*20260725*.png`
Expected: `153913` 한 장만 남는다.

---

## Self-Review

**1. Spec coverage** — PRD User Story 76개를 Task 에 대응시킨 결과:

| PRD 영역 | Story | Task |
|---|---|---|
| 검색·발견 | 1–10 | T4(디바운스), T6(검색), T7(홈 서제스트) |
| 도서 상세 | 11–20 | T1(unavailable·단건 조회), T4(상태 판정), T5(시트·행), T6/T7/T8/T9(연결) |
| 도서 요청 | 21–30 | T10(위저드·상태 가드), T5(시트→요청 이동·대출중 예약) |
| 내 정보 | 31–40 | T2(삭제 API), T11(대시보드·삭제) |
| 지도 | 41–44 | T1(zone 필터), T8(버그·행), T13(배경·마감) |
| 추천 | 45–49 | T1(popular API), T9(화면) |
| 설정·정리 | 50–54 | T3(warmup), T7(온보딩 음성), T12(스캔·OCR·죽은코드) |
| LiBi bot | 55–73 | T14(도구 16개·검증·정본 해석), T15(대화 루프·확인카드) |
| 문서 | 74–76 | T16 |

빠진 Story 없음. R73(접수번호·자리·상태)은 T14 Step 5 의 `request_read`·`request_borrow` 반환 문자열에서 충족한다.

**2. Placeholder scan** — "TBD"·"적절히 처리"·"비슷하게" 없음. codex 검토에서 지적된 T14 의 ellipsis(도구 16개 중 2개만 작성)는 **전부 실제 코드로 채웠다** — `TOOL_DEFS` 16개, `runTool` 의 `switch` 16 케이스, `describeCall` 8 케이스.

**3. Type consistency**
- `CatalogBook.unavailable: boolean` — T4 에서 정의, T5·T6·T10·T14 에서 소비. 이름 일치.
- `bookAvailability` 반환 `"available" | "borrowed" | "blocked"` — T4 정의, T5·T14 소비. 일치.
- `fetchCatalogResult` 반환 `{ ok, rows }` — T4 정의, T8 소비. 일치.
- `fetchBook(id)` — T4 정의, T10 소비. 일치.
- `BookRow` props(`book`·`onSelect`·`showStatus`·`trailing`) — T5 정의, T6~T10·T15 소비. 일치.
- `BookDetailSheet` props(`book`·`onOpenChange`·`onReserve`) — T5 정의, T6·T7·T8·T9 소비. 일치.
- `/request` 의 `?bookId=` — **T5 Step 2c 가 계약을 세우고** T10 이 위저드를 얹는다. 순서 모순 해소됨.
- `memberApi.deleteRequest(id)` — T11 정의, T14 소비(`delete_request` 도구). **T11 이 T14 보다 앞 wave 에 있다.** 일치.
- `PendingCall`·`prepareTool`·`runPending`·`ToolName` — T14 정의, T15 소비. 일치.
- 백엔드 `DELETE /api/member/requests/{id}` — T2 정의, T11 소비. 경로 일치(라우터 prefix `+ "s/{request_id}"`).
- 백엔드 `GET /api/books/popular` — T1 정의, T4 `fetchPopular` 소비. 일치.
- 백엔드 `GET /api/books/{id}` — T1 정의, T4 `fetchBook` 소비. **`/popular` 뒤에 선언**해야 함을 T1 에 명시, 회귀 테스트도 있음.
- 백엔드 `?zone=` — T1 정의, T4 `CatalogQuery.zone` → T8 소비. 일치.

**4. 파일 소유권 (wave 내 충돌)**
- Wave 1: `books.py`+`schemas.py`(T1) / `delivery.py`(T2) / `main.py`(T3) — 무교차.
- Wave 4: 라우트별 1:1 소유(위 표). `home.tsx` 는 T7 단독, `member.ts` 는 T11 단독.
- `routeTree.gen.ts` 는 Wave 5(T12)에서만 커밋.

## codex 적대적 검토 반영 (2026-07-25)

`codex exec` 로 이 plan 을 적대적 검토해 Critical 4 · High 4 · Medium 4 를 받았고, 전부 코드로 재확인한 뒤 반영했다.

| # | 지적 | 반영 |
|---|---|---|
| C1 | tool 호출이 대화 루프가 아님 (이력·결과 왕복 없음) | T15 Step 2·4 — 이력 유지 + tool 결과를 모델에 되돌리고 후속 답변 |
| C2 | `resolveBook` 이 `rows[0]` 로 엉뚱한 책 선택 | T14 Step 3 — 유일 완전일치만 자동 선택, 모호하면 `choose` 로 후보 제시. 확인 카드는 **정본 도서**를 표시 |
| C3 | 로봇 정규식이 LLM 보다 먼저 돌아 회원 발화를 삼킴 | T15 — LLM 우선, 도구 미선택 시에만 정규식 (사용자 승인) |
| C4 | `?bookId=` 가 대출중 책의 예약을 건너뛰고, 3단계에서 중복 제출 가능 | T5 시트에서 대출중은 그 자리 예약 · T10 이 진입 시 상태 검사 · 3단계는 종착점 |
| H5 | tool 인자 미검증 (`"undefined"` 문자열 유입) | T14 Step 2·4 — zod 스키마로 검증, 실패 시 실행도 확인 카드도 없음 |
| H6 | `stream:false` UX 퇴행 + 연타 경쟁 | T15 Step 3·4b — 스트리밍 유지하며 청크에서 tool_calls 수집, 대기 중 입력 잠금 (사용자 승인) |
| H7 | `z.number()` 가 쿼리스트링을 못 받음 + 카탈로그 전체 조회 | T5 Step 2c `z.coerce.number()` · T10 이 `fetchBook` 단건 조회 (사용자 승인) |
| H8 | 지도가 여전히 200 권만 받아 거름 + 빈 결과를 실패로 오인 | T1 `zone` 필터 · T4 `fetchCatalogResult` · T8 서버측 필터 (사용자 승인) |
| M9 | Wave 4 에서 `home.tsx` 충돌, `routeTree.gen.ts` 공용, `git add -A` 위험 | 온보딩을 T7 로 이관 · T12 를 Wave 5 단독 · 모든 커밋에 파일 명시 |
| M10 | T5→T10 순서 모순 | search 계약을 T5 Step 2c 로 선점 |
| M11 | 「다음」이 우측에 붙음 (R22 위반) | T10 Step 4 — spacer 를 버튼 뒤로 |
| M12 | 도구 16개가 ellipsis, R73 결과 정보 부족 | T14 Step 2·5·6 — 16개 전부 작성, 결과에 접수번호·자리·상태 포함 |

## Execution Handoff

Plan complete and saved to `docs/agents/plan-customer-ui-revamp.md`.
