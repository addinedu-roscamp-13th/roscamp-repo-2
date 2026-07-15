/**
 * 로봇팔 대시보드 — /admin/arm
 *
 * gazebo_fastapi 의 로봇팔(JetCobot) 모듈을 bot_ai_server 관리자(_authed)로 이식.
 * 이식 시 변경한 것: AdminShell import 경로 + createFileRoute 경로 뿐.
 * 나머지 로직(API URL, WebSocket, 상태 타입)은 원본 그대로.
 */
import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  Camera, Cpu, Hand, Info, RefreshCw, ScanEye, Square, Type, Wifi, WifiOff, Zap,
} from "lucide-react";
import { AdminShell } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buildRobotHttpUrl, buildRobotWsUrl, useActiveRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/arm/")({ component: ArmDashboard });

// ── 타입 ─────────────────────────────────────────────────────
export interface ArmState {
  connected: boolean;
  demo_mode: boolean;
  mode: "idle" | "homing" | "color_pick" | "face_track" | "gesture" | "ocr" | "playback";
  joints: number[];      // degrees ×7
  gripper: number;       // 0=열림 100=닫힘
  detection: Record<string, unknown> | null;
}

const JOINT_LABELS = ["J1 베이스", "J2 숄더", "J3 엘보", "J4 리스트1", "J5 리스트2", "J6 리스트3", "J7 그리퍼"];
const MODE_LABEL: Record<ArmState["mode"], string> = {
  idle: "대기", homing: "홈 이동 중", color_pick: "색상 집기", face_track: "얼굴 추적", gesture: "제스처 제어", ocr: "OCR 인식", playback: "모션 재생 중",
};
const MODE_COLOR: Record<ArmState["mode"], string> = {
  idle: "bg-slate-100 text-slate-600",
  homing: "bg-blue-100 text-blue-700",
  color_pick: "bg-green-100 text-green-700",
  face_track: "bg-orange-100 text-orange-700",
  gesture: "bg-purple-100 text-purple-700",
  ocr: "bg-blue-100 text-blue-700",
  playback: "bg-rose-100 text-rose-700",
};

// ── WebSocket 훅 ──────────────────────────────────────────────
export function useArmWS() {
  const robotBase = useActiveRobotBase();
  const wsRef = useRef<WebSocket | null>(null);
  const [wsOk, setWsOk] = useState(false);
  const [state, setState] = useState<ArmState>({
    connected: false, demo_mode: true, mode: "idle",
    joints: Array(7).fill(0), gripper: 0, detection: null,
  });

  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onopen = () => setWsOk(true);
    ws.onclose = () => setWsOk(false);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") setState(msg as ArmState);
      } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [robotBase]);

  const send = (obj: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify(obj));
  };
  return { wsOk, state, send };
}

// ── 관절 게이지 ───────────────────────────────────────────────
function JointBar({ label, value }: { label: string; value: number }) {
  const pct = Math.abs(value) / 180 * 100;
  return (
    <div>
      <div className="flex justify-between text-[12px] mb-0.5">
        <span className="text-slate-500">{label}</span>
        <span className="font-mono font-semibold text-slate-700">{value.toFixed(1)}°</span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 relative overflow-hidden">
        <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
        <div
          className="absolute top-0 h-full rounded-full bg-indigo-400 transition-all duration-100"
          style={{
            width: `${pct / 2}%`,
            left: value >= 0 ? "50%" : `${50 - pct / 2}%`,
          }}
        />
      </div>
    </div>
  );
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────
function ArmDashboard() {
  const robotBase = useActiveRobotBase();
  const { wsOk, state, send } = useArmWS();
  const [loading, setLoading] = useState(false);

  const callAPI = async (path: string, body?: object) => {
    setLoading(true);
    try { await fetch(buildRobotHttpUrl(robotBase, "/api/arm" + path), { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); }
    finally { setLoading(false); }
  };

  const FEATURE_CARDS = [
    { to: "/admin/arm/joint-control", icon: Zap,      label: "관절 수동 제어",   desc: "슬라이더로 각 관절을 직접 조작, 포지션 좌표 확인", color: "text-indigo-600 bg-indigo-50 border-indigo-200" },
    { to: "/admin/arm/color-pick",    icon: ScanEye,  label: "색상 블록 집기",   desc: "카메라로 색상 감지 후 팔이 집어올림",             color: "text-green-600 bg-green-50 border-green-200" },
    { to: "/admin/arm/face-track",    icon: Camera,   label: "얼굴 추적",        desc: "카메라로 얼굴을 감지해 팔이 따라 움직임",         color: "text-orange-600 bg-orange-50 border-orange-200" },
    { to: "/admin/arm/gesture",       icon: Hand,     label: "제스처 제어",      desc: "손 제스처로 로봇팔 동작을 명령",                  color: "text-purple-600 bg-purple-50 border-purple-200" },
    { to: "/admin/arm/ocr",           icon: Type,     label: "OCR 텍스트 인식",  desc: "카메라로 텍스트를 감지하여 모니터링",             color: "text-blue-600 bg-blue-50 border-blue-200" },
    { to: "/admin/arm/playback",      icon: RefreshCw, label: "모션 티칭 및 재생",  desc: "로봇팔 모션을 학습시키고 자동 재생",               color: "text-rose-600 bg-rose-50 border-rose-200" },
  ];

  return (
    <AdminShell title="로봇팔 JetCobot">
      <div className="space-y-5">
        {/* 헤더 */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">JetCobot 로봇팔 대시보드</h1>
            <p className="text-[13px] text-slate-500">7축 비전 협동 로봇팔 제어 · Yahboom JetCobot</p>
          </div>
          <div className="flex items-center gap-2">
            {wsOk
              ? <span className="flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-[12px] font-medium text-emerald-600"><Wifi className="size-3" /> WS 연결됨</span>
              : <span className="flex items-center gap-1.5 rounded-full bg-red-50 px-3 py-1 text-[12px] font-medium text-red-600"><WifiOff className="size-3" /> 연결 안됨</span>}
            <Badge className={cn("text-[11px]", MODE_COLOR[state.mode])}>{MODE_LABEL[state.mode]}</Badge>
          </div>
        </div>

        {/* 데모 모드 배너 */}
        {state.demo_mode && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-700">
            <Info className="size-4 shrink-0" />
            <span><strong>Demo 모드</strong> — JetCobot 하드웨어가 연결되어 있지 않습니다. 시뮬레이션으로 동작합니다.</span>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          {/* 관절 상태 */}
          <Card className="lg:col-span-2">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-[14px]">
                <Cpu className="size-4 text-indigo-500" /> 관절 각도 (7축)
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {JOINT_LABELS.map((label, i) => (
                <JointBar key={label} label={label} value={i === 6 ? state.gripper : state.joints[i] ?? 0} />
              ))}
              <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                <span>-180°</span><span className="text-slate-500 font-medium">0°</span><span>+180°</span>
              </div>
            </CardContent>
          </Card>

          {/* 제어 패널 */}
          <div className="space-y-3">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">빠른 제어</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Button className="w-full" variant="outline" disabled={loading}
                  onClick={() => send({ action: "home" })}>
                  <RefreshCw className="size-4" /> 홈 포지션 복귀
                </Button>
                <Button className="w-full" variant="destructive" disabled={loading}
                  onClick={() => send({ action: "stop" })}>
                  <Square className="size-4" /> 긴급 정지
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-[14px]">그리퍼</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between text-[13px] mb-2">
                  <span className="text-slate-500">상태</span>
                  <span className="font-mono font-semibold">{state.gripper.toFixed(0)}%</span>
                </div>
                <div className="h-3 rounded-full bg-slate-100 overflow-hidden">
                  <div className="h-full rounded-full bg-indigo-400 transition-all duration-200"
                    style={{ width: `${state.gripper}%` }} />
                </div>
                <p className="mt-1.5 text-[11px] text-slate-400 text-center">
                  {state.gripper < 20 ? "열림" : state.gripper > 80 ? "닫힘" : "중간"}
                </p>
              </CardContent>
            </Card>

            {/* 연결 상태 */}
            <Card>
              <CardContent className="pt-4 space-y-1.5 text-[12px]">
                {[
                  ["하드웨어", state.connected ? "연결됨" : "데모 모드"],
                  ["현재 모드", MODE_LABEL[state.mode]],
                  ["관절 수", "7축 (6DOF + 그리퍼)"],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <span className="text-slate-500">{k}</span>
                    <span className="font-medium text-slate-700">{v}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>

        {/* 기능 카드 */}
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {FEATURE_CARDS.map(({ to, icon: Icon, label, desc, color }) => (
            <Link key={to} to={to}
              className={cn("rounded-xl border p-4 hover:shadow-md transition-shadow cursor-pointer", color)}>
              <Icon className="size-6 mb-2" />
              <p className="font-semibold text-[14px]">{label}</p>
              <p className="text-[12px] opacity-75 mt-0.5">{desc}</p>
            </Link>
          ))}
        </div>

        {/* 학습 노트 */}
        <div className="rounded-xl border border-dashed border-indigo-200 bg-indigo-50/50 p-4">
          <p className="text-[12px] font-semibold text-indigo-700 mb-2"><Zap className="size-3 inline mr-1" />JetCobot 하드웨어 연동 포인트</p>
          <div className="grid gap-2 sm:grid-cols-2 text-[12px] text-indigo-600">
            <div>
              <p className="font-mono bg-indigo-100 px-2 py-1 rounded mb-1">backend/app/routers/arm.py</p>
              <p>• ArmBridge 클래스 → 실제 SDK 교체</p>
              <p>• Serial: /dev/ttyUSB0 @ 115200</p>
            </div>
            <div>
              <p className="font-mono bg-indigo-100 px-2 py-1 rounded mb-1">카메라 (OpenCV)</p>
              <p>• cv2.VideoCapture(0) 으로 교체</p>
              <p>• _demo_camera_frame() 함수 내 TODO</p>
            </div>
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
