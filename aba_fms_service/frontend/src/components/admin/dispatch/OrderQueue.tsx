import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FleetRobotRow, OrderSnapshot } from "@/lib/admin-api";

import {
  STATUS_STYLE,
  TERMINAL,
  isDispatchable,
  pickRobot,
  undispatchableReason,
} from "./dispatch-shared";

/**
 * 유입 큐 — "지금 무슨 주문이 있고 무엇을 배차해야 하나".
 * 행을 고르면 오른쪽 TaskDetail 이 그 주문의 다리 진행을 보여준다.
 *
 * 배차 모드(자동/수동)는 이 컴포넌트가 소유한다 — 큐를 보면서 결정하는 값이라
 * 큐 밖으로 끌어내면 시선이 갈라진다.
 */

type Mode = "auto" | "manual";

export function OrderQueue({
  orders,
  robots,
  selectedId,
  onSelect,
  onAssign,
  busy,
}: {
  orders: OrderSnapshot[];
  robots: FleetRobotRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAssign: (id: string, robot: string) => void;
  busy: boolean;
}) {
  const [mode, setMode] = useState<Mode>("auto");
  const [manualRobot, setManualRobot] = useState("");
  const [lastAuto, setLastAuto] = useState("");

  const ready = robots.filter(isDispatchable);

  const handleAssign = (id: string) => {
    if (mode === "manual") {
      if (!manualRobot) {
        toast.error("배차할 로봇을 먼저 고르세요");
        return;
      }
      onAssign(id, manualRobot);
      return;
    }
    const pick = pickRobot(robots);
    if (!pick) {
      toast.error("자동 배차 불가 — 배차 가능한 로봇이 없습니다");
      return;
    }
    setLastAuto(`${id} → ${pick.robot}: ${pick.reason}`);
    onAssign(id, pick.robot);
  };

  return (
    <section className="space-y-3 rounded-lg border p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">주문 큐</h3>

        {/* 배차 모드 토글 */}
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border p-0.5">
            {(["auto", "manual"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {m === "auto" ? "자동 배차" : "수동 배차"}
              </button>
            ))}
          </div>

          {mode === "manual" ? (
            <Select value={manualRobot} onValueChange={setManualRobot}>
              <SelectTrigger className="h-8 w-44 text-xs">
                <SelectValue placeholder="로봇 선택" />
              </SelectTrigger>
              <SelectContent>
                {robots.map((r) => {
                  const bad = undispatchableReason(r);
                  return (
                    <SelectItem key={r.name} value={r.name} disabled={!!bad}>
                      {r.name}
                      <span className="ml-2 text-xs text-muted-foreground">
                        {bad || r.state}
                      </span>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-xs text-muted-foreground">
              PATROL 우선 · 배터리 최대 ({ready.length}대 가능)
            </span>
          )}
        </div>
      </header>

      {mode === "auto" && lastAuto ? (
        <p className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">
          최근 자동 배차 근거 — {lastAuto}
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="pb-2 pr-3">주문</th>
              <th className="pb-2 pr-3">상태</th>
              <th className="pb-2 pr-3">로봇</th>
              <th className="pb-2 pr-3">진행</th>
              <th className="pb-2 pr-3">배차</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-3 text-muted-foreground">
                  주문이 없습니다. 아래 「주문 만들기」에서 접수하세요.
                </td>
              </tr>
            ) : (
              orders.map((o) => {
                const selected = o.id === selectedId;
                return (
                  <tr
                    key={o.id}
                    onClick={() => onSelect(o.id)}
                    className={`cursor-pointer border-t transition ${
                      selected ? "bg-muted" : "hover:bg-muted/50"
                    }`}
                  >
                    <td className="py-2 pr-3 font-mono text-xs">{o.id}</td>
                    <td className="py-2 pr-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[o.status]}`}
                      >
                        {o.status}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-xs">{o.robot ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs tabular-nums">
                      {o.leg_idx}/{o.leg_count}
                    </td>
                    <td className="py-2 pr-3">
                      {o.status === "PENDING" ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleAssign(o.id);
                          }}
                        >
                          배차
                        </Button>
                      ) : TERMINAL.has(o.status) ? (
                        <span className="text-xs text-muted-foreground">—</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          배차됨
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
