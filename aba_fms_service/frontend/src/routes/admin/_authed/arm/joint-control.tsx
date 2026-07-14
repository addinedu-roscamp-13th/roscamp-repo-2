/**
 * 관절 수동 제어 — /admin/arm/joint-control
 * 각 관절을 슬라이더/버튼으로 조작하고 현재 좌표를 복사한다.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, Home, Minus, Plus, Wifi, WifiOff } from "lucide-react";
import { useArmKeyboard } from "@/hooks/useArmKeyboard";
import { AdminShell } from "@/components/admin/AdminShell";
import { ArmKeyboardGuide } from "@/components/admin/ArmKeyboardGuide";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buildRobotHttpUrl, buildRobotWsUrl, useActiveRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/arm/joint-control")({ component: JointControlPage });

const JOINTS = [
  { label: "J1 베이스",   min: -168, max: 168 },
  { label: "J2 숄더",    min: -135, max: 135 },
  { label: "J3 엘보",    min: -150, max: 150 },
  { label: "J4 리스트1", min: -145, max: 145 },
  { label: "J5 리스트2", min: -165, max: 165 },
  { label: "J6 리스트3", min: -180, max: 180 },
];

const STEP_OPTIONS = [1, 5, 10, 30] as const;
type Step = typeof STEP_OPTIONS[number];

function JointControlPage() {
  const robotBase = useActiveRobotBase();
  const [realAngles, setRealAngles] = useState<number[]>([0, 0, 0, 0, 0, 0]);
  const [targetAngles, setTargetAngles] = useState<number[]>([0, 0, 0, 0, 0, 0]);
  const [gripper, setGripper] = useState(0);
  const [connected, setConnected] = useState(false);
  const [step, setStep] = useState<Step>(10);
  const [copied, setCopied] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const syncedRef = useRef(false);
  const { syncAngles: kbSync } = useArmKeyboard();

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setConnected(msg.connected);
          if (msg.joints?.length >= 6) {
            setRealAngles(msg.joints.slice(0, 6).map((v: number) => Math.round(v * 10) / 10));
            kbSync(msg.joints);
            // 첫 수신 시 target을 실제 각도로 동기화
            if (!syncedRef.current) {
              setTargetAngles(msg.joints.slice(0, 6).map((v: number) => Math.round(v)));
              syncedRef.current = true;
            }
          }
          if (typeof msg.gripper === "number") setGripper(msg.gripper);
        }
      } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [robotBase]);

  const sendAngles = async (angles: number[]) => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/angles"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ angles, speed: 20 }),
    });
  };

  const sendGripper = async (value: number) => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/gripper"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, speed: 20 }),
    });
  };

  const jogJoint = (idx: number, delta: number) => {
    const next = targetAngles.map((v, i) => {
      if (i !== idx) return v;
      const clamped = Math.max(JOINTS[i].min, Math.min(JOINTS[i].max, v + delta));
      return Math.round(clamped * 10) / 10;
    });
    setTargetAngles(next);
    sendAngles(next);
  };

  const handleSlider = (idx: number, val: number) => {
    const next = targetAngles.map((v, i) => (i === idx ? val : v));
    setTargetAngles(next);
  };

  const handleSliderCommit = (angles: number[]) => {
    sendAngles(angles);
  };

  const goHome = async () => {
    const home = [0, 0, 0, 0, 0, 0];
    setTargetAngles(home);
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/angles"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ angles: home, speed: 30 }),
    });
  };

  const copyAngles = () => {
    const txt = `[${realAngles.map((v) => v.toFixed(1)).join(", ")}]`;
    navigator.clipboard.writeText(txt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <AdminShell title="관절 수동 제어">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">관절 수동 제어</h1>
            <p className="text-[13px] text-slate-500">각 관절을 직접 조작해서 원하는 포지션을 찾으세요.</p>
          </div>
          <div className="flex items-center gap-2">
            {connected
              ? <span className="flex items-center gap-1 text-[12px] text-emerald-600"><Wifi className="size-3" />연결됨</span>
              : <span className="flex items-center gap-1 text-[12px] text-slate-400"><WifiOff className="size-3" />미연결</span>}
          </div>
        </div>

<div className="grid gap-4 lg:grid-cols-3">
          {/* 왼쪽: 관절 슬라이더 */}
          <div className="lg:col-span-2 space-y-3">
            {/* 이동 단위 선택 */}
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-slate-500">이동 단위:</span>
              {STEP_OPTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => setStep(s)}
                  className={cn(
                    "px-3 py-1 rounded text-[12px] font-mono border transition-all",
                    step === s
                      ? "bg-indigo-600 text-white border-indigo-600"
                      : "border-slate-200 text-slate-600 hover:border-indigo-300",
                  )}
                >
                  ±{s}°
                </button>
              ))}
            </div>

            {/* 관절 카드 */}
            {JOINTS.map((j, idx) => (
              <Card key={idx}>
                <CardContent className="pt-4">
                  <div className="flex items-center gap-3">
                    <div className="w-24 shrink-0">
                      <p className="text-[12px] font-semibold text-slate-700">{j.label}</p>
                      <p className="text-[11px] text-slate-400 font-mono">{j.min}° ~ {j.max}°</p>
                    </div>

                    {/* - 버튼 */}
                    <Button
                      size="sm" variant="outline"
                      className="size-8 p-0 shrink-0"
                      onClick={() => jogJoint(idx, -step)}
                    >
                      <Minus className="size-3" />
                    </Button>

                    {/* 슬라이더 */}
                    <input
                      type="range"
                      min={j.min} max={j.max} step={1}
                      value={targetAngles[idx]}
                      className="flex-1 accent-indigo-600"
                      onChange={(e) => handleSlider(idx, Number(e.target.value))}
                      onMouseUp={() => handleSliderCommit(targetAngles)}
                      onTouchEnd={() => handleSliderCommit(targetAngles)}
                    />

                    {/* + 버튼 */}
                    <Button
                      size="sm" variant="outline"
                      className="size-8 p-0 shrink-0"
                      onClick={() => jogJoint(idx, +step)}
                    >
                      <Plus className="size-3" />
                    </Button>

                    {/* 현재값 / 목표값 */}
                    <div className="w-28 shrink-0 text-right">
                      <p className="text-[13px] font-mono font-bold text-indigo-600">
                        {targetAngles[idx].toFixed(0)}°
                      </p>
                      <p className="text-[11px] font-mono text-slate-400">
                        실제 {realAngles[idx].toFixed(1)}°
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}

            {/* 그리퍼 */}
            <Card>
              <CardContent className="pt-4">
                <div className="flex items-center gap-3">
                  <div className="w-24 shrink-0">
                    <p className="text-[12px] font-semibold text-slate-700">그리퍼</p>
                    <p className="text-[11px] text-slate-400 font-mono">0=닫힘 ~ 100=열림</p>
                  </div>
                  <Button size="sm" variant="outline" className="size-8 p-0 shrink-0"
                    onClick={() => { const v = Math.max(0, gripper - 10); setGripper(v); sendGripper(v); }}>
                    <Minus className="size-3" />
                  </Button>
                  <input
                    type="range" min={0} max={100} step={5}
                    value={gripper}
                    className="flex-1 accent-emerald-600"
                    onChange={(e) => setGripper(Number(e.target.value))}
                    onMouseUp={() => sendGripper(gripper)}
                    onTouchEnd={() => sendGripper(gripper)}
                  />
                  <Button size="sm" variant="outline" className="size-8 p-0 shrink-0"
                    onClick={() => { const v = Math.min(100, gripper + 10); setGripper(v); sendGripper(v); }}>
                    <Plus className="size-3" />
                  </Button>
                  <div className="w-28 shrink-0 text-right">
                    <p className="text-[13px] font-mono font-bold text-emerald-600">{gripper}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* 오른쪽: 현재 좌표 + 버튼 */}
          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">현재 실제 좌표</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="rounded-lg bg-slate-950 p-3 font-mono text-[13px] text-emerald-400">
                  [<br />
                  {realAngles.map((v, i) => (
                    <span key={i} className="block pl-4">
                      {v.toFixed(1)}{i < 5 ? "," : ""}
                      <span className="text-slate-500 text-[11px] ml-2">// {JOINTS[i]?.label}</span>
                    </span>
                  ))}
                  ]
                </div>
                <Button
                  className="w-full" size="sm" variant="outline"
                  onClick={copyAngles}
                >
                  {copied
                    ? <><CheckCircle2 className="size-4 text-emerald-500" /> 복사됨!</>
                    : <><Copy className="size-4" /> 각도 복사</>}
                </Button>
                <p className="text-[11px] text-slate-400 text-center">
                  복사 후 <code className="bg-slate-100 px-1 rounded">CAMERA_VIEW_ANGLES</code>에 붙여넣기
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-4 space-y-2">
                <Button className="w-full" variant="outline" onClick={goHome}>
                  <Home className="size-4" /> 홈으로
                </Button>
                <Button
                  className="w-full bg-indigo-600 hover:bg-indigo-700 text-white"
                  onClick={() => sendAngles(targetAngles)}
                >
                  현재 슬라이더 값으로 이동
                </Button>
              </CardContent>
            </Card>

            <div className="rounded-xl border border-dashed border-slate-200 p-3 text-[12px] text-slate-500 space-y-1">
              <p className="font-semibold text-slate-700">사용 방법</p>
              <p>1. 슬라이더 또는 ±버튼으로 관절 조작</p>
              <p>2. 카메라가 바닥을 볼 때 "각도 복사"</p>
              <p>3. <code className="bg-slate-100 px-1 rounded">arm.py</code>의<br />
                 <code className="bg-slate-100 px-1 rounded">CAMERA_VIEW_ANGLES</code>에 붙여넣기</p>
            </div>
            <ArmKeyboardGuide />
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
