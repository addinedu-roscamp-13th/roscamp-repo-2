import { createFileRoute } from "@tanstack/react-router";
import { RotateCcw, Search, Square, Zap } from "lucide-react";
import type { ReactNode } from "react";

import { AdminShell } from "@/components/admin/AdminShell";

export const Route = createFileRoute("/admin/_authed/dev/server-ops")({
  head: () => ({ meta: [{ title: "Labi Admin — 서버 운영 가이드" }] }),
  component: ServerOpsPage,
});

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6">
      <h2 className="mb-4 border-b border-slate-100 pb-3 text-[15px] font-bold text-slate-800">
        {title}
      </h2>
      {children}
    </div>
  );
}

function CodeBlock({ code }: { code: string }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-4 font-mono text-[13px] leading-relaxed text-green-400">
      {code}
    </pre>
  );
}

function Step({ num, label, children }: { num: number; label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-800 text-[11px] font-bold text-white">
          {num}
        </span>
        <span className="text-[13px] font-semibold text-slate-700">{label}</span>
      </div>
      <div className="ml-8">{children}</div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start gap-4 border-b border-slate-50 py-2.5 last:border-0">
      <span className="w-28 shrink-0 text-[13px] font-medium text-slate-500">{label}</span>
      <span className="font-mono text-[13px] text-slate-800">{value}</span>
    </div>
  );
}

function ServerOpsPage() {
  return (
    <AdminShell title="서버 운영 가이드">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">서버 운영 가이드</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            systemd 서비스로 구동되는 FastAPI 백엔드와 nginx의 실행·재시작·재배포 방법입니다.
          </p>
        </div>

        <Section title="백엔드 서비스 (systemd)">
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-[13px] text-blue-800">
              백엔드는 <code className="font-mono font-semibold">labi-admin-api.service</code> 로 등록되어 부팅 시 자동 시작되고, 크래시 시 자동 재시작됩니다.
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Search className="h-4 w-4 text-slate-400" /> 상태 확인
                </div>
                <CodeBlock code="sudo systemctl status labi-admin-api" />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <RotateCcw className="h-4 w-4 text-slate-400" /> 재시작
                </div>
                <CodeBlock code="sudo systemctl restart labi-admin-api" />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Square className="h-4 w-4 text-slate-400" /> 중지
                </div>
                <CodeBlock code="sudo systemctl stop labi-admin-api" />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Search className="h-4 w-4 text-slate-400" /> 실시간 로그
                </div>
                <CodeBlock code="sudo journalctl -u labi-admin-api -f" />
              </div>
            </div>
          </div>
        </Section>

        <Section title="코드 변경 후 재배포">
          <div className="flex flex-col gap-5">
            <Step num={1} label="백엔드 변경 시 — 서비스 재시작">
              <CodeBlock code="sudo systemctl restart labi-admin-api" />
            </Step>
            <Step num={2} label="프론트엔드 변경 시 — 빌드 후 nginx 리로드">
              <CodeBlock code={`cd /home/Aiprj/chatbot/frontend
npm run build            # dist/ 갱신
sudo systemctl reload nginx`} />
              <p className="mt-2 text-[12px] text-slate-500">
                nginx가 <code className="font-mono">dist/</code> 를 :3000에 서빙하므로 빌드만 하면 반영됩니다.
              </p>
            </Step>
            <Step num={3} label="정상 동작 확인">
              <CodeBlock code="curl -s http://localhost:3000/api/health" />
            </Step>
          </div>
        </Section>

        <Section title="nginx (정적 서빙 + 프록시)">
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
              설정 파일: <code className="font-mono font-semibold">/etc/nginx/conf.d/chatbot-frontend.conf</code>
              {" "}— <code className="font-mono">/</code>→dist, <code className="font-mono">/api</code>→:8010, <code className="font-mono">/ollama</code>→:11434
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                <Zap className="h-4 w-4 text-amber-500" /> 설정 검사 후 리로드
              </div>
              <CodeBlock code={`sudo nginx -t
sudo systemctl reload nginx`} />
            </div>
          </div>
        </Section>

        <Section title="서버 정보">
          <div className="flex flex-col">
            <InfoRow label="프론트 포트" value="3000 (nginx)" />
            <InfoRow label="백엔드 포트" value="8010 (FastAPI / uvicorn)" />
            <InfoRow label="systemd 유닛" value="labi-admin-api.service" />
            <InfoRow label="런타임" value="Python 3.12 · .venv" />
            <InfoRow label="DB" value="MariaDB labi @ localhost:3306" />
            <InfoRow label="LLM" value="Ollama @ localhost:11434" />
          </div>
        </Section>
      </div>
    </AdminShell>
  );
}
