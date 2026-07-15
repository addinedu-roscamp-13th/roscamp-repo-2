import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Gamepad2,
  Square,
  Wifi,
  WifiOff,
} from "lucide-react";
import { AdminShell } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useQuery } from "@tanstack/react-query";
import { adminApi, getToken, type Robot } from "@/lib/admin-api";
import { buildRobotWsUrl, useActiveRobotBase, useActiveRobotId } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/joystick")({
  component: JoystickPage,
});

// ── 가상 조이스틱 훅 ──────────────────────────────────────────────────────────

function useJoystick(onVelocity: (lin: number, ang: number) => void) {
  const circleRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const center = useRef({ x: 0, y: 0 });
  const radius = useRef(0);
  const [pos, setPos] = useState({ x: 0, y: 0 });

  const move = useCallback(
    (clientX: number, clientY: number) => {
      const r = radius.current;
      const dx = clientX - center.current.x;
      const dy = clientY - center.current.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const scale = dist > r ? r / dist : 1;
      const nx = Math.max(-1, Math.min(1, (dx * scale) / r));
      const ny = Math.max(-1, Math.min(1, (dy * scale) / r));
      setPos({ x: nx, y: ny });
      onVelocity(-ny, -nx); // -y → linear, -x → angular
    },
    [onVelocity],
  );

  const start = useCallback(
    (clientX: number, clientY: number) => {
      if (!circleRef.current) return;
      const rect = circleRef.current.getBoundingClientRect();
      center.current = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      radius.current = rect.width / 2 - 16;
      dragging.current = true;
      move(clientX, clientY);
    },
    [move],
  );

  const stop = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setPos({ x: 0, y: 0 });
    onVelocity(0, 0);
  }, [onVelocity]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => { if (dragging.current) move(e.clientX, e.clientY); };
    const onMouseUp = () => stop();
    const onTouchMove = (e: TouchEvent) => {
      if (dragging.current) { e.preventDefault(); move(e.touches[0].clientX, e.touches[0].clientY); }
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("touchmove", onTouchMove, { passive: false });
    window.addEventListener("touchend", stop);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("touchend", stop);
    };
  }, [move, stop]);

  return { circleRef, pos, start };
}

// ── WebSocket 훅 ──────────────────────────────────────────────────────────────

function driveWsUrl(base: string, token: string): string {
  // buildRobotWsUrl 이 /api/robot/ → /api/admin/robot/ 재작성을 처리한다(로봇 실제 경로).
  return buildRobotWsUrl(base, "/api/robot/ws/drive") + "?token=" + encodeURIComponent(token);
}

function useDriveWS(robotBase: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastLeft, setLastLeft] = useState(0);
  const [lastRight, setLastRight] = useState(0);
  const lastSendRef = useRef(0);

  const connect = useCallback(() => {
    const token = getToken();
    if (!token || !robotBase) return;
    wsRef.current?.close();
    const ws = new WebSocket(driveWsUrl(robotBase, token));
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.type === "status") { setLastLeft(d.left ?? 0); setLastRight(d.right ?? 0); }
      } catch { /* ignore */ }
    };
    wsRef.current = ws;
  }, [robotBase]);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  const send = useCallback((linear: number, angular: number) => {
    const now = Date.now();
    if (now - lastSendRef.current < 80) return; // 최대 12.5 Hz
    lastSendRef.current = now;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ linear, angular }));
    }
  }, []);

  return { connected, lastLeft, lastRight, send, reconnect: connect };
}

// ── 메인 페이지 ──────────────────────────────────────────────────────────────

function JoystickPage() {
  const [curVel, setCurVel] = useState({ linear: 0, angular: 0 });
  const [speedScale, setSpeedScale] = useState(0.6);
  // 파킹 페이지와 동일하게 '선택된 로봇'의 실제 ip:port 로 연결한다.
  // (getRobotBase() 만 쓰면 미선택 시 중앙서버로 폴백돼 실제 모터가 없어 안 움직임)
  const robotId = useActiveRobotId();
  const fallbackBase = useActiveRobotBase();
  const robotsQuery = useQuery({
    queryKey: ["joystick", "robots", robotId],
    queryFn: () => adminApi.listRobots({ limit: 200 }),
    enabled: robotId != null,
    retry: false,
  });
  const robotRecord = (robotsQuery.data?.items ?? []).find(
    (r: Robot) => r.id === robotId && r.is_active,
  ) ?? null;
  const robotBase = robotRecord
    ? `http://${robotRecord.ip_address}:${robotRecord.port}`
    : fallbackBase;
  const { connected, lastLeft, lastRight, send, reconnect } = useDriveWS(robotBase);

  const onVelocity = useCallback(
    (lin: number, ang: number) => {
      const l = lin * speedScale;
      const a = ang * speedScale;
      setCurVel({ linear: l, angular: a });
      send(l, a);
    },
    [send, speedScale],
  );

  const { circleRef, pos, start } = useJoystick(onVelocity);
  const handleX = pos.x * 50;
  const handleY = pos.y * 50;

  // 키보드 제어
  useEffect(() => {
    const pressed = new Set<string>();
    const update = () => {
      let lin = 0, ang = 0;
      if (pressed.has("ArrowUp")    || pressed.has("w") || pressed.has("W")) lin =  speedScale;
      if (pressed.has("ArrowDown")  || pressed.has("s") || pressed.has("S")) lin = -speedScale;
      if (pressed.has("ArrowLeft")  || pressed.has("a") || pressed.has("A")) ang =  speedScale;
      if (pressed.has("ArrowRight") || pressed.has("d") || pressed.has("D")) ang = -speedScale;
      if (pressed.has(" ")) { lin = 0; ang = 0; }
      setCurVel({ linear: lin, angular: ang });
      send(lin, ang);
    };
    const onDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"," "].includes(e.key)) e.preventDefault();
      pressed.add(e.key); update();
    };
    const onUp = (e: KeyboardEvent) => { pressed.delete(e.key); update(); };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => { window.removeEventListener("keydown", onDown); window.removeEventListener("keyup", onUp); };
  }, [send, speedScale]);

  return (
    <AdminShell title="조이스틱 제어">
      <div className="space-y-5">
        {/* 헤더 */}
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Gamepad2 className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <h1 className="text-lg font-semibold text-slate-900">조이스틱 제어</h1>
              <p className="text-sm text-muted-foreground">마우스 드래그 또는 WASD / 방향키로 PinkyPro를 조종합니다.</p>
            </div>
            <div className="flex items-center gap-2">
              {connected ? (
                <Badge className="gap-1.5 rounded-md bg-emerald-600">
                  <Wifi className="h-3 w-3" /> 연결됨
                </Badge>
              ) : (
                <Badge variant="outline" className="gap-1.5 rounded-md text-slate-500">
                  <WifiOff className="h-3 w-3" /> 미연결
                </Badge>
              )}
              {!connected && (
                <Button size="sm" variant="outline" onClick={reconnect}>재연결</Button>
              )}
            </div>
          </div>
        </div>

        {!connected && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <AlertCircle className="size-4 shrink-0" />
            백엔드 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.
          </div>
        )}

        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {/* 가상 조이스틱 */}
          <Card className="flex flex-col items-center gap-4 p-6 shadow-sm">
            <p className="text-[13px] font-semibold text-slate-700">가상 조이스틱</p>
            <div
              ref={circleRef}
              className="relative flex h-48 w-48 cursor-grab items-center justify-center rounded-full border-2 border-border bg-slate-50 select-none active:cursor-grabbing"
              onMouseDown={(e) => start(e.clientX, e.clientY)}
              onTouchStart={(e) => { e.preventDefault(); start(e.touches[0].clientX, e.touches[0].clientY); }}
            >
              {/* 십자선 */}
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className="h-px w-full bg-slate-200" />
              </div>
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className="h-full w-px bg-slate-200" />
              </div>
              {/* 방향 힌트 */}
              <ChevronUp className="absolute top-3 size-4 text-slate-300" />
              <ChevronDown className="absolute bottom-3 size-4 text-slate-300" />
              <ChevronLeft className="absolute left-3 size-4 text-slate-300" />
              <ChevronRight className="absolute right-3 size-4 text-slate-300" />
              {/* 핸들 */}
              <div
                className={cn(
                  "absolute h-14 w-14 rounded-full border-2 shadow-lg",
                  pos.x === 0 && pos.y === 0
                    ? "border-slate-300 bg-white transition-transform duration-150 ease-out"
                    : "border-primary bg-primary",
                )}
                style={{ transform: `translate(${handleX}px, ${handleY}px)` }}
              />
            </div>

            {/* 속도 슬라이더 */}
            <div className="w-full space-y-1.5">
              <div className="flex justify-between text-[12px] text-slate-500">
                <span>속도 배율</span>
                <span className="font-mono font-semibold text-slate-700">{(speedScale * 100).toFixed(0)}%</span>
              </div>
              <input
                type="range" min={10} max={100} step={5}
                value={speedScale * 100}
                onChange={(e) => setSpeedScale(Number(e.target.value) / 100)}
                className="w-full accent-primary"
              />
            </div>
          </Card>

          {/* 키보드 가이드 */}
          <Card className="shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">키보드 조작</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div className="flex flex-col items-center gap-1">
                  <kbd className="rounded-lg border border-slate-300 bg-slate-50 px-4 py-2 text-[13px] font-mono font-bold shadow-sm">W / ↑</kbd>
                  <span className="text-[11px] text-slate-500">전진</span>
                </div>
                <div className="flex items-center justify-center gap-2">
                  {[["A / ←", "좌회전"], ["S / ↓", "후진"], ["D / →", "우회전"]].map(([k, l]) => (
                    <div key={k} className="flex flex-col items-center gap-1">
                      <kbd className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-[12px] font-mono font-bold shadow-sm">{k}</kbd>
                      <span className="text-[10px] text-slate-500">{l}</span>
                    </div>
                  ))}
                </div>
                <div className="flex flex-col items-center gap-1">
                  <kbd className="rounded-lg border border-slate-300 bg-slate-50 px-8 py-2 text-[13px] font-mono font-bold shadow-sm">Space</kbd>
                  <span className="text-[11px] text-slate-500">즉시 정지</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 속도 / 모터 상태 */}
          <div className="space-y-3">
            <Card className="shadow-sm">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">명령 속도</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {[
                  { label: "선속도 (linear)", value: curVel.linear },
                  { label: "각속도 (angular)", value: curVel.angular },
                ].map((v) => (
                  <div key={v.label}>
                    <div className="mb-1 flex justify-between text-[12px]">
                      <span className="text-slate-500">{v.label}</span>
                      <span className={cn("font-mono font-semibold", v.value !== 0 ? "text-primary" : "text-slate-400")}>
                        {v.value.toFixed(2)}
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-primary transition-all duration-75"
                        style={{
                          width: `${Math.abs(v.value) * 100}%`,
                          marginLeft: v.value < 0 ? `${(1 + v.value) * 50}%` : "50%",
                        }}
                      />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">모터 출력</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-[12px]">
                {[
                  { label: "좌측 (Left)", value: lastLeft },
                  { label: "우측 (Right)", value: lastRight },
                ].map((v) => (
                  <div key={v.label} className="flex justify-between">
                    <span className="text-slate-500">{v.label}</span>
                    <span className={cn("font-mono font-semibold", v.value !== 0 ? "text-primary" : "text-slate-400")}>
                      {v.value > 0 ? "+" : ""}{v.value}%
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Button
              variant="destructive"
              className="w-full gap-2"
              onClick={() => { setCurVel({ linear: 0, angular: 0 }); send(0, 0); }}
            >
              <Square className="size-4" />
              긴급 정지 (E-STOP)
            </Button>
          </div>
        </div>

        {/* ROS2 학습 노트 스타일 정보 */}
        <div className="rounded-xl border border-dashed border-primary/30 bg-primary/5 p-4">
          <p className="mb-2 text-[12px] font-semibold text-primary">모터 제어 정보</p>
          <div className="grid gap-2 text-[12px] text-primary/80 sm:grid-cols-2">
            <div>
              <p className="mb-1 rounded bg-primary/10 px-2 py-1 font-mono text-[11px]">linear: -1.0 ~ +1.0</p>
              <p>• +1.0 → 전진 (좌우 동일 속도)</p>
              <p>• -1.0 → 후진</p>
            </div>
            <div>
              <p className="mb-1 rounded bg-primary/10 px-2 py-1 font-mono text-[11px]">angular: -1.0 ~ +1.0</p>
              <p>• +1.0 → 좌회전 (제자리)</p>
              <p>• -1.0 → 우회전</p>
            </div>
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
