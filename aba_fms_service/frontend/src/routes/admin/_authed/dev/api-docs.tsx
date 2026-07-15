import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";

export const Route = createFileRoute("/admin/_authed/dev/api-docs")({
  head: () => ({ meta: [{ title: "Labi Admin — API 문서" }] }),
  component: ApiDocsPage,
});

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");
const DOCS_URL = API_BASE ? `${API_BASE}/docs` : "/docs";

function ApiDocsPage() {
  return (
    <AdminShell title="API 문서">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">API 문서</h1>
            <p className="mt-1 text-[13px] text-slate-500">
              FastAPI Swagger UI —{" "}
              <a
                href={DOCS_URL}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-orange-600 underline underline-offset-2 hover:text-orange-700"
              >
                {DOCS_URL}
              </a>
            </p>
          </div>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] font-medium text-slate-600 hover:border-orange-300 hover:text-orange-600"
          >
            새 탭으로 열기 ↗
          </a>
        </div>
        <div
          className="overflow-hidden rounded-xl border border-slate-200 bg-white"
          style={{ height: "calc(100vh - 220px)" }}
        >
          <iframe src={DOCS_URL} title="API 문서" className="h-full w-full" />
        </div>
      </div>
    </AdminShell>
  );
}
