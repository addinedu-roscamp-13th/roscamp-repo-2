import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Loader2, Play, Shield, Square } from "lucide-react";

import { AdminShell } from "@/components/admin/AdminShell";
import { RobotConsole } from "@/components/admin/RobotConsole";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { adminApi, type FleetCoordinatorState, type Robot } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/fleet")({ component: FleetPage });

// 로봇 레지스트리(ip/port)로부터 로봇 에이전트 직접 호출용 base URL 을 만든다.
function robotBaseOf(r: Robot): string {
  return `http://${r.ip_address}:${r.port}`;
}

function FleetPage() {
  const [message, setMessage] = useState("");

  const robotsQuery = useQuery({
    queryKey: ["fleet", "pinky-robots"],
    queryFn: () => adminApi.listRobots({ robot_type: "pinky", limit: 50 }),
  });

  const robots = (robotsQuery.data?.items ?? [])
    .filter((r) => r.is_active)
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

  // 전체 정지: 등록된 모든 주행로봇에 미션 정지를 브로드캐스트(안전 방향이라 확인 없이 실행).
  const stopAll = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(robots.map((r) => adminApi.controlMissionStop(r.id)));
      return results.filter((x) => x.status === "rejected").length;
    },
    onSuccess: (failed) => setMessage(failed === 0 ? "모든 로봇에 정지 명령을 보냈습니다." : `정지 전송 완료 (실패 ${failed}대)`),
    onError: () => setMessage("전체 정지 전송 실패"),
  });

  return (
    <AdminShell title="멀티 관제">
      <div className="-m-4 min-h-[calc(100vh-60px)] bg-[radial-gradient(circle_at_top_left,#201537,#020617_55%,#000_100%)] p-4 text-slate-100 md:-m-6 md:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[15px] font-semibold">주행로봇 멀티 관제</div>
            <div className="text-[12px] text-slate-400">
              활성 주행로봇 {robots.length}대 · 각 콘솔은 독립된 지도/제어입니다.
            </div>
          </div>
          <div className="flex items-center gap-2">
            {message && <span className="text-[11px] text-slate-400">{message}</span>}
            <Button
              size="sm"
              variant="outline"
              className="rounded-full border-rose-500/60 bg-rose-500/10 text-rose-200 hover:bg-rose-500/20"
              disabled={robots.length === 0 || stopAll.isPending}
              onClick={() => stopAll.mutate()}
            >
              {stopAll.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />} 전체 정지
            </Button>
          </div>
        </div>

        <CoordinatorPanel />

        {robotsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-slate-400"><Loader2 className="h-4 w-4 animate-spin" /> 로봇 목록 불러오는 중…</div>
        ) : robots.length === 0 ? (
          <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-6 text-[13px] text-slate-400">
            활성 상태의 주행로봇(pinky)이 없습니다. <span className="text-slate-200">로봇 IP 관리</span> 화면에서 pinky 로봇을 등록·활성화하세요.
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {robots.map((r) => (
              <RobotConsole
                key={r.id}
                robotId={r.id}
                robotBase={robotBaseOf(r)}
                robotName={r.name}
                canControl={r.robot_type === "pinky" && r.is_active}
                variant="compact"
              />
            ))}
          </div>
        )}
      </div>
    </AdminShell>
  );
}

// 근접 안전 코디네이터 감시·설정 패널. 로직은 백엔드 백그라운드 서비스에 있고 여기선 감시/재개만.
function CoordinatorPanel() {
  const queryClient = useQueryClient();
  const [minDist, setMinDist] = useState("0.6");
  const [msg, setMsg] = useState("");

  const coordQuery = useQuery({
    queryKey: ["fleet", "coordinator"],
    queryFn: () => adminApi.getFleetCoordinator(),
    staleTime: Infinity,
  });
  const coord = coordQuery.data;

  useEffect(() => {
    const ws = new WebSocket(adminApi.fleetCoordinatorWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) queryClient.setQueryData(["fleet", "coordinator"], payload.state);
      } catch {
        /* malformed coordinator frame */
      }
    };
    return () => ws.close();
  }, [queryClient]);

  useEffect(() => {
    if (coord) setMinDist(String(coord.min_distance));
  }, [coord?.min_distance]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = useMutation({
    mutationFn: (body: { enabled?: boolean; min_distance?: number }) => adminApi.updateFleetCoordinator(body),
    onSuccess: (data: FleetCoordinatorState) => queryClient.setQueryData(["fleet", "coordinator"], data),
    onError: () => setMsg("설정 변경 실패"),
  });
  const resume = useMutation({
    mutationFn: (robotId: number) => adminApi.fleetResume(robotId),
    onSuccess: (r) => { setMsg(r.msg); void queryClient.invalidateQueries({ queryKey: ["fleet", "coordinator"] }); },
    onError: () => setMsg("재개 실패"),
  });

  const enabled = coord?.enabled ?? false;
  const holds = coord?.holds ?? {};
  const heldIds = Object.keys(holds);
  const robotsById = new Map((coord?.robots ?? []).map((r) => [r.id, r]));
  const stale = enabled && coord != null && coord.last_tick > 0 && Date.now() / 1000 - coord.last_tick > 5;

  return (
    <div className="mb-4 rounded-2xl border border-white/10 bg-slate-900/70 p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-2 text-[13px] font-semibold"><Shield className={cn("h-4 w-4", enabled ? "text-emerald-400" : "text-slate-500")} />근접 안전 조율</span>
          <button
            type="button"
            onClick={() => update.mutate({ enabled: !enabled })}
            className={cn("rounded-full px-3 py-1 text-[12px] font-semibold transition", enabled ? "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40" : "bg-slate-700/60 text-slate-300")}
          >
            {enabled ? "ON" : "OFF"}
          </button>
          <label className="flex items-center gap-1 text-[12px] text-slate-400">
            최소 거리
            <Input value={minDist} onChange={(e) => setMinDist(e.target.value)} className="h-7 w-16 rounded-full border-slate-600 bg-slate-950/70 text-center text-slate-100" />
            m
          </label>
          <Button size="sm" variant="outline" className="h-7 rounded-full border-slate-600 bg-slate-950/70 text-slate-100" disabled={update.isPending} onClick={() => update.mutate({ min_distance: Number(minDist) || 0.6 })}>적용</Button>
          {stale && <span className="text-[11px] text-amber-300">⚠ 코디네이터 응답 지연</span>}
          {msg && <span className="text-[11px] text-slate-400">{msg}</span>}
        </div>
        <div className="text-[11px] text-slate-500">거리는 <span className="text-slate-300">공통 맵 프레임</span> 기준입니다(같은 지도로 로컬라이즈 필요). 자동 정지만 수행 · 재개는 수동.</div>
      </div>

      {enabled && heldIds.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {heldIds.map((idStr) => {
            const id = Number(idStr);
            const h = holds[idStr];
            const rs = robotsById.get(id);
            const resumable = rs?.resumable ?? false;
            return (
              <div key={idStr} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5">
                <span className="text-[12px] text-rose-100">
                  ⚠ <b>{rs?.name ?? `#${id}`}</b> 근접 자동 정지 · {h.peer_name} 와 {h.distance}m
                  {resumable ? <span className="ml-2 text-emerald-300">해제 가능</span> : <span className="ml-2 text-amber-300">아직 근접</span>}
                </span>
                <Button size="sm" className="h-7 rounded-full bg-gradient-to-r from-pink-400 to-blue-400 text-slate-950" disabled={!resumable || resume.isPending} onClick={() => resume.mutate(id)}>
                  <Play className="h-3.5 w-3.5" />재개
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {enabled && coord && coord.robots.length > 1 && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
          {coord.robots.map((r) => (
            <span key={r.id} className={cn(r.held && "text-rose-300")}>
              {r.name}: {r.online ? (r.near_dist != null ? `최근접 ${r.near_dist.toFixed(2)}m` : "단독") : "오프라인"}{r.moving ? " · 주행" : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
