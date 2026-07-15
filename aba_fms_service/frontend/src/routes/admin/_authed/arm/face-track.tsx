/**
 * 얼굴 추적 — /admin/arm/face-track
 *
 * 동작 흐름:
 *   1. "추적 시작" 클릭 → POST /api/arm/face-track/start
 *   2. 백엔드가 카메라로 얼굴 감지 → 팔이 얼굴을 따라 이동
 *   3. WS로 실시간 detection 좌표 수신 → 카메라 오버레이 표시
 *   4. "추적 중지" 클릭 → POST /api/arm/face-track/stop
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Camera, Info, Loader2, Square, User } from "lucide-react";
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

export const Route = createFileRoute("/admin/_authed/arm/face-track")({ component: FaceTrackPage });

interface FaceDetection { faces: number; cx: number; cy: number; distance_cm: number }

function FaceTrackPage() {
  const robotBase = useActiveRobotBase();
  const [tracking, setTracking] = useState(false);
  const [demoMode, setDemoMode] = useState(true);
  const [detection, setDetection] = useState<FaceDetection | null>(null);
  const [joints, setJoints] = useState<number[]>(Array(7).fill(0));
  const { frameUrl: camFrame, pushFrame } = useCameraFrame();
  const [fps, setFps] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const fpsRef = useRef<{ count: number; t: number }>({ count: 0, t: Date.now() });
  const { syncAngles } = useArmKeyboard();

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setDemoMode(!!msg.demo_mode);
          setJoints(msg.joints ?? Array(7).fill(0));
          if (msg.joints?.length >= 6) syncAngles(msg.joints);
          if (msg.mode === "face_track" && msg.detection) {
            setDetection(msg.detection as FaceDetection);
          } else if (msg.mode !== "face_track") {
            setDetection(null);
          }
        }
        if (msg.type === "camera") {
          pushFrame(msg.frame);
          fpsRef.current.count++;
          const now = Date.now();
          if (now - fpsRef.current.t >= 1000) {
            setFps(fpsRef.current.count);
            fpsRef.current = { count: 0, t: now };
          }
        }
      } catch { /* ignore */ }
    };
    return () => { ws.close(); };
  }, [robotBase]);

  const toggleTracking = async () => {
    if (tracking) {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/face-track/stop"), { method: "POST" });
      setTracking(false);
      setDetection(null);
    } else {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/face-track/start"), { method: "POST" });
      setTracking(true);
    }
  };

  const stopAll = async () => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
    setTracking(false);
    setDetection(null);
  };

  // 카메라 화면 위 bounding box 위치 (640×480 기준)
  const boxStyle = detection ? {
    left:   `${((detection.cx - 60) / 640) * 100}%`,
    top:    `${((detection.cy - 80) / 480) * 100}%`,
    width:  `${(120 / 640) * 100}%`,
    height: `${(160 / 480) * 100}%`,
  } : undefined;

  return (
    <AdminShell title="얼굴 추적">
      <div className="space-y-5">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">얼굴 추적</h1>
            <p className="text-[13px] text-slate-500">카메라로 얼굴을 감지하고 로봇팔이 실시간으로 따라 움직입니다.</p>
          </div>
          <div className="flex items-center gap-3">
            <Label htmlFor="track-toggle" className="text-[13px] font-medium">
              {tracking ? "추적 중" : "추적 꺼짐"}
            </Label>
            <Switch id="track-toggle" checked={tracking} onCheckedChange={toggleTracking} />
          </div>
        </div>

        {demoMode && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <Info className="size-4 shrink-0" />
            Demo 모드 — 얼굴 추적 시뮬레이션 중입니다. (실제 카메라 없음)
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          {/* 카메라 뷰 */}
          <div className="lg:col-span-2 space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between text-[14px]">
                  <span className="flex items-center gap-2">
                    <Camera className="size-4 text-orange-500" />
                    카메라 뷰
                  </span>
                  <span className="text-[11px] font-normal text-slate-400">
                    {fps > 0 && `${fps} FPS · `}640×480
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="aspect-video rounded-lg bg-slate-900 flex items-center justify-center overflow-hidden relative">
                  {camFrame
                    ? <img src={camFrame} className="w-full h-full object-cover" alt="camera" />
                    : (
                      <div className="text-center text-slate-500">
                        <Camera className="size-8 mx-auto mb-2 opacity-30" />
                        <p className="text-[12px]">{tracking ? "카메라 스트림 대기 중..." : "추적 시작 버튼을 눌러주세요"}</p>
                      </div>
                    )}

                  {/* 얼굴 bounding box */}
                  {tracking && detection && boxStyle && (
                    <div className="absolute border-2 border-orange-400 rounded-sm transition-all duration-100"
                      style={boxStyle}>
                      <div className="absolute -top-5 left-0 bg-orange-500 text-white text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap">
                        얼굴 ({detection.distance_cm}cm)
                      </div>
                    </div>
                  )}

                  {/* 추적 중 표시 */}
                  {tracking && (
                    <div className="absolute top-2 right-2 flex items-center gap-1.5 bg-red-500/90 text-white text-[11px] px-2 py-1 rounded-full">
                      <span className="h-1.5 w-1.5 rounded-full bg-white animate-pulse" />
                      TRACKING
                    </div>
                  )}

                  {/* 십자선 */}
                  {tracking && (
                    <>
                      <div className="absolute inset-x-0 top-1/2 h-px bg-orange-400/30 pointer-events-none" />
                      <div className="absolute inset-y-0 left-1/2 w-px bg-orange-400/30 pointer-events-none" />
                    </>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* 제어 버튼 */}
            <div className="space-y-2">
              <Button className="w-full" variant="outline"
                onClick={() => fetch(buildRobotHttpUrl(robotBase, "/api/arm/face-view"), { method: "POST" })}>
                <Camera className="size-4" /> 얼굴 추적 뷰 포지션 (왼쪽)
              </Button>
              <div className="flex gap-2">
                <Button className="flex-1" variant={tracking ? "outline" : "default"} onClick={toggleTracking}>
                  {tracking
                    ? <><Square className="size-4" /> 추적 중지</>
                    : <><User className="size-4" /> 추적 시작</>}
                </Button>
                <Button variant="destructive" onClick={stopAll}>
                  <Square className="size-4" /> 긴급 정지
                </Button>
              </div>
            </div>
          </div>

          {/* 오른쪽: 감지 정보 + 관절 */}
          <div className="space-y-4">
            {/* 감지 정보 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">감지 정보</CardTitle>
              </CardHeader>
              <CardContent>
                {tracking && detection ? (
                  <div className="space-y-2 text-[12px]">
                    <div className="flex items-center gap-1.5 text-orange-600 font-medium mb-2">
                      <span className="h-2 w-2 rounded-full bg-orange-500 animate-pulse" />
                      얼굴 {detection.faces}명 감지
                    </div>
                    {[
                      ["얼굴 중심 X", `${detection.cx}px`],
                      ["얼굴 중심 Y", `${detection.cy}px`],
                      ["추정 거리",   `${detection.distance_cm} cm`],
                    ].map(([k, v]) => (
                      <div key={String(k)} className="flex justify-between">
                        <span className="text-slate-500">{String(k)}</span>
                        <span className="font-mono font-semibold">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-[13px] text-slate-400">
                    {tracking
                      ? <><Loader2 className="size-4 animate-spin" /> 얼굴 탐색 중...</>
                      : "추적 꺼짐"}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 관절 상태 */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">관절 상태</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {["J1 (베이스)", "J2 (숄더)"].map((label, i) => (
                  <div key={label}>
                    <div className="flex justify-between text-[12px] mb-0.5">
                      <span className="text-slate-500">{label}</span>
                      <span className="font-mono font-semibold">{(joints[i] ?? 0).toFixed(1)}°</span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 relative overflow-hidden">
                      <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
                      <div
                        className={cn("absolute top-0 h-full rounded-full transition-all duration-100",
                          tracking ? "bg-orange-400" : "bg-slate-300")}
                        style={{
                          width: `${Math.abs(joints[i] ?? 0) / 180 * 50}%`,
                          left: (joints[i] ?? 0) >= 0 ? "50%" : `${50 - Math.abs(joints[i] ?? 0) / 180 * 50}%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* 안내 */}
            <div className="rounded-xl border border-dashed border-orange-200 bg-orange-50/50 p-3 text-[12px] text-orange-700 space-y-1">
              <p className="font-semibold">얼굴 추적 원리 (OpenCV)</p>
              <p>• Haar Cascade 또는 DNN 얼굴 감지</p>
              <p>• 얼굴 중심 → 화면 중앙 오차 계산</p>
              <p>• P 제어로 J1/J2 각도 실시간 보정</p>
            </div>
          </div>
        </div>
        <ArmKeyboardGuide />
      </div>
    </AdminShell>
  );
}
