import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { useI18n } from "@/lib/i18n";
import { BOOKS, type Book } from "@/lib/mock-data";
import { fetchBooks } from "@/lib/books-api";
import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";

const CATS = ["all", "literature", "art", "science"] as const;
type Cat = (typeof CATS)[number];

export const Route = createFileRoute("/recommend")({
  head: () => ({ meta: [{ title: "Labi Bot — 추천 랭킹" }] }),
  component: Recommend,
});

function Recommend() {
  const { lang, tr } = useI18n();
  const [cat, setCat] = useState<Cat>("all");
  const [open, setOpen] = useState<string | null>(null);
  // Load the live catalog from the DB; fall back to the local mock on failure.
  const [books, setBooks] = useState<Book[]>(BOOKS);

  useEffect(() => {
    let alive = true;
    fetchBooks({ limit: 200 }).then((rows) => {
      if (alive && rows.length) setBooks(rows);
    });
    return () => {
      alive = false;
    };
  }, []);

  const filtered = cat === "all" ? books : books.filter((b) => b.category === cat);

  const labels: Record<Cat, string> = {
    all: tr("catAll"),
    literature: tr("catLiterature"),
    art: tr("catArt"),
    science: tr("catScience"),
  };

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <h1 className="text-balance text-xl font-bold leading-snug text-foreground">
          🔥 {tr("hotTitle")}
        </h1>

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

        <ol className="mt-5 space-y-3">
          {filtered.map((b, i) => {
            const isOpen = open === b.id;
            return (
              <li
                key={b.id}
                className="overflow-hidden rounded-2xl border border-border bg-card shadow-card"
              >
                <button
                  onClick={() => setOpen(isOpen ? null : b.id)}
                  className="flex w-full items-center gap-3 p-3 text-left"
                >
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent text-base font-black text-accent-foreground">
                    #{i + 1}
                  </div>
                  <div
                    className={`flex size-16 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${b.color} text-3xl`}
                  >
                    {b.cover}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-1 text-sm font-bold text-foreground">
                      {b.title[lang]}
                    </div>
                    <div className="text-xs text-muted-foreground">{b.author}</div>
                  </div>
                  <ChevronDown
                    className={`size-5 shrink-0 text-muted-foreground transition-transform ${
                      isOpen ? "rotate-180" : ""
                    }`}
                  />
                </button>
                {isOpen && (
                  <div className="border-t border-border bg-muted/30 p-4">
                    <p className="text-sm leading-relaxed text-foreground">{b.summary[lang]}</p>
                    <div className="mt-3">
                      <p className="text-[11px] font-bold uppercase tracking-wide text-primary">
                        {tr("recommendFor")}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {b.forWhom[lang]?.map((k) => (
                          <span
                            key={k}
                            className="rounded-full bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-accent-foreground"
                          >
                            {k}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </AppShell>
  );
}
