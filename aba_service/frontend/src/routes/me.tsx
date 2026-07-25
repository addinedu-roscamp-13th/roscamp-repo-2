import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  APPROVAL_LABEL,
  memberApi,
  setToken,
  type DeliveryRequestOut,
  type Loan,
  type Member,
  type Reservation,
  type WishlistItem,
} from "@/lib/member";

export const Route = createFileRoute("/me")({
  head: () => ({ meta: [{ title: "LiBi — 내 정보" }] }),
  component: MePage,
});

function fmtDate(iso: string): string {
  return iso.slice(0, 10);
}

function MePage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<Member | null>(null);
  const [loans, setLoans] = useState<Loan[]>([]);
  const [requests, setRequests] = useState<DeliveryRequestOut[]>([]);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [m, l, rq, rs, w] = await Promise.all([
        memberApi.me(),
        memberApi.loans(),
        memberApi.requests(),
        memberApi.reservations(),
        memberApi.wishlist(),
      ]);
      setMe(m);
      setLoans(l);
      setRequests(rq);
      setReservations(rs.filter((r) => r.status !== "cancelled"));
      setWishlist(w);
    } catch (err) {
      // 401 이면 토큰이 이미 지워졌다 — 로그인으로 보낸다.
      const msg = err instanceof Error ? err.message : "불러오지 못했습니다";
      if (msg.includes("로그인")) {
        navigate({ to: "/login" });
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "처리하지 못했습니다");
    }
  };

  const removeRequest = async (id: number) => {
    try {
      await memberApi.deleteRequest(id);
      toast.success("요청 이력을 지웠습니다");
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "지우지 못했습니다");
    }
  };

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

  const logout = () => {
    setToken(null);
    navigate({ to: "/login" });
  };

  if (loading) {
    return (
      <AppShell>
        <p className="px-5 pt-10 text-sm text-muted-foreground">
          불러오는 중...
        </p>
      </AppShell>
    );
  }

  const overdue = loans.filter((l) => l.overdue);
  const dueSoon = loans.filter((l) => l.due_soon);
  const pendingApproval = requests.filter(
    (r) => r.approval === "PENDING_APPROVAL",
  );
  const finishedRequests = requests.filter(
    (r) => r.approval !== "PENDING_APPROVAL",
  );

  return (
    <AppShell>
      <div className="px-5 pb-10 pt-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-foreground">
              {me?.full_name ?? me?.username}
            </h1>
            <p className="text-xs text-muted-foreground">@{me?.username}</p>
          </div>
          <button
            onClick={logout}
            className="rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground"
          >
            로그아웃
          </button>
        </div>

        {/* 요약 타일 */}
        <div className="mt-4 grid grid-cols-4 gap-2">
          {[
            {
              label: "대출",
              n: loans.filter((l) => l.status !== "returned").length,
            },
            { label: "요청", n: requests.length },
            { label: "예약", n: reservations.length },
            { label: "읽고싶은", n: wishlist.length },
          ].map((s) => (
            <div
              key={s.label}
              className="rounded-2xl border border-border bg-card p-3 text-center shadow-card"
            >
              <div className="text-xl font-black text-primary">{s.n}</div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* 급한 것만 먼저 — 연체 · 반납 임박 · 사서 승인 대기 */}
        <div className="mt-3 space-y-2">
          {overdue.length > 0 ? (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-xs font-semibold text-destructive">
              ⚠️ 연체 {overdue.length}권 — 빠르게 반납해 주세요
            </p>
          ) : dueSoon.length > 0 ? (
            <p className="rounded-xl bg-amber-500/15 px-3 py-2 text-xs font-semibold text-amber-700">
              ⏰ 반납 임박 {dueSoon.length}권 (3일 이내)
            </p>
          ) : null}
          {pendingApproval.length > 0 ? (
            <p className="rounded-xl bg-sky-500/15 px-3 py-2 text-xs font-semibold text-sky-700">
              🗂️ 사서 승인 대기 {pendingApproval.length}건
            </p>
          ) : null}
        </div>

        {error ? (
          <p className="mt-3 rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {/* 섹션 — 접이식, 기본은 전부 접힘(요약이 먼저 보여야 한다) */}
        <Accordion type="multiple" className="mt-4">
          <AccordionItem value="loans">
            <AccordionTrigger>{`대출 현황 (${loans.length})`}</AccordionTrigger>
            <AccordionContent>
              <div className="space-y-2">
                {loans.length === 0 ? (
                  <Empty text="대출 중인 도서가 없습니다" />
                ) : (
                  loans.map((l) => (
                    <Card key={l.id}>
                      <Row
                        cover={l.book.cover}
                        title={l.book.title}
                        sub={`${l.book.author} · 반납 ${fmtDate(l.due_at)}`}
                        badge={
                          l.status === "returned"
                            ? { text: "반납 완료", tone: "muted" }
                            : l.overdue
                              ? { text: `연체 ${-l.days_left}일`, tone: "bad" }
                              : {
                                  text: `D-${l.days_left}`,
                                  tone: l.due_soon ? "warn" : "ok",
                                }
                        }
                      />
                      {l.can_extend ? (
                        <button
                          onClick={() => act(() => memberApi.extendLoan(l.id))}
                          className="mt-2 h-9 w-full rounded-lg bg-secondary text-xs font-semibold text-secondary-foreground"
                        >
                          7일 연장 (1회)
                        </button>
                      ) : null}
                    </Card>
                  ))
                )}
              </div>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="requests">
            <div className="flex items-center gap-2">
              <div className="flex-1">
                <AccordionTrigger>{`요청 현황 (${requests.length})`}</AccordionTrigger>
              </div>
              {finishedRequests.length > 0 ? (
                <button
                  onClick={() => void clearFinished()}
                  className="shrink-0 rounded-full border border-border px-2.5 py-1 text-[11px] font-semibold text-muted-foreground"
                >
                  완료/반려 정리
                </button>
              ) : null}
            </div>
            <AccordionContent>
              <div className="space-y-2">
                {requests.length === 0 ? (
                  <Empty text="요청 내역이 없습니다. 「도서 요청」 화면에서 신청해 보세요" />
                ) : (
                  requests.map((r) => (
                    <Card key={r.id}>
                      <Row
                        cover={r.kind === "borrow" ? "🧾" : "📖"}
                        title={r.book_title}
                        sub={`${r.kind === "borrow" ? "대여(안내데스크)" : "열람"} · ${r.dropoff}`}
                        // 승인 상태가 먼저다 — 승인 대기/반려는 로봇 진행 상황 자체가 없다.
                        badge={
                          r.approval === "PENDING_APPROVAL"
                            ? {
                                text: APPROVAL_LABEL.PENDING_APPROVAL,
                                tone: "warn",
                              }
                            : r.approval === "REJECTED"
                              ? { text: APPROVAL_LABEL.REJECTED, tone: "bad" }
                              : {
                                  text: r.status ?? "접수",
                                  tone:
                                    r.status === "COMPLETED" ? "ok" : "warn",
                                }
                        }
                      />
                      {r.reject_reason ? (
                        <p className="mt-2 rounded-lg bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
                          반려 사유: {r.reject_reason}
                        </p>
                      ) : null}
                      {r.leg_count ? (
                        <div className="mt-2">
                          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full bg-primary"
                              style={{
                                width: `${((r.leg_idx ?? 0) / r.leg_count) * 100}%`,
                              }}
                            />
                          </div>
                          <p className="mt-1 text-[10px] text-muted-foreground">
                            진행 {r.leg_idx}/{r.leg_count}
                          </p>
                        </div>
                      ) : null}
                      {r.approval !== "PENDING_APPROVAL" ? (
                        <button
                          onClick={() => void removeRequest(r.id)}
                          className="mt-2 h-8 w-full rounded-lg border border-destructive/30 text-[11px] font-semibold text-destructive"
                        >
                          이력 삭제
                        </button>
                      ) : null}
                    </Card>
                  ))
                )}
              </div>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="reservations">
            <AccordionTrigger>{`예약 (${reservations.length})`}</AccordionTrigger>
            <AccordionContent>
              <div className="space-y-2">
                {reservations.length === 0 ? (
                  <Empty text="예약한 도서가 없습니다" />
                ) : (
                  reservations.map((r) => (
                    <Card key={r.id}>
                      <Row
                        cover={r.book.cover}
                        title={r.book.title}
                        sub={`${r.book.author} · ${fmtDate(r.created_at)} 예약`}
                        badge={{ text: r.status, tone: "warn" }}
                      />
                      <button
                        onClick={() =>
                          act(() => memberApi.cancelReservation(r.id))
                        }
                        className="mt-2 h-9 w-full rounded-lg bg-secondary text-xs font-semibold text-secondary-foreground"
                      >
                        예약 취소
                      </button>
                    </Card>
                  ))
                )}
              </div>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="wishlist">
            <AccordionTrigger>{`읽고 싶은 책 (${wishlist.length})`}</AccordionTrigger>
            <AccordionContent>
              <div className="space-y-2">
                {wishlist.length === 0 ? (
                  <Empty text="담아둔 책이 없습니다" />
                ) : (
                  wishlist.map((w) => (
                    <Card key={w.id}>
                      <Row
                        cover={w.book.cover}
                        title={w.book.title}
                        sub={`${w.book.author} · ${w.book.zone}`}
                        badge={
                          w.book.in_stock
                            ? { text: "대출 가능", tone: "ok" }
                            : { text: "대출 중", tone: "muted" }
                        }
                      />
                      <button
                        onClick={() =>
                          act(() => memberApi.removeWishlist(w.id))
                        }
                        className="mt-2 h-9 w-full rounded-lg bg-secondary text-xs font-semibold text-secondary-foreground"
                      >
                        목록에서 빼기
                      </button>
                    </Card>
                  ))
                )}
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>

        <div className="mt-6 grid grid-cols-2 gap-2">
          <Link
            to="/request"
            className="block rounded-xl border border-dashed border-primary/40 p-3 text-center text-xs font-semibold text-primary"
          >
            도서 요청하기 →
          </Link>
          <Link
            to="/search"
            className="block rounded-xl border border-dashed border-border p-3 text-center text-xs text-muted-foreground"
          >
            도서 검색으로 이동 →
          </Link>
        </div>
      </div>
    </AppShell>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-3 shadow-card">
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
      {text}
    </p>
  );
}

function Row({
  cover,
  title,
  sub,
  badge,
}: {
  cover: string;
  title: string;
  sub: string;
  badge: { text: string; tone: "ok" | "warn" | "bad" | "muted" };
}) {
  const tones = {
    ok: "bg-emerald-500/15 text-emerald-700",
    warn: "bg-amber-500/15 text-amber-700",
    bad: "bg-destructive/10 text-destructive",
    muted: "bg-muted text-muted-foreground",
  };
  return (
    <div className="flex items-center gap-3">
      <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted text-2xl">
        {cover}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-foreground">
          {title}
        </p>
        <p className="truncate text-xs text-muted-foreground">{sub}</p>
      </div>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${tones[badge.tone]}`}
      >
        {badge.text}
      </span>
    </div>
  );
}
