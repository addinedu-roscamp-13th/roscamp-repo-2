import { createFileRoute } from "@tanstack/react-router";
import { MessageSquare, Send, Bot, User, Cpu, Square, Ban } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { adminApi } from "@/lib/admin-api";
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

const GREETING = [
  "안녕하세요! 🤖 주행로봇 제어 챗봇입니다.",
  "",
  "이렇게 말해보세요:",
  '• "웃어줘" · "벨 소리" · "삐 소리내" · "정지"',
  '• "주행로봇1 LCD에 안녕 출력"',
  '• "주행로봇1 뒤로 10cm" · "2번 앞으로 20cm" · "1번 왼쪽 30도"',
  '• "2번 주차해" · "주행로봇1 충전소 이동"',
  "",
  "문장 앞에 \"주행로봇1\", \"2번\" 처럼 번호를 붙이면 해당 로봇으로 전송됩니다.",
].join("\n");

const NOT_MATCHED = [
  "🤖 해당 명령을 이해하지 못했어요.",
  "",
  "이렇게 말해보세요:",
  '• "웃어줘" · "벨 소리" · "삐 소리내" · "정지"',
  '• "주행로봇1 LCD에 안녕 출력"',
  '• "주행로봇1 뒤로 10cm" · "2번 앞으로 20cm" · "1번 왼쪽 30도"',
  '• "2번 주차해" · "주행로봇1 충전소 이동"',
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
  if (actionType === "relative_move") return `${target} → 짧은 전/후진`;
  if (actionType === "relative_turn") return `${target} → 짧은 좌/우 회전`;
  if (actionType === "mission_stop" || actionType === "stop") return `${target} → 정지`;
  if (actionType === "home") return `${target} → 홈 복귀`;
  if (actionType === "parking_start") return `${target} → 주차 시작`;
  if (actionType === "emotion") return `${target} → ${extractEmotionLabel(command) ?? "표정 변경"}`;
  if (actionType === "lcd_text") return `${target} → LCD 문구 표시`;
  if (actionType === "buzzer") return `${target} → 소리 재생`;
  return `${target} → ${interp.kind === "scenario" ? "시나리오 실행" : "명령 실행"}`;
}

function ChatPage() {
  const [messages, setMessages] = useState<MessageItem[]>([{ id: "greeting", role: "assistant", content: GREETING }]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

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
    const detail = interp.result?.stopped_at ? `'${interp.result.stopped_at}'에서 중단` : interp.result?.response ?? interp.result?.message ?? "알 수 없는 에러";
    const summary = actionSummary(interp, target, command);
    const doneMsg = ok
      ? `🤖 실행했어요.\n\n${summary}`
      : `❌ 실행 실패: ${detail}`;
    const stoppable = Boolean(ok) && (interp.kind === "scenario" || DRIVING_ACTION_TYPES.has(interp.result?.action_type ?? ""));
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: doneMsg, pending: false, needsConfirm: false, stoppable, robotNumber: interp.robot_number ?? null, robotLabel: target } : m)));
  };

  const confirmExecute = async (m: MessageItem) => {
    const cmd = m.confirmCommand ?? "";
    setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, needsConfirm: false, pending: true, content: `🤖 ${m.confirmLabel ?? ""} 실행 중...` } : x)));
    try {
      const resMsg = await adminApi.sendChatMessage(cmd, { sessionId: SESSION_ID, execute: true, confirm: true });
      const interp = resMsg.interpretation;
      const target = interp.target_robot ?? (interp.robot_number ? `${interp.robot_number}번(미등록)` : "로컬");
      renderInterpResult(m.id, interp, target, cmd);
    } catch {
      setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, pending: false, content: "❌ 실행 중 오류가 발생했습니다." } : x)));
    }
  };

  const cancelConfirm = (m: MessageItem) => {
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
      setMessages((prev) => prev.map((m) => (m.id === pendingId ? { ...m, content: NOT_MATCHED, pending: false } : m)));
    } catch (err) {
      console.error(err);
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

        {/* Right 1 col: Commands Guide */}
        <Card className="flex flex-col h-[70vh]">
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
    </AdminShell>
  );
}
