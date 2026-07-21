import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";

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

function MembersPage() {
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [loans, setLoans] = useState<LoanRow[]>([]);
  const [books, setBooks] = useState<BookOption[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [bookQuery, setBookQuery] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  return (
    <AdminShell title="회원 · 대여/반납">
      <div className="space-y-4">
        {/* 요약 */}
        <div className="flex flex-wrap gap-6 rounded-lg border bg-muted/30 p-4">
          <Stat label="회원" value={members.length} />
          <Stat label="대출 중" value={activeLoans.length} />
          <Stat
            label="연체"
            value={overdue.length}
            tone={overdue.length ? "bad" : undefined}
          />
          <Stat label="누적 대출" value={loans.length} />
        </div>

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

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          {/* 회원 목록 */}
          <section className="rounded-lg border p-4">
            <h3 className="mb-3 text-sm font-semibold">회원 목록</h3>
            {loading ? (
              <p className="text-sm text-muted-foreground">불러오는 중...</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="pb-2 pr-3">아이디</th>
                      <th className="pb-2 pr-3">이름</th>
                      <th className="pb-2 pr-3">대출중</th>
                      <th className="pb-2 pr-3">누적</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
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
                        <td className="py-2 pr-3">{m.full_name ?? "—"}</td>
                        <td className="py-2 pr-3 tabular-nums">
                          {m.active_loans}
                        </td>
                        <td className="py-2 pr-3 tabular-nums text-muted-foreground">
                          {m.total_loans}
                        </td>
                      </tr>
                    ))}
                    {members.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="py-3 text-muted-foreground">
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
          <section className="rounded-lg border p-4">
            {selected === null ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                왼쪽에서 회원을 선택하면 대출 이력과 대여 처리가 나옵니다.
              </p>
            ) : (
              <>
                <h3 className="mb-3 text-sm font-semibold">
                  {members.find((m) => m.id === selected)?.full_name ??
                    members.find((m) => m.id === selected)?.username}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    대출 이력
                  </span>
                </h3>

                <div className="mb-4 space-y-1">
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
                <div className="border-t pt-3">
                  <h4 className="mb-2 text-xs font-semibold">대여 처리</h4>
                  <input
                    value={bookQuery}
                    onChange={(e) => setBookQuery(e.target.value)}
                    placeholder="도서 제목 검색 (재고 있는 것만)"
                    className="mb-2 h-9 w-full rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
                  />
                  <div className="max-h-56 space-y-1 overflow-y-auto">
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
    </AdminShell>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "bad";
}) {
  return (
    <div className="flex flex-col">
      <span
        className={`text-xl font-semibold tabular-nums ${tone === "bad" ? "text-rose-600" : ""}`}
      >
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}
