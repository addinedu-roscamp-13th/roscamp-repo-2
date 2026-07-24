import type { FleetRobotRow, OrderStatus } from "@/lib/admin-api";

/** 오케스트레이터는 WebSocket 피드가 없다(=fleet_node 와 다름) — 짧게 폴링한다. */
export const POLL_MS = 1500;

export const STATUS_STYLE: Record<OrderStatus, string> = {
  PENDING: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  ASSIGNED: "bg-blue-500/15 text-blue-600 dark:text-blue-300",
  EXECUTING: "bg-amber-500/15 text-amber-600 dark:text-amber-300",
  COMPLETED: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
  FAILED: "bg-rose-500/15 text-rose-600 dark:text-rose-300",
  CANCELLED: "bg-slate-500/10 text-muted-foreground",
};

export const TERMINAL: ReadonlySet<OrderStatus> = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);

/**
 * 배달 1건이 풀리는 다리 순서. orchestrator.submit_delivery 가 만드는 4다리와 같다.
 * current_leg 는 종류("navigate"|"perform_action")만 주므로, 몇 번째인지는 leg_idx 로 읽는다.
 */
export const LEG_STEPS = [
  { kind: "navigate", label: "주행 → 서가" },
  { kind: "perform_action", label: "집기" },
  { kind: "navigate", label: "주행 → 전달지" },
  { kind: "perform_action", label: "놓기" },
] as const;

/**
 * 배차를 받을 수 있는 상태 — FSM 전이표(`libi_modes/registry.py` = `fsm_model.py`, 둘은 동일)에서
 * `task_assigned` 로 WORKING 에 갈 수 있는 상태가 근거다:
 *
 *   ("IDLE",        "WORKING", "task_assigned")
 *   ("PATROL",      "WORKING", "task_assigned")
 *   ("INTERACTING", "WORKING", "task_assigned")
 *
 * fleet_node.cpp 의 `can_accept()` 도 IDLE|PATROL 을 받는다 — 여기서 그 규칙을 그대로 따른다.
 * (INTERACTING 은 방문자가 패널을 쓰는 중이라 배차 후보에서 제외한다 — fleet_node 와 동일)
 *
 * 상태를 모르면(state===null) 배차하지 않는다 — 관제가 모르는 배차보다 거절이 낫다
 * (fail-closed, admin_follow 와 같은 원칙).
 */
const ACCEPTING_STATES: ReadonlySet<string> = new Set(["IDLE", "PATROL"]);

/**
 * fleet_node 의 자체 순회 task 인가. 순회는 `P-<robot>` 이름으로 만들어진다.
 *
 * 순회 중이어도 **배차는 받을 수 있다** — fleet_node 의 `on_submit` 이
 * `if (r.busy) { cancel_task(robot); }` 로 순회를 선점하고 새 일을 받는다.
 * 여기서 막으면 "순회 도는 로봇에게는 영영 주문을 못 준다"가 되어 fleet_node 와 어긋난다.
 */
function isPatrolTask(taskId: string | null | undefined): boolean {
  return !!taskId && taskId.startsWith("P-");
}

export function isDispatchable(r: FleetRobotRow): boolean {
  return (
    r.state !== null &&
    ACCEPTING_STATES.has(r.state) &&
    (!r.busy || isPatrolTask(r.task_id)) &&
    !r.stale
  );
}

/** 배차 불가 사유 — UI 가 "왜 이 로봇은 안 되는가"를 그대로 보여준다. */
export function undispatchableReason(r: FleetRobotRow): string {
  if (r.stale) return "텔레메트리 끊김";
  if (r.state === null) return "상태 미상 (fleet_node 관측 없음)";
  if (!ACCEPTING_STATES.has(r.state)) return `${r.state} — 배차 불가`;
  if (r.busy && !isPatrolTask(r.task_id)) return "작업 중";
  return "";
}

/**
 * 자동 배차 후보 선택 + 근거 문자열.
 *
 * 정렬 규칙: **PATROL 우선 → 그 다음 배터리 최대.**
 * IDLE 도 FSM 상 배차를 받을 수 있지만, 이미 순찰 중인 로봇을 먼저 쓰는 것이 운영 의도다
 * (충전 직후 IDLE 로봇도 후보로 남으므로 큐가 막히지 않는다).
 *
 * ⚠️ 진짜 경매(Dijkstra 경로비용 최소)는 fleet_node(C++) `Auction` 플러그인 몫이고
 * orchestrator 와 아직 배선되지 않았다(핸드오프 6절 ①). 여기서는 관제가 눈으로 확인 가능한
 * 최소 휴리스틱을 쓰고 **그 근거를 그대로 노출**한다 — 이 패널의 목적이 "왜 이 로봇인가"를
 * 보는 것이므로. 배선되면 이 함수는 fleet_node 의 선택으로 대체된다.
 */
export function pickRobot(
  robots: FleetRobotRow[],
): { robot: string; reason: string } | null {
  const ready = robots.filter(isDispatchable);
  if (ready.length === 0) return null;

  const rank = (r: FleetRobotRow) => (r.state === "PATROL" ? 0 : 1);
  const sorted = [...ready].sort(
    (a, b) => rank(a) - rank(b) || (b.battery ?? -1) - (a.battery ?? -1),
  );
  const best = sorted[0];

  const batt =
    best.battery === null ? "배터리 미상" : `배터리 ${best.battery}%`;
  const patrolCount = ready.filter((r) => r.state === "PATROL").length;
  const tier =
    best.state === "PATROL"
      ? `PATROL ${patrolCount}대 중`
      : `PATROL 없음 → IDLE ${ready.length}대 중`;
  const others = ready.length - 1;
  const reason =
    `${tier} ${batt} 최상위` +
    (others > 0 ? ` (나머지 ${others}대보다 우선)` : "");
  return { robot: best.name, reason };
}
