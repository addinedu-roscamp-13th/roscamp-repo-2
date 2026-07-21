import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { CATEGORY_LABEL, ops } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/insight")({
  head: () => ({ meta: [{ title: "LiBi Admin — 통합 검색 · 통계" }] }),
  component: InsightPage,
});

/** 통합 검색(도서·위치·회원) + 대출/작업 통계. */

type Stats = Awaited<ReturnType<typeof ops.stats>>;
type SearchResult = Awaited<ReturnType<typeof ops.search>>;

function InsightPage() {
  const [q, setQ] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    ops
      .stats()
      .then(setStats)
      .catch((e) => setErr(e instanceof Error ? e.message : "통계 실패"));
  }, []);

  useEffect(() => {
    if (!q.trim()) {
      setResult(null);
      return;
    }
    const t = setTimeout(() => {
      ops
        .search(q.trim())
        .then(setResult)
        .catch((e) => setErr(e instanceof Error ? e.message : "검색 실패"));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <AdminShell title="통합 검색 · 통계">
      <div className="space-y-4">
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        {/* 통합 검색 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-2 text-sm font-semibold">통합 검색</h3>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="도서 제목·저자, 서가 이름, 회원 아이디·이름"
            className="h-10 w-full rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
          />

          {result ? (
            <div className="mt-4 grid gap-4 md:grid-cols-3">
              <Group title={`도서 (${result.books.length})`}>
                {result.books.map((b) => (
                  <Row
                    key={b.id}
                    main={`${b.cover} ${b.title_kr}`}
                    sub={`${b.author} · ${b.zone}`}
                  />
                ))}
              </Group>
              <Group title={`회원 (${result.members.length})`}>
                {result.members.map((m) => (
                  <Row
                    key={m.id}
                    main={m.full_name ?? m.username}
                    sub={`@${m.username}`}
                  />
                ))}
              </Group>
              <Group title={`서가 (${result.zones.length})`}>
                {result.zones.map((z) => (
                  <Row key={z} main={z} sub="내비 정점" />
                ))}
              </Group>
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">
              검색어를 입력하면 도서·회원·서가를 한 번에 찾습니다.
            </p>
          )}
        </section>

        {/* 통계 */}
        {stats ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="분야별 대출">
              <Bars
                data={stats.loans_by_category.map((r) => ({
                  label: CATEGORY_LABEL[r.category] ?? r.category,
                  value: r.count,
                }))}
              />
            </Panel>
            <Panel title="인기 도서 TOP">
              <Bars
                data={stats.top_books.map((r) => ({
                  label: r.title,
                  value: r.count,
                }))}
              />
            </Panel>
            <Panel title="대출 많은 회원">
              <Bars
                data={stats.top_members.map((r) => ({
                  label: r.username,
                  value: r.count,
                }))}
              />
            </Panel>
            <Panel title="작업 상태 분포">
              {stats.fleet_linked ? (
                <Bars
                  data={stats.tasks_by_status.map((r) => ({
                    label: r.status,
                    value: r.count,
                  }))}
                />
              ) : (
                <p className="text-xs text-muted-foreground">FMS 연결 없음</p>
              )}
            </Panel>
            <Panel title="회원 요청 종류">
              <Bars
                data={stats.requests_by_kind.map((r) => ({
                  label:
                    r.kind === "borrow" ? "대여(안내데스크)" : "열람(테이블)",
                  value: r.count,
                }))}
              />
            </Panel>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold text-muted-foreground">
        {title}
      </h4>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({ main, sub }: { main: string; sub: string }) {
  return (
    <div className="rounded border px-2 py-1.5">
      <div className="truncate text-sm">{main}</div>
      <div className="truncate text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

/** 값 비교만 하면 되는 자리라 축·격자 없이 가로 막대만 쓴다. */
function Bars({ data }: { data: { label: string; value: number }[] }) {
  if (data.length === 0)
    return <p className="text-xs text-muted-foreground">데이터가 없습니다</p>;
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-2">
          <span className="w-28 shrink-0 truncate text-xs">{d.label}</span>
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${(d.value / max) * 100}%` }}
            />
          </span>
          <span className="w-8 shrink-0 text-right text-xs tabular-nums">
            {d.value}
          </span>
        </div>
      ))}
    </div>
  );
}
