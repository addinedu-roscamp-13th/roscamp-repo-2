# 사서(Admin) UI 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aba_service/frontend`의 사서 admin(`/admin`)에서 대시보드·개발센터를 제외한 나머지 페이지(회원/도서/실시간모니터링/보안/운영)를 대시보드 수준 UX(화면고정 카드그리드 + 차트 시각화 + 헤더클릭 정렬)로 개편한다. 기능(API/데이터 흐름)은 유지, UI 레이어만 변경.

**Architecture:** 대시보드의 시각화 컴포넌트(`MiniDonut`/`WeeklyTaskBars`)와 books.tsx의 가로스택막대를 `components/admin/charts.tsx`로 추출해 공용화. 헤더클릭 정렬은 `components/admin/useSortableTable.tsx` 훅으로 공용화. 각 페이지는 이 공용 모듈을 가져다 화면-고정 레이아웃으로 재구성한다. 백엔드는 `/api/admin/ops/robots`에 `/dashboard`와 동일한 DemoRobotState fallback만 추가.

**Tech Stack:** React 19, TanStack Router/Start, shadcn/ui, Tailwind v4, recharts, FastAPI, SQLAlchemy, pytest.

## Global Constraints

- 대시보드(`routes/admin/_authed/index.tsx`)와 개발센터(`routes/admin/_authed/dev/*`)는 시각적으로 무변경이어야 한다 — index.tsx는 로직 추출 후에도 렌더링 결과가 이전과 동일해야 한다.
- `aba_service`만 수정한다. `aba_fms_service`/`aba_controller`/`aba_ai_service`는 건드리지 않는다.
- API 엔드포인트 경로·요청/응답 스키마·인증 방식은 바꾸지 않는다(예외: Task 1의 `/robots` 응답에 데모 fallback 로직만 추가, 스키마 자체는 동일).
- `members.tsx`의 자체 `api()` 헬퍼(️`opsApi` 미사용)는 리팩터하지 않는다 — 그대로 둔 채 필요한 기능만 얹는다.
- 백엔드 `TASK_KINDS`(`ops.py`)는 수정하지 않는다 — porter 숨김은 프론트엔드 필터로만 처리한다.
- 각 Task 완료 시 커밋한다(작업 중 세션이 끊길 수 있다는 사용자 제약 — 체크포인트를 자주 남긴다).
- 프론트엔드에는 기존 테스트 스위트가 없다 — 프론트 Task의 검증은 `npm run build` + `npm run lint` 통과로 한다(단위 테스트를 새로 지어내지 않는다). 백엔드는 기존 pytest 관례(`tests/conftest.py`의 `client`/`admin_auth`/`db_session` 픽스처)를 따른다.

참고 문서(중복 기술 안 함): 설계 `docs/superpowers/specs/2026-07-24-librarian-admin-ui-redesign-design.md`, PRD `docs/agents/prd-librarian-admin-ui.md`.

---

## Wave 편성

**Wave 1 (병렬 4, 서로 다른 파일)**: Task 1(백엔드 /robots fallback), Task 2(charts.tsx+index.tsx), Task 3(useSortableTable.tsx, 신규 파일), Task 11(자동 마이그레이션 실행기)
**Wave 2 (Wave 1 완료 후, 병렬 7, 서로 다른 파일)**: Task 4(members.tsx), Task 5(books.tsx), Task 6(robots.tsx), Task 7(security.tsx), Task 8(tasks.tsx+신규 다이얼로그), Task 9(approvals/users/alerts), Task 12(데모 데이터 무결성 — Task 11에 의존)
**Wave 3 (순차)**: Task 10(전체 브랜치 검증 — build/lint/pytest/스크린샷)

> Task 11/12는 `/codex:adversarial-review`가 이번 세션 시작 전부터 이미 uncommitted 상태였던 작업트리(내 UI개편 plan 범위 밖 파일들)에서 찾은 critical/high 발견 3건을 처리한다 — 사용자 확인 후 이 plan에 편입됨. UI 개편(Task 1~10)과는 독립적인 데이터 안전성 이슈다.

병렬 Wave는 각 Task를 별도 git worktree에서 진행할 것(`superpowers:using-git-worktrees`) — 같은 Wave 안 Task들은 파일셋이 겹치지 않는다.

---

### Task 1: 백엔드 — `/api/admin/ops/robots` 데모 fallback

**Files:**
- Modify: `aba_service/backend/app/routers/ops.py:250-258`
- Test: `aba_service/backend/tests/test_ops_robots_fallback.py` (신규)

**Interfaces:**
- Consumes: 기존 `fms_client.fleet_snapshot()` (`(ok: bool, snap: dict)`), `DemoRobotState` 모델(`robot: str, state: str`).
- Produces: `GET /api/admin/ops/robots` 응답의 `robots` 배열 — 로봇이 하나도 안 잡히면(연결 여부 무관) `DemoRobotState` 테이블 내용으로 채운 `RobotRow` 셰이프(`name,x,y,state,battery,busy,stale,task_id,task_state,progress,goal_vertex`) 객체 리스트. Wave 2 Task 6(robots.tsx)이 이 응답을 그대로 소비한다 — 필드명/타입 변경 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

`aba_service/backend/tests/test_ops_robots_fallback.py`:

```python
"""/api/admin/ops/robots — FMS 로봇이 하나도 없을 때 DemoRobotState 로 대체하는지.

/dashboard 가 이미 쓰는 것과 같은 fallback 을 여기도 추가한다(실시간 모니터링
페이지가 데모 환경에서 빈 화면으로 보이지 않게). 진짜 텔레메트리가 하나라도
있으면 이 분기를 절대 타지 않는다는 것도 함께 검증한다.
"""

from app.models import DemoRobotState
from app.routers import ops

ROBOTS = "/api/admin/ops/robots"


def test_fms_로봇_없으면_데모_상태로_대체(client, admin_auth, db_session, monkeypatch):
    monkeypatch.setattr(
        ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": [], "plugins": {}})
    )
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.add(DemoRobotState(robot="arm1", state="ERROR"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["linked"] is True
    by_name = {r["name"]: r for r in body["robots"]}
    assert set(by_name) == {"pinky1", "arm1"}
    assert by_name["pinky1"]["state"] == "PATROL"
    assert by_name["pinky1"]["busy"] is False
    assert by_name["arm1"]["state"] == "ERROR"
    assert by_name["arm1"]["x"] is None
    assert by_name["arm1"]["task_id"] == ""


def test_fms_연결_끊겨도_로봇_없으면_데모로_채우되_linked는_false(
    client, admin_auth, db_session, monkeypatch
):
    monkeypatch.setattr(ops.fms_client, "fleet_snapshot", lambda: (False, {}))
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    body = res.json()
    assert body["linked"] is False
    assert [r["name"] for r in body["robots"]] == ["pinky1"]


def test_fms_실제_로봇_있으면_데모_안_섞는다(client, admin_auth, db_session, monkeypatch):
    real = [
        {
            "name": "pinky2",
            "x": 1.0,
            "y": 2.0,
            "state": "WORKING",
            "battery": 80,
            "busy": True,
            "stale": False,
            "task_id": "t-1",
            "task_state": "EXECUTING",
            "progress": 0.4,
            "goal_vertex": 3,
        }
    ]
    monkeypatch.setattr(
        ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": real, "plugins": {}})
    )
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    body = res.json()
    assert [r["name"] for r in body["robots"]] == ["pinky2"]


def test_로그인_없이_조회_불가(client):
    res = client.get(ROBOTS)
    assert res.status_code == 401
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_ops_robots_fallback.py -v`
Expected: FAIL — 첫 3개 테스트가 `AssertionError`(로봇 목록이 빈 채로 옴, fallback 없음). 4번째(인증 테스트)는 이미 통과할 수 있음(기존 인증 로직이라).

- [ ] **Step 3: 최소 구현**

`aba_service/backend/app/routers/ops.py:250-258`을 다음으로 교체:

```python
@router.get("/robots")
def robots(
    db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    """로봇별 상태·배터리·현재 작업·위치. FMS 가 죽어도 linked:false 로 응답한다.

    로봇이 하나도 안 잡히면(FMS 미연결 포함) `/dashboard` 와 같은 이유로 DemoRobotState 로
    대체한다 — 실제 텔레메트리가 하나라도 있으면 이 분기는 절대 타지 않는다.
    """
    ok, snap = fms_client.fleet_snapshot()
    robots = snap.get("robots", []) if ok else []
    if not robots:
        robots = [
            {
                "name": name,
                "x": None,
                "y": None,
                "state": state,
                "battery": None,
                "busy": state == "WORKING",
                "stale": False,
                "task_id": "",
                "task_state": "",
                "progress": 0,
                "goal_vertex": None,
            }
            for name, state in db.execute(
                select(DemoRobotState.robot, DemoRobotState.state)
            ).all()
        ]
    return {
        "linked": ok,
        "robots": robots,
        "plugins": snap.get("plugins", {}) if ok else {},
    }
```

(`select`, `Session`, `get_db`, `DemoRobotState`, `AdminUser`는 이미 파일 상단에 import돼 있음 — 추가 import 불필요.)

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_ops_robots_fallback.py -v`
Expected: PASS (4개 전부)

- [ ] **Step 5: 기존 스위트 회귀 확인**

Run: `cd aba_service/backend && .venv/bin/pytest -q`
Expected: 전체 PASS (기존 테스트 깨짐 없음)

- [ ] **Step 6: 커밋**

```bash
git add aba_service/backend/app/routers/ops.py aba_service/backend/tests/test_ops_robots_fallback.py
git commit -m "feat(ops): add DemoRobotState fallback to /api/admin/ops/robots"
```

---

### Task 2: 공용 차트 컴포넌트 추출 (`charts.tsx`) + 대시보드 무변경 검증

**Files:**
- Create: `aba_service/frontend/src/components/admin/charts.tsx`
- Modify: `aba_service/frontend/src/routes/admin/_authed/index.tsx` (MiniDonut/WeeklyTaskBars 로컬 정의 삭제, import로 교체)

**Interfaces:**
- Produces (Wave 2가 소비):
  - `MiniDonut({ title: string; data: { label: string; value: number; color: string }[] })`
  - `WeeklyTaskBars({ data: { date: string; completed: number; failed: number }[] })`
  - `StackedStatusBar({ rows: { label: string; values: Record<string, number> }[]; segments: { key: string; label: string; color: string }[]; unit?: string })` — books.tsx 서가차트를 일반화한 신규 컴포넌트. `rows`가 1개면 단일 막대(members 용), N개면 항목별 여러 막대(books 용).

- [ ] **Step 1: `charts.tsx` 작성**

`aba_service/frontend/src/components/admin/charts.tsx`:

```tsx
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * 전체 중 비율(part-of-whole) 강조용 도넛 — 조각이 몇 개 안 될 때만 쓴다.
 * 차트 박스를 정사각형으로 고정해 원이 항상 박스를 꽉 채우게 한다. 각도만으로는
 * 못 읽으니 옆에 값 목록을 항상 같이 보여준다(범례 겸 직접 라벨).
 */
export function MiniDonut({
  title,
  data,
}: {
  title: string;
  data: { label: string; value: number; color: string }[];
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col rounded-lg border p-2">
      <p className="mb-1 shrink-0 text-sm font-semibold whitespace-nowrap text-muted-foreground">
        {title}
      </p>
      {total === 0 ? (
        <p className="text-xs text-muted-foreground">데이터 없음</p>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-12">
          <ul className="grid shrink-0 grid-cols-[auto_auto_auto] items-center gap-x-2 gap-y-0.5 text-xs">
            {data.map((d) => (
              <li key={d.label} className="contents">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: d.color }}
                />
                <span className="text-muted-foreground">{d.label}</span>
                <span className="text-right font-semibold tabular-nums">
                  {d.value}
                </span>
              </li>
            ))}
          </ul>
          <div className="aspect-square h-full min-w-0 max-w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="label"
                  innerRadius="55%"
                  outerRadius="100%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {data.map((d) => (
                    <Cell key={d.label} fill={d.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number, name: string) => [
                    `${value}건`,
                    name,
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 최근 7일 작업 성공/실패 — 하루 하나의 막대에 완료(good)/실패(critical) 두 상태를
 * 쌓아 올린다. 2개 시리즈라 범례는 항상 붙인다(색만으로 구분하게 두지 않음).
 */
export function WeeklyTaskBars({
  data,
}: {
  data: { date: string; completed: number; failed: number }[];
}) {
  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <div className="mb-1 flex shrink-0 items-center justify-between">
        <p className="text-sm font-semibold whitespace-nowrap text-muted-foreground">
          최근 7일 작업 성공/실패
        </p>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-good)" }}
            />
            완료
          </span>
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-critical)" }}
            />
            실패
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          >
            <XAxis
              dataKey="date"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <Tooltip
              cursor={{ fill: "var(--color-muted)" }}
              contentStyle={{
                background: "var(--color-card)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="completed"
              name="완료"
              stackId="a"
              fill="var(--chart-status-good)"
              radius={[0, 0, 0, 0]}
              maxBarSize={28}
            />
            <Bar
              dataKey="failed"
              name="실패"
              stackId="a"
              fill="var(--chart-status-critical)"
              radius={[4, 4, 0, 0]}
              maxBarSize={28}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/**
 * 카테고리별 상태 구성비 — 가로 스택 막대. `rows`가 하나면(members 상태 요약처럼)
 * 단일 막대, 여러 개면(books 서가 배치처럼) 항목마다 한 줄. `segments` 순서가 곧
 * 스택 쌓는 순서이자 범례 순서.
 */
export function StackedStatusBar({
  rows,
  segments,
  unit = "",
}: {
  rows: { label: string; values: Record<string, number> }[];
  segments: { key: string; label: string; color: string }[];
  unit?: string;
}) {
  const data = rows.map((r) => ({
    label: r.label,
    total: segments.reduce((sum, s) => sum + (r.values[s.key] ?? 0), 0),
    ...r.values,
  }));
  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <ResponsiveContainer
        width="100%"
        height={Math.max(64, data.length * 44)}
      >
        <BarChart
          data={data}
          layout="vertical"
          margin={{ left: 8, right: 28, top: 4, bottom: 4 }}
        >
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            width={72}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
          />
          <Tooltip
            cursor={{ fill: "var(--color-muted)" }}
            contentStyle={{
              background: "var(--color-card)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number, name: string) => [
              `${value}${unit}`,
              segments.find((s) => s.key === name)?.label ?? name,
            ]}
          />
          {segments.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              stackId="s"
              fill={s.color}
              radius={
                i === 0
                  ? [4, 0, 0, 4]
                  : i === segments.length - 1
                    ? [0, 4, 4, 0]
                    : [0, 0, 0, 0]
              }
              maxBarSize={18}
            >
              {i === segments.length - 1 ? (
                <LabelList
                  dataKey="total"
                  position="right"
                  formatter={(v: number) => `${v}${unit}`}
                  className="fill-muted-foreground text-xs"
                />
              ) : null}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {segments.map((s) => (
          <span key={s.key} className="flex items-center gap-1">
            <span
              className="size-2.5 rounded-full"
              style={{ background: s.color }}
            />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `index.tsx`에서 로컬 정의 삭제하고 import로 교체**

`index.tsx` 상단 recharts import(현재 `Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis` 전부를 recharts에서 가져오는 줄, `index.tsx:24-34` 부근)를 다음으로 교체:

```tsx
import { Tooltip } from "recharts";

import { MiniDonut, WeeklyTaskBars } from "@/components/admin/charts";
```

(index.tsx가 recharts `Tooltip`을 다른 곳에서 직접 안 쓴다면 이 import도 삭제 — Step 2 진행 중 `MiniDonut`/`WeeklyTaskBars` 정의를 지우고 나면 recharts 관련 잔여 사용처가 있는지 grep으로 확인할 것: `grep -n "Bar\|BarChart\|Cell\|Pie\|PieChart\|ResponsiveContainer\|XAxis\|YAxis" index.tsx` — 결과가 없으면 recharts import 줄 전체 삭제.)

`index.tsx:607-673`의 `function MiniDonut({ ... }) { ... }` 전체 블록을 삭제.
`index.tsx:1174-1254`의 `function WeeklyTaskBars({ ... }) { ... }` 전체 블록을 삭제.

- [ ] **Step 3: 대시보드 무변경 스크린샷 대조 (build 전 임시 확인)**

Run: `cd aba_service/frontend && npm run build`
Expected: 빌드 성공, 타입 에러 없음(특히 `MiniDonut`/`WeeklyTaskBars` 참조가 전부 import된 것으로 해석되는지).

이 Task는 index.tsx의 렌더링 결과가 추출 전후 100% 동일해야 한다 — Task 10(전체 검증)에서 스크린샷 대조를 최종 수행하되, 이 Task를 마치는 implementer는 로컬에서 `npm run dev`로 `/admin` 대시보드를 열어 육안으로 4개 카드(도서관리/회원관리/실시간모니터링/운영)의 도넛·막대 차트가 이전과 동일하게 보이는지 확인한다.

- [ ] **Step 4: lint**

Run: `cd aba_service/frontend && npm run lint`
Expected: PASS (미사용 import 없음)

- [ ] **Step 5: 커밋**

```bash
git add aba_service/frontend/src/components/admin/charts.tsx aba_service/frontend/src/routes/admin/_authed/index.tsx
git commit -m "refactor(admin): extract MiniDonut/WeeklyTaskBars/StackedStatusBar into shared charts.tsx"
```

---

### Task 3: 공용 정렬 훅 (`useSortableTable.tsx`)

**Files:**
- Create: `aba_service/frontend/src/components/admin/useSortableTable.tsx`

**Interfaces:**
- Produces (Wave 2 Task 4, Task 5가 소비):
  - `useSortableTable<T>(rows: T[], comparators: Record<string, (a: T, b: T) => number>): { sorted: T[]; sortKey: string | null; direction: "asc" | "desc"; toggle: (key: string) => void }`
  - `SortIcon({ active: boolean; direction: "asc" | "desc" })` — 헤더 옆에 붙이는 방향 아이콘.

- [ ] **Step 1: 파일 작성**

`aba_service/frontend/src/components/admin/useSortableTable.tsx`:

```tsx
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

/**
 * 헤더 클릭 정렬 — 클릭할 때마다 없음 → 오름차순 → 내림차순 → 없음으로 순환한다.
 * `comparators`는 정렬 가능한 칼럼 키만 등록한다(작업/관리 같은 칼럼은 등록하지 않으면
 * 자연히 정렬 불가).
 */
export function useSortableTable<T>(
  rows: T[],
  comparators: Record<string, (a: T, b: T) => number>,
) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [direction, setDirection] = useState<SortDirection>("asc");

  const toggle = (key: string) => {
    if (sortKey !== key) {
      setSortKey(key);
      setDirection("asc");
      return;
    }
    if (direction === "asc") {
      setDirection("desc");
      return;
    }
    setSortKey(null);
  };

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const cmp = comparators[sortKey];
    if (!cmp) return rows;
    const arr = [...rows].sort(cmp);
    return direction === "desc" ? arr.reverse() : arr;
  }, [rows, sortKey, direction, comparators]);

  return { sorted, sortKey, direction, toggle };
}

export function SortIcon({
  active,
  direction,
}: {
  active: boolean;
  direction: SortDirection;
}) {
  const Icon = !active ? ArrowUpDown : direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <Icon
      className={`ml-1 inline size-3.5 ${active ? "text-foreground" : "text-muted-foreground/50"}`}
    />
  );
}
```

- [ ] **Step 2: 순수 로직 단위 테스트 작성 (Vitest 없다면 이 스텝은 스킵하고 build/lint로 대체)**

Run: `cd aba_service/frontend && grep -n '"vitest"\|"test"' package.json`
Expected 확인용 커맨드 — vitest 등 프론트 테스트 러너가 `package.json`에 없다면(대부분 이 프로젝트엔 없음, PRD Testing Decisions 참고) 단위 테스트 파일을 만들지 않고 Step 3(build)로 바로 진행한다. 있다면 `toggle`의 asc→desc→none 순환과 `sorted` 결과를 검증하는 짧은 테스트를 추가한다.

- [ ] **Step 3: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 4: 커밋**

```bash
git add aba_service/frontend/src/components/admin/useSortableTable.tsx
git commit -m "feat(admin): add useSortableTable hook for header-click column sorting"
```

---

### Task 11: 백엔드 — 자동 마이그레이션 실행기 (codex 발견 [high] 대응)

codex adversarial review 발견: `db/add_book_unavailable_column.sql`이 기존 DB에 `unavailable` 컬럼을 추가하는 유일한 방법인데, 운영자가 수동으로 돌려야 하고 배포 순서에 강제되지 않는다 — 그 컬럼을 읽는 코드가 먼저 배포되면 기존 DB에서 바로 깨진다. 이 Task는 그 SQL을 앱 기동 시 자동·멱등 실행하는 최소 인프라를 만든다(Alembic 같은 새 마이그레이션 프레임워크 도입은 이 프로젝트 규모에 과함 — 기존 "raw SQL + `create_all`" 패턴을 유지한 채 실행만 자동화).

**Files:**
- Create: `aba_service/backend/app/migrations.py`
- Modify: `aba_service/backend/app/main.py:58-73` (`startup_event`)
- Test: `aba_service/backend/tests/test_migrations.py` (신규)

**Interfaces:**
- Produces: `run_migrations(engine: Engine) -> None` — Task 12가 여기 `MIGRATIONS` 리스트에 자기 컬럼을 추가해 재사용한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`aba_service/backend/tests/test_migrations.py`:

```python
"""run_migrations 의 dialect 가드 — sqlite(테스트)에서는 손대지 않고 스킵하는지.

MariaDB 전용 문법(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)이라 실제 컬럼 추가
동작 자체는 sqlite로 검증할 수 없다 — 여기서 검증하는 건 "다른 dialect 에서는
예외 없이 조용히 넘어간다"는 안전장치뿐이다. MariaDB 대상 동작은 운영 배포 시
수동 확인 대상으로 남긴다.
"""

from sqlalchemy import create_engine

from app.migrations import run_migrations


def test_sqlite_에서는_아무것도_안_하고_예외도_없다():
    engine = create_engine("sqlite://")
    run_migrations(engine)  # 예외 없이 조용히 스킵되면 성공
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.migrations'`

- [ ] **Step 3: 최소 구현**

`aba_service/backend/app/migrations.py`:

```python
"""가벼운 자동 마이그레이션 — 기존 MariaDB 표에 새 컬럼을 멱등하게 더한다.

`Base.metadata.create_all()` 은 새 DB 에는 완벽하지만 **이미 있는 표에는 컬럼을
안 더한다**. 컬럼 하나 늘 때마다 운영자가 따로 SQL 을 손으로 돌려야 했는데, 깜빡
하면 그 컬럼을 읽는 쿼리부터 배포 직후 깨진다. 여기 목록에 넣어두면 앱이 뜰 때마다
자동으로 맞춰준다. `IF NOT EXISTS` 덕분에 여러 번 돌려도 안전하다.

sqlite(테스트)에서는 스킵한다 — 테스트는 `create_all` 로 이미 최신 스키마로 뜨고,
아래 문법은 MariaDB 전용이라 sqlite에서 에러난다.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS = [
    # db/add_book_unavailable_column.sql 과 동일 — 실행 지점을 여기로 옮긴다.
    "ALTER TABLE cb_books "
    "ADD COLUMN IF NOT EXISTS unavailable TINYINT(1) NOT NULL DEFAULT 0",
]


def run_migrations(engine: Engine) -> None:
    if not engine.dialect.name.startswith("mysql"):
        return  # sqlite 등 — create_all 이 이미 최신 스키마를 만든다.
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
```

`aba_service/backend/app/main.py:73` (`Base.metadata.create_all(bind=engine)` 바로 다음)에 추가:

```python
    from .migrations import run_migrations

    run_migrations(engine)
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 5: 기존 스위트 회귀 확인**

Run: `cd aba_service/backend && .venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add aba_service/backend/app/migrations.py aba_service/backend/app/main.py aba_service/backend/tests/test_migrations.py
git commit -m "feat(backend): run idempotent schema migrations automatically at startup"
```

---

### Task 4: 회원(members.tsx) — 카드그리드 + 다이얼로그 폼 + 차트 stat + 헤더정렬

**Files:**
- Modify: `aba_service/frontend/src/routes/admin/_authed/members.tsx` (전체 재작성)

**Interfaces:**
- Consumes: Task 2의 `StackedStatusBar`, Task 3의 `useSortableTable`/`SortIcon`.
- 기존 API 호출(`api()` 헬퍼, `/api/admin/circulation/*`)은 전혀 바꾸지 않는다.

- [ ] **Step 1: 파일 전체를 아래 내용으로 교체**

`aba_service/frontend/src/routes/admin/_authed/members.tsx` 전체를 다음으로 교체한다. 기존 상태/핸들러 로직(`load`, `createMember`, `openEdit`, `saveEdit`, `confirmDelete`, `act`, 수정/삭제 다이얼로그)은 그대로 유지하고, ①레이아웃을 화면-고정으로, ②stat row를 차트로, ③생성 폼을 다이얼로그로, ④테이블에 헤더클릭 정렬을 추가한 버전이다:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { StackedStatusBar } from "@/components/admin/charts";
import { SortIcon, useSortableTable } from "@/components/admin/useSortableTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export const Route = createFileRoute("/admin/_authed/members")({
  head: () => ({ meta: [{ title: "LiBi Admin — 회원 · 대여/반납" }] }),
  component: MembersPage,
});

/**
 * 사서용 회원 관리 + 대여/반납 처리.
 *
 * 회원 앱의 「대여 신청」은 로봇이 안내데스크로 책을 가져다 놓는 데까지다.
 * **실제 대출 확정은 여기서** 사서가 누른다(`cb_loans` 행이 여기서 생긴다).
 */

interface MemberRow {
  id: number;
  username: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
  active_loans: number;
  total_loans: number;
}

interface LoanRow {
  id: number;
  member_id: number;
  member_name: string;
  book_id: number;
  book_title: string;
  status: string;
  borrowed_at: string;
  due_at: string;
  returned_at: string | null;
  overdue: boolean;
}

interface BookOption {
  id: number;
  title: string;
  author: string;
  zone: string;
}

const TOKEN_KEY = "labi.adminToken";

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token =
    typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(TOKEN_KEY);
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let msg = `요청 실패 (${res.status})`;
    try {
      const b = await res.json();
      if (typeof b?.detail === "string") msg = b.detail;
    } catch {
      /* JSON 아니면 기본 메시지 */
    }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const fmt = (iso: string) => iso.slice(0, 10);

const MEMBER_STATUS_COLOR = { active: "#10b981", inactive: "#94a3b8" } as const;
const LOAN_STATUS_COLOR = {
  normal: "#f59e0b",
  overdue: "#f43f5e",
  none: "#94a3b8",
} as const;

function MembersPage() {
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [loans, setLoans] = useState<LoanRow[]>([]);
  const [books, setBooks] = useState<BookOption[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [bookQuery, setBookQuery] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 회원 등록 (다이얼로그)
  const [createOpen, setCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [creating, setCreating] = useState(false);

  // 회원 수정(다이얼로그)
  const [editing, setEditing] = useState<MemberRow | null>(null);
  const [editFullName, setEditFullName] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [saving, setSaving] = useState(false);

  // 회원 삭제(확인 다이얼로그)
  const [deleteTarget, setDeleteTarget] = useState<MemberRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [m, l] = await Promise.all([
        api<MemberRow[]>("/api/admin/circulation/members"),
        api<LoanRow[]>("/api/admin/circulation/loans"),
      ]);
      setMembers(m);
      setLoans(l);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setTimeout(() => {
      void api<BookOption[]>(
        `/api/admin/circulation/available-books?q=${encodeURIComponent(bookQuery)}`,
      )
        .then(setBooks)
        .catch(() => setBooks([]));
    }, 250);
    return () => clearTimeout(t);
  }, [bookQuery]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리하지 못했습니다");
    }
  };

  const createMember = async (e: FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await api("/api/admin/circulation/members", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername.trim(),
          full_name: newFullName.trim() || undefined,
          password: newPassword,
        }),
      });
      toast.success(`«${newUsername}» 회원을 등록했습니다`);
      setNewUsername("");
      setNewFullName("");
      setNewPassword("");
      setCreateOpen(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "회원 등록에 실패했습니다");
    } finally {
      setCreating(false);
    }
  };

  const openEdit = (m: MemberRow) => {
    setEditing(m);
    setEditFullName(m.full_name ?? "");
    setEditPassword("");
  };

  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await api(`/api/admin/circulation/members/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          full_name: editFullName.trim() || null,
          ...(editPassword.trim() ? { password: editPassword.trim() } : {}),
        }),
      });
      toast.success("회원 정보를 수정했습니다");
      setEditing(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "수정에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api(`/api/admin/circulation/members/${deleteTarget.id}`, {
        method: "DELETE",
      });
      toast.success(
        `«${deleteTarget.full_name ?? deleteTarget.username}» 회원을 삭제했습니다`,
      );
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제에 실패했습니다");
    } finally {
      setDeleting(false);
    }
  };

  const memberLoans = useMemo(
    () =>
      selected === null ? [] : loans.filter((l) => l.member_id === selected),
    [loans, selected],
  );
  const activeLoans = useMemo(
    () => loans.filter((l) => l.status === "borrowed"),
    [loans],
  );
  const overdue = activeLoans.filter((l) => l.overdue);

  // 상단 stat 차트 — 회원 활성/비활성, 대출 상태(정상/연체/미대출)는 회원 단위로 집계.
  const overdueMemberIds = useMemo(
    () => new Set(overdue.map((l) => l.member_id)),
    [overdue],
  );
  const loanStatusCounts = useMemo(() => {
    let normal = 0;
    let lateCount = 0;
    let none = 0;
    for (const m of members) {
      if (overdueMemberIds.has(m.id)) lateCount++;
      else if (m.active_loans > 0) normal++;
      else none++;
    }
    return { normal, overdue: lateCount, none };
  }, [members, overdueMemberIds]);
  const activeMemberCount = members.filter((m) => m.is_active).length;

  // 헤더클릭 정렬 — 관리(작업) 칼럼은 comparator 를 등록하지 않아 자연히 정렬 불가.
  const { sorted: sortedMembers, sortKey, direction, toggle } =
    useSortableTable<MemberRow>(members, {
      username: (a, b) => a.username.localeCompare(b.username),
      full_name: (a, b) => (a.full_name ?? "").localeCompare(b.full_name ?? ""),
      is_active: (a, b) => Number(a.is_active) - Number(b.is_active),
      active_loans: (a, b) => a.active_loans - b.active_loans,
      total_loans: (a, b) => a.total_loans - b.total_loans,
    });

  const Th = ({ label, sortk }: { label: string; sortk?: string }) => (
    <th
      className={`pb-2 pr-3 ${sortk ? "cursor-pointer select-none hover:text-foreground" : ""}`}
      onClick={sortk ? () => toggle(sortk) : undefined}
    >
      {label}
      {sortk ? (
        <SortIcon active={sortKey === sortk} direction={direction} />
      ) : null}
    </th>
  );

  return (
    <AdminShell title="회원 · 대여/반납">
      <div className="flex h-full flex-col gap-4">
        {/* 상단 stat 차트 */}
        <div className="grid shrink-0 grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <StackedStatusBar
            rows={[
              {
                label: "회원 상태",
                values: {
                  active: activeMemberCount,
                  inactive: members.length - activeMemberCount,
                },
              },
            ]}
            segments={[
              { key: "active", label: "활성", color: MEMBER_STATUS_COLOR.active },
              { key: "inactive", label: "비활성", color: MEMBER_STATUS_COLOR.inactive },
            ]}
            unit="명"
          />
          <StackedStatusBar
            rows={[{ label: "대출 상태", values: loanStatusCounts }]}
            segments={[
              { key: "normal", label: "정상대출", color: LOAN_STATUS_COLOR.normal },
              { key: "overdue", label: "연체", color: LOAN_STATUS_COLOR.overdue },
              { key: "none", label: "미대출", color: LOAN_STATUS_COLOR.none },
            ]}
            unit="명"
          />
          <div className="flex flex-col justify-center rounded-lg border bg-muted/30 px-4 py-2">
            <span className="text-xl font-semibold tabular-nums">
              {loans.length}
            </span>
            <span className="text-xs text-muted-foreground">누적 대출</span>
          </div>
        </div>

        {msg ? (
          <p className="shrink-0 rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
            {msg}
          </p>
        ) : null}
        {err ? (
          <p className="shrink-0 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          {/* 회원 관리: 목록(정렬/수정/비활성화) */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border p-4">
            <div className="mb-3 flex shrink-0 items-center justify-between">
              <h3 className="text-sm font-semibold">회원 관리</h3>
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus className="mr-1 size-3.5" /> 회원 추가
              </Button>
            </div>

            {loading ? (
              <p className="text-sm text-muted-foreground">불러오는 중...</p>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-background text-left text-xs text-muted-foreground">
                    <tr>
                      <Th label="아이디" sortk="username" />
                      <Th label="이름" sortk="full_name" />
                      <Th label="상태" sortk="is_active" />
                      <Th label="대출중" sortk="active_loans" />
                      <Th label="누적" sortk="total_loans" />
                      <th className="pb-2 pr-3">관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedMembers.map((m) => (
                      <tr
                        key={m.id}
                        onClick={() => setSelected(m.id)}
                        className={`cursor-pointer border-t transition ${
                          selected === m.id ? "bg-muted" : "hover:bg-muted/50"
                        }`}
                      >
                        <td className="py-2 pr-3 font-mono text-xs">
                          {m.username}
                        </td>
                        <td className="py-2 pr-3">{m.full_name ?? "-"}</td>
                        <td className="py-2 pr-3">
                          <button
                            type="button"
                            title={
                              m.is_active
                                ? "클릭하면 비활성화"
                                : "클릭하면 활성화"
                            }
                            onClick={(e) => {
                              e.stopPropagation();
                              void act(
                                () =>
                                  api(
                                    `/api/admin/circulation/members/${m.id}`,
                                    {
                                      method: "PATCH",
                                      body: JSON.stringify({
                                        is_active: !m.is_active,
                                      }),
                                    },
                                  ),
                                m.is_active
                                  ? `«${m.full_name ?? m.username}» 비활성화했습니다`
                                  : `«${m.full_name ?? m.username}» 활성화했습니다`,
                              );
                            }}
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold transition ${
                              m.is_active
                                ? "bg-emerald-500/15 text-emerald-700 hover:bg-emerald-500/25"
                                : "bg-muted text-muted-foreground hover:bg-muted/70"
                            }`}
                          >
                            {m.is_active ? "활성" : "비활성"}
                          </button>
                        </td>
                        <td className="py-2 pr-3 tabular-nums">
                          {m.active_loans}
                        </td>
                        <td className="py-2 pr-3 tabular-nums text-muted-foreground">
                          {m.total_loans}
                        </td>
                        <td className="py-2 pr-3">
                          <div className="flex gap-1">
                            <button
                              type="button"
                              aria-label={`${m.full_name ?? m.username} 정보 수정`}
                              onClick={(e) => {
                                e.stopPropagation();
                                openEdit(m);
                              }}
                              className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              aria-label={`${m.full_name ?? m.username} 삭제`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setDeleteTarget(m);
                              }}
                              className="rounded p-1 text-rose-700 transition hover:bg-rose-500/10"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {members.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="py-3 text-muted-foreground">
                          회원이 없습니다
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 선택 회원 상세 + 대여 처리 */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border p-4">
            {selected === null ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                왼쪽에서 회원을 선택하면 대출 이력과 대여 처리가 나옵니다.
              </p>
            ) : (
              <>
                <h3 className="mb-3 shrink-0 text-sm font-semibold">
                  {members.find((m) => m.id === selected)?.full_name ??
                    members.find((m) => m.id === selected)?.username}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    대출 이력
                  </span>
                </h3>

                <div className="mb-4 max-h-40 space-y-1 overflow-y-auto">
                  {memberLoans.length === 0 ? (
                    <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
                      대출 이력이 없습니다
                    </p>
                  ) : (
                    memberLoans.map((l) => (
                      <div
                        key={l.id}
                        className="flex items-center gap-2 rounded border px-3 py-2 text-sm"
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {l.book_title}
                        </span>
                        <span
                          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            l.status === "returned"
                              ? "bg-muted text-muted-foreground"
                              : l.overdue
                                ? "bg-rose-500/15 text-rose-700"
                                : "bg-amber-500/15 text-amber-700"
                          }`}
                        >
                          {l.status === "returned"
                            ? `반납 ${fmt(l.returned_at ?? "")}`
                            : l.overdue
                              ? "연체"
                              : `~${fmt(l.due_at)}`}
                        </span>
                        {l.status === "borrowed" ? (
                          <button
                            onClick={() =>
                              act(
                                () =>
                                  api(
                                    `/api/admin/circulation/loans/${l.id}/return`,
                                    { method: "POST" },
                                  ),
                                `«${l.book_title}» 반납 처리했습니다`,
                              )
                            }
                            className="shrink-0 rounded bg-secondary px-2 py-1 text-xs font-semibold"
                          >
                            반납
                          </button>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>

                {/* 대여 처리 */}
                <div className="flex min-h-0 flex-1 flex-col border-t pt-3">
                  <h4 className="mb-2 shrink-0 text-xs font-semibold">
                    대여 처리
                  </h4>
                  <input
                    value={bookQuery}
                    onChange={(e) => setBookQuery(e.target.value)}
                    placeholder="도서 제목 검색 (재고 있는 것만)"
                    className="mb-2 h-9 w-full shrink-0 rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
                  />
                  <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
                    {books.map((b) => (
                      <div
                        key={b.id}
                        className="flex items-center gap-2 rounded border px-3 py-2 text-sm"
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {b.title}
                          <span className="ml-2 text-xs text-muted-foreground">
                            {b.author} · {b.zone}
                          </span>
                        </span>
                        <button
                          onClick={() =>
                            act(
                              () =>
                                api("/api/admin/circulation/borrow", {
                                  method: "POST",
                                  body: JSON.stringify({
                                    member_id: selected,
                                    book_id: b.id,
                                  }),
                                }),
                              `«${b.title}» 대출 처리했습니다 (14일)`,
                            )
                          }
                          className="shrink-0 rounded bg-primary px-2 py-1 text-xs font-semibold text-primary-foreground"
                        >
                          대출
                        </button>
                      </div>
                    ))}
                    {books.length === 0 ? (
                      <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
                        대출 가능한 도서가 없습니다
                      </p>
                    ) : null}
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      </div>

      {/* 회원 등록 다이얼로그 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>회원 추가</DialogTitle>
          </DialogHeader>
          <form onSubmit={createMember} className="space-y-3">
            <div>
              <Label htmlFor="new-username" className="text-xs">
                아이디
              </Label>
              <Input
                id="new-username"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="new-fullname" className="text-xs">
                이름 (선택)
              </Label>
              <Input
                id="new-fullname"
                value={newFullName}
                onChange={(e) => setNewFullName(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="new-password" className="text-xs">
                비밀번호
              </Label>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
              >
                취소
              </Button>
              <Button type="submit" disabled={creating}>
                {creating ? "등록 중..." : "등록"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 회원 수정 다이얼로그 */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>회원 정보 수정</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="edit-fullname">이름</Label>
            <Input
              id="edit-fullname"
              value={editFullName}
              onChange={(e) => setEditFullName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="edit-password">비밀번호 재설정 (선택)</Label>
            <Input
              id="edit-password"
              type="password"
              placeholder="비워두면 그대로 유지"
              value={editPassword}
              onChange={(e) => setEditPassword(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setEditing(null)}
              disabled={saving}
            >
              취소
            </Button>
            <Button onClick={() => void saveEdit()} disabled={saving}>
              {saving ? "저장 중..." : "저장"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 회원 삭제 확인 */}
      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="회원 삭제"
        description={
          <>
            «{deleteTarget?.full_name ?? deleteTarget?.username}» 회원을
            삭제할까요? 대출/요청/예약 이력이 함께 영구 삭제되며 되돌릴 수
            없습니다. 처리 중인 대출/요청/예약이 있으면 실패합니다.
          </>
        }
        confirmLabel="삭제"
        onConfirm={() => void confirmDelete()}
        busy={deleting}
      />
    </AdminShell>
  );
}
```

- [ ] **Step 2: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 3: 수동 확인**

Run: `cd aba_service/frontend && bun --bun run dev` (Node 18은 낮아 bun 필요)
`/admin/members` 접속해 확인: ① 화면이 스크롤 없이 꽉 차는지, ② "회원 추가" 버튼이 다이얼로그를 여는지 + 등록 성공 시 닫히는지, ③ 상단 두 막대차트가 실제 회원/대출 수와 일치하는지, ④ 헤더(아이디/이름/상태/대출중/누적) 클릭 시 정렬 방향이 바뀌고 아이콘이 반영되는지(관리 칼럼은 클릭해도 무반응).

- [ ] **Step 4: 커밋**

```bash
git add aba_service/frontend/src/routes/admin/_authed/members.tsx
git commit -m "feat(admin): rebuild members page as fixed-height card grid with charts and sortable table"
```

---

### Task 5: 도서(books.tsx) — 상단 stat 차트 + 드롭다운 정렬→헤더클릭 정렬

**Files:**
- Modify: `aba_service/frontend/src/routes/admin/_authed/books.tsx`

**Interfaces:**
- Consumes: Task 2의 `StackedStatusBar`, Task 3의 `useSortableTable`/`SortIcon`.

- [ ] **Step 1: import 교체**

`books.tsx:1-43`의 import 블록에서 recharts import(`Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis`)를 삭제하고 다음을 추가:

```tsx
import { StackedStatusBar } from "@/components/admin/charts";
import { SortIcon, useSortableTable } from "@/components/admin/useSortableTable";
```

- [ ] **Step 2: 로컬 정렬 로직을 공용 훅으로 교체**

`books.tsx:93-111`의 `type SortKey = ...` 및 `function sortBooks(...) { ... }` 전체 블록을 삭제한다.

`books.tsx:118` `const [sortBy, setSortBy] = useState<SortKey>("default");` 줄을 삭제한다.

`books.tsx:144` `const sortedBooks = useMemo(() => sortBooks(books, sortBy), [books, sortBy]);` 를 삭제하고, `BooksPage` 함수 안(다른 `useMemo` 근처)에 다음을 추가:

```tsx
const {
  sorted: sortedBooks,
  sortKey: booksSortKey,
  direction: booksDirection,
  toggle: toggleBooksSort,
} = useSortableTable<AdminBook>(books, {
  title_kr: (a, b) => a.title_kr.localeCompare(b.title_kr),
  author: (a, b) => a.author.localeCompare(b.author),
  category: (a, b) =>
    (CATEGORY_LABEL[a.category] ?? a.category).localeCompare(
      CATEGORY_LABEL[b.category] ?? b.category,
    ),
  zone: (a, b) => a.zone.localeCompare(b.zone),
  status: (a, b) => {
    const rank = (x: AdminBook) => (x.unavailable ? 2 : x.in_stock ? 0 : 1);
    return rank(a) - rank(b);
  },
});
```

- [ ] **Step 3: 정렬 `<select>` 제거, `<TableHead>`에 헤더클릭 정렬 연결**

`books.tsx:380-390`의 정렬 `<select>` 블록(`<select value={sortBy} ... 재고 상태순 </select>`)을 통째로 삭제한다(카테고리 `<select>`는 그대로 둔다 — 그건 필터이지 정렬이 아니다).

`books.tsx:394-402`의 `<TableHeader><TableRow>...</TableRow></TableHeader>`를 다음으로 교체:

```tsx
<TableHeader>
  <TableRow>
    {(
      [
        { key: "title_kr", label: "제목" },
        { key: "author", label: "저자" },
        { key: "category", label: "분야" },
        { key: "zone", label: "서가" },
        { key: "status", label: "재고" },
      ] as const
    ).map((col) => (
      <TableHead
        key={col.key}
        className="cursor-pointer select-none hover:text-foreground"
        onClick={() => toggleBooksSort(col.key)}
      >
        {col.label}
        <SortIcon
          active={booksSortKey === col.key}
          direction={booksDirection}
        />
      </TableHead>
    ))}
    <TableHead className="text-right">작업</TableHead>
  </TableRow>
</TableHeader>
```

- [ ] **Step 4: 서가 배치 차트를 공용 `StackedStatusBar`로 교체**

`books.tsx:255-346`(`<ResponsiveContainer ...>` 시작부터 범례 `<div className="mt-2 flex flex-wrap ...">` 끝까지, `{shelfChartData.length === 0 ? (...) : (<> ... </>)}`의 `<>...</>` 내부 전체)를 다음으로 교체:

```tsx
<StackedStatusBar
  rows={shelfChartData.map((s) => ({
    label: s.zone,
    values: {
      available: s.available,
      borrowed: s.borrowed,
      unavailable: s.unavailable,
    },
  }))}
  segments={[
    { key: "available", label: "대출가능", color: STATUS_COLOR.available },
    { key: "borrowed", label: "대출중", color: STATUS_COLOR.borrowed },
    {
      key: "unavailable",
      label: "대출불가능(훼손·분실)",
      color: STATUS_COLOR.unavailable,
    },
  ]}
  unit="권"
/>
```

이 교체로 `AlertTriangle, CheckCircle2, XCircle` 아이콘의 서가차트-범례 쪽 사용은 없어지지만, 같은 아이콘들이 재고 상태 배지(테이블 셀, `books.tsx:419-435`)에서 계속 쓰이므로 import는 그대로 둔다.

- [ ] **Step 5: 상단 stat 차트 섹션 추가**

`books.tsx:229`(`<div className="space-y-6">` 바로 다음, 메시지 배너들보다 앞) 위치에 새 섹션을 추가한다 — "서가 배치 현황" 섹션(`books.tsx:242` 시작) 바로 앞에 삽입:

```tsx
{/* 전체 재고 현황 — 서가별 상세 차트보다 위, 한눈에 보는 총계 */}
<section className="rounded-lg border bg-muted/30 p-4">
  <h3 className="mb-2 text-sm font-semibold text-muted-foreground">
    전체 재고 현황
  </h3>
  <StackedStatusBar
    rows={[
      {
        label: "전체",
        values: shelfChartData.reduce(
          (acc, s) => ({
            available: acc.available + s.available,
            borrowed: acc.borrowed + s.borrowed,
            unavailable: acc.unavailable + s.unavailable,
          }),
          { available: 0, borrowed: 0, unavailable: 0 },
        ),
      },
    ]}
    segments={[
      { key: "available", label: "대출가능", color: STATUS_COLOR.available },
      { key: "borrowed", label: "대출중", color: STATUS_COLOR.borrowed },
      {
        key: "unavailable",
        label: "대출불가능",
        color: STATUS_COLOR.unavailable,
      },
    ]}
    unit="권"
  />
</section>
```

- [ ] **Step 6: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS — 특히 미사용 `useMemo`/recharts import가 안 남아있는지 확인.

- [ ] **Step 7: 수동 확인**

`/admin/books`에서: ① 상단에 전체 재고 막대, ② 서가 배치 차트가 이전과 같은 정보(존별 재고)를 보여주는지(범례가 점+텍스트로 바뀜, 의도된 변경), ③ 헤더(제목/저자/분야/서가/재고) 클릭 정렬 동작, ④ 카테고리 필터 `<select>`는 여전히 동작.

- [ ] **Step 8: 커밋**

```bash
git add aba_service/frontend/src/routes/admin/_authed/books.tsx
git commit -m "feat(admin): add stat chart to books page, replace sort dropdown with clickable headers"
```

---

### Task 6: 실시간 모니터링(robots.tsx) — 지도 가로회전 + stat 차트 + 작업정보 join

**Files:**
- Modify: `aba_service/frontend/src/routes/admin/_authed/robots.tsx` (전체 재작성)

**Interfaces:**
- Consumes: Task 2의 `MiniDonut`. `LibraryMap.tsx`의 `rotate(x, y) => [y, 1-x]` 좌표 변환(그대로 재사용, 새로 유도하지 않음). `ops.tasks()`(이미 존재하는 API, `{orders: OrderRow[], kinds: TaskKind[], linked}`).
- Task 1의 백엔드 fallback에 의존(실 FMS 미연결 시 데모 로봇이 뜨는지는 Task 1이 이미 커밋된 상태에서 수동 확인).

- [ ] **Step 1: 파일 전체를 아래 내용으로 교체**

`aba_service/frontend/src/routes/admin/_authed/robots.tsx` 전체를 다음으로 교체:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { MiniDonut } from "@/components/admin/charts";
import { MAP_IMAGE, WAYPOINTS } from "@/lib/map-waypoints";
import { ops, type OrderRow, type RobotRow, type TaskKind } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/robots")({
  head: () => ({ meta: [{ title: "LiBi Admin — 실시간 모니터링" }] }),
  component: RobotsPage,
});

/**
 * 실시간 모니터링 — 로봇 상태·배터리·현재 작업 + 지도 위 위치.
 *
 * 지도 좌표는 `waypoint.yaml` 과 같은 계이므로, 로봇의 (x, y) 를 정규화해 그대로 찍으면
 * 실제 위치와 일치한다. 화면 표시는 `LibraryMap.tsx` 와 같은 90°(+180°) 회전 규칙을
 * 그대로 따른다 — 새로 유도하면 로봇 점이 벽 안쪽에 찍히는 사고가 난다.
 */

/** 월드 좌표 → 지도 이미지 안의 정규화 좌표(세로 원본 기준, 0~1). */
const ORIGIN_X = -0.184;
const ORIGIN_Y = -1.949;
const RES = 0.02;
const W = 63;
const H = 108;

function toNorm(x: number, y: number): { nx: number; ny: number } {
  return { nx: (x - ORIGIN_X) / (W * RES), ny: 1 - (y - ORIGIN_Y) / (H * RES) };
}

/** `LibraryMap.tsx` 와 동일한 회전 — (x, y) → (y, 1 − x). 세로 정규화 좌표를 가로 화면 좌표로. */
function rotate(x: number, y: number): [number, number] {
  return [y, 1 - x];
}

const STATE_TONE: Record<string, string> = {
  PATROL: "bg-emerald-500/15 text-emerald-700",
  IDLE: "bg-slate-500/15 text-slate-600",
  WORKING: "bg-amber-500/15 text-amber-700",
  ERROR: "bg-rose-500/15 text-rose-700",
  CHARGING: "bg-sky-500/15 text-sky-700",
  RETURNING: "bg-violet-500/15 text-violet-700",
};

const ORDER_STATUS_LABEL: Record<string, string> = {
  PENDING: "대기",
  ASSIGNED: "배차됨",
  EXECUTING: "수행중",
  COMPLETED: "완료",
  FAILED: "실패",
  CANCELLED: "취소",
};

const FLEET_STATE_COLOR = {
  available: "#10b981",
  working: "#f59e0b",
  charging: "#0ea5e9",
  error: "#f43f5e",
} as const;

function RobotsPage() {
  const [robots, setRobots] = useState<RobotRow[]>([]);
  const [linked, setLinked] = useState(true);
  const [plugins, setPlugins] = useState<Record<string, string>>({});
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [kinds, setKinds] = useState<TaskKind[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      Promise.all([ops.robots(), ops.tasks()])
        .then(([r, t]) => {
          setRobots(r.robots);
          setLinked(r.linked);
          setPlugins(r.plugins);
          setOrders(t.orders);
          setKinds(t.kinds);
          setErr(null);
        })
        .catch((e) => setErr(e instanceof Error ? e.message : "불러오기 실패"));
    void load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, []);

  const fleetChart = [
    {
      label: "가용",
      value: robots.filter(
        (r) => !r.busy && !r.stale && r.state !== "ERROR" && r.state !== "CHARGING",
      ).length,
      color: FLEET_STATE_COLOR.available,
    },
    {
      label: "작업중",
      value: robots.filter((r) => r.busy).length,
      color: FLEET_STATE_COLOR.working,
    },
    {
      label: "충전중",
      value: robots.filter((r) => r.state === "CHARGING" || r.state === "RETURNING")
        .length,
      color: FLEET_STATE_COLOR.charging,
    },
    {
      label: "오류",
      value: robots.filter((r) => r.state === "ERROR").length,
      color: FLEET_STATE_COLOR.error,
    },
  ];

  const orderFor = (robotName: string) =>
    orders.find(
      (o) =>
        o.robot === robotName &&
        !["COMPLETED", "FAILED", "CANCELLED"].includes(o.status),
    );

  return (
    <AdminShell title="실시간 모니터링">
      <div className="flex h-full flex-col gap-4">
        {!linked ? (
          <p className="shrink-0 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
            FMS 연결 없음 — 로봇 정보를 읽지 못합니다.
          </p>
        ) : null}
        {err ? (
          <p className="shrink-0 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="shrink-0">
          <MiniDonut title="로봇 상태" data={fleetChart} />
        </div>

        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
          {/* 지도 위 로봇 위치 — 가로(108:63)로 회전, LibraryMap.tsx 와 동일 규칙 */}
          <section className="flex h-full min-h-0 flex-col rounded-lg border p-4">
            <h3 className="mb-3 shrink-0 text-sm font-semibold">
              지도 위 로봇 위치
            </h3>
            <div
              className="relative mx-auto w-full max-w-2xl overflow-hidden rounded-lg bg-white ring-1 ring-border"
              style={{ aspectRatio: "108 / 63" }}
            >
              <img
                src={MAP_IMAGE}
                alt="도서관 지도"
                aria-hidden
                className="pointer-events-none absolute left-1/2 top-1/2 h-auto w-[58%] -translate-x-1/2 -translate-y-1/2 -rotate-90 opacity-60 [image-rendering:pixelated]"
              />
              {/* 서가·시설 정점 (옅게) — WAYPOINTS 는 이미 0~1 정규화 좌표라 rotate() 만 적용 */}
              {WAYPOINTS.filter((w) => w.kind !== "corridor").map((w) => {
                const [rx, ry] = rotate(w.x, w.y);
                return (
                  <span
                    key={w.name}
                    title={w.label}
                    style={{ left: `${rx * 100}%`, top: `${ry * 100}%` }}
                    className="absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-400"
                  />
                );
              })}
              {/* 로봇 — 월드좌표 → toNorm(0~1) → rotate() 순서로 같은 화면 좌표계에 맞춘다 */}
              {robots
                .filter((r) => r.x !== null && r.y !== null)
                .map((r) => {
                  const { nx, ny } = toNorm(r.x as number, r.y as number);
                  const [rx, ry] = rotate(nx, ny);
                  return (
                    <span
                      key={r.name}
                      style={{ left: `${rx * 100}%`, top: `${ry * 100}%` }}
                      className="absolute -translate-x-1/2 -translate-y-1/2"
                    >
                      <span className="block size-3 rounded-full border-2 border-white bg-primary shadow ring-2 ring-primary/40" />
                      <span className="absolute left-1/2 top-full mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-primary px-1 py-0.5 text-[9px] font-bold text-primary-foreground">
                        {r.name}
                      </span>
                    </span>
                  );
                })}
            </div>
            {plugins.dispatcher ? (
              <p className="mt-3 shrink-0 text-center font-mono text-[11px] text-muted-foreground">
                배차 {plugins.dispatcher} · 교통 {plugins.traffic}
              </p>
            ) : null}
          </section>

          {/* 로봇 카드 */}
          <section className="min-h-0 space-y-2 overflow-y-auto">
            {robots.length === 0 ? (
              <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                관측된 로봇이 없습니다
              </p>
            ) : (
              robots.map((r) => {
                const order = orderFor(r.name);
                const kindLabel = order
                  ? (kinds.find((k) => k.key === order.task_type)?.label ??
                    order.task_type)
                  : null;
                return (
                  <div key={r.name} className="rounded-lg border p-4">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{r.name}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-bold ${
                          STATE_TONE[r.state ?? ""] ??
                          "bg-muted text-muted-foreground"
                        }`}
                      >
                        {r.state ?? "상태 미상"}
                      </span>
                      {r.stale ? (
                        <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs font-bold text-rose-700">
                          텔레메트리 끊김
                        </span>
                      ) : null}
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <Field label="배터리">
                        {r.battery === null ? (
                          "—"
                        ) : (
                          <span className="flex items-center gap-2">
                            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                              <span
                                className={`block h-full rounded-full ${
                                  r.battery < 20
                                    ? "bg-rose-500"
                                    : "bg-emerald-500"
                                }`}
                                style={{
                                  width: `${Math.max(0, Math.min(100, r.battery))}%`,
                                }}
                              />
                            </span>
                            {r.battery}%
                          </span>
                        )}
                      </Field>
                      <Field label="위치">
                        {r.x === null
                          ? "—"
                          : `${r.x.toFixed(2)}, ${(r.y as number).toFixed(2)}`}
                      </Field>
                      <Field label={order ? "작업 종류" : "현재 작업"}>
                        {order ? kindLabel : r.task_id || "—"}
                      </Field>
                      <Field label="작업 상태">
                        {order ? (
                          <span
                            className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                            style={{
                              background:
                                order.status === "FAILED"
                                  ? "var(--chart-status-critical)"
                                  : order.status === "COMPLETED"
                                    ? "var(--chart-status-good)"
                                    : "var(--chart-status-warning)",
                              color: "white",
                            }}
                          >
                            {ORDER_STATUS_LABEL[order.status] ?? order.status}
                          </span>
                        ) : (
                          r.task_state || "—"
                        )}
                      </Field>
                      {order ? (
                        <Field label="요청자">{order.requester || "—"}</Field>
                      ) : (
                        <Field label="목표 정점">
                          {r.goal_vertex === null
                            ? "—"
                            : (WAYPOINTS[r.goal_vertex]?.label ??
                              `v${r.goal_vertex}`)}
                        </Field>
                      )}
                      <Field label="진행률">
                        <span className="flex items-center gap-2">
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                            <span
                              className="block h-full rounded-full bg-primary"
                              style={{
                                width: `${
                                  order
                                    ? order.leg_count
                                      ? Math.round(
                                          (order.leg_idx / order.leg_count) * 100,
                                        )
                                      : 0
                                    : Math.round((r.progress ?? 0) * 100)
                                }%`,
                              }}
                            />
                          </span>
                          {order
                            ? `${order.leg_idx}/${order.leg_count}`
                            : `${Math.round((r.progress ?? 0) * 100)}%`}
                        </span>
                      </Field>
                    </div>
                  </div>
                );
              })
            )}
          </section>
        </div>
      </div>
    </AdminShell>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded bg-muted/50 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 font-medium text-foreground">{children}</div>
    </div>
  );
}
```

- [ ] **Step 2: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 3: 수동 확인 (Task 1 백엔드 fallback과 함께)**

`.venv/bin/python scripts/seed_demo_data.py` 실행 후(회원/도서는 이미 seed 돼있다고 가정) `/admin/robots` 접속: ① FMS 미연결 상태에서도(로컬 dev는 보통 FMS 없음) `pinky1/pinky2/pinky3/arm1/arm2` 5개 카드가 뜨는지(Task 1 fallback), ② 지도가 가로로 표시되는지, ③ 상단에 로봇상태 도넛이 뜨는지, ④ 실제 FMS가 붙어있는 환경이 있다면 그 경우 로봇 점이 waypoint 점들과 같은 방향/비율로 지도 위에 정확히 찍히는지(벽 안쪽에 박히지 않는지) 육안 확인 — 이 마지막 항목은 실 FMS 연결 환경이 없으면 스킵하고 Task 10에서 재확인.

- [ ] **Step 4: 커밋**

```bash
git add aba_service/frontend/src/routes/admin/_authed/robots.tsx
git commit -m "feat(admin): rotate monitoring map horizontal, add fleet stat chart and task-join details"
```

---

### Task 7: 보안(security.tsx) — 화면고정 레이아웃 + 이벤트 요약 차트

**Files:**
- Modify: `aba_service/frontend/src/routes/admin/_authed/security.tsx`

**Interfaces:**
- Consumes: Task 2의 `MiniDonut`.

- [ ] **Step 1: import 추가**

`security.tsx:1-7`의 import 블록에 추가:

```tsx
import { MiniDonut } from "@/components/admin/charts";
```

- [ ] **Step 2: 화면-고정 레이아웃으로 전환**

`security.tsx:133` `<div className="space-y-4">` 를 `<div className="flex h-full flex-col gap-4">` 로 교체.

`security.tsx:146` 운영모드 `<section className="rounded-lg border p-4">` 는 `shrink-0`을 추가: `<section className="shrink-0 rounded-lg border p-4">`.

`security.tsx:230` 침입 이벤트 `<section className="rounded-lg border p-4">` 를 `<section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">` 로 교체.

`security.tsx:245` `<div className="space-y-2">`(이벤트 리스트를 담는 div, `{sortedEvents.map(...)}` 바로 위)를 `<div className="min-h-0 flex-1 space-y-2 overflow-y-auto">` 로 교체.

- [ ] **Step 3: 침입 이벤트 요약 차트 추가**

`security.tsx:230-238`의 `<h3 className="mb-3 text-sm font-semibold">침입 감지 기록 ...</h3>` 블록 바로 다음(이벤트 리스트/빈상태 분기 이전)에 삽입:

```tsx
{state && state.events.length > 0 ? (
  <div className="mb-3 shrink-0">
    <MiniDonut
      title="확인 상태"
      data={[
        {
          label: "확인됨",
          value: state.events.length - unacked.length,
          color: "#10b981",
        },
        { label: "미확인", value: unacked.length, color: "#f43f5e" },
      ]}
    />
  </div>
) : null}
```

- [ ] **Step 4: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 5: 수동 확인**

`/admin/security`에서: ① 운영모드 섹션은 고정, 침입기록 섹션만 내부 스크롤되는지, ② 이벤트가 있을 때 확인/미확인 도넛이 뜨는지, ③ 기존 기능(모드 전환/스케줄/확인처리/삭제/영상재생) 전부 그대로 동작하는지.

- [ ] **Step 6: 커밋**

```bash
git add aba_service/frontend/src/routes/admin/_authed/security.tsx
git commit -m "feat(admin): fixed-height layout and ack-status chart for security page"
```

---

### Task 8: 운영 — 작업지시(tasks.tsx) + 신규 도서 선택 다이얼로그

**Files:**
- Create: `aba_service/frontend/src/components/admin/TaskBookPickerDialog.tsx`
- Modify: `aba_service/frontend/src/routes/admin/_authed/tasks.tsx`

**Interfaces:**
- Produces: `TaskBookPickerDialog({ open, books, excludeUnavailable, onClose, onPick })` — `books`는 호출부(tasks.tsx)가 이미 들고 있는 `AdminBook[]`를 그대로 전달(새 API 호출 없음). `onPick(book: AdminBook)`.
- Consumes (tasks.tsx 변경): 기존 `ops.books()`/`ops.robots()`/`ops.tasks()`/`ops.createTask()` 그대로.

- [ ] **Step 1: 신규 다이얼로그 컴포넌트 작성**

`aba_service/frontend/src/components/admin/TaskBookPickerDialog.tsx`:

```tsx
import { useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AdminBook } from "@/lib/ops-api";

/**
 * 작업지시(이송)용 도서 선택 팝업 — 검색 → 목록 → 클릭 → 닫힘.
 * 새 API 호출을 추가하지 않는다: 호출부가 이미 `ops.books()`로 불러온 전체
 * 카탈로그를 그대로 받아 클라이언트에서 필터링한다.
 * 대여 가능한 책만 굵게 표시하고 클릭 가능하게 하며, 대출중/대출불가능 도서는
 * 흐리게 표시하고 클릭할 수 없게 한다(로봇을 이미 나간 책 집으러 보내지 않도록).
 */
export function TaskBookPickerDialog({
  open,
  books,
  onClose,
  onPick,
}: {
  open: boolean;
  books: AdminBook[];
  onClose: () => void;
  onPick: (book: AdminBook) => void;
}) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const filtered = books.filter(
    (b) =>
      b.title_kr.toLowerCase().includes(query.toLowerCase()) ||
      b.author.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[80vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle>대상 도서 선택</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="제목·저자 검색"
        />
        <div className="max-h-96 space-y-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="p-4 text-center text-sm text-muted-foreground">
              검색 결과가 없습니다
            </p>
          ) : (
            filtered.map((b) => {
              const available = b.in_stock && !b.unavailable;
              return (
                <button
                  key={b.id}
                  type="button"
                  disabled={!available}
                  onClick={() => {
                    onPick(b);
                    onClose();
                  }}
                  className={`flex w-full items-center gap-2 rounded border px-3 py-2 text-left text-sm transition ${
                    available
                      ? "font-semibold hover:bg-muted/50"
                      : "cursor-not-allowed font-normal text-muted-foreground opacity-60"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {b.title_kr}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      {b.author} · {b.zone}
                    </span>
                  </span>
                  {!available ? (
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold">
                      {b.unavailable ? "대출불가능" : "대출중"}
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: `tasks.tsx` import 추가 + 상태 추가**

`tasks.tsx:1-12`의 import 블록에 추가:

```tsx
import { TaskBookPickerDialog } from "@/components/admin/TaskBookPickerDialog";
```

`tasks.tsx:90` 부근(`const [pickupAuto, setPickupAuto] = useState<string | null>(null);` 다음)에 추가:

```tsx
const [bookPickerOpen, setBookPickerOpen] = useState(false);
```

- [ ] **Step 3: porter 숨김 (클라이언트단 필터)**

`tasks.tsx:84` `const spec = kinds.find((k) => k.key === kind);` 바로 앞에 추가:

```tsx
// 짐꾼(porter)은 libi_gui 쪽 전용 기능이라 사서 admin 화면에서는 선택지에서 뺀다.
// 백엔드 TASK_KINDS 는 그대로 둔다(다른 경로에서 여전히 유효한 종류).
const visibleKinds = kinds.filter((k) => k.key !== "porter");
```

`tasks.tsx:212-227`의 종류 pill 버튼 목록 `{kinds.map((k) => (...))}` 에서 `kinds.map`을 `visibleKinds.map`으로 교체.

- [ ] **Step 4: sort(분류) 고정 위치 처리 — 로봇만 배정**

`tasks.tsx:136-145`의 `ready` 계산 함수 안, `if (shows("pickup") && !pickup) return false;` 줄 앞에 분기를 추가하기 위해 `ready` 전체를 다음으로 교체:

```tsx
const ready = (() => {
  if (!spec || !linked) return false;
  if (spec.mode === "state") return !!robot; // 복귀·순찰은 대상 로봇이 필수
  if (kind === "sort") return true; // 분류는 로봇 배정만 하면 된다(위치 고정)
  if (shows("book") && !book.trim()) return false;
  if (shows("cargo") && !cargo.trim()) return false;
  if (shows("pickup") && !pickup) return false;
  if (shows("dropoff") && !dropoff) return false;
  return true;
})();
```

`tasks.tsx:147-184`의 `submit` 함수 안, `ops.createTask({...})` 호출부를 다음으로 교체:

```tsx
const SORT_FIXED_WAYPOINT = "테이블-1번-좌";

const submit = async () => {
  setErr(null);
  setMsg(null);
  if (!spec) return;
  try {
    // 안 쓰는 칸은 아예 보내지 않는다 — 「정리」에 책 이름이 실리면 로봇이 집으러 간다.
    // 「분류」는 출발지/목적지가 항상 같은 고정 지점이라(진짜 반납함/분류대
    // waypoint 가 아직 없어 임시로 재사용) 화면에서 고르지 않고 여기서 채운다.
    const res = await ops.createTask({
      kind,
      ...(shows("book") ? { book } : {}),
      ...(shows("cargo") ? { cargo } : {}),
      ...(kind === "sort"
        ? { pickup: SORT_FIXED_WAYPOINT, dropoff: SORT_FIXED_WAYPOINT }
        : {
            ...(shows("pickup") ? { pickup } : {}),
            ...(shows("dropoff") ? { dropoff } : {}),
          }),
      robot,
    });

    if (res.mode === "state") {
      setMsg(
        res.accepted
          ? `${res.robot} → ${res.state} 모드로 전환했습니다`
          : `${res.robot} 이(가) 거절했습니다 (현재 ${res.current_state}) — ${res.reason}`,
      );
    } else {
      const where = res.dropoff ? ` → ${res.dropoff}` : "";
      setMsg(
        res.warn
          ? `${res.task_id} 접수${where} — ${res.warn}`
          : res.assigned
            ? `${res.task_id} 접수${where} + ${res.assigned} 배차`
            : `${res.task_id} 접수${where} (자동 배차 대기)`,
      );
      setBook("");
      setCargo("");
    }
    await load();
  } catch (e) {
    setErr(e instanceof Error ? e.message : "지시 실패");
  }
};
```

`tasks.tsx:267-289`(`{shows("pickup") ? (...) : null}` 와 `{shows("dropoff") ? (...) : null}` 두 블록)의 조건을 각각 `shows("pickup") && kind !== "sort"`, `shows("dropoff") && kind !== "sort"`로 바꿔 sort일 때는 두 select가 아예 안 그려지게 한다. sort를 선택했을 때는 대신 안내 문구를 보여주기 위해, `{shows("dropoff") ...}` 블록 바로 다음에 추가:

```tsx
{kind === "sort" ? (
  <Labeled label="위치 (자동)">
    <p className="flex h-9 items-center rounded-md border bg-muted px-3 text-sm text-muted-foreground">
      {SORT_FIXED_WAYPOINT} (반납함 ↔ 분류대 고정)
    </p>
  </Labeled>
) : null}
```

- [ ] **Step 5: 도서 입력을 팝업 다이얼로그로 교체**

`tasks.tsx:235-253`의 "대상 도서" `<Labeled>` 블록(`<input list="ops-book-list" .../>` + `<datalist>`)을 다음으로 교체:

```tsx
{shows("book") ? (
  <Labeled label="대상 도서">
    <button
      type="button"
      onClick={() => setBookPickerOpen(true)}
      className="flex h-9 w-full items-center rounded-md border px-3 text-left text-sm outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-primary"
    >
      {book || (
        <span className="text-muted-foreground">
          클릭해서 도서 선택 (고르면 출발지 자동)
        </span>
      )}
    </button>
  </Labeled>
) : null}
```

`tasks.tsx:336-343` 부근(작업 지시 제출 버튼 다음, `</section>` 앞)에 다이얼로그를 마운트:

```tsx
<TaskBookPickerDialog
  open={bookPickerOpen}
  books={books}
  onClose={() => setBookPickerOpen(false)}
  onPick={(b) => onBookChange(b.title_kr)}
/>
```

(`onBookChange`는 기존 로직 그대로 재사용 — 선택된 책 제목으로 `book` state를 세팅하고 그 책의 `zone`으로 `pickup`을 자동 설정한다. 새 로직 불필요.)

- [ ] **Step 6: 로봇 배차 select — 가용/불가 구분(볼드 + disabled)**

`tasks.tsx:316-333`의 로봇 `<select>` 블록을 다음으로 교체:

```tsx
<Labeled label={isModeChange ? "로봇 (필수)" : "로봇 (비우면 자동)"}>
  <select
    value={robot}
    onChange={(e) => setRobot(e.target.value)}
    className="h-9 w-full rounded-md border px-2 text-sm"
  >
    <option value="">
      {isModeChange ? "— 로봇을 고르세요 —" : "— 자동 배차 —"}
    </option>
    {robots.map((r) => {
      const available = !r.busy && !r.stale && r.state !== "ERROR";
      return (
        <option
          key={r.name}
          value={r.name}
          disabled={!available}
          style={{ fontWeight: available ? 700 : 400 }}
        >
          {r.name} ({r.state ?? "상태미상"}){available ? "" : " — 배차 불가"}
        </option>
      );
    })}
  </select>
</Labeled>
```

- [ ] **Step 7: 큐 테이블 상태 라벨 한글화 + id 축약**

`tasks.tsx:34-41`의 `STATUS_TONE` 정의 바로 다음에 추가:

```tsx
const STATUS_LABEL: Record<string, string> = {
  PENDING: "대기",
  ASSIGNED: "배차됨",
  EXECUTING: "수행중",
  COMPLETED: "완료",
  FAILED: "실패",
  CANCELLED: "취소",
};
```

`tasks.tsx:383` `<td className="py-2 pr-3 font-mono text-xs">{o.id}</td>` 를 다음으로 교체:

```tsx
<td
  className="py-2 pr-3 font-mono text-xs"
  title={o.id}
>
  {o.id.slice(0, 8)}
</td>
```

`tasks.tsx:384-389`의 상태 배지 `{o.status}` 를 `{STATUS_LABEL[o.status] ?? o.status}` 로 교체.

- [ ] **Step 8: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 9: 수동 확인**

`/admin/tasks`에서: ① 짐꾼 pill이 안 보이는지, ② 분류 선택 시 출발지/목적지 select 대신 고정위치 안내문이 뜨고 로봇만 고르면 지시 가능한지, ③ 이송 선택 후 "대상 도서" 클릭 시 팝업이 열리고 검색·선택되는지, 선택 시 출발지가 자동 설정되는지, 대출중 도서는 클릭 불가인지, ④ 로봇 select에서 배차불가 로봇이 disabled + 일반굵기, 가용 로봇은 굵게 보이는지, ⑤ 큐 테이블 상태가 한글로, id가 8자로 축약돼 보이는지(hover 시 전체 id 툴팁).

- [ ] **Step 10: 커밋**

```bash
git add aba_service/frontend/src/components/admin/TaskBookPickerDialog.tsx aba_service/frontend/src/routes/admin/_authed/tasks.tsx
git commit -m "feat(admin): task-creation UX — book picker dialog, robot availability styling, sort fixed location, porter hidden"
```

---

### Task 9: 운영 — 승인/사용자/알림(approvals.tsx, users.tsx, alerts.tsx) 경량 레이아웃 정리

**Files:**
- Modify: `aba_service/frontend/src/routes/admin/_authed/approvals.tsx`
- Modify: `aba_service/frontend/src/routes/admin/_authed/alerts.tsx`
- Modify: `aba_service/frontend/src/routes/admin/_authed/users.tsx`

**Interfaces:** 없음(레이아웃만, 데이터/로직 무변경).

이 Task는 "경량" 범위다 — 화면-고정 컨테이너 안에서 긴 목록만 내부 스크롤되게 감싸는 것으로 한정하고, 새 차트/정렬/기능 추가는 하지 않는다(approvals/alerts는 상세 요구사항이 없었고, users는 이번 계획에서 상세 탐색을 안 해 안전하게 최소 변경만 한다).

- [ ] **Step 1: approvals.tsx**

`approvals.tsx:95` `<div className="space-y-4">` 를 `<div className="flex h-full flex-col gap-4">` 로 교체.
`approvals.tsx:107` 승인 대기 `<section className="rounded-lg border p-4">` 를 `<section className="flex min-h-0 flex-[2] flex-col rounded-lg border p-4">` 로 교체.
`approvals.tsx:119` `<div className="mt-3 space-y-2">`(대기 목록 컨테이너)를 `<div className="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto">` 로 교체.
`approvals.tsx:178` 최근 처리 `<section className="rounded-lg border p-4">` 를 `<section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">` 로 교체.
`approvals.tsx:185` `<div className="overflow-x-auto">` 를 `<div className="min-h-0 flex-1 overflow-auto">` 로 교체.

- [ ] **Step 2: alerts.tsx**

`alerts.tsx:112` `<div className="space-y-4">` 를 `<div className="flex h-full flex-col gap-4">` 로 교체.
미확인 침입 섹션(`alerts.tsx:121`)과 최근 작업 알림 섹션(`alerts.tsx:156`)에는 `shrink-0`을 추가(각각 `<section className="shrink-0 rounded-lg border ...">`).
로그 섹션(`alerts.tsx:200`)을 `<section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">` 로 교체하고, 그 안의 `<div className="overflow-x-auto">`(`alerts.tsx:217`)를 `<div className="min-h-0 flex-1 overflow-auto">` 로 교체.

- [ ] **Step 3: users.tsx**

이 파일은 이번 계획에서 상세 탐색을 하지 않았다 — implementer는 먼저 파일을 읽고, Step 1/2에서 approvals.tsx·alerts.tsx에 적용한 것과 동일한 패턴(루트 `space-y-*` → `flex h-full flex-col gap-4`, 목록/테이블을 담은 섹션에 `flex min-h-0 flex-1 flex-col` + 내부 스크롤 컨테이너에 `min-h-0 flex-1 overflow-y-auto`)을 그대로 적용한다. 기존 상태/핸들러/폼 로직은 절대 바꾸지 않는다 — 클래스명 변경만.

- [ ] **Step 4: build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: PASS

- [ ] **Step 5: 수동 확인**

세 페이지 모두 열어 기존 기능(승인/반려, 침입 확인, 로그 필터/삭제, 사용자 관리 — 있는 그대로)이 전부 그대로 동작하는지, 화면이 스크롤 없이 카드 안에서만 스크롤되는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add aba_service/frontend/src/routes/admin/_authed/approvals.tsx aba_service/frontend/src/routes/admin/_authed/alerts.tsx aba_service/frontend/src/routes/admin/_authed/users.tsx
git commit -m "style(admin): fixed-height card layout for approvals/alerts/users pages"
```

---

### Task 12: 데모 데이터 무결성 — is_demo 마커 + reset 안전화 + 중복대출 방지 (codex 발견 [critical]+[high] 대응)

codex adversarial review 발견 2건을 함께 처리한다(둘 다 `seed_demo_data.py`/`reset_demo_data.py`를 건드려 파일이 겹치므로 한 Task로 묶는다):
- **[critical]** `reset_demo_data.py`가 데모/실제 데이터를 구분할 마커 없이 `cb_loans`/`cb_delivery_requests`/`cb_reservations`/`cb_intrusion_events`를 통째로 지운다 — 프로덕션에서 실행하면 실제 이력이 영구 삭제된다.
- **[high]** `seed_demo_data.py`의 대출 생성이 책을 독립적으로 무작위 선택해서, 같은 책이 동시에 여러 건 "대출중"으로 겹칠 수 있다.

**Files:**
- Modify: `aba_service/backend/app/models.py` (`Loan`, `Reservation`, `DeliveryRequest`, `IntrusionEvent`에 `is_demo` 컬럼)
- Modify: `aba_service/backend/app/migrations.py` (Task 11의 `MIGRATIONS` 리스트에 컬럼 4개 추가)
- Modify: `aba_service/backend/scripts/seed_demo_data.py`
- Modify: `aba_service/backend/scripts/reset_demo_data.py`
- Test: `aba_service/backend/tests/test_reset_demo_data.py` (신규)
- Test: `aba_service/backend/tests/test_seed_demo_data_loan_dedup.py` (신규)

**Interfaces:**
- Consumes: Task 11의 `app.migrations.MIGRATIONS` 리스트.
- `TaskLog`는 컬럼을 추가하지 않는다 — 이미 `task_id=f"demo-task-{i}"`로 자연스러운 마커가 있다(`seed_demo_data.py:101`). `DemoRobotState`도 손대지 않는다(그 표는 태생부터 전부 데모 데이터라 통째로 지워도 안전 — 기존 주석이 이미 그렇게 설명함).

- [ ] **Step 1: `is_demo` 컬럼을 모델에 추가**

`aba_service/backend/app/models.py`의 아래 위치에 각각 필드를 추가한다(기존 `Boolean` import는 `IntrusionEvent.acknowledged`에서 이미 쓰고 있으므로 추가 import 불필요):

`Loan` 클래스(`models.py:192-194`, `returned_at` 필드 다음)에 추가:
```python
    is_demo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="데모 시드가 만든 행인지"
    )
```

`Reservation` 클래스(`models.py:224-226`, `created_at` 필드 다음)에 동일하게 추가.

`DeliveryRequest` 클래스(`models.py:318-320`, `created_at` 필드 다음)에 동일하게 추가.

`IntrusionEvent` 클래스(`models.py:370-372`, `acknowledged` 필드 다음)에 동일하게 추가.

- [ ] **Step 2: Task 11의 마이그레이션 목록에 추가**

`aba_service/backend/app/migrations.py`의 `MIGRATIONS` 리스트에 추가:

```python
    "ALTER TABLE cb_loans ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_reservations ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_delivery_requests ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_intrusion_events ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
```

- [ ] **Step 3: 실패하는 테스트 작성 — reset 안전화**

`aba_service/backend/tests/test_reset_demo_data.py`:

```python
"""reset_demo_data — is_demo 마커가 있는 행만 지우고 실제 이력은 남기는지.

`main()`은 자체 SessionLocal 을 만들어서 테스트하기 번거로우므로, 핵심 로직을
세션을 인자로 받는 `reset_demo_data(db)` 로 뽑아 `main()`은 그걸 부르는 얇은
래퍼로만 남긴다(그래야 `db_session` 픽스처로 직접 검증할 수 있다).
"""

from datetime import datetime, timedelta

from app.models import Book, DeliveryRequest, IntrusionEvent, Loan, Member, Reservation
from scripts.reset_demo_data import reset_demo_data


def _make_member(db, username="real_user"):
    from app.security import hash_password

    m = Member(username=username, hashed_password=hash_password("pw"), is_active=True)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_book(db, title="실제 책"):
    b = Book(
        title_kr=title, title_en=title, title_zh=title, title_vi=title,
        author="누군가", category="literature", cover="📘",
        color="from-slate-200 to-slate-300", zone="문학-1", shelf="1단",
        in_stock=True, unavailable=False,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def test_실제_대출은_안_지우고_데모_대출만_지운다(db_session):
    member = _make_member(db_session)
    book = _make_book(db_session)
    now = datetime.now()

    real_loan = Loan(
        member_id=member.id, book_id=book.id, status="returned",
        borrowed_at=now - timedelta(days=10), due_at=now - timedelta(days=3),
        returned_at=now - timedelta(days=5), is_demo=False,
    )
    demo_loan = Loan(
        member_id=member.id, book_id=book.id, status="returned",
        borrowed_at=now - timedelta(days=10), due_at=now - timedelta(days=3),
        returned_at=now - timedelta(days=5), is_demo=True,
    )
    db_session.add_all([real_loan, demo_loan])
    db_session.commit()

    reset_demo_data(db_session)

    remaining = db_session.query(Loan).all()
    assert len(remaining) == 1
    assert remaining[0].is_demo is False


def test_실제_침입기록도_보존한다(db_session):
    real_event = IntrusionEvent(source="pinky1", is_demo=False)
    demo_event = IntrusionEvent(source="pinky2", is_demo=True)
    db_session.add_all([real_event, demo_event])
    db_session.commit()

    reset_demo_data(db_session)

    remaining = db_session.query(IntrusionEvent).all()
    assert len(remaining) == 1
    assert remaining[0].source == "pinky1"


def test_예약과_배달요청도_is_demo만_지운다(db_session):
    member = _make_member(db_session)
    book = _make_book(db_session)

    db_session.add_all([
        Reservation(member_id=member.id, book_id=book.id, status="waiting", is_demo=False),
        Reservation(member_id=member.id, book_id=book.id, status="waiting", is_demo=True),
        DeliveryRequest(
            member_id=member.id, book_id=book.id, kind="read",
            pickup="문학-1", dropoff="테이블-1번-좌", is_demo=False,
        ),
        DeliveryRequest(
            member_id=member.id, book_id=book.id, kind="read",
            pickup="문학-1", dropoff="테이블-1번-좌", is_demo=True,
        ),
    ])
    db_session.commit()

    reset_demo_data(db_session)

    assert db_session.query(Reservation).count() == 1
    assert db_session.query(DeliveryRequest).count() == 1
```

- [ ] **Step 4: 테스트 실행해서 실패 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_reset_demo_data.py -v`
Expected: FAIL — `ImportError`(아직 `reset_demo_data(db)` 함수가 없음, `is_demo` 컬럼도 모델에 없어서 `TypeError` 가능).

- [ ] **Step 5: `reset_demo_data.py` 리팩터 — 세션을 받는 함수로 분리 + is_demo 필터**

`aba_service/backend/scripts/reset_demo_data.py`의 `main()` 함수 정의부(현재 `def main() -> None:` 부터 파일 끝까지)를 다음으로 교체:

```python
def reset_demo_data(db) -> dict[str, int]:
    """핵심 로직 — 세션을 받아 데모(`is_demo=True`) 행만 지운다. 실제 이력은 절대 안 지운다."""
    n_res = db.query(Reservation).filter(Reservation.is_demo.is_(True)).delete()
    n_req = db.query(DeliveryRequest).filter(DeliveryRequest.is_demo.is_(True)).delete()
    n_loans = db.query(Loan).filter(Loan.is_demo.is_(True)).delete()
    # TaskLog 는 is_demo 컬럼이 없다 — task_id="demo-task-*" 네이밍이 이미 마커다.
    n_logs = db.query(TaskLog).filter(TaskLog.task_id.like("demo-%")).delete(
        synchronize_session=False
    )
    n_intrusions = db.query(IntrusionEvent).filter(IntrusionEvent.is_demo.is_(True)).delete()
    # DemoRobotState 는 태생부터 전부 데모 데이터라(파일 상단 docstring 참고) 통째로 비워도 안전.
    n_robots = db.query(DemoRobotState).delete()
    db.commit()

    n_books = (
        db.query(Book).filter(Book.title_kr.like("%데모도서%")).delete(
            synchronize_session=False
        )
    )
    db.commit()

    db.query(Book).update({Book.in_stock: True, Book.unavailable: False})
    db.commit()

    return {
        "예약": n_res, "요청": n_req, "대출": n_loans, "작업로그": n_logs,
        "침입이력": n_intrusions, "로봇상태": n_robots, "데모도서": n_books,
    }


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        counts = reset_demo_data(db)
        print(
            f"[reset_demo_data] 삭제: 예약={counts['예약']} 요청={counts['요청']} "
            f"대출={counts['대출']} 작업로그={counts['작업로그']} "
            f"침입이력={counts['침입이력']} 로봇상태={counts['로봇상태']} "
            f"데모도서={counts['데모도서']} — 남은 도서 재고 전부 대출가능으로 복구"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

파일 상단 docstring(`reset_demo_data.py:1-21`)에서 "표시(마커)가 없어서... 진짜 이력도 같이 지워진다"는 경고 문단을 삭제하고, 다음으로 교체:

```python
"""데모/시연용으로 쌓인 대출·요청·예약·침입이력과 데모 도서를 지운다.

⚠️ `cb_loans`/`cb_delivery_requests`/`cb_reservations`/`cb_intrusion_events`는
`is_demo=True`인 행만 지운다 — `seed_demo_data.py`가 만든 행은 전부 이 플래그가
켜져 있고, 실제 사용자 활동으로 생긴 행은 절대 지워지지 않는다.
`cb_task_logs`는 `task_id`가 `demo-`로 시작하는 행만 지운다(같은 이유의 마커).

`cb_demo_robot_states`는 성격이 다르다 — 이 표엔 처음부터 데모 데이터만 들어간다(실제
로봇 상태는 FMS 텔레메트리에서만 오고 여기 저장되지 않는다), 그래서 구분 없이 통째로
비워도 안전하다.

실행 (aba_service/backend 에서):
    .venv/bin/python scripts/reset_demo_data.py
"""
```

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_reset_demo_data.py -v`
Expected: PASS

- [ ] **Step 7: 실패하는 테스트 작성 — 중복 대출 방지**

`aba_service/backend/tests/test_seed_demo_data_loan_dedup.py`:

```python
"""seed_demo_data 의 대출 생성 — 같은 책이 동시에 두 번 대출중이 되지 않는지."""

from scripts.seed_demo_data import _would_create_duplicate_active_loan


def test_이미_대출중인_책에_또_대출중을_얹으면_중복으로_판정():
    assert _would_create_duplicate_active_loan(1, True, {1}) is True


def test_다른_책이면_중복_아님():
    assert _would_create_duplicate_active_loan(2, True, {1}) is False


def test_반납완료로_판정되는_대출은_애초에_중복_아님():
    assert _would_create_duplicate_active_loan(1, False, {1}) is False
```

- [ ] **Step 8: 테스트 실행해서 실패 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_seed_demo_data_loan_dedup.py -v`
Expected: FAIL — `ImportError: cannot import name '_would_create_duplicate_active_loan'`

- [ ] **Step 9: `seed_demo_data.py`에 중복 방지 로직 추가 + is_demo 세팅**

`seed_demo_data.py` 상단(모듈 레벨 상수들 다음, `def _top_up_books` 앞)에 추가:

```python
def _would_create_duplicate_active_loan(
    book_id: int, is_active: bool, active_book_ids: set[int]
) -> bool:
    """이 대출을 그대로 대출중으로 확정하면 같은 책이 두 번째로 대출중이 되는가."""
    return is_active and book_id in active_book_ids
```

`seed_demo_data.py:170-192`(대출 이력 생성 루프, `for _ in range(N_LOANS):` 부터 `db.commit()` 까지)를 다음으로 교체:

```python
        # 1) 대출 이력 — 대부분 반납 완료, 일부는 지금 대출중(자연히 연체/반납임박도 섞인다).
        # active_book_ids 로 "지금 대출중" 인 책을 추적해 같은 책이 동시에 두 번
        # 대출중이 되지 않게 한다(현실에서 불가능한 상태라 화면 집계가 깨진다).
        active_book_ids: set[int] = set()
        for _ in range(N_LOANS):
            member = random.choice(members)
            book = random.choice(books)
            borrowed_at = now - timedelta(days=random.uniform(1, DAYS_BACK))
            due_at = borrowed_at + timedelta(days=LOAN_DAYS)
            returned_at = borrowed_at + timedelta(days=random.uniform(1, LOAN_DAYS + 5))
            is_active = returned_at > now
            if _would_create_duplicate_active_loan(book.id, is_active, active_book_ids):
                returned_at = borrowed_at + timedelta(days=random.uniform(1, LOAN_DAYS))
                is_active = False
            if is_active:
                returned_at = None
                status = "borrowed"
                active_book_ids.add(book.id)
            else:
                status = "returned"
            db.add(
                Loan(
                    member_id=member.id,
                    book_id=book.id,
                    status=status,
                    borrowed_at=borrowed_at,
                    due_at=due_at,
                    returned_at=returned_at,
                    is_demo=True,
                )
            )
        db.commit()
```

`seed_demo_data.py:194-211`(반납임박 전용 배치)를 다음으로 교체 — 역시 `active_book_ids`를 재사용해 중복을 막는다:

```python
        # 1-1) 반납 임박(1일 이내) 전용 배치 — 위 랜덤 분포만으로는 24시간 창에 잘 안 걸려서
        # 대시보드 "반납 임박(1일 이내)" 리스트가 매번 비어 보이는 걸 막으려고 따로 몇 건 박아둔다.
        # 이미 대출중으로 잡힌 책은 피한다(중복 대출 방지, 위와 같은 이유).
        for _ in range(N_DUE_TOMORROW_LOANS):
            member = random.choice(members)
            candidates = [b for b in books if b.id not in active_book_ids] or books
            book = random.choice(candidates)
            active_book_ids.add(book.id)
            borrowed_at = now - timedelta(days=LOAN_DAYS) + timedelta(hours=random.uniform(1, 23))
            due_at = now + timedelta(hours=random.uniform(1, 23))
            db.add(
                Loan(
                    member_id=member.id,
                    book_id=book.id,
                    status="borrowed",
                    borrowed_at=borrowed_at,
                    due_at=due_at,
                    returned_at=None,
                    is_demo=True,
                )
            )
        db.commit()
```

`seed_demo_data.py:230-244`(`DeliveryRequest(...)` 생성부)와 `seed_demo_data.py:255`(`Reservation(...)` 생성부), `seed_demo_data.py:121-129`(`IntrusionEvent(...)` 생성부, `_seed_intrusions` 함수 안)에 각각 `is_demo=True,` 인자를 추가한다.

- [ ] **Step 10: 테스트 실행해서 통과 확인**

Run: `cd aba_service/backend && .venv/bin/pytest tests/test_seed_demo_data_loan_dedup.py tests/test_reset_demo_data.py -v`
Expected: PASS (전부)

- [ ] **Step 11: 기존 스위트 회귀 확인**

Run: `cd aba_service/backend && .venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 12: 수동 확인 — 실제 시딩으로 중복 없는지**

Run:
```bash
cd aba_service/backend
.venv/bin/python scripts/seed_members.py
.venv/bin/python scripts/seed_books.py
.venv/bin/python scripts/seed_demo_data.py
.venv/bin/python -c "
from app.database import SessionLocal
from app.models import Loan
db = SessionLocal()
active = db.query(Loan).filter(Loan.status == 'borrowed').all()
book_ids = [l.book_id for l in active]
assert len(book_ids) == len(set(book_ids)), '중복 대출 발견!'
print('중복 없음, 대출중', len(book_ids), '건')
"
```
Expected: `AssertionError` 없이 "중복 없음" 출력.

- [ ] **Step 13: 커밋**

```bash
git add aba_service/backend/app/models.py aba_service/backend/app/migrations.py aba_service/backend/scripts/seed_demo_data.py aba_service/backend/scripts/reset_demo_data.py aba_service/backend/tests/test_reset_demo_data.py aba_service/backend/tests/test_seed_demo_data_loan_dedup.py
git commit -m "fix(backend): mark demo rows with is_demo, make reset safe, prevent duplicate active loans"
```

---

### Task 13: 전체 브랜치 검증 (Wave 3)

**Files:** 없음(검증 전용).

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd aba_service/backend && .venv/bin/pytest -q`
Expected: 전체 PASS.

- [ ] **Step 2: 프론트 build + lint**

Run: `cd aba_service/frontend && npm run build && npm run lint`
Expected: 둘 다 PASS.

- [ ] **Step 3: 데모 데이터 재시딩 후 전 페이지 육안 확인**

Run:
```bash
cd aba_service/backend
.venv/bin/python scripts/seed_admin.py     # 이미 있으면 스킵됨
.venv/bin/python scripts/seed_members.py
.venv/bin/python scripts/seed_books.py
.venv/bin/python scripts/seed_demo_data.py
.venv/bin/uvicorn app.main:app --reload --port 8000
```
별도 터미널: `cd aba_service/frontend && bun --bun run dev` → `0.0.0.0:3000/admin` 로그인 후:
- 대시보드(`/admin`): Task 2 추출 전후로 스크린샷이 있다면 픽셀 대조, 없다면 4개 카드의 차트가 이전과 동일한 값/모양으로 보이는지 세밀히 확인.
- `/admin/members`, `/admin/books`, `/admin/robots`, `/admin/security`, `/admin/tasks`, `/admin/approvals`, `/admin/alerts`, `/admin/users` — 각 화면이 스크롤 없이 한눈에 들어오는지, 데모 데이터로 모든 상태(특히 robots 페이지 우측 패널)가 실제로 채워져 보이는지.

이 스텝은 find-skills로 스크린샷/브라우저 자동화 스킬을 임시 설치해 자동 캡처하는 방식을 우선 시도한다(사용자 요청 #8) — 설치했다면 이 Task가 끝난 뒤 반드시 삭제한다(사용자 요청 #5/#10 "스킬 다시 지워놓을 것"). 설치가 여의치 않으면 위 수동 확인으로 대체하고 그 사실을 최종 보고에 명시한다.

- [ ] **Step 4: whole-branch 코드 리뷰**

가장 강력한 모델로 브랜치 전체 diff를 리뷰(코드 품질 + PRD `docs/agents/prd-librarian-admin-ui.md`의 User Story 21개 커버리지 대조). Critical/Important 발견 시 수정 후 재리뷰.

- [ ] **Step 5: `finishing-a-development-branch` 호출**

`superpowers:finishing-a-development-branch` 스킬로 병합/PR 등 완료 옵션을 사용자에게 제시한다(실행은 사용자 몫 — merge/push는 컨트롤러가 하지 않는다).

---

## Self-Review 체크리스트 (작성자가 이미 수행)

1. **Spec coverage** — PRD의 User Story 1~21은 Task 1(fallback)/Task 2(차트 추출)/Task 3(정렬 훅)/Task 4(회원)/Task 5(도서)/Task 6(모니터링)/Task 7(보안)/Task 8(작업지시)/Task 9(운영 경량)에 전부 대응됨. User Story 21(대시보드 무변경)은 Task 2 Step 3 + Task 13 Step 3에서 검증. Task 11/12는 PRD 범위 밖이지만 codex adversarial review(5단계)가 찾은 critical 1건 + high 2건을 대응하며, 사용자가 명시적으로 이 plan에 편입을 승인했다.
2. **Placeholder scan** — 각 Task의 코드 블록은 실제 컴파일 가능한 완전한 코드다. "TODO"/"적절히 처리" patterns 없음.
3. **Type consistency** — `RobotRow`/`OrderRow`/`TaskKind`/`AdminBook` 등은 모두 `ops-api.ts`에 이미 정의된 기존 타입을 그대로 재사용했고, 새로 만든 `StackedStatusBar`/`useSortableTable`/`TaskBookPickerDialog`의 시그니처는 Task 2/3/8 전체에서 동일하게 참조됨. Task 12의 `is_demo` 컬럼은 Task 11의 `MIGRATIONS` 리스트와 `models.py` 양쪽에 같은 4개 테이블(`cb_loans`/`cb_reservations`/`cb_delivery_requests`/`cb_intrusion_events`)로 일치시켰다.
4. **codex 발견 커버리지** — [critical] reset 데이터파괴 → Task 12. [high] 중복 대출 → Task 12. [high] 마이그레이션 누락 → Task 11. 3건 전부 대응.
