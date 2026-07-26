import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import {
  BookDetailSheet,
  reserveFromSheet,
} from "@/components/BookDetailSheet";
import { BookRow, BookRowSkeleton } from "@/components/BookRow";
import { fetchPopular, type CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";
import { useEffect, useState } from "react";

const CATS = ["all", "literature", "art", "science", "humanities"] as const;
type Cat = (typeof CATS)[number];

export const Route = createFileRoute("/recommend")({
  head: () => ({ meta: [{ title: "LiBi — 추천 랭킹" }] }),
  component: Recommend,
});

function Recommend() {
  const { tr } = useI18n();
  const [cat, setCat] = useState<Cat>("all");
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<CatalogBook | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    void fetchPopular({ category: cat === "all" ? null : cat, limit: 10 })
      .then((rows) => alive && setBooks(rows))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [cat]);

  const labels: Record<Cat, string> = {
    all: tr("catAll"),
    literature: tr("catLiterature"),
    art: tr("catArt"),
    science: tr("catScience"),
    humanities: tr("catHumanities"),
  };

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <h1 className="text-balance text-xl font-bold leading-snug text-foreground">
          🔥 {tr("hotTitle")}
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          대출 횟수 기준 Top 10
        </p>

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
              {labels[c]}
            </button>
          ))}
        </div>

        <ol className="mt-5 space-y-2">
          {loading ? (
            <>
              <BookRowSkeleton />
              <BookRowSkeleton />
              <BookRowSkeleton />
            </>
          ) : books.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              아직 대출 기록이 없어요.
            </p>
          ) : (
            books.map((b, i) => (
              <li key={b.id} className="flex items-center gap-2">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-accent text-sm font-black text-accent-foreground">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <BookRow book={b} onSelect={setPicked} />
                </div>
              </li>
            ))
          )}
        </ol>
      </div>

      <BookDetailSheet
        book={picked}
        onOpenChange={(open) => !open && setPicked(null)}
        onReserve={(b) => void reserveFromSheet(b)}
      />
    </AppShell>
  );
}
