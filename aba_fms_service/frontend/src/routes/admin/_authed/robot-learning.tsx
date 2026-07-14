import { createFileRoute, useRouterState } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Clock3,
  Image as ImageIcon,
  MapPin,
  Minus,
  MessageSquareText,
  ParkingCircle,
  Pencil,
  Play,
  Plus,
  Ban,
  RotateCcw,
  Route as RouteIcon,
  ScanEye,
  Save,
  Smile,
  Square,
  Trash2,
  Upload,
  UserRound,
  Volume2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  adminApi,
  type LearnedRobotAction,
  type LearnedRobotActionInput,
  type LearnedRobotScenario,
  type LearnedRobotScenarioInput,
  type LearnedRunResult,
  type LearnedScenarioNode,
  type Robot,
} from "@/lib/admin-api";
import { cn } from "@/lib/utils";

// 실행 헬퍼: 주행 액션이면 requires_confirm 이 오므로 사용자 확인 후 confirm=true 로 재실행한다.
async function runWithConfirm(
  runner: (confirm: boolean) => Promise<LearnedRunResult>,
  label: string,
): Promise<void> {
  try {
    let res = await runner(false);
    if (res.requires_confirm) {
      if (!window.confirm(`${res.message ?? "이 동작은 로봇을 실제로 움직입니다."}\n\n정말 실행할까요?`)) return;
      res = await runner(true);
    }
    if (res.success) {
      toast.success(`${label} 실행됨`);
    } else {
      toast.error(`${label} 실패: ${res.stopped_at ? `'${res.stopped_at}'에서 중단` : res.response ?? res.message ?? "오류"}`);
    }
    if (res.note) toast.message(res.note);
  } catch (e) {
    toast.error(`${label} 실행 오류: ${e instanceof Error ? e.message : String(e)}`);
  }
}

export const Route = createFileRoute("/admin/_authed/robot-learning")({ component: RobotLearningPage });

type Tab = "actions" | "scenarios";
type RobotScope = "selected" | "explicit" | "all";
type ActionType = "goto" | "mission_start" | "mission_stop" | "home" | "wait" | "parking_start" | "human_follow_start" | "pinky_detect_start" | "pinky_greet" | "lcd_text" | "lcd_image" | "emotion" | "buzzer" | "stop";

const EMOTIONS = ["happy", "angry", "sad", "hello", "fun", "interest", "bored", "basic"] as const;
const BUZZER_PRESETS = ["bell", "beep", "alarm", "success", "error"] as const;

const ACTION_TYPES: Array<{ value: ActionType; label: string; icon: typeof MapPin; hint: string }> = [
  { value: "goto", label: "지정 위치로 이동", icon: MapPin, hint: "A, B 같은 저장 위치로 이동" },
  { value: "mission_start", label: "순회 시작", icon: RouteIcon, hint: "여러 위치를 순서대로 방문" },
  { value: "mission_stop", label: "정지", icon: Square, hint: "현재 주행 또는 순회 정지" },
  { value: "home", label: "홈 복귀", icon: RotateCcw, hint: "기준 위치로 복귀" },
  { value: "parking_start", label: "주차 시작", icon: ParkingCircle, hint: "지정 구역으로 이동 후 주차" },
  { value: "human_follow_start", label: "사람 추종 시작", icon: UserRound, hint: "사람을 인식하고 따라가기" },
  { value: "pinky_detect_start", label: "핑키프로 인식 시작", icon: ScanEye, hint: "Pinky Pro 객체 인식 시작" },
  { value: "pinky_greet", label: "핑키를 보면 인사", icon: MessageSquareText, hint: "Pinky Pro 감지 시 LCD 문구 표시" },
  { value: "lcd_text", label: "LCD 문구 표시", icon: MessageSquareText, hint: "로봇 LCD에 문구 표시" },
  { value: "lcd_image", label: "LCD 이미지 표시", icon: ImageIcon, hint: "이미지를 업로드하거나 선택해서 표시" },
  { value: "emotion", label: "표정", icon: Smile, hint: "LCD 표정 표시 (기쁨/화남/슬픔 등)" },
  { value: "buzzer", label: "사운드", icon: Volume2, hint: "부저 소리 (벨/삐/알람)" },
  { value: "stop", label: "모터 정지", icon: Ban, hint: "모터 즉시 정지" },
  { value: "wait", label: "잠시 대기", icon: Clock3, hint: "시나리오 중간 대기" },
];

const QUICK_LOCATIONS = ["A", "B", "C", "D", "E", "F", "G", "H"];
const DEFAULT_TRIGGERS = ["로봇3 B로 가", "3번 순회 시작", "전체 정지", "핑키를 보면 인사해", "사람 따라가"];

function csvToList(value: string): string[] {
  return value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
}

function listToCsv(value: string[]): string {
  return value.join(", ");
}

function actionLabel(type: string): string {
  return ACTION_TYPES.find((x) => x.value === type)?.label ?? type;
}

function robotScopeLabel(scope: string): string {
  if (scope === "all") return "전체 로봇";
  if (scope === "explicit") return "문장에 나온 로봇";
  return "FMS에서 선택한 로봇";
}

function defaultAction(): LearnedRobotActionInput {
  return {
    name: "",
    description: "",
    trigger_phrases: [],
    robot_scope: "explicit",
    action_type: "goto",
    params: { location: "B" },
    enabled: true,
  };
}

function defaultScenario(): LearnedRobotScenarioInput {
  const nodes: LearnedScenarioNode[] = [
    { id: "start", type: "start", label: "시작", x: 70, y: 90, config: {} },
    { id: "move-a", type: "action", label: "A 위치로 이동", x: 270, y: 90, config: { action_type: "goto", location: "A" } },
    { id: "wait-3", type: "wait", label: "3초 대기", x: 470, y: 90, config: { seconds: 3 } },
    { id: "move-b", type: "action", label: "B 위치로 이동", x: 670, y: 90, config: { action_type: "goto", location: "B" } },
    { id: "end", type: "end", label: "종료", x: 870, y: 90, config: {} },
  ];
  const edges = [
    { id: "start-move-a", from: "start", to: "move-a" },
    { id: "move-a-wait-3", from: "move-a", to: "wait-3" },
    { id: "wait-3-move-b", from: "wait-3", to: "move-b" },
    { id: "move-b-end", from: "move-b", to: "end" },
  ];
  return { name: "기본 주행 시나리오", description: "", trigger_phrases: ["순회 시작", "A 갔다가 B로 가"], nodes, edges, enabled: true };
}

function actionParams(type: ActionType, location: string, namesText: string, loop: boolean, seconds: number, message: string, targetLabel: string, fontName: string, fontSize: number, color: string, bgColor: string, align: string, imageFilename: string, followMaxDuration: number): Record<string, any> {
  const names = csvToList(namesText);
  if (type === "goto") return { location: location.trim() || "B" };
  if (type === "mission_start") return { names: names.length ? names : ["A", "B", "C"], loop };
  if (type === "wait") return { seconds: Math.max(1, seconds || 1) };
  if (type === "parking_start") return { zone: location.trim() || "A", mode: "front", precision_mode: "hybrid" };
  if (type === "human_follow_start") return { follow: true, max_duration_s: Math.max(0, Math.min(600, followMaxDuration || 0)) };
  if (type === "pinky_detect_start") return { target_label: targetLabel.trim() || "pinky_63", mode: "server" };
  if (type === "pinky_greet") return { target_label: targetLabel.trim() || "pinky_63", ...lcdTextParams(message, fontName, fontSize, color, bgColor, align) };
  if (type === "lcd_text") return lcdTextParams(message, fontName, fontSize, color, bgColor, align);
  if (type === "lcd_image") return { filename: imageFilename };
  return {};
}

function nonLcdActionParams(type: ActionType, emotion: string, preset: string): Record<string, any> | null {
  if (type === "emotion") return { emotion };
  if (type === "buzzer") return { preset };
  if (type === "stop") return {};
  return null;
}

function lcdTextParams(message: string, fontName: string, fontSize: number, color: string, bgColor: string, align: string): Record<string, any> {
  const text = message.trim() || "안녕하세요!";
  return {
    text,
    lcd_text: text,
    font_name: fontName || "MaruBuri-Bold.ttf",
    font_size: Math.max(8, Math.min(96, Number(fontSize) || 28)),
    color: color || "#ffffff",
    bg_color: bgColor || "#000000",
    align: align || "center",
  };
}

function RobotLearningPage() {
  const qc = useQueryClient();
  const search = useRouterState({ select: (s) => s.location.search as { tab?: string } });
  const tab: Tab = search.tab === "scenarios" ? "scenarios" : "actions";
  const actionsQuery = useQuery({ queryKey: ["robot-learning", "actions"], queryFn: adminApi.listLearnedActions });
  const scenariosQuery = useQuery({ queryKey: ["robot-learning", "scenarios"], queryFn: adminApi.listLearnedScenarios });
  const robotsQuery = useQuery({ queryKey: ["robots", "pinky", "learning"], queryFn: () => adminApi.listRobots({ robot_type: "pinky", limit: 20 }) });

  return (
    <AdminShell title="주행로봇 학습">
      <div className="-m-4 min-h-[calc(100vh-60px)] bg-slate-950 p-4 text-slate-100 md:-m-6 md:p-6">
        <div className="mb-4 border-b border-white/10 pb-4">
          <div className="text-xl font-bold">주행로봇 학습 도구</div>
          <div className="text-[12px] text-slate-400">문장, 액션, 노드 연결을 학습시켜 채팅 제어용 데이터를 저장합니다.</div>
        </div>

        <CommandBar />

        <section className="min-w-0">
          {tab === "actions" ? (
            <ActionTraining
              actions={actionsQuery.data?.items ?? []}
              robots={robotsQuery.data?.items ?? []}
              loading={actionsQuery.isLoading}
              onChanged={() => void qc.invalidateQueries({ queryKey: ["robot-learning", "actions"] })}
            />
          ) : (
            <ScenarioTraining
              scenarios={scenariosQuery.data?.items ?? []}
              loading={scenariosQuery.isLoading}
              onChanged={() => void qc.invalidateQueries({ queryKey: ["robot-learning", "scenarios"] })}
            />
          )}
        </section>
      </div>
    </AdminShell>
  );
}

function CommandBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<string | null>(null);

  const run = async () => {
    const cmd = text.trim();
    if (!cmd || busy) return;
    setBusy(true);
    setLast(null);
    try {
      let res = await adminApi.interpretCommand(cmd, { execute: true, confirm: false });
      if (!res.matched) {
        toast.error(res.message ?? "일치하는 액션을 찾지 못했습니다.");
        return;
      }
      const target = res.target_robot ? ` → ${res.target_robot}` : res.robot_number ? ` → ${res.robot_number}번(미등록)` : "";
      const picked = `${res.kind === "scenario" ? "시나리오" : "액션"} '${res.name}'${target}`;
      setLast(picked);
      if (res.result?.requires_confirm) {
        if (!window.confirm(`${res.result.message ?? "이 동작은 로봇을 실제로 움직입니다."}\n\n(${picked})\n정말 실행할까요?`)) return;
        res = await adminApi.interpretCommand(cmd, { execute: true, confirm: true });
      }
      if (res.result?.success) {
        toast.success(`${picked} 실행됨`);
      } else {
        toast.error(`${picked} 실패: ${res.result?.stopped_at ? `'${res.result.stopped_at}'에서 중단` : res.result?.response ?? res.result?.message ?? "오류"}`);
      }
      if (res.result?.note) toast.message(res.result.note);
    } catch (e) {
      toast.error(`실행 오류: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 rounded-lg border border-sky-500/30 bg-sky-950/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-sky-200"><Bot className="h-4 w-4" />자연어 명령 (LLM)</div>
      <div className="flex gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void run(); }}
          placeholder="예: 주행로봇1번 충전소 이동 / 2번 주차해"
          className="border-slate-700 bg-slate-950 text-slate-100"
        />
        <Button onClick={() => void run()} disabled={busy || !text.trim()} className="bg-sky-600 hover:bg-sky-700">
          <Play className="mr-1.5 h-4 w-4" />{busy ? "해석 중..." : "실행"}
        </Button>
      </div>
      {last ? <div className="mt-2 text-[11px] text-slate-400">매칭: {last}</div> : null}
    </div>
  );
}

function ActionTraining({ actions, robots, loading, onChanged }: { actions: LearnedRobotAction[]; robots: Robot[]; loading: boolean; onChanged: () => void }) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(defaultAction());
  const [phrases, setPhrases] = useState("");
  const [location, setLocation] = useState("B");
  const [namesText, setNamesText] = useState("A, B, C");
  const [loop, setLoop] = useState(false);
  const [seconds, setSeconds] = useState(3);
  const [message, setMessage] = useState("안녕하세요!\nPinky Pro 감지됨 :)");
  const [targetLabel, setTargetLabel] = useState("pinky_63");
  const [fontName, setFontName] = useState("MaruBuri-Bold.ttf");
  const [fontSize, setFontSize] = useState(28);
  const [lcdColor, setLcdColor] = useState("#ffffff");
  const [lcdBgColor, setLcdBgColor] = useState("#000000");
  const [lcdAlign, setLcdAlign] = useState("center");
  const [imageFilename, setImageFilename] = useState("");
  const [emotion, setEmotion] = useState<string>("happy");
  const [preset, setPreset] = useState<string>("bell");
  const [followMaxDuration, setFollowMaxDuration] = useState<number>(30);

  const selectedType = form.action_type as ActionType;
  const save = useMutation({
    mutationFn: (payload: LearnedRobotActionInput) => editingId ? adminApi.updateLearnedAction(editingId, payload) : adminApi.createLearnedAction(payload),
    onSuccess: () => {
      setEditingId(null);
      setForm(defaultAction());
      setPhrases("");
      setLocation("B");
      setNamesText("A, B, C");
      setLoop(false);
      setSeconds(3);
      setMessage("안녕하세요!\nPinky Pro 감지됨 :)");
      setTargetLabel("pinky_63");
      setFontName("MaruBuri-Bold.ttf");
      setFontSize(28);
      setLcdColor("#ffffff");
      setLcdBgColor("#000000");
      setLcdAlign("center");
      setImageFilename("");
      setEmotion("happy");
      setPreset("bell");
      setFollowMaxDuration(30);
      onChanged();
    },
  });
  const remove = useMutation({ mutationFn: adminApi.deleteLearnedAction, onSuccess: onChanged });

  const edit = (action: LearnedRobotAction) => {
    const params = action.params ?? {};
    setEditingId(action.id);
    setForm({
      name: action.name,
      description: action.description ?? "",
      trigger_phrases: action.trigger_phrases,
      robot_scope: action.robot_scope,
      action_type: action.action_type,
      params,
      enabled: action.enabled,
    });
    setPhrases(listToCsv(action.trigger_phrases));
    setLocation(String(params.location ?? "B"));
    setNamesText(Array.isArray(params.names) ? params.names.join(", ") : "A, B, C");
    setLoop(Boolean(params.loop));
    setSeconds(Number(params.seconds ?? 3));
    setMessage(String(params.lcd_text ?? params.text ?? "안녕하세요!\nPinky Pro 감지됨 :)"));
    setTargetLabel(String(params.target_label ?? "pinky_63"));
    setFontName(String(params.font_name ?? "MaruBuri-Bold.ttf"));
    setFontSize(Number(params.font_size ?? 28));
    setLcdColor(String(params.color ?? "#ffffff"));
    setLcdBgColor(String(params.bg_color ?? "#000000"));
    setLcdAlign(String(params.align ?? "center"));
    setImageFilename(String(params.filename ?? ""));
    setEmotion(String(params.emotion ?? "happy"));
    setPreset(String(params.preset ?? "bell"));
    setFollowMaxDuration(Number(params.max_duration_s ?? 30));
  };

  const submit = () => {
    const triggers = csvToList(phrases);
    const name = form.name.trim() || (triggers[0] ? `${triggers[0]} 액션` : actionLabel(selectedType));
    save.mutate({
      ...form,
      name,
      trigger_phrases: triggers,
      params: nonLcdActionParams(selectedType, emotion, preset) ?? actionParams(selectedType, location, namesText, loop, seconds, message, targetLabel, fontName, fontSize, lcdColor, lcdBgColor, lcdAlign, imageFilename, followMaxDuration),
      enabled: true,
    });
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(420px,520px)_1fr]">
      <div className="rounded-lg border border-white/10 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold"><Bot className="h-4 w-4 text-sky-400" /> 새 액션 만들기</div>
          {editingId ? <button className="text-[11px] text-slate-400" onClick={() => { setEditingId(null); setForm(defaultAction()); setPhrases(""); }}>새로 작성</button> : null}
        </div>

        <div className="space-y-4">
          <section className="rounded-lg border border-white/10 bg-slate-950/70 p-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-300">1. 어떤 말에 반응할까요?</div>
            <Input value={phrases} onChange={(e) => setPhrases(e.target.value)} placeholder="예: 로봇3 B로 가, 3번 B 이동" className="border-slate-700 bg-slate-950 text-slate-100" />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {DEFAULT_TRIGGERS.map((text) => <button key={text} onClick={() => setPhrases(phrases ? `${phrases}, ${text}` : text)} className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:border-sky-500">{text}</button>)}
            </div>
          </section>

          <section className="rounded-lg border border-white/10 bg-slate-950/70 p-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-300">2. 어떤 로봇에 적용할까요?</div>
            <div className="grid gap-2 sm:grid-cols-3">
              {[
                { value: "explicit", label: "문장 기준", hint: "로봇3처럼 말하면 해당 로봇" },
                { value: "selected", label: "선택 로봇", hint: "FMS에서 고른 로봇" },
                { value: "all", label: "전체", hint: "모든 주행로봇" },
              ].map((scope) => <button key={scope.value} onClick={() => setForm({ ...form, robot_scope: scope.value as RobotScope })} className={cn("rounded-lg border p-2 text-left", form.robot_scope === scope.value ? "border-sky-400 bg-sky-950/80" : "border-slate-700 bg-slate-900") }><div className="text-[12px] font-semibold">{scope.label}</div><div className="mt-1 text-[10px] text-slate-400">{scope.hint}</div></button>)}
            </div>
            <div className="mt-2 text-[11px] text-slate-500">등록된 Pinky: {robots.length ? robots.map((robot) => robot.name).join(", ") : "불러오는 중"}</div>
          </section>

          <section className="rounded-lg border border-white/10 bg-slate-950/70 p-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-300">3. 어떤 동작을 할까요?</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {ACTION_TYPES.filter((type) => type.value !== "wait").map((type) => {
                const Icon = type.icon;
                return <button key={type.value} onClick={() => setForm({ ...form, action_type: type.value })} className={cn("rounded-lg border p-3 text-left", selectedType === type.value ? "border-sky-400 bg-sky-950/80" : "border-slate-700 bg-slate-900") }><div className="flex items-center gap-2 text-[12px] font-semibold"><Icon className="h-4 w-4" />{type.label}</div><div className="mt-1 text-[10px] text-slate-400">{type.hint}</div></button>;
              })}
            </div>
          </section>

          <section className="rounded-lg border border-white/10 bg-slate-950/70 p-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-300">4. 필요한 값을 고르세요</div>
            {selectedType === "goto" || selectedType === "parking_start" ? <LocationPicker value={location} onChange={setLocation} /> : null}
            {selectedType === "mission_start" ? <MissionPicker namesText={namesText} setNamesText={setNamesText} loop={loop} setLoop={setLoop} /> : null}
            {selectedType === "parking_start" ? <div className="mt-2 text-[12px] text-slate-400">선택한 위치로 이동한 뒤 주차 시나리오를 실행합니다.</div> : null}
            {selectedType === "human_follow_start" ? <div className="space-y-1"><div className="text-[12px] text-slate-400">사람 인식 스트림을 켜고 추종을 시작합니다.</div><label className="flex items-center gap-2 text-[12px] text-slate-300">최대 지속시간(초)<input type="number" min={0} max={600} value={followMaxDuration} onChange={(e) => setFollowMaxDuration(Number(e.target.value))} className="h-8 w-24 rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]" /></label><div className="text-[10px] text-slate-500">이 시간이 지나면 추종을 자동 종료합니다(0 = 무제한).</div></div> : null}
            {selectedType === "pinky_detect_start" ? <div className="space-y-2"><Input value={targetLabel} onChange={(e) => setTargetLabel(e.target.value)} placeholder="감지 라벨: pinky_63" className="border-slate-700 bg-slate-950 text-slate-100" /><div className="text-[12px] text-slate-400">Pinky Pro 객체 인식 스트림을 시작합니다.</div></div> : null}
            {selectedType === "pinky_greet" ? <div className="space-y-2"><Input value={targetLabel} onChange={(e) => setTargetLabel(e.target.value)} placeholder="감지 라벨: pinky_63" className="border-slate-700 bg-slate-950 text-slate-100" /><LcdTextConfigEditor cfg={{ lcd_text: message, font_name: fontName, font_size: fontSize, color: lcdColor, bg_color: lcdBgColor, align: lcdAlign }} updateConfig={(patch) => { if (patch.lcd_text != null || patch.text != null) setMessage(String(patch.lcd_text ?? patch.text)); if (patch.font_name != null) setFontName(String(patch.font_name)); if (patch.font_size != null) setFontSize(Number(patch.font_size)); if (patch.color != null) setLcdColor(String(patch.color)); if (patch.bg_color != null) setLcdBgColor(String(patch.bg_color)); if (patch.align != null) setLcdAlign(String(patch.align)); }} /></div> : null}
            {selectedType === "lcd_text" ? <LcdTextConfigEditor cfg={{ lcd_text: message, font_name: fontName, font_size: fontSize, color: lcdColor, bg_color: lcdBgColor, align: lcdAlign }} updateConfig={(patch) => { if (patch.lcd_text != null || patch.text != null) setMessage(String(patch.lcd_text ?? patch.text)); if (patch.font_name != null) setFontName(String(patch.font_name)); if (patch.font_size != null) setFontSize(Number(patch.font_size)); if (patch.color != null) setLcdColor(String(patch.color)); if (patch.bg_color != null) setLcdBgColor(String(patch.bg_color)); if (patch.align != null) setLcdAlign(String(patch.align)); }} /> : null}
            {selectedType === "lcd_image" ? <LcdImageConfigEditor cfg={{ filename: imageFilename }} updateConfig={(patch) => setImageFilename(String(patch.filename ?? ""))} /> : null}
            {selectedType === "mission_stop" ? <div className="text-[12px] text-slate-400">정지는 추가 값 없이 바로 실행됩니다.</div> : null}
            {selectedType === "home" ? <div className="text-[12px] text-slate-400">홈 복귀는 추가 값 없이 기준 위치로 이동합니다.</div> : null}
            {selectedType === "stop" ? <div className="text-[12px] text-slate-400">모터를 즉시 정지합니다 (추가 값 없음).</div> : null}
            {selectedType === "emotion" ? <div><div className="mb-1 text-[11px] text-slate-400">표정 선택</div><select value={emotion} onChange={(e) => setEmotion(e.target.value)} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px] text-slate-100">{EMOTIONS.map((e) => <option key={e} value={e}>{e}</option>)}</select></div> : null}
            {selectedType === "buzzer" ? <div><div className="mb-1 text-[11px] text-slate-400">사운드 프리셋</div><select value={preset} onChange={(e) => setPreset(e.target.value)} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px] text-slate-100">{BUZZER_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}</select></div> : null}
            {selectedType === "wait" ? <NumberPicker value={seconds} onChange={setSeconds} suffix="초" /> : null}
          </section>

          <section className="rounded-lg border border-white/10 bg-slate-950/70 p-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-300">5. 이름을 붙이고 저장</div>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="예: 3번 로봇 B 이동" className="border-slate-700 bg-slate-950 text-slate-100" />
            <Input value={form.description ?? ""} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="메모 또는 설명" className="mt-2 border-slate-700 bg-slate-950 text-slate-100" />
          </section>

          <Button onClick={submit} disabled={!csvToList(phrases).length || save.isPending} className="h-11 w-full bg-sky-600 hover:bg-sky-700"><Save className="mr-1.5 h-4 w-4" />{editingId ? "수정 저장" : "액션 학습 저장"}</Button>
        </div>
      </div>

      <LearnedActionList actions={actions} loading={loading} edit={edit} remove={(id) => remove.mutate(id)} run={(action) => void runWithConfirm((confirm) => adminApi.runLearnedAction(action.id, confirm), action.name)} />
    </div>
  );
}

function LocationPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <div><div className="grid grid-cols-4 gap-2">{QUICK_LOCATIONS.map((name) => <button key={name} onClick={() => onChange(name)} className={cn("h-10 rounded-lg border text-sm font-semibold", value === name ? "border-sky-400 bg-sky-950 text-sky-100" : "border-slate-700 bg-slate-900 text-slate-300")}>{name}</button>)}</div><Input value={value} onChange={(e) => onChange(e.target.value.toUpperCase())} placeholder="직접 입력" className="mt-2 border-slate-700 bg-slate-950 text-slate-100" /></div>;
}

function MissionPicker({ namesText, setNamesText, loop, setLoop }: { namesText: string; setNamesText: (value: string) => void; loop: boolean; setLoop: (value: boolean) => void }) {
  return <div className="space-y-2"><Input value={namesText} onChange={(e) => setNamesText(e.target.value.toUpperCase())} placeholder="예: A, B, C" className="border-slate-700 bg-slate-950 text-slate-100" /><div className="flex flex-wrap gap-1.5">{QUICK_LOCATIONS.map((name) => <button key={name} onClick={() => setNamesText(namesText ? `${namesText}, ${name}` : name)} className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:border-sky-500">{name} 추가</button>)}</div><label className="flex items-center gap-2 text-[12px] text-slate-300"><input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> 반복 순회</label></div>;
}

function NumberPicker({ value, onChange, suffix }: { value: number; onChange: (value: number) => void; suffix: string }) {
  return <div className="flex items-center gap-2"><button onClick={() => onChange(Math.max(1, value - 1))} className="h-9 w-9 rounded border border-slate-700 bg-slate-900">-</button><Input type="number" min={1} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-24 border-slate-700 bg-slate-950 text-center text-slate-100" /><span className="text-[12px] text-slate-400">{suffix}</span><button onClick={() => onChange(value + 1)} className="h-9 w-9 rounded border border-slate-700 bg-slate-900">+</button></div>;
}

function actionSummary(action: LearnedRobotAction): string {
  const params = action.params ?? {};
  if (action.action_type === "goto") return `${params.location ?? "B"} 위치로 이동`;
  if (action.action_type === "mission_start") return `${Array.isArray(params.names) ? params.names.join(" -> ") : "A -> B -> C"} 순회${params.loop ? " 반복" : ""}`;
  if (action.action_type === "mission_stop") return "주행/순회 정지";
  if (action.action_type === "home") return "홈 위치로 복귀";
  if (action.action_type === "parking_start") return `${params.zone ?? "A"} 구역 주차 시작`;
  if (action.action_type === "human_follow_start") return "사람 인식 후 추종 시작";
  if (action.action_type === "pinky_detect_start") return `${params.target_label ?? "pinky_63"} 인식 시작`;
  if (action.action_type === "pinky_greet") return `${params.target_label ?? "pinky_63"} 감지 시 LCD 인사`;
  if (action.action_type === "lcd_text") return "LCD 문구 표시";
  if (action.action_type === "lcd_image") return `LCD 이미지 ${params.filename || "선택 필요"}`;
  if (action.action_type === "emotion") return `표정: ${params.emotion ?? "happy"}`;
  if (action.action_type === "buzzer") return `사운드: ${params.preset ?? "bell"}`;
  if (action.action_type === "stop") return "모터 정지";
  return action.action_type;
}

function LearnedActionList({ actions, loading, edit, remove, run }: { actions: LearnedRobotAction[]; loading: boolean; edit: (action: LearnedRobotAction) => void; remove: (id: number) => void; run: (action: LearnedRobotAction) => void }) {
  return (
    <div className="rounded-lg border border-white/10 bg-slate-900/50 p-4">
      <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold">학습된 액션</div><div className="text-[11px] text-slate-500">{actions.length}개</div></div>
      {loading ? <div className="text-sm text-slate-400">불러오는 중...</div> : <div className="grid gap-2 lg:grid-cols-2">
        {actions.map((action) => (
          <div key={action.id} className="rounded-lg border border-white/10 bg-slate-950/80 p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[13px] font-semibold text-sky-200">{action.name}</div>
                <div className="mt-1 text-[11px] text-slate-400">{actionSummary(action)}</div>
              </div>
              <span className={cn("rounded px-2 py-0.5 text-[10px]", action.enabled ? "bg-emerald-500/10 text-emerald-300" : "bg-slate-700 text-slate-300")}>{action.enabled ? "ON" : "OFF"}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">{(action.trigger_phrases ?? []).slice(0, 4).map((phrase) => <span key={phrase} className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300">{phrase}</span>)}</div>
            <div className="mt-3 flex gap-1">
              <button disabled={!action.enabled} className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => run(action)}><Play className="h-3 w-3" />실행</button>
              <button className="inline-flex items-center gap-1 rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-200 hover:bg-sky-500/20" onClick={() => edit(action)}><Pencil className="h-3 w-3" />수정</button>
              <button className="inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-500/20" onClick={() => remove(action.id)}><Trash2 className="h-3 w-3" />삭제</button>
            </div>
          </div>
        ))}
        {!actions.length ? <div className="rounded-lg border border-dashed border-slate-700 p-6 text-center text-[12px] text-slate-500 lg:col-span-2">학습된 액션이 없습니다.</div> : null}
      </div>}
    </div>
  );
}

function ScenarioTraining({ scenarios, loading, onChanged }: { scenarios: LearnedRobotScenario[]; loading: boolean; onChanged: () => void }) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [scenario, setScenario] = useState<LearnedRobotScenarioInput>(defaultScenario());
  const [phrases, setPhrases] = useState(listToCsv(defaultScenario().trigger_phrases));
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(scenario.nodes[1]?.id ?? null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const canvasWidth = 1400;
  const canvasHeight = 900;

  const selectedNode = scenario.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const save = useMutation({
    mutationFn: (payload: LearnedRobotScenarioInput) => editingId ? adminApi.updateLearnedScenario(editingId, payload) : adminApi.createLearnedScenario(payload),
    onSuccess: () => onChanged(),
  });
  const remove = useMutation({ mutationFn: adminApi.deleteLearnedScenario, onSuccess: onChanged });

  const loadScenario = (item: LearnedRobotScenario) => {
    setEditingId(item.id);
    setScenario({
      name: item.name,
      description: item.description ?? "",
      trigger_phrases: item.trigger_phrases,
      nodes: item.nodes,
      edges: item.edges,
      enabled: item.enabled,
    });
    setPhrases(listToCsv(item.trigger_phrases));
    setSelectedNodeId(item.nodes.find((node) => node.type !== "start")?.id ?? null);
    setConnectFrom(null);
  };
  const reset = () => {
    const base = defaultScenario();
    setEditingId(null);
    setScenario(base);
    setPhrases(listToCsv(base.trigger_phrases));
    setSelectedNodeId(base.nodes[1]?.id ?? null);
    setConnectFrom(null);
  };
  const saveScenario = () => save.mutate({ ...scenario, trigger_phrases: csvToList(phrases) });
  const addNode = (type: string) => {
    const id = `${type}-${Date.now()}`;
    const node: LearnedScenarioNode = {
      id,
      type,
      label: nodeTypeLabel(type),
      x: 90 + (scenario.nodes.length % 4) * 170,
      y: 90 + Math.floor(scenario.nodes.length / 4) * 110,
      config: defaultNodeConfig(type),
    };
    setScenario({ ...scenario, nodes: [...scenario.nodes, node] });
    setSelectedNodeId(id);
  };
  const updateNode = (patch: Partial<LearnedScenarioNode>) => {
    if (!selectedNode) return;
    setScenario({ ...scenario, nodes: scenario.nodes.map((node) => node.id === selectedNode.id ? { ...node, ...patch } : node) });
  };
  const updateNodeConfig = (patch: Record<string, any>) => {
    if (!selectedNode) return;
    updateNode({ config: { ...(selectedNode.config ?? {}), ...patch } });
  };
  const deleteNode = () => {
    if (!selectedNode || selectedNode.type === "start") return;
    setScenario({
      ...scenario,
      nodes: scenario.nodes.filter((node) => node.id !== selectedNode.id),
      edges: scenario.edges.filter((edge) => edge.from !== selectedNode.id && edge.to !== selectedNode.id),
    });
    setSelectedNodeId(null);
  };
  const connectNode = (id: string) => {
    if (connectFrom && connectFrom !== id) {
      const exists = scenario.edges.some((edge) => edge.from === connectFrom && edge.to === id);
      if (!exists) setScenario({ ...scenario, edges: [...scenario.edges, { id: `${connectFrom}-${id}-${Date.now()}`, from: connectFrom, to: id }] });
      setConnectFrom(null);
      setSelectedNodeId(id);
      return;
    }
    setSelectedNodeId(id);
  };
  const moveNode = (id: string, x: number, y: number) => {
    setScenario({ ...scenario, nodes: scenario.nodes.map((node) => node.id === id ? { ...node, x, y } : node) });
  };
  const zoomOut = () => setZoom((value) => Math.max(0.5, Number((value - 0.1).toFixed(2))));
  const zoomIn = () => setZoom((value) => Math.min(1.8, Number((value + 0.1).toFixed(2))));
  const resetZoom = () => setZoom(1);

  return (
    <div className="grid gap-4 xl:grid-cols-[260px_minmax(0,1fr)_320px]">
      <div className="rounded-lg border border-white/10 bg-slate-900/70 p-4">
        <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold">저장된 시나리오</div><Button size="sm" onClick={reset} className="bg-sky-600 hover:bg-sky-700"><Plus className="mr-1 h-3.5 w-3.5" />새로</Button></div>
        {loading ? <div className="text-xs text-slate-400">불러오는 중...</div> : <div className="space-y-2">
          {scenarios.map((item) => <div key={item.id} className="rounded-lg border border-white/10 bg-slate-950 p-3"><div className="text-[13px] font-semibold text-sky-200">{item.name}</div><div className="mt-1 text-[11px] text-slate-500">노드 {item.nodes.length}개 · 연결 {item.edges.length}개</div><div className="mt-3 flex gap-1"><button disabled={!item.enabled} className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => void runWithConfirm((confirm) => adminApi.runLearnedScenario(item.id, confirm), item.name)}><Play className="h-3 w-3" />실행</button><button className="inline-flex items-center gap-1 rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-200 hover:bg-sky-500/20" onClick={() => loadScenario(item)}><Pencil className="h-3 w-3" />수정</button><button className="inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-500/20" onClick={() => remove.mutate(item.id)}><Trash2 className="h-3 w-3" />삭제</button></div></div>)}
          {!scenarios.length ? <div className="rounded-lg border border-dashed border-slate-700 p-4 text-center text-[12px] text-slate-500">저장된 시나리오가 없습니다.</div> : null}
        </div>}
      </div>

      <div className="min-w-0 rounded-lg border border-white/10 bg-slate-900/50 p-4">
        <div className="mb-3 grid min-w-0 gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
          <Input value={scenario.name} onChange={(e) => setScenario({ ...scenario, name: e.target.value })} placeholder="시나리오 이름" className="border-slate-700 bg-slate-950 text-slate-100" />
          <Input value={phrases} onChange={(e) => setPhrases(e.target.value)} placeholder="트리거 문장: 핑키를 보면 인사해" className="border-slate-700 bg-slate-950 text-slate-100" />
          <Button onClick={saveScenario} disabled={!scenario.name.trim() || save.isPending} className="bg-emerald-600 hover:bg-emerald-700"><Save className="mr-1.5 h-4 w-4" />저장</Button>
        </div>
        <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
          {["event", "condition", "action", "lcd_text", "lcd_image", "wait", "end"].map((type) => <Button key={type} size="sm" variant="outline" onClick={() => addNode(type)} className="border-slate-700 bg-slate-950 text-slate-200"><Plus className="mr-1 h-3.5 w-3.5" />{nodeTypeLabel(type)}</Button>)}
          <Button size="sm" variant="outline" disabled={!selectedNodeId} onClick={() => setConnectFrom(selectedNodeId)} className="border-sky-600 bg-sky-950 text-sky-100">연결 시작</Button>
          <div className="ml-auto inline-flex items-center rounded-md border border-slate-700 bg-slate-950">
            <button type="button" onClick={zoomOut} className="flex h-8 w-8 items-center justify-center text-slate-300 hover:bg-slate-800" aria-label="축소"><Minus className="h-3.5 w-3.5" /></button>
            <div className="min-w-14 border-x border-slate-700 px-2 text-center text-[11px] text-slate-300">{Math.round(zoom * 100)}%</div>
            <button type="button" onClick={zoomIn} className="flex h-8 w-8 items-center justify-center text-slate-300 hover:bg-slate-800" aria-label="확대"><Plus className="h-3.5 w-3.5" /></button>
            <button type="button" onClick={resetZoom} className="flex h-8 w-8 items-center justify-center border-l border-slate-700 text-slate-300 hover:bg-slate-800" aria-label="확대 초기화"><RotateCcw className="h-3.5 w-3.5" /></button>
          </div>
          {connectFrom ? <span className="rounded bg-emerald-950 px-2 py-1.5 text-[11px] text-emerald-200">연결할 다음 노드를 클릭</span> : null}
        </div>
        <div className="relative h-[620px] w-full max-w-full overflow-auto rounded-lg border border-slate-800 bg-slate-950">
          <div className="relative" style={{ width: canvasWidth * zoom, height: canvasHeight * zoom }}>
            <div className="absolute left-0 top-0" style={{ width: canvasWidth, height: canvasHeight, transform: `scale(${zoom})`, transformOrigin: "0 0" }}>
              <svg className="absolute inset-0 h-full w-full">
                <defs><marker id="scenario-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#38bdf8" /></marker></defs>
                {scenario.edges.map((edge) => {
                  const from = scenario.nodes.find((node) => node.id === edge.from);
                  const to = scenario.nodes.find((node) => node.id === edge.to);
                  if (!from || !to) return null;
                  return <line key={edge.id} x1={from.x + 80} y1={from.y + 28} x2={to.x + 80} y2={to.y + 28} stroke="#38bdf8" strokeWidth="2" markerEnd="url(#scenario-arrow)" />;
                })}
              </svg>
              {scenario.nodes.map((node) => <button key={node.id} onClick={() => connectNode(node.id)} onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); setDragId(node.id); }} onPointerUp={(e) => { e.currentTarget.releasePointerCapture(e.pointerId); setDragId(null); }} onPointerMove={(e) => { if (dragId === node.id) { const rect = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect(); moveNode(node.id, Math.max(8, (e.clientX - rect.left) / zoom - 80), Math.max(8, (e.clientY - rect.top) / zoom - 28)); } }} className={cn("absolute w-[160px] cursor-grab touch-none rounded-lg border px-3 py-2 text-left text-[12px] shadow-lg active:cursor-grabbing", selectedNodeId === node.id ? "border-sky-400 bg-sky-950 text-sky-100" : connectFrom === node.id ? "border-emerald-400 bg-emerald-950 text-emerald-100" : "border-slate-700 bg-slate-900 text-slate-200")} style={{ left: node.x, top: node.y }}><div className="truncate font-semibold">{node.label}</div><div className="mt-1 text-[10px] text-slate-400">{nodeTypeLabel(node.type)}</div></button>)}
            </div>
          </div>
        </div>
      </div>

      <div className="min-w-0 rounded-lg border border-white/10 bg-slate-900/70 p-4">
        <div className="mb-3 text-sm font-semibold">노드 속성</div>
        {selectedNode ? <div className="space-y-3"><Input value={selectedNode.label} onChange={(e) => updateNode({ label: e.target.value })} className="border-slate-700 bg-slate-950 text-slate-100" /><select value={selectedNode.type} onChange={(e) => updateNode({ type: e.target.value, config: defaultNodeConfig(e.target.value) })} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]">{["start", "event", "condition", "action", "lcd_text", "lcd_image", "wait", "end"].map((type) => <option key={type} value={type}>{nodeTypeLabel(type)}</option>)}</select><NodeConfigEditor node={selectedNode} updateConfig={updateNodeConfig} />{selectedNode.type !== "start" ? <Button variant="outline" className="w-full border-rose-500/40 bg-rose-500/10 text-rose-200" onClick={deleteNode}><Trash2 className="mr-1.5 h-4 w-4" />노드 삭제</Button> : null}</div> : <div className="text-[12px] text-slate-400">노드를 선택하면 속성을 편집할 수 있습니다.</div>}
      </div>
    </div>
  );
}

function nodeTypeLabel(type: string): string {
  if (type === "start") return "시작";
  if (type === "event") return "이벤트";
  if (type === "condition") return "조건";
  if (type === "action") return "로봇 액션";
  if (type === "lcd_text") return "LCD 문구";
  if (type === "lcd_image") return "LCD 이미지";
  if (type === "wait") return "대기";
  if (type === "end") return "종료";
  return type;
}

function defaultNodeConfig(type: string): Record<string, any> {
  if (type === "event") return { event: "detect", target_label: "pinky_63" };
  if (type === "condition") return { field: "detected", op: "eq", value: true };
  if (type === "action") return { action_type: "goto", location: "B" };
  if (type === "lcd_text") return lcdTextParams("안녕하세요!\nPinky Pro 감지됨 :)", "MaruBuri-Bold.ttf", 28, "#ffffff", "#000000", "center");
  if (type === "lcd_image") return { filename: "" };
  if (type === "wait") return { seconds: 3 };
  return {};
}

function NodeConfigEditor({ node, updateConfig }: { node: LearnedScenarioNode; updateConfig: (patch: Record<string, any>) => void }) {
  const cfg = node.config ?? {};
  if (node.type === "event") return <div className="space-y-2"><Input value={String(cfg.event ?? "detect")} onChange={(e) => updateConfig({ event: e.target.value })} placeholder="이벤트: detect/chat" className="border-slate-700 bg-slate-950 text-slate-100" /><Input value={String(cfg.target_label ?? "pinky_63")} onChange={(e) => updateConfig({ target_label: e.target.value })} placeholder="감지 라벨" className="border-slate-700 bg-slate-950 text-slate-100" /></div>;
  if (node.type === "condition") return <div className="space-y-2"><Input value={String(cfg.field ?? "detected")} onChange={(e) => updateConfig({ field: e.target.value })} placeholder="조건 필드" className="border-slate-700 bg-slate-950 text-slate-100" /><Input value={String(cfg.value ?? "true")} onChange={(e) => updateConfig({ value: e.target.value })} placeholder="값" className="border-slate-700 bg-slate-950 text-slate-100" /></div>;
  if (node.type === "action") return <ActionNodeConfig cfg={cfg} updateConfig={updateConfig} />;
  if (node.type === "lcd_text") return <LcdTextConfigEditor cfg={cfg} updateConfig={updateConfig} />;
  if (node.type === "lcd_image") return <LcdImageConfigEditor cfg={cfg} updateConfig={updateConfig} />;
  if (node.type === "wait") return <NumberPicker value={Number(cfg.seconds ?? 3)} onChange={(seconds) => updateConfig({ seconds })} suffix="초" />;
  return <div className="text-[12px] text-slate-400">추가 설정 없음</div>;
}

function ActionNodeConfig({ cfg, updateConfig }: { cfg: Record<string, any>; updateConfig: (patch: Record<string, any>) => void }) {
  const type = String(cfg.action_type ?? "goto") as ActionType;
  return <div className="space-y-2"><select value={type} onChange={(e) => updateConfig({ action_type: e.target.value })} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]">{ACTION_TYPES.filter((item) => item.value !== "wait").map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{type === "goto" || type === "parking_start" ? <LocationPicker value={String(cfg.location ?? cfg.zone ?? "B")} onChange={(value) => updateConfig(type === "parking_start" ? { zone: value } : { location: value })} /> : null}{type === "mission_start" ? <MissionPicker namesText={Array.isArray(cfg.names) ? cfg.names.join(", ") : "A, B, C"} setNamesText={(value) => updateConfig({ names: csvToList(value) })} loop={Boolean(cfg.loop)} setLoop={(loop) => updateConfig({ loop })} /> : null}</div>;
}

function LcdTextConfigEditor({ cfg, updateConfig }: { cfg: Record<string, any>; updateConfig: (patch: Record<string, any>) => void }) {
  const fontsQuery = useQuery({ queryKey: ["robot", "fonts", "learning"], queryFn: adminApi.listFonts });
  return <div className="space-y-2"><textarea value={String(cfg.lcd_text ?? cfg.text ?? "")} onChange={(e) => updateConfig({ text: e.target.value, lcd_text: e.target.value })} placeholder="LCD에 표시할 문구" className="h-20 w-full rounded-md border border-slate-700 bg-slate-950 p-2 text-[12px] text-slate-100" /><select value={String(cfg.font_name ?? "MaruBuri-Bold.ttf")} onChange={(e) => updateConfig({ font_name: e.target.value })} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]"><option value="default">기본 폰트</option><option value="MaruBuri-Bold.ttf">MaruBuri-Bold.ttf</option>{(fontsQuery.data?.fonts ?? []).map((font) => <option key={font} value={font}>{font}</option>)}</select><div className="grid grid-cols-2 gap-2"><Input type="number" min={8} max={96} value={Number(cfg.font_size ?? 28)} onChange={(e) => updateConfig({ font_size: Number(e.target.value) })} className="border-slate-700 bg-slate-950 text-slate-100" /><select value={String(cfg.align ?? "center")} onChange={(e) => updateConfig({ align: e.target.value })} className="h-9 rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]"><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></div><div className="grid grid-cols-2 gap-2"><Input type="color" value={String(cfg.color ?? "#ffffff")} onChange={(e) => updateConfig({ color: e.target.value })} className="h-9 border-slate-700 bg-slate-950" /><Input type="color" value={String(cfg.bg_color ?? "#000000")} onChange={(e) => updateConfig({ bg_color: e.target.value })} className="h-9 border-slate-700 bg-slate-950" /></div></div>;
}

function LcdImageConfigEditor({ cfg, updateConfig }: { cfg: Record<string, any>; updateConfig: (patch: Record<string, any>) => void }) {
  const qc = useQueryClient();
  const imagesQuery = useQuery({ queryKey: ["robot", "lcd-images", "learning"], queryFn: adminApi.listImages });
  const upload = useMutation({ mutationFn: (file: File) => adminApi.lcdUploadImage(file), onSuccess: (res) => { if (res.image?.filename) updateConfig({ filename: res.image.filename }); void qc.invalidateQueries({ queryKey: ["robot", "lcd-images"] }); } });
  return <div className="space-y-2"><select value={String(cfg.filename ?? "")} onChange={(e) => updateConfig({ filename: e.target.value })} className="h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-2 text-[12px]"><option value="">이미지 선택</option>{(imagesQuery.data?.images ?? []).map((image) => <option key={image.filename} value={image.filename}>{image.original_name || image.filename}</option>)}</select><label className="flex cursor-pointer items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-[12px] text-slate-300 hover:border-sky-500"><Upload className="h-3.5 w-3.5" />이미지 첨부<input type="file" accept="image/*" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) upload.mutate(file); e.currentTarget.value = ""; }} /></label>{upload.isPending ? <div className="text-[11px] text-slate-400">업로드 중...</div> : null}</div>;
}
