import { createFileRoute } from "@tanstack/react-router";
import {
  CheckCircle2,
  Pencil,
  Plus,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

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
 * 도서 관리(등록·수정·삭제·재고·위치) + 서가 관리.
 *
 * `zone` 은 **로봇 내비 그래프의 정점 이름**이다 — 여기서 고른 값이 그대로 배달 주문의
 * pickup 이 되므로, 자유 입력이 아니라 waypoint 목록에서만 고르게 한다.
 */

const SHELF_WAYPOINTS = NAMED_WAYPOINTS.filter((w) => w.kind === "shelf");
const CATEGORIES = Object.keys(CATEGORY_LABEL);

// 네이티브 select 를 shadcn Input 과 같은 표면으로 맞추기 위한 공용 클래스.
const selectClass =
  "h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

const EMPTY = {
  title_kr: "",
  author: "",
  category: "literature",
  cover: "📘",
  zone: SHELF_WAYPOINTS[0]?.name ?? "문학-1",
  shelf: "",
  in_stock: true,
};

function BooksPage() {
  const [books, setBooks] = useState<AdminBook[]>([]);
  const [shelves, setShelves] = useState<ShelfRow[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [form, setForm] = useState<typeof EMPTY & { id?: number }>({
    ...EMPTY,
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminBook | null>(null);
  const [deleting, setDeleting] = useState(false);

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

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      setForm({ ...EMPTY });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리 실패");
    }
  };

  const save = () =>
    act(
      () => (form.id ? ops.updateBook(form.id, form) : ops.createBook(form)),
      form.id ? "수정했습니다" : "등록했습니다",
    );

  const removeBook = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await ops.deleteBook(deleteTarget.id);
      toast.success(`«${deleteTarget.title_kr}» 도서를 삭제했습니다`);
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

        {/* 서가 배치 — 참고용 현황, 등록/목록보다 낮은 시각 무게 */}
        <section className="rounded-lg border border-dashed bg-muted/30 p-4">
          <h3 className="mb-1 text-sm font-semibold text-muted-foreground">
            서가 배치 현황
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">
            서가 이름은 로봇 내비 정점과 같습니다. 도서의 위치를 바꾸면 로봇이
            집으러 가는 곳도 함께 바뀝니다.
          </p>
          <div className="flex flex-wrap gap-2">
            {shelves.map((s) => (
              <div
                key={s.zone}
                className="rounded-md border bg-card px-3 py-2"
              >
                <div className="text-sm font-semibold">{s.zone}</div>
                <div className="text-xs text-muted-foreground">
                  {s.total}권 ·{" "}
                  {Object.entries(s.categories)
                    .map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ${n}`)
                    .join(", ")}
                </div>
              </div>
            ))}
            {shelves.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                등록된 서가가 없습니다
              </p>
            ) : null}
          </div>
        </section>

        {/* 등록 / 수정 — 조작 영역이므로 좌측 강조 바 + 카드 표면으로 목록과 분리 */}
        <section className="rounded-xl border border-l-4 border-l-primary bg-card p-5 shadow-card">
          <div className="mb-4 flex items-center gap-2">
            {form.id ? (
              <Pencil className="size-4 text-primary" />
            ) : (
              <Plus className="size-4 text-primary" />
            )}
            <h3 className="text-sm font-semibold">
              {form.id ? `도서 수정 (#${form.id})` : "도서 등록"}
            </h3>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="제목">
              <Input
                value={form.title_kr}
                onChange={(e) => setForm({ ...form, title_kr: e.target.value })}
              />
            </Field>
            <Field label="저자">
              <Input
                value={form.author}
                onChange={(e) => setForm({ ...form, author: e.target.value })}
              />
            </Field>
            <Field label="분야">
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
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
                value={form.zone}
                onChange={(e) => setForm({ ...form, zone: e.target.value })}
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
                value={form.shelf}
                onChange={(e) => setForm({ ...form, shelf: e.target.value })}
                placeholder="예: 첫째 줄"
              />
            </Field>
            <Field label="표지">
              <Input
                value={form.cover}
                onChange={(e) => setForm({ ...form, cover: e.target.value })}
              />
            </Field>
            <Field label="재고">
              <select
                value={form.in_stock ? "1" : "0"}
                onChange={(e) =>
                  setForm({ ...form, in_stock: e.target.value === "1" })
                }
                className={selectClass}
              >
                <option value="1">대출 가능</option>
                <option value="0">대출 중</option>
              </select>
            </Field>
            <div className="flex items-end gap-2">
              <Button
                onClick={() => void save()}
                disabled={!form.title_kr || !form.author}
                className="flex-1"
              >
                {form.id ? "수정" : "등록"}
              </Button>
              {form.id ? (
                <Button variant="outline" onClick={() => setForm({ ...EMPTY })}>
                  취소
                </Button>
              ) : null}
            </div>
          </div>
        </section>

        {/* 목록 — 조회 전용 영역, 등록 폼과 대비되도록 강조 없는 평평한 카드 */}
        <section className="rounded-xl border bg-card p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              도서 목록{" "}
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
                <TableHead>제목</TableHead>
                <TableHead>저자</TableHead>
                <TableHead>분야</TableHead>
                <TableHead>서가</TableHead>
                <TableHead>재고</TableHead>
                <TableHead className="text-right">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {books.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-medium">
                    {b.cover} {b.title_kr}
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
                    {b.in_stock ? (
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
                        onClick={() =>
                          setForm({
                            id: b.id,
                            title_kr: b.title_kr,
                            author: b.author,
                            category: b.category,
                            cover: b.cover,
                            zone: b.zone,
                            shelf: b.shelf,
                            in_stock: b.in_stock,
                          })
                        }
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
