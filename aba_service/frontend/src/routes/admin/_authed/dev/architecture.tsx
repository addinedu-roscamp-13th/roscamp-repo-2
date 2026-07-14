import { createFileRoute } from "@tanstack/react-router";
import {
  ArrowDown,
  Boxes,
  Database,
  Globe,
  Server,
  ServerCog,
  Sparkles,
} from "lucide-react";
import type { ComponentType } from "react";

import { AdminShell } from "@/components/admin/AdminShell";

export const Route = createFileRoute("/admin/_authed/dev/architecture")({
  head: () => ({ meta: [{ title: "Labi Admin — 아키텍처" }] }),
  component: ArchitecturePage,
});

function Layer({
  icon: Icon,
  title,
  subtitle,
  items,
  tone = "slate",
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  items: string[];
  tone?: "slate" | "orange" | "green" | "indigo";
}) {
  const tones: Record<string, string> = {
    slate: "border-slate-200 bg-white",
    orange: "border-orange-200 bg-orange-50",
    green: "border-emerald-200 bg-emerald-50",
    indigo: "border-indigo-200 bg-indigo-50",
  };
  const iconTones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-600",
    orange: "bg-orange-100 text-orange-600",
    green: "bg-emerald-100 text-emerald-600",
    indigo: "bg-indigo-100 text-indigo-600",
  };
  return (
    <div className={`w-full rounded-xl border p-4 md:p-5 ${tones[tone]}`}>
      <div className="flex items-start gap-3">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${iconTones[tone]}`}>
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <h3 className="text-[15px] font-bold text-slate-900">{title}</h3>
            <span className="text-[12px] text-slate-500">{subtitle}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {items.map((it) => (
              <span
                key={it}
                className="rounded-md border border-slate-200/70 bg-white/70 px-2 py-1 font-mono text-[11.5px] text-slate-600"
              >
                {it}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Arrow({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center py-1 text-slate-400">
      <ArrowDown className="h-4 w-4" />
      <span className="text-[11px]">{label}</span>
    </div>
  );
}

function ArchitecturePage() {
  return (
    <AdminShell title="아키텍처">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">아키텍처</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            RobotChatAI 서비스 구성 — 요청 흐름과 각 계층의 기술 스택입니다.
          </p>
        </div>

        <div className="mx-auto flex w-full max-w-2xl flex-col items-stretch">
          <Layer
            icon={Globe}
            title="클라이언트 (브라우저)"
            subtitle="사용자 / 관리자"
            items={["챗봇 UI", "관리자 /admin", "Swagger /api/docs"]}
          />
          <Arrow label="HTTP :3000" />
          <Layer
            icon={Server}
            title="nginx — 리버스 프록시 / 정적 서빙"
            subtitle="listen :3000"
            tone="orange"
            items={[
              "/ → dist (SPA, try_files)",
              "/api/* → :8010",
              "/ollama/* → :11434",
            ]}
          />
          <Arrow label="proxy" />
          <div className="grid gap-3 md:grid-cols-3">
            <Layer
              icon={Boxes}
              title="프론트엔드"
              subtitle="빌드 산출물"
              tone="indigo"
              items={["React 19", "TanStack Router", "shadcn/ui", "Vite"]}
            />
            <Layer
              icon={ServerCog}
              title="백엔드 API"
              subtitle=":8010 (systemd)"
              items={["FastAPI", "JWT (PyJWT)", "SQLAlchemy", "bcrypt"]}
            />
            <Layer
              icon={Sparkles}
              title="LLM"
              subtitle=":11434"
              tone="green"
              items={["Ollama", "qwen3:1.7b"]}
            />
          </div>
          <Arrow label="mysql+pymysql" />
          <Layer
            icon={Database}
            title="MariaDB — labi"
            subtitle=":3306"
            tone="green"
            items={["cb_admin_users", "cb_conversations", "cb_messages", "cb_ai_model_settings"]}
          />
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="mb-3 border-b border-slate-100 pb-3 text-[15px] font-bold text-slate-800">
            핵심 설계 포인트
          </h2>
          <ul className="flex flex-col gap-2 text-[13px] text-slate-600">
            <li>• <b>같은 출처 API</b>: Ollama를 제외한 모든 API는 <code className="font-mono">/api</code> 프리픽스로 호출하고 nginx가 백엔드(:8010)로 프록시한다. 브라우저에서 CORS 없이 동작.</li>
            <li>• <b>인증</b>: 로그인 시 JWT 발급 → <code className="font-mono">localStorage</code> 저장 → <code className="font-mono">Authorization: Bearer</code> 헤더. 401이면 로그인으로 이동.</li>
            <li>• <b>상시 구동</b>: 백엔드는 <code className="font-mono">labi-admin-api.service</code>(systemd)로 부팅 시 자동 시작·크래시 자동 재시작.</li>
            <li>• <b>비밀 관리</b>: DB 비밀번호·JWT 시크릿은 <code className="font-mono">backend/.env</code>에만 둔다. 비밀번호는 bcrypt 해시로만 저장.</li>
          </ul>
        </div>
      </div>
    </AdminShell>
  );
}
