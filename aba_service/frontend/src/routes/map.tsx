import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { ZONES } from "@/lib/mock-data";
import { useState } from "react";

export const Route = createFileRoute("/map")({
  head: () => ({ meta: [{ title: "Labi Bot — 공간 안내" }] }),
  component: MapPage,
});

function MapPage() {
  const [active, setActive] = useState<string>("C");
  const zone = ZONES.find((z) => z.id === active);

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <h1 className="text-xl font-bold text-foreground">서점 내부 지도</h1>
        <p className="mt-1 text-xs text-muted-foreground">코너를 탭하면 상세 안내가 나와요</p>

        <div className="mt-4 rounded-2xl border border-border bg-card p-3 shadow-card">
          <div className="relative aspect-[4/3] overflow-hidden rounded-xl bg-paper ring-1 ring-border">
            {/* grid lines */}
            <div className="absolute inset-0 opacity-20 [background-image:linear-gradient(to_right,oklch(0.27_0.12_273)_1px,transparent_1px),linear-gradient(to_bottom,oklch(0.27_0.12_273)_1px,transparent_1px)] [background-size:20px_20px]" />
            {ZONES.map((z) => {
              const isActive = z.id === active;
              return (
                <button
                  key={z.id}
                  onClick={() => setActive(z.id)}
                  className={`absolute flex flex-col items-center justify-center rounded-lg text-xs font-bold transition-all ${z.color} ${
                    isActive ? "scale-105 ring-4 ring-primary shadow-lg z-10" : "ring-1 ring-border/60"
                  }`}
                  style={{
                    left: `${z.x}%`,
                    top: `${z.y}%`,
                    width: `${z.w}%`,
                    height: `${z.h}%`,
                  }}
                >
                  <span className="font-mono text-[10px] text-foreground/60">{z.id}</span>
                  <span className="text-foreground">{z.label}</span>
                </button>
              );
            })}
            <div className="absolute bottom-2 left-2 rounded-full bg-primary px-3 py-1 text-[10px] font-bold text-primary-foreground shadow">
              📍 현위치 (입구)
            </div>
          </div>
        </div>

        {zone && (
          <div className="mt-5 rounded-2xl border border-border bg-card p-5 shadow-card">
            <div className="flex items-center gap-3">
              <div className="flex size-14 items-center justify-center rounded-2xl bg-primary text-2xl text-primary-foreground">
                {zone.id}
              </div>
              <div>
                <h2 className="text-lg font-bold text-foreground">{zone.label}</h2>
                <p className="text-xs text-muted-foreground">코너 {zone.id} · 1층</p>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <Info label="거리" value="약 25m" />
              <Info label="도보 시간" value="약 30초" />
              <Info label="가까운 시설" value="에스컬레이터" />
              <Info label="혼잡도" value="여유" tone="emerald" />
            </div>
            <button className="mt-4 h-12 w-full rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow active:scale-[0.98]">
              🧭 길안내 시작
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function Info({ label, value, tone }: { label: string; value: string; tone?: "emerald" }) {
  return (
    <div className="rounded-xl bg-muted px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-0.5 font-bold ${tone === "emerald" ? "text-emerald-700" : "text-foreground"}`}>
        {value}
      </div>
    </div>
  );
}
