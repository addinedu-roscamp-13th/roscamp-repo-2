import { useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AdminBook } from "@/lib/ops-api";

/**
 * 작업지시(이송)용 도서 선택 팝업 — 검색 → 목록 → 클릭 → 닫힘.
 * 새 API 호출을 추가하지 않는다: 호출부가 이미 `ops.books()`로 불러온 전체
 * 카탈로그를 그대로 받아 클라이언트에서 필터링한다.
 * 대여 가능한 책만 굵게 표시하고 클릭 가능하게 하며, 대출중/대출불가능 도서는
 * 흐리게 표시하고 클릭할 수 없게 한다(로봇을 이미 나간 책 집으러 보내지 않도록).
 */
export function TaskBookPickerDialog({
  open,
  books,
  onClose,
  onPick,
}: {
  open: boolean;
  books: AdminBook[];
  onClose: () => void;
  onPick: (book: AdminBook) => void;
}) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const filtered = books.filter(
    (b) =>
      b.title_kr.toLowerCase().includes(query.toLowerCase()) ||
      b.author.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[80vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle>대상 도서 선택</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="제목·저자 검색"
        />
        <div className="max-h-96 space-y-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="p-4 text-center text-sm text-muted-foreground">
              검색 결과가 없습니다
            </p>
          ) : (
            filtered.map((b) => {
              const available = b.in_stock && !b.unavailable;
              return (
                <button
                  key={b.id}
                  type="button"
                  disabled={!available}
                  onClick={() => {
                    onPick(b);
                    onClose();
                  }}
                  className={`flex w-full items-center gap-2 rounded border px-3 py-2 text-left text-sm transition ${
                    available
                      ? "font-semibold hover:bg-muted/50"
                      : "cursor-not-allowed font-normal text-muted-foreground opacity-60"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {b.title_kr}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      {b.author} · {b.zone}
                    </span>
                  </span>
                  {!available ? (
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold">
                      {b.unavailable ? "대출불가능" : "대출중"}
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
