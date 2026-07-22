import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { BookOpen, MapPin, Search as SearchIcon, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";
import {
  APPROVAL_LABEL,
  getToken,
  memberApi,
  TABLES,
  type DeliveryRequestOut,
} from "@/lib/member";

export const Route = createFileRoute("/request")({
  head: () => ({ meta: [{ title: "LiBi — 도서 요청" }] }),
  component: RequestPage,
});

/**
 * 도서 요청 — 검색과 분리된 독립 화면.
 *
 * 검색 화면(`/search`)은 이제 찾기만 한다. 요청은 여기서 처음부터 끝까지 끝난다:
 * 책 고르기 → 요청 종류 고르기 → (열람이면) 자리 고르기 → 접수.
 *
 * ## 두 요청의 차이
 * - **자리로 받기(열람)** — 승인 없이 바로 로봇이 움직인다. 책이 관내에 남기 때문.
 * - **대여 신청** — 사서 승인 뒤에야 로봇이 움직인다. 반출이라 확인이 필요하다.
 *
 * ## 대여 중인 책
 * 로봇이 없는 책을 찾으러 가면 안 되므로 **버튼 단계에서 막고** 예약으로 유도한다
 * (백엔드도 409 로 막지만, 눌러야 알게 하지 않는다).
 */
type Mode = "read" | "borrow";

function RequestPage() {
  const { lang, tr } = useI18n();
  const navigate = useNavigate();
  const loggedIn = getToken() !== null;

  const [query, setQuery] = useState("");
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [picked, setPicked] = useState<CatalogBook | null>(null);
  const [mode, setMode] = useState<Mode>("read");
  const [table, setTable] = useState(TABLES[0].value);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState<DeliveryRequestOut[]>([]);

  useEffect(() => {
    let cancelled = false;
    void fetchCatalog({ q: query.trim() || null }).then((rows) => {
      if (!cancelled) setBooks(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [query]);

  const loadMine = useCallback(async () => {
    if (!loggedIn) return;
    try {
      setMine(await memberApi.requests());
    } catch {
      /* 목록을 못 읽어도 요청은 낼 수 있어야 한다 */
    }
  }, [loggedIn]);

  useEffect(() => {
    void loadMine();
  }, [loadMine]);

  const submit = async () => {
    if (!picked) return;
    setNotice(null);
    setError(null);
    setBusy(true);
    try {
      if (mode === "read") {
        const res = await memberApi.requestRead(picked.bookId, table);
        setNotice(
          `«${res.book_title}» 을(를) ${res.dropoff} 로 가져다 드릴게요`,
        );
      } else {
        const res = await memberApi.requestBorrow(picked.bookId);
        setNotice(
          `«${res.book_title}» 대여를 신청했습니다. 사서 승인 후 안내데스크로 가져다 놓을게요`,
        );
      }
      setPicked(null);
      await loadMine();
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청하지 못했습니다");
    } finally {
      setBusy(false);
    }
  };

  const reserve = async (book: CatalogBook) => {
    setNotice(null);
    setError(null);
    try {
      await memberApi.reserve(book.bookId);
      setNotice("예약했습니다. 반납되면 알려드릴게요");
    } catch (err) {
      setError(err instanceof Error ? err.message : "예약하지 못했습니다");
    }
  };

  if (!loggedIn) {
    return (
      <AppShell>
        <div className="px-5 pb-10 pt-6">
          <h1 className="text-xl font-bold text-foreground">도서 요청</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            로봇이 책을 가져다 드립니다. 요청하려면 로그인이 필요해요.
          </p>
          <button
            onClick={() => navigate({ to: "/login" })}
            className="mt-6 h-12 w-full rounded-2xl bg-primary text-sm font-bold text-primary-foreground"
          >
            로그인하기
          </button>
          <Link
            to="/search"
            className="mt-3 block rounded-2xl border border-dashed border-border p-3 text-center text-xs text-muted-foreground"
          >
            먼저 도서를 찾아볼게요 →
          </Link>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="px-5 pb-10 pt-4">
        <h1 className="text-xl font-bold text-foreground">도서 요청</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          책을 고르고 어떻게 받을지 정하면 로봇이 움직입니다.
        </p>

        {notice ? (
          <p className="mt-3 rounded-xl bg-primary-soft px-3 py-2 text-xs font-medium text-primary">
            {notice}
          </p>
        ) : null}
        {error ? (
          <p className="mt-3 rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {/* 1단계 — 책 고르기
            고르고 나면 목록을 접는다. 20권이 펼쳐진 채로 있으면 2단계까지
            한참 스크롤해야 하는데, 이미 고른 뒤의 목록은 볼 이유가 없다. */}
        <section className="mt-5">
          <h2 className="text-sm font-bold text-foreground">1. 책 고르기</h2>

          {picked ? (
            <div className="mt-2 flex items-center gap-3 rounded-2xl border border-primary bg-card p-3 shadow-card ring-2 ring-primary/30">
              <span
                className={`flex size-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${picked.color} text-2xl`}
              >
                {picked.cover}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-foreground">
                  {picked.title[lang]}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {picked.author}
                </p>
                <span className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                  <MapPin className="size-3" />
                  {picked.zone} · {picked.shelf}
                </span>
              </div>
              <button
                onClick={() => setPicked(null)}
                className="shrink-0 rounded-full bg-secondary px-3 py-1.5 text-xs font-bold text-secondary-foreground"
              >
                다시 고르기
              </button>
            </div>
          ) : (
            <>
              <div className="mt-2 flex items-center gap-2 rounded-2xl border border-border bg-card p-2 shadow-card">
                <SearchIcon className="ml-2 size-4 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={tr("searchPh")}
                  className="flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground"
                />
                {query ? (
                  <button
                    onClick={() => setQuery("")}
                    className="text-muted-foreground"
                    aria-label="clear"
                  >
                    <X className="size-4" />
                  </button>
                ) : null}
              </div>

              {/* 목록만 스크롤한다 — 검색창은 위에 남고 2·3단계는 아래에 붙어 있다.
                  이렇게 두면 검색어를 고쳐 가며 찾는 동안에도 페이지가 안 길어진다. */}
              <div className="mt-3 max-h-[46vh] space-y-2 overflow-y-auto pr-1">
                {books.length === 0 ? (
                  <p className="rounded-2xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
                    검색 결과가 없습니다.
                  </p>
                ) : null}
                {/* 여기 오는 건 아직 아무것도 안 골랐을 때뿐이다(picked 면 위에서 접었다).
                    그래서 "선택됨" 상태를 따로 그릴 일이 없다. */}
                {books.slice(0, 20).map((b) => (
                  <div
                    key={b.id}
                    className="rounded-2xl border border-border bg-card p-3 shadow-card transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`flex size-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${b.color} text-2xl`}
                      >
                        {b.cover}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-foreground">
                          {b.title[lang]}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {b.author}
                        </p>
                        <span className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                          <MapPin className="size-3" />
                          {b.zone} · {b.shelf}
                        </span>
                      </div>
                      {/* 대여 중이면 요청 자체를 못 고르게 하고 예약으로 보낸다 */}
                      {b.inStock ? (
                        <button
                          onClick={() => setPicked(b)}
                          className="shrink-0 rounded-full bg-secondary px-3 py-1.5 text-xs font-bold text-secondary-foreground"
                        >
                          선택
                        </button>
                      ) : (
                        <button
                          onClick={() => void reserve(b)}
                          className="shrink-0 rounded-full bg-muted px-3 py-1.5 text-xs font-bold text-muted-foreground"
                        >
                          대출 중 · 예약
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>

        {/* 2단계 — 어떻게 받을지 */}
        <section className="mt-6">
          <h2 className="text-sm font-bold text-foreground">
            2. 어떻게 받을까요?
          </h2>
          {!picked ? (
            <p className="mt-2 rounded-2xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              먼저 책을 골라주세요
            </p>
          ) : (
            <div className="mt-2 space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <ModeCard
                  active={mode === "read"}
                  onClick={() => setMode("read")}
                  emoji="📖"
                  title="자리로 받기"
                  sub="바로 배달 · 승인 불필요"
                />
                <ModeCard
                  active={mode === "borrow"}
                  onClick={() => setMode("borrow")}
                  emoji="🧾"
                  title="대여 신청"
                  sub="사서 승인 후 안내데스크"
                />
              </div>

              {mode === "read" ? (
                <div className="rounded-2xl border border-border bg-card p-3 shadow-card">
                  <p className="mb-2 text-[11px] font-semibold text-foreground">
                    어느 자리로 가져다 드릴까요?
                  </p>
                  <div className="grid grid-cols-3 gap-1.5">
                    {TABLES.map((t) => (
                      <button
                        key={t.value}
                        onClick={() => setTable(t.value)}
                        className={`rounded-lg border py-2 text-[10px] font-medium ${
                          table === t.value
                            ? "border-primary bg-primary-soft text-primary"
                            : "border-border bg-card text-foreground"
                        }`}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="rounded-2xl bg-amber-500/10 px-3 py-2 text-[11px] font-medium text-amber-700">
                  대여는 사서 승인이 필요합니다. 승인되면 로봇이 안내데스크로
                  책을 가져다 놓고, 대출 확정은 사서가 합니다.
                </p>
              )}

              <button
                onClick={() => void submit()}
                disabled={busy}
                className="h-12 w-full rounded-2xl bg-primary text-sm font-bold text-primary-foreground disabled:opacity-50"
              >
                {busy
                  ? "접수 중..."
                  : mode === "read"
                    ? `«${picked.title[lang]}» 자리로 받기`
                    : `«${picked.title[lang]}» 대여 신청하기`}
              </button>
            </div>
          )}
        </section>

        {/* 3단계 — 내 요청 */}
        <section className="mt-8">
          <div className="mb-2 flex items-end justify-between">
            <h2 className="text-sm font-bold text-foreground">내 요청</h2>
            <Link to="/me" className="text-xs font-medium text-primary">
              전체 보기 →
            </Link>
          </div>
          {mine.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              아직 요청이 없습니다
            </p>
          ) : (
            <div className="space-y-2">
              {mine.slice(0, 5).map((r) => (
                <div
                  key={r.id}
                  className="rounded-2xl border border-border bg-card p-3 shadow-card"
                >
                  <div className="flex items-center gap-3">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted text-xl">
                      {r.kind === "borrow" ? "🧾" : "📖"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-foreground">
                        {r.book_title}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {r.kind === "borrow" ? "대여" : "열람"} · {r.dropoff}
                      </p>
                    </div>
                    <ApprovalBadge value={r.approval} progress={r.status} />
                  </div>
                  {r.reject_reason ? (
                    <p className="mt-2 rounded-lg bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
                      반려 사유: {r.reject_reason}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>

        <Link
          to="/search"
          className="mt-6 flex items-center justify-center gap-2 rounded-xl border border-dashed border-border p-3 text-center text-xs text-muted-foreground"
        >
          <BookOpen className="size-3.5" />
          도서 검색으로 이동
        </Link>
      </div>
    </AppShell>
  );
}

function ModeCard({
  active,
  onClick,
  emoji,
  title,
  sub,
}: {
  active: boolean;
  onClick: () => void;
  emoji: string;
  title: string;
  sub: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-2xl border-2 p-3 text-left transition-colors ${
        active ? "border-primary bg-primary-soft" : "border-border bg-card"
      }`}
    >
      <span className="text-2xl">{emoji}</span>
      <p
        className={`mt-1 text-sm font-bold ${active ? "text-primary" : "text-foreground"}`}
      >
        {title}
      </p>
      <p className="text-[11px] text-muted-foreground">{sub}</p>
    </button>
  );
}

/** 승인 상태를 우선 보여주고, 승인된 건에 한해 로봇 진행 상황을 보여준다. */
function ApprovalBadge({
  value,
  progress,
}: {
  value: DeliveryRequestOut["approval"];
  progress: string | null;
}) {
  if (value === "APPROVED") {
    return (
      <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
        {progress ?? APPROVAL_LABEL.APPROVED}
      </span>
    );
  }
  const tone =
    value === "REJECTED"
      ? "bg-destructive/10 text-destructive"
      : "bg-amber-500/15 text-amber-700";
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${tone}`}
    >
      {APPROVAL_LABEL[value]}
    </span>
  );
}
