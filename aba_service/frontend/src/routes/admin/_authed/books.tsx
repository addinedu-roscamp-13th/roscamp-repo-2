import { createFileRoute } from "@tanstack/react-router";
import {
  AlertTriangle,
  CheckCircle2,
  Pencil,
  Plus,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { StackedStatusBar } from "@/components/admin/charts";
import {
  SortIcon,
  useSortableTable,
} from "@/components/admin/useSortableTable";
import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { NAMED_WAYPOINTS } from "@/lib/map-waypoints";
import {
  CATEGORY_COVER,
  CATEGORY_LABEL,
  ops,
  type AdminBook,
  type ShelfRow,
} from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/books")({
  head: () => ({ meta: [{ title: "LiBi Admin — 도서 · 서가 관리" }] }),
  component: BooksPage,
});

/**
 * 도서 관리(검색·등록·수정·삭제·재고·위치) + 서가 관리.
 *
 * `zone` 은 **로봇 내비 그래프의 정점 이름**이다 — 여기서 고른 값이 그대로 배달 주문의
 * pickup 이 되므로, 자유 입력이 아니라 waypoint 목록에서만 고르게 한다.
 */

const SHELF_WAYPOINTS = NAMED_WAYPOINTS.filter((w) => w.kind === "shelf");
const CATEGORIES = Object.keys(CATEGORY_LABEL);

// 네이티브 select 를 shadcn Input 과 같은 표면으로 맞추기 위한 공용 클래스.
const selectClass =
  "h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

interface BookFormValue {
  title_kr: string;
  author: string;
  category: string;
  cover: string;
  zone: string;
  shelf: string;
  in_stock: boolean;
  unavailable: boolean;
}

const EMPTY: BookFormValue = {
  title_kr: "",
  author: "",
  category: "literature",
  cover: CATEGORY_COVER.literature,
  zone: SHELF_WAYPOINTS[0]?.name ?? "문학-1",
  shelf: "",
  in_stock: true,
  unavailable: false,
};

// 재고 배지·서가 현황 차트가 공유하는 3상태 색 — 이 페이지 안에서 한 가지 색 언어로 통일.
const STATUS_COLOR = {
  available: "#10b981", // emerald-500
  borrowed: "#f59e0b", // amber-500
  unavailable: "#f43f5e", // rose-500
} as const;

function BooksPage() {
  const [books, setBooks] = useState<AdminBook[]>([]);
  const [shelves, setShelves] = useState<ShelfRow[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [createForm, setCreateForm] = useState<BookFormValue>({ ...EMPTY });
  const [editForm, setEditForm] = useState<
    (BookFormValue & { id: number }) | null
  >(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminBook | null>(null);
  const [deleting, setDeleting] = useState(false);
  const editSectionRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const [b, s] = await Promise.all([ops.books(q, cat), ops.shelves()]);
      setBooks(b);
      setShelves(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, [q, cat]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
  }, [load]);

  const {
    sorted: sortedBooks,
    sortKey: booksSortKey,
    direction: booksDirection,
    toggle: toggleBooksSort,
  } = useSortableTable<AdminBook>(books, {
    title_kr: (a, b) => a.title_kr.localeCompare(b.title_kr),
    author: (a, b) => a.author.localeCompare(b.author),
    category: (a, b) =>
      (CATEGORY_LABEL[a.category] ?? a.category).localeCompare(
        CATEGORY_LABEL[b.category] ?? b.category,
      ),
    zone: (a, b) => a.zone.localeCompare(b.zone),
    status: (a, b) => {
      const rank = (x: AdminBook) => (x.unavailable ? 2 : x.in_stock ? 0 : 1);
      return rank(a) - rank(b);
    },
  });

  const shelfChartData = useMemo(
    () =>
      shelves.map((s) => ({
        zone: s.zone,
        available: s.status.available,
        borrowed: s.status.borrowed,
        unavailable: s.status.unavailable,
        total: s.total,
      })),
    [shelves],
  );

  const act = async (
    fn: () => Promise<unknown>,
    ok: string,
    after?: () => void,
  ) => {
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      after?.();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리 실패");
    }
  };

  const submitCreate = () =>
    act(
      () => ops.createBook({ ...createForm }),
      "등록했습니다",
      () => setCreateForm({ ...EMPTY }),
    );

  const submitEdit = () => {
    if (!editForm) return;
    void act(
      () => ops.updateBook(editForm.id, { ...editForm }),
      "수정했습니다",
      () => setEditForm(null),
    );
  };

  const openEdit = (b: AdminBook) => {
    setEditForm({
      id: b.id,
      title_kr: b.title_kr,
      author: b.author,
      category: b.category,
      cover: CATEGORY_COVER[b.category] ?? b.cover,
      zone: b.zone,
      shelf: b.shelf,
      in_stock: b.in_stock,
      unavailable: b.unavailable,
    });
    requestAnimationFrame(() =>
      editSectionRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      }),
    );
  };

  const removeBook = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await ops.deleteBook(deleteTarget.id);
      toast.success(`«${deleteTarget.title_kr}» 도서를 삭제했습니다`);
      if (editForm?.id === deleteTarget.id) setEditForm(null);
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제 실패");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AdminShell title="도서 · 서가 관리">
      <div className="space-y-6">
        {msg ? (
          <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
            {msg}
          </p>
        ) : null}
        {err ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        {/* 전체 재고 현황 — 서가별 상세 차트보다 위, 한눈에 보는 총계 */}
        <section className="rounded-lg border bg-muted/30 p-4">
          <h3 className="mb-2 text-sm font-semibold text-muted-foreground">
            전체 재고 현황
          </h3>
          <StackedStatusBar
            rows={[
              {
                label: "전체",
                values: shelfChartData.reduce(
                  (acc, s) => ({
                    available: acc.available + s.available,
                    borrowed: acc.borrowed + s.borrowed,
                    unavailable: acc.unavailable + s.unavailable,
                  }),
                  { available: 0, borrowed: 0, unavailable: 0 },
                ),
              },
            ]}
            segments={[
              {
                key: "available",
                label: "대출가능",
                color: STATUS_COLOR.available,
              },
              {
                key: "borrowed",
                label: "대출중",
                color: STATUS_COLOR.borrowed,
              },
              {
                key: "unavailable",
                label: "대출불가능",
                color: STATUS_COLOR.unavailable,
              },
            ]}
            unit="권"
          />
        </section>

        {/* 서가 배치 현황 — 참고용 현황 차트, 등록/목록보다 낮은 시각 무게 */}
        <section className="rounded-lg border border-dashed bg-muted/30 p-4">
          <h3 className="mb-1 text-sm font-semibold text-muted-foreground">
            서가 배치 현황
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">
            서가 이름은 로봇 내비 정점과 같습니다. 도서의 위치를 바꾸면 로봇이
            집으러 가는 곳도 함께 바뀝니다.
          </p>
          {shelfChartData.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              등록된 서가가 없습니다
            </p>
          ) : (
            <StackedStatusBar
              rows={shelfChartData.map((s) => ({
                label: s.zone,
                values: {
                  available: s.available,
                  borrowed: s.borrowed,
                  unavailable: s.unavailable,
                },
              }))}
              segments={[
                {
                  key: "available",
                  label: "대출가능",
                  color: STATUS_COLOR.available,
                },
                {
                  key: "borrowed",
                  label: "대출중",
                  color: STATUS_COLOR.borrowed,
                },
                {
                  key: "unavailable",
                  label: "대출불가능(훼손·분실)",
                  color: STATUS_COLOR.unavailable,
                },
              ]}
              unit="권"
            />
          )}
        </section>

        {/* 1) 도서 검색 — 목록 + 정렬/필터, 여기서 수정을 누르면 3번 섹션이 채워진다 */}
        <section className="rounded-xl border bg-card p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              1. 도서 검색{" "}
              <span className="text-xs font-normal text-muted-foreground">
                ({books.length})
              </span>
            </h3>
            <div className="relative ml-auto w-full sm:w-56">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="제목·저자 검색"
                className="h-8 pl-9"
              />
            </div>
            <select
              value={cat}
              onChange={(e) => setCat(e.target.value)}
              className={`h-8 w-auto shrink-0 ${selectClass}`}
            >
              <option value="">전체 분야</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                {(
                  [
                    { key: "title_kr", label: "제목" },
                    { key: "author", label: "저자" },
                    { key: "category", label: "분야" },
                    { key: "zone", label: "서가" },
                    { key: "status", label: "재고" },
                  ] as const
                ).map((col) => (
                  <TableHead
                    key={col.key}
                    className="cursor-pointer select-none hover:text-foreground"
                    onClick={() => toggleBooksSort(col.key)}
                  >
                    {col.label}
                    <SortIcon
                      active={booksSortKey === col.key}
                      direction={booksDirection}
                    />
                  </TableHead>
                ))}
                <TableHead className="text-right">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedBooks.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-medium">
                    {CATEGORY_COVER[b.category] ?? b.cover} {b.title_kr}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {b.author}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {CATEGORY_LABEL[b.category] ?? b.category}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {b.zone} {b.shelf}
                  </TableCell>
                  <TableCell>
                    {b.unavailable ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/25 px-2.5 py-1 text-xs font-bold text-rose-800">
                        <AlertTriangle className="size-3.5" />
                        대출불가능
                      </span>
                    ) : b.in_stock ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/25 px-2.5 py-1 text-xs font-bold text-emerald-800">
                        <CheckCircle2 className="size-3.5" />
                        가능
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/25 px-2.5 py-1 text-xs font-bold text-amber-800">
                        <XCircle className="size-3.5" />
                        대출중
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="수정"
                        onClick={() => openEdit(b)}
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="삭제"
                        onClick={() => setDeleteTarget(b)}
                      >
                        <Trash2 className="size-4 text-destructive" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {books.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-6 text-center text-muted-foreground"
                  >
                    도서가 없습니다
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </section>

        {/* 2) 도서 등록 — 항상 빈 폼, 등록 후에도 비워진 채 유지 */}
        <section className="rounded-xl border border-l-4 border-l-primary bg-card p-5 shadow-card">
          <div className="mb-4 flex items-center gap-2">
            <Plus className="size-4 text-primary" />
            <h3 className="text-sm font-semibold">2. 도서 등록</h3>
          </div>
          <BookFormFields value={createForm} onChange={setCreateForm} />
          <div className="mt-4">
            <Button
              onClick={() => void submitCreate()}
              disabled={!createForm.title_kr || !createForm.author}
            >
              등록
            </Button>
          </div>
        </section>

        {/* 3) 도서 수정 — 1번 목록에서 수정을 누르면 여기가 채워진다 */}
        <section
          ref={editSectionRef}
          className="rounded-xl border border-l-4 border-l-amber-500 bg-card p-5 shadow-card"
        >
          <div className="mb-4 flex items-center gap-2">
            <Pencil className="size-4 text-amber-600" />
            <h3 className="text-sm font-semibold">
              {editForm ? `3. 도서 수정 (#${editForm.id})` : "3. 도서 수정"}
            </h3>
          </div>
          {editForm ? (
            <>
              <BookFormFields
                value={editForm}
                onChange={(v) => setEditForm({ ...v, id: editForm.id })}
              />
              <div className="mt-4 flex gap-2">
                <Button onClick={() => void submitEdit()}>수정</Button>
                <Button variant="outline" onClick={() => setEditForm(null)}>
                  취소
                </Button>
              </div>
            </>
          ) : (
            <p className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
              위 목록에서 «수정» 버튼을 누르면 여기에 채워집니다
            </p>
          )}
        </section>
      </div>

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="도서 삭제"
        description={
          <>«{deleteTarget?.title_kr}» 도서를 삭제할까요? 되돌릴 수 없습니다.</>
        }
        onConfirm={() => void removeBook()}
        busy={deleting}
      />
    </AdminShell>
  );
}

function BookFormFields({
  value,
  onChange,
}: {
  value: BookFormValue;
  onChange: (v: BookFormValue) => void;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="제목">
        <Input
          value={value.title_kr}
          onChange={(e) => onChange({ ...value, title_kr: e.target.value })}
        />
      </Field>
      <Field label="저자">
        <Input
          value={value.author}
          onChange={(e) => onChange({ ...value, author: e.target.value })}
        />
      </Field>
      <Field label="분야">
        <select
          value={value.category}
          onChange={(e) =>
            onChange({
              ...value,
              category: e.target.value,
              cover: CATEGORY_COVER[e.target.value] ?? value.cover,
            })
          }
          className={selectClass}
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="서가 (내비 정점)">
        <select
          value={value.zone}
          onChange={(e) => onChange({ ...value, zone: e.target.value })}
          className={selectClass}
        >
          {NAMED_WAYPOINTS.map((w) => (
            <option key={w.name} value={w.name}>
              {w.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="청구기호/줄">
        <Input
          value={value.shelf}
          onChange={(e) => onChange({ ...value, shelf: e.target.value })}
          placeholder="예: 첫째 줄"
        />
      </Field>
      <Field label="재고">
        <select
          value={value.in_stock ? "1" : "0"}
          onChange={(e) =>
            onChange({ ...value, in_stock: e.target.value === "1" })
          }
          className={selectClass}
        >
          <option value="1">대출 가능</option>
          <option value="0">대출 중</option>
        </select>
      </Field>
      <Field label="훼손·분실">
        <select
          value={value.unavailable ? "1" : "0"}
          onChange={(e) =>
            onChange({ ...value, unavailable: e.target.value === "1" })
          }
          className={selectClass}
        >
          <option value="0">정상</option>
          <option value="1">훼손/분실 (대출 불가)</option>
        </select>
      </Field>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}
