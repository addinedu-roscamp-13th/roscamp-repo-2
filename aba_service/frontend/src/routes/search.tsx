import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { LANGS, useI18n } from "@/lib/i18n";
import { getToken, memberApi, TABLES } from "@/lib/member";
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

function SearchPage() {
  const { lang, tr } = useI18n();
  const { q } = Route.useSearch();
  const navigate = useNavigate();
  const [query, setQuery] = useState(q ?? "");
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, start, stop } =
    useSpeechRecognition(speechLang);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => setQuery(q ?? ""), [q]);
  useEffect(() => {
    if (transcript) setQuery(transcript);
  }, [transcript]);

  // 도서는 DB(cb_books)에서 온다 — 요청/예약이 실제 도서 id 와 서가 위치를 필요로 한다.
  const [results, setResults] = useState<CatalogBook[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const loggedIn = getToken() !== null;

  useEffect(() => {
    let cancelled = false;
    void fetchCatalog({ q: query.trim() || null }).then((rows) => {
      if (!cancelled) setResults(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [query]);

  const request = async (
    book: CatalogBook,
    kind: "read" | "borrow",
    table?: string,
  ) => {
    setNotice(null);
    try {
      const res =
        kind === "read"
          ? await memberApi.requestRead(book.bookId, table ?? TABLES[0].value)
          : await memberApi.requestBorrow(book.bookId);
      setNotice(
        kind === "read"
          ? `«${res.book_title}» 을(를) ${res.dropoff} 로 가져다 드릴게요`
          : `«${res.book_title}» 을(를) 안내데스크로 가져다 놓을게요. 사서에게 받아가세요`,
      );
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "요청하지 못했습니다");
    }
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

        {notice ? (
          <p className="mt-3 rounded-xl bg-primary-soft px-3 py-2 text-xs font-medium text-primary">
            {notice}
          </p>
        ) : null}

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
              {/* 요청 — 대여 중인 책은 로봇이 가지러 갈 수 없으므로 예약으로 유도한다 */}
              {b.inStock ? (
                loggedIn ? (
                  <div className="grid grid-cols-2 border-t border-border">
                    <button
                      onClick={() =>
                        setSelected(selected === b.id ? null : b.id)
                      }
                      className="border-r border-border py-2.5 text-xs font-bold text-primary"
                    >
                      📖 자리로 받기
                    </button>
                    <button
                      onClick={() => void request(b, "borrow")}
                      className="py-2.5 text-xs font-bold text-primary"
                    >
                      🧾 대여 신청
                    </button>
                  </div>
                ) : (
                  <Link
                    to="/login"
                    className="block w-full border-t border-border bg-primary-soft py-2.5 text-center text-xs font-bold text-primary"
                  >
                    로그인하고 요청하기
                  </Link>
                )
              ) : (
                <button
                  onClick={() =>
                    void memberApi
                      .reserve(b.bookId)
                      .then(() =>
                        setNotice("예약했습니다. 반납되면 알려드릴게요"),
                      )
                      .catch((e) =>
                        setNotice(
                          e instanceof Error
                            ? e.message
                            : "예약하지 못했습니다",
                        ),
                      )
                  }
                  disabled={!loggedIn}
                  className="block w-full border-t border-border bg-muted py-2.5 text-xs font-bold text-muted-foreground disabled:opacity-60"
                >
                  {loggedIn ? "대출 중 — 예약하기" : "대출 중"}
                </button>
              )}
              {selected === b.id && b.inStock && (
                <div className="border-t border-border bg-muted/40 p-3">
                  <p className="mb-2 text-[11px] font-semibold text-foreground">
                    어느 자리로 가져다 드릴까요?
                  </p>
                  <div className="grid grid-cols-3 gap-1.5">
                    {TABLES.map((t) => (
                      <button
                        key={t.value}
                        onClick={() => void request(b, "read", t.value)}
                        className="rounded-lg border border-border bg-card py-2 text-[10px] font-medium text-foreground"
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </article>
          ))}
        </div>

        <Link
          to="/chat"
          className="mt-6 block rounded-2xl border-2 border-dashed border-primary/30 bg-primary-soft/40 p-4 text-center"
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
