import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { BtTreeView } from "@/components/admin/BtTreeView";
import { Button } from "@/components/ui/button";
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
  type FsmModel,
  type FsmSnapshot,
  type Robot,
} from "@/lib/admin-api";
import { cn } from "@/lib/utils";

// 상태별 배지 색. 상태 **이름**은 여기 적지 않는다 — 키가 없으면 기본색으로 떨어진다.
// (전이 박스는 GET /api/fsm/model 이 정본이고, 색은 표현일 뿐이다.)
const BADGE_TONE: Record<string, string> = {
  PATROL: "bg-blue-100 text-blue-700",
  WORKING: "bg-orange-100 text-orange-700",
  CHARGING: "bg-emerald-100 text-emerald-700",
  RETURNING: "bg-amber-100 text-amber-700",
  INTERACTING: "bg-cyan-100 text-cyan-700",
  SECURITY_PATROL: "bg-violet-100 text-violet-700",
  IDLE: "bg-slate-100 text-slate-600",
  ERROR: "bg-rose-100 text-rose-700",
};

function Pill({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone?: "warn";
}) {
  return (
    <span
      className={cn(
        "rounded px-2 py-0.5 text-xs font-medium",
        tone === "warn"
          ? "bg-amber-100 text-amber-800"
          : "bg-slate-100 text-slate-600",
      )}
    >
      {children}
    </span>
  );
}

interface Props {
  /** 이 카드가 보고 있는 로봇 이름(DB 이름). 미선택이면 null. */
  robotName: string | null;
  onRobotChange: (name: string) => void;
  /** 선택 후보 — 주행 로봇만 넘어온다. */
  robots: Robot[];
  /** 다른 카드가 이미 보고 있는 로봇 이름들. 중복 선택을 표시만 하고 막지는 않는다. */
  takenNames: string[];
  model: FsmModel;
  /** 페이지가 공유하는 WebSocket 이 채워주는 스냅샷. 없으면 최초 조회로 대체한다. */
  liveSnapshots: Record<string, FsmSnapshot>;
}

export function FsmRobotCard({
  robotName,
  onRobotChange,
  robots,
  takenNames,
  model,
  liveSnapshots,
}: Props) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState("");
  const [force, setForce] = useState(false);
  const [open, setOpen] = useState(true);

  // 최초 1회 스냅샷 — WebSocket 첫 프레임이 오기 전 빈 화면을 막는다.
  const stateQuery = useQuery({
    queryKey: ["fsm", "state", robotName],
    queryFn: () => adminApi.fsmState(robotName!),
    enabled: !!robotName,
  });

  const historyQuery = useQuery({
    queryKey: ["fsm", "history", robotName],
    queryFn: () => adminApi.fsmHistory(robotName!, 20),
    enabled: !!robotName,
  });

  // 이력은 최신이 배열 맨 앞(desc)이라, 새 항목이 오면 목록 맨 위로 스크롤해서
  // 사용자가 아래로 내려가 있어도 방금 일어난 전이를 놓치지 않게 한다.
  const historyRef = useRef<HTMLDivElement | null>(null);
  const latestHistoryId = historyQuery.data?.items?.[0]?.id;
  useEffect(() => {
    if (latestHistoryId != null) historyRef.current?.scrollTo({ top: 0 });
  }, [latestHistoryId]);

  // 백엔드가 정규화한 브릿지 키로 캐시가 오므로, 초기 응답이 알려준 키를 우선 쓴다.
  const resolvedId = stateQuery.data?.robot_id ?? robotName ?? "";
  const snapshot =
    (resolvedId ? liveSnapshots[resolvedId] : undefined) ??
    stateQuery.data?.snapshot ??
    null;
  const current = snapshot?.current_state ?? null;
  const robot = robots.find((r) => r.name === robotName) ?? null;

  // 이력은 수동 전이 성공 시에만 갱신했다 — BT 가 스스로 전이할 때는(배터리 임계값,
  // 커맨드 완료 등) 로그가 안 따라왔다. 스냅샷의 current_state 가 바뀔 때마다
  // 다시 불러온다. 최초 마운트 값은 "전이"가 아니므로 건너뛴다.
  const prevCurrentRef = useRef<string | null>(null);
  useEffect(() => {
    if (!current) return;
    if (prevCurrentRef.current !== null && prevCurrentRef.current !== current) {
      queryClient.invalidateQueries({
        queryKey: ["fsm", "history", robotName],
      });
    }
    prevCurrentRef.current = current;
  }, [current, robotName, queryClient]);

  const allowed = useMemo(
    () => (current ? (model.allowed_targets[current] ?? []) : []),
    [model, current],
  );

  const transition = useMutation({
    mutationFn: () =>
      adminApi.fsmTransition({
        robot_id: resolvedId,
        target_state: target,
        force,
      }),
    onSuccess: (result) => {
      if (result.accepted) toast.success(`전이 수락: ${result.current_state}`);
      else toast.error(`전이 거부: ${result.reason}`);
      queryClient.invalidateQueries({
        queryKey: ["fsm", "history", robotName],
      });
    },
    onError: () => toast.error("전이 요청을 보내지 못했습니다."),
  });

  function handleExecute() {
    if (!target || !robotName) return;
    const warning = force
      ? "\n\n⚠️ 강제 전이(force)가 켜져 있습니다. 전이 박스에 없는 간선도 실행됩니다."
      : "";
    if (
      !window.confirm(
        `[${robotName}] '${current}' → '${target}' 전이를 실행할까요?${warning}`,
      )
    )
      return;
    transition.mutate();
  }

  const received = !!snapshot;
  const badgeText = current ?? "수신 대기";
  const badgeTone = current
    ? (BADGE_TONE[current] ?? "bg-slate-100 text-slate-600")
    : "bg-slate-100 text-slate-400";

  return (
    <section className="flex h-full min-h-[75vh] flex-col overflow-hidden rounded-xl border bg-card">
      {/* ── 헤더: 항상 보인다. 3대를 한눈에 훑는 게 목적 ──
          좁은 컬럼이라 두 줄로 나눈다: (선택+상태) / (브랜치+계기판) */}
      <div className="space-y-2 border-b px-3 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={open ? "접기" : "펼치기"}
          >
            {open ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </button>

          <Select value={robotName ?? ""} onValueChange={onRobotChange}>
            <SelectTrigger className="h-8 min-w-0 flex-1">
              <SelectValue placeholder="로봇 선택" />
            </SelectTrigger>
            <SelectContent>
              {robots.map((r) => (
                <SelectItem key={r.id} value={r.name}>
                  {r.name}
                  {takenNames.includes(r.name) && r.name !== robotName && (
                    <span className="ml-2 text-xs opacity-60">(다른 카드)</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <span
            className={cn(
              "shrink-0 rounded-full px-3.5 py-1 text-base font-bold tracking-wide",
              badgeTone,
            )}
          >
            {badgeText}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-medium text-muted-foreground">
            {received
              ? (snapshot?.active_branch ?? "")
              : robotName
                ? "FSM 노드 미기동 또는 브릿지 미연결"
                : "카드에 로봇을 선택하세요"}
          </span>
          {snapshot?.stale && <Pill tone="warn">수신 끊김</Pill>}
          {snapshot?.error_code && (
            <span className="font-mono text-xs text-rose-600">
              {snapshot.error_code}
            </span>
          )}

          <span className="flex-1" />

          <Pill>
            🔋{" "}
            {snapshot?.battery_percent != null
              ? `${snapshot.battery_percent}%`
              : "—"}
          </Pill>
          <Pill>도킹 {snapshot?.is_docked ? "○" : "✕"}</Pill>
          {robot?.domain_id != null && <Pill>d{robot.domain_id}</Pill>}
        </div>
      </div>

      {open &&
        (received ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 상태 다이어그램은 뺐다 — 카드가 세로 컬럼이라 폭이 좁고, 8상태 전이도는
                컬럼 안에서 뭉개진다. 현재 상태는 헤더 배지로 충분하고, 지금 무슨 일이
                일어나는지는 아래 BT 트리가 더 정확히 보여준다. */}
            <div className="border-b p-3">
              <h3 className="mb-2 text-xs font-bold text-slate-700">
                BT 트리{" "}
                <span className="font-normal text-muted-foreground">
                  실행 중 강조
                </span>
              </h3>
              <BtTreeView tree={snapshot?.tree ?? null} />
            </div>

            {/* ── 전이 제어 ── */}
            <div className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2.5">
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger className="h-8 min-w-0 flex-1">
                  <SelectValue placeholder="목표 상태 선택" />
                </SelectTrigger>
                <SelectContent>
                  {model.states.map((state) => {
                    const reachable = allowed.includes(state);
                    return (
                      <SelectItem
                        key={state}
                        value={state}
                        disabled={!force && !reachable}
                      >
                        {state}
                        {!reachable && (
                          <span className="ml-2 text-xs opacity-60">
                            (도달 불가)
                          </span>
                        )}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>

              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Switch checked={force} onCheckedChange={setForce} />
                force
              </label>

              <Button
                size="sm"
                onClick={handleExecute}
                disabled={!target || transition.isPending}
              >
                전이 실행
              </Button>

              {force && (
                <Pill tone="warn">
                  ⚠ force — 전이 박스에 없는 간선도 허용됩니다
                </Pill>
              )}
            </div>

            {/* ── 전이 이력: 카드 하단 남는 공간을 채우는 스크롤 로그 ── */}
            <div className="flex min-h-0 flex-1 flex-col px-3 py-2.5">
              <h3 className="mb-1.5 shrink-0 text-xs font-bold text-slate-700">
                전이 이력
              </h3>
              <div
                ref={historyRef}
                className="min-h-0 flex-1 space-y-1 overflow-y-auto text-sm text-muted-foreground"
              >
                {(historyQuery.data?.items ?? []).length === 0 ? (
                  <p className="text-xs">전이 이력 없음</p>
                ) : (
                  (historyQuery.data?.items ?? []).map((item) => (
                    <div
                      key={item.id}
                      className={cn(
                        "flex items-center gap-1.5 rounded px-1.5 py-1",
                        !item.accepted && "opacity-50",
                      )}
                    >
                      <b className="text-slate-700">
                        {item.from_state} → {item.to_state}
                      </b>
                      <span className="text-xs">
                        {new Date(item.created_at).toLocaleTimeString()}
                      </span>
                      {item.forced && (
                        <span className="text-xs font-semibold text-rose-600">
                          [force]
                        </span>
                      )}
                      {!item.accepted && (
                        <span className="text-xs font-semibold text-amber-600">
                          [거부]
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : (
          <p className="flex flex-1 items-center justify-center py-6 text-center text-xs text-muted-foreground">
            {robotName
              ? (stateQuery.data?.reason ?? "상태를 아직 수신하지 못했습니다.")
              : "이 카드에서 볼 로봇을 선택하세요."}
          </p>
        ))}
    </section>
  );
}
