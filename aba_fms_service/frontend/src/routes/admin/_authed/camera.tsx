import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw, Wifi, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { adminApi, type CameraAnalysis } from "@/lib/admin-api";
import { getRobotBase } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/camera")({
  component: CameraPage,
});

const RECONNECT_DELAY = 3000;

type StreamState = "connecting" | "live" | "reconnecting";

function buildStreamUrl() {
  return `${getRobotBase()}/api/robot/camera/stream?fps=15&t=${Date.now()}`;
}

function Clock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    <span className="font-mono text-xs tabular-nums">
      {time.getFullYear()}-{pad(time.getMonth() + 1)}-{pad(time.getDate())}{" "}
      {pad(time.getHours())}:{pad(time.getMinutes())}:{pad(time.getSeconds())}
    </span>
  );
}

function CameraPage() {
  const imgRef = useRef<HTMLImageElement>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [streamUrl, setStreamUrl] = useState<string>(() => buildStreamUrl());
  const [state, setState] = useState<StreamState>("connecting");
  const [analysis, setAnalysis] = useState<CameraAnalysis | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const reconnect = useCallback(() => {
    setState("reconnecting");
    if (retryRef.current) clearTimeout(retryRef.current);
    retryRef.current = setTimeout(() => {
      setStreamUrl(buildStreamUrl());
      setState("connecting");
    }, RECONNECT_DELAY);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadAnalysis() {
      try {
        const data = await adminApi.cameraAnalysis();
        if (!cancelled) {
          setAnalysis(data);
          setAnalysisError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setAnalysisError(error instanceof Error ? error.message : "분석 데이터 없음");
        }
      }
    }

    loadAnalysis();
    const id = setInterval(loadAnalysis, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // 언마운트 시 타이머 정리
  useEffect(() => () => {
    if (retryRef.current) clearTimeout(retryRef.current);
  }, []);

  function handleLoad() {
    setState("live");
  }

  function handleError() {
    reconnect();
  }

  function handleManualReconnect() {
    if (retryRef.current) clearTimeout(retryRef.current);
    setStreamUrl(buildStreamUrl());
    setState("connecting");
  }

  const isLive = state === "live";
  const motionPercent = analysis ? Math.min(100, analysis.motion_score * 100) : 0;
  const edgePercent = analysis ? Math.min(100, analysis.edge_density * 100) : 0;

  return (
    <AdminShell title="카메라">
      <div className="flex flex-col gap-3">
        {/* CCTV 뷰어 */}
        <div className="flex justify-center sm:justify-start">
          <div className="relative w-full max-w-[320px] overflow-hidden rounded-lg bg-black shadow-sm" style={{ aspectRatio: "4/3" }}>
          <img
            ref={imgRef}
            src={streamUrl}
            alt="CCTV 스트림"
            className={cn(
              "absolute inset-0 h-full w-full object-contain transition-opacity duration-300",
              isLive ? "opacity-100" : "opacity-30"
            )}
            style={{ transform: "scaleY(-1)" }}
            onLoad={handleLoad}
            onError={handleError}
          />

          {/* 상태 오버레이 — 연결 중 / 재연결 중 */}
          {!isLive && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              {state === "reconnecting" ? (
                <>
                  <WifiOff className="h-8 w-8 text-red-400 animate-pulse" />
                  <p className="text-sm text-slate-300">재연결 중...</p>
                </>
              ) : (
                <>
                  <Wifi className="h-8 w-8 text-yellow-400 animate-pulse" />
                  <p className="text-sm text-slate-300">스트림 연결 중...</p>
                </>
              )}
            </div>
          )}

          {/* CCTV HUD — 좌상단 */}
          <div className="absolute left-2 top-2 flex items-center gap-1.5">
            {isLive && (
              <span className="flex items-center gap-1 rounded bg-red-600/80 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-white">
                <span className="h-1.5 w-1.5 animate-ping rounded-full bg-white" />
                REC
              </span>
            )}
            <span className="rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-300">
              CAM 01
            </span>
          </div>

          {/* CCTV HUD — 우상단 시계 */}
          <div className="absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-slate-200">
            <Clock />
          </div>

          {/* 해상도 — 우하단 */}
          <div className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-400">
            640 × 480 · LIVE
          </div>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="text-xs font-medium text-slate-500">밝기</div>
            <div className="mt-1 text-2xl font-semibold text-slate-900">
              {analysis ? analysis.brightness.toFixed(1) : "--"}
            </div>
            <div className="mt-1 text-xs text-slate-500">0-255 평균 휘도</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="text-xs font-medium text-slate-500">움직임</div>
            <div className="mt-1 text-2xl font-semibold text-slate-900">
              {analysis ? `${motionPercent.toFixed(1)}%` : "--"}
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-100">
              <div className="h-full bg-emerald-500" style={{ width: `${motionPercent}%` }} />
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="text-xs font-medium text-slate-500">윤곽 밀도</div>
            <div className="mt-1 text-2xl font-semibold text-slate-900">
              {analysis ? `${edgePercent.toFixed(1)}%` : "--"}
            </div>
            <div className="mt-1 text-xs text-slate-500">{analysisError ?? `${analysis?.width ?? 0} x ${analysis?.height ?? 0}`}</div>
          </div>
        </div>

        {/* 컨트롤 바 */}
        <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-4 py-2">
          <div className="flex items-center gap-2 text-sm">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                isLive ? "bg-green-500 animate-pulse" : state === "reconnecting" ? "bg-red-400" : "bg-yellow-400"
              )}
            />
            <span className="text-slate-600">
              {isLive ? "라이브 스트리밍 중" : state === "reconnecting" ? "재연결 대기 중" : "연결 중..."}
            </span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={handleManualReconnect}
            disabled={state === "connecting"}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", state === "connecting" && "animate-spin")} />
            새로고침
          </Button>
        </div>
      </div>
    </AdminShell>
  );
}
