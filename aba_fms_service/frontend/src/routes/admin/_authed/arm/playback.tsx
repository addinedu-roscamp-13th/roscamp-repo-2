/**
 * 모션 티칭 및 재생 — /admin/arm/playback
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, Copy, Home, Minus, Plus, Wifi, WifiOff, Trash2, Play, Save,
  RotateCcw, PlusCircle, Download, List, Move, SlidersHorizontal, Loader2, StopCircle
} from "lucide-react";
import { useArmKeyboard } from "@/hooks/useArmKeyboard";
import { AdminShell } from "@/components/admin/AdminShell";
import { ArmKeyboardGuide } from "@/components/admin/ArmKeyboardGuide";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { buildRobotHttpUrl, buildRobotWsUrl, useActiveRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export const Route = createFileRoute("/admin/_authed/arm/playback")({ component: PlaybackPage });

interface Waypoint {
  angles: number[];
  gripper: number;
  speed: number;
  delay: number;
}

interface SavedSequence {
  id: number;
  name: string;
  description: string | null;
  waypoints: Waypoint[];
  created_at: string | null;
}

const JOINTS = [
  { label: "J1 베이스",   min: -168, max: 168 },
  { label: "J2 숄더",    min: -135, max: 135 },
  { label: "J3 엘보",    min: -150, max: 150 },
  { label: "J4 리스트1", min: -145, max: 145 },
  { label: "J5 리스트2", min: -165, max: 165 },
  { label: "J6 리스트3", min: -180, max: 180 },
];

function PlaybackPage() {
  const robotBase = useActiveRobotBase();
  const queryClient = useQueryClient();

  const [realAngles, setRealAngles] = useState<number[]>([0, 0, 0, 0, 0, 0]);
  const [targetAngles, setTargetAngles] = useState<number[]>([0, 0, 0, 0, 0, 0]);
  const [gripper, setGripper] = useState(0);
  const [connected, setConnected] = useState(false);
  const [robotMode, setRobotMode] = useState<string>("idle");
  const [step, setStep] = useState<number>(10);
  
  // 임시 티칭 목록
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);
  
  // 저장용 폼
  const [saveName, setSaveName] = useState("");
  const [saveDesc, setSaveDesc] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const syncedRef = useRef(false);
  const { syncAngles: kbSync } = useArmKeyboard();

  // 1. WebSocket 연결 — 로봇 실시간 각도 및 그리퍼 수신
  useEffect(() => {
    const ws = new WebSocket(buildRobotWsUrl(robotBase, "/api/arm/ws/arm"));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setConnected(msg.connected);
          setRobotMode(msg.mode || "idle");
          if (msg.joints?.length >= 6) {
            setRealAngles(msg.joints.slice(0, 6).map((v: number) => Math.round(v * 10) / 10));
            kbSync(msg.joints);
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

  // 2. 저장된 시퀀스 목록 가져오기
  const { data: savedSequences = [], refetch: refetchSeqs } = useQuery<SavedSequence[]>({
    queryKey: ["arm", "sequences", robotBase],
    queryFn: async () => {
      const res = await fetch(buildRobotHttpUrl(robotBase, "/api/arm/sequences"));
      if (!res.ok) throw new Error("시퀀스 목록 조회 실패");
      return res.json();
    }
  });

  // ── 로봇 제어 API 호출 ──
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

  const stopRobot = async () => {
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/stop"), { method: "POST" });
  };

  // ── 티칭 액션 ──
  const recordWaypoint = () => {
    const newWp: Waypoint = {
      angles: [...realAngles],
      gripper: gripper,
      speed: 25,
      delay: 1.0,
    };
    setWaypoints([...waypoints, newWp]);
    toast.success(`${waypoints.length + 1}번째 웨이포인트 기록 완료!`);
  };

  const removeWaypoint = (idx: number) => {
    setWaypoints(waypoints.filter((_, i) => i !== idx));
  };

  const updateWaypointField = (idx: number, field: keyof Waypoint, value: number) => {
    setWaypoints(
      waypoints.map((wp, i) => (i === idx ? { ...wp, [field]: value } : wp))
    );
  };

  const clearWaypoints = () => {
    setWaypoints([]);
    toast.info("티칭 목록이 초기화되었습니다.");
  };

  // ── 재생/미리보기 액션 ──
  const startPreview = async () => {
    if (waypoints.length === 0) {
      toast.warning("재생할 웨이포인트가 없습니다.");
      return;
    }
    toast.info("임시 시퀀스 미동작(프리뷰) 실행 중...");
    await fetch(buildRobotHttpUrl(robotBase, "/api/arm/playback/preview"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(waypoints),
    });
  };

  // ── DB 시퀀스 CRUD 액션 ──
  const saveSequence = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!saveName.trim()) {
      toast.error("이름을 입력해주세요.");
      return;
    }
    if (waypoints.length === 0) {
      toast.error("저장할 웨이포인트가 존재하지 않습니다.");
      return;
    }

    setIsSaving(true);
    try {
      const res = await fetch(buildRobotHttpUrl(robotBase, "/api/arm/sequences"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: saveName,
          description: saveDesc,
          waypoints,
        }),
      });
      if (!res.ok) throw new Error("저장 실패");
      toast.success(`모션 시퀀스 '${saveName}' 저장 완료!`);
      setSaveName("");
      setSaveDesc("");
      refetchSeqs();
    } catch {
      toast.error("저장에 실패했습니다.");
    } finally {
      setIsSaving(false);
    }
  };

  const playSequence = async (seqId: number, name: string) => {
    toast.info(`'${name}' 시퀀스 자동 재생 실행`);
    await fetch(buildRobotHttpUrl(robotBase, `/api/arm/sequences/${seqId}/playback`), {
      method: "POST",
    });
  };

  const deleteSequence = async (seqId: number) => {
    if (!confirm("정말 이 시퀀스를 삭제하시겠습니까?")) return;
    try {
      const res = await fetch(buildRobotHttpUrl(robotBase, `/api/arm/sequences/${seqId}`), {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("삭제 실패");
      toast.success("시퀀스가 정상적으로 삭제되었습니다.");
      refetchSeqs();
    } catch {
      toast.error("삭제에 실패했습니다.");
    }
  };

  const loadSequence = (seq: SavedSequence) => {
    setWaypoints(seq.waypoints);
    toast.success(`'${seq.name}' 시퀀스를 편집창으로 불러왔습니다.`);
  };

  return (
    <AdminShell title="로봇팔 모션 티칭 및 재생">
      <div className="space-y-5">
        {/* 헤더 */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-[20px] font-bold text-slate-900">모션 티칭 및 재생</h1>
            <p className="text-[13px] text-slate-500">
              슬라이더 및 버튼으로 자세를 잡고 각 좌표를 웨이포인트(경로)로 저장한 후, 일괄 자동 재생합니다.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <BadgeConnected connected={connected} />
            {robotMode !== "idle" && (
              <span className="flex items-center gap-1 text-[11px] font-semibold bg-rose-100 text-rose-700 px-2 py-0.5 rounded animate-pulse">
                {robotMode === "playback" ? "재생 중" : "로봇 동작 중"}
              </span>
            )}
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-3">
          {/* 1. 수동 티칭 및 조그 조작 (왼쪽 + 가운데) */}
          <div className="lg:col-span-2 space-y-4">
            <Card className="border-slate-200 shadow-sm">
              <CardHeader className="pb-3 bg-slate-50/50 border-b border-slate-100 flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-[14px] flex items-center gap-1.5 text-slate-800">
                    <SlidersHorizontal className="size-4 text-indigo-500" />
                    실시간 자세 티칭 및 조그 제어
                  </CardTitle>
                  <CardDescription className="text-[11px] mt-0.5">로봇팔 각 축을 조작하여 원하는 모션을 티칭하세요.</CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-400">단위:</span>
                  {[5, 10, 30].map((s) => (
                    <button
                      key={s}
                      onClick={() => setStep(s)}
                      className={cn(
                        "px-2 py-0.5 rounded text-[11px] font-mono border transition-all",
                        step === s ? "bg-indigo-600 text-white border-indigo-600 font-bold" : "border-slate-200 hover:border-indigo-300 text-slate-500"
                      )}
                    >
                      ±{s}°
                    </button>
                  ))}
                </div>
              </CardHeader>
              <CardContent className="pt-4 space-y-3">
                {JOINTS.map((j, idx) => (
                  <div key={idx} className="flex items-center gap-3">
                    <div className="w-20 shrink-0">
                      <p className="text-[12px] font-bold text-slate-700">{j.label}</p>
                      <p className="text-[10px] text-slate-400 font-mono">실제: {realAngles[idx]?.toFixed(0)}°</p>
                    </div>

                    <Button size="sm" variant="outline" className="size-8 p-0 shrink-0 border-slate-200" onClick={() => jogJoint(idx, -step)}>
                      <Minus className="size-3" />
                    </Button>

                    <input
                      type="range" min={j.min} max={j.max} step={1}
                      value={targetAngles[idx]}
                      className="flex-1 accent-indigo-600 h-1.5 bg-slate-100 rounded-lg appearance-none cursor-pointer"
                      onChange={(e) => handleSlider(idx, Number(e.target.value))}
                      onMouseUp={() => handleSliderCommit(targetAngles)}
                      onTouchEnd={() => handleSliderCommit(targetAngles)}
                    />

                    <Button size="sm" variant="outline" className="size-8 p-0 shrink-0 border-slate-200" onClick={() => jogJoint(idx, +step)}>
                      <Plus className="size-3" />
                    </Button>

                    <div className="w-14 shrink-0 text-right">
                      <span className="text-[13px] font-mono font-bold text-indigo-600">{targetAngles[idx]}°</span>
                    </div>
                  </div>
                ))}

                {/* 그리퍼 */}
                <div className="flex items-center gap-3 pt-2 border-t border-slate-100">
                  <div className="w-20 shrink-0">
                    <p className="text-[12px] font-bold text-emerald-700">그리퍼</p>
                    <p className="text-[10px] text-slate-400 font-mono">실제: {gripper}</p>
                  </div>
                  <Button size="sm" variant="outline" className="size-8 p-0 shrink-0 border-slate-200"
                    onClick={() => { const v = Math.max(0, gripper - 10); setGripper(v); sendGripper(v); }}>
                    <Minus className="size-3" />
                  </Button>
                  <input
                    type="range" min={0} max={100} step={5}
                    value={gripper}
                    className="flex-1 accent-emerald-600 h-1.5 bg-slate-100 rounded-lg appearance-none cursor-pointer"
                    onChange={(e) => setGripper(Number(e.target.value))}
                    onMouseUp={() => sendGripper(gripper)}
                    onTouchEnd={() => sendGripper(gripper)}
                  />
                  <Button size="sm" variant="outline" className="size-8 p-0 shrink-0 border-slate-200"
                    onClick={() => { const v = Math.min(100, gripper + 10); setGripper(v); sendGripper(v); }}>
                    <Plus className="size-3" />
                  </Button>
                  <div className="w-14 shrink-0 text-right">
                    <span className="text-[13px] font-mono font-bold text-emerald-600">{gripper}</span>
                  </div>
                </div>

                {/* 티칭 버튼 패널 */}
                <div className="flex gap-2 pt-3">
                  <Button className="flex-1 bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm" onClick={recordWaypoint}>
                    <PlusCircle className="size-4 mr-2" /> 현재 자세 기록 (웨이포인트)
                  </Button>
                  <Button variant="outline" className="text-slate-700 border-slate-200 hover:bg-slate-50" onClick={stopRobot}>
                    <StopCircle className="size-4 mr-2 text-rose-500" /> 긴급 정지
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* 임시 기록된 웨이포인트 목록 */}
            <Card className="border-slate-200 shadow-sm">
              <CardHeader className="pb-2 bg-slate-50/50 border-b border-slate-100 flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-[14px] flex items-center gap-1.5 text-slate-800">
                    <List className="size-4 text-rose-500" />
                    기록 중인 모션 시퀀스 ({waypoints.length}단계)
                  </CardTitle>
                </div>
                {waypoints.length > 0 && (
                  <Button variant="ghost" size="sm" className="h-7 text-xs text-rose-600 hover:bg-rose-50" onClick={clearWaypoints}>
                    <RotateCcw className="size-3 mr-1" /> 전체 초기화
                  </Button>
                )}
              </CardHeader>
              <CardContent className="pt-4">
                {waypoints.length === 0 ? (
                  <div className="text-center py-8 text-[12px] text-slate-400">
                    자세를 움직여 기록하면 웨이포인트 경로 목록이 여기에 채워집니다.
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="max-h-[300px] overflow-y-auto space-y-2 pr-1">
                      {waypoints.map((wp, i) => (
                        <div key={i} className="flex flex-wrap items-center justify-between gap-3 p-3 bg-slate-50 border border-slate-100 rounded-xl relative hover:border-slate-200">
                          <div className="flex items-center gap-2">
                            <span className="flex items-center justify-center size-5 rounded-full bg-slate-200 text-[10px] font-bold text-slate-600">
                              {i + 1}
                            </span>
                            <div className="text-[11px] font-mono text-slate-600">
                              각도: [{wp.angles.map(v => Math.round(v)).join(", ")}], 그리퍼: {wp.gripper}
                            </div>
                          </div>

                          <div className="flex items-center gap-3">
                            <div className="flex items-center gap-1 text-[11px]">
                              <span className="text-slate-400">속도:</span>
                              <input
                                type="number" min={5} max={80} step={5}
                                value={wp.speed}
                                onChange={(e) => updateWaypointField(i, "speed", Number(e.target.value))}
                                className="w-11 border border-slate-200 bg-white px-1.5 py-0.5 rounded font-mono text-center text-slate-700"
                              />
                            </div>
                            <div className="flex items-center gap-1 text-[11px]">
                              <span className="text-slate-400">대기(초):</span>
                              <input
                                type="number" min={0.2} max={10.0} step={0.2}
                                value={wp.delay}
                                onChange={(e) => updateWaypointField(i, "delay", Number(e.target.value))}
                                className="w-12 border border-slate-200 bg-white px-1.5 py-0.5 rounded font-mono text-center text-slate-700"
                              />
                            </div>
                            <Button size="sm" variant="ghost" className="size-7 p-0 text-slate-400 hover:text-rose-600 hover:bg-rose-50" onClick={() => removeWaypoint(i)}>
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* 임시 재생 및 DB 저장 패널 */}
                    <div className="flex flex-col sm:flex-row gap-3 pt-3 border-t border-slate-100">
                      <Button className="flex-1 bg-rose-500 hover:bg-rose-600 text-white shadow-sm" onClick={startPreview}>
                        <Play className="size-4 mr-2" /> 임시 시퀀스 미동작 재생
                      </Button>
                      
                      <form onSubmit={saveSequence} className="flex-1 flex gap-2">
                        <Input
                          placeholder="시퀀스 이름 (예: 입고 분류)"
                          value={saveName}
                          onChange={(e) => setSaveName(e.target.value)}
                          className="flex-1 text-[12px] h-9"
                        />
                        <Button type="submit" className="bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm" disabled={isSaving}>
                          <Save className="size-4 mr-2" /> 저장
                        </Button>
                      </form>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* 2. 저장된 모션 데이터베이스 목록 (오른쪽) */}
          <div className="space-y-4">
            <Card className="border-slate-200 shadow-sm">
              <CardHeader className="pb-2 bg-slate-50/50 border-b border-slate-100">
                <CardTitle className="text-[14px] font-semibold text-slate-800 flex items-center gap-1.5">
                  <Download className="size-4 text-emerald-500" />
                  저장된 시퀀스 목록
                </CardTitle>
                <CardDescription className="text-[11px] mt-0.5">데이터베이스에 저장된 로봇팔 동선 목록입니다.</CardDescription>
              </CardHeader>
              <CardContent className="pt-4">
                {savedSequences.length === 0 ? (
                  <div className="text-center py-8 text-[12px] text-slate-400">
                    저장된 모션 시퀀스가 없습니다.
                  </div>
                ) : (
                  <div className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
                    {savedSequences.map((seq) => (
                      <div key={seq.id} className="p-3 bg-slate-50 border border-slate-100 rounded-xl hover:border-slate-200 transition-all space-y-2">
                        <div className="flex justify-between items-start">
                          <div>
                            <p className="text-[12px] font-bold text-slate-800">{seq.name}</p>
                            <p className="text-[10px] text-slate-400 mt-0.5">{seq.description || "설명 없음"}</p>
                          </div>
                          <span className="text-[10px] bg-slate-200 text-slate-600 font-semibold px-2 py-0.5 rounded-full">
                            {seq.waypoints?.length || 0} step
                          </span>
                        </div>
                        <div className="flex gap-1.5 pt-1.5 border-t border-slate-100">
                          <Button size="sm" className="flex-1 bg-indigo-50 text-indigo-600 hover:bg-indigo-100 h-7 text-xs border border-indigo-100" onClick={() => playSequence(seq.id, seq.name)}>
                            <Play className="size-3 mr-1" /> 재생
                          </Button>
                          <Button size="sm" variant="outline" className="flex-1 h-7 text-xs text-slate-600 border-slate-200" onClick={() => loadSequence(seq)}>
                            불러오기
                          </Button>
                          <Button size="sm" variant="ghost" className="h-7 text-slate-400 hover:text-rose-600 hover:bg-rose-50 p-0 px-2" onClick={() => deleteSequence(seq.id)}>
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 작동 도움말 및 키보드 조작 */}
            <div className="rounded-xl border border-dashed border-rose-200 bg-rose-50/50 p-4.5 text-[12px] text-rose-700 space-y-2">
              <p className="font-semibold flex items-center gap-1">
                <Info className="size-3.5" />
                모션 티칭 사용 안내
              </p>
              <ul className="list-disc list-inside space-y-1 text-[11px] opacity-90 pl-0.5">
                <li>슬라이더나 조그 버튼을 조작하여 로봇을 움직입니다.</li>
                <li>원하는 지점의 자세를 잡고 "현재 자세 기록"을 누릅니다.</li>
                <li>각 지점(Waypoint)별 동작 속도와 대기 시간을 설정합니다.</li>
                <li>"임시 재생"으로 시뮬레이션 후 이름을 적어 "저장"합니다.</li>
              </ul>
            </div>
            <ArmKeyboardGuide />
          </div>
        </div>
      </div>
    </AdminShell>
  );
}

function BadgeConnected({ connected }: { connected: boolean }) {
  if (connected) {
    return (
      <span className="flex items-center gap-1 text-[12px] text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full font-medium">
        <Wifi className="size-3" />연결됨
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[12px] text-slate-500 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded-full font-medium">
      <WifiOff className="size-3" />미연결 (데모)
    </span>
  );
}
