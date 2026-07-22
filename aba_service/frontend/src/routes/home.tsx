import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { LANGS, useI18n } from "@/lib/i18n";
import { useSpeechRecognition, useSpeechSupported } from "@/lib/use-speech";
import { useEffect, useState } from "react";
import {
  Mic,
  BookMarked,
  Map,
  Search,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { BOOKS } from "@/lib/mock-data";
import { Link } from "@tanstack/react-router";

export const Route = createFileRoute("/home")({
  head: () => ({ meta: [{ title: "LiBi — 홈" }] }),
  component: Home,
});

function Home() {
  const { lang, tr } = useI18n();
  const [query, setQuery] = useState("");
  const supported = useSpeechSupported();
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, error, start, stop } =
    useSpeechRecognition(speechLang);
  const navigate = useNavigate();

  useEffect(() => {
    if (!listening && transcript.trim()) {
      const q = transcript.trim();
      const id = setTimeout(
        () => navigate({ to: "/search", search: { q } }),
        400,
      );
      return () => clearTimeout(id);
    }
  }, [listening, transcript, navigate]);

  const newest = BOOKS.slice(0, 3);

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-4">
        {/* Voice hero */}
        <section className="flex flex-col items-center py-8">
          <p className="mb-6 text-center text-sm font-medium text-muted-foreground">
            {listening ? tr("listening") : tr("tapToTalk")}
          </p>
          <button
            onClick={() => (listening ? stop() : start())}
            disabled={!supported}
            aria-label={tr("tapToTalk")}
            className={`relative flex size-40 items-center justify-center rounded-full text-primary-foreground shadow-float transition-transform active:scale-95 disabled:opacity-50 ${
              listening ? "bg-accent voice-pulse" : "bg-primary"
            }`}
          >
            {listening ? (
              <span className="flex h-8 items-end">
                {[0, 1, 2, 3, 4].map((i) => (
                  <span
                    key={i}
                    className="listening-bar"
                    style={{ animationDelay: `${i * 0.12}s` }}
                  />
                ))}
              </span>
            ) : (
              <Mic className="size-16" />
            )}
          </button>
          {transcript && (
            <p className="mt-6 max-w-xs rounded-2xl bg-card px-4 py-2 text-center text-sm text-foreground shadow-card">
              "{transcript}"
            </p>
          )}
          {error === "unsupported" && (
            <p className="mt-4 text-xs text-destructive">
              {tr("noSpeechSupport")}
            </p>
          )}
          {error === "error" && (
            <p className="mt-4 text-xs text-destructive">{tr("micDenied")}</p>
          )}
        </section>

        {/* 도서 검색 — 음성이 어려운 상황을 위한 텍스트 입구 */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            if (q) navigate({ to: "/search", search: { q } });
          }}
          className="relative mt-2"
        >
          <Search className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tr("searchPh")}
            aria-label={tr("navSearch")}
            className="h-14 w-full rounded-2xl border border-border bg-card pl-12 pr-4 text-sm text-foreground shadow-card outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-primary"
          />
        </form>

        {/* Quick menu — 요청은 검색과 분리된 화면(`/request`)으로 간다 */}
        <section className="mt-3 grid grid-cols-3 gap-3">
          <QuickCard
            to="/request"
            icon={BookMarked}
            label="도서 요청"
            tone="primary"
          />
          <QuickCard
            to="/recommend"
            icon={TrendingUp}
            label={tr("bestseller")}
            tone="muted"
          />
          <QuickCard
            to="/map"
            icon={Map}
            label={tr("storeMap")}
            tone="accent"
          />
        </section>

        {/* New arrivals */}
        <section className="mt-8">
          <div className="mb-3 flex items-end justify-between">
            <h2 className="text-base font-bold text-foreground">
              <Sparkles className="-mt-1 mr-1 inline size-4 text-accent" />
              {tr("bestseller")}
            </h2>
            <Link to="/recommend" className="text-xs font-medium text-primary">
              더보기 →
            </Link>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-2 -mx-5 px-5 snap-x">
            {newest.map((b) => (
              <Link
                key={b.id}
                to="/search"
                search={{ q: b.title[lang] }}
                className="w-32 shrink-0 snap-start"
              >
                <div
                  className={`flex h-44 items-center justify-center rounded-xl bg-gradient-to-br ${b.color} text-5xl shadow-card`}
                >
                  {b.cover}
                </div>
                <div className="mt-2 line-clamp-2 text-sm font-semibold text-foreground">
                  {b.title[lang]}
                </div>
                <div className="text-xs text-muted-foreground">{b.author}</div>
              </Link>
            ))}
          </div>
        </section>

        <section className="mt-8 rounded-2xl border border-border bg-card p-4 shadow-card">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <BookMarked className="size-5" />
            </div>
            <div className="flex-1">
              <div className="text-sm font-bold text-foreground">
                {tr("navChat")}
              </div>
              <div className="text-xs text-muted-foreground">
                {tr("chatPh")}
              </div>
            </div>
            <Link
              to="/chat"
              className="rounded-full bg-primary px-4 py-2 text-xs font-bold text-primary-foreground"
            >
              열기
            </Link>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function QuickCard({
  to,
  icon: Icon,
  label,
  tone,
}: {
  to: "/recommend" | "/map" | "/request";
  icon: typeof Mic;
  label: string;
  tone: "primary" | "accent" | "muted";
}) {
  const tones = {
    primary: "bg-primary text-primary-foreground",
    accent: "bg-accent text-accent-foreground",
    muted: "bg-secondary text-secondary-foreground",
  };
  return (
    <Link
      to={to}
      className="flex aspect-square flex-col items-center justify-center gap-2 rounded-2xl border border-border bg-card p-3 text-center shadow-card transition-transform active:scale-95"
    >
      <div
        className={`flex size-12 items-center justify-center rounded-xl ${tones[tone]}`}
      >
        <Icon className="size-6" />
      </div>
      <div className="text-xs font-semibold leading-tight text-foreground">
        {label}
      </div>
    </Link>
  );
}
