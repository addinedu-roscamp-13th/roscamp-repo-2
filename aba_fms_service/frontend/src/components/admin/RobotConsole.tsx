import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Battery, Crosshair, Home, Loader2, MapPinned, Navigation, Play, RefreshCw, Route as RouteIcon, Save, Square, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminApi, normalizeNav2State, type LocationActionItem, type Nav2Grid, type Nav2Locations, type Nav2Pose, type Nav2State } from "@/lib/admin-api";
import { ACTION_LABELS, buildParams, runAction, ROBOT_EMOTIONS, LCD_FONTS, type RobotActionType } from "@/lib/robot-actions";
import { cn } from "@/lib/utils";

type Viewport = { scale: number; ox: number; oy: number; width: number; height: number };
type GoalDraft = { x: number; y: number; yaw: number };
type DragState = { startWorld: { x: number; y: number }; currentCanvas: { x: number; y: number }; mode: "goal" | "zone"; zoneName?: string };

type CanvasFrame = {
  state?: Nav2State;
  locations?: Nav2Locations;
  showGlobal: boolean;
  showLocal: boolean;
  goal: GoalDraft | null;
  drag: DragState | null;
};

export interface RobotConsoleProps {
  robotId: number | null;
  robotBase: string;
  robotName?: string;
  canControl: boolean;
  variant?: "full" | "compact";
}

const NAV_PORT_KEY = "labi.navControlPort";
const DEFAULT_NAV_PORT = 9001;
const ZONE_NAMES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"];
const ARRIVE_DIST = 0.3; // m — 목표 좌표에 이만큼 접근하면 "도착"으로 판정
const ARRIVE_TIMEOUT = 90_000; // ms — 도착 감지 최대 대기 시간

type PendingArrival = { name: string; action: LocationActionItem; target: { x: number; y: number }; startedAt: number };

function worldToCanvas(map: Nav2Grid, vp: Viewport, wx: number, wy: number) {
  const mx = (wx - map.origin.x) / map.resolution;
  const my = (wy - map.origin.y) / map.resolution;
  return [vp.ox + mx * vp.scale, vp.oy + (map.height - 1 - my) * vp.scale] as const;
}

function canvasToWorld(map: Nav2Grid, vp: Viewport, cx: number, cy: number) {
  const ix = (cx - vp.ox) / vp.scale;
  const iy = map.height - 1 - (cy - vp.oy) / vp.scale;
  if (ix < 0 || iy < 0 || ix > map.width || iy > map.height) return null;
  return { x: map.origin.x + ix * map.resolution, y: map.origin.y + iy * map.resolution };
}

function drawRobot(ctx: CanvasRenderingContext2D, x: number, y: number, yaw: number, fill = "#f97316") {
  ctx.fillStyle = fill;
  ctx.beginPath();
  ctx.arc(x, y, 6, 0, Math.PI * 2);
  ctx.fill();
  const len = 22;
  ctx.strokeStyle = "#fde68a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + len * Math.cos(-yaw), y + len * Math.sin(-yaw));
  ctx.stroke();
}

function drawCostmap(ctx: CanvasRenderingContext2D, map: Nav2Grid, vp: Viewport, costmap: Nav2Grid, color: string) {
  const cell = Math.max(1, vp.scale);
  const cos = Math.cos(costmap.origin.yaw || 0);
  const sin = Math.sin(costmap.origin.yaw || 0);
  ctx.save();
  ctx.fillStyle = color;
  for (let iy = 0; iy < costmap.height; iy++) {
    for (let ix = 0; ix < costmap.width; ix++) {
      const cost = costmap.data[iy * costmap.width + ix];
      if (cost <= 0) continue;
      const lx = (ix + 0.5) * costmap.resolution;
      const ly = (iy + 0.5) * costmap.resolution;
      const wx = costmap.origin.x + cos * lx - sin * ly;
      const wy = costmap.origin.y + sin * lx + cos * ly;
      const [x, y] = worldToCanvas(map, vp, wx, wy);
      ctx.fillRect(x, y, cell, cell);
    }
  }
  ctx.restore();
}

function drawCanvas(canvas: HTMLCanvasElement, frame: CanvasFrame): Viewport | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#020617";
  ctx.fillRect(0, 0, rect.width, rect.height);

  const map = frame.state?.map;
  if (!map) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "14px system-ui";
    ctx.fillText("/api/state 맵 데이터 대기 중", 24, 34);
    return null;
  }

  const scale = Math.min(rect.width / map.width, rect.height / map.height);
  const vp = { scale, ox: (rect.width - map.width * scale) / 2, oy: (rect.height - map.height * scale) / 2, width: map.width * scale, height: map.height * scale };

  for (let iy = 0; iy < map.height; iy++) {
    for (let ix = 0; ix < map.width; ix++) {
      const v = map.data[iy * map.width + ix];
      ctx.fillStyle = v < 0 ? "#4b5563" : v === 0 ? "#ffffff" : v >= 100 ? "#000000" : "#d1d5db";
      const x = vp.ox + ix * vp.scale;
      const y = vp.oy + (map.height - 1 - iy) * vp.scale;
      ctx.fillRect(x, y, Math.max(1, vp.scale), Math.max(1, vp.scale));
    }
  }

  if (frame.showGlobal && frame.state?.global_costmap) drawCostmap(ctx, map, vp, frame.state.global_costmap, "rgba(56,189,248,0.48)");
  if (frame.showLocal && frame.state?.local_costmap) drawCostmap(ctx, map, vp, frame.state.local_costmap, "rgba(124,58,237,0.68)");

  const path = frame.state?.path ?? [];
  if (path.length > 0) {
    ctx.strokeStyle = "#22c55e";
    ctx.lineWidth = 2;
    ctx.beginPath();
    path.forEach((p, i) => {
      const [x, y] = worldToCanvas(map, vp, p.x, p.y);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  Object.entries(frame.locations ?? {}).forEach(([name, loc]) => {
    const [x, y] = worldToCanvas(map, vp, loc.x, loc.y);
    ctx.fillStyle = name === "HOME" ? "#2dd4bf" : "#ff9ad4";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    // 라벨: 흰 지도/어두운 미지영역 어디서나 보이도록 흰 테두리(할로) + 진한 글씨
    ctx.font = "bold 13px system-ui";
    ctx.lineJoin = "round";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.strokeText(name, x + 8, y - 8);
    ctx.fillStyle = "#0f172a";
    ctx.fillText(name, x + 8, y - 8);
  });

  if (frame.goal) {
    const [x, y] = worldToCanvas(map, vp, frame.goal.x, frame.goal.y);
    ctx.strokeStyle = "#fb7185";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 12, 0, Math.PI * 2);
    ctx.stroke();
    drawRobot(ctx, x, y, frame.goal.yaw, "#fb7185");
  }

  if (frame.drag) {
    const [sx, sy] = worldToCanvas(map, vp, frame.drag.startWorld.x, frame.drag.startWorld.y);
    const ex = frame.drag.currentCanvas.x;
    const ey = frame.drag.currentCanvas.y;
    ctx.strokeStyle = frame.drag.mode === "zone" ? "#38bdf8" : "#fb7185";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ex, ey, 4, 0, Math.PI * 2);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fill();
  }

  if (frame.state?.pose) {
    const [x, y] = worldToCanvas(map, vp, frame.state.pose.x, frame.state.pose.y);
    drawRobot(ctx, x, y, frame.state.pose.yaw);
  }

  ctx.strokeStyle = "rgba(255,255,255,0.1)";
  ctx.strokeRect(vp.ox, vp.oy, vp.width, vp.height);
  return vp;
}

function poseLine(pose?: Nav2Pose | null) {
  return pose ? `x=${pose.x.toFixed(2)}, y=${pose.y.toFixed(2)}, yaw=${pose.yaw.toFixed(2)}` : "-";
}

function statusLabel(status?: string, current?: string | null, minutes?: number) {
  const label = { idle: "대기", running: "주행중", done: "완료", failed: "실패", stopped: "정지" }[status ?? ""] ?? (status || "offline");
  return `${current ? `${label} -> ${current}` : label}${minutes ? ` · ${minutes}분` : ""}`;
}

// 단일 주행로봇 관제 콘솔. robotId/robotBase 를 prop 으로 받아 여러 로봇에 재사용한다.
//  - variant="full": /admin/control 의 전체 콘솔(모든 패널).
//  - variant="compact": /admin/fleet 그리드 셀용 축약 콘솔(지도+상태+구역이동+운행제어).
export function RobotConsole({ robotId, robotBase, robotName, canControl, variant = "full" }: RobotConsoleProps) {
  const isFull = variant === "full";
  const queryClient = useQueryClient();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<Viewport | null>(null);
  const [navPort, setNavPort] = useState(() => Number(localStorage.getItem(NAV_PORT_KEY) || DEFAULT_NAV_PORT));
  const [showGlobal, setShowGlobal] = useState(true);
  const [showLocal, setShowLocal] = useState(true);
  const [goal, setGoal] = useState<GoalDraft | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [sendOnDrag, setSendOnDrag] = useState(true);
  const [zonePick, setZonePick] = useState<string | null>(null);
  const [mapName, setMapName] = useState("pinky_map");
  const [, setLocName] = useState("A");
  const [selected, setSelected] = useState<string[]>([]);
  const [loop, setLoop] = useState(false);
  const [minutes, setMinutes] = useState(10);
  const [message, setMessage] = useState("");

  useEffect(() => localStorage.setItem(NAV_PORT_KEY, String(navPort || DEFAULT_NAV_PORT)), [navPort]);

  const [wsState, setWsState] = useState<Nav2State | null>(null);
  const [wsStateError, setWsStateError] = useState<string | null>(null);
  const stateQuery = useQuery({
    queryKey: ["control", "state", robotId, navPort],
    queryFn: () => adminApi.controlState(robotId!, navPort),
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
  const currentState = wsState ?? stateQuery.data ?? null;

  useEffect(() => {
    if (!canControl || !robotId) {
      setWsState(null);
      return;
    }
    const ws = new WebSocket(adminApi.controlStateWsUrl(robotId, navPort));
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) {
          setWsState(normalizeNav2State(payload.state as Nav2State));
          setWsStateError(null);
        } else if (payload?.error) {
          setWsStateError(String(payload.error));
        }
      } catch {
        setWsStateError("상태 스트림 해석 실패");
      }
    };
    ws.onerror = () => setWsStateError("상태 스트림 연결 오류");
    ws.onclose = () => setWsStateError((prev) => prev ?? "상태 스트림 연결 종료");
    return () => ws.close();
  }, [canControl, robotId, navPort]);
  const locationsQuery = useQuery({
    queryKey: ["control", "locations", robotId, navPort],
    queryFn: () => adminApi.controlLocations(robotId!, navPort),
    enabled: canControl,
    staleTime: Infinity,
    retry: false,
  });
  const locationActionsQuery = useQuery({
    queryKey: ["control", "location-actions", robotId],
    queryFn: () => adminApi.listLocationActions(robotId!),
    enabled: canControl,
    retry: false,
  });

  const locations = locationsQuery.data ?? {};
  const names = useMemo(() => Object.keys(locations).sort(), [locations]);
  const missionNames = (selected.length ? selected : names).filter((name) => locations[name]);
  // 운행 제어: 현재 미션 상태로 어떤 버튼이 활성인지 표시
  const _mission = currentState?.mission;
  const _mRunning = _mission?.status === "running";
  const patrolActive = _mRunning && _mission?.loop === true;
  const onewayActive = _mRunning && _mission?.loop === false;
  const stoppedActive = _mission != null && !_mRunning;
  const activeRing = "ring-2 ring-emerald-400 ring-offset-2 ring-offset-slate-900";
  const actionByName = useMemo(
    () => Object.fromEntries((locationActionsQuery.data ?? []).map((a) => [a.name, a])) as Record<string, LocationActionItem>,
    [locationActionsQuery.data],
  );

  // 구역 도착 액션: "이동+액션" → controlGoto 후 pose 가 목표에 접근하면 액션 실행.
  const [pending, setPending] = useState<PendingArrival | null>(null);
  const firedRef = useRef(false);

  const moveAndAction = useCallback((name: string) => {
    const loc = locations[name];
    const action = actionByName[name];
    if (!loc) { setMessage(`'${name}' 구역이 등록되어 있지 않습니다.`); return; }
    if (!action || !action.enabled || action.action_type === "none") {
      setMessage(`'${name}' 구역에 사용 설정된 액션이 없습니다. 먼저 액션을 저장하세요.`);
      return;
    }
    firedRef.current = false;
    run.mutate(() => adminApi.controlGoto(robotId!, name, navPort));
    setPending({ name, action, target: { x: loc.x, y: loc.y }, startedAt: Date.now() });
    setMessage(`'${name}' 구역으로 이동 중… 도착하면 '${ACTION_LABELS[action.action_type]}' 액션을 실행합니다.`);
  }, [locations, actionByName, robotId, navPort]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pending) return;
    const pose = currentState?.pose;
    if (pose && !firedRef.current) {
      const d = Math.hypot(pose.x - pending.target.x, pose.y - pending.target.y);
      if (d <= ARRIVE_DIST) {
        firedRef.current = true;
        setPending(null);
        runAction(robotBase, pending.action)
          .then(() => setMessage(`'${pending.name}' 도착 · '${ACTION_LABELS[pending.action.action_type]}' 실행 완료`))
          .catch((e) => setMessage(e instanceof Error ? `액션 실행 실패: ${e.message}` : "액션 실행 실패"));
        return;
      }
    }
    if (Date.now() - pending.startedAt > ARRIVE_TIMEOUT) {
      setPending(null);
      setMessage(`'${pending.name}' 도착 감지 실패(타임아웃). '지금 실행'으로 수동 실행할 수 있습니다.`);
    }
    // currentState 전체에 의존해 매 폴링마다 재평가한다(pose 가 null 로 고정되면 타임아웃 체크 멈춤 방지).
  }, [currentState, pending, robotBase]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    viewportRef.current = drawCanvas(canvas, { state: currentState, locations, showGlobal, showLocal, goal, drag });
  }, [currentState, locations, showGlobal, showLocal, goal, drag]);

  useEffect(() => { redraw(); }, [redraw]);
  useEffect(() => {
    window.addEventListener("resize", redraw);
    return () => window.removeEventListener("resize", redraw);
  }, [redraw]);

  const run = useMutation({
    mutationFn: (fn: () => Promise<{ success?: boolean; msg?: string; name?: string }>) => fn(),
    onSuccess: (res) => {
      setMessage(res.success === false ? (res.msg ?? "요청 실패") : "명령을 전송했습니다.");
      void queryClient.invalidateQueries({ queryKey: ["control"] });
    },
    onError: (err) => setMessage(err instanceof Error ? err.message : "요청 실패"),
  });

  // 구역 이동 버튼: 사용 설정된 도착 액션이 있으면 이동+액션, 없으면 단순 이동.
  const gotoZone = (name: string) => {
    const a = actionByName[name];
    if (a?.enabled && a.action_type !== "none") moveAndAction(name);
    else run.mutate(() => adminApi.controlGoto(robotId!, name, navPort));
  };

  const mousePos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const map = currentState?.map;
    const vp = viewportRef.current;
    if (!map || !vp) return;
    const p = mousePos(e);
    const w = canvasToWorld(map, vp, p.x, p.y);
    if (!w) return;
    setDrag({ startWorld: w, currentCanvas: p, mode: zonePick ? "zone" : "goal", zoneName: zonePick ?? undefined });
  };
  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!drag) return;
    setDrag({ ...drag, currentCanvas: mousePos(e) });
  };
  const onMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const map = currentState?.map;
    const vp = viewportRef.current;
    if (!map || !vp || !drag) return;
    const end = canvasToWorld(map, vp, mousePos(e).x, mousePos(e).y);
    if (!end) { setDrag(null); return; }
    const yaw = Math.atan2(end.y - drag.startWorld.y, end.x - drag.startWorld.x);
    const nextGoal = { x: drag.startWorld.x, y: drag.startWorld.y, yaw };
    if (drag.mode === "zone" && drag.zoneName) {
      run.mutate(() => adminApi.controlSetLocation(robotId!, { name: drag.zoneName!, ...nextGoal }, navPort));
      setZonePick(null);
    } else {
      setGoal(nextGoal);
      if (sendOnDrag) run.mutate(() => adminApi.controlGoal(robotId!, nextGoal, navPort));
      else setMessage("목표가 선택되었습니다. 목표 전송 버튼을 누르면 주행합니다.");
    }
    setDrag(null);
  };

  const toggleSelect = (name: string) => setSelected((prev) => prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name]);
  const batteryPct = currentState?.battery.percent;
  const map = currentState?.map;
  const localCostmap = currentState?.local_costmap;
  const globalCostmap = currentState?.global_costmap;
  const pathCount = currentState?.path?.length ?? 0;
  const activeGoalText = goal ? `x=${goal.x.toFixed(2)}, y=${goal.y.toFixed(2)}, yaw=${goal.yaw.toFixed(2)}` : "-";

  return (
    <div className={isFull ? "-m-4 min-h-[calc(100vh-60px)] bg-[radial-gradient(circle_at_top_left,#201537,#020617_55%,#000_100%)] p-4 text-slate-100 md:-m-6 md:p-6" : "text-slate-100"}>
      <div className={isFull ? "grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]" : "flex flex-col gap-3"}>
        <section className={cn("flex flex-col items-center justify-start self-start rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_top_left,rgba(255,155,210,0.12),transparent_55%),radial-gradient(circle_at_bottom_right,rgba(104,166,255,0.12),transparent_55%),#0b1020] shadow-2xl", isFull ? "min-h-[680px] p-4" : "p-3")}>
          <div className={cn("mb-3 flex w-full items-center justify-between gap-3", isFull && "max-w-[900px]")}>
            <div className="truncate text-[13px] font-semibold uppercase tracking-wider text-slate-400">{robotName ?? "Pinky"} <span className="bg-gradient-to-r from-pink-400 to-blue-300 bg-clip-text text-transparent">Navigation</span></div>
            <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-slate-950/70 px-3 py-1 text-[11px] uppercase tracking-widest text-slate-400"><span className={cn("h-2 w-2 rounded-full", currentState ? "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.9)]" : "bg-rose-400")} />LIVE</div>
          </div>
          <canvas ref={canvasRef} onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} className={cn("aspect-square w-full cursor-crosshair rounded-xl border border-white/10 bg-black", isFull && "max-w-[900px]")} />
          {isFull && <div className="mt-3 text-center text-[12px] text-slate-400">지도에서 클릭 후 드래그하면 위치와 방향이 지정됩니다. 즉시 전송 모드를 끄면 목표만 선택됩니다.</div>}
        </section>

        <aside className="flex flex-col gap-3">
          <Panel>
            {isFull && (
              <div className="mb-3 flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-pink-400 to-blue-500"><MapPinned className="h-5 w-5 text-slate-950" /></div>
                <div><div className="text-[14px] font-semibold">PinkLab · Pinky Pro</div><div className="text-[11px] text-slate-400">Web Navigation & SLAM Console</div></div>
              </div>
            )}
            <Info label="Robot pose" value={poseLine(currentState?.pose)} />
            <Info label="Battery" value={batteryPct != null ? `${batteryPct.toFixed(0)}% (${currentState?.battery.voltage?.toFixed(2) ?? "-"}V)` : "-"} />
            <BatteryBar value={batteryPct} />
            <Info label="상태" value={statusLabel(currentState?.mission.status, currentState?.mission.current, currentState?.mission.schedule_minutes)} />
            {isFull && <Info label="Goal" value={activeGoalText} />}
            {isFull && <Info label="Target" value={`${robotBase} · Nav2 ${navPort}`} />}
            {isFull && <Info label="Map" value={map ? `${map.width}x${map.height} · ${map.resolution}m` : "-"} />}
            {isFull && <Info label="Costmap" value={`G:${globalCostmap ? "ON" : "-"} / L:${localCostmap ? "ON" : "-"} / path:${pathCount}`} />}
            <div className="mt-3 grid grid-cols-[1fr_auto_auto] gap-2">
              {isFull
                ? <Input className="h-8 rounded-full border-slate-600 bg-slate-950/70 text-slate-100" type="number" value={navPort} onChange={(e) => setNavPort(Number(e.target.value) || DEFAULT_NAV_PORT)} />
                : <div className="min-w-0 truncate self-center text-[11px] text-slate-500">{robotBase}</div>}
              <Button size="sm" variant="outline" className="rounded-full border-slate-600 bg-slate-950/70 text-slate-100" onClick={() => void stateQuery.refetch().then((r) => r.data && setWsState(r.data))}>{stateQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}</Button>
              <DarkButton disabled={!canControl || !goal} title="선택 목표로 이동" onClick={() => goal && run.mutate(() => adminApi.controlGoal(robotId!, goal, navPort))}><Navigation className="h-4 w-4" /></DarkButton>
            </div>
            {(stateQuery.error || wsStateError) && <div className="mt-2 text-[11px] text-rose-300">{stateQuery.error?.message ?? wsStateError}</div>}
          </Panel>

          {isFull && (
            <Panel title="Layers">
              <ToggleRow label="Global costmap" color="bg-cyan-400" checked={showGlobal} onChange={setShowGlobal} />
              <ToggleRow label="Local costmap" color="bg-violet-600" checked={showLocal} onChange={setShowLocal} />
              <ToggleRow label="드래그 후 즉시 Goal 전송" color="bg-pink-400" checked={sendOnDrag} onChange={setSendOnDrag} />
            </Panel>
          )}

          {isFull && (
            <Panel title="SLAM Mapping">
              <Input className="mb-2 h-8 rounded-full border-slate-600 bg-slate-950/70 text-slate-100" value={mapName} onChange={(e) => setMapName(e.target.value)} />
              <div className="grid grid-cols-2 gap-2"><DarkButton onClick={() => run.mutate(() => adminApi.controlSlamReset(robotId!, navPort))} disabled={!canControl}>Reset Map</DarkButton><PrimaryButton onClick={() => run.mutate(() => adminApi.controlSaveMap(robotId!, mapName, navPort))} disabled={!canControl}><Save className="h-4 w-4" />Save Map</PrimaryButton></div>
            </Panel>
          )}

          <Panel title="Zones · 구역 이동">
            <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>{isFull ? "▶ 이동 · 지도 지정 · 현재 저장 · 삭제" : "▶ 이동(액션 설정 시 도착 실행)"}</span>
              <span>{names.length}/{ZONE_NAMES.length} 등록</span>
            </div>
            <div className={cn("space-y-1 overflow-auto pr-1", isFull ? "max-h-[300px]" : "max-h-[220px]")}>
              {ZONE_NAMES.map((name) => {
                const loc = locations[name];
                const has = loc != null;
                return (
                  <div key={name} className={cn("grid items-center gap-2 rounded-lg py-1 hover:bg-white/5", isFull ? "grid-cols-[auto_28px_minmax(0,1fr)_auto_auto_auto_auto]" : "grid-cols-[auto_28px_minmax(0,1fr)_auto]")}>
                    <Checkbox checked={selected.includes(name)} disabled={!has} onCheckedChange={() => toggleSelect(name)} />
                    <span className={cn("flex h-7 w-7 items-center justify-center rounded-lg text-[12px] font-bold", has ? "bg-gradient-to-br from-pink-400 to-blue-400 text-slate-950" : "bg-slate-700/70 text-slate-400")}>{name}</span>
                    <button className="min-w-0 text-left disabled:cursor-default" disabled={!has} onClick={() => has && setGoal(loc)}>
                      <div className={cn("truncate text-[12px]", has ? "text-slate-300" : "text-slate-500")}>{has ? `x=${loc.x.toFixed(2)}, y=${loc.y.toFixed(2)}` : "미등록"}</div>
                      <div className="truncate text-[10px] text-slate-500">
                        {has ? `yaw=${loc.yaw.toFixed(2)}` : "지도 지정 또는 현재 저장"}
                        {actionByName[name]?.enabled && actionByName[name]?.action_type !== "none" ? <span className="text-pink-300"> · ⚡{ACTION_LABELS[actionByName[name].action_type]}</span> : null}
                      </div>
                    </button>
                    <PrimaryButton className="h-7 px-2" disabled={!canControl || !has || pending?.name === name} title="이 구역으로 이동" onClick={() => (isFull ? run.mutate(() => adminApi.controlGoto(robotId!, name, navPort)) : gotoZone(name))}>{pending?.name === name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "▶"}</PrimaryButton>
                    {isFull && <DarkButton className="h-7 px-2" disabled={!canControl} title="지도에서 클릭해 위치 지정" onClick={() => { setLocName(name); setZonePick(name); setMessage(`지도에서 '${name}' 위치를 클릭 후 드래그하세요.`); }}><Crosshair className="h-3.5 w-3.5" /></DarkButton>}
                    {isFull && <DarkButton className="h-7 px-2" disabled={!canControl} title="로봇 현재 위치 저장" onClick={() => run.mutate(() => adminApi.controlSetLocation(robotId!, { name }, navPort))}><Save className="h-3.5 w-3.5" /></DarkButton>}
                    {isFull && <DarkButton className="h-7 px-2" disabled={!canControl || !has} title="구역 삭제" onClick={() => run.mutate(() => adminApi.controlDeleteLocation(robotId!, name, navPort))}><Trash2 className="h-3.5 w-3.5" /></DarkButton>}
                  </div>
                );
              })}
            </div>
            {isFull && <div className="mt-2 text-[11px] text-slate-500">미등록 구역도 원본 Flask 화면처럼 고정 표시됩니다. 체크한 등록 구역만 순찰 대상이 됩니다.</div>}
          </Panel>

          <Panel title="운행 제어">
            <div className="mb-2 flex items-center gap-2"><Checkbox checked={loop} onCheckedChange={(v) => setLoop(Boolean(v))} /><Label className="text-[12px] text-slate-300">반복 순찰</Label></div>
            <div className="grid grid-cols-2 gap-2"><PrimaryButton className={cn(patrolActive && activeRing)} disabled={!canControl || missionNames.length === 0} onClick={() => run.mutate(() => adminApi.controlMissionStart(robotId!, { names: missionNames, loop: true }, navPort))}><RouteIcon className="h-4 w-4" />순찰{patrolActive && " ●"}</PrimaryButton><DarkButton className={cn(onewayActive && [activeRing, "bg-slate-700 text-white"])} disabled={!canControl || missionNames.length === 0} onClick={() => run.mutate(() => adminApi.controlMissionStart(robotId!, { names: missionNames, loop: false }, navPort))}>한바퀴{onewayActive && " ●"}</DarkButton><DarkButton disabled={!canControl} onClick={() => run.mutate(() => adminApi.controlHome(robotId!, navPort))}><Home className="h-4 w-4" />홈</DarkButton><DarkButton className={cn(stoppedActive && [activeRing, "bg-slate-700 text-white"])} disabled={!canControl} onClick={() => run.mutate(() => adminApi.controlMissionStop(robotId!, navPort))}><Square className="h-4 w-4" />정지{stoppedActive && " ●"}</DarkButton></div>
            <div className="mt-2 text-center text-[11px] text-slate-400">현재: <span className="font-semibold text-emerald-300">{patrolActive ? "반복 순찰 중" : onewayActive ? "한바퀴 주행 중" : _mission?.status === "done" ? "완료" : _mission?.status === "failed" ? "실패" : "정지/대기"}</span>{_mission?.current ? <> · 목표 <span className="text-slate-200">{_mission.current}</span></> : null}</div>
            {isFull && <div className="mt-2 grid grid-cols-[70px_1fr_auto] gap-2"><Input className="h-8 rounded-full border-slate-600 bg-slate-950/70 text-slate-100" type="number" value={minutes} onChange={(e) => setMinutes(Number(e.target.value) || 1)} /><DarkButton disabled={!canControl || missionNames.length === 0} onClick={() => run.mutate(() => adminApi.controlScheduleStart(robotId!, { minutes, names: missionNames, loop }, navPort))}><Play className="h-4 w-4" />스케줄</DarkButton><DarkButton disabled={!canControl} onClick={() => run.mutate(() => adminApi.controlScheduleStop(robotId!, navPort))}>해제</DarkButton></div>}
            {message && <div className="mt-2 text-[11px] text-slate-400">{message}</div>}
          </Panel>

          {isFull && (
            <ZoneActionPanel
              canControl={canControl}
              robotId={robotId}
              robotBase={robotBase}
              actions={locationActionsQuery.data ?? []}
              registered={names}
              busyName={pending?.name ?? null}
              onSaved={() => void queryClient.invalidateQueries({ queryKey: ["control", "location-actions"] })}
              onMoveAction={moveAndAction}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function Panel({ title, children }: { title?: string; children: React.ReactNode }) {
  return <div className="rounded-2xl border border-white/10 bg-slate-900/85 p-3.5 shadow-xl">{title && <div className="mb-2 text-[12px] font-semibold uppercase tracking-widest text-slate-400">{title}</div>}{children}</div>;
}
function Info({ label, value }: { label: string; value: string }) {
  return <div className="mb-1 flex justify-between gap-3 text-[12px]"><span className="text-slate-400">{label}</span><span className="truncate text-right font-medium text-slate-100">{value}</span></div>;
}
function BatteryBar({ value }: { value?: number | null }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  return <div className="mb-2 mt-1 flex items-center gap-2"><Battery className="h-3.5 w-3.5 text-slate-400" /><div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-950"><div className={cn("h-full rounded-full", pct > 30 ? "bg-emerald-400" : "bg-rose-400")} style={{ width: `${pct}%` }} /></div><span className="w-9 text-right text-[11px] text-slate-400">{value != null ? `${pct.toFixed(0)}%` : "-"}</span></div>;
}
function ToggleRow({ label, color, checked, onChange }: { label: string; color: string; checked: boolean; onChange: (v: boolean) => void }) {
  return <label className="flex items-center justify-between py-1 text-[13px]"><span className="flex items-center gap-2"><span className={cn("h-2.5 w-2.5 rounded-full", color)} />{label}</span><Checkbox checked={checked} onCheckedChange={(v) => onChange(Boolean(v))} /></label>;
}
function DarkButton({ className, ...props }: React.ComponentProps<typeof Button>) {
  return <Button size="sm" variant="outline" className={cn("rounded-full border-slate-600 bg-slate-950/70 text-slate-100 hover:bg-slate-800 hover:text-white", className)} {...props} />;
}
function PrimaryButton({ className, ...props }: React.ComponentProps<typeof Button>) {
  return <Button size="sm" className={cn("rounded-full bg-gradient-to-r from-pink-400 to-blue-400 text-slate-950 hover:brightness-110", className)} {...props} />;
}

const zInput = "mt-1 w-full rounded-lg border border-slate-600 bg-slate-950/70 px-2 py-1 text-[12px] text-slate-100";

// 구역(A~O) 도착 액션 설정 + "이동+액션" 실행 패널. 마커 액션과 동일한 액션 세트를 사용한다.
function ZoneActionPanel({ canControl, robotId, robotBase, actions, registered, busyName, onSaved, onMoveAction }: {
  canControl: boolean;
  robotId: number | null;
  robotBase: string;
  actions: LocationActionItem[];
  registered: string[];
  busyName: string | null;
  onSaved: () => void;
  onMoveAction: (name: string) => void;
}) {
  const [zone, setZone] = useState("A");
  const [type, setType] = useState<RobotActionType>("lcd_text");
  const [enabled, setEnabled] = useState(true);
  const [p, setP] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const ex = actions.find((a) => a.name === zone);
    if (ex) {
      setType(ex.action_type);
      setEnabled(ex.enabled);
      setP(Object.fromEntries(Object.entries(ex.params ?? {}).map(([k, v]) => [k, String(v)])));
    } else {
      setType("lcd_text");
      setEnabled(true);
      setP({});
    }
  }, [zone, actions]);

  const f = (k: string, d = "") => p[k] ?? d;
  const set = (k: string, v: string) => setP((prev) => ({ ...prev, [k]: v }));

  async function save() {
    if (robotId == null) return;
    setBusy(true); setMsg(null);
    try {
      await adminApi.upsertLocationAction(robotId, zone, { action_type: type, enabled, params: buildParams(type, p) });
      onSaved(); setMsg("저장됨");
    } catch (e) { setMsg(e instanceof Error ? e.message : "저장 실패 (백엔드 재시작 필요?)"); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (robotId == null) return;
    setBusy(true); setMsg(null);
    try { await adminApi.deleteLocationAction(robotId, zone); onSaved(); setMsg("삭제됨"); }
    catch (e) { setMsg(e instanceof Error ? e.message : "삭제 실패"); }
    finally { setBusy(false); }
  }
  async function runNow() {
    setMsg("액션 전송…");
    try { await runAction(robotBase, { action_type: type, params: buildParams(type, p) }); setMsg("액션 전송됨"); }
    catch (e) { setMsg(e instanceof Error ? `실패: ${e.message}` : "실패"); }
  }

  const configuredCount = actions.filter((a) => a.enabled && a.action_type !== "none").length;

  return (
    <Panel title="구역 도착 액션">
      <div className="mb-2 flex items-center justify-between text-[11px] text-slate-500">
        <span>도착 후 실행할 동작을 구역별로 저장</span>
        <span>{configuredCount}개 설정</span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11px] text-slate-400">구역
          <select value={zone} onChange={(e) => setZone(e.target.value)} className={zInput}>
            {ZONE_NAMES.map((z) => <option key={z} value={z}>{z}{registered.includes(z) ? "" : " (미등록)"}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-slate-400">액션
          <select value={type} onChange={(e) => setType(e.target.value as RobotActionType)} className={zInput}>
            {(Object.keys(ACTION_LABELS) as RobotActionType[]).map((t) => <option key={t} value={t}>{ACTION_LABELS[t]}</option>)}
          </select>
        </label>

        {type === "rotate" && (<>
          <label className="text-[11px] text-slate-400">각도 (°, +좌/-우)
            <input value={f("angle", "90")} onChange={(e) => set("angle", e.target.value)} className={zInput} /></label>
          <label className="text-[11px] text-slate-400">속도 (0~1)
            <input value={f("speed", "0.3")} onChange={(e) => set("speed", e.target.value)} className={zInput} /></label>
        </>)}
        {type === "move" && (<>
          <label className="text-[11px] text-slate-400">방향
            <select value={f("direction", "forward")} onChange={(e) => set("direction", e.target.value)} className={zInput}>
              {["forward", "backward", "left", "right"].map((d) => <option key={d} value={d}>{d}</option>)}
            </select></label>
          <label className="text-[11px] text-slate-400">거리 (m)
            <input value={f("distance", "0.3")} onChange={(e) => set("distance", e.target.value)} className={zInput} /></label>
        </>)}
        {type === "lcd_emotion" && (
          <label className="col-span-2 text-[11px] text-slate-400">표정
            <select value={f("emotion", "happy")} onChange={(e) => set("emotion", e.target.value)} className={zInput}>
              {ROBOT_EMOTIONS.map((em) => <option key={em} value={em}>{em}</option>)}
            </select></label>
        )}
        {type === "lcd_text" && (<>
          <label className="col-span-2 text-[11px] text-slate-400">텍스트
            <input value={f("text")} onChange={(e) => set("text", e.target.value)} placeholder="예: B구역 도착" className={zInput} /></label>
          <label className="text-[11px] text-slate-400">폰트
            <select value={f("font_name", "MaruBuri-Bold.ttf")} onChange={(e) => set("font_name", e.target.value)} className={zInput}>
              {LCD_FONTS.map((font) => <option key={font} value={font}>{font}</option>)}
            </select></label>
          <label className="text-[11px] text-slate-400">크기
            <input value={f("font_size", "24")} onChange={(e) => set("font_size", e.target.value.replace(/[^0-9]/g, ""))} className={zInput} /></label>
          <label className="text-[11px] text-slate-400">글자색
            <input type="color" value={f("color", "#ffffff")} onChange={(e) => set("color", e.target.value)} className="mt-1 h-8 w-full rounded-lg border border-slate-600 bg-slate-950/70 px-1" /></label>
          <label className="text-[11px] text-slate-400">배경색
            <input type="color" value={f("bg_color", "#000000")} onChange={(e) => set("bg_color", e.target.value)} className="mt-1 h-8 w-full rounded-lg border border-slate-600 bg-slate-950/70 px-1" /></label>
          <label className="text-[11px] text-slate-400">정렬
            <select value={f("align", "center")} onChange={(e) => set("align", e.target.value)} className={zInput}>
              {["left", "center", "right"].map((align) => <option key={align} value={align}>{align}</option>)}
            </select></label>
          <label className="text-[11px] text-slate-400">표시 시간 (초, 0=계속)
            <input value={f("duration", "0")} onChange={(e) => set("duration", e.target.value.replace(/[^0-9]/g, ""))} className={zInput} /></label>
        </>)}
        {type === "lcd_image" && (
          <label className="col-span-2 text-[11px] text-slate-400">이미지 파일명
            <input value={f("filename")} onChange={(e) => set("filename", e.target.value)} placeholder="rc_lcd_images 파일명" className={zInput} /></label>
        )}
        {type === "dock" && (
          <label className="col-span-2 text-[11px] text-slate-400">목표 크기 (정지 거리)
            <input value={f("target_size", "0.3")} onChange={(e) => set("target_size", e.target.value)} className={zInput} /></label>
        )}
      </div>

      <label className="mt-2 flex items-center gap-2 text-[12px] text-slate-300">
        <Checkbox checked={enabled} onCheckedChange={(v) => setEnabled(Boolean(v))} /> 사용 (도착 시 실행 대상)
      </label>

      <div className="mt-2 grid grid-cols-2 gap-2">
        <DarkButton onClick={save} disabled={busy || robotId == null}><Save className="h-4 w-4" />저장</DarkButton>
        <DarkButton onClick={runNow} disabled={!canControl}>지금 실행</DarkButton>
        <PrimaryButton onClick={() => onMoveAction(zone)} disabled={!canControl || !registered.includes(zone) || busyName != null}>
          {busyName === zone ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4" />}이동+액션
        </PrimaryButton>
        <DarkButton onClick={remove} disabled={busy || robotId == null} className="text-rose-300"><Trash2 className="h-4 w-4" />삭제</DarkButton>
      </div>
      {msg && <div className="mt-2 text-[11px] text-slate-400">{msg}</div>}
      {busyName === zone && <div className="mt-1 text-[11px] text-pink-300">이동 중 · 도착 시 자동 실행 대기…</div>}
    </Panel>
  );
}
