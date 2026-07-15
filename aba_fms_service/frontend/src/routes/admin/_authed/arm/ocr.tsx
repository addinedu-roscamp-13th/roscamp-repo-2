/**
 * OCR 텍스트 인식 — /admin/arm/ocr
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Camera, Info, Loader2, Square, Type, FileText } from "lucide-react";
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

export const Route = createFileRoute("/admin/_authed/arm/ocr")({ component: OcrPage });

interface OcrResult {
  text: string;
  bbox: number[][];
  conf: number;
}

interface OcrDetection {
  results: OcrResult[];
}

function OcrPage() {
  const robotBase = useActiveRobotBase();
  const [running, setRunning] = useState(false);
  const [demoMode, setDemoMode] = useState(true);
  const [detection, setDetection] = useState<OcrDetection | null>(null);
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
        if (msg.type !== "camera") {
          console.log("[ocr.tsx] WS Message received:", msg);
        }
        if (msg.type === "state") {
          setDemoMode(!!msg.demo_mode);
          setJoints(msg.joints ?? Array(7).fill(0));
          if (msg.joints?.length >= 6) syncAngles(msg.joints);
          
          if (msg.mode === "ocr") {
            setRunning(true);
            if (msg.detection) {
              setDetection(msg.detection as OcrDetection);
            }
          } else {
            setRunning(false);
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

  const toggleOcr = async () => {
    if (running) {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/ocr/stop"), { method: "POST" });
      setRunning(false);
      setDetection(null);
    } else {
      await fetch(buildRobotHttpUrl(robotBase, "/api/arm/ocr/start"), { method: "POST" });
      setRunning(true);
    }
  };

  const stopAll = async () => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
    setRunning(false);
    setDetection(null);
  };

  return (
    <AdminShell title="OCR 텍스트 인식">
      <div className="space-y-5">
        {/* 헤더 */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">OCR 텍스트 인식</h1>
            <p className="text-[13px] text-slate-500">
              EasyOCR 엔진을 사용하여 로봇팔 카메라 뷰포인트의 한글 및 영문 텍스트를 실시간 인식합니다.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Label htmlFor="ocr-toggle" className="text-[13px] font-medium text-slate-700">
              {running ? "인식 중" : "인식 꺼짐"}
            </Label>
            <Switch id="ocr-toggle" checked={running} onCheckedChange={toggleOcr} />
          </div>
        </div>

        {/* 데모 모드 경고 배너 */}
        {demoMode && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <Info className="size-4 shrink-0" />
            <span><strong>Demo 모드</strong> — 실제 로봇팔 하드웨어 및 카메라가 감지되지 않아 시뮬레이션으로 동작합니다.</span>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          {/* 카메라 뷰 */}
          <div className="lg:col-span-2 space-y-4">
            <Card className="overflow-hidden border-slate-200 shadow-sm">
              <CardHeader className="pb-2 bg-slate-50/50 border-b border-slate-100">
                <CardTitle className="flex items-center justify-between text-[14px]">
                  <span className="flex items-center gap-2 font-semibold text-slate-800">
                    <Camera className="size-4 text-blue-500 animate-pulse" />
                    실시간 카메라 스트림
                  </span>
                  <span className="text-[11px] font-normal text-slate-400">
                    {fps > 0 && `${fps} FPS · `}640×480
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="aspect-video bg-slate-950 flex items-center justify-center overflow-hidden relative">
                  {camFrame ? (
                    <img src={camFrame} className="w-full h-full object-contain" alt="camera" />
                  ) : (
                    <div className="text-center text-slate-500">
                      <Camera className="size-8 mx-auto mb-2 opacity-30" />
                      <p className="text-[12px]">
                        {running ? "카메라 스트림을 기다리는 중..." : "텍스트 인식 시작 버튼을 눌러주세요"}
                      </p>
                    </div>
                  )}

                  {/* 인식 중 배지 */}
                  {running && (
                    <div className="absolute top-3 right-3 flex items-center gap-1.5 bg-blue-500/90 text-white text-[11px] px-2.5 py-1 rounded-full shadow-sm">
                      <span className="h-1.5 w-1.5 rounded-full bg-white animate-ping" />
                      OCR ACTIVE
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* 하단 제어 제스처 */}
            <div className="space-y-2">
              <Button
                className="w-full justify-center text-slate-700 border-slate-200 hover:bg-slate-50 hover:text-slate-900 transition-colors"
                variant="outline"
                onClick={() => fetch(buildRobotHttpUrl(robotBase, "/api/arm/camera-view"), {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ preset: 1 })
                })}
              >
                <Camera className="size-4 mr-2" /> OCR 카메라 뷰 포지션으로 이동
              </Button>
              
              <div className="flex gap-2">
                <Button
                  className={cn(
                    "flex-1 justify-center transition-all duration-300",
                    running ? "bg-slate-200 text-slate-700 hover:bg-slate-300" : "bg-blue-600 text-white hover:bg-blue-700 shadow-sm"
                  )}
                  variant={running ? "outline" : "default"}
                  onClick={toggleOcr}
                >
                  {running ? (
                    <>
                      <Square className="size-4 mr-2" /> OCR 중지
                    </>
                  ) : (
                    <>
                      <Type className="size-4 mr-2" /> OCR 시작
                    </>
                  )}
                </Button>
                <Button variant="destructive" onClick={stopAll} className="shadow-sm">
                  <Square className="size-4 mr-2" /> 긴급 정지
                </Button>
              </div>
            </div>
          </div>

          {/* 정보 패널 */}
          <div className="space-y-4">
            {/* 실시간 텍스트 감지 목록 */}
            <Card className="border-slate-200 shadow-sm">
              <CardHeader className="pb-2 bg-slate-50/50 border-b border-slate-100">
                <CardTitle className="text-[14px] font-semibold text-slate-800 flex items-center gap-1.5">
                  <FileText className="size-4 text-blue-500" />
                  감지된 텍스트 결과
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-4">
                {running ? (
                  detection && detection.results && detection.results.length > 0 ? (
                    <div className="space-y-2">
                      <div className="flex items-center gap-1.5 text-blue-600 text-[12px] font-medium mb-3">
                        <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                        총 {detection.results.length}개의 텍스트 블록 감지됨
                      </div>
                      <div className="max-h-[220px] overflow-y-auto space-y-1.5 pr-1">
                        {detection.results.map((item, index) => (
                          <div
                            key={`${item.text}-${index}`}
                            className="flex items-center justify-between rounded-lg bg-slate-50 border border-slate-100 px-3 py-2 text-[12px] transition-all hover:bg-slate-100/70"
                          >
                            <span className="font-semibold text-slate-700 truncate max-w-[150px]">{item.text}</span>
                            <span className="text-[11px] font-mono text-slate-400 font-medium">{(item.conf * 100).toFixed(0)}% 신뢰</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-8 text-[12px] text-slate-400">
                      <Loader2 className="size-5 animate-spin text-blue-400 mb-2" />
                      <span>텍스트를 스캔하고 있습니다...</span>
                    </div>
                  )
                ) : (
                  <div className="text-center py-8 text-[12px] text-slate-400">
                    인식을 실행하면 감지 결과가 여기에 표시됩니다.
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 관절 상태 모니터링 */}
            <Card className="border-slate-200 shadow-sm">
              <CardHeader className="pb-2 bg-slate-50/50 border-b border-slate-100">
                <CardTitle className="text-[14px] font-semibold text-slate-800">로봇 관절 위치 모니터</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 pt-4">
                {["J1 베이스", "J2 숄더"].map((label, i) => (
                  <div key={label}>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-slate-500 font-medium">{label}</span>
                      <span className="font-mono font-semibold text-slate-700">{(joints[i] ?? 0).toFixed(1)}°</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-slate-100 relative overflow-hidden">
                      <div className="absolute inset-y-0 left-1/2 w-px bg-slate-200" />
                      <div
                        className={cn(
                          "absolute top-0 h-full rounded-full transition-all duration-150",
                          running ? "bg-blue-500" : "bg-slate-300"
                        )}
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

            {/* 작동 원리 안내 카드 */}
            <div className="rounded-xl border border-dashed border-blue-200 bg-blue-50/50 p-4.5 text-[12px] text-blue-700 space-y-2">
              <p className="font-semibold flex items-center gap-1">
                <Info className="size-3.5" />
                동작 흐름
              </p>
              <ol className="list-decimal list-inside space-y-2 text-[11px] opacity-90 pl-0.5">
                <li>사용자가 웹 GUI 화면에서 <strong>"OCR 시작"</strong> 또는 <strong>"OCR 카메라 뷰 포지션으로 이동"</strong>을 누릅니다.</li>
                <li>로봇팔이 물체(상자 라벨 등)를 판독하기 가장 좋은 사선 아래 방향 각도(CAMERA_VIEW_ANGLES)로 이동합니다.</li>
                <li>백엔드에서 실시간 카메라 영상 프레임을 받아 EasyOCR 엔진을 통해 이미지 내 한글/영문 텍스트 및 바운딩 박스를 탐색합니다.</li>
                <li>인식된 문자 정보(예: 모델명, 사이즈 등)와 신뢰도(Confidence %) 데이터를 웹 GUI의 결과 목록 창에 실시간으로 표시해 줍니다.</li>
                <li>실제 스마트 스토어 시나리오 연계 시, 이 문자 판독 결과에 따라 로봇팔이 상자를 분류 슬롯별로 다르게 적재(정렬)하는 기초 비전 데이터로 사용됩니다.</li>
              </ol>
            </div>
          </div>
        </div>
        <ArmKeyboardGuide />
      </div>
    </AdminShell>
  );
}
