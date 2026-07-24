import { useEffect, useRef, useState, useCallback } from "react";

type SpeechRecognitionLike = {
  start: () => void;
  stop: () => void;
  abort: () => void;
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> & { length: number } }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  onend: (() => void) | null;
};

function getRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function useSpeechSupported() {
  const [supported, setSupported] = useState(false);
  useEffect(() => setSupported(!!getRecognitionCtor()), []);
  return supported;
}

// Auto-send after this much silence once the user has started speaking.
const DEFAULT_SILENCE_MS = 1300;

export function useSpeechRecognition(lang: string, silenceMs: number = DEFAULT_SILENCE_MS) {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasSpeechRef = useRef(false);

  const clearSilence = useCallback(() => {
    if (silenceTimer.current) {
      clearTimeout(silenceTimer.current);
      silenceTimer.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    clearSilence();
    recRef.current?.stop();
    setListening(false);
  }, [clearSilence]);

  const start = useCallback(() => {
    setError(null);
    setTranscript("");
    clearSilence();
    hasSpeechRef.current = false;
    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      setError("unsupported");
      return;
    }
    try {
      const rec = new Ctor();
      rec.lang = lang;
      rec.interimResults = true;
      // keep listening so we stream interim results; we stop on silence ourselves
      rec.continuous = true;
      rec.onresult = (e) => {
        let text = "";
        for (let i = 0; i < e.results.length; i++) {
          text += e.results[i][0].transcript;
        }
        setTranscript(text);
        if (text.trim()) hasSpeechRef.current = true;

        // reset the silence countdown on every chunk of speech
        clearSilence();
        if (hasSpeechRef.current) {
          silenceTimer.current = setTimeout(() => {
            // user paused -> stop recognition (onend -> consumer auto-sends)
            recRef.current?.stop();
          }, silenceMs);
        }
      };
      rec.onerror = () => {
        clearSilence();
        setError("error");
      };
      rec.onend = () => {
        clearSilence();
        setListening(false);
      };
      recRef.current = rec;
      rec.start();
      setListening(true);
    } catch {
      setError("error");
    }
  }, [lang, silenceMs, clearSilence]);

  useEffect(
    () => () => {
      clearSilence();
      recRef.current?.abort();
    },
    [clearSilence],
  );

  return { listening, transcript, error, start, stop, setTranscript };
}

export function speak(text: string, lang: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  u.rate = 1;
  u.pitch = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}
