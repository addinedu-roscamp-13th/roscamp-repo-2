import { useCallback, useEffect, useRef, useState } from "react";

import { LIBI_REALTIME_TOOLS, prepareTool, type PendingCall } from "./libi-tools";
import type { CatalogBook } from "./books-api";

/**
 * OpenAI Realtime(WebRTC) 음성 대화 훅 — **푸시투토크(누르고 말하기)**.
 *
 *   마이크(버튼 누르는 동안만) → OpenAI STT → LiBi 응답 → TTS → 스피커
 *
 * ## 왜 서버 VAD 를 안 쓰나
 * 세션을 `turn_detection: null` 로 열어 서버가 발화 끝을 스스로 판단하지 않게 한다.
 * 도서관 로비처럼 시끄러운 곳에서 항상 듣기로 두면 주변 소리에 반응해 엉뚱한 요청이
 * 나간다. 버튼을 뗄 때만 `input_audio_buffer.commit` + `response.create` 를 보낸다.
 *
 * ## 도구 실행 경로
 * 도구 정의(`LIBI_REALTIME_TOOLS`)와 실행(`prepareTool`)은 화면 챗과 **같은 것**을 쓴다.
 * 특히 되돌리기 어려운 도구(대여 신청·예약·취소…)는 여기서도 바로 실행하지 않는다 —
 * `prepareTool` 이 confirm 을 돌려주면 화면 확인 카드로 넘기고, 모델에게는 "사용자
 * 확인 대기 중"이라고 알려 말로 안내하게 한다. 음성이라고 확인 절차를 건너뛰지 않는다.
 *
 * ⚠️ 마이크는 보안 컨텍스트에서만 잡힌다: http://localhost 는 OK, LAN IP·도메인은 HTTPS 필요.
 */

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

// GA(정식) SDP 교환 엔드포인트. 모델·음성·PTT 설정은 발급 시 서버가 이미 지정했다.
const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

// ── 버튼을 뗀 뒤 말끝을 지키는 타이밍 ─────────────────────────────────────────
// 사람은 마지막 음절과 거의 동시에 버튼을 뗀다. 그 순간 마이크를 끄면 끝말이 통째로
// 날아가고, 모델은 잘린 문장에 답한다("혼자 엉뚱하게 빨리 대답").
const TAIL_MIC_MS = 250;
// commit 은 데이터채널(SCTP), 음성은 미디어 경로(RTP)로 따로 간다. 미디어가 더 늦게
// 닿으므로 마이크를 끈 직후 커밋하면 마지막 패킷들이 버퍼에 담기기 전에 확정돼 버린다.
const COMMIT_DELAY_MS = 250;
// 이보다 짧게 누른 건 오조작으로 본다 — 빈 버퍼를 커밋하면 OpenAI 가 에러를 주고,
// 소리가 거의 없는 버퍼로 응답을 요청하면 모델이 허공에 대고 말한다.
const MIN_PRESS_MS = 350;

export type VoiceStatus = "idle" | "connecting" | "live" | "error";

export interface RealtimeVoiceHandlers {
  /** 사용자 발화가 전사됐을 때 — 채팅 목록에 사용자 말풍선으로 넣는다. */
  onUserText?: (text: string) => void;
  /** LiBi 음성 응답의 전사 — 봇 말풍선으로 넣는다. */
  onBotText?: (text: string) => void;
  /** 도구가 즉시 실행된 결과(조회형) — 결과 문구와 책 목록을 화면에 붙인다. */
  onToolResult?: (text: string, books?: CatalogBook[]) => void;
  /** 확인이 필요한 도구 — 화면 확인 카드를 띄운다. */
  onConfirm?: (pending: PendingCall) => void;
}

export interface UseRealtimeVoice {
  status: VoiceStatus;
  live: boolean;
  /** 버튼을 누르고 있는 중(= 마이크 ON) */
  talking: boolean;
  lastError: string | null;
  /** 마이크 버튼을 누를 때 — 필요하면 접속부터 하고 이어서 듣기 시작. */
  pressStart: () => Promise<void>;
  /** 버튼을 뗄 때 — 마이크 OFF + 발화 확정 + 응답 요청. */
  pressStop: () => void;
  stop: () => void;
}

export function useRealtimeVoice(handlers: RealtimeVoiceHandlers = {}): UseRealtimeVoice {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [talking, setTalking] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // 이벤트 핸들러가 항상 최신 콜백을 보게 한다(재연결 없이 갱신).
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  // 버튼을 뗀 시점에 talking state 가 아직 안 반영됐을 수 있어 ref 로도 들고 있는다.
  const talkingRef = useRef(false);
  // 버튼이 **물리적으로** 눌려 있는지. 첫 누름은 접속(수 초)을 기다리는데, 그 사이에
  // 손을 떼면 talkingRef 는 아직 false 라 pressStop 이 그냥 빠져나간다 — 그 상태로
  // 접속이 끝나면 마이크가 켜진 채 남는다. 그래서 '눌림'과 '듣는 중'을 따로 둔다.
  const pressedRef = useRef(false);
  // 응답 동시성 — 진행 중인 response 가 있는데 response.create 를 또 보내면
  // 'conversation_already_has_active_response' 오류가 난다. 끝난 뒤로 미룬다.
  const responseActiveRef = useRef(false);
  const pendingResponseRef = useRef(false);
  // 버튼을 뗀 뒤 말끝을 기다리는 타이머와, 그때 실행할 확정 작업. 기다리는 중에 다시
  // 누르면 먼저 이걸 즉시 실행해 앞 발화를 흘려보내고 새 발화를 시작한다.
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingFlushRef = useRef<(() => void) | null>(null);
  const pressStartedAtRef = useRef(0);

  const requestModelResponse = useCallback(() => {
    const dc = dcRef.current;
    if (!dc || dc.readyState !== "open") return;
    if (responseActiveRef.current) {
      pendingResponseRef.current = true;
      return;
    }
    responseActiveRef.current = true;
    dc.send(JSON.stringify({ type: "response.create" }));
  }, []);

  /** 대기 중인 '말끝 기다리기'를 지금 끝낸다(재누름·정리 시). */
  const flushNow = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const flush = pendingFlushRef.current;
    pendingFlushRef.current = null;
    flush?.();
  }, []);

  const cleanup = useCallback(() => {
    if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
    flushTimerRef.current = null;
    pendingFlushRef.current = null;
    try { dcRef.current?.close(); } catch { /* noop */ }
    try { pcRef.current?.close(); } catch { /* noop */ }
    micRef.current?.getTracks().forEach((t) => t.stop());
    if (audioRef.current) {
      audioRef.current.srcObject = null;
      audioRef.current.remove();
    }
    dcRef.current = null;
    pcRef.current = null;
    micRef.current = null;
    audioRef.current = null;
    talkingRef.current = false;
    pressedRef.current = false;
    responseActiveRef.current = false;
    pendingResponseRef.current = false;
  }, []);

  const stop = useCallback(() => {
    cleanup();
    setStatus("idle");
    setTalking(false);
  }, [cleanup]);

  /** 모델에게 function 실행 결과를 되돌리고, 이어서 말하게 한다. */
  const sendFunctionOutput = useCallback((callId: string, payload: unknown) => {
    const dc = dcRef.current;
    if (!dc || dc.readyState !== "open") return;
    dc.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "function_call_output", call_id: callId, output: JSON.stringify(payload) },
    }));
    requestModelResponse();
  }, [requestModelResponse]);

  const handleFunctionCall = useCallback(
    async (name: string, callId: string, argsJson: string) => {
      // 인자 파싱 — 발화가 끊기면 argsJson 이 잘려 올 수 있다. 그땐 실행하지 않고 되묻게 한다.
      let args: unknown;
      try {
        args = JSON.parse(argsJson || "{}");
      } catch {
        sendFunctionOutput(callId, {
          ok: false,
          text: "요청이 중간에 끊겼어요. 한 번만 더 말씀해 주세요.",
        });
        return;
      }

      try {
        const prepared = await prepareTool(name, args);
        switch (prepared.kind) {
          case "run":
            handlersRef.current.onToolResult?.(prepared.result.text, prepared.result.books);
            sendFunctionOutput(callId, prepared.result);
            break;
          case "confirm":
            // 음성으로도 임의 실행하지 않는다 — 화면 확인 카드로 넘긴다.
            handlersRef.current.onConfirm?.(prepared.pending);
            sendFunctionOutput(callId, {
              ok: false,
              awaiting_user_confirmation: true,
              text: `«${prepared.pending.sentence}» 확인 카드를 화면에 띄웠습니다. 사용자가 「네」를 눌러야 실행됩니다. 그렇게 안내하세요.`,
            });
            break;
          case "choose":
            handlersRef.current.onToolResult?.(prepared.text, prepared.books);
            sendFunctionOutput(callId, {
              ok: false,
              text: `${prepared.text} 후보: ${prepared.books.map((b) => b.title.KR).join(", ")}`,
            });
            break;
          case "error":
            sendFunctionOutput(callId, { ok: false, text: prepared.text });
            break;
        }
      } catch (e) {
        setLastError(e instanceof Error ? e.message : String(e));
        sendFunctionOutput(callId, {
          ok: false,
          text: "처리 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요.",
        });
      }
    },
    [sendFunctionOutput],
  );

  const handleEvent = useCallback((evt: Record<string, unknown>) => {
    switch (evt.type as string) {
      case "response.created":
        responseActiveRef.current = true;
        break;
      case "response.done":
        responseActiveRef.current = false;
        if (pendingResponseRef.current) {
          pendingResponseRef.current = false;
          requestModelResponse();
        }
        break;
      case "response.function_call_arguments.done":
        void handleFunctionCall(
          evt.name as string,
          evt.call_id as string,
          evt.arguments as string,
        );
        break;
      case "conversation.item.input_audio_transcription.completed":
        handlersRef.current.onUserText?.(((evt.transcript as string) ?? "").trim());
        break;
      case "response.audio_transcript.done":
        handlersRef.current.onBotText?.(((evt.transcript as string) ?? "").trim());
        break;
      case "error":
        setLastError(JSON.stringify(evt.error ?? evt));
        break;
      default:
        break;
    }
  }, [handleFunctionCall, requestModelResponse]);

  /** 접속 — ephemeral 발급 → WebRTC 연결 → 도구 목록 등록. */
  const connect = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("이 환경에서는 마이크를 쓸 수 없어요. HTTPS 로 접속해 주세요.");
    }
    const res = await fetch(`${API_BASE}/api/voice/session`, {
      method: "POST",
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) {
      // FastAPI 는 {detail: "..."} 로 준다. detail 만 그대로 띄우면 "Not Found" 처럼
      // 맥락 없는 문구가 나오므로 항상 '무엇이 실패했는지'를 앞에 붙인다.
      const detail = await res.json().catch(() => null);
      throw new Error(
        `음성 세션 발급 실패 (${res.status}): ${detail?.detail ?? res.statusText}`,
      );
    }
    const session = (await res.json()) as { client_secret: string };

    const pc = new RTCPeerConnection();
    pcRef.current = pc;

    // LiBi TTS 재생용 엘리먼트
    const audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    audioRef.current = audioEl;
    pc.ontrack = (e) => { audioEl.srcObject = e.streams[0]; };

    // 브라우저 기본값에 맡기지 않고 명시한다 — 스피커로 나가는 LiBi 목소리가 마이크로
    // 되돌아가면(에코) 전사가 엉킨다. 잡음 억제·자동 게인은 로비 소음과 작은 목소리 대응.
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
    micRef.current = mic;
    mic.getTracks().forEach((track) => pc.addTrack(track, mic));
    // PTT: 접속 직후 마이크는 OFF — 버튼을 누르는 동안만 켠다.
    mic.getAudioTracks().forEach((t) => { t.enabled = false; });

    const dc = pc.createDataChannel("oai-events");
    dcRef.current = dc;
    dc.onmessage = (e) => {
      try { handleEvent(JSON.parse(e.data)); } catch { /* non-JSON */ }
    };
    // 도구 목록은 여기서 올린다 — 정본은 libi-tools.ts 하나뿐이라 서버(파이썬)에
    // 같은 정의를 복사해두지 않는다.
    dc.onopen = () => {
      dc.send(JSON.stringify({
        type: "session.update",
        session: { type: "realtime", tools: LIBI_REALTIME_TOOLS, tool_choice: "auto" },
      }));
    };

    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      if (st === "connected") setStatus("live");
      else if (st === "failed" || st === "disconnected" || st === "closed") {
        setStatus((prev) => (prev === "idle" ? prev : "error"));
      }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdpRes = await fetch(REALTIME_CALLS_URL, {
      method: "POST",
      body: offer.sdp,
      headers: {
        Authorization: `Bearer ${session.client_secret}`,
        "Content-Type": "application/sdp",
      },
    });
    if (!sdpRes.ok) {
      throw new Error(`Realtime 연결 실패 (${sdpRes.status}): ${await sdpRes.text()}`);
    }
    await pc.setRemoteDescription({ type: "answer", sdp: await sdpRes.text() });

    // connectionState 가 connected 로 갈 때까지 기다린다 — 여기서 기다리지 않으면
    // 첫 발화가 아직 붙지 않은 채널로 나가 통째로 사라진다.
    await new Promise<void>((resolve, reject) => {
      if (pc.connectionState === "connected") return resolve();
      const timer = setTimeout(() => reject(new Error("음성 서버 연결이 지연되고 있어요.")), 10000);
      pc.addEventListener("connectionstatechange", function onChange() {
        if (pc.connectionState === "connected") {
          clearTimeout(timer);
          pc.removeEventListener("connectionstatechange", onChange);
          resolve();
        } else if (["failed", "closed"].includes(pc.connectionState)) {
          clearTimeout(timer);
          pc.removeEventListener("connectionstatechange", onChange);
          reject(new Error("음성 서버에 연결하지 못했어요."));
        }
      });
    });
  }, [handleEvent]);

  /**
   * 버튼 누름 — 처음이면 접속부터 하고, 이어서 마이크를 켠다.
   * (별도 '연결' 버튼 없이 마이크 하나로 쓰기 위해서다.)
   */
  const pressStart = useCallback(async () => {
    if (talkingRef.current) return;
    pressedRef.current = true;
    setLastError(null);
    // 앞 발화의 말끝을 기다리는 중이었다면 여기서 끊고 먼저 확정한다.
    flushNow();
    // 끼어들기(barge-in) — LiBi 가 말하는 도중에 버튼을 누르면 답을 멈춘다.
    // 안 멈추면 다음 응답이 'conversation_already_has_active_response' 로 밀린다.
    const liveDc = dcRef.current;
    if (responseActiveRef.current && liveDc?.readyState === "open") {
      pendingResponseRef.current = false;
      liveDc.send(JSON.stringify({ type: "response.cancel" }));
    }
    if (status !== "live" || !pcRef.current) {
      setStatus("connecting");
      try {
        await connect();
      } catch (e) {
        setLastError(e instanceof Error ? e.message : String(e));
        cleanup();
        setStatus("error");
        return;
      }
    }
    // 접속을 기다리는 동안 손을 뗐다면 마이크를 켜지 않는다(켜면 그대로 남는다).
    if (!pressedRef.current) return;
    const mic = micRef.current;
    const dc = dcRef.current;
    if (!mic) return;
    // 이전 발화 잔여 버퍼를 비우고 시작한다.
    if (dc && dc.readyState === "open") {
      dc.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
    }
    mic.getAudioTracks().forEach((t) => { t.enabled = true; });
    talkingRef.current = true;
    pressStartedAtRef.current = Date.now();
    setTalking(true);
  }, [status, connect, cleanup, flushNow]);

  /**
   * 버튼 뗌 — 곧바로 확정하지 않는다. 마이크를 잠깐 더 열어 말끝을 받고(TAIL_MIC_MS),
   * 미디어가 서버에 닿을 시간을 준 뒤(COMMIT_DELAY_MS) commit + 응답 요청을 보낸다.
   */
  const pressStop = useCallback(() => {
    pressedRef.current = false;
    // 아직 듣기 시작 전(접속 중)이었다면 커밋할 발화 자체가 없다.
    if (!talkingRef.current) return;
    talkingRef.current = false;
    setTalking(false);
    const heldMs = Date.now() - pressStartedAtRef.current;

    const flush = () => {
      micRef.current?.getAudioTracks().forEach((t) => { t.enabled = false; });
      const dc = dcRef.current;
      if (!dc || dc.readyState !== "open") return;
      if (heldMs < MIN_PRESS_MS) {
        // 스치듯 누른 것 — 말한 게 없으니 버리고 응답도 요청하지 않는다.
        dc.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
        return;
      }
      dc.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      requestModelResponse();
    };

    pendingFlushRef.current = flush;
    flushTimerRef.current = setTimeout(() => {
      // 마이크는 여기서 끈다(말끝 확보). 커밋은 조금 더 뒤.
      micRef.current?.getAudioTracks().forEach((t) => { t.enabled = false; });
      flushTimerRef.current = setTimeout(() => {
        flushTimerRef.current = null;
        pendingFlushRef.current = null;
        flush();
      }, COMMIT_DELAY_MS);
    }, TAIL_MIC_MS);
  }, [requestModelResponse]);

  useEffect(() => cleanup, [cleanup]);

  return {
    status,
    live: status === "live",
    talking,
    lastError,
    pressStart,
    pressStop,
    stop,
  };
}
