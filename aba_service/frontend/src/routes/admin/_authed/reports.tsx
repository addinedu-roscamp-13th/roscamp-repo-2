import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { opsApi } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/reports")({
  head: () => ({ meta: [{ title: "LiBi Admin — 정리 · 분류 리포트" }] }),
  component: ReportsPage,
});

/**
 * 정리·분류 리포트 — 작업 종류별로 **잘 수행했는지**를 본다.
 *
 * 성공률은 취소를 빼고 계산한다: 사서가 취소한 작업까지 실패로 치면 로봇이 억울해진다.
 */

interface Report {
  days: number;
  by_kind: {
    kind: string;
    completed: number;
    failed: number;
    cancelled: number;
    total: number;
    success_rate: number | null;
  }[];
  recent_failures: {
    task_id: string;
    kind: string;
    robot: string | null;
    reason: string | null;
    at: string;
  }[];
}

const KIND_LABEL: Record<string, string> = {
  transfer: "이송",
  shelve: "진열",
  sort: "분류",
  tidy: "정리",
  porter: "짐꾼",
  dispatch: "파견",
  return: "복귀",
  patrol: "순찰",
  delivery: "배달",
};

const fmtTime = (iso: string) => iso.slice(5, 16).replace("T", " ");

function ReportsPage() {
  const [days, setDays] = useState(7);
  const [r, setR] = useState<Report | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setR(await opsApi<Report>(`/api/admin/ops/reports?days=${days}`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AdminShell title="정리 · 분류 리포트">
      <div className="space-y-4">
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">기간</span>
          {[1, 7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold ${
                days === d
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-secondary-foreground"
              }`}
            >
              최근 {d}일
            </button>
          ))}
        </div>

        {/* 종류별 수행 결과 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-1 text-sm font-semibold">작업 종류별 수행 결과</h3>
          <p className="mb-3 text-xs text-muted-foreground">
            성공률은 취소를 제외하고 계산합니다 (완료 ÷ (완료+실패)).
          </p>

          {!r || r.by_kind.length === 0 ? (
            <p className="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">
              기간 내 완료·실패한 작업이 없습니다
            </p>
          ) : (
            <div className="space-y-2">
              {r.by_kind.map((k) => (
                <div key={k.kind} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">
                      {KIND_LABEL[k.kind] ?? k.kind}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      총 {k.total}건
                    </span>
                    {k.success_rate !== null ? (
                      <span
                        className={`ml-auto rounded px-2 py-0.5 text-xs font-bold ${
                          k.success_rate >= 90
                            ? "bg-emerald-500/15 text-emerald-700"
                            : k.success_rate >= 60
                              ? "bg-amber-500/15 text-amber-700"
                              : "bg-rose-500/15 text-rose-700"
                        }`}
                      >
                        성공률 {k.success_rate}%
                      </span>
                    ) : (
                      <span className="ml-auto text-xs text-muted-foreground">
                        판정 불가 (완료·실패 없음)
                      </span>
                    )}
                  </div>

                  {/* 완료/실패/취소 비율 막대 */}
                  <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-muted">
                    <span
                      className="bg-emerald-500"
                      style={{
                        width: `${(k.completed / Math.max(k.total, 1)) * 100}%`,
                      }}
                    />
                    <span
                      className="bg-rose-500"
                      style={{
                        width: `${(k.failed / Math.max(k.total, 1)) * 100}%`,
                      }}
                    />
                    <span
                      className="bg-slate-400"
                      style={{
                        width: `${(k.cancelled / Math.max(k.total, 1)) * 100}%`,
                      }}
                    />
                  </div>
                  <div className="mt-1 flex gap-3 text-[11px] text-muted-foreground">
                    <span>완료 {k.completed}</span>
                    <span>실패 {k.failed}</span>
                    <span>취소 {k.cancelled}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* 실패 상세 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">최근 실패 상세</h3>
          {!r || r.recent_failures.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              실패한 작업이 없습니다
            </p>
          ) : (
            <div className="space-y-1">
              {r.recent_failures.map((f) => (
                <div
                  key={f.task_id}
                  className="flex flex-wrap items-center gap-2 rounded border px-3 py-2 text-sm"
                >
                  <span className="font-mono text-xs">{f.task_id}</span>
                  <span className="text-xs">
                    {KIND_LABEL[f.kind] ?? f.kind}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {f.robot ?? "—"} · {fmtTime(f.at)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-rose-700">
                    {f.reason ?? "사유 없음"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </AdminShell>
  );
}
