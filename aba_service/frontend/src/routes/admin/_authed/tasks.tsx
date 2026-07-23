import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { NAMED_WAYPOINTS } from "@/lib/map-waypoints";
import {
  ops,
  type AdminBook,
  type OrderRow,
  type RobotRow,
  type TaskKind,
} from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/tasks")({
  head: () => ({ meta: [{ title: "LiBi Admin — 작업 지시" }] }),
  component: TasksPage,
});

/**
 * 작업 지시 + 큐 · 이력 · 진행률.
 *
 * 로봇을 **지정**하면 그 로봇에 배차하고, **비워두면** 큐에 대기시켜 자동 배차 대상이 된다
 * (fleet_node 의 dispatcher 플러그인이 고른다).
 *
 * ## 종류마다 폼이 다르다
 * 예전에는 8종이 전부 같은 폼(대상·출발지·목적지)이었고 `kind` 는 라벨일 뿐이었다.
 * 이제 **백엔드가 종류별 필드 목록(`fields`)을 내려주고 화면은 그대로 그린다** — 화면에
 * 종류 표를 다시 적으면 백엔드와 어긋난다.
 *
 * `mode:"state"`(복귀·순찰)는 작업이 아니라 **로봇 모드 변경**이다. 주문이 생기지 않으며
 * 대상 로봇을 반드시 골라야 한다.
 */

const STATUS_TONE: Record<string, string> = {
  PENDING: "bg-slate-500/15 text-slate-600",
  ASSIGNED: "bg-blue-500/15 text-blue-700",
  EXECUTING: "bg-amber-500/15 text-amber-700",
  COMPLETED: "bg-emerald-500/15 text-emerald-700",
  FAILED: "bg-rose-500/15 text-rose-700",
  CANCELLED: "bg-muted text-muted-foreground",
};

/** 진행 중인 작업이 먼저 보이도록 — 현장에서 지금 뭘 봐야 하는지가 목록 위에 온다. */
const STATUS_PRIORITY: Record<string, number> = {
  EXECUTING: 0,
  ASSIGNED: 1,
  PENDING: 2,
  FAILED: 3,
  COMPLETED: 4,
  CANCELLED: 5,
};

/** 목적지 칸의 이름 — 종류마다 뜻이 다르다. 없으면 그냥 「목적지」. */
const DROPOFF_LABEL: Record<string, string> = {
  transfer: "목적지 — 전달할 곳",
  sort: "목적지 — 분류대",
  tidy: "점검할 서가",
  dispatch: "대기 위치",
  porter: "목적지 — 내릴 곳",
};

const SHELVES = NAMED_WAYPOINTS.filter((w) => w.kind === "shelf");
const DESTS = NAMED_WAYPOINTS.filter(
  (w) => w.kind === "table" || w.kind === "facility",
);

function TasksPage() {
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [kinds, setKinds] = useState<TaskKind[]>([]);
  const [robots, setRobots] = useState<RobotRow[]>([]);
  const [linked, setLinked] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 지시 폼
  const [kind, setKind] = useState("transfer");
  const [book, setBook] = useState("");
  const [cargo, setCargo] = useState("");
  const [pickup, setPickup] = useState(SHELVES[0]?.name ?? "");
  const [dropoff, setDropoff] = useState(DESTS[0]?.name ?? "");
  const [robot, setRobot] = useState("");

  // 고른 종류의 정의 — 어떤 칸을 보여줄지, 주문인지 모드 변경인지가 여기서 나온다.
  const spec = kinds.find((k) => k.key === kind);
  const shows = (field: string) => spec?.fields.includes(field) ?? false;
  const isModeChange = spec?.mode === "state";

  // 도서 목록 — 책을 고르면 출발지를 그 책의 서가(zone)로 자동 설정한다.
  // 사서가 "이 책 어느 서가더라"를 외우지 않아도 되고, 오타로 엉뚱한 정점에 보내는 일도 없다.
  const [books, setBooks] = useState<AdminBook[]>([]);
  const [pickupAuto, setPickupAuto] = useState<string | null>(null);

  // 상태 우선 정렬 — 지금 뛰고 있는 작업이 큐 맨 위로 온다.
  const sortedOrders = [...orders].sort(
    (a, b) =>
      (STATUS_PRIORITY[a.status] ?? 9) - (STATUS_PRIORITY[b.status] ?? 9),
  );

  const load = useCallback(async () => {
    try {
      const [t, r] = await Promise.all([ops.tasks(), ops.robots()]);
      setOrders(t.orders);
      setKinds(t.kinds);
      setLinked(t.linked);
      setRobots(r.robots);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, []);

  useEffect(() => {
    void ops
      .books()
      .then(setBooks)
      .catch(() => setBooks([]));
  }, []);

  /** 입력한 제목이 실제 도서와 맞으면 출발지를 그 책의 서가로 옮긴다. */
  const onBookChange = (value: string) => {
    setBook(value);
    const hit = books.find((b) => b.title_kr === value);
    if (hit) {
      setPickup(hit.zone);
      setPickupAuto(`«${hit.title_kr}» 은 ${hit.zone} 에 있습니다`);
    } else {
      setPickupAuto(null);
    }
  };

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 2000);
    return () => clearInterval(t);
  }, [load]);

  /** 종류에 필요한 칸이 다 찼는가 — 안 쓰는 칸은 보지 않는다. */
  const ready = (() => {
    if (!spec || !linked) return false;
    if (spec.mode === "state") return !!robot; // 복귀·순찰은 대상 로봇이 필수
    if (shows("book") && !book.trim()) return false;
    if (shows("cargo") && !cargo.trim()) return false;
    if (shows("pickup") && !pickup) return false;
    if (shows("dropoff") && !dropoff) return false;
    return true;
  })();

  const submit = async () => {
    setErr(null);
    setMsg(null);
    if (!spec) return;
    try {
      // 안 쓰는 칸은 아예 보내지 않는다 — 「정리」에 책 이름이 실리면 로봇이 집으러 간다.
      const res = await ops.createTask({
        kind,
        ...(shows("book") ? { book } : {}),
        ...(shows("cargo") ? { cargo } : {}),
        ...(shows("pickup") ? { pickup } : {}),
        ...(shows("dropoff") ? { dropoff } : {}),
        robot,
      });

      if (res.mode === "state") {
        setMsg(
          res.accepted
            ? `${res.robot} → ${res.state} 모드로 전환했습니다`
            : `${res.robot} 이(가) 거절했습니다 (현재 ${res.current_state}) — ${res.reason}`,
        );
      } else {
        const where = res.dropoff ? ` → ${res.dropoff}` : "";
        setMsg(
          res.warn
            ? `${res.task_id} 접수${where} — ${res.warn}`
            : res.assigned
              ? `${res.task_id} 접수${where} + ${res.assigned} 배차`
              : `${res.task_id} 접수${where} (자동 배차 대기)`,
        );
        setBook("");
        setCargo("");
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "지시 실패");
    }
  };

  return (
    <AdminShell title="작업 지시">
      <div className="space-y-4">
        {!linked ? (
          <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
            FMS 연결 없음 — 작업을 지시할 수 없습니다.
          </p>
        ) : null}
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

        {/* 지시 폼 */}
        <section className="rounded-lg border p-4">
          <h3 className="text-sm font-semibold">새 작업 지시</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            로봇을 비워두면 자동 배차 대기(큐)로 들어갑니다.
          </p>

          <div className="mt-3 flex flex-wrap gap-1.5">
            {kinds.map((k) => (
              <button
                key={k.key}
                onClick={() => setKind(k.key)}
                title={k.desc}
                className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                  kind === k.key
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-secondary-foreground"
                }`}
              >
                {k.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{spec?.desc}</p>
          {spec?.hint ? (
            <p className="mt-1 text-xs text-primary">💡 {spec.hint}</p>
          ) : null}

          {/* 종류에 필요한 칸만 그린다 — 필드 목록은 백엔드가 준다 */}
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {shows("book") ? (
              <Labeled label="대상 도서">
                <input
                  value={book}
                  onChange={(e) => onBookChange(e.target.value)}
                  list="ops-book-list"
                  placeholder="도서 제목 (고르면 서가 자동)"
                  className="h-9 w-full rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
                />
                {/* 도서 목록을 datalist 로 — 고르면 위 onBookChange 가 출발지를 채운다 */}
                <datalist id="ops-book-list">
                  {books.map((b) => (
                    <option key={b.id} value={b.title_kr}>
                      {b.author} · {b.zone}
                      {b.in_stock ? "" : " (대출 중)"}
                    </option>
                  ))}
                </datalist>
              </Labeled>
            ) : null}

            {shows("cargo") ? (
              <Labeled label="짐 이름">
                <input
                  value={cargo}
                  onChange={(e) => setCargo(e.target.value)}
                  placeholder="예: 행사용 의자 (도서 목록에 없는 물건)"
                  className="h-9 w-full rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
                />
              </Labeled>
            ) : null}

            {shows("pickup") ? (
              <Labeled label="출발지 — 집을 곳">
                <select
                  value={pickup}
                  onChange={(e) => {
                    setPickup(e.target.value);
                    setPickupAuto(null); // 손으로 바꿨으면 자동 안내는 지운다
                  }}
                  className="h-9 w-full rounded-md border px-2 text-sm"
                >
                  {NAMED_WAYPOINTS.map((w) => (
                    <option key={w.name} value={w.name}>
                      {w.label}
                    </option>
                  ))}
                </select>
                {pickupAuto ? (
                  <span className="block text-[11px] text-emerald-700">
                    ✔ {pickupAuto}
                  </span>
                ) : null}
              </Labeled>
            ) : null}

            {shows("dropoff") ? (
              <Labeled label={DROPOFF_LABEL[kind] ?? "목적지"}>
                <select
                  value={dropoff}
                  onChange={(e) => setDropoff(e.target.value)}
                  className="h-9 w-full rounded-md border px-2 text-sm"
                >
                  {NAMED_WAYPOINTS.map((w) => (
                    <option key={w.name} value={w.name}>
                      {w.label}
                    </option>
                  ))}
                </select>
              </Labeled>
            ) : null}

            {spec?.auto_dropoff === "book_zone" ? (
              <Labeled label="목적지 (자동)">
                <p className="flex h-9 items-center rounded-md border bg-muted px-3 text-sm text-muted-foreground">
                  {books.find((b) => b.title_kr === book)?.zone ??
                    "도서를 고르면 그 책의 서가"}
                </p>
              </Labeled>
            ) : null}

            <Labeled
              label={isModeChange ? "로봇 (필수)" : "로봇 (비우면 자동)"}
            >
              <select
                value={robot}
                onChange={(e) => setRobot(e.target.value)}
                className="h-9 w-full rounded-md border px-2 text-sm"
              >
                <option value="">
                  {isModeChange ? "— 로봇을 고르세요 —" : "— 자동 배차 —"}
                </option>
                {robots.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.name} ({r.state ?? "상태미상"})
                  </option>
                ))}
              </select>
            </Labeled>
          </div>

          <button
            onClick={() => void submit()}
            disabled={!ready}
            className="mt-3 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {isModeChange ? `${spec?.label} 지시 (모드 변경)` : "작업 지시"}
          </button>
        </section>

        {/* 큐 · 이력 */}
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 text-sm font-semibold">
            작업 큐 · 이력{" "}
            <span className="text-xs font-normal text-muted-foreground">
              ({orders.length}건)
            </span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="pb-2 pr-3">작업</th>
                  <th className="pb-2 pr-3">상태</th>
                  <th className="pb-2 pr-3">로봇</th>
                  <th className="pb-2 pr-3">진행률</th>
                  <th className="pb-2 pr-3">요청자</th>
                  <th className="pb-2 pr-3">사유</th>
                  <th className="pb-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-3 text-muted-foreground">
                      작업이 없습니다
                    </td>
                  </tr>
                ) : (
                  sortedOrders.map((o) => {
                    const pct = o.leg_count
                      ? Math.round((o.leg_idx / o.leg_count) * 100)
                      : 0;
                    const done = ["COMPLETED", "FAILED", "CANCELLED"].includes(
                      o.status,
                    );
                    return (
                      <tr key={o.id} className="border-t align-top">
                        <td className="py-2 pr-3 font-mono text-xs">{o.id}</td>
                        <td className="py-2 pr-3">
                          <span
                            className={`rounded px-1.5 py-0.5 text-xs font-bold ${STATUS_TONE[o.status] ?? ""}`}
                          >
                            {o.status}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-xs">{o.robot ?? "—"}</td>
                        <td className="py-2 pr-3">
                          <div className="flex items-center gap-2">
                            <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                              <span
                                className="block h-full rounded-full bg-primary"
                                style={{ width: `${pct}%` }}
                              />
                            </span>
                            <span className="text-xs tabular-nums">
                              {o.leg_idx}/{o.leg_count}
                            </span>
                          </div>
                        </td>
                        <td className="py-2 pr-3 text-xs text-muted-foreground">
                          {o.requester || "—"}
                        </td>
                        <td className="py-2 pr-3 text-xs text-muted-foreground">
                          {o.reason || "—"}
                        </td>
                        <td className="py-2 pr-3">
                          {!done ? (
                            <button
                              onClick={() =>
                                void ops
                                  .cancelTask(o.id)
                                  .then(load)
                                  .catch((e) =>
                                    setErr(
                                      e instanceof Error
                                        ? e.message
                                        : "취소 실패",
                                    ),
                                  )
                              }
                              className="rounded bg-secondary px-2 py-1 text-xs"
                            >
                              취소
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </AdminShell>
  );
}

function Labeled({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="space-y-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}
