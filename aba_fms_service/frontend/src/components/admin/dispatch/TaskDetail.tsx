import { Button } from "@/components/ui/button";
import type { OrderSnapshot } from "@/lib/admin-api";

import { STATUS_STYLE, TERMINAL } from "./dispatch-shared";

/**
 * 선택한 주문 1건의 상세 — orchestrator 가 이 주문을 어떤 다리로 쪼갰고 지금 어디까지
 * 왔는지를 보여준다. 큐(목록)는 "무엇이 있나", 여기는 "이 하나가 어떻게 돌고 있나".
 */

function LegRow({
  index,
  label,
  kind,
  state,
}: {
  index: number;
  label: string;
  kind: string;
  state: "done" | "current" | "todo";
}) {
  const dot = {
    done: "bg-emerald-500 border-emerald-500",
    current: "bg-amber-500 border-amber-500 animate-pulse",
    todo: "bg-transparent border-muted-foreground/40",
  }[state];
  const text = {
    done: "text-muted-foreground line-through",
    current: "font-medium text-foreground",
    todo: "text-muted-foreground",
  }[state];

  return (
    <li className="flex items-center gap-3">
      <span
        className={`size-3 shrink-0 rounded-full border-2 ${dot}`}
        aria-hidden
      />
      <span className={`text-sm ${text}`}>
        {index + 1}. {label}
      </span>
      <span className="ml-auto font-mono text-xs text-muted-foreground">
        {kind}
      </span>
    </li>
  );
}

export function TaskDetail({
  order,
  onAdvance,
  onCancel,
  onDismiss,
  busy,
}: {
  order: OrderSnapshot | null;
  onAdvance: () => void;
  onCancel: () => void;
  /** 끝난 주문을 큐에서 치운다. `cancel` 은 종료 상태를 무시하므로 별도 동작이 필요하다. */
  onDismiss: () => void;
  busy: boolean;
}) {
  if (!order) {
    return (
      <div className="flex h-full min-h-[16rem] items-center justify-center rounded-lg border border-dashed p-6">
        <p className="text-sm text-muted-foreground">
          왼쪽 큐에서 주문을 선택하면 다리 진행 상황이 여기에 표시됩니다.
        </p>
      </div>
    );
  }

  const isExecuting = order.status === "EXECUTING";
  const isTerminal = TERMINAL.has(order.status);
  // task_type 마다 다리 뜻이 다르다(수거 4개는 배달 4개와 전혀 다르다) — 실제
  // leg 값과 백엔드가 만든 라벨을 그대로 쓴다. 예전엔 4칸짜리 고정 배열을
  // leg_idx % 4 로 흉내내서, 수거처럼 종류가 다르면 항상 틀린 라벨이 떴다.
  const steps = order.legs.map((leg, i) => ({
    label: order.leg_labels[i] ?? "작업",
    kind: leg.type,
  }));
  const pct =
    order.leg_count === 0
      ? 0
      : Math.round((order.leg_idx / order.leg_count) * 100);

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{order.id}</span>
          <span
            className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[order.status]}`}
          >
            {order.status}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          {order.task_type} · 로봇 {order.robot ?? "미배차"}
          {order.requester ? ` · 요청자 ${order.requester}` : ""}
          {order.priority ? ` · P${order.priority}` : ""}
        </p>
      </header>

      <div className="space-y-1">
        <div className="flex items-baseline justify-between text-xs text-muted-foreground">
          <span>다리 진행</span>
          <span className="tabular-nums">
            {order.leg_idx}/{order.leg_count} ({pct}%)
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-emerald-500 transition-[width]"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <ol className="space-y-2">
        {steps.map((s, i) => (
          <LegRow
            key={i}
            index={i}
            label={s.label}
            kind={s.kind}
            state={
              i < order.leg_idx
                ? "done"
                : i === order.leg_idx && isExecuting
                  ? "current"
                  : "todo"
            }
          />
        ))}
      </ol>

      {order.reason ? (
        <p className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">
          사유 — {order.reason}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2 border-t pt-3">
        <Button
          size="sm"
          variant="outline"
          className="border-amber-500/50 text-amber-600 dark:text-amber-300"
          disabled={busy || !isExecuting}
          onClick={onAdvance}
          title="로봇 보고 없이 현재 다리를 완료로 치고 다음 다리로 넘긴다 (로봇 미배선 상태 검증용)"
        >
          현재 다리 완료 처리
        </Button>
        {isTerminal ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={onDismiss}>
            큐에서 치우기
          </Button>
        ) : (
          <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            주문 취소
          </Button>
        )}
      </div>
      {!isExecuting && !isTerminal ? (
        <p className="text-xs text-muted-foreground">
          「완료 처리」는 EXECUTING 상태에서만 쓸 수 있습니다 (지금{" "}
          {order.status}).
        </p>
      ) : null}
    </div>
  );
}
