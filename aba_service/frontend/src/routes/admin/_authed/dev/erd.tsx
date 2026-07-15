import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import mermaid from "mermaid";
import { Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { adminApi, type ErdResponse } from "@/lib/admin-api";

export const Route = createFileRoute("/admin/_authed/dev/erd")({
  head: () => ({ meta: [{ title: "Labi Admin — ERD" }] }),
  component: ErdPage,
});

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "neutral",
  er: { useMaxWidth: true },
});

function mermaidType(type: string): string {
  const t = type.trim().toUpperCase();
  if (t.startsWith("VARCHAR") || t.startsWith("CHAR") || t.includes("TEXT") || t.startsWith("ENUM"))
    return "string";
  if (t.startsWith("BIGINT")) return "bigint";
  if (t.startsWith("TINYINT")) return "tinyint";
  if (t.startsWith("INT")) return "int";
  if (t.startsWith("DECIMAL") || t.startsWith("NUMERIC")) return "decimal";
  if (t.startsWith("DATETIME") || t.startsWith("TIMESTAMP")) return "datetime";
  if (t.startsWith("DATE")) return "date";
  return t.replace(/[^A-Z0-9_]/g, "").toLowerCase() || "string";
}

function mermaidKey(key: string): string {
  if (key === "PK") return "PK";
  if (key === "FK") return "FK";
  if (key === "UQ") return "UK";
  return "";
}

function buildSource(data: ErdResponse | undefined): string {
  if (!data) return "erDiagram\n";
  const lines = ["erDiagram"];
  data.relations.forEach((r) => {
    lines.push(`    ${r.toTable} ||--o{ ${r.fromTable} : "${r.fromColumn}"`);
  });
  data.tables.forEach((t) => {
    lines.push(`    ${t.tableName} {`);
    t.columns.forEach((c) => {
      const parts = [mermaidType(c.type), c.name];
      const k = mermaidKey(c.key);
      if (k) parts.push(k);
      lines.push(`        ${parts.join(" ")}`);
    });
    lines.push("    }");
  });
  return lines.join("\n");
}

function ErdPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "dev", "erd"],
    queryFn: adminApi.getErd,
  });
  const source = useMemo(() => buildSource(data), [data]);
  const [svg, setSvg] = useState("");
  const [zoom, setZoom] = useState(1);
  const [renderError, setRenderError] = useState("");
  const previewRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    void (async () => {
      try {
        const { svg: out } = await mermaid.render(`erd-${Date.now()}`, source);
        if (!cancelled) {
          setSvg(out);
          setRenderError("");
        }
      } catch (e) {
        if (!cancelled) setRenderError("ERD 미리보기를 생성하지 못했습니다.");
        console.error(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, source]);

  useEffect(() => {
    if (!previewRef.current) return;
    previewRef.current.innerHTML = svg;
    const el = previewRef.current.querySelector("svg");
    if (el) {
      el.style.maxWidth = "";
      el.style.transformOrigin = "top left";
      el.style.transform = `scale(${zoom})`;
    }
  }, [svg, zoom]);

  async function copySource() {
    try {
      await navigator.clipboard.writeText(source);
    } catch {
      /* ignore */
    }
  }

  return (
    <AdminShell title="ERD">
      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">ERD</h1>
            <p className="mt-1 text-[13px] text-slate-500">
              현재 <code className="font-mono">labi</code> 스키마로 자동 생성한 관계도 (GitHub Mermaid 호환).
            </p>
          </div>
          <Button type="button" variant="outline" onClick={copySource} disabled={isLoading}>
            Mermaid 소스 복사
          </Button>
        </div>

        {(error || renderError) && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-600">
            {renderError || (error as Error)?.message}
          </div>
        )}

        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setZoom((v) => Math.max(0.5, Number((v - 0.1).toFixed(1))))}
              >
                <Minus className="h-4 w-4" />
              </Button>
              <span className="w-12 text-center text-[13px] text-slate-600">
                {(zoom * 100).toFixed(0)}%
              </span>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setZoom((v) => Math.min(2, Number((v + 0.1).toFixed(1))))}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex flex-wrap gap-2 text-[12px] text-slate-600">
              <span className="rounded bg-slate-100 px-2 py-1">테이블 {data?.tables.length ?? 0}개</span>
              <span className="rounded bg-slate-100 px-2 py-1">관계 {data?.relations.length ?? 0}개</span>
            </div>
          </div>

          {isLoading ? (
            <div className="py-10 text-[13px] text-slate-500">ERD를 불러오는 중…</div>
          ) : (
            <div className="overflow-auto rounded-lg border border-slate-200 bg-[#fcfcfb] p-4">
              <div ref={previewRef} />
            </div>
          )}
        </div>
      </div>
    </AdminShell>
  );
}
