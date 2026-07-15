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
      <span className="w-32 shrink-0 text-[13px] font-medium text-slate-500">{label}</span>
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
            FastAPI 백엔드(uvicorn)와 React 프론트엔드(Vite)의 실행·재시작·재배포 방법입니다.
          </p>
        </div>

        <Section title="백엔드 서버 (uvicorn)">
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-[13px] text-blue-800">
              백엔드 루트: <code className="font-mono font-semibold">/home/pinky/bot_ai_server/backend</code> —
              Python 가상환경(.venv) 안의 uvicorn으로 구동합니다.
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Zap className="h-4 w-4 text-slate-400" /> 서버 시작
                </div>
                <CodeBlock code={`cd /home/pinky/bot_ai_server/backend\n.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001`} />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Zap className="h-4 w-4 text-slate-400" /> reload 모드 (개발)
                </div>
                <CodeBlock code={`.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001 --reload`} />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Search className="h-4 w-4 text-slate-400" /> 헬스체크
                </div>
                <CodeBlock code={`curl http://192.168.0.70:9001/api/health`} />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <Square className="h-4 w-4 text-slate-400" /> 프로세스 종료
                </div>
                <CodeBlock code={`pkill -f "uvicorn main:app"`} />
              </div>
            </div>
          </div>
        </Section>

        <Section title="프론트엔드 (Vite)">
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-[13px] text-indigo-800">
              프론트 루트: <code className="font-mono font-semibold">/home/pinky/bot_ai_server/frontend</code> —
              Vite 개발 서버로 실행하거나, 빌드 후 정적 서버로 서빙합니다.
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2 text-[13px] text-slate-600">
                <Zap className="h-4 w-4 text-slate-400" /> 개발 서버 시작 (:3000)
              </div>
              <CodeBlock code={`cd /home/pinky/bot_ai_server/frontend\nnpm run dev`} />
            </div>
          </div>
        </Section>

        <Section title="코드 변경 후 재시작">
          <div className="flex flex-col gap-5">
            <Step num={1} label="백엔드 변경 시 — uvicorn 재시작">
              <CodeBlock code={`pkill -f "uvicorn main:app"\ncd /home/pinky/bot_ai_server/backend\nnohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001 > /tmp/labi_api.log 2>&1 &`} />
            </Step>
            <Step num={2} label="프론트엔드 변경 시 — Vite가 자동 반영 (HMR)">
              <p className="text-[13px] text-slate-600">
                개발 서버 실행 중에는 파일 저장 즉시 브라우저에 자동 반영됩니다 (Hot Module Replacement).
              </p>
            </Step>
            <Step num={3} label="정상 동작 확인">
              <CodeBlock code={`curl http://192.168.0.70:9001/api/health\n# 응답: {"status":"ok"}`} />
            </Step>
          </div>
        </Section>

        <Section title="systemd 서비스 등록 (선택)">
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
              부팅 시 자동 시작이 필요하다면 systemd 서비스로 등록합니다.
            </div>
            <div className="flex flex-col gap-2">
              <CodeBlock code={`# /etc/systemd/system/labi-admin-api.service\n[Unit]\nDescription=Labi Bot Admin API\nAfter=network.target\n\n[Service]\nUser=pinky\nWorkingDirectory=/home/pinky/bot_ai_server/backend\nExecStart=/home/pinky/bot_ai_server/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 9001\nRestart=always\n\n[Install]\nWantedBy=multi-user.target`} />
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                <RotateCcw className="h-4 w-4 text-slate-400" /> 서비스 등록 및 시작
              </div>
              <CodeBlock code={`sudo systemctl enable --now labi-admin-api\nsudo journalctl -u labi-admin-api -f   # 실시간 로그`} />
            </div>
          </div>
        </Section>

        <Section title="서버 정보">
          <div className="flex flex-col">
            <InfoRow label="프론트 포트" value="3000 (Vite dev server)" />
            <InfoRow label="백엔드 포트" value="9001 (FastAPI / uvicorn)" />
            <InfoRow label="런타임" value="Python 3.12 · .venv" />
            <InfoRow label="DB" value="MariaDB · labi (rc_ 접두사)" />
            <InfoRow label="서버 IP" value="192.168.0.70" />
            <InfoRow label="API 베이스" value="http://192.168.0.70:9001" />
          </div>
        </Section>
      </div>
    </AdminShell>
  );
}
