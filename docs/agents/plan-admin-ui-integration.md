# ABA 사서/관제 UI 리디자인 + 연동 통합 테스트 + 로그삭제/회원관리

## Context

사서용 admin UI(`localhost:3000/admin`, `aba_service/frontend`)가 배치·직관성이 떨어짐. 동시에
UI↔DB↔UI 연동(대출·이송요청·task분배·회원관리·재고상태)이 실제로 맞물려 도는지 검증이 필요하고,
기존 connectivity 하네스는 현재 코드에 없는 endpoint를 찌르는 낡은 스냅샷이라 신뢰 불가. 목표: 기능을
유지한 채 admin 쉘+핵심페이지를 재배치하고, 빠진 기능(회원 CRUD, 로그 개별삭제)을 채우고, 핵심
비즈니스 흐름을 백엔드 통합테스트로 못박는다. codex 설계검증(5단계)에서 나온 레이스/부활버그/하드삭제
위험 지적을 전부 아래 태스크에 반영했음 — Task 텍스트가 최종 스펙이다.

## 탐색으로 확정된 사실 (재작업 금지 — 이미 존재)

- **"대출중 책 이송요청 금지" 규칙 = 이미 존재/작동**: `aba_service/backend/app/routers/delivery.py:90-103`
  (`_get_requestable_book`) + `approvals.py:106-110` + `circulation.py:137-138`. 신규구현 아님, 검증만.
- **재고 뱃지(가능/대출중) = 실제 데이터**: `cb_books.in_stock`. `circulation.borrow()`/`return_loan()`만 뒤집음.
- **회원관리 CRUD 없음**: `circulation.py:56` 목록조회 + `member_auth.py` 본인 login/me 뿐.
- **로그 삭제 endpoint 없음**: `cb_task_logs`/`cb_robot_control_logs`/`cb_intrusion_events`/
  `cb_delivery_requests` 목록은 있지만 DELETE 없음.
- **`GET /api/admin/ops/logs`가 매 호출마다 `_sync_logs()`로 FMS 종료작업을 재수입** — 이게 삭제 후
  부활 버그의 원인 (Task 3 참고).
- **admin 쉘**: `AdminShell.tsx` 단일파일, 손수 만든 사이드바, 하드코딩 hex. 미사용 shadcn
  `components/ui/sidebar.tsx`(23K) 존재.
- **테스트 기반**: 백엔드 pytest 존재(`aba_service/backend/tests/`, conftest에 `FakeFms`/SQLite StaticPool).
  프론트 테스트 0개.

## Global Constraints (모든 Task 공통, reviewer가 그대로 대조)

1. **인증**: 신규/변경 endpoint 전부 `Depends(get_current_admin)` 유지. `AdminRole` 세분화나
   `/api/robot/execute`·`/history`의 기존 인증부재는 이번 범위 밖 — 건드리지 않는다.
2. **비밀번호 해싱**: 신규 코드에서 비밀번호를 직접 해싱하지 말고 `member_security.py`(또는 그 안의
   해싱 함수)를 재사용한다.
3. **DB 스키마 변경**은 SQLAlchemy 모델에 컬럼 추가 + `Base.metadata.create_all`(현재 방식, 마이그레이션
   프레임워크 없음) — 새 Alembic 등 도입 금지.
4. **테스트**: 모든 백엔드 Task는 TDD로, 기존 `aba_service/backend/tests/conftest.py`의 SQLite
   `StaticPool` + `FakeFms` 패턴을 그대로 재사용한다(새 테스트 인프라 만들지 않음). 완료 보고 시 이
   테스트들은 SQLite 기반 로직/회귀 테스트이지 MariaDB 동시성 증명이 아니라는 점을 report에 명시한다
   (과장 금지).
5. **API 클라이언트(⚠️ 정정)**: 이 리포엔 통일된 client 파일이 없다 — 페이지마다 기존 관례가 다르다:
   `books.tsx`→`books-api.ts`, `alerts.tsx`/`approvals.tsx`/`security.tsx`→`ops-api.ts`,
   `members.tsx`→자기 파일 안 로컬 `api()` 헬퍼. **각 페이지의 기존 관례를 그대로 따른다** — 새 공유
   client 파일을 만들거나 다른 페이지 관례로 통일하지 않는다. 각 Task 텍스트에 정확한 대상이 명시돼
   있다.
6. **UI 프레임워크**: React 19 + TanStack Router(파일기반, `routeTree.gen.ts` 직접 수정 금지 — 라우터가
   자동생성) + shadcn/ui(new-york) + Tailwind v4. 새 UI 라이브러리 추가 금지.
7. **범위 밖(명시)**: 회원 self-register, 비번리셋/재활성 API, 로봇 auto-auction 배차 로직 변경,
   `clip_path` 실파일 삭제, MariaDB 전용 CI 티어.
8. **디자인 스킬(UI 레이아웃 Task 전용 — Task 9/10/12/13)**: 배치를 바꾸기 전에 먼저
   `redesign-existing-projects`(audit-first — 기존 기능/데이터흐름부터 파악) 스킬을 적용하고, 실제
   비주얼/배치 결정에는 `design-taste-frontend` 스킬을 따른다. 두 스킬 다 이 세션에 이미 설치돼 있는
   기존 스킬이다(find-skills로 새로 설치하는 임시 스킬 아님).

---

## Task 1: 대출/승인 재고 체크를 atomic UPDATE로 교체 (레이스 수정)

**배경**: codex 설계검증 지적 — `circulation.borrow()`와 `approvals.py`의 승인 로직이 둘 다
`if not book.in_stock` 체크 후 나중에 `book.in_stock = False`를 쓰는 read-then-write라서, 두 세션이
동시에 같은 책을 대출/승인하면 둘 다 성공할 수 있다.

**파일**: `aba_service/backend/app/routers/circulation.py`, `aba_service/backend/app/routers/approvals.py`,
`aba_service/backend/tests/` (새 테스트 파일 또는 기존 확장).

**요구사항**:
- `circulation.py`의 `borrow()`(현재 라인 124-152 부근): `book.in_stock`을 읽고 나중에 쓰는 대신,
  `db.execute(update(Book).where(Book.id == book_id, Book.in_stock == True).values(in_stock=False))`
  형태의 원자적 UPDATE를 실행하고 `result.rowcount == 0`이면 409 "이미 대출 중인 도서입니다"를 던진다.
  book/member 존재 확인(404)은 기존대로 먼저 한다.
- `approvals.py`의 승인(`approve`, 현재 라인 95-121 부근)에서 재고 재확인하는 부분도 동일한
  atomic UPDATE...WHERE 패턴으로 바꾼다 — rowcount 0이면 409 "그 사이 대여된 도서입니다. 반려하고
  예약을 안내하세요."(기존 메시지 유지).
- `return_loan()`은 read-then-write 위험이 없음(단일 세션이 반납 확정) — 바꾸지 않는다.
- 테스트(TDD, 먼저 작성): (a) 정상 대출 1회 성공, (b) 이미 `in_stock=False`인 책에 `borrow()` 재호출 시
  409(rowcount 0 경로가 실제로 타는지 — mock 없이 실제 UPDATE 결과로 확인), (c) 승인 재확인 409 경로도
  동일하게 검증. 기존 `test_delivery_approval.py`가 있으면 그 파일에 이어서 추가.

**완료 조건**: `cd aba_service/backend && .venv/bin/pytest -q` 신규 테스트 포함 전부 통과.

---

## Task 2: 회원관리 CRUD (사서용)

**배경**: 사서가 회원을 생성/수정/비활성화하는 endpoint가 없다(목록조회+본인로그인만). 대여현장에서
신규회원 등록·정보수정·탈퇴처리가 안 됨.

**파일**: `aba_service/backend/app/routers/circulation.py`, `aba_service/backend/tests/`.

**요구사항** (전부 `Depends(get_current_admin)`, `circulation.py` 라우터에 추가):
- `POST /api/admin/circulation/members` — body: `username`, `full_name`(optional), `password`.
  `username` 중복이면 409 "이미 존재하는 아이디입니다". 비밀번호는 Global Constraint 2의 해싱 함수로
  저장. 응답은 기존 `MemberRow` 형태(`active_loans`/`total_loans`는 0으로).
- `PATCH /api/admin/circulation/members/{id}` — body: `full_name`(optional), `is_active`(optional).
  회원 없으면 404.
- `DELETE /api/admin/circulation/members/{id}` — soft delete(`is_active = False`로 세팅, 행 자체는
  안 지움). 아래 중 하나라도 있으면 409 "처리 중인 대출/요청/예약이 있어 비활성화할 수 없습니다":
  - `Loan.status == "borrowed"` 존재
  - `DeliveryRequest.approval == "PENDING_APPROVAL"` 존재
  - `Reservation.status == "waiting"` 존재
  회원 없으면 404. 이미 비활성 상태면 그대로 200(idempotent).
- 재활성화 API, 비번리셋 API는 만들지 않는다(범위 밖).
- 테스트(TDD): 생성 성공, username 중복 409, 수정 성공, 대출/요청/예약 각각 있을 때 비활성화 409,
  아무것도 없을 때 비활성화 성공, 이미 비활성 회원 재비활성화 시 200.

**완료 조건**: pytest 신규 테스트 포함 전부 통과.

---

## Task 3: 작업로그(`cb_task_logs`) soft-delete — 부활 버그 수정 포함

**배경**: codex 지적 — `GET /api/admin/ops/logs`가 매 호출마다 `_sync_logs()`로 FMS 종료작업을
task_id 부재 여부로 재수입한다. 단순 하드 DELETE를 추가하면 지운 행이 다음 조회에 그대로 부활한다.

**파일**: `aba_service/backend/app/models.py`(TaskLog 모델), `aba_service/backend/app/routers/ops.py`
(`_sync_logs()`, `GET /logs`), `aba_service/backend/tests/`.

**요구사항**:
- `TaskLog` 모델(`models.py:369` 부근)에 `hidden: bool = Column(Boolean, default=False, nullable=False)`
  컬럼 추가.
- `_sync_logs()`의 "이미 cb_task_logs에 있는지" 판단은 **hidden 여부와 무관하게 task_id 존재만으로**
  판단하도록 유지/확인한다(hidden=True인 행도 "존재"로 취급해야 재수입 안 됨) — 코드를 읽고 이미 그렇게
  동작하면 그대로 두고, task_id를 지우고 있었다면 hidden 처리로 바꾼다.
- `GET /api/admin/ops/logs`의 목록 쿼리에 `WHERE hidden == False` 조건 추가.
- `DELETE /api/admin/ops/logs/{id}` 신규 — `Depends(get_current_admin)`. 행을 hard-delete하지 않고
  `hidden = True`로 세팅. 없으면 404.
- 테스트(TDD, 부활 버그를 실제로 재현·수정 검증하는 게 핵심): (a) FMS mock에 종료 task 하나 만들고
  `GET /logs` 호출 → `_sync_logs()`가 행을 만듦 → 목록에 보임. (b) `DELETE /logs/{id}` 호출.
  (c) **다시 `GET /logs` 호출** — 목록에 없어야 함(이게 핵심 assertion, 단순 delete 응답만 보면 버그를
  못 잡는다). (d) DB에는 여전히 `hidden=True`로 행이 남아있는지 직접 쿼리로 확인(감사 목적 보존).

**완료 조건**: pytest 신규 테스트(부활 재현 포함) 전부 통과.

---

## Task 4: 로봇제어로그·침입이벤트 삭제 (하드 삭제)

**배경**: `cb_robot_control_logs`, `cb_intrusion_events` 둘 다 FMS 재동기화 대상이 아닌 순수 로그라
Task 3 같은 soft-delete 장치 불필요 — 바로 하드 DELETE.

**⚠️ 컨트롤러 확인(구현 전)**: `cb_robot_control_logs`는 **admin 페이지 어디에도 표시되지 않는다** —
프론트에서 이걸 참조하는 곳은 `chat.tsx`(회원 화면, `/api/robot/history`) 하나뿐이고 그건 admin
리디자인 스코프 밖이다. 그래서 로봇제어로그 삭제는 **백엔드 endpoint만** 만든다 — UI 연결 없음(연결할
화면이 없어서다, 빠뜨린 게 아니다). `cb_intrusion_events`는 `security.tsx`(야간보안 페이지, `ops-api.ts`
사용)에서 표시되므로 그쪽은 Task 8/12에서 UI까지 연결한다.

**파일**: `aba_service/backend/app/routers/robot_control.py`, `aba_service/backend/app/routers/ops_extra.py`
(`IntrusionEvent`가 실제로 여기 있음, grep으로 재확인 완료), `aba_service/backend/tests/`.

**요구사항**:
- `DELETE /api/admin/robot-control-logs/{id}`(기존 `robot_control.py` prefix 관례를 따라 실제 경로 확정) —
  `Depends(get_current_admin)`, 행 hard-delete, 없으면 404. **UI 연결 없음(위 사유).**
- `DELETE /api/admin/security/events/{id}`(`ops_extra.py`의 기존 prefix 관례 따름) — `Depends(get_current_admin)`,
  행 hard-delete, 없으면 404. `clip_path`가 가리키는 파일 자체는 지우지 않는다(범위 밖, 그대로 둔다).
- 테스트(TDD): 각각 생성→삭제→목록에서 사라짐→재삭제 시 404.

**완료 조건**: pytest 신규 테스트 전부 통과.

---

## Task 5: 대여승인 이력(`cb_delivery_requests`) 삭제 — 상태 가드

**배경**: codex 지적 — 이 테이블은 단순 로그가 아니라 member/book/승인결정/진행중 FMS task를 잇는
링크다. 하드 삭제를 아무 때나 허용하면 로봇이 계속 도는데 회원 화면에서만 사라지는 상황이 생긴다.

**파일**: `aba_service/backend/app/routers/approvals.py`, `aba_service/backend/tests/`.

**요구사항**:
- `DELETE /api/admin/ops/approvals/{id}` 신규 — `Depends(get_current_admin)`.
- 삭제 허용 조건: `approval == "REJECTED"` **이거나**, `approval == "APPROVED"`이면서 연결된 FMS task가
  종료 상태(approvals.py가 이미 갖고 있는 fms task 상태 조회 로직 재사용 — 새 FMS 클라이언트 코드
  만들지 말 것)인 경우.
- 그 외(`PENDING_APPROVAL`, 또는 `APPROVED`인데 FMS task가 아직 진행중) → 409
  "진행 중인 요청은 삭제할 수 없습니다".
- 없으면 404. 실제 hard-delete(soft 아님 — 이 표는 부활 버그가 없으므로 Task 3과 다르게 진짜 삭제).
- 테스트(TDD): REJECTED 삭제 성공, PENDING_APPROVAL 삭제 시도 409, APPROVED+진행중 FMS task 409,
  APPROVED+종료된 FMS task 삭제 성공.

**완료 조건**: pytest 신규 테스트 전부 통과.

---

## Task 6: task 분배 테스트 인프라 보강 + 관측 동작 고정

**배경**: codex 지적 — 현재 `FakeFms`(conftest.py)에 `assign_order`가 없어 task 분배 흐름을 테스트할
수 없다. 로봇 미지정시 PENDING 방치는 "관측된 동작"이지 보장된 스펙이 아니므로 그렇게 문서화한다.

**파일**: `aba_service/backend/tests/conftest.py`, `aba_service/backend/tests/` (신규 테스트).

**요구사항**:
- `FakeFms`에 `assign_order(task_id, robot)` 스텁 추가(성공 응답 리턴, 호출 기록 저장해서 테스트에서
  assert 가능하게).
- 테스트: 로봇을 지정해 `POST /api/admin/ops/tasks` 호출 → `FakeFms.assign_order`가 올바른 인자로
  호출됐는지 확인. 로봇 미지정 호출 시 `assign_order`가 호출되지 않고 task가 생성만 되는지 확인 —
  테스트 docstring/주석에 "이것은 현재 관측된 동작이며 운영상 의도된 스펙인지는 미확정"이라고 명시한다
  (동작을 스펙으로 확정 짓지 않는다).

**완료 조건**: pytest 신규 테스트 전부 통과.

---

## Task 7: connectivity 하네스 read-only 스모크로 축소

**배경**: `tests/connectivity/run.py`가 현재 코드에 없는 endpoint(`/api/control/goto`@aba_service,
`/api/db/ping`, `/api/perception/last`, aba_service 웹소켓)를 찌르는 낡은 스냅샷. codex 지적 —
mutating 워크플로로 바꾸면 실제 로봇 task를 발생시킬 위험이 있으니 read-only로 좁힌다(mutating 흐름
검증은 Task 1~6의 pytest가 이미 커버).

**파일**: `tests/connectivity/run.py`, `tests/connectivity/report.json`(재실행 결과로 갱신은 6단계
수동 스모크에서, 이 Task는 스크립트 자체만 고침).

**요구사항**:
- 존재하지 않는 edge(1a `/api/control/goto`@:8000, 2a aba_service ws, 3/4 `/api/db/ping`,
  6/7 `/api/perception/last`)를 제거하거나, 실제 대응 endpoint가 있으면 그걸로 교체한다.
- 남기는/새로 넣는 edge는 전부 **read-only**(GET, 부수효과 없는 것)만: 양쪽 `/api/health`,
  FMS `GET /api/control/state`(이미 살아있음, run.py:91-98 유지), FMS `ws /api/control/ws/state`
  (이미 살아있음, run.py:112-122 유지), aba_service `/api/admin/auth/login` 스모크(로그인만, 뭔가를
  만들지 않음) 정도로 구성.
- 존재하지 않는 게 확인된 UDP/TCP AI perception 체인(6/7)은 제거하거나 명시적 `PENDING(사유: endpoint
  없음)`으로 마킹 — FAIL로 남기지 않는다.
- `record()`/report.json 포맷(run.py:43-47)은 그대로 유지, edge 정의만 바꾼다.

**완료 조건**: 서비스 미기동 상태에서도 스크립트가 문법 오류 없이 로드됨(`python -c "import ast;
ast.parse(open('tests/connectivity/run.py').read())"`). 실제 재실행 검증은 수동 스모크 단계(계획 하단
Verification)에서.

---

## Task 8: `ops-api.ts`에 로그 삭제 client 함수 추가 (⚠️ 계획 정정, 아래 참고)

**⚠️ 컨트롤러 정정(구현 전 실제 코드 확인 결과, 최초 계획 텍스트가 틀렸음)**: 이 리포엔 프론트 API
호출에 통일된 "admin-api.ts 하나"가 없다 — 페이지마다 다른 client를 쓴다:
- `books.tsx` → `books-api.ts`
- `alerts.tsx`/`approvals.tsx`/`security.tsx` → **`ops-api.ts`**(`ops`/`opsApi` export)
- `members.tsx` → **자기 파일 안에 로컬 `api()` 헬퍼**(공유 client 없음, `TOKEN_KEY = "labi.adminToken"`)
- `chat.tsx`만 `/api/robot/history`(`RobotControlLog`)를 참조하는데, 이건 **회원 화면**이라 admin
  리디자인 스코프 밖 — **admin 페이지 중 로봇제어로그를 보여주는 곳이 없다.**

그래서 이 Task는 원래 "admin-api.ts 확장"이 아니라 **`ops-api.ts`에 3개 삭제 함수만 추가**하는 걸로
좁힌다. 회원 CRUD 함수는 여기 안 넣는다 — Task 11이 `members.tsx` 자기 로컬 `api()`에 직접 추가한다
(그게 이 페이지의 기존 관례). 로봇제어로그 삭제 함수도 여기 안 넣는다 — 보여줄 화면이 없어서 UI
연결 자체가 없다(Task 4 참고).

**파일**: `aba_service/frontend/src/lib/ops-api.ts`.

**요구사항**: 기존 `ops`/`opsApi` 함수들과 동일한 패턴으로 추가:
- `deleteTaskLog(id)` → Task 3의 `DELETE /api/admin/ops/logs/{id}` (alerts.tsx에서 씀).
- `deleteIntrusionEvent(id)` → Task 4의 보안이벤트 삭제 endpoint (security.tsx에서 씀).
- `deleteApprovalHistory(id)` → Task 5의 `DELETE /api/admin/ops/approvals/{id}` (approvals.tsx에서 씀).
- 각 함수의 에러 처리(409/404)는 `ops-api.ts`의 기존 에러 처리 관례를 그대로 따른다.
- **UI 연결 없이 client 함수만** 추가한다 — 실제 화면 배선은 Task 12/13에서.

**완료 조건**: `cd aba_service/frontend && bun run build` 통과(타입 에러 없음).

---

## Task 9: AdminShell → shadcn sidebar 프리미티브로 재배치 (동작 보존)

**배경**: `AdminShell.tsx`가 손수 만든 사이드바 + 하드코딩 hex를 쓴다. 이미 있는(미사용) shadcn
`components/ui/sidebar.tsx`로 교체하면 접힘/키보드 네비/툴팁을 얻는다. codex 지적 — 이건 "API
시그니처만 유지하면 공짜"가 아니라 실제 동작 변경이므로, 기존 동작을 하나하나 보존해야 한다.

**파일**: `aba_service/frontend/src/components/admin/AdminShell.tsx`,
`aba_service/frontend/src/components/ui/sidebar.tsx`(사용만, 수정 안 함),
`aba_service/frontend/src/styles.css`(하드코딩 hex를 기존 CSS 변수 토큰으로).

**요구사항**:
- `NAV_GROUPS` 데이터 구조(운영/관리/개발센터 3그룹, 각 items)는 그대로 유지 — shadcn sidebar의
  `SidebarGroup`/`SidebarMenu` 등으로 렌더링만 바꾼다.
- **보존해야 하는 기존 동작**(reviewer가 이 목록으로 대조):
  1. 모바일에서 슬라이드오버 열기/닫기(`mobileOpen` 상태, 배경 클릭시 닫힘)가 shadcn sidebar의
     `Sheet` 기반 모바일 모드로 동등하게 동작.
  2. 현재 라우트가 속한 그룹은 자동으로 펼쳐짐(`groupActive` 로직 동등하게 유지).
  3. active 항목 표시(현재는 왼쪽 오렌지 레일 + 배경) — 정확히 같은 시각효과 아니어도 되지만
     "지금 어디 있는지 한눈에 보임"은 유지.
  4. 로그아웃 버튼, breadcrumb, 로그인한 사용자 이름 표시(topbar) 전부 그대로.
  5. `Toaster` 마운트 위치 유지.
- 하드코딴 `#EEF1F6`/`#F8F9FB`/`orange-500` 등은 `styles.css`의 기존 CSS 변수(`--paper`, `--ink`,
  accent 등)와 조화되는 토큰으로 바꾼다 — 완전히 새 팔레트를 만들지 않는다(기존 member 앱과 톤 통일).
- 라우트 파일(`admin/_authed/*.tsx`)들의 `<AdminShell title=...>` 사용법(props)은 바꾸지 않는다.

**완료 조건**: `bun run build` 통과. 이 Task는 수동 스모크(6단계 마지막)에서 위 5가지 보존 항목을
브라우저로 직접 확인하는 것까지 self-review에 포함한다.

---

## Task 10: `books.tsx` 레이아웃 개선 (스크린샷 대상 페이지)

**배경**: 사서가 "배치가 마음에 안 든다"고 지목한 페이지. 기능(검색/등록/수정/삭제/재고필터) 불변,
배치·밀도만 개선.

**파일**: `aba_service/frontend/src/routes/admin/_authed/books.tsx`.

**요구사항**:
- 상단 등록 폼(제목/저자/분야/서가/청구기호/표지/재고)과 하단 목록(검색+분야필터+테이블)의 시각적
  구분을 명확히 하고, 재고 컬럼(가능/대출중 배지)을 더 눈에 띄게(색상 배지 강조).
- 기존 API 콜(`fetchBooks`/등록/수정/삭제 함수 등)은 시그니처·호출부 불변 — 배치만 바꾼다.
- 반응형(모바일 폭에서 폼 필드 줄바꿈 등) 유지.

**완료 조건**: `bun run build` 통과.

---

## Task 11: `members.tsx` 레이아웃 개선 + 회원 CRUD 배선 (client 함수 포함)

**배경**: Task 2(백엔드)에서 만든 회원 생성/수정/비활성화를 실제 화면에 연결.

**⚠️ 컨트롤러 확인(구현 전)**: `members.tsx`는 공유 client 파일(admin-api.ts 등)을 쓰지 않고 **자기
파일 안에 로컬 `api<T>(path, init)` 헬퍼**를 이미 갖고 있다(`TOKEN_KEY = "labi.adminToken"`,
`localStorage`에서 읽어 Bearer 헤더 붙임 — 기존 `members()`/`loans()`/`available-books`/`borrow`/`return`
호출이 전부 이 헬퍼를 씀). 이 파일만의 확립된 패턴이니 **새 client 파일을 만들지 말고 이 로컬 `api()`를
그대로 재사용**해서 3개 함수 호출(생성/수정/비활성화)을 추가한다 — 이게 Task 8이 커버 못 하는 부분이다.

**파일**: `aba_service/frontend/src/routes/admin/_authed/members.tsx`.

**요구사항**:
- `api()` 헬퍼로 `POST /api/admin/circulation/members`, `PATCH .../members/{id}`,
  `DELETE .../members/{id}` 호출부 추가(기존 `borrow`/`return` 호출과 동일한 방식).
- 회원 생성 폼(username/full_name/password), 목록의 각 행에 수정(다이얼로그)·비활성화 버튼 추가.
- 비활성화는 `components/ui/alert-dialog`로 확인 후 실행, 409 응답(처리중인 대출/요청/예약)은
  toast(sonner)로 에러 메시지 그대로 표시.
- 레이아웃은 대출/반납 처리 UI 옆에 회원 관리 섹션을 자연스럽게 배치(기존 대출/반납 기능 위치·동작 불변).

**완료 조건**: `bun run build` 통과.

---

## Task 12: `tasks.tsx` + `approvals.tsx` + `security.tsx` 레이아웃 개선 + 삭제 배선

**배경**: 작업지시(`tasks.tsx`), 대여승인(`approvals.tsx`), 야간보안(`security.tsx`) 세 화면 배치 개선 +
Task 5/8의 승인이력 삭제를 `approvals.tsx`에, Task 4/8의 침입이벤트 삭제를 `security.tsx`에 연결.
(`tasks.tsx`/`approvals.tsx`/`security.tsx` 전부 `@/lib/ops-api`의 `ops`/`opsApi` export를 이미 씀 —
같은 client 파일이라 한 Task로 묶는다.)

**파일**: `aba_service/frontend/src/routes/admin/_authed/tasks.tsx`,
`aba_service/frontend/src/routes/admin/_authed/approvals.tsx`,
`aba_service/frontend/src/routes/admin/_authed/security.tsx`.

**요구사항**:
- 세 페이지 모두 기존 API 콜 시그니처 불변, 배치·정보밀도만 개선(현장 워크스테이션 벤치마킹 —
  상태 우선 정렬, 액션 버튼 근접 배치).
- `approvals.tsx` 이력 목록의 각 행에 삭제 버튼 추가, `ops-api.ts`의 `deleteApprovalHistory` 호출,
  409(진행중이라 삭제불가)는 alert-dialog 열기 전에 이미 알 수 없으므로 실패시 toast로 안내.
- `security.tsx` 침입이벤트 목록의 각 행에 삭제 버튼 추가, `ops-api.ts`의 `deleteIntrusionEvent` 호출.

**완료 조건**: `bun run build` 통과.

---

## Task 13: 대시보드(`index.tsx`) + `alerts.tsx` 레이아웃 개선 + 작업로그 삭제 배선

**배경**: 대시보드 KPI/차트 배치 개선 + Task 3/8의 작업로그 삭제를 `alerts.tsx`에 연결.

**파일**: `aba_service/frontend/src/routes/admin/_authed/index.tsx`,
`aba_service/frontend/src/routes/admin/_authed/alerts.tsx`.

**요구사항**:
- `index.tsx`: 메트릭 카드/차트/테이블 배치 개선, 기존 데이터 소스(React Query) 불변.
- `alerts.tsx`: 로그 목록 각 행에 삭제 버튼(alert-dialog 확인) 추가, `deleteTaskLog` 호출 후 목록
  refetch(삭제한 행이 다시 안 보이는지 — Task 3에서 서버가 이미 보장, 프론트는 단순 refetch만).

**완료 조건**: `bun run build` 통과.

---

## Verification (전체, 실제 실행 — 완료주장 전 이 메시지에서 출력 확보)

1. 백엔드 pytest: `cd aba_service/backend && .venv/bin/pytest -q` (Task 1~6 신규 테스트 포함 전부 통과).
2. FMS pytest 회귀: `cd aba_fms_service/backend && .venv/bin/pytest -q` (기존 깨지지 않음 — 이번
   범위는 FMS 코드를 안 건드리므로 원래 통과하던 그대로여야 함).
3. 프론트 빌드/린트: `cd aba_service/frontend && bun run build`.
4. 수동 스모크: 서비스 기동 후 `localhost:3000/admin`에서 Task 9의 5가지 보존항목 + 재고뱃지 +
   회원CRUD + 로그삭제(부활 안 하는지 새로고침으로 재확인) 육안 확인.
5. connectivity: 서비스 기동 후 `.venv/bin/python tests/connectivity/run.py` — Task 7로 좁힌 edge
   전부 PASS(또는 명시 PENDING).

## 워크플로 후속 (Task 1~13 전부 완료 후)
7단계 self-review(matt-pocock code-review) → 8단계 codex 최종검토(코드품질 + 요구사항 커버리지,
`codex exec` 폴백) → 9단계 반영.
