import { MapPin } from "lucide-react";
import type { ReactNode } from "react";

import { AVAILABILITY_LABEL, bookAvailability } from "@/lib/book-status";
import type { CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";

/**
 * 도서 한 줄 — 검색·서제스트·지도 구역·요청 목록·추천이 모두 이걸 쓴다.
 *
 * 목록에서는 상태 뱃지를 **띄우지 않는다**(요구사항). 상태는 눌러서 열리는 상세 시트가
 * 문장으로 설명한다. 다만 요청 화면처럼 "지금 고를 수 있나"가 곧 조작인 화면은
 * `showStatus` 로 켤 수 있다.
 */
export function BookRow({
  book,
  onSelect,
  showStatus = false,
  trailing,
}: {
  book: CatalogBook;
  onSelect?: (book: CatalogBook) => void;
  showStatus?: boolean;
  trailing?: ReactNode;
}) {
  const { lang } = useI18n();
  const availability = bookAvailability(book);

  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3 shadow-card">
      <button
        type="button"
        onClick={() => onSelect?.(book)}
        disabled={!onSelect}
        className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-default"
      >
        <span
          className={`flex size-14 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${book.color} text-3xl`}
        >
          {book.cover}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-foreground">
            {book.title[lang]}
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            {book.author}
          </span>
          <span className="mt-1 flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-primary">
              <MapPin className="size-3" />
              {book.zone} · {book.shelf}
            </span>
            {showStatus && (
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                  availability === "available"
                    ? "bg-emerald-100 text-emerald-700"
                    : availability === "borrowed"
                      ? "bg-stone-200 text-stone-600"
                      : "bg-destructive/10 text-destructive"
                }`}
              >
                {AVAILABILITY_LABEL[availability]}
              </span>
            )}
          </span>
        </span>
      </button>
      {trailing}
    </div>
  );
}

/** 목록을 불러오는 동안 보여줄 자리표시자. */
export function BookRowSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3">
      <div className="size-14 shrink-0 animate-pulse rounded-xl bg-muted" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}
