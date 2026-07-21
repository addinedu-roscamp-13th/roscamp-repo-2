import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { opsApi } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/security")({
  head: () => ({ meta: [{ title: "LiBi Admin — 야간 보안" }] }),
  component: SecurityPage,
});

/**
 * 야간 보안 — 운영모드 전환 · 침입 알림 · 침입 영상.
 *
 * 감지는 로봇/AI 서비스가 하고(`POST /security/events`, 인증 없음 — 기계가 부른다),
 * 여기서는 그 보고를 보고 확인 처리한다.
 */

interface SecurityState {
  mode: string;
  events: {
    id: number;
    detected_at: string;
    source: string;
    zone: string | null;
    note: string | null;
    clip_path: string | null;
    acknowledged: boolean;
  }[];
}

const fmtTime = (iso: string) => iso.slice(0, 16).replace("T", " ");

function SecurityPage() {
  const [state, setState] = useState<SecurityState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await opsApi<SecurityState>("/api/admin/ops/security"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const setMode = (mode: string) =>
    void opsApi<{ mode: string }>("/api/admin/ops/security/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    })
      .then(() => {
        setMsg(
          mode === "night"
            ? "야간 운영모드로 전환했습니다"
            : "주간 운영모드로 전환했습니다",
        );
        return load();
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "전환 실패"));

  const ack = (id: number) =>
    void opsApi(`/api/admin/ops/security/events/${id}/ack`, { method: "POST" })
      .then(load)
      .catch((e) => setErr(e instanceof Error ? e.message : "처리 실패"));

  const night = state?.mode === "night";
  const unacked = state?.events.filter((e) => !e.acknowledged) ?? [];

  return (
    <AdminShell title="야간 보안">
      <div className="space-y-4">
        {msg ? (
          <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
            {msg}
          </p>
        ) : null}
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        {/* 운영모드 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-1 text-sm font-semibold">운영 모드</h3>
          <p className="mb-3 text-xs text-muted-foreground">
            야간 모드에서는 로봇이 순찰하며 이동체를 감지해 보고합니다. 감지
            자체는 로봇·AI 서비스가 수행하고, 이 화면은 그 보고를 받습니다.
          </p>
          <div className="flex items-center gap-3">
            <div className="inline-flex rounded-md border p-0.5">
              {(
                [
                  { key: "day", label: "☀️ 주간" },
                  { key: "night", label: "🌙 야간" },
                ] as const
              ).map((m) => (
                <button
                  key={m.key}
                  onClick={() => setMode(m.key)}
                  className={`rounded px-4 py-2 text-sm font-semibold transition ${
                    state?.mode === m.key
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <span
              className={`rounded px-2 py-1 text-xs font-bold ${
                night
                  ? "bg-indigo-500/15 text-indigo-700"
                  : "bg-amber-500/15 text-amber-700"
              }`}
            >
              현재: {night ? "야간 보안 가동" : "주간 정상 운영"}
            </span>
          </div>
        </section>

        {/* 침입 이벤트 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">
            침입 감지 기록{" "}
            {unacked.length > 0 ? (
              <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs font-bold text-rose-700">
                미확인 {unacked.length}
              </span>
            ) : null}
          </h3>

          {!state || state.events.length === 0 ? (
            <p className="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">
              감지 기록이 없습니다
            </p>
          ) : (
            <div className="space-y-2">
              {state.events.map((e) => (
                <div
                  key={e.id}
                  className={`rounded-lg border p-3 ${
                    e.acknowledged
                      ? "opacity-60"
                      : "border-rose-300 bg-rose-500/5"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">{e.source}</span>
                    <span className="text-xs text-muted-foreground">
                      {e.zone ?? "위치 미상"} · {fmtTime(e.detected_at)}
                    </span>
                    {e.acknowledged ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">
                        확인됨
                      </span>
                    ) : (
                      <button
                        onClick={() => ack(e.id)}
                        className="ml-auto rounded bg-secondary px-2 py-1 text-xs font-semibold"
                      >
                        확인 처리
                      </button>
                    )}
                  </div>
                  {e.note ? <p className="mt-1 text-xs">{e.note}</p> : null}

                  {/* 침입 영상 — 파일 경로만 보관한다(바이트는 저장 안 함) */}
                  <div className="mt-2">
                    {e.clip_path ? (
                      <video
                        controls
                        src={e.clip_path}
                        className="max-h-56 w-full rounded bg-black"
                      />
                    ) : (
                      <p className="rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground">
                        저장된 영상이 없습니다 (감지 측이 clip_path 를 보고하면
                        여기서 재생됩니다)
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </AdminShell>
  );
}
