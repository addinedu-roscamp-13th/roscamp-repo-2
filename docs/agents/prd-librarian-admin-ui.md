# PRD — 사서(Admin) UI 개편

설계 배경/탐색 결과 전체: `docs/superpowers/specs/2026-07-24-librarian-admin-ui-redesign-design.md` 참고(중복 기술 안 함).

## Problem Statement

사서가 `aba_service` 관리자 화면(`/admin`, 로그인 후 접속)을 쓸 때, 대시보드와 개발센터만 한눈에 보기 좋고 나머지 화면(회원관리·도서관리·실시간 모니터링·보안·운영/작업지시)은 그렇지 않다.

- 수치가 그냥 텍스트 숫자로만 있어서 대시보드처럼 감이 안 온다.
- 회원/도서 목록은 정렬이 없거나(회원) 드롭다운으로만 가능(도서)해서, 원하는 기준으로 훑어보기 불편하다.
- 페이지 전체가 세로로 스크롤돼서 화면 하나에 다 안 들어온다.
- 실시간 모니터링 지도가 세로로 좁게 나오고, 로봇이 지금 무슨 작업을 하는지 한눈에 안 들어온다.
- 작업지시(이송) 화면에서 도서를 텍스트로 직접 입력해야 하고, 로봇 배차 시 지금 배차 가능한 로봇인지 구분이 안 된다.
- 데모/시연 시 일부 화면(특히 실시간 모니터링 우측 로봇 패널)이 비어 보인다.

## Solution

대시보드·개발센터는 그대로 두고, 나머지 admin 페이지를 대시보드와 동일한 UX 언어(화면 고정 카드그리드, 차트+숫자 병행, 헤더클릭 정렬)로 맞춘다. 대시보드에 이미 있는 시각화 컴포넌트(`MiniDonut`, `WeeklyTaskBars`, books.tsx의 가로스택막대)를 공용 컴포넌트로 추출해 재사용하고, 신규 정렬 컴포넌트를 books/members가 공유한다. 실시간 모니터링은 지도를 가로로 돌리고 작업 정보를 보강한다. 작업지시는 도서 선택을 팝업 방식으로, 로봇 배차는 가용 여부를 시각적으로 구분한다. demo seed를 보강해 모든 화면 상태가 시연 가능하게 만든다. 기능(API/데이터 흐름)은 바꾸지 않고 UI/UX 레이어만 개선한다.

## User Stories

1. As a librarian, I want each admin page to fit on one screen without page-level scrolling, so that I can see everything relevant without scrolling around.
2. As a librarian, I want overflow content (long lists) contained inside its own card section, so that the rest of the page stays visible while I scroll one part.
3. As a librarian, I want the top-of-page numbers on members/books/robots/security pages to be shown as charts (not just plain numbers), so that I can grasp proportions at a glance like on the dashboard.
4. As a librarian, I want to click a table column header on the members and books pages to sort by that column, and click again to reverse the order, so that I can quickly find what I'm looking for without a separate dropdown.
5. As a librarian, I want the member creation form to be in a popup dialog instead of always taking up space, so that the member list gets more room on screen.
6. As a librarian, I want to see a bar chart of member status (active/inactive) and a bar chart of loan status (borrowed/overdue/no loans), so that I understand member/loan health at a glance.
7. As a librarian, I want the real-time monitoring map laid out horizontally (matching the public library map's orientation), so that it uses screen space efficiently.
8. As a librarian, I want the real-time monitoring page to show each robot's current task type, requester, and leg progress (not just a raw task id), so that I know what each robot is actually doing.
9. As a librarian, I want robot status shown as a chart summary at the top of the monitoring page, so that I can see the fleet's overall health quickly.
10. As a librarian, I want demo/seed data to populate every page's states (including the monitoring page's robot panel) even when the real FMS fleet is small or offline, so that I can demo the system fully.
11. As a librarian, when creating a "이송(transfer)" task, I want to click the book field and pick from a searchable popup instead of typing, so that I reliably get an exact match and the pickup location auto-fills correctly.
12. As a librarian, I want unavailable books in that picker to be visually distinguished (not bold, not clickable) from available ones, so that I don't try to send a robot for a book that's already out.
13. As a librarian, when manually assigning a robot to a task, I want unavailable robots (busy, stale telemetry, or in error) to be visually distinguished (not bold, not clickable) from available ones, so that I don't assign work to a robot that can't take it.
14. As a librarian, I want the "분류(sort)" task kind to only require picking a robot (no location fields), since its pickup/dropoff are always the same fixed spot, so that creating this common task is one click faster.
15. As a librarian, I want the "짐꾼(porter)" task kind hidden from the admin task-creation UI (it's handled elsewhere, in libi_gui), so that I'm not offered an option I shouldn't use here.
16. As a librarian, I want the task queue/history table's status shown in Korean labels (not raw enum strings like EXECUTING) and the task id shortened, so that it's easier to read.
17. As a librarian, I want the books page's sort-by-dropdown replaced with clickable column headers, so that sorting works the same way as on the members page.
18. As a librarian, I want a stat summary chart at the top of the books page, so I can see inventory health at a glance like the dashboard's book card.
19. As a librarian, I want the security page laid out as a fixed-screen card layout with a chart summary of intrusion events, so that it matches the rest of the admin UI.
20. As a librarian, I want the operations pages (approvals, users, alerts) to use the same fixed-screen card layout as the rest of the admin, without their existing functionality changing, so that the whole admin area feels consistent.
21. As a developer, I want the dashboard page's visual output to be pixel-identical before and after refactoring its chart components into shared modules, so that the one page that already works well doesn't regress.

## Implementation Decisions

### 공용 컴포넌트 추출
- 대시보드의 도넛 차트(`MiniDonut`: recharts PieChart + 항상 병기되는 숫자 legend)와 스택 막대 차트(`WeeklyTaskBars`)를 공용 admin 컴포넌트 모듈로 추출한다. books.tsx의 기존 가로 스택 막대(서가 배치 현황에 쓰이는 것)도 같은 모듈로 추출해 members 페이지의 상태 막대(회원 활성/비활성, 대출 상태)에 재사용한다.
- 추출 후 대시보드 자체는 렌더링 결과가 이전과 동일해야 한다(시각적 회귀 없음).
- 정렬 가능한 테이블 헤더를 위한 공용 훅/컴포넌트를 추가한다: 클릭 시 없음→오름차순→내림차순 순환, 현재 정렬 상태를 아이콘으로 표시. books.tsx와 members.tsx가 공유한다.

### 회원 페이지
- 레이아웃을 대시보드와 같은 화면-고정 그리드(카드 내부 스크롤)로 재구성한다.
- 회원 생성 폼은 다이얼로그로 이동한다(항상 노출되던 것을 버튼 클릭 시 여는 방식으로).
- 상단 통계는 막대 차트 2개(회원 활성/비활성 비율, 대출 상태: 정상대출/연체/대출없음)와 숫자(누적 대출)로 구성한다.
- 회원 테이블에 헤더클릭 정렬을 추가한다.

### 도서 페이지
- 상단에 재고 상태 등 통계 차트를 추가한다.
- 기존 정렬 드롭다운을 제거하고 헤더클릭 정렬로 교체한다(공용 정렬 컴포넌트 재사용).
- 기존 서가 배치 차트는 유지하되 공용 차트 모듈에서 가져온다.

### 실시간 모니터링 페이지
- 지도를 세로(63:108)에서 가로(108:63)로 회전한다. 회전 변환은 공개 도서관 지도 컴포넌트에서 이미 사용자 확인을 거쳐 검증된 좌표 변환 규칙을 그대로 재사용한다(새로 유도하지 않음).
- 상단에 로봇 상태 요약을 차트(도넛)로 추가한다.
- 로봇별 작업 정보를 보강하기 위해, 기존에 로봇 목록만 가져오던 폴링에 작업 큐 조회도 함께 수행해 작업 id로 두 데이터를 연결한다. 작업 종류 라벨, 요청자, leg 진행을 로봇 카드에 표시한다. 작업 상태는 한글 라벨로, 진행률은 막대 시각화로 보여준다.
- 백엔드 로봇 목록 조회 API에, 대시보드가 이미 쓰고 있는 "실제 로봇 연결이 없을 때 데모 로봇 상태로 대체 응답" 방식과 동일한 fallback을 추가한다(현재는 대시보드 API에만 있고 모니터링 API에는 없어서, 데모 상태에서는 모니터링 페이지 우측 로봇 패널이 비어 보이는 문제가 있음).

### 보안 페이지
- 화면-고정 카드 레이아웃으로 재구성한다.
- 침입 이벤트 요약 수치를 차트로 보여준다(예: 확인/미확인 비율).
- 기존 기능(주야간 모드 토글, 스케줄 설정, 이벤트 확인/삭제, 영상 재생)은 그대로 유지한다.

### 운영 — 작업지시 페이지
- "짐꾼" 작업 종류는 화면에서만 숨긴다(선택 목록에서 제외). 백엔드의 작업 종류 정의는 바꾸지 않는다.
- "분류" 작업 종류는 출발지/목적지 입력 필드를 화면에서 제거하고, 로봇 선택만 남긴다. 제출 시 출발지/목적지 값은 고정된 기존 waypoint("테이블-1번-좌")로 자동 채워 전송한다.
- "이송" 작업의 도서 입력을, 기존 텍스트+자동완성 방식 대신 검색 가능한 팝업 다이얼로그로 바꾼다. 이 팝업은 이미 이 페이지가 불러와 둔 도서 목록 데이터를 그대로 쓰고(새 API 호출 추가하지 않음), 대출 가능 여부에 따라 선택 가능/불가능을 시각적으로 구분한다(불가능한 항목은 굵게 표시하지 않고 클릭 불가능하게 한다). 도서를 고르면 출발지가 자동으로 그 책의 서가로 설정되는 기존 동작은 그대로 유지한다.
- 로봇 배차 선택에서, 현재 다른 작업 중이거나 텔레메트리가 끊겼거나 오류 상태인 로봇은 흐리게 표시하고 선택할 수 없게 한다. 배차 가능한 로봇은 굵게 표시한다.
- 작업 큐/이력 테이블의 상태 값을 한글 라벨로 바꾸고, 작업 id는 축약해서 보여준다(전체 값은 마우스오버 등으로 확인 가능하게 한다).

### 운영 — 승인/사용자/알림 페이지
- 레이아웃만 화면-고정 카드 스타일로 정리한다. 이미 있는 수치가 있다면 차트로 보여줄 수 있는지 검토한다. 기존 기능/데이터 흐름은 바꾸지 않는다.

### Demo Seed
- 데모 시드 스크립트를 보강해, 실제 FMS 연결이 없거나 로봇 수가 적을 때도 모든 관리자 화면의 상태(특히 실시간 모니터링의 로봇 상태 다양성)가 눈에 보이도록 데이터를 채운다. 기존에 이미 채워지고 있는 대출/연체/승인/작업이력/침입이벤트 데이터는 유지한다.

## Testing Decisions

- 좋은 테스트란 구현 세부사항이 아니라 외부에서 관찰 가능한 동작(정렬 결과, 렌더링된 값, API 응답 형태)을 검증하는 것이다.
- 백엔드: 로봇 목록 조회 API에 추가하는 fallback 동작에 대해, 기존 대시보드 API의 유사 fallback 테스트를 선례로 삼아 같은 패턴으로 검증한다(FMS 연결 없음 → 데모 상태 반환, FMS 연결 있음 → 실제 스냅샷 반환).
- 프론트엔드: 정렬 컴포넌트/훅은 순수 로직이므로 단위 테스트로 오름차순/내림차순/토글 동작을 검증한다. 나머지 UI 변경은 프로젝트에 기존 프론트 테스트 관례가 없으므로, `npm run build`/`npm run lint` 통과와 화면 캡처를 통한 육안 확인으로 검증한다.
- 시각적 회귀: 대시보드 페이지는 차트 컴포넌트 추출 전후로 스크린샷을 비교해 동일함을 확인한다.

## Out of Scope

- 대시보드(`index.tsx`)와 개발센터(`dev/*`) 페이지의 UX/레이아웃 변경 — 시각화 로직 추출로 인한 내부 리팩터만 발생하며, 그 외 변경 없음.
- FMS/컨트롤러/AI 서비스 코드 변경 — `aba_service`만 대상.
- 백엔드 API 엔드포인트 경로, 요청/응답 스키마, 인증 방식 변경 — fallback 추가(로봇 목록 API) 외에는 API 계약을 바꾸지 않는다.
- "짐꾼" 작업 종류를 백엔드에서 완전히 제거하는 것 — libi_gui 쪽에서 쓰이므로 존치, admin UI에서만 숨김.
- 새로운 반납함/분류대 waypoint를 실제로 추가하는 것(로봇 하드웨어/네비게이션 설정 변경) — 기존 "테이블-1번-좌" waypoint를 임시로 재사용.
- `members.tsx`의 자체 fetch 헬퍼(`opsApi` 미사용)를 공용 API 클라이언트로 통합하는 리팩터.
- 회원/도서 외 다른 목록(예: 작업 큐, 승인 이력)에 헤더클릭 정렬을 추가하는 것 — 요청 범위는 회원/도서로 한정.

## Further Notes

- 지도 가로 회전 좌표 변환은 이미 `LibraryMap.tsx`에서 사용자 확인을 거쳐 검증된 공식이 있으므로, 실시간 모니터링 페이지에서 새로 유도하지 않고 그대로 재사용한다(잘못 유도하면 로봇 점이 벽 안쪽에 찍히는 문제가 생길 수 있음 — 반드시 그 기존 공식을 참고할 것).
- "분류" 작업의 고정 위치("테이블-1번-좌")는 실제 반납함/분류대 waypoint가 아직 없어서 정한 임시 대체 값이다. 나중에 전용 waypoint가 추가되면 그때 바꾸면 된다.
- 작업 도중 세션이 끊길 수 있다는 사용자 제약이 있어, 구현 단계에서 격리된 브랜치/worktree 안에 Task 단위 커밋 체크포인트를 남기는 방식으로 진행한다(브레이크다운 단계에서 wave/Task로 구체화).
