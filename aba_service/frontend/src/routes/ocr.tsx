import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import {
  ArrowLeft,
  Camera,
  Upload,
  ScanText,
  Copy,
  Check,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const Route = createFileRoute("/ocr")({
  head: () => ({ meta: [{ title: "Labi Bot — OCR 텍스트 인식" }] }),
  component: OcrPage,
});

type Phase = "idle" | "preview" | "running" | "done";

function OcrPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [cameraOn, setCameraOn] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [engineInfo, setEngineInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => () => stopCamera(), []);

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setCameraOn(false);
  }

  async function startCamera() {
    setError(null);
    setText("");
    setImageUrl(null);
    setPhase("idle");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setCameraOn(true);
    } catch {
      setError("카메라를 사용할 수 없어요. 권한을 확인하거나 이미지를 업로드해주세요.");
    }
  }

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    stopCamera();
    setPhase("preview");
    canvas.toBlob((blob) => {
      if (!blob) {
        setError("이미지를 캡처하지 못했어요. 다시 시도해주세요.");
        setPhase("idle");
        return;
      }
      setImageUrl(URL.createObjectURL(blob));
      void recognize(blob);
    }, "image/png");
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    stopCamera();
    setError(null);
    setImageUrl(URL.createObjectURL(file));
    setPhase("preview");
    void recognize(file);
    e.target.value = "";
  }

  async function recognize(blob: Blob) {
    setPhase("running");
    setText("");
    setEngineInfo(null);
    try {
      const form = new FormData();
      form.append("image", blob, "capture.png");
      const res = await fetch("/api/ocr", { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((d) => d?.detail as string | undefined)
          .catch(() => undefined);
        throw new Error(detail ?? `HTTP ${res.status}`);
      }
      const data = (await res.json()) as {
        text: string;
        engine: string;
        gpu: boolean;
      };
      setText(data.text.trim());
      setEngineInfo(`${data.engine}${data.gpu ? " · GPU" : " · CPU"}`);
      setPhase("done");
    } catch (e) {
      setError(
        e instanceof Error
          ? `텍스트 인식에 실패했어요: ${e.message}`
          : "텍스트 인식에 실패했어요. 백엔드 서버가 실행 중인지 확인해주세요.",
      );
      setPhase("idle");
    }
  }

  function reset() {
    stopCamera();
    setImageUrl(null);
    setText("");
    setEngineInfo(null);
    setError(null);
    setCopied(false);
    setPhase("idle");
  }

  async function copyText() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <AppShell showStore={false}>
      <div className="px-5 pb-8 pt-3">
        <div className="flex items-center gap-2">
          <Link
            to="/settings"
            className="inline-flex size-8 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground transition-colors hover:text-foreground"
            aria-label="뒤로"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <h1 className="text-xl font-bold text-foreground">OCR 텍스트 인식</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          카메라로 촬영하거나 이미지를 올리면 한국어·영어 텍스트를 추출해요.
        </p>

        {/* Viewport */}
        <div className="relative mt-4 overflow-hidden rounded-2xl border border-border bg-black shadow-card">
          <video
            ref={videoRef}
            playsInline
            muted
            className={`aspect-[3/4] w-full object-cover ${cameraOn ? "block" : "hidden"}`}
          />
          {!cameraOn && imageUrl && (
            <img src={imageUrl} alt="인식 대상" className="aspect-[3/4] w-full object-contain bg-card" />
          )}
          {!cameraOn && !imageUrl && (
            <div className="flex aspect-[3/4] flex-col items-center justify-center gap-2 bg-card text-muted-foreground">
              <ScanText className="size-10" />
              <span className="text-sm">카메라를 켜거나 이미지를 업로드하세요</span>
            </div>
          )}

          {phase === "running" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/55 text-white">
              <Loader2 className="size-8 animate-spin" />
              <span className="text-sm font-semibold">EasyOCR로 인식 중…</span>
            </div>
          )}
        </div>
        <canvas ref={canvasRef} className="hidden" />

        {error && (
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
            {error}
          </div>
        )}

        {/* Controls */}
        <div className="mt-4 grid grid-cols-2 gap-2">
          {cameraOn ? (
            <button
              onClick={capture}
              className="col-span-2 inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-primary-foreground transition-colors hover:bg-primary/90"
            >
              <Camera className="size-4" />
              촬영하고 인식
            </button>
          ) : (
            <>
              <button
                onClick={startCamera}
                disabled={phase === "running"}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
              >
                <Camera className="size-4" />
                카메라
              </button>
              <button
                onClick={() => fileRef.current?.click()}
                disabled={phase === "running"}
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-3 text-sm font-semibold text-foreground transition-colors hover:bg-background disabled:opacity-40"
              >
                <Upload className="size-4" />
                이미지 업로드
              </button>
            </>
          )}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="environment"
          onChange={onFile}
          className="hidden"
        />

        {/* Result */}
        {phase === "done" && (
          <div className="mt-4 rounded-2xl border-2 border-primary bg-primary-soft p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="inline-flex items-center gap-1.5 text-sm font-bold text-primary">
                <ScanText className="size-4" />
                인식된 텍스트
                {engineInfo && (
                  <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold uppercase text-primary-foreground">
                    {engineInfo}
                  </span>
                )}
              </span>
              <button
                onClick={copyText}
                disabled={!text}
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-card px-2 py-1 text-xs font-semibold text-foreground transition-colors hover:bg-background disabled:opacity-40"
              >
                {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                {copied ? "복사됨" : "복사"}
              </button>
            </div>
            {text ? (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-card p-3 text-sm text-foreground">
                {text}
              </pre>
            ) : (
              <p className="rounded-xl bg-card p-3 text-sm text-muted-foreground">
                인식된 텍스트가 없어요. 더 선명한 이미지로 다시 시도해보세요.
              </p>
            )}
            <button
              onClick={reset}
              className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-semibold text-foreground transition-colors hover:bg-background"
            >
              <RotateCcw className="size-4" />
              다시 인식하기
            </button>
          </div>
        )}

        <p className="mt-4 inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <ScanText className="size-3.5" />
          EasyOCR (딥러닝) · 한국어 + 영어 · 백엔드 GPU 추론
        </p>
      </div>
    </AppShell>
  );
}
