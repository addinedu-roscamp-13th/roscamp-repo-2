import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { FleetPanel } from "@/components/admin/FleetPanel";
import { OrderCreate } from "@/components/admin/dispatch/OrderCreate";
import { OrderQueue } from "@/components/admin/dispatch/OrderQueue";
import { SummaryStrip } from "@/components/admin/dispatch/SummaryStrip";
import { TaskDetail } from "@/components/admin/dispatch/TaskDetail";
import { TemplatePanel } from "@/components/admin/dispatch/TemplatePanel";
import {
  POLL_MS,
  pickRobot,
} from "@/components/admin/dispatch/dispatch-shared";
import {
  listTemplates,
  removeTemplate,
  saveTemplate,
  type OrderTemplate,
} from "@/components/admin/dispatch/templates";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { adminApi, type CreateOrderInput } from "@/lib/admin-api";

export const Route = createFileRoute("/admin/_authed/dispatch")({
  component: DispatchPage,
});

/**
 * FMS 배차·교통 대시보드.
 *
 * 정보 위계: ① 요약(지금 상황) → ② 큐(무엇을 배차하나) + 상세(이 하나가 어떻게 도나)
 * → ③ 주문 만들기 → ④ fleet_node 코어(보조, 접힘).
 *
 * 데이터는 orchestrator 폴링(WebSocket 피드가 없다). 서버 상태는 react-query 가 들고,
 * 이 컴포넌트는 "무엇을 선택했나"만 로컬로 가진다.
 */
function DispatchPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ordersQuery = useQuery({
    queryKey: ["fleet", "orders"],
    queryFn: () => adminApi.fleetOrders(),
    refetchInterval: POLL_MS,
  });

  const snapshotQuery = useQuery({
    queryKey: ["fleet", "snapshot"],
    queryFn: () => adminApi.fleetSnapshot(),
    refetchInterval: POLL_MS,
  });

  const orders = useMemo(
    () => ordersQuery.data?.orders ?? [],
    [ordersQuery.data],
  );
  const robots = useMemo(
    () => snapshotQuery.data?.snapshot.robots ?? [],
    [snapshotQuery.data],
  );

  // 선택은 id 로만 들고, 표시할 값은 매 폴링의 최신 스냅샷에서 다시 찾는다.
  const selected = useMemo(
    () => orders.find((o) => o.id === selectedId) ?? null,
    [orders, selectedId],
  );

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["fleet", "orders"] });

  const createOrder = useMutation({
    mutationFn: (input: CreateOrderInput) => adminApi.fleetCreateOrder(input),
    onSuccess: (res) => {
      toast.success(`주문 접수 — ${res.task_id}`);
      setSelectedId(res.task_id);
      void invalidate();
    },
    onError: () => toast.error("주문 접수 실패"),
  });

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
      toast.success(`${res.task.id} 다리 완료 → ${res.task.status}`);
      void invalidate();
    },
    onError: () => toast.error("완료 처리 실패 (EXECUTING 아님)"),
  });

  const dismiss = useMutation({
    mutationFn: (id: string) => adminApi.fleetDismissOrder(id),
    onSuccess: (res) => {
      toast.success(`${res.dismissed} 큐에서 치움`);
      setSelectedId(null);
      void invalidate();
    },
    onError: () => toast.error("치우기 실패 (진행 중이거나 없는 주문)"),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => adminApi.fleetCancelOrder(id),
    onSuccess: (res) => {
      toast.success(`${res.task.id} 취소`);
      void invalidate();
    },
    onError: () => toast.error("취소 실패"),
  });

  const busy =
    assign.isPending ||
    advance.isPending ||
    cancel.isPending ||
    dismiss.isPending;

  // ── 주문 템플릿 (localStorage) ───────────────────────────────────────────
  const [templates, setTemplates] = useState<OrderTemplate[]>(() =>
    listTemplates(),
  );

  const templateInput = (t: OrderTemplate): CreateOrderInput => ({
    book: t.book,
    pickup: t.pickup,
    dropoff: t.dropoff,
    requester: t.requester,
    priority: t.priority,
  });

  // 이름을 use* 로 지으면 eslint(react-hooks) 가 Hook 으로 오인한다 — create* 로 둔다.
  /** 템플릿 → 주문 접수만. */
  const createFromTemplate = (t: OrderTemplate) =>
    createOrder.mutate(templateInput(t));

  /**
   * 템플릿 → 접수 + 자동 배차.
   * 접수는 성공했는데 배차 가능한 로봇이 없을 수 있다 — 그때는 주문을 되돌리지 않고
   * 큐에 PENDING 으로 남긴다(수동 배차로 이어갈 수 있게).
   */
  const createAndAssignFromTemplate = async (t: OrderTemplate) => {
    try {
      const res = await createOrder.mutateAsync(templateInput(t));
      const pick = pickRobot(robots);
      if (!pick) {
        toast.warning("접수됨 — 배차 가능한 로봇이 없어 큐에 대기합니다");
        return;
      }
      assign.mutate({ id: res.task_id, robot: pick.robot });
    } catch {
      // createOrder.onError 가 이미 토스트를 띄운다.
    }
  };

  return (
    <AdminShell title="FMS 배차·교통">
      <div className="space-y-6">
        <SummaryStrip orders={orders} robots={robots} />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <OrderQueue
            orders={orders}
            robots={robots}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAssign={(id, robot) => assign.mutate({ id, robot })}
            busy={busy}
          />
          <TaskDetail
            order={selected}
            onAdvance={() => selected && advance.mutate(selected.id)}
            onCancel={() => selected && cancel.mutate(selected.id)}
            onDismiss={() => selected && dismiss.mutate(selected.id)}
            busy={busy}
          />
        </div>

        <TemplatePanel
          templates={templates}
          onUse={createFromTemplate}
          onUseAndAssign={(t) => void createAndAssignFromTemplate(t)}
          onRemove={(id) => setTemplates(removeTemplate(id))}
          busy={busy || createOrder.isPending}
        />

        <OrderCreate
          onSubmit={(input) => createOrder.mutate(input)}
          onSaveTemplate={(t) => {
            setTemplates(saveTemplate(t));
            toast.success(`템플릿 저장 — ${t.name}`);
          }}
          pending={createOrder.isPending}
        />

        <Collapsible>
          <CollapsibleTrigger className="w-full rounded-lg border border-dashed p-3 text-left text-sm text-muted-foreground hover:bg-muted/50">
            fleet_node 배차·교통 코어 (플러그인·navgraph·로봇 관측) — 펼치기
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-4">
            <FleetPanel />
          </CollapsibleContent>
        </Collapsible>
      </div>
    </AdminShell>
  );
}
