import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  adminApi,
  type FleetRobotRow,
  type OrderSnapshot,
  type OrderStatus,
} from "@/lib/admin-api";

// 오케스트레이터는 WebSocket 피드가 없다(=fleet_node 와 다름) — 짧게 폴링한다.
const POLL_MS = 1500;

const STATUS_STYLE: Record<OrderStatus, string> = {
  PENDING: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  ASSIGNED: "bg-blue-500/15 text-blue-600 dark:text-blue-300",
  EXECUTING: "bg-amber-500/15 text-amber-600 dark:text-amber-300",
  COMPLETED: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
  FAILED: "bg-rose-500/15 text-rose-600 dark:text-rose-300",
  CANCELLED: "bg-slate-500/10 text-muted-foreground",
};

const TERMINAL: ReadonlySet<OrderStatus> = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);

/**
 * 자동 배차 근거 계산 — fleet 스냅샷에서 "지금 배차 가능한 로봇"을 고르고 그 이유를 문자열로.
 * 진짜 경매(비용 최소화)는 fleet_node(C++) 플러그인의 몫이고 아직 orchestrator 와 배선되지
 * 않았다. 여기서는 관제가 눈으로 확인 가능한 최소 휴리스틱(유휴 + 배터리 최대)을 쓰고,
 * **그 근거를 그대로 노출**한다 — 이 패널의 목적이 "왜 이 로봇인가"를 보는 것이므로.
 */
function pickRobot(
  robots: FleetRobotRow[],
): { robot: string; reason: string } | null {
  const idle = robots.filter((r) => !r.busy && !r.stale);
  if (idle.length === 0) return null;
  const sorted = [...idle].sort(
    (a, b) => (b.battery ?? -1) - (a.battery ?? -1),
  );
  const best = sorted[0];
  const batt =
    best.battery === null ? "배터리 미상" : `배터리 ${best.battery}%`;
  const others = idle.length - 1;
  const reason =
    `유휴 로봇 ${idle.length}대 중 ${batt} 최상위` +
    (others > 0 ? ` (나머지 ${others}대보다 우선)` : "");
  return { robot: best.name, reason };
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      {hint ? (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function OrderPanel() {
  const queryClient = useQueryClient();

  // 주문 작성
  const [book, setBook] = useState("");
  const [pickup, setPickup] = useState("");
  const [dropoff, setDropoff] = useState("");
  const [requester, setRequester] = useState("");
  const [priority, setPriority] = useState("0");

  // 배차 대상(수동) + 디버그 게이트
  const [assignRobot, setAssignRobot] = useState("");
  const [debug, setDebug] = useState(false);
  const [lastAuto, setLastAuto] = useState("");

  const ordersQuery = useQuery({
    queryKey: ["fleet", "orders"],
    queryFn: () => adminApi.fleetOrders(),
    refetchInterval: POLL_MS,
  });

  // 배차 후보 로봇은 fleet 스냅샷에서 — fleet_node 가 관측한 실제 목록.
  const snapshotQuery = useQuery({
    queryKey: ["fleet", "snapshot"],
    queryFn: () => adminApi.fleetSnapshot(),
    refetchInterval: POLL_MS,
  });

  const orders = ordersQuery.data?.orders ?? [];
  const robots = useMemo(
    () => snapshotQuery.data?.snapshot.robots ?? [],
    [snapshotQuery.data],
  );
  const robotNames = useMemo(() => robots.map((r) => r.name), [robots]);
  const pending = orders.filter((o) => o.status === "PENDING");
  const active = orders.filter((o) => !TERMINAL.has(o.status));

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["fleet", "orders"] });

  const createOrder = useMutation({
    mutationFn: () =>
      adminApi.fleetCreateOrder({
        book,
        pickup,
        dropoff,
        requester,
        priority: Number(priority) || 0,
      }),
    onSuccess: (res) => {
      toast.success(`주문 접수 — ${res.task_id}`);
      setBook("");
      setPickup("");
      setDropoff("");
      void invalidate();
    },
    onError: () => toast.error("주문 접수 실패"),
  });

  // 배차: 로봇을 인자로 받아 수동/자동을 한 경로로 처리한다.
  const assign = useMutation({
    mutationFn: ({ id, robot }: { id: string; robot: string }) =>
      adminApi.fleetAssignOrder(id, robot),
    onSuccess: (res) => {
      toast.success(`${res.task.id} → ${res.task.robot} 배차`);
      void invalidate();
    },
    onError: () => toast.error("배차 실패 (이미 배정됐거나 없는 주문)"),
  });

  const advance = useMutation({
    mutationFn: (id: string) => adminApi.fleetAdvanceOrder(id),
    onSuccess: (res) => {
      toast.success(`${res.task.id} 다리 강제완료 → ${res.task.status}`);
      void invalidate();
    },
    onError: () => toast.error("강제완료 실패 (EXECUTING 아님)"),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => adminApi.fleetCancelOrder(id),
    onSuccess: (res) => {
      toast.success(`${res.task.id} 취소`);
      void invalidate();
    },
    onError: () => toast.error("취소 실패"),
  });

  const manualAssign = (id: string) => {
    if (!assignRobot) {
      toast.error("배차할 로봇을 먼저 고르세요");
      return;
    }
    assign.mutate({ id, robot: assignRobot });
  };

  const autoAssign = (id: string) => {
    const pick = pickRobot(robots);
    if (!pick) {
      toast.error("자동 배차 불가 — 유휴 로봇이 없습니다");
      return;
    }
    setLastAuto(`${id} → ${pick.robot}: ${pick.reason}`);
    assign.mutate({ id, robot: pick.robot });
  };

  const canCreate = book && pickup && dropoff && !createOrder.isPending;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
        <span>
          배달 주문을 다리(주행→집기→주행→놓기)로 분해해 하나씩 굴리는 큐다.
          fleet_node(배차·교통) 위 계층 — 로봇 배선 전에도 강제완료로 시퀀스를
          검증한다.
        </span>
        <label className="flex shrink-0 items-center gap-2">
          <span>디버그 도구</span>
          <Switch checked={debug} onCheckedChange={setDebug} />
        </label>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="주문 작성"
          hint="도서·집을 선반(pickup)·전달지(dropoff) 를 waypoint 로 지정한다. 도서→선반→waypoint 해석은 상위 데이터 계층(#27)."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="order-book">도서</Label>
              <Input
                id="order-book"
                value={book}
                onChange={(e) => setBook(e.target.value)}
                placeholder="예: ISBN 또는 제목"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="order-requester">요청자</Label>
              <Input
                id="order-requester"
                value={requester}
                onChange={(e) => setRequester(e.target.value)}
                placeholder="(선택)"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="order-pickup">집을 선반(pickup)</Label>
              <Input
                id="order-pickup"
                value={pickup}
                onChange={(e) => setPickup(e.target.value)}
                placeholder="예: 3"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="order-dropoff">전달지(dropoff)</Label>
              <Input
                id="order-dropoff"
                value={dropoff}
                onChange={(e) => setDropoff(e.target.value)}
                placeholder="예: 7"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="order-priority">우선순위</Label>
              <Input
                id="order-priority"
                type="number"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              />
            </div>
          </div>
          <Button
            className="mt-3"
            disabled={!canCreate}
            onClick={() => createOrder.mutate()}
          >
            주문 접수
          </Button>
        </Section>

        <Section
          title="배차 대상 (수동)"
          hint="여기서 고른 로봇이 대기 큐의 「수동 배차」에 쓰인다. 「자동」은 이 선택을 무시하고 유휴 로봇을 근거와 함께 고른다."
        >
          <div className="space-y-2">
            <Select value={assignRobot} onValueChange={setAssignRobot}>
              <SelectTrigger>
                <SelectValue placeholder="로봇 선택" />
              </SelectTrigger>
              <SelectContent>
                {robotNames.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={assignRobot}
              onChange={(e) => setAssignRobot(e.target.value)}
              placeholder="또는 직접 입력 (예: pinky1)"
            />
            {robotNames.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                fleet_node 가 관측한 로봇이 없습니다. 이름을 직접 입력하거나
                fleet_node 를 기동하세요.
              </p>
            ) : null}
            {lastAuto ? (
              <p className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">
                최근 자동 배차 근거 — {lastAuto}
              </p>
            ) : null}
          </div>
        </Section>
      </div>

      <Section
        title="주문 큐 / 현황"
        hint={`대기 ${pending.length} · 진행 ${active.length - pending.length} · 전체 ${orders.length}. 다리 = ${"주행→집기→주행→놓기"}.`}
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="pb-2 pr-3">주문</th>
                <th className="pb-2 pr-3">상태</th>
                <th className="pb-2 pr-3">로봇</th>
                <th className="pb-2 pr-3">진행(다리)</th>
                <th className="pb-2 pr-3">요청자/우선</th>
                <th className="pb-2 pr-3">사유</th>
                <th className="pb-2 pr-3">동작</th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-3 text-muted-foreground">
                    주문이 없습니다. 위에서 접수하세요.
                  </td>
                </tr>
              ) : (
                orders.map((o) => (
                  <OrderRow
                    key={o.id}
                    order={o}
                    debug={debug}
                    onManual={() => manualAssign(o.id)}
                    onAuto={() => autoAssign(o.id)}
                    onAdvance={() => advance.mutate(o.id)}
                    onCancel={() => cancel.mutate(o.id)}
                    busy={
                      assign.isPending || advance.isPending || cancel.isPending
                    }
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

function OrderRow({
  order: o,
  debug,
  onManual,
  onAuto,
  onAdvance,
  onCancel,
  busy,
}: {
  order: OrderSnapshot;
  debug: boolean;
  onManual: () => void;
  onAuto: () => void;
  onAdvance: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const isPending = o.status === "PENDING";
  const isExecuting = o.status === "EXECUTING";
  const isTerminal = TERMINAL.has(o.status);
  return (
    <tr className="border-t align-top">
      <td className="py-2 pr-3 font-mono text-xs">
        {o.id}
        <div className="text-muted-foreground">{o.task_type}</div>
      </td>
      <td className="py-2 pr-3">
        <span
          className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[o.status]}`}
        >
          {o.status}
        </span>
      </td>
      <td className="py-2 pr-3">{o.robot ?? "—"}</td>
      <td className="py-2 pr-3">
        {o.leg_idx}/{o.leg_count}
        {o.current_leg ? (
          <span className="ml-1 text-xs text-muted-foreground">
            ({o.current_leg})
          </span>
        ) : null}
      </td>
      <td className="py-2 pr-3 text-xs text-muted-foreground">
        {o.requester || "—"}
        {o.priority ? ` · P${o.priority}` : ""}
      </td>
      <td className="py-2 pr-3 text-xs text-muted-foreground">
        {o.reason || "—"}
      </td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap gap-1">
          {isPending ? (
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={onManual}
              >
                수동 배차
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onAuto}
              >
                자동
              </Button>
            </>
          ) : null}
          {debug && isExecuting ? (
            <Button
              size="sm"
              variant="outline"
              className="border-amber-500/50 text-amber-600 dark:text-amber-300"
              disabled={busy}
              onClick={onAdvance}
              title="[디버그] 로봇 없이 현재 다리를 완료로 치고 다음으로"
            >
              강제완료
            </Button>
          ) : null}
          {!isTerminal ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={onCancel}
            >
              취소
            </Button>
          ) : null}
        </div>
      </td>
    </tr>
  );
}
