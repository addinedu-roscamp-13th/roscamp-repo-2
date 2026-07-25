import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { ops, opsApi } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/alerts")({
  head: () => ({ meta: [{ title: "LiBi Admin — 작업 로그" }] }),
  component: AlertsPage,
});

/**
 * 작업 알림(완료/실패) + 결과 로그 + 재시도 추적.
 *
 * FMS 는 진행 중인 큐만 들고 있어서 끝난 작업은 치우면 사라진다. 백엔드가 조회할 때마다
 * FMS 를 훑어 종료 작업을 `cb_task_logs` 로 적재하므로, 여기서는 그 보관본을 읽는다.
 */

interface LogRow {
  id: number;
  task_id: string;
  kind: string;
  robot: string | null;
  status: string;
  leg_idx: number;
  leg_count: number;
  retries: number;
  reason: string | null;
  recorded_at: string;
}

interface Alerts {
  tasks: {
    task_id: string;
    kind: string;
    robot: string | null;
    status: string;
    reason: string | null;
    at: string;
  }[];
  intrusions: {
    id: number;
    source: string;
    zone: string | null;
    note: string | null;
    at: string;
    clip_path: string | null;
  }[];
}

const TONE: Record<string, string> = {
  ASSIGNED: "bg-sky-500/15 text-sky-700",
  COMPLETED: "bg-emerald-500/15 text-emerald-700",
  FAILED: "bg-rose-500/15 text-rose-700",
  CANCELLED: "bg-muted text-muted-foreground",
};

const STATUS_LABEL: Record<string, string> = {
  ASSIGNED: "배정",
  COMPLETED: "완료",
  FAILED: "실패",
  CANCELLED: "취소",
};

const fmtTime = (iso: string) => iso.slice(5, 16).replace("T", " ");

function AlertsPage() {
  const [alerts, setAlerts] = useState<Alerts | null>(null);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [filter, setFilter] = useState<
    "" | "ASSIGNED" | "COMPLETED" | "FAILED"
  >("");
  const [err, setErr] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LogRow | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, l] = await Promise.all([
        opsApi<Alerts>("/api/admin/ops/alerts"),
        opsApi<LogRow[]>(
          `/api/admin/ops/logs${filter ? `?status=${filter}` : ""}`,
        ),
      ]);
      setAlerts(a);
      setLogs(l);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, [filter]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const ack = (id: number) =>
    void opsApi(`/api/admin/ops/security/events/${id}/ack`, { method: "POST" })
      .then(load)
      .catch((e) => setErr(e instanceof Error ? e.message : "처리 실패"));

  const resetLogs = async () => {
    if (
      !window.confirm(
        "작업 로그를 전부 초기화할까요?\n\n" +
          "목록에서 감춰지며(감사 위해 행은 보존), FMS 가 같은 종료 작업을 계속 " +
          "돌려줘도 다시 나타나지 않습니다. 새로 생기는 작업은 계속 쌓입니다.",
      )
    )
      return;
    setResetting(true);
    try {
      const r = await ops.resetTaskLogs();
      toast.success(`작업 로그 ${r.hidden}건을 초기화했습니다`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "초기화 실패");
    } finally {
      setResetting(false);
    }
  };

  const removeLog = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await ops.deleteTaskLog(deleteTarget.id);
      toast.success("로그를 삭제했습니다");
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제 실패");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AdminShell title="작업 로그">
      <div className="flex h-full flex-col gap-4">
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        {/* 미확인 침입 — 가장 위 */}
        {alerts && alerts.intrusions.length > 0 ? (
          <section className="shrink-0 rounded-lg border border-rose-300 bg-rose-500/5 p-4">
            <h3 className="mb-2 text-sm font-semibold text-rose-700">
              미확인 침입 알림 {alerts.intrusions.length}건
            </h3>
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {alerts.intrusions.map((i) => (
                <div
                  key={i.id}
                  className="flex items-center gap-2 rounded border bg-white px-3 py-2 text-sm"
                >
                  <span className="font-medium">{i.source}</span>
                  <span className="text-xs text-muted-foreground">
                    {i.zone ?? "위치 미상"} · {fmtTime(i.at)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs">
                    {i.note}
                  </span>
                  {i.clip_path ? (
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                      {i.clip_path}
                    </span>
                  ) : null}
                  <button
                    onClick={() => ack(i.id)}
                    className="shrink-0 rounded bg-secondary px-2 py-1 text-xs"
                  >
                    확인
                  </button>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {/* 로그 */}
        <section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-semibold">작업 로그</h3>
            {(["", "ASSIGNED", "COMPLETED", "FAILED"] as const).map((f) => (
              <button
                key={f || "all"}
                onClick={() => setFilter(f)}
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  filter === f
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-secondary-foreground"
                }`}
              >
                {f === "" ? "전체" : STATUS_LABEL[f]}
              </button>
            ))}
            <button
              onClick={() => void resetLogs()}
              disabled={resetting || logs.length === 0}
              className="ml-auto rounded-full bg-rose-500/10 px-2.5 py-1 text-xs font-semibold text-rose-700 disabled:pointer-events-none disabled:opacity-40"
            >
              {resetting ? "초기화 중…" : "초기화"}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="pb-2 pr-3">시각</th>
                  <th className="pb-2 pr-3">작업</th>
                  <th className="pb-2 pr-3">종류</th>
                  <th className="pb-2 pr-3">로봇</th>
                  <th className="pb-2 pr-3">결과</th>
                  <th className="pb-2 pr-3">다리</th>
                  <th className="pb-2 pr-3">재시도</th>
                  <th className="pb-2 pr-3">사유</th>
                  <th className="pb-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.id} className="border-t">
                    <td className="py-2 pr-3 text-xs">
                      {fmtTime(l.recorded_at)}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">{l.task_id}</td>
                    <td className="py-2 pr-3 text-xs">{l.kind}</td>
                    <td className="py-2 pr-3 text-xs">{l.robot ?? "—"}</td>
                    <td className="py-2 pr-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-bold ${TONE[l.status] ?? ""}`}
                      >
                        {STATUS_LABEL[l.status] ?? l.status}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-xs tabular-nums">
                      {l.leg_idx}/{l.leg_count}
                    </td>
                    <td className="py-2 pr-3 text-xs tabular-nums">
                      {l.retries}
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {l.reason ?? "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <button
                        onClick={() => setDeleteTarget(l)}
                        className="rounded bg-rose-500/10 px-2 py-1 text-xs font-semibold text-rose-700"
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                ))}
                {logs.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-3 text-muted-foreground">
                      로그가 없습니다
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="작업 로그 삭제"
        description={
          <>
            {deleteTarget?.task_id} 로그를 삭제할까요? 삭제한 로그는
            새로고침해도 다시 나타나지 않습니다.
          </>
        }
        onConfirm={() => void removeLog()}
        busy={deleting}
      />
    </AdminShell>
  );
}
