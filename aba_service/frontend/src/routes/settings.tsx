import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { LANGS, useI18n, type Lang } from "@/lib/i18n";
import {
  Bot,
  Languages,
  RefreshCw,
  QrCode,
  X,
  ExternalLink,
  ScanLine,
  ScanText,
  ChevronRight,
} from "lucide-react";
import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

const SHARE_URL = "https://sanora-wretched-lenard.ngrok-free.dev/";

type OllamaModel = {
  name: string;
  size: number;
  details?: { parameter_size?: string; quantization_level?: string };
};

const OLLAMA_URL = import.meta.env.VITE_OLLAMA_URL ?? "/ollama";
const OLLAMA_MODEL_KEY = "labi.ollamaModel";
const DEFAULT_OLLAMA_MODEL = import.meta.env.VITE_OLLAMA_MODEL ?? "qwen3:1.7b";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "Labi Bot — 설정" }] }),
  component: Settings,
});

function Settings() {
  const { lang, setLang, tr } = useI18n();
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [selectedModel, setSelectedModel] = useState(DEFAULT_OLLAMA_MODEL);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [qrOpen, setQrOpen] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem(OLLAMA_MODEL_KEY);
    if (saved) setSelectedModel(saved);
    void loadModels(saved || DEFAULT_OLLAMA_MODEL);
  }, []);

  async function loadModels(currentModel: string) {
    setModelLoading(true);
    setModelError(null);
    try {
      const endpoint = OLLAMA_URL.endsWith("/") ? OLLAMA_URL.slice(0, -1) : OLLAMA_URL;
      const res = await fetch(endpoint + "/api/tags", {
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = (await res.json()) as { models: OllamaModel[] };
      const list = data.models ?? [];
      setModels(list);
      if (list.length > 0 && !list.find((m) => m.name === currentModel)) {
        const first = list[0].name;
        setSelectedModel(first);
        localStorage.setItem(OLLAMA_MODEL_KEY, first);
      }
    } catch (e) {
      setModelError("Ollama 서버에 연결할 수 없어요. 서버가 실행 중인지 확인해주세요.");
    } finally {
      setModelLoading(false);
    }
  }

  function selectModel(name: string) {
    setSelectedModel(name);
    localStorage.setItem(OLLAMA_MODEL_KEY, name);
  }

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <h1 className="text-xl font-bold text-foreground">{tr("settings")}</h1>

        <Section title="AI 모델 선택" icon={Bot}>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {modelLoading ? "모델 목록 로딩 중..." : `${models.length}개 모델 발견`}
            </span>
            <button
              onClick={() => void loadModels(selectedModel)}
              disabled={modelLoading}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-card px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
            >
              <RefreshCw className={`size-3 ${modelLoading ? "animate-spin" : ""}`} />
              새로고침
            </button>
          </div>

          {modelError && (
            <div className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
              {modelError}
            </div>
          )}

          {models.length > 0 ? (
            <div className="space-y-2">
              {models.map((m) => (
                <button
                  key={m.name}
                  onClick={() => selectModel(m.name)}
                  className={`w-full rounded-xl border-2 p-3 text-left transition-colors ${
                    selectedModel === m.name
                      ? "border-primary bg-primary-soft text-primary"
                      : "border-border bg-card text-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold">{m.name}</span>
                    {selectedModel === m.name && (
                      <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold text-primary-foreground">
                        사용 중
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex gap-2 text-[11px] text-muted-foreground">
                    {m.details?.parameter_size && <span>{m.details.parameter_size}</span>}
                    {m.details?.quantization_level && <span>{m.details.quantization_level}</span>}
                    <span>{(m.size / 1e9).toFixed(1)} GB</span>
                  </div>
                </button>
              ))}
            </div>
          ) : !modelLoading && !modelError ? (
            <div className="rounded-xl border border-border bg-card px-3 py-4 text-center text-xs text-muted-foreground">
              설치된 모델이 없습니다.
            </div>
          ) : null}
        </Section>

        <Section title="스캔 / 인식 도구" icon={ScanLine}>
          <Link
            to="/scan"
            className="flex items-center gap-3 rounded-xl border-2 border-border bg-card p-3 text-left transition-colors hover:border-primary"
          >
            <span className="inline-flex size-10 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <ScanLine className="size-5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-foreground">바코드 · QR 카메라</span>
              <span className="block text-[11px] text-muted-foreground">
                카메라로 QR·바코드를 인식해 텍스트로 변환
              </span>
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
          </Link>

          <Link
            to="/ocr"
            className="flex items-center gap-3 rounded-xl border-2 border-border bg-card p-3 text-left transition-colors hover:border-primary"
          >
            <span className="inline-flex size-10 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <ScanText className="size-5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-foreground">OCR 텍스트 인식</span>
              <span className="block text-[11px] text-muted-foreground">
                이미지 속 한국어·영어 문자를 추출해 화면에 출력
              </span>
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
          </Link>
        </Section>

        <Section title={tr("chooseLang")} icon={Languages}>
          <div className="grid grid-cols-2 gap-2">
            {LANGS.map((l) => (
              <button
                key={l.code}
                onClick={() => setLang(l.code as Lang)}
                className={`rounded-xl border-2 p-3 text-left text-sm font-semibold transition-colors ${
                  lang === l.code
                    ? "border-primary bg-primary-soft text-primary"
                    : "border-border bg-card text-foreground"
                }`}
              >
                <div className="font-mono text-[10px] text-muted-foreground">{l.code}</div>
                {l.native}
              </button>
            ))}
          </div>
        </Section>

        <Section title="공유 / QR 코드" icon={QrCode}>
          <button
            onClick={() => setQrOpen(true)}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <QrCode className="size-4" />
            QR 코드 보기
          </button>
          <p className="mt-2 break-all text-center text-[11px] text-muted-foreground">{SHARE_URL}</p>
        </Section>

        <p className="mt-8 text-center text-[11px] text-muted-foreground">
          Labi Bot v0.1 · made with 📚 by Lovable
        </p>
      </div>

      {/* QR code modal */}
      {qrOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6"
          onClick={() => setQrOpen(false)}
        >
          <div
            className="w-full max-w-xs rounded-3xl bg-card p-6 shadow-float"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="font-bold text-foreground">📱 QR 코드</h3>
              <button onClick={() => setQrOpen(false)} aria-label="close">
                <X className="size-5 text-muted-foreground" />
              </button>
            </div>
            <div className="flex justify-center rounded-2xl bg-white p-4">
              <QRCodeSVG value={SHARE_URL} size={200} level="M" marginSize={2} />
            </div>
            <p className="mt-3 break-all text-center text-[11px] text-muted-foreground">{SHARE_URL}</p>
            <a
              href={SHARE_URL}
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-bold text-primary-foreground"
            >
              <ExternalLink className="size-4" />
              페이지 열기
            </a>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: typeof Bot;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6">
      <h2 className="mb-3 inline-flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-muted-foreground">
        <Icon className="size-4 text-primary" />
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}
