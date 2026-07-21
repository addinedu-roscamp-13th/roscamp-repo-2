import { useCallback, useEffect, useRef, useState } from "react";

import { adminApi, type VoiceExecuteResult } from "./admin-api";

// OpenAI Realtime(WebRTC) 음성 대화 훅.
//  - 마이크 → OpenAI(STT) → 로봇 페르소나 응답(TTS) → 스피커
//  - 모델이 control_robot/stop_robot function 을 호출하면 백엔드 /api/voice/execute 로 실행하고
//    그 결과를 function_call_output 으로 되돌려 모델이 음성으로 확인하게 한다.
// ⚠️ 마이크는 보안 컨텍스트에서만 잡힌다: http://localhost 는 OK, LAN IP·도메인은 HTTPS 필요.

// GA(정식) SDP 교환 엔드포인트. 모델·세션 설정은 ephemeral 발급 시 서버가 이미 지정했다.
const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

export type VoiceStatus = "idle" | "connecting" | "live" | "error";

export interface VoiceTranscript {
  id: string;
  role: "user" | "assistant";
  text: string;
}

export interface UseRealtimeVoice {
  status: VoiceStatus;
  live: boolean;
  talking: boolean;
  allowDriving: boolean;
  lastError: string | null;
  transcripts: VoiceTranscript[];
  start: () => Promise<void>;
  stop: () => void;
  pttStart: () => void; // 버튼 누름 — 마이크 ON(듣기 시작)
  pttStop: () => void;  // 버튼 뗌 — 마이크 OFF + 발화 확정(commit)
  setAllowDriving: (v: boolean) => void;
}

export function useRealtimeVoice(
  onExecute?: (result: VoiceExecuteResult) => void,
): UseRealtimeVoice {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [talking, setTalking] = useState(false);
  const [allowDriving, setAllowDrivingState] = useState(true);
  const [lastError, setLastError] = useState<string | null>(null);
  const [transcripts, setTranscripts] = useState<VoiceTranscript[]>([]);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // 최신 값을 이벤트 핸들러에서 읽기 위한 ref (closure 회피)
  const allowDrivingRef = useRef(allowDriving);
  const onExecuteRef = useRef(onExecute);
  allowDrivingRef.current = allowDriving;
  onExecuteRef.current = onExecute;
  // 응답(response) 동시성 관리 — 진행 중인 response 가 있는데 response.create 를
  // 또 보내면 'conversation_already_has_active_response' 오류가 난다. 그래서
  // function 결과 응답은 현재 response 가 끝난(response.done) 뒤에만 요청한다.
  const responseActiveRef = useRef(false);
  const pendingResponseRef = useRef(false);

  const requestModelResponse = useCallback(() => {
    const dc = dcRef.current;
    if (!dc || dc.readyState !== "open") return;
    if (responseActiveRef.current) {
      // 아직 응답이 진행 중 → done 이벤트에서 보내도록 예약
      pendingResponseRef.current = true;
      return;
    }
    responseActiveRef.current = true;
    dc.send(JSON.stringify({ type: "response.create" }));
  }, []);

  const pushTranscript = useCallback((role: "user" | "assistant", text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setTranscripts((prev) => [...prev, { id: `${Date.now()}-${Math.random()}`, role, text: trimmed }]);
  }, []);

  const cleanup = useCallback(() => {
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
    responseActiveRef.current = false;
    pendingResponseRef.current = false;
  }, []);

  const stop = useCallback(() => {
    cleanup();
    setStatus("idle");
    setTalking(false);
  }, [cleanup]);

  // 모델의 function call 을 백엔드로 실행하고 결과를 모델에 되돌린다.
  const handleFunctionCall = useCallback(async (name: string, callId: string, argsJson: string) => {
    let result: VoiceExecuteResult;

    // 1) 인자 파싱 — 말이 겹쳐 server VAD 가 생성 중인 함수호출을 끊으면 argsJson 이
    //    잘려('Unterminated string in JSON') 파싱이 실패할 수 있다. 이땐 실행하지 않고
    //    모델이 사용자에게 다시 묻게 한다. (로봇 상태에는 영향 없음)
    let args: Record<string, unknown> | null = null;
    try {
      args = JSON.parse(argsJson || "{}");
    } catch (e) {
      console.error("[voice] function 인자 JSON 파싱 실패:", e, "raw:", argsJson);
      const out: VoiceExecuteResult = { success: false, spoken: "명령이 중간에 끊겼어요. 한 번만 더 말씀해 주세요." };
      onExecuteRef.current?.(out);
      const dcx = dcRef.current;
      if (dcx && dcx.readyState === "open") {
        dcx.send(JSON.stringify({ type: "conversation.item.create", item: { type: "function_call_output", call_id: callId, output: JSON.stringify(out) } }));
        requestModelResponse();
      }
      return;
    }

    // 2) 백엔드 실행 — 네트워크/응답 잘림 등은 로봇이 이미 동작했을 수도 있으므로
    //    'unknown' 으로 처리하고 사용자에게 상태 확인을 안내한다(오보 방지).
    try {
      const a = args as Record<string, any>;
      if (name === "stop_robot") {
        result = await adminApi.voiceExecute({ robot_number: Number(a.robot_number), steps: [{ action: "stop" }], confirm: true });
      } else if (name === "control_robot") {
        result = await adminApi.voiceExecute({
          robot_number: Number(a.robot_number),
          steps: Array.isArray(a.steps) ? a.steps : [],
          confirm: allowDrivingRef.current,
        });
      } else if (name === "run_robot_tasks") {
        const commands: string[] = Array.isArray(a.commands)
          ? a.commands.map((c: unknown) => String(c))
          : (a.command_text ? [String(a.command_text)] : []);
        result = await adminApi.voiceTasks(commands, allowDrivingRef.current);
      } else if (name === "run_robot_command") {
        result = await adminApi.voiceCommand(String(a.command_text ?? ""), allowDrivingRef.current);
      } else {
        result = { success: false, spoken: `알 수 없는 명령입니다: ${name}` };
      }
    } catch (e) {
      console.error("[voice] 백엔드 실행 실패:", e);
      // 요청은 나갔으나 응답을 못 받은 상태 — 로봇은 동작 중일 수 있다.
      result = { success: false, spoken: "명령은 보냈는데 응답을 못 받았어요. 로봇 상태를 확인해 주세요." };
      setLastError(e instanceof Error ? e.message : String(e));
    }
    onExecuteRef.current?.(result);
    const dc = dcRef.current;
    if (dc && dc.readyState === "open") {
      // 결과 아이템 추가는 응답 진행 여부와 무관하게 안전.
      dc.send(JSON.stringify({
        type: "conversation.item.create",
        item: { type: "function_call_output", call_id: callId, output: JSON.stringify(result) },
      }));
      // 확인 음성(response.create)은 현재 응답이 끝난 뒤에만.
      requestModelResponse();
    }
  }, [requestModelResponse]);

  const handleEvent = useCallback((evt: Record<string, unknown>) => {
    const type = evt.type as string;
    switch (type) {
      case "response.created":
        responseActiveRef.current = true;
        break;
      case "response.done":
        responseActiveRef.current = false;
        // function 결과 응답이 대기 중이면 지금(응답이 끝난 시점) 요청한다.
        if (pendingResponseRef.current) {
          pendingResponseRef.current = false;
          requestModelResponse();
        }
        break;
      case "response.function_call_arguments.done":
        void handleFunctionCall(evt.name as string, evt.call_id as string, evt.arguments as string);
        break;
      case "conversation.item.input_audio_transcription.completed":
        pushTranscript("user", (evt.transcript as string) ?? "");
        break;
      case "response.audio_transcript.done":
        pushTranscript("assistant", (evt.transcript as string) ?? "");
        break;
      case "error":
        setLastError(JSON.stringify(evt.error ?? evt));
        break;
      default:
        break;
    }
  }, [handleFunctionCall, pushTranscript, requestModelResponse]);

  const start = useCallback(async () => {
    if (status === "connecting" || status === "live") return;
    setLastError(null);
    setStatus("connecting");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("이 환경에서는 마이크를 쓸 수 없습니다. http://localhost 또는 HTTPS 로 접속하세요.");
      }
      const session = await adminApi.voiceSession();

      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      // 원격(로봇 TTS) 오디오 재생용 엘리먼트
      const audioEl = document.createElement("audio");
      audioEl.autoplay = true;
      audioRef.current = audioEl;
      pc.ontrack = (e) => { audioEl.srcObject = e.streams[0]; };

      // 마이크
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      micRef.current = mic;
      mic.getTracks().forEach((track) => pc.addTrack(track, mic));
      // PTT: 연결 직후 마이크는 OFF — '누르고 말하기' 버튼을 누르는 동안만 켠다.
      mic.getAudioTracks().forEach((t) => { t.enabled = false; });

      // 이벤트 데이터 채널
      const dc = pc.createDataChannel("oai-events");
      dcRef.current = dc;
      dc.onmessage = (e) => {
        try { handleEvent(JSON.parse(e.data)); } catch { /* non-JSON */ }
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
    } catch (e) {
      setLastError(e instanceof Error ? e.message : String(e));
      cleanup();
      setStatus("error");
    }
  }, [status, handleEvent, cleanup]);

  // PTT 누름 — 이전 버퍼를 비우고 마이크 ON.
  const pttStart = useCallback(() => {
    const mic = micRef.current;
    const dc = dcRef.current;
    if (!mic || status !== "live") return;
    if (dc && dc.readyState === "open") {
      dc.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
    }
    mic.getAudioTracks().forEach((t) => { t.enabled = true; });
    setTalking(true);
  }, [status]);

  // PTT 뗌 — 마이크 OFF + 발화 확정(commit) 후 응답 요청.
  const pttStop = useCallback(() => {
    const mic = micRef.current;
    const dc = dcRef.current;
    if (mic) mic.getAudioTracks().forEach((t) => { t.enabled = false; });
    if (!talking) return; // 눌린 적 없으면 무시
    setTalking(false);
    if (dc && dc.readyState === "open") {
      dc.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      requestModelResponse();
    }
  }, [talking, requestModelResponse]);

  const setAllowDriving = useCallback((v: boolean) => setAllowDrivingState(v), []);

  useEffect(() => cleanup, [cleanup]);

  return {
    status,
    live: status === "live",
    talking,
    allowDriving,
    lastError,
    transcripts,
    start,
    stop,
    pttStart,
    pttStop,
    setAllowDriving,
  };
}
