import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Camera, Loader2, Play, Square, UserRound, Monitor, Type } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useCameraFrame } from "@/hooks/useCameraFrame";
import { useActiveRobotBase } from "@/lib/active-robot";
import { adminApi, type LcdTextConfig } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/human-follow")({
  component: HumanFollowPage,
});

type Detection = {
  class_id: number;
  label: string;
  confidence: number;
  box: [number, number, number, number];
};

type ModelStatus = {
  available: boolean;
  loaded: boolean;
  classes: string[];
  confidence: number;
  error: string | null;
};

const LS_SERVER_KEY = "pinky-detect.ai-server";
const DEFAULT_AI_SERVER = "192.168.0.19:9001";
const LS_CONFIG_KEY = "human-follow.config";
const HUMAN_LABELS = new Set(["person", "human", "pinky_63"]);

function aiBase(value: string) {
  const trimmed = value.trim() || DEFAULT_AI_SERVER;
  return trimmed.startsWith("http") ? trimmed.replace(/\/$/, "") : `http://${trimmed}`;
}

function hostFromBase(base: string) {
  try {
    return new URL(base).hostname;
  } catch {
    return base.replace(/https?:\/\//, "").split(":")[0];
  }
}

function bestHuman(detections: Detection[]) {
  return detections
    .filter((d) => HUMAN_LABELS.has(d.label.toLowerCase()))
    .sort((a, b) => b.confidence - a.confidence)[0] ?? null;
}

function HumanFollowPage() {
  const robotBase = useActiveRobotBase();
  const robotHost = hostFromBase(robotBase);
  const [server, setServer] = useState(() => localStorage.getItem(LS_SERVER_KEY) ?? DEFAULT_AI_SERVER);
  const [serverInput, setServerInput] = useState(server);
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [following, setFollowing] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);

  // 로컬 스토리지에 저장된 설정값 불러오기
  const savedConfig = useMemo(() => {
    try {
      const raw = localStorage.getItem(LS_CONFIG_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.error("설정 로드 실패:", e);
    }
    return null;
  }, []);

  const [confidence, setConfidence] = useState(() => savedConfig?.confidence ?? 0.25);
  const [targetArea, setTargetArea] = useState(() => savedConfig?.targetArea ?? 0.18);
  const [turnGain, setTurnGain] = useState(() => savedConfig?.turnGain ?? 55);
  const [forwardSpeed, setForwardSpeed] = useState(() => savedConfig?.forwardSpeed ?? 34);
  const [deadband, setDeadband] = useState(() => savedConfig?.deadband ?? 0.08);
  const [invertSteering, setInvertSteering] = useState(() => savedConfig?.invertSteering ?? true);
  const [lastCmd, setLastCmd] = useState("-");
  const wsRef = useRef<WebSocket | null>(null);
  const lastMoveRef = useRef(0);
  const lastSeenRef = useRef(0);
  const followingRef = useRef(false);
  const configRef = useRef({ targetArea, turnGain, forwardSpeed, deadband, invertSteering });
  const isMovingRef = useRef(false);
  const { frameUrl, pushFrame } = useCameraFrame();
  const base = aiBase(server);

  // LCD 설정 및 인식 후 액션 상태
  const [lcdText, setLcdText] = useState(() => savedConfig?.lcdText ?? "안녕하세요!\n사람 감지됨 :)");
  const [lcdFont, setLcdFont] = useState(() => savedConfig?.lcdFont ?? "default");
  const [lcdSize, setLcdSize] = useState(() => savedConfig?.lcdSize ?? 28);
  const [lcdColor, setLcdColor] = useState(() => savedConfig?.lcdColor ?? "#ffffff");
  const [lcdBgColor, setLcdBgColor] = useState(() => savedConfig?.lcdBgColor ?? "#000000");
  const [lcdAlign, setLcdAlign] = useState<"left" | "center" | "right">(
    () => savedConfig?.lcdAlign ?? "center"
  );
  const [autoSend, setAutoSend] = useState(() => savedConfig?.autoSend ?? false);
  const autoSentRef = useRef(false);
  const lcdTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // 설정값 변경 시 자동으로 로컬 스토리지에 저장
  useEffect(() => {
    const config = {
      confidence,
      targetArea,
      turnGain,
      forwardSpeed,
      deadband,
      invertSteering,
      lcdText,
      lcdFont,
      lcdSize,
      lcdColor,
      lcdBgColor,
      lcdAlign,
      autoSend,
    };
    localStorage.setItem(LS_CONFIG_KEY, JSON.stringify(config));
  }, [
    confidence,
    targetArea,
    turnGain,
    forwardSpeed,
    deadband,
    invertSteering,
    lcdText,
    lcdFont,
    lcdSize,
    lcdColor,
    lcdBgColor,
    lcdAlign,
    autoSend,
  ]);

  const human = useMemo(() => bestHuman(detections), [detections]);

  const { data: fontsData } = useQuery({
    queryKey: ["robot", "fonts"],
    queryFn: adminApi.listFonts,
  });

  const lcdMut = useMutation({
    mutationFn: (cfg: LcdTextConfig) => adminApi.lcdText(cfg),
    onSuccess: () => toast.success("LCD에 텍스트 전송 완료"),
    onError: () => toast.error("LCD 전송 실패"),
  });

  const lcdStopMut = useMutation({
    mutationFn: () => adminApi.lcdStop(),
    onSuccess: () => toast.success("LCD 화면 끄기 완료"),
    onError: () => toast.error("LCD 끄기 실패"),
  });

  const sendLcd = () => lcdMut.mutate({
    text: lcdText,
    font_name: lcdFont,
    font_size: lcdSize,
    color: lcdColor,
    bg_color: lcdBgColor,
    align: lcdAlign,
  });

  // 사람 감지 시 LCD 자동 전송 (핑키 인사와 동일 방식):
  //  - 감지 상승엣지에서 1회만 전송하고 20초 유지 후 자동으로 1회 끈다.
  //  - 표시 중(20초)에는 감지 깜빡임을 무시 → 켜짐/꺼짐 반복 스팸 방지.
  //  - 자동 전송/끄기는 토스트를 띄우지 않는다(수동 버튼만 토스트).
  useEffect(() => {
    if (!autoSend) {
      autoSentRef.current = false;
      if (lcdTimeoutRef.current) {
        clearTimeout(lcdTimeoutRef.current);
        lcdTimeoutRef.current = null;
      }
      return;
    }
    if (human && !autoSentRef.current) {
      autoSentRef.current = true;
      void adminApi.lcdText({
        text: lcdText,
        font_name: lcdFont,
        font_size: lcdSize,
        color: lcdColor,
        bg_color: lcdBgColor,
        align: lcdAlign,
      }).catch(() => undefined);
      if (lcdTimeoutRef.current) clearTimeout(lcdTimeoutRef.current);
      lcdTimeoutRef.current = setTimeout(() => {
        void adminApi.lcdStop().catch(() => undefined);
        lcdTimeoutRef.current = null;
        autoSentRef.current = false; // 20초 후 다시 감지되면 재인사 가능
      }, 20000);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [human, autoSend]);

  // 언마운트 시 대기 중인 자동 오프 타이머 정리
  useEffect(() => () => {
    if (lcdTimeoutRef.current) clearTimeout(lcdTimeoutRef.current);
  }, []);

  async function postRobot(path: string, body?: unknown) {
    const cleanBase = robotBase.replace(/\/$/, "");
    const res = await fetch(`${cleanBase}/api/robot${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: body == null ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${path} HTTP ${res.status}`);
  }

  useEffect(() => {
    followingRef.current = following;
  }, [following]);

  useEffect(() => {
    configRef.current = { targetArea, turnGain, forwardSpeed, deadband, invertSteering };
  }, [targetArea, turnGain, forwardSpeed, deadband, invertSteering]);

  useEffect(() => {
    fetch(`${base}/api/arm/pinky-detect/status`)
      .then((r) => {
        if (!r.ok) throw new Error(`YOLO 상태 오류: HTTP ${r.status}`);
        return r.json();
      })
      .then((data: ModelStatus) => {
        setStatus(data);
        setConfidence(data.confidence ?? 0.55);
        setError(data.error);
      })
      .catch((e: Error) => {
        setStatus(null);
        setError(e.message);
      });
    return () => wsRef.current?.close();
  }, [base]);

  useEffect(() => {
    if (!following) {
      void postRobot("/human-follow/stop").catch(() => undefined);
      return;
    }
    const cfg = configRef.current;
    void postRobot("/human-follow/start", {
      ai_server_url: base,
      model: "yolov8n",
      confidence,
      target_area: cfg.targetArea,
      forward_speed: cfg.forwardSpeed,
      turn_speed: Math.max(18, Math.min(45, Math.round(cfg.turnGain * 0.45))),
      deadband: cfg.deadband,
      invert_steering: cfg.invertSteering,
      labels: ["human", "person"],
    })
      .then(() => setLastCmd("robot-side follow started"))
      .catch((e: Error) => {
        setLastCmd("robot-side start failed");
        setError(e.message);
        setFollowing(false);
      });
    return () => {
      void postRobot("/human-follow/stop").catch(() => undefined);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [following]);

  async function stopMotor(force = false) {
    // 이미 멈춘 상태이고 강제(force) 옵션이 없다면 불필요한 중복 호출(네트워크/로봇 CPU 부하)을 방지합니다.
    if (!isMovingRef.current && !force) return;
    isMovingRef.current = false;
    try {
      await postRobot("/motor/stop");
      setLastCmd("stop");
    } catch {
      setLastCmd("stop failed");
    }
  }

  async function sendFollowCommand(d: Detection) {
    const now = Date.now();
    if (now - lastMoveRef.current < 220) return;
    lastMoveRef.current = now;
    lastSeenRef.current = now;

    const [x1, y1, x2, y2] = d.box;
    const cfg = configRef.current;
    const cx = (x1 + x2) / 2;
    const w = Math.max(1, x2 - x1);
    const h = Math.max(1, y2 - y1);
    const ex = (cx - 320) / 320;
    const area = (w * h) / (640 * 480);
    const centered = Math.abs(ex) < cfg.deadband;
    const tooClose = area >= cfg.targetArea;
    const steerDir = cfg.invertSteering ? -1 : 1;
    const rawTurn = steerDir * ex * cfg.turnGain;
    const turnAbs = Math.min(45, Math.max(18, Math.abs(rawTurn)));
    const turn = Math.round(Math.sign(rawTurn || ex) * turnAbs);

    let left = 0;
    let right = 0;
    let phase = "stop";

    if (!centered) {
      // 사람 중심이 화면 중앙에 들어오기 전에는 제자리 회전만 한다.
      left = Math.max(-70, Math.min(70, turn));
      right = Math.max(-70, Math.min(70, -turn));
      phase = "align";
    } else if (!tooClose) {
      // 중앙 정렬이 된 뒤에만 전진한다.
      left = cfg.forwardSpeed;
      right = cfg.forwardSpeed;
      phase = "forward";
    }

    if (left === 0 && right === 0) {
      await stopMotor();
      setLastCmd(`stop · ex ${ex.toFixed(2)} · area ${area.toFixed(2)}`);
      return;
    }
    try {
      await postRobot("/motor/move", { left, right, duration: 0.22 });
      isMovingRef.current = true;
      setLastCmd(`${phase} · L ${left} / R ${right} · ex ${ex.toFixed(2)} · area ${area.toFixed(2)}`);
    } catch {
      setLastCmd("move failed");
    }
  }

  function start() {
    stop();
    setRunning(true);
    setError(null);
    const wsBase = base.replace(/^http/, "ws");
    const ws = new WebSocket(`${wsBase}/api/arm/pinky-detect/ws?robot_ip=${encodeURIComponent(robotHost)}`);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      setRunning(false);
      void stopMotor();
    };
    ws.onerror = () => setError("YOLO 스트림 연결 실패");
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "error") {
        setError(msg.message);
        return;
      }
      if (msg.type === "detection") {
        pushFrame(msg.frame);
        const next = msg.detections ?? [];
        setDetections(next);
        const target = bestHuman(next);
        if (followingRef.current) {
          setLastCmd(target ? "robot-side tracking" : "robot-side searching");
        }
      }
    };
  }

  function stop() {
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);
    setRunning(false);
    void stopMotor();
  }

  async function saveServer() {
    const next = serverInput.trim() || DEFAULT_AI_SERVER;
    localStorage.setItem(LS_SERVER_KEY, next);
    setServer(next);
  }

  async function updateConfidence(v: number[]) {
    const value = v[0] ?? confidence;
    setConfidence(value);
    await fetch(`${base}/api/arm/pinky-detect/confidence`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confidence: value }),
    });
  }

  return (
    <AdminShell title="사람 추종">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="rounded-lg border border-slate-200 bg-white p-3">
          <div className="relative aspect-[4/3] overflow-hidden rounded bg-black">
            {frameUrl ? (
              <>
                <img
                  src={frameUrl}
                  className="h-full w-full object-contain"
                  style={{ transform: "scaleY(-1)" }}
                  alt="human follow camera"
                />
                {/* Bounding Boxes Overlay */}
                <div className="absolute inset-0 pointer-events-none">
                  {detections.map((d, idx) => {
                    const [x1, y1, x2, y2] = d.box;
                    // Calculate relative percentages based on 640x480 coordinate space
                    const left = (x1 / 640) * 100;
                    const width = ((x2 - x1) / 640) * 100;
                    // 이미지가 scaleY(-1)로 반전되어 있으나 백엔드에서도 반전된 프레임으로 예측을 진행하므로,
                    // 반환된 y1 좌표가 그대로 정방향 top 위치에 대응됩니다.
                    const top = (y1 / 480) * 100;
                    const height = ((y2 - y1) / 480) * 100;

                    const isTarget = human && human.box[0] === x1 && human.box[1] === y1;

                    return (
                      <div
                        key={`${d.label}-${idx}`}
                        className={cn(
                          "absolute border-2 rounded transition-all duration-75",
                          isTarget
                            ? "border-emerald-500 bg-emerald-500/10 shadow-[0_0_8px_rgba(16,185,129,0.5)]"
                            : "border-blue-500 bg-blue-500/5"
                        )}
                        style={{
                          left: `${left}%`,
                          top: `${top}%`,
                          width: `${width}%`,
                          height: `${height}%`,
                        }}
                      >
                        <div
                          className={cn(
                            "absolute -top-5 left-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-white uppercase tracking-wider",
                            isTarget ? "bg-emerald-600/90" : "bg-blue-600/90"
                          )}
                        >
                          {d.label} {(d.confidence * 100).toFixed(0)}%
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-slate-400">
                <Camera className="mr-2 h-4 w-4" /> 카메라 대기
              </div>
            )}
            {human && (
              <div className="absolute left-2 top-2 rounded bg-emerald-600/85 px-2 py-1 text-xs font-semibold text-white">
                HUMAN {(human.confidence * 100).toFixed(0)}%
              </div>
            )}
          </div>
        </section>

        <aside className="space-y-3">
          <section className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-800">
              <UserRound className="h-4 w-4" /> Human Follow
            </div>
            <div className="mb-2 grid grid-cols-[1fr_auto] gap-2">
              <Input value={serverInput} onChange={(e) => setServerInput(e.target.value)} />
              <Button variant="outline" onClick={saveServer}>저장</Button>
            </div>
            <div className="mb-3 text-xs text-slate-500">AI 서버: {base} · 로봇: {robotHost}</div>
            <div className="flex gap-2">
              <Button onClick={running ? stop : start} disabled={!status?.available} variant={running ? "destructive" : "default"} className="gap-1.5">
                {running ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                {running ? "인식 중지" : "인식 시작"}
              </Button>
              <Button variant={following ? "secondary" : "outline"} onClick={() => setFollowing((v) => !v)} className="gap-1.5">
                {following ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserRound className="h-4 w-4" />}
                {following ? "로봇 추종 ON" : "로봇 추종 OFF"}
              </Button>
              <Button variant="outline" onClick={stopMotor}>정지</Button>
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-3 grid grid-cols-2 gap-2 text-center text-xs">
              <Metric label="YOLO" value={status?.loaded ? "loaded" : status?.available ? "ready" : "off"} ok={!!status?.available} />
              <Metric label="WS" value={connected ? "connected" : "closed"} ok={connected} />
              <Metric label="Human" value={human ? "detected" : "none"} ok={!!human} />
              <Metric label="Last cmd" value={lastCmd} />
            </div>
            {error && <div className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{error}</div>}
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">탐지 후보</div>
            <div className="space-y-1">
              {detections.length ? detections.slice(0, 4).map((d, idx) => (
                <div key={d.label + "-" + idx} className="flex items-center justify-between rounded border border-slate-200 px-2 py-1 text-xs">
                  <span className="truncate font-medium text-slate-700">{d.label}</span>
                  <span className="font-mono text-slate-500">{(d.confidence * 100).toFixed(0)}%</span>
                </div>
              )) : <div className="text-xs text-slate-500">탐지 결과 없음</div>}
            </div>
          </section>
          {/* [액션 1] LCD 화면 설정 카드 */}
          <section className="rounded-lg border border-slate-200 bg-white p-3 space-y-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <Monitor className="size-4 text-blue-500" />
              [액션 1] LCD 화면 설정 (인식 후 액션)
              {human && (
                <span className="ml-auto rounded bg-emerald-600 text-white text-[9px] px-1.5 py-0.5 font-bold animate-pulse">사람 감지됨</span>
              )}
            </div>

            {/* 자동 전송 토글 */}
            <div className="flex items-center justify-between rounded border border-slate-100 bg-slate-50/50 px-2 py-1.5">
              <div>
                <p className="text-xs font-semibold text-slate-700">사람 감지 시 LCD 자동 전송</p>
                <p className="text-[10px] text-slate-400">사람이 인식될 때 설정값을 전송합니다</p>
              </div>
              <Switch checked={autoSend} onCheckedChange={setAutoSend} />
            </div>

            <div className="space-y-3">
              {/* 텍스트 */}
              <div>
                <Label className="mb-1 block text-[11px] text-slate-500">표시할 텍스트</Label>
                <textarea
                  value={lcdText}
                  onChange={(e) => setLcdText(e.target.value)}
                  rows={2}
                  className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-800 focus:border-primary focus:outline-none"
                />
              </div>

              {/* 폰트 선택 및 정렬 */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="mb-1 block text-[11px] text-slate-500">폰트</Label>
                  <select
                    value={lcdFont}
                    onChange={(e) => setLcdFont(e.target.value)}
                    className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-800 focus:outline-none"
                  >
                    <option value="default">기본 폰트</option>
                    {(fontsData?.fonts ?? []).map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </select>
                </div>

                {/* 정렬 */}
                <div>
                  <Label className="mb-1 block text-[11px] text-slate-500">정렬</Label>
                  <div className="flex gap-0.5">
                    {(["left", "center", "right"] as const).map((a) => (
                      <button
                        key={a}
                        onClick={() => setLcdAlign(a)}
                        className={cn(
                          "flex-1 rounded border py-1.5 text-[10px] font-medium transition-all",
                          lcdAlign === a
                            ? "border-slate-800 bg-slate-800 text-white"
                            : "border-slate-200 bg-white text-slate-600 hover:border-slate-400",
                        )}
                      >
                        {a === "left" ? "왼" : a === "center" ? "중" : "오"}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* 글꼴 크기 */}
              <div>
                <div className="flex justify-between text-[11px] text-slate-500 mb-1">
                  <span>글꼴 크기</span>
                  <span>{lcdSize}px</span>
                </div>
                <Slider min={8} max={80} step={2} value={[lcdSize]} onValueChange={([v]) => setLcdSize(v)} />
              </div>

              {/* 색상 */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="mb-1 block text-[11px] text-slate-500">글자 색상</Label>
                  <div className="flex items-center gap-1.5">
                    <input type="color" value={lcdColor} onChange={(e) => setLcdColor(e.target.value)}
                      className="h-7 w-7 cursor-pointer rounded border border-slate-200 p-0.5" />
                    <span className="font-mono text-[10px] text-slate-500">{lcdColor}</span>
                  </div>
                </div>
                <div>
                  <Label className="mb-1 block text-[11px] text-slate-500">배경 색상</Label>
                  <div className="flex items-center gap-1.5">
                    <input type="color" value={lcdBgColor} onChange={(e) => setLcdBgColor(e.target.value)}
                      className="h-7 w-7 cursor-pointer rounded border border-slate-200 p-0.5" />
                    <span className="font-mono text-[10px] text-slate-500">{lcdBgColor}</span>
                  </div>
                </div>
              </div>

              {/* 미리보기 */}
              <div
                className="flex h-12 w-full items-center justify-center overflow-hidden rounded border border-slate-200"
                style={{ backgroundColor: lcdBgColor }}
              >
                <span
                  className="truncate px-1 text-center text-[10px] leading-tight font-medium"
                  style={{ color: lcdColor, fontSize: Math.min(lcdSize * 0.35, 12) }}
                >
                  {lcdText || "미리보기"}
                </span>
              </div>

              {/* 제어 버튼 */}
              <div className="flex gap-2 pt-1">
                <Button onClick={sendLcd} disabled={lcdMut.isPending} size="sm" className="flex-1 gap-1">
                  <Type className="size-3.5" />
                  전송
                </Button>
                <Button variant="outline" onClick={() => lcdStopMut.mutate()} disabled={lcdStopMut.isPending} size="sm" className="flex-1 gap-1">
                  <Square className="size-3.5" />
                  끄기
                </Button>
              </div>
            </div>
          </section>

          {/* [액션 2] 사람 추종 주행 설정 */}
          <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <UserRound className="size-4 text-emerald-500" />
              [액션 2] 사람 추종 주행 설정 (인식 후 액션)
            </div>

            {/* 자동 주행 토글 */}
            <div className="flex items-center justify-between rounded border border-slate-100 bg-slate-50/50 px-2 py-1.5">
              <div>
                <p className="text-xs font-semibold text-slate-700">사람 감지 시 자동 추종 주행</p>
                <p className="text-[10px] text-slate-400">로봇 내부 루프가 사람 위치를 보고 직접 주행합니다</p>
              </div>
              <Switch checked={following} onCheckedChange={setFollowing} />
            </div>

            <ControlSlider label={`신뢰도 ${(confidence * 100).toFixed(0)}%`} min={0.1} max={0.95} step={0.05} value={confidence} onValueChange={updateConfidence} />
            <ControlSlider label={`정지 거리 기준 ${(targetArea * 100).toFixed(0)}%`} min={0.05} max={0.45} step={0.01} value={targetArea} onValueChange={(v) => setTargetArea(v[0] ?? targetArea)} />
            <ControlSlider label={`전진 속도 ${forwardSpeed}`} min={18} max={60} step={1} value={forwardSpeed} onValueChange={(v) => setForwardSpeed(v[0] ?? forwardSpeed)} />
            <ControlSlider label={`회전 게인 ${turnGain}`} min={20} max={90} step={1} value={turnGain} onValueChange={(v) => setTurnGain(v[0] ?? turnGain)} />
            <div className="flex items-center justify-between rounded border border-slate-200 px-2 py-1.5 text-xs">
              <span>좌우 조향 반전</span>
              <Switch checked={invertSteering} onCheckedChange={setInvertSteering} />
            </div>
            <div className="flex items-center justify-between rounded border border-slate-200 px-2 py-1.5 text-xs">
              <span>중앙 데드밴드</span>
              <Switch checked={deadband > 0.07} onCheckedChange={(v) => setDeadband(v ? 0.08 : 0.03)} />
            </div>
          </section>
        </aside>
      </div>
    </AdminShell>
  );
}

function Metric({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={cn("truncate font-mono text-xs font-semibold", ok ? "text-emerald-600" : "text-slate-700")}>{value}</div>
    </div>
  );
}

function ControlSlider({ label, value, onValueChange, min, max, step }: {
  label: string;
  value: number;
  onValueChange: (v: number[]) => void;
  min: number;
  max: number;
  step: number;
}) {
  return (
    <div>
      <div className="mb-1 text-xs text-slate-600">{label}</div>
      <Slider value={[value]} onValueChange={onValueChange} min={min} max={max} step={step} />
    </div>
  );
}
