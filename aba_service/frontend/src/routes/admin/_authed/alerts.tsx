import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { ops, opsApi } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/alerts")({
  head: () => ({ meta: [{ title: "LiBi Admin — 작업 알림 · 로그" }] }),
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
  COMPLETED: "bg-emerald-500/15 text-emerald-700",
  FAILED: "bg-rose-500/15 text-rose-700",
  CANCELLED: "bg-muted text-muted-foreground",
};

const fmtTime = (iso: string) => iso.slice(5, 16).replace("T", " ");

function AlertsPage() {
  const [alerts, setAlerts] = useState<Alerts | null>(null);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [filter, setFilter] = useState<"" | "COMPLETED" | "FAILED">("");
  const [err, setErr] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LogRow | null>(null);
  const [deleting, setDeleting] = useState(false);

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
    <AdminShell title="작업 알림 · 로그">
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
            <div className="space-y-1">
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

        {/* 최근 작업 알림 */}
        <section className="shrink-0 rounded-lg border p-4">
          <h3 className="mb-2 text-sm font-semibold">
            최근 작업 알림{" "}
            <span className="text-xs font-normal text-muted-foreground">
              (최근 12시간)
            </span>
          </h3>
          {!alerts || alerts.tasks.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              최근 완료·실패한 작업이 없습니다
            </p>
          ) : (
            <div className="space-y-1">
              {alerts.tasks.map((t) => (
                <div
                  key={t.task_id}
                  className="flex flex-wrap items-center gap-2 rounded border px-3 py-2 text-sm"
                >
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-bold ${TONE[t.status] ?? ""}`}
                  >
                    {t.status === "COMPLETED"
                      ? "완료"
                      : t.status === "FAILED"
                        ? "실패"
                        : "취소"}
                  </span>
                  <span className="font-mono text-xs">{t.task_id}</span>
                  <span className="text-xs">{t.kind}</span>
                  <span className="text-xs text-muted-foreground">
                    {t.robot ?? "—"} · {fmtTime(t.at)}
                  </span>
                  {t.reason ? (
                    <span className="min-w-0 flex-1 truncate text-xs text-rose-700">
                      {t.reason}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* 로그 */}
        <section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-semibold">작업 결과 로그</h3>
            {(["", "COMPLETED", "FAILED"] as const).map((f) => (
              <button
                key={f || "all"}
                onClick={() => setFilter(f)}
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  filter === f
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-secondary-foreground"
                }`}
              >
                {f === "" ? "전체" : f === "COMPLETED" ? "완료" : "실패"}
              </button>
            ))}
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
                        {l.status}
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
            {deleteTarget?.task_id} 로그를 삭제할까요? 삭제한 로그는 새로고침해도
            다시 나타나지 않습니다.
          </>
        }
        onConfirm={() => void removeLog()}
        busy={deleting}
      />
    </AdminShell>
  );
}
