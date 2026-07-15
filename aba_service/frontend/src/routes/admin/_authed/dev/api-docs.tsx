import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";

export const Route = createFileRoute("/admin/_authed/dev/api-docs")({
  head: () => ({ meta: [{ title: "Labi Admin — API 문서" }] }),
  component: ApiDocsPage,
});

function ApiDocsPage() {
  return (
    <AdminShell title="API 문서">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">API 문서</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            FastAPI Swagger UI로 API를 조회하고 테스트합니다. (same-origin{" "}
            <code className="font-mono">/api/docs</code>)
          </p>
        </div>
        <div
          className="overflow-hidden rounded-xl border border-slate-200 bg-white"
          style={{ height: "calc(100vh - 200px)" }}
        >
          <iframe src="/api/docs" title="API 문서" className="h-full w-full" />
        </div>
      </div>
    </AdminShell>
  );
}
