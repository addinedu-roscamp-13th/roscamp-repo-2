import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { MiniDonut } from "@/components/admin/charts";
import { ops, opsApi } from "@/lib/ops-api";

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
  night_start: string | null;
  night_end: string | null;
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
  const [deleteId, setDeleteId] = useState<number | null>(null);
  // 스케줄 입력칸 — 서버 값으로 딱 한 번만 채운다(폴링마다 덮어쓰면 입력 중에 지워진다).
  const [scheduleSeeded, setScheduleSeeded] = useState(false);
  const [nightStart, setNightStart] = useState("");
  const [nightEnd, setNightEnd] = useState("");
  const [savingSchedule, setSavingSchedule] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await opsApi<SecurityState>("/api/admin/ops/security");
      setState(s);
      setScheduleSeeded((seeded) => {
        if (!seeded) {
          setNightStart(s.night_start ?? "");
          setNightEnd(s.night_end ?? "");
        }
        return true;
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const saveSchedule = async (start = nightStart, end = nightEnd) => {
    setSavingSchedule(true);
    try {
      await ops.setSecuritySchedule(start || null, end || null);
      toast.success(
        start && end
          ? `자동 전환 시각을 저장했습니다 (${start} → ${end})`
          : "자동 전환 스케줄을 껐습니다",
      );
      setNightStart(start);
      setNightEnd(end);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSavingSchedule(false);
    }
  };

  const clearSchedule = () => void saveSchedule("", "");

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

  const removeEvent = async () => {
    if (deleteId == null) return;
    try {
      await ops.deleteIntrusionEvent(deleteId);
      toast.success("기록을 삭제했습니다");
      setDeleteId(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  const night = state?.mode === "night";
  const unacked = state?.events.filter((e) => !e.acknowledged) ?? [];
  // 미확인 침입부터 — 야간 근무자가 지금 확인해야 할 것이 목록 위에 온다.
  const sortedEvents = [...(state?.events ?? [])].sort(
    (a, b) => Number(a.acknowledged) - Number(b.acknowledged),
  );

  return (
    <AdminShell title="야간 보안">
      <div className="flex h-full flex-col gap-4">
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
        <section className="shrink-0 rounded-lg border p-4">
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

          {/* 자동 전환 시각 — 설정하면 그 시각에 위 모드를 자동으로 바꾼다 */}
          <div className="mt-4 border-t pt-3">
            <p className="mb-2 text-xs font-semibold text-muted-foreground">
              야간 자동 전환 시각
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="time"
                value={nightStart}
                onChange={(e) => setNightStart(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
              />
              <span className="text-xs text-muted-foreground">부터</span>
              <input
                type="time"
                value={nightEnd}
                onChange={(e) => setNightEnd(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
              />
              <span className="text-xs text-muted-foreground">까지 야간</span>
              <button
                onClick={() => void saveSchedule()}
                disabled={savingSchedule || !nightStart || !nightEnd}
                className="rounded bg-secondary px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              >
                저장
              </button>
              {state?.night_start && state?.night_end ? (
                <button
                  onClick={clearSchedule}
                  disabled={savingSchedule}
                  className="rounded bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-700 disabled:opacity-50"
                >
                  스케줄 끄기
                </button>
              ) : null}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              {state?.night_start && state?.night_end
                ? `매일 ${state.night_start}에 야간, ${state.night_end}에 주간으로 자동 전환됩니다. 그 사이 위 버튼으로 수동 전환도 가능합니다.`
                : "비워두면 위 버튼으로만 수동 전환합니다."}
            </p>
          </div>
        </section>

        {/* 침입 이벤트 */}
        <section className="flex min-h-0 flex-1 flex-col rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">
            침입 감지 기록{" "}
            {unacked.length > 0 ? (
              <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs font-bold text-rose-700">
                미확인 {unacked.length}
              </span>
            ) : null}
          </h3>

          {state && state.events.length > 0 ? (
            <div className="mb-3 shrink-0">
              <MiniDonut
                title="확인 상태"
                data={[
                  {
                    label: "확인됨",
                    value: state.events.length - unacked.length,
                    color: "#10b981",
                  },
                  { label: "미확인", value: unacked.length, color: "#f43f5e" },
                ]}
              />
            </div>
          ) : null}

          {!state || state.events.length === 0 ? (
            <p className="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">
              감지 기록이 없습니다
            </p>
          ) : (
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
              {sortedEvents.map((e) => (
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
                    <button
                      onClick={() => setDeleteId(e.id)}
                      className={`rounded bg-rose-500/10 px-2 py-1 text-xs font-semibold text-rose-700 ${e.acknowledged ? "ml-auto" : ""}`}
                    >
                      삭제
                    </button>
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

      <ConfirmDeleteDialog
        open={deleteId != null}
        onOpenChange={(o) => !o && setDeleteId(null)}
        title="감지 기록 삭제"
        description="이 감지 기록을 삭제할까요? 이 작업은 되돌릴 수 없습니다."
        onConfirm={() => void removeEvent()}
      />
    </AdminShell>
  );
}
