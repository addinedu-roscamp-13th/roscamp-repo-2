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
import { getToken, memberApi } from "@/lib/member";
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
  const [summary, setSummary] = useState<{
    name: string;
    loans: number;
    requests: number;
    reservations: number;
    wishlist: number;
  } | null>(null);

  // 로그인 상태면 LiBi AI 카드 옆에 간단한 요약(대출/요청/예약/위시리스트 건수)을
  // 띄운다. 급한 것만 골라 보이는 대신 항상 네 가지를 그대로 보여준다 — 상세
  // 목록·조작은 /me 몫이라 여기선 개수만 가볍게 가져온다.
  useEffect(() => {
    if (getToken() === null) return;
    let cancelled = false;
    void Promise.all([
      memberApi.me(),
      memberApi.loans(),
      memberApi.requests(),
      memberApi.reservations(),
      memberApi.wishlist(),
    ])
      .then(([m, loans, requests, reservations, wishlist]) => {
        if (!cancelled) {
          setSummary({
            name: m.full_name ?? m.username,
            loans: loans.length,
            requests: requests.length,
            reservations: reservations.length,
            wishlist: wishlist.length,
          });
        }
      })
      .catch(() => {
        /* 요약을 못 가져와도 홈은 떠야 한다 — 카드가 조용히 로그인 유도로 남는다 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        <section className="mt-6 grid grid-cols-3 gap-3">
          <QuickCard
            to="/request"
            icon={BookMarked}
            label="도서 요청"
            tone="primary"
          />
          <QuickCard
            to="/recommend"
            icon={TrendingUp}
            label={tr("bestsellerShort")}
            tone="muted"
          />
          <QuickCard
            to="/map"
            icon={Map}
            label={tr("storeMapShort")}
            tone="accent"
          />
        </section>

        {/* New arrivals — 예전엔 카드가 w-40/h-56 로 커서 줄 하나가 화면 폭을 넘어
            튀어나온 느낌을 줬다. 카드는 줄이고, 대신 섹션 사이 여백을 넓혀 아래
            LiBi AI 카드까지의 배치가 성기게 남지 않게 한다. */}
        <section className="mt-10">
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
                  className={`flex h-44 items-center justify-center rounded-2xl bg-gradient-to-br ${b.color} text-5xl shadow-card`}
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

        {/* 왼쪽 LiBi AI · 오른쪽은 로그인 여부에 따라 내 정보 요약 또는 로그인 유도 */}
        <section className="mt-12 grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-4 shadow-card">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <BookMarked className="size-5" />
            </div>
            <div className="text-sm font-bold text-foreground">
              {tr("navChat")}
            </div>
            <div className="line-clamp-2 text-xs text-muted-foreground">
              {tr("chatPh")}
            </div>
            <Link
              to="/chat"
              className="mt-auto w-full rounded-full bg-primary py-2 text-center text-xs font-bold text-primary-foreground"
            >
              열기
            </Link>
          </div>

          {summary ? (
            <div className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-4 shadow-card">
              <div className="truncate text-sm font-bold text-foreground">
                {summary.name}님
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {(
                  [
                    { key: "loans", label: "대출", n: summary.loans },
                    { key: "requests", label: "요청", n: summary.requests },
                    {
                      key: "reservations",
                      label: "예약",
                      n: summary.reservations,
                    },
                    { key: "wishlist", label: "위시", n: summary.wishlist },
                  ] as const
                ).map((s) => (
                  <Link
                    key={s.key}
                    to="/me"
                    search={{ open: s.key }}
                    className="rounded-lg bg-muted/60 py-1 text-center active:bg-muted"
                  >
                    <div className="text-sm font-black leading-tight text-primary">
                      {s.n}
                    </div>
                    <div className="text-[10px] leading-tight text-muted-foreground">
                      {s.label}
                    </div>
                  </Link>
                ))}
              </div>
              <Link
                to="/me"
                className="mt-auto w-full rounded-full border border-border py-2 text-center text-xs font-semibold text-foreground"
              >
                내 정보 보기 →
              </Link>
            </div>
          ) : (
            <div className="flex flex-col gap-2 rounded-2xl border border-dashed border-primary/40 bg-primary-soft/40 p-4 shadow-card">
              <div className="text-sm font-bold text-foreground">
                로그인하고 더 써보세요
              </div>
              <div className="line-clamp-2 text-xs text-muted-foreground">
                대출·예약·요청 현황을 한눈에 볼 수 있어요
              </div>
              <Link
                to="/login"
                search={{ redirect: "/home" }}
                className="mt-auto w-full rounded-full bg-primary py-2 text-center text-xs font-bold text-primary-foreground"
              >
                로그인하기
              </Link>
            </div>
          )}
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
      {/* 라벨이 두 줄로 넘어가면 aspect-square 박스가 그 카드만 더 커진다
          (실측 확인됨) — 언어별 라벨 길이가 달라도 항상 한 줄로 고정해 세
          카드 외곽 박스 크기를 맞춘다. */}
      <div className="w-full truncate text-sm font-semibold leading-tight text-foreground">
        {label}
      </div>
    </Link>
  );
}
