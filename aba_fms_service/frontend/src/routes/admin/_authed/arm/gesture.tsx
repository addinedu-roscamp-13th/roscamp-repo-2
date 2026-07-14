/**
 * 제스처 제어 — /admin/arm/gesture
 *
 * 제스처 → 동작 매핑:
 *   0손가락(주먹)  → 대기 (그리퍼는 핀치로 제어)
 *   1손가락        → J1 +15° (오른쪽 회전)
 *   2손가락        → J1 -15° (왼쪽 회전)
 *   3손가락        → J2 +15° (팔 올리기)
 *   4손가락        → J2 -15° (팔 내리기)
 *   5손가락(손바닥) → 홈 복귀
 *   흔들기         → 긴급 정지
 *   엄지-검지 간격 → 그리퍼 개폐
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Camera, Hand, Info, Square } from "lucide-react";
import { AdminShell } from "@/components/admin/AdminShell";
import { ArmKeyboardGuide } from "@/components/admin/ArmKeyboardGuide";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useArmKeyboard } from "@/hooks/useArmKeyboard";
import { useCameraFrame } from "@/hooks/useCameraFrame";
import { buildRobotHttpUrl, buildRobotWsUrl, useActiveRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/arm/gesture")({ component: GesturePage });

const GESTURE_MAP = [
  { fingers: 0,  label: "✊ 주먹",     action: "대기 (핀치로 그리퍼 조정)",   color: "bg-slate-100 text-slate-700" },
  { fingers: 1,  label: "☝️ 1손가락",  action: "인사하기 — 위아래 3회",       color: "bg-green-100 text-green-700" },
  { fingers: 2,  label: "✌️ 2손가락",  action: "세로 이동 — 위아래 2회",      color: "bg-blue-100 text-blue-700" },
  { fingers: 3,  label: "🤟 3손가락",  action: "J2 +15° 팔 올리기",           color: "bg-purple-100 text-purple-700" },
  { fingers: 4,  label: "🖖 4손가락",  action: "J2 -15° 팔 내리기",           color: "bg-orange-100 text-orange-700" },
  { fingers: 5,  label: "✋ 손바닥",   action: "홈 포지션 복귀",              color: "bg-amber-100 text-amber-700" },
  { fingers: -1, label: "🌊 흔들기",   action: "긴급 정지",                   color: "bg-red-100 text-red-700" },
] as const;

interface GestureDetection { fingers: number; pinch: number; cx: number; cy: number }

function GesturePage() {
  const robotBase = useActiveRobotBase();
  const [active, setActive] = useState(false);
  const [demoMode, setDemoMode] = useState(true);
  const [detection, setDetection] = useState<GestureDetection | null>(null);
  const { frameUrl: camFrame, pushFrame } = useCameraFrame();
  const [joints, setJoints] = useState<number[]>(Array(6).fill(0));
  const [gripper, setGripper] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const { syncAngles } = useArmKeyboard();

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setDemoMode(!!msg.demo_mode);
          setJoints(msg.joints ?? Array(6).fill(0));
          if (msg.joints?.length >= 6) syncAngles(msg.joints);
          setGripper(msg.gripper ?? 0);
          if (msg.mode === "gesture" && msg.detection) {
            setDetection(msg.detection as GestureDetection);
          } else if (msg.mode !== "gesture") {
            setDetection(null);
          }
        }
        if (msg.type === "camera") pushFrame(msg.frame);
      } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [robotBase]);

  const toggle = async () => {
    if (active) {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/gesture/stop"), { method: "POST" });
      setActive(false);
      setDetection(null);
    } else {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/gesture/start"), { method: "POST" });
      setActive(true);
    }
  };

  const stopAll = async () => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
    setActive(false);
    setDetection(null);
  };

  const fingers = detection?.fingers ?? -2;
  const pinchPct = Math.round((detection?.pinch ?? 0) * 100);
  const activeFingersMap = GESTURE_MAP.find((m) => m.fingers === fingers);

  return (
    <AdminShell title="제스처 제어">
      <div className="space-y-5">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">제스처 제어</h1>
            <p className="text-[13px] text-slate-500">손 제스처로 로봇팔 동작을 명령합니다.</p>
          </div>
          <div className="flex items-center gap-3">
            <Label htmlFor="gesture-toggle" className="text-[13px] font-medium">
              {active ? "제스처 인식 중" : "꺼짐"}
            </Label>
            <Switch id="gesture-toggle" checked={active} onCheckedChange={toggle} />
          </div>
        </div>

        {demoMode && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <Info className="size-4 shrink-0" />
            Demo 모드 — MediaPipe 없음. 실제 로봇팔 연결 시 동작합니다.
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          {/* 카메라 뷰 */}
          <div className="lg:col-span-2 space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between text-[14px]">
                  <span className="flex items-center gap-2">
                    <Hand className="size-4 text-purple-500" /> 카메라 뷰
                  </span>
                  {active && (
                    <span className="text-[11px] font-normal text-purple-600 flex items-center gap-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-purple-500 animate-pulse" />
                      인식 중
                    </span>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="aspect-video rounded-lg bg-slate-900 flex items-center justify-center overflow-hidden relative">
                  {camFrame
                    ? <img src={camFrame} className="w-full h-full object-cover" alt="camera" />
                    : (
                      <div className="text-center text-slate-500">
                        <Hand className="size-8 mx-auto mb-2 opacity-30" />
                        <p className="text-[12px]">{active ? "카메라 스트림 대기 중..." : "제스처 제어를 시작해주세요"}</p>
                      </div>
                    )}

                  {/* 감지된 손가락 수 + 핀치 오버레이 */}
                  {active && detection && fingers >= 0 && (
                    <div className="absolute inset-0 pointer-events-none">
                      {/* 핀치(그리퍼) 바 */}
                      <div className="absolute bottom-0 left-0 right-0 px-3 pb-2">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[11px] text-white/80">그리퍼</span>
                          <div className="flex-1 h-2 rounded-full bg-white/20 overflow-hidden">
                            <div
                              className="h-full rounded-full bg-orange-400 transition-all duration-150"
                              style={{ width: `${pinchPct}%` }}
                            />
                          </div>
                          <span className="text-[11px] text-orange-300 font-mono">{pinchPct}%</span>
                        </div>
                      </div>
                      {/* 동작 설명 */}
                      {activeFingersMap && (
                        <div className="absolute top-2 left-2 right-2 bg-black/60 rounded-lg px-3 py-1.5 text-white text-[12px]">
                          {activeFingersMap.label} → {activeFingersMap.action}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <div className="space-y-2">
              <Button className="w-full" variant="outline"
                onClick={() => fetch(buildRobotHttpUrl(robotBase, "/api/arm/face-view"), { method: "POST" })}>
                <Camera className="size-4" /> 뷰 포지션으로 이동
              </Button>
              <div className="flex gap-2">
                <Button className="flex-1" variant={active ? "outline" : "default"} onClick={toggle}>
                  {active
                    ? <><Square className="size-4" /> 제스처 제어 중지</>
                    : <><Hand className="size-4" /> 제스처 제어 시작</>}
                </Button>
                <Button variant="destructive" onClick={stopAll}>
                  <Square className="size-4" /> 긴급 정지
                </Button>
              </div>
            </div>
          </div>

          {/* 사이드바 */}
          <div className="space-y-4">
            {/* 제스처 매핑 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">제스처 매핑</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {GESTURE_MAP.map((m) => {
                  const isActive = m.fingers === fingers;
                  return (
                    <div
                      key={m.label}
                      className={cn(
                        "flex items-center justify-between rounded-lg px-3 py-2 text-[12px] transition-all",
                        isActive ? m.color + " ring-1 ring-current/30 scale-[1.02]" : "bg-slate-50",
                      )}
                    >
                      <span className="font-medium">{m.label}</span>
                      <span className={cn("text-[11px]", isActive ? "" : "text-slate-500")}>{m.action}</span>
                    </div>
                  );
                })}
                <div className="flex items-center justify-between rounded-lg px-3 py-2 text-[12px] bg-slate-50">
                  <span className="font-medium">👌 핀치</span>
                  <span className="text-slate-500">그리퍼 개폐 (간격 비례)</span>
                </div>
              </CardContent>
            </Card>

            {/* 관절 상태 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">관절 상태</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-[12px]">
                {["J1 베이스", "J2 숄더"].map((label, i) => (
                  <div key={label}>
                    <div className="flex justify-between mb-0.5">
                      <span className="text-slate-500">{label}</span>
                      <span className="font-mono font-semibold">{(joints[i] ?? 0).toFixed(1)}°</span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 relative overflow-hidden">
                      <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
                      <div
                        className="absolute top-0 h-full rounded-full bg-purple-400 transition-all duration-100"
                        style={{
                          width: `${Math.abs(joints[i] ?? 0) / 90 * 50}%`,
                          left: (joints[i] ?? 0) >= 0 ? "50%" : `${50 - Math.abs(joints[i] ?? 0) / 90 * 50}%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
                <div>
                  <div className="flex justify-between mb-0.5">
                    <span className="text-slate-500">그리퍼</span>
                    <span className="font-mono font-semibold">{gripper.toFixed(0)}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                    <div className="h-full rounded-full bg-orange-400 transition-all duration-200" style={{ width: `${gripper}%` }} />
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
        <ArmKeyboardGuide />
      </div>
    </AdminShell>
  );
}
