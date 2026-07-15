import { createFileRoute } from "@tanstack/react-router";
import { Crosshair, Play, Square, ScanEye } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { buildRobotWsUrl, getRobotBase } from "@/lib/active-robot";
import {
  adminApi,
  type MarkerActionItem,
  type MarkerActionType,
} from "@/lib/admin-api";
import {
  ACTION_LABELS,
  buildParams,
  runAction,
  ROBOT_EMOTIONS as EMOTIONS,
  LCD_FONTS,
} from "@/lib/robot-actions";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/aruco")({
  component: ArucoDockPage,
});

// 로봇 온보드 에이전트(robot_agent)의 실제 경로. /admin 유지되는 별도 저장소이므로 직접 호출한다.
const DOCK_BASE = "/api/robot/dock";
const CAM_STREAM = "/api/robot/camera/stream";
const ROBOT_BASE_KEY = "labi.dockRobotBase";

const DICTS = [
  "DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_50", "DICT_5X5_100",
  "DICT_6X6_50", "DICT_6X6_100", "DICT_7X7_50", "DICT_APRILTAG_36h11",
];
const MARKER_IDS = Array.from({ length: 21 }, (_, i) => i);

type Marker = {
  id: number; cx: number; cy: number; ex: number;
  size_frac: number; skew: number; side_px: number;
  pose?: { x_m: number; z_m: number; yaw_deg: number };
};
type DetectResp = {
  frame: { width: number; height: number };
  opencv: string; calibrated: boolean; count: number; markers: Marker[];
};
type DockStatus = {
  running: boolean; phase: string; message: string;
  telemetry: Record<string, number | boolean>;
};

function initialBase(): string {
  if (typeof localStorage !== "undefined") {
    const saved = localStorage.getItem(ROBOT_BASE_KEY);
    if (saved) return saved;
  }
  return getRobotBase();
}

function ArucoDockPage() {
  const [base, setBase] = useState<string>(initialBase);
  const [baseInput, setBaseInput] = useState<string>(base);

  const [dictionary, setDictionary] = useState("DICT_4X4_50");
  const [markerId, setMarkerId] = useState<string>("");
  const [targetSize, setTargetSize] = useState(0.4);
  const [linMax, setLinMax] = useState(0.15);
  const [angMax, setAngMax] = useState(0.3);

  const [detecting, setDetecting] = useState(false);
  const [detect, setDetect] = useState<DetectResp | null>(null);
  const [status, setStatus] = useState<DockStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 마커 액션 설정
  const [actions, setActions] = useState<MarkerActionItem[]>([]);
  const [autoAction, setAutoAction] = useState(false);
  const [actionLog, setActionLog] = useState<string[]>([]);
  const firedRef = useRef<Record<number, number>>({});

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const running = status?.running ?? false;

  // ── 검출/상태 스트림 ───────────────────────────────────────
  useEffect(() => {
    if (!detecting) return;
    const path = `${DOCK_BASE}/ws/detect?dictionary=${encodeURIComponent(dictionary)}`;
    const ws = new WebSocket(buildRobotWsUrl(base, path));
    ws.onmessage = (event) => {
      try {
        const data: DetectResp = JSON.parse(event.data);
        setDetect(data);
        setError(null);
      } catch {
        setError("검출 스트림 해석 실패");
      }
    };
    ws.onerror = () => setError("검출 스트림 연결 오류");
    return () => ws.close();
  }, [base, detecting, dictionary]);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${base}${DOCK_BASE}/status`);
      if (r.ok) {
        const data = await r.json();
        setStatus((data?.state ?? data) as DockStatus);
      }
    } catch {
      /* 무시 */
    }
  }, [base]);

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(base, `${DOCK_BASE}/ws/status`));
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setStatus((data?.state ?? data) as DockStatus);
      } catch {
        /* malformed status frame */
      }
    };
    return () => ws.close();
  }, [base]);

  // ── 오버레이 그리기 (카메라는 수직 반전이므로 y 를 뒤집어 정렬) ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !detect) return;
    const { width: fw, height: fh } = detect.frame;
    if (canvas.width !== fw) canvas.width = fw;
    if (canvas.height !== fh) canvas.height = fh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, fw, fh);

    // 중앙 세로 가이드
    ctx.strokeStyle = "rgba(255,255,255,0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(fw / 2, 0);
    ctx.lineTo(fw / 2, fh);
    ctx.stroke();

    for (const m of detect.markers) {
      const side = m.side_px;
      const dy = fh - m.cy; // 수직 반전 보정
      const x = m.cx - side / 2;
      const y = dy - side / 2;
      const centered = Math.abs(m.ex) <= 0.06;
      ctx.strokeStyle = centered ? "#22c55e" : "#f59e0b";
      ctx.lineWidth = 3;
      ctx.strokeRect(x, y, side, side);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.font = "bold 18px monospace";
      ctx.fillText(`id ${m.id}`, x, Math.max(16, y - 6));
    }
  }, [detect]);


  const loadActions = useCallback(async () => {
    try { setActions(await adminApi.listMarkerActions()); }
    catch { /* 백엔드 재시작 전이면 무시 */ }
  }, []);
  useEffect(() => { void loadActions(); }, [loadActions]);

  // 인식 시 지정된 액션 자동 실행 (마커별 6초 쿨다운)
  useEffect(() => {
    if (!autoAction || !detect) return;
    const now = Date.now();
    for (const m of detect.markers) {
      const a = actions.find((x) => x.marker_id === m.id && x.enabled && x.action_type !== "none");
      if (!a) continue;
      if (now - (firedRef.current[m.id] ?? 0) < 6000) continue;
      firedRef.current[m.id] = now;
      void runAction(base, a);
      setActionLog((log) =>
        [`${new Date().toLocaleTimeString()} · id ${m.id} → ${ACTION_LABELS[a.action_type]}`, ...log].slice(0, 6));
    }
  }, [detect, autoAction, actions, base]);

  function saveBase() {
    const v = baseInput.trim().replace(/\/$/, "");
    setBase(v);
    localStorage.setItem(ROBOT_BASE_KEY, v);
  }

  async function startDock() {
    setBusy(true);
    setError(null);
    try {
      const cfg: Record<string, unknown> = {
        dictionary,
        marker_id: markerId.trim() === "" ? null : Number(markerId),
        target_size: targetSize,
        lin_max: linMax,
        ang_max: angMax,
      };
      const r = await fetch(`${base}${DOCK_BASE}/start`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!r.ok) throw new Error(`시작 실패: HTTP ${r.status}`);
      setDetecting(true); // 도킹 중에도 오버레이 유지
      await fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "도킹 시작 실패");
    } finally {
      setBusy(false);
    }
  }

  async function stopDock() {
    setBusy(true);
    try {
      await fetch(`${base}${DOCK_BASE}/stop`, { method: "POST" });
      await fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "정지 실패");
    } finally {
      setBusy(false);
    }
  }

  const top = detect?.markers[0];
  const tele = status?.telemetry ?? {};

  return (
    <AdminShell title="아르코 도킹 관제">
      <div className="flex flex-col gap-3 lg:flex-row">
        {/* ── 좌: 카메라 + 오버레이 ── */}
        <div className="flex flex-col gap-2 lg:w-[420px]">
          <div
            className="relative w-full overflow-hidden rounded-lg bg-black shadow-sm"
            style={{ aspectRatio: "4/3" }}
          >
            <img
              src={`${base}${CAM_STREAM}`}
              alt="로봇 카메라"
              className="absolute inset-0 h-full w-full object-contain"
              style={{ transform: "scaleY(-1)" }}
            />
            <canvas
              ref={canvasRef}
              className="absolute inset-0 h-full w-full"
            />
            <div className="absolute left-2 top-2 flex items-center gap-1.5">
              <span className="rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-300">
                ArUco · {detect?.opencv ?? "--"}
              </span>
              {detecting && (
                <span className="flex items-center gap-1 rounded bg-emerald-600/80 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">
                  <span className="h-1.5 w-1.5 animate-ping rounded-full bg-white" />
                  DETECT
                </span>
              )}
            </div>
            <div className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-400">
              {detect ? `검출 ${detect.count}` : "대기"}
            </div>
          </div>

          {/* 실시간 검출 요약 */}
          <div className="grid grid-cols-4 gap-2 text-center">
            <Metric label="ID" value={top ? String(top.id) : "--"} />
            <Metric label="좌우오차 ex" value={top ? top.ex.toFixed(2) : "--"}
              tone={top ? (Math.abs(top.ex) <= 0.06 ? "ok" : "warn") : "idle"} />
            <Metric label="크기 size" value={top ? top.size_frac.toFixed(3) : "--"} />
            <Metric label="기울기 skew" value={top ? top.skew.toFixed(2) : "--"} />
          </div>
          {error && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
              {error}
            </div>
          )}
        </div>

        {/* ── 우: 설정 + 제어 + 상태 ── */}
        <div className="flex flex-1 flex-col gap-3">
          {/* 로봇 주소 */}
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-1 text-xs font-medium text-slate-500">로봇 주소 (robot_agent)</div>
            <div className="flex gap-2">
              <input
                value={baseInput}
                onChange={(e) => setBaseInput(e.target.value)}
                placeholder="http://192.168.0.28:9001"
                className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm font-mono"
              />
              <Button size="sm" variant="outline" onClick={saveBase}>저장</Button>
            </div>
            <div className="mt-1 text-[11px] text-slate-400">현재: <span className="font-mono">{base}</span></div>
          </div>

          {/* 설정 */}
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 text-sm font-semibold text-slate-700">도킹 설정</div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-600">
                마커 사전
                <select
                  value={dictionary}
                  onChange={(e) => setDictionary(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                >
                  {DICTS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              <label className="text-xs text-slate-600">
                도킹 대상 마커 ID
                <select
                  value={markerId}
                  onChange={(e) => setMarkerId(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                >
                  <option value="">자동 선택</option>
                  {MARKER_IDS.map((i) => <option key={i} value={String(i)}>{i}</option>)}
                </select>
              </label>
              <Slider label={`목표 크기 (정지 거리) ${targetSize.toFixed(2)}`}
                min={0.1} max={0.8} step={0.01} value={targetSize} onChange={setTargetSize} />
              <Slider label={`전진 속도 상한 ${linMax.toFixed(2)}`}
                min={0.08} max={0.35} step={0.01} value={linMax} onChange={setLinMax} />
              <Slider label={`회전 속도 상한 ${angMax.toFixed(2)}`}
                min={0.1} max={0.5} step={0.01} value={angMax} onChange={setAngMax} />
            </div>
          </div>

          {/* 제어 */}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={detecting ? "secondary" : "outline"}
              onClick={() => setDetecting((v) => !v)}
              className="gap-1.5"
            >
              <ScanEye className="h-4 w-4" />
              {detecting ? "인식 중지" : "인식 시작"}
            </Button>
            <Button
              onClick={startDock}
              disabled={busy || running}
              className="gap-1.5 bg-emerald-600 hover:bg-emerald-700"
            >
              <Play className="h-4 w-4" /> 도킹 시작
            </Button>
            <Button
              onClick={stopDock}
              disabled={busy}
              variant="destructive"
              className="gap-1.5"
            >
              <Square className="h-4 w-4" /> 정지
            </Button>
            <Button
              variant={autoAction ? "secondary" : "outline"}
              onClick={() => setAutoAction((v) => !v)}
              className="gap-1.5"
              title="마커 인식 시 지정 액션 자동 실행"
            >
              <Crosshair className="h-4 w-4" />
              {autoAction ? "자동실행 ON" : "자동실행 OFF"}
            </Button>
          </div>

          {/* 상태 */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 flex items-center gap-2">
              <Crosshair className="h-4 w-4 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">도킹 상태</span>
              <span className={cn(
                "ml-auto rounded px-2 py-0.5 text-xs font-medium",
                running ? "bg-emerald-100 text-emerald-700"
                  : status?.phase === "done" ? "bg-blue-100 text-blue-700"
                  : "bg-slate-200 text-slate-600"
              )}>
                {status?.phase ?? "idle"}
              </span>
            </div>
            {status?.message && <div className="mb-2 text-xs text-slate-500">{status.message}</div>}
            <div className="grid grid-cols-3 gap-2 text-center text-xs sm:grid-cols-6">
              <Metric label="ex" value={fmt(tele.ex)} />
              <Metric label="dist" value={fmt(tele.dist)} />
              <Metric label="linear" value={fmt(tele.linear)} />
              <Metric label="angular" value={fmt(tele.angular)} />
              <Metric label="L" value={fmt(tele.left)} />
              <Metric label="R" value={fmt(tele.right)} />
            </div>
          </div>

          {/* 마커별 액션 설정 */}
          <MarkerActionEditor actions={actions} base={base} onSaved={loadActions} />

          {/* 실행 로그 */}
          {actionLog.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <div className="mb-1 text-xs font-semibold text-slate-700">액션 실행 로그</div>
              <ul className="space-y-0.5 text-[11px] font-mono text-slate-500">
                {actionLog.map((l, i) => <li key={i}>{l}</li>)}
              </ul>
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-slate-400">
            ⚠️ <b>도킹 시작</b>은 실제 로봇을 주행시킵니다. 넓은 공간 또는 바퀴를 띄운 상태에서
            낮은 속도로 테스트하고, 이상 시 즉시 <b>정지</b>를 누르세요.
            <br />거리(m) 정밀 모드는 카메라 캘리브레이션(camera_calib.npz) 후 사용 가능합니다.
          </p>
        </div>
      </div>
    </AdminShell>
  );
}

function fmt(v: number | boolean | undefined): string {
  if (v === undefined) return "--";
  if (typeof v === "boolean") return v ? "O" : "X";
  return typeof v === "number" ? String(v) : String(v);
}

function Metric({ label, value, tone = "idle" }: {
  label: string; value: string; tone?: "ok" | "warn" | "idle";
}) {
  return (
    <div className="rounded border border-slate-200 bg-white px-2 py-1.5">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={cn(
        "font-mono text-sm font-semibold",
        tone === "ok" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : "text-slate-800"
      )}>
        {value}
      </div>
    </div>
  );
}

function Slider({ label, min, max, step, value, onChange }: {
  label: string; min: number; max: number; step: number;
  value: number; onChange: (v: number) => void;
}) {
  return (
    <label className="col-span-2 text-xs text-slate-600">
      {label}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full"
      />
    </label>
  );
}

const inputCls = "mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm";

function MarkerActionEditor({ actions, base, onSaved }: {
  actions: MarkerActionItem[]; base: string; onSaved: () => Promise<void> | void;
}) {
  const [mid, setMid] = useState(7);
  const [type, setType] = useState<MarkerActionType>("rotate");
  const [enabled, setEnabled] = useState(true);
  const [p, setP] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const ex = actions.find((a) => a.marker_id === mid);
    if (ex) {
      setType(ex.action_type); setEnabled(ex.enabled);
      setP(Object.fromEntries(Object.entries(ex.params ?? {}).map(([k, v]) => [k, String(v)])));
    } else { setType("rotate"); setEnabled(true); setP({}); }
  }, [mid, actions]);

  const f = (k: string, d = "") => p[k] ?? d;
  const set = (k: string, v: string) => setP((prev) => ({ ...prev, [k]: v }));

  async function save() {
    setBusy(true); setMsg(null);
    try {
      await adminApi.upsertMarkerAction(mid, { action_type: type, enabled, params: buildParams(type, p), label: null });
      await onSaved(); setMsg("저장됨");
    } catch (e) { setMsg(e instanceof Error ? e.message : "저장 실패 (백엔드 재시작 필요?)"); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await adminApi.deleteMarkerAction(mid); await onSaved(); setMsg("삭제됨"); }
    catch (e) { setMsg(e instanceof Error ? e.message : "삭제 실패"); }
    finally { setBusy(false); }
  }
  async function test() {
    setMsg("테스트 전송...");
    try { await runAction(base, { marker_id: mid, action_type: type, enabled, params: buildParams(type, p), label: null }); setMsg("테스트 전송됨"); }
    catch { setMsg("테스트 실패"); }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="mb-2 text-sm font-semibold text-slate-700">마커별 액션 설정</div>

      {/* 설정된 액션 요약 */}
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
          <span>Saved actions {actions.filter((a) => a.action_type !== "none").length}</span>
          <span>Click marker to edit</span>
        </div>
        <div className="grid grid-cols-7 gap-1">
          {MARKER_IDS.map((id) => {
            const a = actions.find((x) => x.marker_id === id);
            const configured = Boolean(a && a.action_type !== "none");
            return (
              <button
                key={id}
                type="button"
                onClick={() => setMid(id)}
                className={cn(
                  "h-8 rounded border text-[11px] font-medium",
                  mid === id ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200",
                  configured
                    ? a?.enabled ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-400"
                    : "bg-white text-slate-400"
                )}
                title={configured && a ? ACTION_LABELS[a.action_type] : "not set"}
              >
                #{id}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-slate-600">
          마커 ID
          <select value={mid} onChange={(e) => setMid(Number(e.target.value))} className={inputCls}>
            {MARKER_IDS.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          액션
          <select value={type} onChange={(e) => setType(e.target.value as MarkerActionType)} className={inputCls}>
            {(Object.keys(ACTION_LABELS) as MarkerActionType[]).map((t) => <option key={t} value={t}>{ACTION_LABELS[t]}</option>)}
          </select>
        </label>

        {type === "rotate" && (<>
          <label className="text-xs text-slate-600">각도 (°, +좌/-우)
            <input value={f("angle", "90")} onChange={(e) => set("angle", e.target.value)} className={inputCls} /></label>
          <label className="text-xs text-slate-600">속도 (0~1)
            <input value={f("speed", "0.3")} onChange={(e) => set("speed", e.target.value)} className={inputCls} /></label>
        </>)}
        {type === "move" && (<>
          <label className="text-xs text-slate-600">방향
            <select value={f("direction", "forward")} onChange={(e) => set("direction", e.target.value)} className={inputCls}>
              {["forward", "backward", "left", "right"].map((d) => <option key={d} value={d}>{d}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">거리 (m)
            <input value={f("distance", "0.3")} onChange={(e) => set("distance", e.target.value)} className={inputCls} /></label>
        </>)}
        {type === "lcd_emotion" && (
          <label className="col-span-2 text-xs text-slate-600">표정
            <select value={f("emotion", "happy")} onChange={(e) => set("emotion", e.target.value)} className={inputCls}>
              {EMOTIONS.map((em) => <option key={em} value={em}>{em}</option>)}
            </select></label>
        )}
        {type === "lcd_text" && (<>
          <label className="col-span-2 text-xs text-slate-600">텍스트
            <input value={f("text")} onChange={(e) => set("text", e.target.value)} placeholder="예: B구역 도착" className={inputCls} /></label>
          <label className="text-xs text-slate-600">폰트
            <select value={f("font_name", "MaruBuri-Bold.ttf")} onChange={(e) => set("font_name", e.target.value)} className={inputCls}>
              {LCD_FONTS.map((font) => <option key={font} value={font}>{font}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">크기
            <input value={f("font_size", "24")} onChange={(e) => set("font_size", e.target.value.replace(/[^0-9]/g, ""))} className={inputCls} /></label>
          <label className="text-xs text-slate-600">글자색
            <input type="color" value={f("color", "#ffffff")} onChange={(e) => set("color", e.target.value)} className="mt-1 h-9 w-full rounded border border-slate-300 px-1 py-1" /></label>
          <label className="text-xs text-slate-600">배경색
            <input type="color" value={f("bg_color", "#000000")} onChange={(e) => set("bg_color", e.target.value)} className="mt-1 h-9 w-full rounded border border-slate-300 px-1 py-1" /></label>
          <label className="col-span-2 text-xs text-slate-600">정렬
            <select value={f("align", "center")} onChange={(e) => set("align", e.target.value)} className={inputCls}>
              {["left", "center", "right"].map((align) => <option key={align} value={align}>{align}</option>)}
            </select></label>
        </>
        )}
        {type === "lcd_image" && (
          <label className="col-span-2 text-xs text-slate-600">이미지 파일명
            <input value={f("filename")} onChange={(e) => set("filename", e.target.value)} placeholder="rc_lcd_images 파일명" className={inputCls} /></label>
        )}
        {type === "dock" && (
          <label className="col-span-2 text-xs text-slate-600">목표 크기 (정지 거리)
            <input value={f("target_size", "0.3")} onChange={(e) => set("target_size", e.target.value)} className={inputCls} /></label>
        )}
      </div>

      <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        사용 (인식 시 자동 실행 대상)
      </label>

      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy}>저장</Button>
        <Button size="sm" variant="outline" onClick={test} disabled={busy}>테스트</Button>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy} className="text-red-500">삭제</Button>
        {msg && <span className="text-[11px] text-slate-500">{msg}</span>}
      </div>
    </div>
  );
}
