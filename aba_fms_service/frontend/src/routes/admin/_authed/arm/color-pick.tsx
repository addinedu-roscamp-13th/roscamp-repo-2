/**
 * 색상 블록 감지 & 집기 — /admin/arm/color-pick
 *
 * 동작 흐름:
 *   1. 감지할 색상 선택
 *   2. "감지 & 집기 실행" 클릭 → POST /api/arm/color-pick
 *   3. 백엔드가 카메라로 색상 감지 → 팔 이동 → 집기 → 홈 복귀
 *   4. WS로 실시간 상태 + 로그 수신
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Camera, CheckCircle2, Circle, Info, Loader2, ScanEye, Square } from "lucide-react";
import { AdminShell } from "@/components/admin/AdminShell";
import { ArmKeyboardGuide } from "@/components/admin/ArmKeyboardGuide";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useArmKeyboard } from "@/hooks/useArmKeyboard";
import { useCameraFrame } from "@/hooks/useCameraFrame";
import { buildRobotHttpUrl, buildRobotWsUrl, useActiveRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/arm/color-pick")({ component: ColorPickPage });

// ── 색상 설정 ────────────────────────────────────────────────
const COLORS = [
  { id: "red",    ko: "빨간색", hex: "#ef4444", hsv: "H:0-10 / S:>120" },
  { id: "green",  ko: "초록색", hex: "#22c55e", hsv: "H:40-80 / S:>100" },
  { id: "blue",   ko: "파란색", hex: "#3b82f6", hsv: "H:100-130 / S:>80" },
  { id: "yellow", ko: "노란색", hex: "#eab308", hsv: "H:20-35 / S:>100" },
] as const;
type ColorId = typeof COLORS[number]["id"];

// ── 로그 타입 ─────────────────────────────────────────────────
interface LogEntry { time: string; level: "info" | "warn" | "success"; msg: string }

function ts() { return new Date().toLocaleTimeString("ko-KR", { hour12: false }); }

// ── 메인 컴포넌트 ─────────────────────────────────────────────
function ColorPickPage() {
  const robotBase = useActiveRobotBase();
  const [selected, setSelected] = useState<ColorId>("red");
  const [mode, setMode]   = useState("idle");
  const [detection, setDetection] = useState<Record<string, unknown> | null>(null);
  const [logs, setLogs]   = useState<LogEntry[]>([]);
  const [busy, setBusy]   = useState(false);
  const [demoMode, setDemoMode] = useState(true);
  const { frameUrl: camFrame, pushFrame } = useCameraFrame();
  const wsRef = useRef<WebSocket | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const { syncAngles } = useArmKeyboard();

  const addLog = (level: LogEntry["level"], msg: string) =>
    setLogs((prev) => [...prev.slice(-49), { time: ts(), level, msg }]);

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setMode(msg.mode);
          setDemoMode(!!msg.demo_mode);
          if (msg.joints?.length >= 6) syncAngles(msg.joints);
          if (msg.detection) {
            setDetection(msg.detection);
            if (msg.detection.done) {
              setBusy(false);
              addLog("success", `${msg.detection.color} 블록 집기 완료!`);
            }
          }
        }
        if (msg.type === "camera") pushFrame(msg.frame);
        if (msg.type === "log") addLog(msg.level, msg.msg);
      } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [robotBase]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const handlePick = async () => {
    setBusy(true);
    setDetection(null);
    addLog("info", `${COLORS.find((c) => c.id === selected)?.ko} 블록 감지 시작...`);
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/color-pick"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ color: selected }),
    });
  };

  const handleStop = async () => {
    setBusy(false);
    addLog("warn", "중지 명령 전송");
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
  };

  const handleCameraView = async (preset: number) => {
    addLog("info", `카메라 뷰 포지션 ${preset} 이동...`);
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/camera-view"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    });
  };

  const colorInfo = COLORS.find((c) => c.id === selected)!;
  const isRunning = mode === "color_pick";

  return (
    <AdminShell title="색상 블록 집기">
      <div className="space-y-5">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">색상 블록 감지 & 집기</h1>
          <p className="text-[13px] text-slate-500">카메라로 색상 블록을 감지하고 로봇팔이 집어올립니다.</p>
        </div>

        {demoMode && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <Info className="size-4 shrink-0" />
            Demo 모드 — 실제 카메라/하드웨어 없이 시뮬레이션합니다.
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          {/* 왼쪽: 색상 선택 + 제어 */}
          <div className="space-y-4">
            {/* 색상 선택 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">감지할 색상 선택</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-2">
                  {COLORS.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => setSelected(c.id)}
                      className={cn(
                        "flex items-center gap-3 rounded-lg border p-3 text-left transition-all",
                        selected === c.id
                          ? "border-indigo-400 bg-indigo-50 shadow-sm"
                          : "border-slate-200 hover:border-slate-300",
                      )}
                    >
                      <span className="h-6 w-6 rounded-full border border-white shadow-sm shrink-0"
                        style={{ background: c.hex }} />
                      <div>
                        <p className="text-[13px] font-medium">{c.ko}</p>
                        <p className="text-[10px] text-slate-400 font-mono">{c.hsv}</p>
                      </div>
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* 실행 버튼 */}
            <Card>
              <CardContent className="pt-4 space-y-2">
                <Button className="w-full" variant="outline" disabled={busy}
                  onClick={() => handleCameraView(1)}>
                  <Camera className="size-4" /> 카메라 뷰 포지션 (바닥)
                </Button>
                <Button
                  className="w-full"
                  disabled={busy}
                  style={!busy ? { background: colorInfo.hex } : {}}
                  onClick={handlePick}
                >
                  {busy
                    ? <><Loader2 className="size-4 animate-spin" /> 실행 중...</>
                    : <><ScanEye className="size-4" /> {colorInfo.ko} 블록 집기</>}
                </Button>
                <Button className="w-full" variant="destructive" onClick={handleStop}>
                  <Square className="size-4" /> 중지
                </Button>
              </CardContent>
            </Card>

            {/* 감지 결과 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">감지 결과</CardTitle>
              </CardHeader>
              <CardContent>
                {isRunning && !detection && (
                  <div className="flex items-center gap-2 text-[13px] text-slate-500">
                    <Loader2 className="size-4 animate-spin" /> 블록 탐색 중...
                  </div>
                )}
                {detection && !detection.done && (
                  <div className="space-y-1.5 text-[12px]">
                    <div className="flex items-center gap-1.5 text-green-600 font-medium">
                      <CheckCircle2 className="size-4" /> 블록 감지됨
                    </div>
                    {[
                      ["위치 X", `${detection.x}px`],
                      ["위치 Y", `${detection.y}px`],
                      ["면적", `${detection.area}px²`],
                    ].map(([k, v]) => (
                      <div key={String(k)} className="flex justify-between">
                        <span className="text-slate-500">{String(k)}</span>
                        <span className="font-mono font-medium">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {detection && !!detection.done && (
                  <div className="flex items-center gap-1.5 text-[13px] text-emerald-600 font-medium">
                    <CheckCircle2 className="size-4" /> 집기 완료 → 홈 복귀
                  </div>
                )}
                {!isRunning && !detection && (
                  <p className="text-[13px] text-slate-400">실행 전</p>
                )}
              </CardContent>
            </Card>
          </div>

          {/* 오른쪽: 카메라 + 로그 */}
          <div className="space-y-4">
            {/* 카메라 뷰 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px] flex items-center gap-2">
                  카메라 뷰
                  {isRunning && <span className="flex h-2 w-2 rounded-full bg-red-500 animate-pulse" />}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="aspect-video rounded-lg bg-slate-900 flex items-center justify-center overflow-hidden relative">
                  {camFrame
                    ? <img src={camFrame} className="w-full h-full object-cover" alt="camera" />
                    : (
                      <div className="text-center text-slate-500">
                        <Circle className="size-8 mx-auto mb-2 opacity-30" />
                        <p className="text-[12px]">{demoMode ? "Demo 모드 — 카메라 없음" : "카메라 연결 중..."}</p>
                      </div>
                    )}
                  {/* 색상 오버레이 힌트 */}
                  {isRunning && detection && !detection.done && (
                    <div
                      className="absolute border-2 rounded transition-all duration-200"
                      style={{
                        borderColor: colorInfo.hex,
                        left: `${(Number(detection.x) / 640) * 100}%`,
                        top: `${(Number(detection.y) / 480) * 100}%`,
                        width: "60px", height: "60px",
                        transform: "translate(-50%, -50%)",
                      }}
                    />
                  )}
                </div>
              </CardContent>
            </Card>

            {/* 로그 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">실행 로그</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-48 overflow-y-auto rounded bg-slate-950 p-2 font-mono text-[11px] space-y-0.5">
                  {logs.length === 0 && (
                    <p className="text-slate-500">대기 중...</p>
                  )}
                  {logs.map((l, i) => (
                    <div key={i} className={cn("flex gap-2",
                      l.level === "success" ? "text-emerald-400"
                      : l.level === "warn" ? "text-amber-400"
                      : "text-slate-300")}>
                      <span className="text-slate-500 shrink-0">{l.time}</span>
                      <span>{l.msg}</span>
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              </CardContent>
            </Card>
          </div>
        </div>

        <ArmKeyboardGuide />

        {/* 학습 노트 */}
        <div className="rounded-xl border border-dashed border-green-200 bg-green-50/50 p-4">
          <p className="text-[12px] font-semibold text-green-700 mb-2">OpenCV HSV 색상 감지 원리</p>
          <div className="grid gap-2 sm:grid-cols-2 text-[12px] text-green-700">
            <div>
              <p className="font-mono bg-green-100 px-2 py-1 rounded mb-1">cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)</p>
              <p>• BGR → HSV 변환으로 조명 영향 최소화</p>
              <p>• cv2.inRange(hsv, lower, upper) 로 마스크 생성</p>
            </div>
            <div>
              <p className="font-mono bg-green-100 px-2 py-1 rounded mb-1">cv2.findContours → 무게중심</p>
              <p>• 가장 큰 contour의 무게중심 = 목표 위치</p>
              <p>• 픽셀 좌표 → IK 역산으로 관절 각도 계산</p>
            </div>
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
