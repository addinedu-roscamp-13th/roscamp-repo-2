import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Search as SearchIcon, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { AppShell } from "@/components/AppShell";
import { BookRow } from "@/components/BookRow";
import { fetchBook, fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";
import { useDebounced } from "@/lib/use-debounced";
import {
  APPROVAL_LABEL,
  getToken,
  memberApi,
  TABLES,
  type DeliveryRequestOut,
} from "@/lib/member";

// 쿼리스트링은 문자열로 들어온다 — `z.number()` 는 통과하지 못한다.
const requestSearchSchema = z.object({
  bookId: z.coerce.number().int().positive().optional(),
});

export const Route = createFileRoute("/request")({
  validateSearch: requestSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — 도서 요청" }] }),
  component: RequestPage,
});

/**
 * 도서 요청 — 3단계 위저드.
 *
 * (1) 책 고르기 → (2) 어떻게 받을지 고르기 → (3) 접수 결과.
 * 상세 시트나 LiBi 가 `?bookId=` 를 달고 보내면 1단계를 건너뛰고 2단계에서 시작한다.
 *
 * ## 두 요청의 차이
 * - **자리로 받기(열람)** — 승인 없이 바로 로봇이 움직인다. 책이 관내에 남기 때문.
 * - **대여 신청** — 사서 승인 뒤에야 로봇이 움직인다. 반출이라 확인이 필요하다.
 *
 * ## 대여 중 / 대출 불가 도서
 * 절대 2단계(제출 경로)에 닿으면 안 된다 — 로봇이 없는 책을 찾으러 가거나, 대출이
 * 막힌 책을 요청하게 된다. 목록의 버튼 단계에서도, `?bookId=` 딥링크 진입 시에도
 * 똑같이 막는다(백엔드도 409 로 막지만, 눌러야 알게 하지 않는다).
 */
type Step = 1 | 2 | 3;
type Mode = "read" | "borrow";

function RequestPage() {
  const { lang, tr } = useI18n();
  const navigate = useNavigate();
  const loggedIn = getToken() !== null;
  const { bookId } = Route.useSearch();

  const [step, setStep] = useState<Step>(1);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query);
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [picked, setPicked] = useState<CatalogBook | null>(null);
  const [lastRequest, setLastRequest] = useState<DeliveryRequestOut | null>(
    null,
  );
  const [mode, setMode] = useState<Mode>("read");
  const [table, setTable] = useState(TABLES[0].value);
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState<DeliveryRequestOut[]>([]);

  useEffect(() => {
    let cancelled = false;
    void fetchCatalog({ q: debouncedQuery.trim() || null }).then((rows) => {
      if (!cancelled) setBooks(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  // 상세 시트나 LiBi 가 `?bookId=` 를 달고 보내면 1단계는 이미 끝난 셈이다.
  // 한 건만 조회한다 — 카탈로그 200 권을 받아 뒤지면 장서가 늘 때 못 찾는다.
  useEffect(() => {
    if (!bookId) return;
    let cancelled = false;
    void fetchBook(bookId).then((hit) => {
      if (cancelled) return;
      if (!hit) {
        toast.error("그 책을 찾지 못했어요. 다시 골라주세요");
        return; // 1단계에 머문다
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

  const reserve = async (book: CatalogBook) => {
    try {
      await memberApi.reserve(book.bookId);
      toast.success("예약했습니다. 반납되면 알려드릴게요");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "예약하지 못했습니다");
    }
  };

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

        {/* 1단계 — 책 고르기 */}
        {step === 1 && (
          <section className="mt-5">
            <h2 className="text-sm font-bold text-foreground">1. 책 고르기</h2>
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

            <div className="mt-3 space-y-2">
              {books.length === 0 ? (
                <p className="rounded-2xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
                  검색 결과가 없습니다.
                </p>
              ) : null}
              {books.slice(0, 20).map((b) => (
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
              ))}
            </div>
          </section>
        )}

        {/* 2단계 — 어떻게 받을지 */}
        {step === 2 && (
          <section className="mt-5">
            <h2 className="text-sm font-bold text-foreground">
              2. 어떻게 받을까요?
            </h2>

            {picked ? (
              <div className="mt-2">
                <BookRow book={picked} />
              </div>
            ) : null}

            <div className="mt-3 space-y-3">
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
                disabled={busy || !picked}
                className="h-12 w-full rounded-2xl bg-primary text-sm font-bold text-primary-foreground disabled:opacity-50"
              >
                {busy
                  ? "접수 중..."
                  : mode === "read"
                    ? `«${picked?.title[lang] ?? ""}» 자리로 받기`
                    : `«${picked?.title[lang] ?? ""}» 대여 신청하기`}
              </button>
            </div>
          </section>
        )}

        {/* 3단계 — 접수 결과 + 내 요청. 종착점이라 제출 버튼이 없다. */}
        {step === 3 && (
          <section className="mt-5">
            {lastRequest && (
              <div className="rounded-2xl border-2 border-primary/40 bg-primary-soft/40 p-4">
                <p className="text-sm font-bold text-foreground">접수됐어요</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  #{lastRequest.id} · «{lastRequest.book_title}» ·{" "}
                  {lastRequest.kind === "borrow" ? "대여" : "열람"} ·{" "}
                  {lastRequest.dropoff} · {APPROVAL_LABEL[lastRequest.approval]}
                </p>
              </div>
            )}

            <div className="mt-6">
              <div className="mb-2 flex items-end justify-between">
                <h2 className="text-sm font-bold text-foreground">내 요청</h2>
                <Link to="/me" className="text-xs font-medium text-primary">
                  내 정보에서 전체 보기 →
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
                            {r.kind === "borrow" ? "대여" : "열람"} ·{" "}
                            {r.dropoff}
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
            </div>
          </section>
        )}

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
                void navigate({ to: "/request", search: {} }); // ?bookId= 를 지운다
              }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-semibold text-muted-foreground"
            >
              새 요청 하기
            </button>
          )}
          <div className="flex-1" />
          <span className="text-xs text-muted-foreground">{step} / 3</span>
        </div>
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
