import { Mic, MicOff, Radio, Loader2, AlertTriangle, ShieldAlert } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useRealtimeVoice } from "@/lib/use-realtime-voice";
import { type VoiceExecuteResult } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

// 음성 대화 제어 패널 — OpenAI Realtime(WebRTC) STT/TTS + 로봇 제어.
// 예: "주행로봇 1번 직진 10cm 이동 후 왼쪽으로 회전하고 10cm 이동 후 정지"
export function VoiceControlPanel({
  onResult,
}: {
  onResult?: (result: VoiceExecuteResult) => void;
}) {
  const voice = useRealtimeVoice(onResult);
  const { status, live, talking, allowDriving, lastError, transcripts } = voice;

  return (
    <Card className="flex flex-col shrink-0">
      <CardHeader className="border-b border-slate-100 py-3 shrink-0">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Radio className={cn("h-4 w-4", live ? "text-emerald-500" : "text-slate-400")} /> 음성 제어
          </CardTitle>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
              status === "live" && "bg-emerald-50 text-emerald-600",
              status === "connecting" && "bg-amber-50 text-amber-600",
              status === "idle" && "bg-slate-100 text-slate-500",
              status === "error" && "bg-rose-50 text-rose-600",
            )}
          >
            {status === "live" ? "대화 중" : status === "connecting" ? "연결 중" : status === "error" ? "오류" : "대기"}
          </span>
        </div>
      </CardHeader>

      <CardContent className="p-4 space-y-3 text-xs">
        {/* 시작/종료 버튼 */}
        <div className="flex items-center gap-2">
          {!live && status !== "connecting" ? (
            <button
              type="button"
              onClick={() => void voice.start()}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <Mic className="h-4 w-4" /> 음성 시작
            </button>
          ) : (
            <button
              type="button"
              onClick={() => voice.stop()}
              disabled={status === "connecting"}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:opacity-60"
            >
              {status === "connecting" ? <Loader2 className="h-4 w-4 animate-spin" /> : <MicOff className="h-4 w-4" />}
              {status === "connecting" ? "연결 중…" : "음성 종료"}
            </button>
          )}
        </div>

        {/* 푸시투토크(PTT): 누르는 동안만 듣고, 떼면 전송한다 */}
        {live && (
          <button
            type="button"
            onPointerDown={(e) => { e.preventDefault(); voice.pttStart(); }}
            onPointerUp={() => voice.pttStop()}
            onPointerLeave={() => voice.pttStop()}
            onContextMenu={(e) => e.preventDefault()}
            className={cn(
              "flex w-full select-none touch-none items-center justify-center gap-2 rounded-lg border px-3 py-3 text-sm font-semibold shadow-sm transition",
              talking
                ? "border-emerald-500 bg-emerald-500 text-white scale-[0.99]"
                : "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100",
            )}
          >
            <Mic className="h-4 w-4" /> {talking ? "듣는 중… (떼면 전송)" : "누르고 말하기"}
          </button>
        )}

        {/* 음성 주행 허용 토글 — CLAUDE.md: 실주행은 사람 감독하에서만 */}
        <label className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <span className="flex items-center gap-1.5 font-medium text-slate-600">
            <ShieldAlert className="h-3.5 w-3.5 text-amber-500" /> 음성 주행 허용
          </span>
          <input
            type="checkbox"
            checked={allowDriving}
            onChange={(e) => voice.setAllowDriving(e.target.checked)}
            className="h-4 w-4 accent-primary"
          />
        </label>
        {!allowDriving && (
          <p className="text-[11px] text-amber-600">주행이 잠겨 있어 이동 명령은 실행되지 않습니다(정지는 항상 가능).</p>
        )}

        {/* 사용 예시 */}
        <div className="rounded-lg border border-slate-100 bg-white px-3 py-2 text-[11px] leading-relaxed text-slate-500">
          이렇게 말해보세요:<br />
          "1번 로봇 직진 10cm 이동 후 왼쪽으로 회전하고 10cm 이동 후 정지"<br />
          "2번 앞으로 20센티" · "정지" · "1번 뒤로 돌아"(유턴)<br />
          다중 주문(최대 4개): "1번 A로 이동 후 완료되면 뒤로 돌아 30cm 직진"<br />
          "직진/앞으로"는 앞에 장애물이 있으면 라이다로 자동 정지합니다.
        </div>

        {lastError && (
          <div className="flex items-start gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] text-rose-600">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span className="break-all">{lastError}</span>
          </div>
        )}

        {/* 실시간 대화 자막 */}
        {transcripts.length > 0 && (
          <div className="space-y-1.5 max-h-40 overflow-y-auto pt-1">
            {transcripts.slice(-8).map((t) => (
              <div
                key={t.id}
                className={cn(
                  "rounded-lg px-2.5 py-1.5 text-[11px]",
                  t.role === "user" ? "bg-primary/10 text-primary" : "bg-slate-100 text-slate-600",
                )}
              >
                <span className="font-semibold">{t.role === "user" ? "나: " : "🤖 로봇: "}</span>
                {t.text}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
