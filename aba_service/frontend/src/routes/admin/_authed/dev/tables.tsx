import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { adminApi } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/dev/tables")({
  head: () => ({ meta: [{ title: "Labi Admin — 테이블 정의서" }] }),
  component: TablesPage,
});

const KEY_BADGE: Record<string, string> = {
  PK: "bg-amber-100 text-amber-700",
  FK: "bg-blue-100 text-blue-700",
  UQ: "bg-purple-100 text-purple-700",
};

function TablesPage() {
  const { data: tables = [], isLoading, error } = useQuery({
    queryKey: ["admin", "dev", "tables"],
    queryFn: adminApi.getTables,
  });
  const [selected, setSelected] = useState<string>("");

  const current =
    tables.find((t) => t.tableName === selected) ?? tables[0] ?? null;

  return (
    <AdminShell title="테이블 정의서">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">테이블 정의서</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            MariaDB <code className="font-mono">labi</code> 현재 스키마를 기준으로 자동 조회한 테이블 구조 및 컬럼 명세입니다.
          </p>
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-600">
            테이블 정의서를 불러오지 못했습니다: {(error as Error).message}
          </div>
        )}

        {/* mobile: horizontal table tabs */}
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white md:hidden">
          <div className="overflow-x-auto">
            <div className="flex min-w-max gap-1 p-2">
              {tables.map((t) => (
                <button
                  key={t.tableName}
                  onClick={() => setSelected(t.tableName)}
                  className={cn(
                    "whitespace-nowrap rounded-lg px-3 py-1.5 font-mono text-[12px] transition-colors",
                    current?.tableName === t.tableName
                      ? "bg-slate-800 font-semibold text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                  )}
                >
                  {t.tableName}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex min-h-0 gap-4">
          {/* desktop sidebar */}
          <aside className="hidden w-52 shrink-0 md:block">
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3">
                <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
                  테이블 목록{tables.length ? ` (${tables.length})` : ""}
                </p>
              </div>
              <nav className="flex flex-col py-1">
                {isLoading && (
                  <div className="px-4 py-3 text-[13px] text-slate-500">불러오는 중…</div>
                )}
                {tables.map((t) => (
                  <button
                    key={t.tableName}
                    onClick={() => setSelected(t.tableName)}
                    className={cn(
                      "px-4 py-2.5 text-left font-mono text-[13px] transition-colors",
                      current?.tableName === t.tableName
                        ? "bg-slate-100 font-semibold text-slate-900"
                        : "text-slate-600 hover:bg-slate-50 hover:text-slate-800",
                    )}
                  >
                    {t.tableName}
                  </button>
                ))}
              </nav>
            </div>
          </aside>

          {/* detail */}
          <div className="min-w-0 flex-1">
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              {!current ? (
                <div className="px-6 py-10 text-[13px] text-slate-500">
                  {isLoading ? "불러오는 중…" : "테이블이 없습니다."}
                </div>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3 md:px-6 md:py-4">
                    <span className="font-mono text-[14px] font-bold text-slate-900 md:text-[16px]">
                      {current.tableName}
                    </span>
                    <span className="text-[12px] text-slate-500 md:text-[13px]">
                      — {current.description || "테이블 설명 없음"}
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-max text-[12px] md:text-[13px]">
                      <thead>
                        <tr className="border-b border-slate-100 bg-slate-50 text-left text-slate-600">
                          <th className="px-3 py-2.5 font-semibold md:px-5 md:py-3">컬럼명</th>
                          <th className="px-3 py-2.5 font-semibold md:px-5 md:py-3">타입</th>
                          <th className="px-3 py-2.5 text-center font-semibold md:px-5 md:py-3">키</th>
                          <th className="px-3 py-2.5 text-center font-semibold md:px-5 md:py-3">NULL</th>
                          <th className="px-3 py-2.5 font-semibold md:px-5 md:py-3">기본값</th>
                          <th className="px-3 py-2.5 font-semibold md:px-5 md:py-3">설명</th>
                        </tr>
                      </thead>
                      <tbody>
                        {current.columns.map((col, i) => (
                          <tr
                            key={col.name}
                            className={cn(
                              "border-b border-slate-50",
                              i % 2 === 0 ? "bg-white" : "bg-slate-50/40",
                            )}
                          >
                            <td className="whitespace-nowrap px-3 py-2.5 font-mono font-medium text-slate-800 md:px-5 md:py-3">
                              {col.name}
                            </td>
                            <td className="whitespace-nowrap px-3 py-2.5 font-mono text-indigo-600 md:px-5 md:py-3">
                              {col.type}
                            </td>
                            <td className="px-3 py-2.5 text-center md:px-5 md:py-3">
                              {col.key && (
                                <span
                                  className={cn(
                                    "inline-block rounded px-2 py-0.5 text-[11px] font-bold",
                                    KEY_BADGE[col.key],
                                  )}
                                >
                                  {col.key}
                                </span>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-center md:px-5 md:py-3">
                              {col.nullable ? (
                                <span className="text-slate-400">YES</span>
                              ) : (
                                <span className="font-medium text-red-500">NO</span>
                              )}
                            </td>
                            <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-slate-500 md:px-5 md:py-3 md:text-[12px]">
                              {col.defaultValue ?? "—"}
                            </td>
                            <td className="px-3 py-2.5 text-slate-600 md:px-5 md:py-3">
                              {col.description || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
