import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import {
  BookDetailSheet,
  reserveFromSheet,
} from "@/components/BookDetailSheet";
import { BookRow, BookRowSkeleton } from "@/components/BookRow";
import { fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { LANGS, useI18n } from "@/lib/i18n";
import { useDebounced } from "@/lib/use-debounced";
import { useSpeechRecognition } from "@/lib/use-speech";
import { Mic, Search as SearchIcon, X } from "lucide-react";
import { useEffect, useState } from "react";
import { z } from "zod";

const searchSchema = z.object({ q: z.string().optional() });

export const Route = createFileRoute("/search")({
  validateSearch: searchSchema,
  head: () => ({ meta: [{ title: "LiBi — 도서 검색" }] }),
  component: SearchPage,
});

const CATS = [
  "all",
  "literature",
  "art",
  "science",
  "humanities",
  "kids",
] as const;
type Cat = (typeof CATS)[number];

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
  const [cat, setCat] = useState<Cat>("all");
  const [results, setResults] = useState<CatalogBook[]>([]);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<CatalogBook | null>(null);
  const debounced = useDebounced(query, 250);
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, start, stop } =
    useSpeechRecognition(speechLang);

  useEffect(() => setQuery(q ?? ""), [q]);
  useEffect(() => {
    if (transcript) setQuery(transcript);
  }, [transcript]);

  // 도서는 DB(cb_books)에서 온다 — 서가 위치와 재고를 그대로 보여준다.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchCatalog({
      q: debounced.trim() || null,
      category: cat === "all" ? null : cat,
      limit: 100,
    })
      .then((rows) => {
        if (!cancelled) setResults(rows);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced, cat]);

  const catLabels: Record<Cat, string> = {
    all: tr("catAll"),
    literature: tr("catLiterature"),
    art: tr("catArt"),
    science: tr("catScience"),
    humanities: tr("catHumanities"),
    kids: tr("catKids"),
  };

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

        {/* 요청은 이 화면의 일이 아니다 — 별도 화면으로 보낸다. 결과를 다 스크롤해야
            보이던 카드였는데, 검색바 바로 아래로 올렸다. */}
        <Link
          to="/request"
          className="mt-4 block rounded-2xl border-2 border-dashed border-primary/40 bg-primary-soft/40 p-4 text-center"
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

        <div className="mt-4 flex gap-2 overflow-x-auto pb-1 -mx-5 px-5">
          {CATS.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              className={`shrink-0 rounded-full px-4 py-2 text-xs font-bold transition-colors ${
                cat === c
                  ? "bg-primary text-primary-foreground"
                  : "bg-card text-muted-foreground ring-1 ring-border"
              }`}
            >
              {catLabels[c]}
            </button>
          ))}
        </div>

        {/* 10권까지는 그대로 보이고, 넘치면 이 상자 안에서만 스크롤된다.
            페이지 전체가 결과로 길어지면 아래 안내가 화면 밖으로 밀려난다. */}
        <div className="mt-4 max-h-[62vh] space-y-2 overflow-y-auto pr-1">
          {loading ? (
            <>
              <BookRowSkeleton />
              <BookRowSkeleton />
              <BookRowSkeleton />
            </>
          ) : results.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              검색 결과가 없습니다.
            </p>
          ) : (
            results.map((b) => (
              <BookRow key={b.id} book={b} onSelect={setPicked} />
            ))
          )}
        </div>
      </div>

      <BookDetailSheet
        book={picked}
        onOpenChange={(open) => !open && setPicked(null)}
        onReserve={(b) => void reserveFromSheet(b)}
      />
    </AppShell>
  );
}
