import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { MAP_IMAGE, WAYPOINTS } from "@/lib/map-waypoints";
import { ops, type RobotRow } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/robots")({
  head: () => ({ meta: [{ title: "LiBi Admin — 실시간 모니터링" }] }),
  component: RobotsPage,
});

/**
 * 실시간 모니터링 — 로봇 상태·배터리·현재 작업 + 지도 위 위치.
 *
 * 지도 좌표는 `waypoint.yaml` 과 같은 계이므로, 로봇의 (x, y) 를 정규화해 그대로 찍으면
 * 실제 위치와 일치한다.
 */

/** 월드 좌표 → 지도 이미지 안의 정규화 좌표. `map-waypoints.ts` 생성 규칙과 같아야 한다. */
const ORIGIN_X = -0.184;
const ORIGIN_Y = -1.949;
const RES = 0.02;
const W = 63;
const H = 108;

function toNorm(x: number, y: number): { nx: number; ny: number } {
  return { nx: (x - ORIGIN_X) / (W * RES), ny: 1 - (y - ORIGIN_Y) / (H * RES) };
}

const STATE_TONE: Record<string, string> = {
  PATROL: "bg-emerald-500/15 text-emerald-700",
  IDLE: "bg-slate-500/15 text-slate-600",
  WORKING: "bg-amber-500/15 text-amber-700",
  ERROR: "bg-rose-500/15 text-rose-700",
  CHARGING: "bg-sky-500/15 text-sky-700",
  RETURNING: "bg-violet-500/15 text-violet-700",
};

function RobotsPage() {
  const [robots, setRobots] = useState<RobotRow[]>([]);
  const [linked, setLinked] = useState(true);
  const [plugins, setPlugins] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      ops
        .robots()
        .then((d) => {
          setRobots(d.robots);
          setLinked(d.linked);
          setPlugins(d.plugins);
          setErr(null);
        })
        .catch((e) => setErr(e instanceof Error ? e.message : "불러오기 실패"));
    void load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, []);

  return (
    <AdminShell title="실시간 모니터링">
      <div className="space-y-4">
        {!linked ? (
          <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
            FMS 연결 없음 — 로봇 정보를 읽지 못합니다.
          </p>
        ) : null}
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-2">
          {/* 지도 위 로봇 위치 */}
          <section className="rounded-lg border p-4">
            <h3 className="mb-3 text-sm font-semibold">지도 위 로봇 위치</h3>
            <div
              className="relative mx-auto w-full max-w-xs overflow-hidden rounded-lg bg-white ring-1 ring-border"
              style={{ aspectRatio: `${W} / ${H}` }}
            >
              <img
                src={MAP_IMAGE}
                alt="도서관 지도"
                className="absolute inset-0 size-full object-contain opacity-60 [image-rendering:pixelated]"
              />
              {/* 서가·시설 정점 (옅게) */}
              {WAYPOINTS.filter((w) => w.kind !== "corridor").map((w) => (
                <span
                  key={w.name}
                  title={w.label}
                  style={{ left: `${w.x * 100}%`, top: `${w.y * 100}%` }}
                  className="absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-400"
                />
              ))}
              {/* 로봇 */}
              {robots
                .filter((r) => r.x !== null && r.y !== null)
                .map((r) => {
                  const { nx, ny } = toNorm(r.x as number, r.y as number);
                  return (
                    <span
                      key={r.name}
                      style={{ left: `${nx * 100}%`, top: `${ny * 100}%` }}
                      className="absolute -translate-x-1/2 -translate-y-1/2"
                    >
                      <span className="block size-3 rounded-full border-2 border-white bg-primary shadow ring-2 ring-primary/40" />
                      <span className="absolute left-1/2 top-full mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-primary px-1 py-0.5 text-[9px] font-bold text-primary-foreground">
                        {r.name}
                      </span>
                    </span>
                  );
                })}
            </div>
            {plugins.dispatcher ? (
              <p className="mt-3 text-center font-mono text-[11px] text-muted-foreground">
                배차 {plugins.dispatcher} · 교통 {plugins.traffic}
              </p>
            ) : null}
          </section>

          {/* 로봇 카드 */}
          <section className="space-y-2">
            {robots.length === 0 ? (
              <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                관측된 로봇이 없습니다
              </p>
            ) : (
              robots.map((r) => (
                <div key={r.name} className="rounded-lg border p-4">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{r.name}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-bold ${
                        STATE_TONE[r.state ?? ""] ??
                        "bg-muted text-muted-foreground"
                      }`}
                    >
                      {r.state ?? "상태 미상"}
                    </span>
                    {r.stale ? (
                      <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs font-bold text-rose-700">
                        텔레메트리 끊김
                      </span>
                    ) : null}
                  </div>

                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <Field label="배터리">
                      {r.battery === null ? (
                        "—"
                      ) : (
                        <span className="flex items-center gap-2">
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                            <span
                              className={`block h-full rounded-full ${
                                r.battery < 20
                                  ? "bg-rose-500"
                                  : "bg-emerald-500"
                              }`}
                              style={{
                                width: `${Math.max(0, Math.min(100, r.battery))}%`,
                              }}
                            />
                          </span>
                          {r.battery}%
                        </span>
                      )}
                    </Field>
                    <Field label="위치">
                      {r.x === null
                        ? "—"
                        : `${r.x.toFixed(2)}, ${(r.y as number).toFixed(2)}`}
                    </Field>
                    <Field label="현재 작업">{r.task_id || "—"}</Field>
                    <Field label="작업 상태">{r.task_state || "—"}</Field>
                    <Field label="목표 정점">
                      {r.goal_vertex === null
                        ? "—"
                        : (WAYPOINTS[r.goal_vertex]?.label ??
                          `v${r.goal_vertex}`)}
                    </Field>
                    <Field label="진행률">
                      {Math.round((r.progress ?? 0) * 100)}%
                    </Field>
                  </div>
                </div>
              ))
            )}
          </section>
        </div>
      </div>
    </AdminShell>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded bg-muted/50 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 font-medium text-foreground">{children}</div>
    </div>
  );
}
