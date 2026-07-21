import type { FleetRobotRow, OrderSnapshot } from "@/lib/admin-api";

import { isDispatchable, undispatchableReason } from "./dispatch-shared";

/**
 * 한눈 요약 — "지금 큐가 어떤 상태이고 배차할 로봇이 있는가"를 숫자로 즉시 보여준다.
 * 아래 큐/상세를 읽기 전에 상황 판단이 끝나야 하므로 맨 위에 둔다.
 */

function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number | string;
  tone?: "default" | "warn" | "bad" | "good";
}) {
  const toneClass = {
    default: "text-foreground",
    warn: "text-amber-600 dark:text-amber-300",
    bad: "text-rose-600 dark:text-rose-300",
    good: "text-emerald-600 dark:text-emerald-300",
  }[tone];
  return (
    <div className="flex min-w-[4.5rem] flex-col">
      <span className={`text-xl font-semibold tabular-nums ${toneClass}`}>
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

export function SummaryStrip({
  orders,
  robots,
}: {
  orders: OrderSnapshot[];
  robots: FleetRobotRow[];
}) {
  const count = (s: OrderSnapshot["status"]) =>
    orders.filter((o) => o.status === s).length;

  const pending = count("PENDING");
  const running = count("ASSIGNED") + count("EXECUTING");
  const done = count("COMPLETED");
  const failed = count("FAILED");

  const ready = robots.filter(isDispatchable).length;
  const byState = (s: string) => robots.filter((r) => r.state === s).length;

  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-4 rounded-lg border bg-muted/30 p-4">
      <div className="flex gap-6">
        <Stat
          label="대기"
          value={pending}
          tone={pending > 0 ? "warn" : "default"}
        />
        <Stat label="진행" value={running} />
        <Stat label="완료" value={done} tone="good" />
        <Stat
          label="실패"
          value={failed}
          tone={failed > 0 ? "bad" : "default"}
        />
      </div>

      <div className="h-10 w-px bg-border" aria-hidden />

      <div className="flex gap-6">
        <Stat
          label="배차 가능"
          value={ready}
          tone={ready > 0 ? "good" : "warn"}
        />
        <Stat label="PATROL" value={byState("PATROL")} />
        <Stat label="IDLE" value={byState("IDLE")} />
        <Stat label="WORKING" value={byState("WORKING")} />
        <Stat label="로봇 전체" value={robots.length} />
      </div>

      {/* 예전엔 사유를 세 가지로 뭉뚱그려("…아니거나 작업 중/텔레메트리 끊김") 보여줬다.
          그래서 "IDLE 1대인데 배차 가능 0대" 같은 화면이 뜨면 왜인지 알 수가 없었다
          (실제 원인은 fleet_node 가 붙잡고 있던 고아 task 였다).
          이제 **로봇마다 실제 사유를 그대로** 적는다. */}
      {ready === 0 && pending > 0 ? (
        <div className="space-y-1 text-xs text-amber-600 dark:text-amber-300">
          <p>대기 주문이 있으나 배차 가능한 로봇이 없습니다.</p>
          <ul className="space-y-0.5">
            {robots.length === 0 ? (
              <li>· 관측된 로봇이 없습니다 (fleet_node 링크 확인)</li>
            ) : (
              robots.map((r) => (
                <li key={r.name} className="font-mono">
                  · {r.name}: {undispatchableReason(r)}
                  {r.task_id ? ` [${r.task_id}]` : ""}
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
