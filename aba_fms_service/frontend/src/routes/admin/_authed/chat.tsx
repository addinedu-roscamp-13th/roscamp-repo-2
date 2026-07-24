import { createFileRoute } from "@tanstack/react-router";
import { MessageSquare, Send, Bot, User, Cpu, Square, Ban, Activity, Wifi, WifiOff, CheckCircle2, Loader2, CircleDashed, ArrowRight, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { VoiceControlPanel } from "@/components/admin/VoiceControlPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { adminApi, type Robot, type Nav2State, type ControlLinkInfo } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/chat")({
  component: ChatPage,
});

type MessageItem = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  pending?: boolean;
  stoppable?: boolean;
  robotNumber?: number | null;
  robotLabel?: string;
  stopped?: boolean;
  needsRobot?: boolean;
  robots?: Array<{ number: number | null; name: string }>;
  pendingCommand?: string;
  needsConfirm?: boolean;
  confirmCommand?: string;
  confirmLabel?: string;
};

// 실행 후 '정지' 버튼을 노출할 주행성 액션 (즉시 끝나는 표정/사운드/LCD 는 제외)
const DRIVING_ACTION_TYPES = new Set(["goto", "mission_start", "parking_start", "home", "human_follow_start", "relative_move", "relative_turn"]);

// 우측 '통신 상태' 패널 — 마지막 명령이 발행→결과회신 왕복 중 어디까지 갔는지.
// fleet_cmd 는 토픽이라 '발행 = 로봇 실행 개시'. 그래서 await_confirm 은 발행 직전 사람 확인 단계.
type CommState =
  | { phase: "idle" }
  | { phase: "interpreting"; label: string }   // ① LLM 해석·매칭 중 (/api/chat/message)
  | { phase: "needs_robot"; label: string }    // 로봇 미지정 → 선택 대기
  | { phase: "await_confirm"; label: string }  // 발행 직전 사람 확인 대기 (발행 시 로봇이 움직임)
  | { phase: "sending"; label: string }        // fleet_cmd 토픽 발행 → 결과 회신 대기
  | { phase: "not_matched"; label: string }    // 등록 액션/시나리오와 불일치 (발행 안 함)
  | { phase: "ok"; label: string; detail?: string }
  | { phase: "fail"; label: string; detail?: string };

const GREETING = [
  "안녕하세요! 🤖 주행로봇 제어 챗봇입니다.",
  "",
  "이렇게 말해보세요:",
  '• "웃어줘" · "벨 소리" · "LED 빨간색으로 켜줘" · "LED 꺼줘" · "정지"',
  '• "주행로봇1 LCD에 안녕 출력"',
  '• "주행로봇1 뒤로 10cm" · "2번 앞으로 20cm" · "1번 왼쪽 30도"',
  '• "1번 뒤로 돌아"(유턴) · "1번 우측으로 50cm"(우회전 90° 후 전진)',
  '• "2번 주차해" · "주행로봇1 충전소 이동"',
  '• 다중 주문(최대 4개, 채팅·음성 공통): "1번 A로 이동 후 완료되면 뒤로 돌아 30cm 직진"',
  '  — "직진/앞으로"는 앞에 장애물이 있으면 라이다로 자동 정지합니다.',
  "",
  "문장 앞에 \"주행로봇1\", \"2번\" 처럼 번호를 붙이면 해당 로봇으로 전송됩니다.",
].join("\n");

const NOT_MATCHED = [
  "🤖 해당 명령을 이해하지 못했어요.",
  "",
  "이렇게 말해보세요:",
  '• "웃어줘" · "벨 소리" · "LED 빨간색으로 켜줘" · "LED 꺼줘" · "정지"',
  '• "주행로봇1 LCD에 안녕 출력"',
  '• "주행로봇1 뒤로 10cm" · "2번 앞으로 20cm" · "1번 왼쪽 30도"',
  '• "1번 뒤로 돌아"(유턴) · "1번 우측으로 50cm"(우회전 90° 후 전진)',
  '• "2번 주차해" · "주행로봇1 충전소 이동"',
  '• 다중 주문(최대 4개, 채팅·음성 공통): "1번 A로 이동 후 완료되면 뒤로 돌아 30cm 직진"',
  '  — "직진/앞으로"는 앞에 장애물이 있으면 라이다로 자동 정지합니다.',
].join("\n");

const SESSION_ID = "admin-robot-control-session";
const ZONE_KEYBOARD_MAP: Record<string, string> = { "ㅁ": "A", "ㅠ": "B", "ㅊ": "C", "ㅇ": "D", "ㄷ": "E", "ㄹ": "F", "ㅎ": "G", "ㅗ": "H" };

function extractZone(text: string): string | null {
  const tokens = [
    ...Array.from(text.matchAll(/(^|[^A-Za-z0-9가-힣])([A-Ha-hㅁㅠㅊㅇㄷㄹㅎㅗ])\s*(?:구역|위치|존|zone|로|으로|이동|가|에)?/g)).map((m) => m[2]),
    ...Array.from(text.matchAll(/(?:로봇|주행로봇)\s*([A-Ha-hㅁㅠㅊㅇㄷㄹㅎㅗ])\s*(?:구역|위치|존|zone|로|으로|이동|가|에)?/g)).map((m) => m[1]),
  ];
  const token = tokens.at(-1);
  return token ? (ZONE_KEYBOARD_MAP[token] ?? token.toUpperCase()) : null;
}

function extractEmotionLabel(text: string): string | null {
  if (/웃|미소|스마일|행복|기뻐/.test(text)) return "웃는 표정";
  if (/슬퍼|울어|우울|시무룩/.test(text)) return "슬픈 표정";
  if (/화나|화내|짜증/.test(text)) return "화난 표정";
  return null;
}

function actionSummary(interp: Awaited<ReturnType<typeof adminApi.interpretCommand>>, target: string, command: string): string {
  const actionType = interp.result?.action_type;
  if (actionType === "goto") {
    const zone = extractZone(command);
    const emotion = extractEmotionLabel(command);
    const parts = [zone ? `${zone} 구역 이동` : "구역 이동"];
    if (emotion) parts.push(emotion);
    return `${target} → ${parts.join(" + ")}`;
  }
  if (actionType === "mission_start") return `${target} → 순회 시작`;
  if (actionType === "relative_move") {
    // 백엔드 _strip_robot_ref 와 동일하게 로봇 지칭어부터 제거한다 —
    // 안 그러면 "주행로봇1" 의 1 을 거리로 오인해 "1cm" 로 표시된다.
    const intent = command
      .replace(/주행\s*로봇\s*\d*\s*번?/g, " ")
      .replace(/로봇\s*\d+\s*번?/g, " ")
      .replace(/\d+\s*번/g, " ");
    const dir = /(뒤|후진|back)/i.test(intent) ? "후진" : "전진";
    // 단위가 붙은 숫자만 거리로 인정(단위 없는 숫자는 무시). 백엔드 파싱과 일치.
    const value = intent.match(/(\d+(?:\.\d+)?)\s*(m|미터|meter|cm|센티|센치)/i);
    const dist = value ? value[1] + value[2].replace("미터", "m").replace("meter", "m").replace("센티", "cm").replace("센치", "cm") : "상대";
    return target + " → " + dist + " " + dir;
  }
  if (actionType === "relative_turn") return `${target} → 짧은 좌/우 회전`;
  if (actionType === "mission_stop" || actionType === "stop") return `${target} → 정지`;
  if (actionType === "home") return `${target} → 홈 복귀`;
  if (actionType === "parking_start") return `${target} → 주차 시작`;
  if (actionType === "emotion") return `${target} → ${extractEmotionLabel(command) ?? "표정 변경"}`;
  if (actionType === "lcd_text") return `${target} → LCD 문구 표시`;
  if (actionType === "buzzer") return `${target} → 소리 재생`;
  if (actionType === "buzzer_melody") return `${target} → 멜로디 연주`;
  if (actionType === "led_fill") return `${target} → LED 켜기`;
  if (actionType === "led_clear") return `${target} → LED 끄기`;
  if (actionType === "led_brightness") return `${target} → LED 밝기 조정`;
  return `${target} → ${interp.kind === "scenario" ? "시나리오 실행" : "명령 실행"}`;
}

function userFacingError(detail: string): string {
  if (!detail) return "알 수 없는 에러";
  try {
    const parsed = JSON.parse(detail);
    if (parsed && typeof parsed === "object") {
      const error = typeof parsed.error === "string" ? parsed.error : null;
      const moved = typeof parsed.moved_m === "number" ? parsed.moved_m : null;
      const target = typeof parsed.target_m === "number" ? parsed.target_m : null;
      if (error && moved != null && target != null) return error + " (목표 " + Math.round(target * 100) + "cm, 실제 " + Math.round(moved * 100) + "cm)";
      if (error) return error;
    }
  } catch {
    /* plain text response */
  }
  return detail.length > 140 ? detail.slice(0, 140) + "..." : detail;
}

function ChatPage() {
  const [messages, setMessages] = useState<MessageItem[]>([{ id: "greeting", role: "assistant", content: GREETING }]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [comm, setComm] = useState<CommState>({ phase: "idle" });
  const [robots, setRobots] = useState<Robot[]>([]);
  const [activeRobotId, setActiveRobotId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  // 상태 패널용 활성 주행로봇 목록 (이름 오름차순). 실패해도 채팅 자체엔 영향 없음.
  useEffect(() => {
    adminApi.listRobots({ robot_type: "pinky", limit: 50 })
      .then((res) => {
        const items = (res.items ?? [])
          .filter((r) => r.is_active)
          .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
        setRobots(items);
        setActiveRobotId((cur) => cur ?? items[0]?.id ?? null);
      })
      .catch(() => { /* 로봇 목록 실패 시 상태 패널만 비활성 */ });
  }, []);

  const stopAction = async (m: MessageItem) => {
    const cmd = m.robotNumber ? `주행로봇${m.robotNumber} 정지` : "정지";
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, stopped: true } : x)));
    try {
      const resMsg = await adminApi.sendChatMessage(cmd, { sessionId: SESSION_ID, execute: true, confirm: true });
      const res = resMsg.interpretation;
      if (res.result?.success) {
        toast.success(`${m.robotLabel ?? "로봇"} 정지됨`);
        setMessages((prev) => [
          ...prev,
          { id: Date.now().toString() + "s", role: "assistant", content: `⏹️ ${m.robotLabel ?? "로봇"} 동작을 정지했습니다.` },
        ]);
      } else {
        toast.error("정지 실패");
        setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, stopped: false } : x)));
      }
    } catch {
      toast.error("정지 명령 오류");
      setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, stopped: false } : x)));
    }
  };

  // interpret 실행 결과를 지정한 말풍선에 렌더 (실행 완료/실패 + 정지 버튼)
  const renderInterpResult = (id: string, interp: Awaited<ReturnType<typeof adminApi.interpretCommand>>, target: string, command: string) => {
    const ok = interp.result?.success;
    const rawDetail = interp.result?.stopped_at ? "'" + interp.result.stopped_at + "'에서 중단" : interp.result?.response ?? interp.result?.message ?? "알 수 없는 에러";
    const detail = userFacingError(rawDetail);
    const summary = actionSummary(interp, target, command);
    // 성공이라도 안전정지 등 부가 설명(백엔드 message)이 있으면 함께 보여준다.
    const note = ok ? (interp.result?.message ?? "") : "";
    const doneMsg = ok
      ? `🤖 실행했어요.\n\n${summary}${note ? `\n\n${note}` : ""}`
      : `❌ 실행 실패: ${detail}`;
    const stoppable = Boolean(ok) && (interp.kind === "scenario" || DRIVING_ACTION_TYPES.has(interp.result?.action_type ?? ""));
    setComm(ok ? { phase: "ok", label: summary } : { phase: "fail", label: summary, detail });
    // 명령이 향한 로봇을 상태 패널 활성 로봇으로 맞춤 (target_robot = 로봇 이름).
    const matchedId = robots.find((r) => r.name === (interp.target_robot ?? ""))?.id;
    if (matchedId) setActiveRobotId(matchedId);
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: doneMsg, pending: false, needsConfirm: false, stoppable, robotNumber: interp.robot_number ?? null, robotLabel: target } : m)));
  };

  const confirmExecute = async (m: MessageItem) => {
    const cmd = m.confirmCommand ?? "";
    setComm({ phase: "sending", label: m.confirmLabel ?? cmd });
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, needsConfirm: false, pending: true, content: `🤖 ${m.confirmLabel ?? ""} 실행 중...` } : x)));
    try {
      const resMsg = await adminApi.sendChatMessage(cmd, { sessionId: SESSION_ID, execute: true, confirm: true });
      const interp = resMsg.interpretation;
      const target = interp.target_robot ?? (interp.robot_number ? `${interp.robot_number}번(미등록)` : "로컬");
      renderInterpResult(m.id, interp, target, cmd);
    } catch {
      setComm({ phase: "fail", label: m.confirmLabel ?? cmd, detail: "실행 중 오류" });
      setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, pending: false, content: "❌ 실행 중 오류가 발생했습니다." } : x)));
    }
  };

  const cancelConfirm = (m: MessageItem) => {
    setComm({ phase: "idle" });
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, needsConfirm: false, content: `⏸️ 실행을 취소했습니다.${m.confirmLabel ? ` (${m.confirmLabel})` : ""}` } : x)));
  };

  const stopAll = async () => {
    try {
      const res = await adminApi.stopAllRobots();
      const okCount = res.results.filter((r) => r.success).length;
      if (res.success) toast.success(`전체 정지 완료 (${okCount}/${res.count})`);
      else toast.error(`일부 정지 실패 (${okCount}/${res.count})`);
      setMessages((prev) => [
        ...prev,
        { id: Date.now().toString() + "sa", role: "assistant", content: `⏹️ 전체 정지: ${res.results.map((r) => `${r.name} ${r.success ? "✓" : "✗"}`).join(", ")}` },
      ]);
    } catch {
      toast.error("전체 정지 오류");
    }
  };

  const handleSend = async (text: string) => {
    if (!text.trim() || loading) return;
    setLoading(true);
    setInput("");
    setComm({ phase: "interpreting", label: text });

    // Add user message
    const userMsg: MessageItem = { id: Date.now().toString(), role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);

    const pendingId = Date.now().toString() + "p";
    setMessages((prev) => [
      ...prev,
      { id: pendingId, role: "assistant", content: "자연어 분석 중...", pending: true }
    ]);

    // 0. 학습된 액션/시나리오 매칭 (LLM 트리거) — 예: "주행로봇1 충전소 이동", "2번 주차해"
    try {
      const chatRes = await adminApi.sendChatMessage(text, { sessionId: SESSION_ID, execute: true, confirm: false });
      const interp = chatRes.interpretation;
      if (interp.matched && interp.needs_robot) {
        // 로봇 미지정 → 어느 로봇에서 실행할지 되묻고, 버튼으로 재전송
        setComm({ phase: "needs_robot", label: interp.message ?? text });
        setMessages((prev) => prev.map((m) => (m.id === pendingId ? {
          ...m,
          content: `🤖 ${interp.message ?? "어느 로봇에서 실행할까요?"}`,
          pending: false,
          needsRobot: true,
          robots: interp.robots ?? [],
          pendingCommand: text,
        } : m)));
        setLoading(false);
        return;
      }
      if (interp.matched) {
        const target = interp.target_robot ?? (interp.robot_number ? `${interp.robot_number}번(미등록)` : "로컬");
        const picked = actionSummary(interp, target, text);

        // 주행 액션은 브라우저 팝업 대신 챗 안에서 실행/취소 버튼으로 확인
        if (interp.result?.requires_confirm) {
          setComm({ phase: "await_confirm", label: picked });
          setMessages((prev) => prev.map((m) => (m.id === pendingId ? {
            ...m,
            content: `⚠️ ${interp.result?.message ?? "이 동작은 로봇을 실제로 움직입니다."}\n(${picked})`,
            pending: false,
            needsConfirm: true,
            confirmCommand: text,
            confirmLabel: picked,
          } : m)));
          setLoading(false);
          return;
        }

        renderInterpResult(pendingId, interp, target, text);
        setLoading(false);
        return;
      }
      // 미매칭 → 실행하지 않고 안내만 (전진/이동 같은 저수준 move 폴백은 NAV2 충돌·오작동 방지 위해 제거)
      setComm({ phase: "not_matched", label: text });
      setMessages((prev) => prev.map((m) => (m.id === pendingId ? { ...m, content: NOT_MATCHED, pending: false } : m)));
    } catch (err) {
      console.error(err);
      setComm({ phase: "fail", label: text, detail: "명령 처리 오류" });
      setMessages((prev) => prev.map((m) => (m.id === pendingId ? { ...m, content: "❌ 명령 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", pending: false } : m)));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AdminShell title="챗봇 제어">
      <div className="grid gap-4 md:grid-cols-3">
        {/* Left 2 cols: Chat Room */}
        <Card className="md:col-span-2 flex flex-col h-[70vh]">
          <CardHeader className="border-b border-slate-100 py-3 shrink-0">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <MessageSquare className="h-4 w-4 text-primary" /> 로컬 LLM 로봇 대화형 콘솔
              </CardTitle>
              <button
                type="button"
                onClick={() => void stopAll()}
                title="모든 주행로봇 즉시 정지"
                className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-50 px-3 py-1 text-[11px] font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100"
              >
                <Ban className="h-3.5 w-3.5" /> 전체 정지
              </button>
            </div>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 flex flex-col p-4">
            {/* Scrollable messages area */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-2 mb-4">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
                  <Bot className="h-8 w-8 text-slate-300" />
                  <p className="text-xs">명령어나 질문을 아래 입력해 보세요.</p>
                </div>
              )}
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    "flex gap-3 text-sm max-w-[85%]",
                    m.role === "user" ? "ml-auto flex-row-reverse" : "mr-auto"
                  )}
                >
                  <div
                    className={cn(
                      "flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-full border text-xs shadow-sm",
                      m.role === "user" ? "bg-primary text-primary-foreground border-primary" : "bg-white text-slate-700 border-slate-200"
                    )}
                  >
                    {m.role === "user" ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                  </div>
                  <div className="flex flex-col gap-1 items-start min-w-0">
                    <div
                      className={cn(
                        "rounded-2xl px-4 py-2 text-leading shadow-sm whitespace-pre-wrap",
                        m.role === "user" ? "rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm bg-slate-50 border border-slate-100 text-slate-800"
                      )}
                    >
                      {m.pending && m.content === "자연어 분석 중..." ? (
                        <span className="inline-flex items-center gap-1 py-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" />
                          <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:0.2s]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:0.4s]" />
                        </span>
                      ) : (
                        m.content
                      )}
                    </div>
                    {m.stoppable ? (
                      <button
                        type="button"
                        disabled={m.stopped}
                        onClick={() => void stopAction(m)}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full border px-3 py-1 text-[11px] font-medium shadow-sm transition",
                          m.stopped
                            ? "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
                            : "border-rose-200 bg-rose-50 text-rose-600 hover:bg-rose-100"
                        )}
                      >
                        <Square className="h-3 w-3" fill="currentColor" />
                        {m.stopped ? "정지됨" : `정지${m.robotLabel ? ` (${m.robotLabel})` : ""}`}
                      </button>
                    ) : null}
                    {m.needsConfirm ? (
                      <div className="flex flex-wrap gap-1.5 pt-0.5">
                        <button
                          type="button"
                          onClick={() => void confirmExecute(m)}
                          className="inline-flex items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-[11px] font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
                        >
                          <Send className="h-3 w-3" /> 실행
                        </button>
                        <button
                          type="button"
                          onClick={() => cancelConfirm(m)}
                          className="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-slate-50 px-3 py-1 text-[11px] font-medium text-slate-600 shadow-sm transition hover:bg-slate-100"
                        >
                          취소
                        </button>
                      </div>
                    ) : null}
                    {m.needsRobot && m.robots?.length ? (
                      <div className="flex flex-wrap gap-1.5 pt-0.5">
                        {m.robots.map((r) => (
                          <button
                            key={r.name}
                            type="button"
                            onClick={() => { const cmd = m.pendingCommand ?? ""; setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, needsRobot: false } : x))); void handleSend(r.number ? `주행로봇${r.number} ${cmd}` : cmd); }}
                            className="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-[11px] font-medium text-sky-700 shadow-sm transition hover:bg-sky-100"
                          >
                            <Bot className="h-3 w-3" />{r.name}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>

            {/* Input area */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend(input);
              }}
              className="flex items-center gap-2 shrink-0 border-t border-slate-100 pt-3"
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder='로봇 지시사항을 자연어로 입력해 보세요... (예: 웃어줘, 주행로봇1 LCD에 안녕 출력)'
                className="flex-1"
                disabled={loading}
              />
              <Button type="submit" disabled={!input.trim() || loading} size="icon">
                <Send className="h-4 w-4" />
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Right 1 col: 음성 제어 + 통신 상태 + 명령어 가이드 */}
        <div className="flex flex-col gap-4 h-[70vh] min-h-0 overflow-y-auto">
        <VoiceControlPanel
          onResult={(r) => {
            if (r.requires_confirm) toast.warning(r.spoken);
            else if (r.success) toast.success(r.spoken);
            else toast.error(r.spoken);
          }}
        />
        <CommStatusPanel comm={comm} robots={robots} activeRobotId={activeRobotId} onSelectRobot={setActiveRobotId} />
        <Card className="flex-1 min-h-0 flex flex-col">
          <CardHeader className="border-b border-slate-100 py-3 shrink-0">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Cpu className="h-4 w-4 text-emerald-600" /> 제어 명령어 가이드
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 overflow-y-auto space-y-4 text-xs">
            <div className="rounded-lg border border-sky-100 bg-sky-50 p-2.5 text-[11px] text-sky-700 leading-normal">
              🤖 <b>로봇 지정</b>: 문장 앞에 <b>"주행로봇1"</b>, <b>"2번"</b> 처럼 번호를 붙이면 해당 로봇으로 전송됩니다. 번호가 없으면 기본(중앙) 로봇으로 실행돼요.
            </div>

            <div>
              <h4 className="font-bold text-slate-800 mb-1 flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-full bg-orange-500" /> 주행 로봇 (PinkyPro)
              </h4>
              <ul className="space-y-1.5 text-slate-600 pl-3 border-l border-orange-100">
                <li>• <b>정지</b>: "2번 정지", "멈춰"</li>
                <li>• <b>표정</b>: "웃어줘", "화내줘", "슬픈 표정", "인사해" (기쁨·화남·슬픔·인사·신남 등)</li>
                <li>• <b>사운드</b>: "벨 소리 내줘", "삐 소리내", "알람 울려"</li>
                <li>• <b>LCD 문구</b>: "주행로봇1 LCD에 안녕 출력"</li>
                <li>• <b>LED</b>: "주행로봇1 LED 빨간색으로 켜줘", "LED 꺼줘"</li>
              </ul>
            </div>

            <div>
              <h4 className="font-bold text-slate-800 mb-1 flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" /> 주행·주차 (학습 액션/시나리오)
              </h4>
              <ul className="space-y-1.5 text-slate-600 pl-3 border-l border-emerald-100">
                <li>• <b>위치 이동</b>: "주행로봇1 충전소 이동", "3번 A로 가"</li>
                <li>• <b>주차</b>: "2번 주차해", "주차 시작"</li>
                <li>• <b>순회/복귀</b>: "1번 순회 시작", "홈으로 가"</li>
                <li className="text-[10px] text-slate-400">※ 주행·주차는 안전을 위해 실행 전 확인 팝업이 뜹니다.</li>
              </ul>
            </div>

            <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-100 text-[11px] text-slate-500 leading-normal">
              💬 <b>작동 원리:</b> 챗봇이 자연어 의도를 분석해 <b>표정·사운드·정지</b>는 로봇에 바로 명령하고, <b>주행·주차</b>는 학습 페이지에 등록된 액션/시나리오를 LLM으로 매칭해 실행합니다. 대화 내역은 MariaDB(<code>rc_conversations</code>·<code>rc_messages</code>)에 기록됩니다.
            </div>
          </CardContent>
        </Card>
        </div>
      </div>
    </AdminShell>
  );
}

// ── 우측 통신 상태 패널 ───────────────────────────────────────────────────────
// 두 축을 보여준다:
//  1) 명령 왕복 — 마지막 채팅 명령이 해석→(확인)→발행→결과회신 중 어디까지 갔나 (comm)
//  2) 로봇 진행 — 선택 로봇의 fleet_status(mission)·브릿지 링크·배터리 (WS + telemetry)
const COMM_STEPS = [
  { key: "interpret", label: "① 입력 해석·매칭 (LLM)" },
  { key: "target", label: "② 로봇 지정·실행 확인" },
  { key: "publish", label: "③ fleet_cmd 발행 (토픽)" },
  { key: "result", label: "④ 결과 회신 (ack)" },
] as const;

// comm 단계 → 각 스텝의 상태(대기/진행/완료/실패)로 변환.
function commStepStatus(phase: CommState["phase"], stepKey: string): "pending" | "active" | "done" | "fail" {
  const order = ["interpret", "target", "publish", "result"];
  const idx = order.indexOf(stepKey);
  switch (phase) {
    case "idle": return "pending";
    case "interpreting": return idx === 0 ? "active" : "pending";
    case "not_matched": return idx === 0 ? "fail" : "pending";
    case "needs_robot":
    case "await_confirm": return idx === 0 ? "done" : idx === 1 ? "active" : "pending";
    case "sending": return idx <= 1 ? "done" : idx === 2 ? "active" : "pending";
    case "ok": return "done";
    case "fail": return idx <= 2 ? "done" : "fail";
    default: return "pending";
  }
}

function commHeadline(comm: CommState): { text: string; tone: "muted" | "active" | "warn" | "ok" | "fail" } {
  switch (comm.phase) {
    case "idle": return { text: "대기 중 — 명령을 입력하면 진행 상태가 표시됩니다.", tone: "muted" };
    case "interpreting": return { text: `해석 중: ${comm.label}`, tone: "active" };
    case "needs_robot": return { text: "로봇 선택 대기 — 어느 로봇에서 실행할지 고르세요.", tone: "warn" };
    case "await_confirm": return { text: `실행 확인 대기 — 발행하면 로봇이 실제로 움직입니다. (${comm.label})`, tone: "warn" };
    case "sending": return { text: `발행됨 · 결과 대기: ${comm.label}`, tone: "active" };
    case "not_matched": return { text: "미매칭 — 등록된 액션/시나리오와 일치하지 않아 발행하지 않았습니다.", tone: "warn" };
    case "ok": return { text: `완료: ${comm.label}`, tone: "ok" };
    case "fail": return { text: "실패: " + comm.label, tone: "fail" };
  }
}

function CommStatusPanel({ comm, robots, activeRobotId, onSelectRobot }: {
  comm: CommState;
  robots: Robot[];
  activeRobotId: number | null;
  onSelectRobot: (id: number) => void;
}) {
  const activeRobot = robots.find((r) => r.id === activeRobotId) ?? null;
  const head = commHeadline(comm);
  const headTone = {
    muted: "text-slate-400", active: "text-sky-600", warn: "text-amber-600", ok: "text-emerald-600", fail: "text-rose-600",
  }[head.tone];

  return (
    <Card className="flex flex-col shrink-0">
      <CardHeader className="border-b border-slate-100 py-3 shrink-0">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Activity className="h-4 w-4 text-sky-600" /> 통신 상태
          </CardTitle>
          {robots.length > 0 && (
            <select
              value={activeRobotId ?? ""}
              onChange={(e) => onSelectRobot(Number(e.target.value))}
              className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 shadow-sm"
            >
              {robots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          )}
        </div>
      </CardHeader>
      <CardContent className="p-4 space-y-3 text-xs">
        {/* 축1: 명령 왕복 */}
        <div className={cn("text-[11px] font-medium", headTone)}>{head.text}</div>
        <ol className="space-y-1.5">
          {COMM_STEPS.map((s) => {
            const st = commStepStatus(comm.phase, s.key);
            const Icon = st === "done" ? CheckCircle2 : st === "active" ? Loader2 : st === "fail" ? XCircle : CircleDashed;
            const color = st === "done" ? "text-emerald-500" : st === "active" ? "text-sky-500" : st === "fail" ? "text-rose-500" : "text-slate-300";
            return (
              <li key={s.key} className="flex items-center gap-2">
                <Icon className={cn("h-3.5 w-3.5 shrink-0", color, st === "active" && "animate-spin")} />
                <span className={cn(st === "pending" ? "text-slate-400" : "text-slate-700")}>{s.label}</span>
              </li>
            );
          })}
        </ol>

        {/* 축2: 로봇 진행 (fleet_status + 링크) */}
        <div className="border-t border-slate-100 pt-3">
          {activeRobot ? (
            <RobotLiveStatus key={activeRobot.id} robot={activeRobot} />
          ) : (
            <div className="text-[11px] text-slate-400">활성 주행로봇이 없습니다. IP 관리에서 pinky 로봇을 등록·활성화하세요.</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// 선택 로봇의 실시간 상태: /api/control/ws/state(mission·battery) + /api/control/telemetry(브릿지 링크).
function RobotLiveStatus({ robot }: { robot: Robot }) {
  const [state, setState] = useState<Nav2State | null>(null);
  const [wsError, setWsError] = useState(false);
  const [link, setLink] = useState<ControlLinkInfo | null>(null);

  // fleet_status(mission)·pose·battery 를 1초 주기로 푸시받는다.
  useEffect(() => {
    setState(null);
    setWsError(false);
    const ws = new WebSocket(adminApi.controlStateWsUrl(robot.id));
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) { setState(payload.state as Nav2State); setWsError(false); }
        else if (payload && payload.ok === false) setWsError(true);
      } catch { /* malformed state frame */ }
    };
    ws.onerror = () => setWsError(true);
    return () => ws.close();
  }, [robot.id]);

  // 브릿지 링크 신선도·명령 토픽 구독자 수 (3초 폴링).
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const t = await adminApi.controlTelemetry();
        if (!alive) return;
        setLink(Object.values(t).find((e) => e.ip === robot.ip_address) ?? null);
      } catch { /* telemetry 실패 무시 */ }
    };
    void poll();
    const id = window.setInterval(poll, 3000);
    return () => { alive = false; window.clearInterval(id); };
  }, [robot.ip_address]);

  const mission = state?.mission;
  const percent = state?.battery?.percent;
  const bridgeUp = (link?.cmd_subscribers ?? 0) > 0;
  const statusAge = link?.status_age_sec;
  const online = statusAge != null && statusAge < 30;
  const missionActive = Boolean(mission && mission.status && mission.status !== "idle");

  return (
    <div className="space-y-2">
      {/* 링크 배지 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
          online ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400"
        )}>
          {online ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
          {online ? "ROS 상태 수신" : "ROS 상태 미수신"}
        </span>
        <span className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
          bridgeUp ? "bg-sky-50 text-sky-600" : "bg-amber-50 text-amber-600"
        )}>
          {bridgeUp ? "ROS 브릿지 연결" : "HTTP로 명령 전송"}
        </span>
        {percent != null && (
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2 py-0.5 text-[10px] text-slate-500">
            🔋 {Math.round(percent)}%
          </span>
        )}
        {wsError && <span className="text-[10px] text-rose-500">상태 수신 오류</span>}
      </div>

      {/* fleet_status 진행 */}
      {mission ? (
        <div className="space-y-1.5">
          <div className="text-[11px] text-slate-600">
            로봇 상태: <b className={missionActive ? "text-emerald-600" : "text-slate-500"}>{missionActive ? "진행 중" : "대기(idle)"}</b>
            {mission.loop && <span className="ml-1 text-slate-400">· 반복</span>}
          </div>
          {mission.names.length > 0 && (
            <ul className="space-y-0.5 pl-1">
              {mission.names.map((n) => {
                const isCurrent = n === mission.current;
                return (
                  <li key={n} className={cn("flex items-center gap-1.5 text-[11px]", isCurrent ? "font-semibold text-sky-700" : "text-slate-500")}>
                    {isCurrent ? <ArrowRight className="h-3 w-3 text-sky-500" /> : <CircleDashed className="h-3 w-3 text-slate-300" />}
                    {n} 구역{isCurrent ? " (현재)" : ""}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : (
        <div className="text-[11px] text-slate-400">{wsError ? "상태를 불러올 수 없습니다." : "상태 수신 대기 중…"}</div>
      )}
    </div>
  );
}
