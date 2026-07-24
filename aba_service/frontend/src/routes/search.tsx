import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { LANGS, useI18n } from "@/lib/i18n";
import { useSpeechRecognition } from "@/lib/use-speech";
import { Mic, Search as SearchIcon, MapPin, X } from "lucide-react";
import { useEffect, useState } from "react";
import { z } from "zod";
import { Link } from "@tanstack/react-router";

const searchSchema = z.object({ q: z.string().optional() });

export const Route = createFileRoute("/search")({
  validateSearch: searchSchema,
  head: () => ({ meta: [{ title: "LiBi — 도서 검색" }] }),
  component: SearchPage,
});

/**
 * 도서 검색 — **찾기만 한다.**
 *
 * 예전에는 이 화면이 검색·로그인 유도·요청·자리 선택·예약을 다 했다. 목적이 흐려서
 * 요청은 「도서 요청」 화면(`/request`)으로 전부 옮겼다. 여기 남는 것은
 * "무슨 책이 어디 있고 지금 빌릴 수 있는가" 뿐이다.
 */
function SearchPage() {
  const { lang, tr } = useI18n();
  const { q } = Route.useSearch();
  const navigate = useNavigate();
  const [query, setQuery] = useState(q ?? "");
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, start, stop } =
    useSpeechRecognition(speechLang);

  useEffect(() => setQuery(q ?? ""), [q]);
  useEffect(() => {
    if (transcript) setQuery(transcript);
  }, [transcript]);

  // 도서는 DB(cb_books)에서 온다 — 서가 위치와 재고를 그대로 보여준다.
  const [results, setResults] = useState<CatalogBook[]>([]);

  useEffect(() => {
    let cancelled = false;
    void fetchCatalog({ q: query.trim() || null }).then((rows) => {
      if (!cancelled) setResults(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [query]);

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <div className="flex items-center gap-2 rounded-2xl border border-border bg-card p-2 shadow-card">
          <SearchIcon className="ml-2 size-5 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onBlur={() => navigate({ to: "/search", search: { q: query } })}
            placeholder={tr("searchPh")}
            className="flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="text-muted-foreground"
            >
              <X className="size-4" />
            </button>
          )}
          <button
            onClick={() => (listening ? stop() : start())}
            className={`flex size-10 items-center justify-center rounded-xl transition-colors ${
              listening
                ? "bg-accent text-accent-foreground"
                : "bg-primary text-primary-foreground"
            }`}
            aria-label="voice search"
          >
            <Mic className="size-5" />
          </button>
        </div>

        {listening && (
          <p className="mt-3 text-center text-xs font-medium text-primary">
            🎙️ {tr("listening")}
          </p>
        )}

        <div className="mt-5 space-y-3">
          {results.length === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              검색 결과가 없습니다.
            </p>
          )}
          {results.map((b) => (
            <article
              key={b.id}
              className="overflow-hidden rounded-2xl border border-border bg-card shadow-card"
            >
              <div className="flex gap-3 p-3">
                <div
                  className={`flex size-20 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${b.color} text-4xl`}
                >
                  {b.cover}
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="line-clamp-1 text-sm font-bold text-foreground">
                    {b.title[lang]}
                  </h3>
                  <p className="text-xs text-muted-foreground">{b.author}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        b.inStock
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-stone-200 text-stone-600"
                      }`}
                    >
                      {b.inStock ? tr("inStock") : tr("soldOut")}
                    </span>
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                      <MapPin className="size-3" />
                      {b.zone} · {b.shelf}
                    </span>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>

        {/* 요청은 이 화면의 일이 아니다 — 별도 화면으로 보낸다 */}
        <Link
          to="/request"
          className="mt-6 block rounded-2xl border-2 border-dashed border-primary/40 bg-primary-soft/40 p-4 text-center"
        >
          <p className="text-sm font-semibold text-primary">
            책을 받아보고 싶으세요? 도서 요청하기 →
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            자리로 받기 · 대여 신청 · 예약을 한 곳에서
          </p>
        </Link>

        <Link
          to="/chat"
          className="mt-3 block rounded-2xl border-2 border-dashed border-primary/30 bg-primary-soft/40 p-4 text-center"
        >
          <p className="text-sm font-semibold text-primary">
            못 찾으셨나요? LiBi에게 물어보세요
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{tr("chatPh")}</p>
        </Link>
      </div>
    </AppShell>
  );
}

function MiniMap({ zoneId }: { zoneId: string }) {
  return (
    <div className="border-t border-border bg-muted/40 p-4">
      <div className="relative h-32 rounded-xl bg-paper ring-1 ring-border">
        {/* simplified zones */}
        {["A", "B", "C", "D", "E", "F"].map((id, i) => {
          const active = id === zoneId;
          return (
            <div
              key={id}
              className={`absolute flex items-center justify-center rounded text-[10px] font-bold ${
                active
                  ? "bg-accent text-accent-foreground ring-2 ring-primary"
                  : "bg-card text-muted-foreground"
              }`}
              style={{
                left: `${(i % 3) * 33 + 2}%`,
                top: `${Math.floor(i / 3) * 50 + 5}%`,
                width: "29%",
                height: "42%",
              }}
            >
              {id}
              {active && (
                <span className="absolute -top-2 right-1 size-3 animate-ping rounded-full bg-accent" />
              )}
            </div>
          );
        })}
        <div className="absolute bottom-1 left-1 rounded bg-primary px-1.5 py-0.5 text-[9px] font-bold text-primary-foreground">
          📍 현위치
        </div>
      </div>
      <p className="mt-2 text-center text-xs font-medium text-foreground">
        현위치에서 도보 약 30초 · 코너 <b className="text-primary">{zoneId}</b>
      </p>
    </div>
  );
}
