import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import {
  BookDetailSheet,
  reserveFromSheet,
} from "@/components/BookDetailSheet";
import { BookRow } from "@/components/BookRow";
import { fetchCatalog, type CatalogBook } from "@/lib/books-api";
import { LANGS, useI18n } from "@/lib/i18n";
import { useDebounced } from "@/lib/use-debounced";
import { useSpeechRecognition, useSpeechSupported } from "@/lib/use-speech";
import { useEffect, useRef, useState } from "react";
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
import { z } from "zod";

const homeSearchSchema = z.object({
  // 쿼리스트링은 문자열이라 coerce 가 필요하다.
  listen: z.coerce.boolean().optional(),
});

export const Route = createFileRoute("/home")({
  validateSearch: homeSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — 홈" }] }),
  component: Home,
});

function Home() {
  const { lang, tr } = useI18n();
  const [query, setQuery] = useState("");
  const [suggest, setSuggest] = useState<CatalogBook[]>([]);
  const [picked, setPicked] = useState<CatalogBook | null>(null);
  const debounced = useDebounced(query, 250);
  const supported = useSpeechSupported();
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, error, start, stop } =
    useSpeechRecognition(speechLang);
  const navigate = useNavigate();
  const { listen } = Route.useSearch();

  // 인식이 끝나면 검색이 아니라 LiBi 로 그대로 보낸다 — "대여 신청해줘" 같은
  // 말이 그냥 검색어로 찍히지 않고 실제로 처리되게.
  useEffect(() => {
    if (!listening && transcript.trim()) {
      const q = transcript.trim();
      const id = setTimeout(
        () => navigate({ to: "/chat", search: { q } }),
        400,
      );
      return () => clearTimeout(id);
    }
  }, [listening, transcript, navigate]);

  useEffect(() => {
    const term = debounced.trim();
    if (!term) {
      setSuggest([]);
      return;
    }
    let cancelled = false;
    void fetchCatalog({ q: term, limit: 30 }).then((rows) => {
      if (!cancelled) setSuggest(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  // useSpeechSupported()는 마운트 직후 false로 시작해 자기 useEffect에서만 true로
  // 바뀐다. []로 한 번만 도는 이 effect가 같은 마운트 플러시의 초기값(false)을
  // 캡처해버리면 영영 못 켜진다 — supported가 실제로 true가 될 때까지 기다린다.
  const startedRef = useRef(false);
  useEffect(() => {
    if (listen && supported && !startedRef.current) {
      startedRef.current = true;
      start();
    }
  }, [listen, supported, start]);

  const newest = BOOKS.slice(0, 3);

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-4">
        {/* 검색 — 마이크는 검색창 안에 작게 둔다. 예전엔 화면 절반을 차지하는
            큰 마이크였는데, 그 크기 때문에 "이건 뭐든 알아듣는 전용 버튼"처럼
            보여 "대여 신청해줘" 같은 말을 검색어로만 처리해 혼란을 줬다.
            검색 화면과 같은 작은 아이콘으로 두면 검색 보조 수단으로 읽힌다.
            말이 끝나면 검색이 아니라 LiBi(`/chat`)로 그대로 넘어간다. */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            if (q) navigate({ to: "/search", search: { q } });
          }}
          className="mt-4 flex items-center gap-2 rounded-2xl border border-border bg-card p-2 shadow-card"
        >
          <Search className="ml-2 size-5 shrink-0 text-muted-foreground" />
          <input
            value={listening ? transcript : query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={listening ? tr("listening") : tr("searchPh")}
            aria-label={tr("navSearch")}
            readOnly={listening}
            className="flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          {supported && (
            <button
              type="button"
              onClick={() => (listening ? stop() : start())}
              aria-label={tr("tapToTalk")}
              className={`flex size-10 shrink-0 items-center justify-center rounded-xl transition-colors ${
                listening
                  ? "voice-pulse bg-accent text-accent-foreground"
                  : "bg-primary text-primary-foreground"
              }`}
            >
              <Mic className="size-5" />
            </button>
          )}
        </form>
        {listening && (
          <p className="mt-2 text-center text-xs font-medium text-primary">
            🎙️ {tr("listening")}
          </p>
        )}
        {error === "unsupported" && (
          <p className="mt-2 text-center text-xs text-destructive">
            {tr("noSpeechSupport")}
          </p>
        )}
        {error === "error" && (
          <p className="mt-2 text-center text-xs text-destructive">
            {tr("micDenied")}
          </p>
        )}

        {suggest.length > 0 && (
          /* 10권까지 보이고 넘치면 목록 안에서 스크롤된다 — 아래 퀵메뉴가 밀리지 않게. */
          <div className="mt-2 max-h-[52vh] space-y-2 overflow-y-auto rounded-2xl border border-border bg-card p-2 shadow-card">
            {suggest.map((b) => (
              <BookRow key={b.id} book={b} onSelect={setPicked} />
            ))}
          </div>
        )}

        <BookDetailSheet
          book={picked}
          onOpenChange={(open) => !open && setPicked(null)}
          onReserve={(b) => void reserveFromSheet(b)}
        />

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
                className="w-40 shrink-0 snap-start"
              >
                <div
                  className={`flex h-56 items-center justify-center rounded-2xl bg-gradient-to-br ${b.color} text-6xl shadow-card`}
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

        <section className="mt-8 rounded-2xl border border-border bg-card p-5 shadow-card">
          <div className="flex items-center gap-4">
            <div className="flex size-14 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
              <BookMarked className="size-7" />
            </div>
            <div className="flex-1">
              <div className="text-base font-bold text-foreground">
                {tr("navChat")}
              </div>
              <div className="mt-0.5 text-sm text-muted-foreground">
                {tr("chatPh")}
              </div>
            </div>
            <Link
              to="/chat"
              className="shrink-0 rounded-full bg-primary px-5 py-2.5 text-sm font-bold text-primary-foreground"
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
      className="flex aspect-square flex-col items-center justify-center gap-3 rounded-2xl border border-border bg-card p-4 text-center shadow-card transition-transform active:scale-95"
    >
      <div
        className={`flex size-16 items-center justify-center rounded-2xl ${tones[tone]}`}
      >
        <Icon className="size-8" />
      </div>
      <div className="text-sm font-semibold leading-tight text-foreground">
        {label}
      </div>
    </Link>
  );
}
