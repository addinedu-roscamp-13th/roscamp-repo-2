import { Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CATEGORY_LABEL, opsApi } from "@/lib/ops-api";

/**
 * 대여/반납 처리 공용 부품 — 대시보드 「회원관리」 카드에서 처음 만든 것을
 * 회원관리 페이지(`members.tsx`)에서도 그대로 재사용한다(복붙 금지).
 *
 * `LoanQueuePanel` 은 이미 회원이 정해진 상황(예: 회원 목록에서 클릭해 고른 상태)에서
 * 대여/반납 도서를 고르고 큐에 담았다가 한 번에 확정하는 부분만 담당한다. 회원을
 * "고르는" UI(대시보드의 `MemberPickerDialog`)는 여기 포함하지 않는다 — 호출부가
 * `memberId` 를 어떻게 정하는지는 저마다 다르기 때문(대시보드는 팝업, 회원관리 페이지는
 * 왼쪽 목록 클릭).
 */

export interface QuickMember {
  id: number;
  username: string;
  full_name: string | null;
}

export interface QuickBook {
  id: number;
  title: string;
  author: string;
  category: string;
  zone: string;
  in_stock: boolean;
  unavailable: boolean;
}

export interface QuickLoan {
  id: number;
  member_id: number;
  member_name: string;
  book_title: string;
  status: string;
  overdue: boolean;
}

const PANEL_TAB = {
  active: "bg-primary text-primary-foreground",
  inactive: "bg-muted text-muted-foreground hover:bg-muted/70",
};

export function LoanQueuePanel({
  memberId,
  onDone,
}: {
  memberId: number | null;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<"borrow" | "return">("borrow");
  const [borrowBooks, setBorrowBooks] = useState<QuickBook[]>([]);
  const [returnLoans, setReturnLoans] = useState<QuickLoan[]>([]);
  const [bookPickerOpen, setBookPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // 회원이 바뀌면 이전 회원 기준으로 담아둔 도서는 의미가 없어진다.
  useEffect(() => {
    setBorrowBooks([]);
    setReturnLoans([]);
  }, [memberId]);

  const reset = () => {
    setBorrowBooks([]);
    setReturnLoans([]);
  };

  const confirm = async () => {
    if (memberId === null) {
      toast.error("회원을 먼저 선택하세요");
      return;
    }
    setBusy(true);
    try {
      if (mode === "borrow") {
        if (borrowBooks.length === 0) {
          toast.error("도서를 하나 이상 선택하세요");
          return;
        }
        for (const b of borrowBooks) {
          await opsApi("/api/admin/circulation/borrow", {
            method: "POST",
            body: JSON.stringify({ member_id: memberId, book_id: b.id }),
          });
        }
        toast.success(`${borrowBooks.length}건 대출 처리했습니다 (14일)`);
      } else {
        if (returnLoans.length === 0) {
          toast.error("반납할 도서를 하나 이상 선택하세요");
          return;
        }
        for (const l of returnLoans) {
          await opsApi(`/api/admin/circulation/loans/${l.id}/return`, {
            method: "POST",
          });
        }
        toast.success(`${returnLoans.length}건 반납 처리했습니다`);
      }
      reset();
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "처리에 실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  const excludeIds = (mode === "borrow" ? borrowBooks : returnLoans).map(
    (s) => s.id,
  );

  const pick = (item: QuickBook | QuickLoan) => {
    if (mode === "borrow") {
      setBorrowBooks((prev) => [...prev, item as QuickBook]);
    } else {
      setReturnLoans((prev) => [...prev, item as QuickLoan]);
    }
    setBookPickerOpen(false);
  };

  const removeBook = (id: number) => {
    if (mode === "borrow") {
      setBorrowBooks((prev) => prev.filter((b) => b.id !== id));
    } else {
      setReturnLoans((prev) => prev.filter((l) => l.id !== id));
    }
  };

  const chips = mode === "borrow" ? borrowBooks : returnLoans;

  return (
    <div className="flex h-full min-h-0 flex-col gap-1.5">
      <div className="flex shrink-0 gap-1 text-xs font-semibold">
        <button
          type="button"
          onClick={() => setMode("borrow")}
          className={`flex-1 rounded px-2 py-[0.425rem] transition ${mode === "borrow" ? PANEL_TAB.active : PANEL_TAB.inactive}`}
        >
          대여
        </button>
        <button
          type="button"
          onClick={() => setMode("return")}
          className={`flex-1 rounded px-2 py-[0.425rem] transition ${mode === "return" ? PANEL_TAB.active : PANEL_TAB.inactive}`}
        >
          반납
        </button>
      </div>

      {/* 도서 — 고른 도서마다 한 줄씩 쌓이고, 맨 아래 "도서 추가" 바는 늘 고정,
          목록만 길어지면 그 부분만 스크롤 */}
      <div className="shrink-0">
        <p className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground">
          도서
        </p>
        {chips.length > 0 ? (
          <div className="mb-1 flex max-h-20 flex-col gap-1 overflow-y-auto">
            {chips.map((item) => (
              <div
                key={item.id}
                className="flex h-8 w-full shrink-0 items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs"
              >
                <span className="min-w-0 flex-1 truncate">
                  {"title" in item ? item.title : item.book_title}
                </span>
                <button
                  type="button"
                  aria-label="제거"
                  onClick={() => removeBook(item.id)}
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <button
          type="button"
          disabled={memberId === null}
          onClick={() => setBookPickerOpen(true)}
          className="flex h-8 w-full items-center gap-1.5 rounded-md border bg-background px-2.5 text-left text-xs outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className="min-w-0 flex-1 text-muted-foreground">
            {memberId === null ? "회원을 먼저 선택하세요" : "도서 추가"}
          </span>
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </div>

      <div className="mt-auto flex shrink-0 gap-1.5 text-xs font-semibold">
        <button
          type="button"
          disabled={busy}
          onClick={reset}
          className="flex-1 rounded border py-[0.425rem] disabled:opacity-40"
        >
          초기화
        </button>
        <button
          type="button"
          disabled={busy || memberId === null}
          onClick={() => void confirm()}
          className="flex-1 rounded bg-primary py-[0.425rem] text-primary-foreground disabled:opacity-40"
        >
          확정
        </button>
      </div>

      <BookPickerDialog
        open={bookPickerOpen}
        mode={mode}
        memberId={memberId}
        excludeIds={excludeIds}
        onClose={() => setBookPickerOpen(false)}
        onPick={pick}
      />
    </div>
  );
}

/**
 * 회원 선택 팝업 — 도서 선택 팝업과 같은 패턴(검색 → 목록 → "선택" → 창 닫힘).
 * 이미 불러온 회원 목록을 클라이언트에서 검색어로 걸러 보여준다(별도 API 호출 없음).
 */
export function MemberPickerDialog({
  open,
  members,
  onClose,
  onPick,
}: {
  open: boolean;
  members: QuickMember[];
  onClose: () => void;
  onPick: (m: QuickMember) => void;
}) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return members;
    return members.filter(
      (m) =>
        m.username.toLowerCase().includes(q) ||
        (m.full_name ?? "").toLowerCase().includes(q),
    );
  }, [members, query]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>회원 선택</DialogTitle>
        </DialogHeader>
        <div className="flex h-[30rem] flex-col gap-3">
          <div className="relative shrink-0">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="아이디·이름 검색"
              autoFocus
              className="h-10 w-full rounded-md border pl-9 text-sm outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="divide-y">
              <div className="sticky top-0 grid grid-cols-[2fr_1fr_auto] gap-3 bg-background px-2 pb-2 text-xs font-semibold text-muted-foreground">
                <span>이름</span>
                <span>아이디</span>
                <span />
              </div>
              {filtered.map((m) => (
                <div
                  key={m.id}
                  className="grid grid-cols-[2fr_1fr_auto] items-center gap-3 px-2 py-2 text-sm hover:bg-muted/40"
                >
                  <span className="min-w-0 truncate">
                    {m.full_name ?? m.username}
                  </span>
                  <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                    {m.username}
                  </span>
                  <button
                    type="button"
                    onClick={() => onPick(m)}
                    className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground"
                  >
                    선택
                  </button>
                </div>
              ))}
              {filtered.length === 0 ? (
                <p className="p-3 text-center text-xs text-muted-foreground">
                  검색 결과가 없습니다
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 도서 선택 팝업 — 웹사이트 주소검색 팝업과 같은 패턴(검색 → 목록 → "선택" → 창 닫힘).
 * 대여 모드는 전체 도서(대출중·사용불가 포함, 그런 건 상태 표시 후 선택 비활성화),
 * 반납 모드는 대출중인 것만 보여준다(반납 대상이 아닌 책은 애초에 뜰 이유가 없음).
 */
function BookPickerDialog({
  open,
  mode,
  memberId,
  excludeIds,
  onClose,
  onPick,
}: {
  open: boolean;
  mode: "borrow" | "return";
  memberId: number | null;
  excludeIds: number[];
  onClose: () => void;
  onPick: (item: QuickBook | QuickLoan) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [books, setBooks] = useState<QuickBook[]>([]);
  const [loans, setLoans] = useState<QuickLoan[]>([]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCategory("");
    }
  }, [open]);

  // books.tsx "1. 도서 검색" 과 같은 패턴 — 팝업을 열면 바로 전체 목록이 뜨고,
  // 검색어/분야를 바꾸면 그때그때(디바운스) 다시 불러온다. 검색을 눌러야만 보이는 방식이 아님.
  useEffect(() => {
    if (!open || mode !== "borrow") return;
    const t = setTimeout(() => {
      const params = new URLSearchParams({ include_unavailable: "true" });
      if (query.trim()) params.set("q", query.trim());
      if (category) params.set("category", category);
      void opsApi<QuickBook[]>(
        `/api/admin/circulation/available-books?${params.toString()}`,
      )
        .then(setBooks)
        .catch(() => setBooks([]));
    }, 250);
    return () => clearTimeout(t);
  }, [open, mode, query, category]);

  useEffect(() => {
    if (!open || mode !== "return" || memberId === null) return;
    void opsApi<QuickLoan[]>("/api/admin/circulation/loans")
      .then((all) =>
        setLoans(
          all.filter(
            (l) => l.status === "borrowed" && l.member_id === memberId,
          ),
        ),
      )
      .catch(() => setLoans([]));
  }, [open, mode, memberId]);

  // 고를 수 있는(대출가능/아직 안 담은) 항목이 위로 오게 — 대출중·이미 담은 건 아래로 밀어낸다.
  const sortedBooks = useMemo(() => {
    const rank = (b: QuickBook) =>
      Number(!b.in_stock || b.unavailable || excludeIds.includes(b.id));
    return [...books].sort((a, b) => rank(a) - rank(b));
  }, [books, excludeIds]);
  const sortedLoans = useMemo(() => {
    const rank = (l: QuickLoan) => Number(excludeIds.includes(l.id));
    return [...loans].sort((a, b) => rank(a) - rank(b));
  }, [loans, excludeIds]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {mode === "borrow" ? "도서 선택" : "반납 도서 선택"}
          </DialogTitle>
        </DialogHeader>
        {mode === "borrow" ? (
          <div className="flex h-[30rem] flex-col gap-3">
            <div className="flex shrink-0 gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="제목·저자 검색"
                  autoFocus
                  className="h-10 w-full rounded-md border pl-9 text-sm outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="h-10 shrink-0 rounded-md border bg-transparent px-2 text-sm outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">전체 분야</option>
                {Object.entries(CATEGORY_LABEL).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="divide-y">
                <div className="sticky top-0 grid grid-cols-[2fr_1fr_auto_auto] gap-3 bg-background px-2 pb-2 text-xs font-semibold text-muted-foreground">
                  <span>제목</span>
                  <span>저자</span>
                  <span>상태</span>
                  <span />
                </div>
                {sortedBooks.map((b) => {
                  const already = excludeIds.includes(b.id);
                  const disabled = !b.in_stock || b.unavailable || already;
                  const statusLabel = b.unavailable
                    ? "사용불가"
                    : !b.in_stock
                      ? "대출중"
                      : "대출가능";
                  return (
                    <div
                      key={b.id}
                      className={`grid grid-cols-[2fr_1fr_auto_auto] items-center gap-3 px-2 py-2 text-sm hover:bg-muted/40 ${disabled ? "opacity-50" : ""}`}
                    >
                      <span className="min-w-0 truncate">{b.title}</span>
                      <span className="min-w-0 truncate text-xs text-muted-foreground">
                        {b.author || "—"}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {statusLabel}
                      </span>
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => onPick(b)}
                        className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-40"
                      >
                        선택
                      </button>
                    </div>
                  );
                })}
                {books.length === 0 ? (
                  <p className="p-3 text-center text-xs text-muted-foreground">
                    검색 결과가 없습니다
                  </p>
                ) : null}
              </div>
            </div>
          </div>
        ) : memberId === null ? (
          <div className="flex h-[30rem] items-center justify-center">
            <p className="text-center text-xs text-muted-foreground">
              회원을 먼저 선택하세요
            </p>
          </div>
        ) : (
          <div className="flex h-[30rem] flex-col">
            <div className="min-h-0 flex-1 divide-y overflow-y-auto">
              <div className="sticky top-0 grid grid-cols-[2fr_1fr_auto_auto] gap-3 bg-background px-2 pb-2 text-xs font-semibold text-muted-foreground">
                <span>제목</span>
                <span>회원</span>
                <span>상태</span>
                <span />
              </div>
              {sortedLoans.map((l) => {
                const already = excludeIds.includes(l.id);
                return (
                  <div
                    key={l.id}
                    className={`grid grid-cols-[2fr_1fr_auto_auto] items-center gap-3 px-2 py-2 text-sm hover:bg-muted/40 ${already ? "opacity-50" : ""}`}
                  >
                    <span className="min-w-0 truncate">{l.book_title}</span>
                    <span className="min-w-0 truncate text-xs text-muted-foreground">
                      {l.member_name}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {l.overdue ? "연체" : "대출중"}
                    </span>
                    <button
                      type="button"
                      disabled={already}
                      onClick={() => onPick(l)}
                      className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-40"
                    >
                      선택
                    </button>
                  </div>
                );
              })}
              {loans.length === 0 ? (
                <p className="p-3 text-center text-xs text-muted-foreground">
                  대출 중인 도서가 없습니다
                </p>
              ) : null}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
