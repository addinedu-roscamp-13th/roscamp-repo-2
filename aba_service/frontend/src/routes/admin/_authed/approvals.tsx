import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { ops, type ApprovalRow } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/approvals")({
  head: () => ({ meta: [{ title: "LiBi Admin — 대여 승인" }] }),
  component: ApprovalsPage,
});

/**
 * 대여 승인 — 회원의 「대여 신청」을 사서가 확인하는 곳.
 *
 * ## 여기서 승인해야 로봇이 움직인다
 * 회원이 신청한 순간에는 도서관 DB 에 기록만 남고 관제(FMS)는 아무것도 모른다.
 * 승인을 누르면 그때 FMS 주문이 생기고 배차가 일어난다. 반려하면 주문은 끝내 생기지 않고
 * 회원 화면에 사유와 함께 반려로 보인다.
 *
 * 「자리로 받기」(열람)는 책이 관내에 남아 되돌리기 쉬우므로 승인 없이 바로 나간다 —
 * 그래서 이 목록에는 대여만 올라온다.
 */
function ApprovalsPage() {
  const [pending, setPending] = useState<ApprovalRow[]>([]);
  const [recent, setRecent] = useState<ApprovalRow[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [reason, setReason] = useState<Record<number, string>>({});
  const [deleteTarget, setDeleteTarget] = useState<ApprovalRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await ops.approvals();
      setPending(res.pending);
      setRecent(res.recent);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (row: ApprovalRow, kind: "approve" | "reject") => {
    setErr(null);
    setMsg(null);
    setBusy(row.id);
    try {
      if (kind === "approve") {
        const res = await ops.approve(row.id);
        setMsg(
          `«${res.book_title}» 승인 — 주문 ${res.fms_task_id} 이 관제에 접수됐습니다`,
        );
      } else {
        const res = await ops.reject(row.id, reason[row.id] ?? "");
        setMsg(`«${res.book_title}» 반려 — ${res.reject_reason}`);
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리 실패");
    } finally {
      setBusy(null);
    }
  };

  // 재고 있어서 바로 승인 가능한 신청을 위로 — 지금 당장 처리할 수 있는 것부터 본다.
  const sortedPending = [...pending].sort(
    (a, b) => Number(b.book_in_stock) - Number(a.book_in_stock),
  );

  const removeHistory = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await ops.deleteApprovalHistory(deleteTarget.id);
      toast.success("이력을 삭제했습니다");
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제 실패");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AdminShell title="대여 승인">
      <div className="flex h-full flex-col gap-4">
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

        <div className="flex min-h-0 flex-1 gap-4">
          <section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">
            <h3 className="text-sm font-semibold">
              승인 대기{" "}
              <span className="text-xs font-normal text-muted-foreground">
                ({pending.length}건)
              </span>
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              승인하면 그때 관제에 주문이 생기고 로봇이 움직입니다. 승인 전에는
              아무 일도 일어나지 않습니다.
            </p>

            <div className="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto">
              {pending.length === 0 ? (
                <p className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
                  대기 중인 대여 신청이 없습니다
                </p>
              ) : (
                sortedPending.map((r) => (
                  <div key={r.id} className="rounded-md border p-3">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="text-sm font-semibold">
                        {r.book_title}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {r.member_name ?? r.member_username} (@
                        {r.member_username})
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {r.pickup} → {r.dropoff}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {r.created_at.slice(0, 16).replace("T", " ")}
                      </span>
                      {!r.book_in_stock ? (
                        <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs font-bold text-rose-700">
                          그 사이 대여됨 — 승인 불가
                        </span>
                      ) : null}
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <button
                        onClick={() => void act(r, "approve")}
                        disabled={busy === r.id || !r.book_in_stock}
                        className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                      >
                        승인 · 로봇 배차
                      </button>
                      <input
                        value={reason[r.id] ?? ""}
                        onChange={(e) =>
                          setReason((m) => ({ ...m, [r.id]: e.target.value }))
                        }
                        placeholder="반려 사유 (선택)"
                        className="h-8 min-w-[12rem] flex-1 rounded-md border px-2 text-xs outline-none focus:ring-2 focus:ring-primary"
                      />
                      <button
                        onClick={() => void act(r, "reject")}
                        disabled={busy === r.id}
                        className="rounded-md bg-secondary px-3 py-1.5 text-xs font-semibold text-secondary-foreground disabled:opacity-50"
                      >
                        반려
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">
            <h3 className="mb-3 text-sm font-semibold">
              최근 처리{" "}
              <span className="text-xs font-normal text-muted-foreground">
                ({recent.length}건)
              </span>
            </h3>
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="pb-2 pr-3">도서</th>
                    <th className="pb-2 pr-3">회원</th>
                    <th className="pb-2 pr-3">결과</th>
                    <th className="pb-2 pr-3">주문</th>
                    <th className="pb-2 pr-3">처리</th>
                    <th className="pb-2 pr-3">사유</th>
                    <th className="pb-2 pr-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {recent.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-3 text-muted-foreground">
                        처리 내역이 없습니다
                      </td>
                    </tr>
                  ) : (
                    recent.map((r) => (
                      <tr key={r.id} className="border-t align-top">
                        <td className="py-2 pr-3">{r.book_title}</td>
                        <td className="py-2 pr-3 text-xs">
                          {r.member_username}
                        </td>
                        <td className="py-2 pr-3">
                          <span
                            className={`rounded px-1.5 py-0.5 text-xs font-bold ${
                              r.approval === "APPROVED"
                                ? "bg-emerald-500/15 text-emerald-700"
                                : "bg-rose-500/15 text-rose-700"
                            }`}
                          >
                            {r.approval === "APPROVED" ? "승인" : "반려"}
                          </span>
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs">
                          {r.fms_task_id || "—"}
                        </td>
                        <td className="py-2 pr-3 text-xs text-muted-foreground">
                          {r.decided_by ?? "—"}
                          {r.decided_at
                            ? ` · ${r.decided_at.slice(0, 16).replace("T", " ")}`
                            : ""}
                        </td>
                        <td className="py-2 pr-3 text-xs text-muted-foreground">
                          {r.reject_reason ?? "—"}
                        </td>
                        <td className="py-2 pr-3">
                          <button
                            onClick={() => setDeleteTarget(r)}
                            className="rounded bg-rose-500/10 px-2 py-1 text-xs font-semibold text-rose-700"
                          >
                            삭제
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="승인 이력 삭제"
        description={
          <>
            «{deleteTarget?.book_title}» 이력을 삭제할까요? 진행 중인 요청은
            삭제할 수 없습니다(그럴 경우 실패 메시지가 표시됩니다).
          </>
        }
        onConfirm={() => void removeHistory()}
        busy={deleting}
      />
    </AdminShell>
  );
}
