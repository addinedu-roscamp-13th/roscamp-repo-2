import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { Scanner, type IDetectedBarcode } from "@yudiel/react-qr-scanner";
import { ArrowLeft, Camera, Copy, Check, ScanLine, RotateCcw } from "lucide-react";
import { useState } from "react";

export const Route = createFileRoute("/scan")({
  head: () => ({ meta: [{ title: "Labi Bot — 바코드/QR 인식" }] }),
  component: ScanPage,
});

type ScanResult = {
  value: string;
  format: string;
  at: string;
};

function ScanPage() {
  const [scanning, setScanning] = useState(true);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function handleScan(codes: IDetectedBarcode[]) {
    if (!codes.length) return;
    const code = codes[0];
    setResult({
      value: code.rawValue,
      format: code.format ?? "unknown",
      at: new Date().toLocaleTimeString("ko-KR"),
    });
    setScanning(false);
    setError(null);
  }

  // Only surface fatal camera errors; the scanner also emits transient
  // (non-fatal) errors during init/decoding which we ignore.
  function handleError(e: unknown) {
    const fatal = new Set([
      "NotAllowedError",
      "NotFoundError",
      "NotReadableError",
      "OverconstrainedError",
      "SecurityError",
    ]);
    if (e instanceof Error && fatal.has(e.name)) {
      setError(
        e.name === "NotAllowedError"
          ? "카메라 권한이 거부됐어요. 브라우저 설정에서 권한을 허용해주세요."
          : "카메라를 사용할 수 없어요. 다른 앱이 카메라를 쓰고 있는지 확인해주세요.",
      );
    } else {
      console.warn("scanner non-fatal error:", e);
    }
  }

  function restart() {
    setResult(null);
    setError(null);
    setCopied(false);
    setScanning(true);
  }

  async function copyValue() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }

  const isUrl = result ? /^https?:\/\//i.test(result.value) : false;

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
          <h1 className="text-xl font-bold text-foreground">바코드 · QR 인식</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          카메라에 QR 코드나 바코드를 비추면 자동으로 인식해요.
        </p>

        {/* Camera viewport */}
        <div className="relative mt-4 overflow-hidden rounded-2xl border border-border bg-black shadow-card">
          {scanning ? (
            <Scanner
              onScan={handleScan}
              onError={handleError}
              formats={[
                "qr_code",
                "ean_13",
                "ean_8",
                "upc_a",
                "upc_e",
                "code_128",
                "code_39",
                "code_93",
                "codabar",
                "itf",
                "pdf417",
                "aztec",
                "data_matrix",
              ]}
              constraints={{ facingMode: "environment" }}
              scanDelay={300}
              components={{ finder: false }}
              styles={{ container: { width: "100%", aspectRatio: "1 / 1" } }}
            />
          ) : (
            <div className="flex aspect-square flex-col items-center justify-center gap-2 bg-card text-muted-foreground">
              <Check className="size-10 text-primary" />
              <span className="text-sm font-semibold">인식 완료</span>
            </div>
          )}

          {/* Scan overlay frame */}
          {scanning && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="relative size-2/3">
                <span className="absolute left-0 top-0 size-6 rounded-tl-lg border-l-4 border-t-4 border-primary" />
                <span className="absolute right-0 top-0 size-6 rounded-tr-lg border-r-4 border-t-4 border-primary" />
                <span className="absolute bottom-0 left-0 size-6 rounded-bl-lg border-b-4 border-l-4 border-primary" />
                <span className="absolute bottom-0 right-0 size-6 rounded-br-lg border-b-4 border-r-4 border-primary" />
                <ScanLine className="absolute inset-x-0 top-1/2 mx-auto size-8 -translate-y-1/2 animate-pulse text-primary/70" />
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
            {error}
          </div>
        )}

        {/* Result card */}
        {result && (
          <div className="mt-4 rounded-2xl border-2 border-primary bg-primary-soft p-4">
            <div className="flex items-center justify-between">
              <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold uppercase text-primary-foreground">
                {result.format}
              </span>
              <span className="text-[11px] text-muted-foreground">{result.at}</span>
            </div>
            <p className="mt-2 break-all text-sm font-semibold text-foreground">{result.value}</p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={copyValue}
                className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground transition-colors hover:bg-background"
              >
                {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                {copied ? "복사됨" : "복사"}
              </button>
              {isUrl && (
                <a
                  href={result.value}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-xs font-bold text-primary-foreground"
                >
                  열기
                </a>
              )}
            </div>
          </div>
        )}

        {/* Restart button */}
        {!scanning && (
          <button
            onClick={restart}
            className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <RotateCcw className="size-4" />
            다시 스캔하기
          </button>
        )}

        <p className="mt-4 inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Camera className="size-3.5" />
          QR · EAN · UPC · Code128/39/93 · ITF · PDF417 · DataMatrix 지원
        </p>
      </div>
    </AppShell>
  );
}
