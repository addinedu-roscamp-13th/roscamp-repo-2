import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { LANGS, useI18n, type Lang } from "@/lib/i18n";
import { Mic, Keyboard, BookOpen, Check } from "lucide-react";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Labi Bot — 시작하기" },
      { name: "description", content: "음성과 텍스트로 책을 찾아주는 서점 AI 가이드, Labi Bot에 오신 것을 환영합니다." },
    ],
  }),
  component: Onboarding,
});

function Onboarding() {
  const { lang, setLang, tr } = useI18n();
  const [step, setStep] = useState<"lang" | "input">("lang");
  const navigate = useNavigate();

  const goHome = async (askMic: boolean) => {
    if (askMic && typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia) {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
      } catch {
        /* user can still proceed */
      }
    }
    navigate({ to: "/home" });
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col bg-background px-6 py-10">
      <div className="flex flex-1 flex-col">
        <div className="mt-6 flex items-center gap-3">
          <div className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-md">
            <BookOpen className="size-6" />
          </div>
          <div>
            <h1 className="font-serif text-2xl font-bold tracking-tight text-primary">
              Labi <span className="text-accent">Bot</span>
            </h1>
            <p className="text-xs text-muted-foreground">{tr("tagline")}</p>
          </div>
        </div>

        <h2 className="mt-12 text-balance text-2xl font-bold leading-snug text-foreground">
          {tr("welcome")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {step === "lang" ? tr("chooseLang") : "↓"}
        </p>

        {step === "lang" ? (
          <div className="mt-8 grid grid-cols-2 gap-3">
            {LANGS.filter((l) => l.code !== "VI").map((l) => {
              const active = l.code === lang;
              return (
                <button
                  key={l.code}
                  onClick={() => setLang(l.code as Lang)}
                  className={`flex items-center justify-between rounded-2xl border-2 p-4 text-left transition-all ${
                    active
                      ? "border-primary bg-primary-soft shadow-sm"
                      : "border-border bg-card hover:border-primary/40"
                  }`}
                >
                  <div>
                    <div className="font-mono text-xs text-muted-foreground">{l.code}</div>
                    <div className="mt-1 font-semibold text-foreground">{l.native}</div>
                  </div>
                  {active && <Check className="size-5 text-primary" />}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="mt-8 space-y-3">
            <button
              onClick={() => goHome(true)}
              className="flex w-full items-center gap-4 rounded-2xl bg-primary p-5 text-left text-primary-foreground shadow-md transition-transform active:scale-[0.98]"
            >
              <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-primary-foreground/15">
                <Mic className="size-6" />
              </div>
              <div>
                <div className="text-base font-bold">{tr("voiceStart")}</div>
                <div className="mt-0.5 text-xs text-primary-foreground/70">
                  {tr("listening")}
                </div>
              </div>
            </button>

            <button
              onClick={() => goHome(false)}
              className="flex w-full items-center gap-4 rounded-2xl border-2 border-border bg-card p-5 text-left transition-colors active:bg-muted"
            >
              <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent-foreground">
                <Keyboard className="size-6" />
              </div>
              <div>
                <div className="text-base font-bold text-foreground">{tr("textStart")}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">{tr("searchPh")}</div>
              </div>
            </button>
          </div>
        )}
      </div>

      <div className="pb-4 pt-6">
        {step === "lang" ? (
          <button
            onClick={() => setStep("input")}
            className="h-14 w-full rounded-2xl bg-primary text-base font-bold text-primary-foreground shadow-md transition-transform active:scale-[0.98]"
          >
            {tr("startNow")} →
          </button>
        ) : (
          <button
            onClick={() => setStep("lang")}
            className="h-12 w-full text-sm text-muted-foreground"
          >
            ← {tr("chooseLang")}
          </button>
        )}
      </div>
    </div>
  );
}
