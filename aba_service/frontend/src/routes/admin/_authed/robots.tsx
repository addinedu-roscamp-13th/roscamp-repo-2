import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { MiniDonut } from "@/components/admin/charts";
import { MAP_IMAGE, WAYPOINTS } from "@/lib/map-waypoints";
import {
  ops,
  type OrderRow,
  type RobotRow,
  type TaskKind,
} from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/robots")({
  head: () => ({ meta: [{ title: "LiBi Admin — 실시간 모니터링" }] }),
  component: RobotsPage,
});

/**
 * 실시간 모니터링 — 로봇 상태·배터리·현재 작업 + 지도 위 위치.
 *
 * 지도 좌표는 `waypoint.yaml` 과 같은 계이므로, 로봇의 (x, y) 를 정규화해 그대로 찍으면
 * 실제 위치와 일치한다. 화면 표시는 `LibraryMap.tsx` 와 같은 90°(+180°) 회전 규칙을
 * 그대로 따른다 — 새로 유도하면 로봇 점이 벽 안쪽에 찍히는 사고가 난다.
 */

/** 월드 좌표 → 지도 이미지 안의 정규화 좌표(세로 원본 기준, 0~1). */
const ORIGIN_X = -0.184;
const ORIGIN_Y = -1.949;
const RES = 0.02;
const W = 63;
const H = 108;

function toNorm(x: number, y: number): { nx: number; ny: number } {
  return { nx: (x - ORIGIN_X) / (W * RES), ny: 1 - (y - ORIGIN_Y) / (H * RES) };
}

/** `LibraryMap.tsx` 와 동일한 회전 — (x, y) → (y, 1 − x). 세로 정규화 좌표를 가로 화면 좌표로. */
function rotate(x: number, y: number): [number, number] {
  return [y, 1 - x];
}

const STATE_TONE: Record<string, string> = {
  PATROL: "bg-emerald-500/15 text-emerald-700",
  IDLE: "bg-slate-500/15 text-slate-600",
  WORKING: "bg-amber-500/15 text-amber-700",
  ERROR: "bg-rose-500/15 text-rose-700",
  CHARGING: "bg-sky-500/15 text-sky-700",
  RETURNING: "bg-violet-500/15 text-violet-700",
  INTERACTING: "bg-fuchsia-500/15 text-fuchsia-700",
  SECURITY_PATROL: "bg-teal-500/15 text-teal-700",
};

const ORDER_STATUS_LABEL: Record<string, string> = {
  PENDING: "대기",
  ASSIGNED: "배차됨",
  EXECUTING: "수행중",
  COMPLETED: "완료",
  FAILED: "실패",
  CANCELLED: "취소",
};

const FLEET_STATE_COLOR = {
  available: "#10b981",
  working: "#f59e0b",
  charging: "#0ea5e9",
  error: "#f43f5e",
  stale: "#94a3b8",
} as const;

// 로봇 한 대가 정확히 한 버킷에만 들어가도록 우선순위로 가른다(끊김 최우선).
// 카드 배지와 **같은 `state`** 만 본다 — `busy`(작업 배정 여부)는 안 본다. 그걸 섞으면
// 같은 PATROL 로봇이 배정 유무로 가용/작업중으로 갈려 상태화면(FMS)과 어긋난다.
// PATROL·SECURITY_PATROL 은 대기 가능 용량이라 가용, 실제 배달(WORKING)만 작업중.
// stale 은 별도 버킷으로 세어 도넛 합계 == 로봇 수.
function fleetBucket(r: RobotRow): keyof typeof FLEET_STATE_COLOR {
  if (r.stale) return "stale";
  if (r.state === "ERROR") return "error";
  if (r.state === "CHARGING" || r.state === "RETURNING") return "charging";
  if (r.state === "WORKING") return "working";
  return "available";
}

function RobotsPage() {
  const [robots, setRobots] = useState<RobotRow[]>([]);
  const [linked, setLinked] = useState(true);
  const [plugins, setPlugins] = useState<Record<string, string>>({});
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [kinds, setKinds] = useState<TaskKind[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      Promise.all([ops.robots(), ops.tasks()])
        .then(([r, t]) => {
          setRobots(r.robots);
          setLinked(r.linked);
          setPlugins(r.plugins);
          setOrders(t.orders);
          setKinds(t.kinds);
          setErr(null);
        })
        .catch((e) => setErr(e instanceof Error ? e.message : "불러오기 실패"));
    void load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, []);

  const fleetCounts = robots.reduce(
    (acc, r) => {
      acc[fleetBucket(r)] += 1;
      return acc;
    },
    { available: 0, working: 0, charging: 0, error: 0, stale: 0 },
  );
  const fleetChart = [
    { label: "가용", value: fleetCounts.available, color: FLEET_STATE_COLOR.available },
    { label: "작업중", value: fleetCounts.working, color: FLEET_STATE_COLOR.working },
    { label: "충전중", value: fleetCounts.charging, color: FLEET_STATE_COLOR.charging },
    { label: "오류", value: fleetCounts.error, color: FLEET_STATE_COLOR.error },
    { label: "끊김", value: fleetCounts.stale, color: FLEET_STATE_COLOR.stale },
  ];

  const orderFor = (robotName: string) =>
    orders.find(
      (o) =>
        o.robot === robotName &&
        !["COMPLETED", "FAILED", "CANCELLED"].includes(o.status),
    );

  return (
    <AdminShell title="실시간 모니터링">
      <div className="flex h-full flex-col gap-4">
        {!linked ? (
          <p className="shrink-0 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
            FMS 연결 없음 — 로봇 정보를 읽지 못합니다.
          </p>
        ) : null}
        {err ? (
          <p className="shrink-0 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="flex min-h-0 flex-1 gap-4">
          {/* 왼쪽: 지도 + 로봇 상태 요약 */}
          <div className="flex min-h-0 w-1/2 shrink-0 flex-col gap-4 overflow-y-auto">
          {/* 지도 위 로봇 위치 — 가로(108:63)로 회전, LibraryMap.tsx 와 동일 규칙 */}
          <section className="shrink-0 rounded-lg border p-4">
            <h3 className="mb-3 text-lg font-semibold">지도 위 로봇 위치</h3>
            <div
              className="relative mx-auto w-full overflow-hidden rounded-lg bg-white ring-1 ring-border"
              style={{ aspectRatio: "108 / 63" }}
            >
              <img
                src={MAP_IMAGE}
                alt="도서관 지도"
                aria-hidden
                className="pointer-events-none absolute left-1/2 top-1/2 h-auto w-[58%] -translate-x-1/2 -translate-y-1/2 -rotate-90 opacity-60 [image-rendering:pixelated]"
              />
              {/* 서가·시설 정점 (옅게) — WAYPOINTS 는 이미 0~1 정규화 좌표라 rotate() 만 적용 */}
              {WAYPOINTS.filter((w) => w.kind !== "corridor").map((w) => {
                const [rx, ry] = rotate(w.x, w.y);
                return (
                  <span
                    key={w.name}
                    title={w.label}
                    style={{ left: `${rx * 100}%`, top: `${ry * 100}%` }}
                    className="absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-400"
                  />
                );
              })}
              {/* 로봇 — 월드좌표 → toNorm(0~1) → rotate() 순서로 같은 화면 좌표계에 맞춘다 */}
              {robots
                .filter((r) => r.x !== null && r.y !== null)
                .map((r) => {
                  const { nx, ny } = toNorm(r.x as number, r.y as number);
                  const [rx, ry] = rotate(nx, ny);
                  return (
                    <span
                      key={r.name}
                      style={{ left: `${rx * 100}%`, top: `${ry * 100}%` }}
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
              <p className="mt-3 shrink-0 text-center font-mono text-[11px] text-muted-foreground">
                배차 {plugins.dispatcher} · 교통 {plugins.traffic}
              </p>
            ) : null}
          </section>

          <div className="min-h-40 flex-1">
            <MiniDonut title="로봇 상태" data={fleetChart} size="lg" />
          </div>
          </div>

          {/* 오른쪽: 로봇 카드 — 좌우 분할, 세로로 넉넉히 스크롤 */}
          <section className="min-h-0 flex-1 overflow-y-auto">
            {robots.length === 0 ? (
              <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                관측된 로봇이 없습니다
              </p>
            ) : (
              <div className="grid gap-2 xl:grid-cols-2">
                {robots.map((r) => {
                  const order = orderFor(r.name);
                  const kindLabel = order
                    ? (kinds.find((k) => k.key === order.task_type)?.label ??
                      order.task_type)
                    : null;
                  return (
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
                        <Field label={order ? "작업 종류" : "현재 작업"}>
                          {order ? kindLabel : r.task_id || "—"}
                        </Field>
                        <Field label="작업 상태">
                          {order ? (
                            <span
                              className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                              style={{
                                background:
                                  order.status === "FAILED"
                                    ? "var(--chart-status-critical)"
                                    : order.status === "COMPLETED"
                                      ? "var(--chart-status-good)"
                                      : "var(--chart-status-warning)",
                                color: "white",
                              }}
                            >
                              {ORDER_STATUS_LABEL[order.status] ?? order.status}
                            </span>
                          ) : (
                            r.task_state || "—"
                          )}
                        </Field>
                        {order ? (
                          <Field label="요청자">{order.requester || "—"}</Field>
                        ) : (
                          <Field label="목표 정점">
                            {r.goal_vertex === null
                              ? "—"
                              : (WAYPOINTS[r.goal_vertex]?.label ??
                                `v${r.goal_vertex}`)}
                          </Field>
                        )}
                        <Field label="진행률">
                          <span className="flex items-center gap-2">
                            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                              <span
                                className="block h-full rounded-full bg-primary"
                                style={{
                                  width: `${
                                    order
                                      ? order.leg_count
                                        ? Math.round(
                                            (order.leg_idx / order.leg_count) *
                                              100,
                                          )
                                        : 0
                                      : Math.round((r.progress ?? 0) * 100)
                                  }%`,
                                }}
                              />
                            </span>
                            {order
                              ? `${order.leg_idx}/${order.leg_count}`
                              : `${Math.round((r.progress ?? 0) * 100)}%`}
                          </span>
                        </Field>
                      </div>
                    </div>
                  );
                })}
              </div>
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
