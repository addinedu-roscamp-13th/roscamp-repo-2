import { useNavigate } from "@tanstack/react-router";
import { MapPin } from "lucide-react";
import { toast } from "sonner";

import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { availabilitySentence, bookAvailability } from "@/lib/book-status";
import type { CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";
import { memberApi } from "@/lib/member";

/**
 * 도서 상세 — 별도 라우트가 아니라 바텀시트다.
 *
 * 목록이 이미 들고 있는 도서 객체를 그대로 받는다. `summary`·`for_whom` 이 목록
 * 응답에 이미 들어 있어서 단건 조회 API 가 필요 없다.
 *
 * 요청으로 넘어갈 때 `?bookId=` 를 달아 보내면 요청 위저드가 1단계를 채운 채
 * 2단계에서 시작한다 — 방금 고른 책을 또 고르게 하지 않는다.
 */
export function BookDetailSheet({
  book,
  onOpenChange,
  onReserve,
}: {
  book: CatalogBook | null;
  onOpenChange: (open: boolean) => void;
  /** 대출 중인 책의 예약. 주지 않으면 예약 버튼 대신 안내만 뜬다. */
  onReserve?: (book: CatalogBook) => void;
}) {
  const { lang } = useI18n();
  const navigate = useNavigate();
  if (!book) return null;

  const availability = bookAvailability(book);
  const tags = book.forWhom?.[lang] ?? [];

  return (
    <Drawer open={book !== null} onOpenChange={onOpenChange}>
      <DrawerContent className="max-h-[85vh]">
        <DrawerHeader className="text-left">
          <div className="flex items-start gap-3">
            <span
              className={`flex size-20 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br ${book.color} text-4xl`}
            >
              {book.cover}
            </span>
            <div className="min-w-0 flex-1">
              <DrawerTitle className="text-base leading-snug">
                {book.title[lang]}
              </DrawerTitle>
              <DrawerDescription className="mt-0.5">
                {book.author}
              </DrawerDescription>
              <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                <MapPin className="size-3" />
                {book.zone} · {book.shelf}
              </span>
            </div>
            {/* 예전엔 본문 하단에 h-12 풀폭 버튼이었다 — 요청 하나 위해 시트를 절반 넘게
                차지해서 우측 상단의 작은 버튼으로 옮겼다. */}
            {availability === "available" ? (
              <button
                onClick={() => {
                  onOpenChange(false);
                  void navigate({
                    to: "/request",
                    search: { bookId: book.bookId },
                  });
                }}
                className="shrink-0 rounded-full bg-primary px-3 py-1.5 text-xs font-bold text-primary-foreground"
              >
                요청하기 →
              </button>
            ) : null}
          </div>
        </DrawerHeader>

        <div className="overflow-y-auto px-4 pb-6">
          <p
            className={`rounded-xl px-3 py-2 text-xs font-semibold ${
              availability === "available"
                ? "bg-emerald-500/10 text-emerald-700"
                : availability === "borrowed"
                  ? "bg-muted text-muted-foreground"
                  : "bg-destructive/10 text-destructive"
            }`}
          >
            {availabilitySentence(availability, book.zone, book.shelf)}
          </p>

          {book.summary?.[lang] ? (
            <p className="mt-4 text-sm leading-relaxed text-foreground">
              {book.summary[lang]}
            </p>
          ) : null}

          {tags.length > 0 ? (
            <div className="mt-4">
              <p className="text-[11px] font-bold uppercase tracking-wide text-primary">
                이런 분께
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {tags.map((k) => (
                  <span
                    key={k}
                    className="rounded-full bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-accent-foreground"
                  >
                    {k}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {/* 상태마다 갈 곳이 다르다.
              - 배치중  : 요청 버튼은 위 헤더 우측 상단에 있다(요청으로).
              - 대출 중 : **요청으로 보내지 않는다.** 로봇이 없는 책을 찾으러 가면 안 되므로
                          여기서 바로 예약한다.
              - 대출 불가: 아무 데도 못 간다. 이유만 위에 이미 적혀 있다. */}
          {availability === "borrowed" && onReserve ? (
            <button
              onClick={() => {
                onReserve(book);
                onOpenChange(false);
              }}
              className="mt-6 h-12 w-full rounded-2xl bg-secondary text-sm font-bold text-secondary-foreground"
            >
              예약하기
            </button>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}

/** 시트에서 바로 예약한다. 결과는 토스트로 알린다. */
export async function reserveFromSheet(book: CatalogBook): Promise<void> {
  try {
    await memberApi.reserve(book.bookId);
    toast.success("예약했습니다. 반납되면 알려드릴게요");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "예약하지 못했습니다");
  }
}
