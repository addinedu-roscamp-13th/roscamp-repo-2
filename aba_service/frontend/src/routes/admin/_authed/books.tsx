import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { NAMED_WAYPOINTS } from "@/lib/map-waypoints";
import {
  CATEGORY_LABEL,
  ops,
  type AdminBook,
  type ShelfRow,
} from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/books")({
  head: () => ({ meta: [{ title: "LiBi Admin — 도서 · 서가 관리" }] }),
  component: BooksPage,
});

/**
 * 도서 관리(등록·수정·삭제·재고·위치) + 서가 관리.
 *
 * `zone` 은 **로봇 내비 그래프의 정점 이름**이다 — 여기서 고른 값이 그대로 배달 주문의
 * pickup 이 되므로, 자유 입력이 아니라 waypoint 목록에서만 고르게 한다.
 */

const SHELF_WAYPOINTS = NAMED_WAYPOINTS.filter((w) => w.kind === "shelf");
const CATEGORIES = Object.keys(CATEGORY_LABEL);

const EMPTY = {
  title_kr: "",
  author: "",
  category: "literature",
  cover: "📘",
  zone: SHELF_WAYPOINTS[0]?.name ?? "문학-1",
  shelf: "",
  in_stock: true,
};

function BooksPage() {
  const [books, setBooks] = useState<AdminBook[]>([]);
  const [shelves, setShelves] = useState<ShelfRow[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [form, setForm] = useState<typeof EMPTY & { id?: number }>({
    ...EMPTY,
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [b, s] = await Promise.all([ops.books(q, cat), ops.shelves()]);
      setBooks(b);
      setShelves(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, [q, cat]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      setForm({ ...EMPTY });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리 실패");
    }
  };

  const save = () =>
    act(
      () => (form.id ? ops.updateBook(form.id, form) : ops.createBook(form)),
      form.id ? "수정했습니다" : "등록했습니다",
    );

  return (
    <AdminShell title="도서 · 서가 관리">
      <div className="space-y-4">
        {msg ? (
          <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
            {msg}
          </p>
        ) : null}
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        {/* 서가 관리 — 배치 현황 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-1 text-sm font-semibold">서가 배치</h3>
          <p className="mb-3 text-xs text-muted-foreground">
            서가 이름은 로봇 내비 정점과 같습니다. 도서의 위치를 바꾸면 로봇이
            집으러 가는 곳도 함께 바뀝니다.
          </p>
          <div className="flex flex-wrap gap-2">
            {shelves.map((s) => (
              <div key={s.zone} className="rounded-lg border px-3 py-2">
                <div className="text-sm font-semibold">{s.zone}</div>
                <div className="text-xs text-muted-foreground">
                  {s.total}권 ·{" "}
                  {Object.entries(s.categories)
                    .map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ${n}`)
                    .join(", ")}
                </div>
              </div>
            ))}
            {shelves.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                등록된 서가가 없습니다
              </p>
            ) : null}
          </div>
        </section>

        {/* 등록 / 수정 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">
            {form.id ? `도서 수정 (#${form.id})` : "도서 등록"}
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <L label="제목">
              <input
                value={form.title_kr}
                onChange={(e) => setForm({ ...form, title_kr: e.target.value })}
                className="h-9 w-full rounded-md border px-3 text-sm"
              />
            </L>
            <L label="저자">
              <input
                value={form.author}
                onChange={(e) => setForm({ ...form, author: e.target.value })}
                className="h-9 w-full rounded-md border px-3 text-sm"
              />
            </L>
            <L label="분야">
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="h-9 w-full rounded-md border px-2 text-sm"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {CATEGORY_LABEL[c]}
                  </option>
                ))}
              </select>
            </L>
            <L label="서가 (내비 정점)">
              <select
                value={form.zone}
                onChange={(e) => setForm({ ...form, zone: e.target.value })}
                className="h-9 w-full rounded-md border px-2 text-sm"
              >
                {NAMED_WAYPOINTS.map((w) => (
                  <option key={w.name} value={w.name}>
                    {w.label}
                  </option>
                ))}
              </select>
            </L>
            <L label="청구기호/줄">
              <input
                value={form.shelf}
                onChange={(e) => setForm({ ...form, shelf: e.target.value })}
                placeholder="예: 첫째 줄"
                className="h-9 w-full rounded-md border px-3 text-sm"
              />
            </L>
            <L label="표지">
              <input
                value={form.cover}
                onChange={(e) => setForm({ ...form, cover: e.target.value })}
                className="h-9 w-full rounded-md border px-3 text-sm"
              />
            </L>
            <L label="재고">
              <select
                value={form.in_stock ? "1" : "0"}
                onChange={(e) =>
                  setForm({ ...form, in_stock: e.target.value === "1" })
                }
                className="h-9 w-full rounded-md border px-2 text-sm"
              >
                <option value="1">대출 가능</option>
                <option value="0">대출 중</option>
              </select>
            </L>
            <div className="flex items-end gap-2">
              <button
                onClick={() => void save()}
                disabled={!form.title_kr || !form.author}
                className="h-9 flex-1 rounded-md bg-primary text-sm font-semibold text-primary-foreground disabled:opacity-50"
              >
                {form.id ? "수정" : "등록"}
              </button>
              {form.id ? (
                <button
                  onClick={() => setForm({ ...EMPTY })}
                  className="h-9 rounded-md bg-secondary px-3 text-sm"
                >
                  취소
                </button>
              ) : null}
            </div>
          </div>
        </section>

        {/* 목록 */}
        <section className="rounded-lg border p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              도서 목록{" "}
              <span className="text-xs font-normal text-muted-foreground">
                ({books.length})
              </span>
            </h3>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="제목·저자 검색"
              className="h-8 w-48 rounded-md border px-3 text-sm"
            />
            <select
              value={cat}
              onChange={(e) => setCat(e.target.value)}
              className="h-8 rounded-md border px-2 text-sm"
            >
              <option value="">전체 분야</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="pb-2 pr-3">제목</th>
                  <th className="pb-2 pr-3">저자</th>
                  <th className="pb-2 pr-3">분야</th>
                  <th className="pb-2 pr-3">서가</th>
                  <th className="pb-2 pr-3">재고</th>
                  <th className="pb-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {books.map((b) => (
                  <tr key={b.id} className="border-t">
                    <td className="py-2 pr-3">
                      {b.cover} {b.title_kr}
                    </td>
                    <td className="py-2 pr-3 text-xs">{b.author}</td>
                    <td className="py-2 pr-3 text-xs">
                      {CATEGORY_LABEL[b.category] ?? b.category}
                    </td>
                    <td className="py-2 pr-3 text-xs">
                      {b.zone} {b.shelf}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-bold ${
                          b.in_stock
                            ? "bg-emerald-500/15 text-emerald-700"
                            : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {b.in_stock ? "가능" : "대출중"}
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex gap-1">
                        <button
                          onClick={() =>
                            setForm({
                              id: b.id,
                              title_kr: b.title_kr,
                              author: b.author,
                              category: b.category,
                              cover: b.cover,
                              zone: b.zone,
                              shelf: b.shelf,
                              in_stock: b.in_stock,
                            })
                          }
                          className="rounded bg-secondary px-2 py-1 text-xs"
                        >
                          수정
                        </button>
                        <button
                          onClick={() =>
                            act(() => ops.deleteBook(b.id), "삭제했습니다")
                          }
                          className="rounded bg-rose-500/10 px-2 py-1 text-xs text-rose-700"
                        >
                          삭제
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {books.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-3 text-muted-foreground">
                      도서가 없습니다
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </AdminShell>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1">
      <span className="text-xs font-medium">{label}</span>
      {children}
    </label>
  );
}
