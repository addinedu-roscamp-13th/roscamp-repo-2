import { createFileRoute } from "@tanstack/react-router";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { ConfirmDeleteDialog } from "@/components/admin/ConfirmDeleteDialog";
import { LoanQueuePanel } from "@/components/admin/circulation";
import { StackedStatusBar } from "@/components/admin/charts";
import {
  SortIcon,
  useSortableTable,
} from "@/components/admin/useSortableTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export const Route = createFileRoute("/admin/_authed/members")({
  head: () => ({ meta: [{ title: "LiBi Admin — 회원 관리 및 대여/반납" }] }),
  component: MembersPage,
});

/**
 * 사서용 회원 관리 + 대여/반납 처리.
 *
 * 회원 앱의 「대여 신청」은 로봇이 안내데스크로 책을 가져다 놓는 데까지다.
 * **실제 대출 확정은 여기서** 사서가 누른다(`cb_loans` 행이 여기서 생긴다).
 */

interface MemberRow {
  id: number;
  username: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
  active_loans: number;
  total_loans: number;
}

interface LoanRow {
  id: number;
  member_id: number;
  member_name: string;
  book_id: number;
  book_title: string;
  status: string;
  borrowed_at: string;
  due_at: string;
  returned_at: string | null;
  overdue: boolean;
}

const TOKEN_KEY = "labi.adminToken";

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token =
    typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(TOKEN_KEY);
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let msg = `요청 실패 (${res.status})`;
    try {
      const b = await res.json();
      if (typeof b?.detail === "string") msg = b.detail;
    } catch {
      /* JSON 아니면 기본 메시지 */
    }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const fmt = (iso: string) => iso.slice(0, 10);

const MEMBER_STATUS_COLOR = { active: "#10b981", inactive: "#94a3b8" } as const;
const LOAN_STATUS_COLOR = {
  normal: "#f59e0b",
  overdue: "#f43f5e",
  none: "#94a3b8",
} as const;

function MembersPage() {
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [loans, setLoans] = useState<LoanRow[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [memberQuery, setMemberQuery] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 회원 등록 (다이얼로그)
  const [createOpen, setCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [creating, setCreating] = useState(false);

  // 회원 수정(다이얼로그)
  const [editing, setEditing] = useState<MemberRow | null>(null);
  const [editFullName, setEditFullName] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [saving, setSaving] = useState(false);

  // 회원 삭제(확인 다이얼로그)
  const [deleteTarget, setDeleteTarget] = useState<MemberRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [m, l] = await Promise.all([
        api<MemberRow[]>("/api/admin/circulation/members"),
        api<LoanRow[]>("/api/admin/circulation/loans"),
      ]);
      setMembers(m);
      setLoans(l);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast.success(ok);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "처리하지 못했습니다");
    }
  };

  const createMember = async (e: FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await api("/api/admin/circulation/members", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername.trim(),
          full_name: newFullName.trim() || undefined,
          password: newPassword,
        }),
      });
      toast.success(`«${newUsername}» 회원을 등록했습니다`);
      setNewUsername("");
      setNewFullName("");
      setNewPassword("");
      setCreateOpen(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "회원 등록에 실패했습니다");
    } finally {
      setCreating(false);
    }
  };

  const openEdit = (m: MemberRow) => {
    setEditing(m);
    setEditFullName(m.full_name ?? "");
    setEditPassword("");
  };

  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await api(`/api/admin/circulation/members/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          full_name: editFullName.trim() || null,
          ...(editPassword.trim() ? { password: editPassword.trim() } : {}),
        }),
      });
      toast.success("회원 정보를 수정했습니다");
      setEditing(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "수정에 실패했습니다");
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api(`/api/admin/circulation/members/${deleteTarget.id}`, {
        method: "DELETE",
      });
      toast.success(
        `«${deleteTarget.full_name ?? deleteTarget.username}» 회원을 삭제했습니다`,
      );
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "삭제에 실패했습니다");
    } finally {
      setDeleting(false);
    }
  };

  const memberLoans = useMemo(
    () =>
      selected === null ? [] : loans.filter((l) => l.member_id === selected),
    [loans, selected],
  );
  const activeLoans = useMemo(
    () => loans.filter((l) => l.status === "borrowed"),
    [loans],
  );
  const overdue = activeLoans.filter((l) => l.overdue);

  // 상단 stat 차트 — 회원 활성/비활성, 대출 상태(정상/연체/미대출)는 회원 단위로 집계.
  const overdueMemberIds = useMemo(
    () => new Set(overdue.map((l) => l.member_id)),
    [overdue],
  );
  const loanStatusCounts = useMemo(() => {
    let normal = 0;
    let lateCount = 0;
    let none = 0;
    for (const m of members) {
      if (overdueMemberIds.has(m.id)) lateCount++;
      else if (m.active_loans > 0) normal++;
      else none++;
    }
    return { normal, overdue: lateCount, none };
  }, [members, overdueMemberIds]);
  const activeMemberCount = members.filter((m) => m.is_active).length;

  // 회원 검색 — 아이디·이름 클라이언트 필터(회원 수가 적어 별도 API 없이 충분).
  const filteredMembers = useMemo(() => {
    const q = memberQuery.trim().toLowerCase();
    if (!q) return members;
    return members.filter(
      (m) =>
        m.username.toLowerCase().includes(q) ||
        (m.full_name ?? "").toLowerCase().includes(q),
    );
  }, [members, memberQuery]);

  // 헤더클릭 정렬 — 관리(작업) 칼럼은 comparator 를 등록하지 않아 자연히 정렬 불가.
  const {
    sorted: sortedMembers,
    sortKey,
    direction,
    toggle,
  } = useSortableTable<MemberRow>(filteredMembers, {
    username: (a, b) => a.username.localeCompare(b.username),
    full_name: (a, b) => (a.full_name ?? "").localeCompare(b.full_name ?? ""),
    is_active: (a, b) => Number(a.is_active) - Number(b.is_active),
    active_loans: (a, b) => a.active_loans - b.active_loans,
    total_loans: (a, b) => a.total_loans - b.total_loans,
  });

  const Th = ({ label, sortk }: { label: string; sortk?: string }) => (
    <th
      className={`pb-2 pr-3 ${sortk ? "cursor-pointer select-none hover:text-foreground" : ""}`}
      onClick={sortk ? () => toggle(sortk) : undefined}
    >
      {label}
      {sortk ? (
        <SortIcon active={sortKey === sortk} direction={direction} />
      ) : null}
    </th>
  );

  return (
    <AdminShell title="회원 관리 및 대여/반납">
      <div className="flex h-full flex-col gap-4">
        {/* 상단 stat 차트 */}
        <div className="grid shrink-0 grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <StackedStatusBar
            rows={[
              {
                label: "회원 상태",
                values: {
                  active: activeMemberCount,
                  inactive: members.length - activeMemberCount,
                },
              },
            ]}
            segments={[
              {
                key: "active",
                label: "활성",
                color: MEMBER_STATUS_COLOR.active,
              },
              {
                key: "inactive",
                label: "비활성",
                color: MEMBER_STATUS_COLOR.inactive,
              },
            ]}
            unit="명"
          />
          <StackedStatusBar
            rows={[{ label: "대출 상태", values: loanStatusCounts }]}
            segments={[
              {
                key: "normal",
                label: "정상대출",
                color: LOAN_STATUS_COLOR.normal,
              },
              {
                key: "overdue",
                label: "연체",
                color: LOAN_STATUS_COLOR.overdue,
              },
              { key: "none", label: "미대출", color: LOAN_STATUS_COLOR.none },
            ]}
            unit="명"
          />
          <div className="flex flex-col justify-center rounded-lg border bg-muted/30 px-4 py-2">
            <span className="text-xl font-semibold tabular-nums">
              {loans.length}
            </span>
            <span className="text-xs text-muted-foreground">누적 대출</span>
          </div>
        </div>

        {err ? (
          <p className="shrink-0 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
            {err}
          </p>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          {/* 회원 관리: 목록(정렬/수정/비활성화) */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border p-4">
            <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold">
                회원 관리{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  ({filteredMembers.length})
                </span>
              </h3>
              <div className="relative ml-auto w-full sm:w-56">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={memberQuery}
                  onChange={(e) => setMemberQuery(e.target.value)}
                  placeholder="아이디·이름 검색"
                  className="h-8 pl-9"
                />
              </div>
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus className="mr-1 size-3.5" /> 회원 추가
              </Button>
            </div>

            {loading ? (
              <p className="text-sm text-muted-foreground">불러오는 중...</p>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-background text-left text-xs text-muted-foreground">
                    <tr>
                      <Th label="아이디" sortk="username" />
                      <Th label="이름" sortk="full_name" />
                      <Th label="상태" sortk="is_active" />
                      <Th label="대출중" sortk="active_loans" />
                      <Th label="누적" sortk="total_loans" />
                      <th className="pb-2 pr-3">관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedMembers.map((m) => (
                      <tr
                        key={m.id}
                        onClick={() => setSelected(m.id)}
                        className={`cursor-pointer border-t transition ${
                          selected === m.id ? "bg-muted" : "hover:bg-muted/50"
                        }`}
                      >
                        <td className="py-2 pr-3 font-mono text-xs">
                          {m.username}
                        </td>
                        <td className="py-2 pr-3">{m.full_name ?? "-"}</td>
                        <td className="py-2 pr-3">
                          <button
                            type="button"
                            title={
                              m.is_active
                                ? "클릭하면 비활성화"
                                : "클릭하면 활성화"
                            }
                            onClick={(e) => {
                              e.stopPropagation();
                              void act(
                                () =>
                                  api(
                                    `/api/admin/circulation/members/${m.id}`,
                                    {
                                      method: "PATCH",
                                      body: JSON.stringify({
                                        is_active: !m.is_active,
                                      }),
                                    },
                                  ),
                                m.is_active
                                  ? `«${m.full_name ?? m.username}» 비활성화했습니다`
                                  : `«${m.full_name ?? m.username}» 활성화했습니다`,
                              );
                            }}
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold transition ${
                              m.is_active
                                ? "bg-emerald-500/15 text-emerald-700 hover:bg-emerald-500/25"
                                : "bg-muted text-muted-foreground hover:bg-muted/70"
                            }`}
                          >
                            {m.is_active ? "활성" : "비활성"}
                          </button>
                        </td>
                        <td className="py-2 pr-3 tabular-nums">
                          {m.active_loans}
                        </td>
                        <td className="py-2 pr-3 tabular-nums text-muted-foreground">
                          {m.total_loans}
                        </td>
                        <td className="py-2 pr-3">
                          <div className="flex gap-1">
                            <button
                              type="button"
                              aria-label={`${m.full_name ?? m.username} 정보 수정`}
                              onClick={(e) => {
                                e.stopPropagation();
                                openEdit(m);
                              }}
                              className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              aria-label={`${m.full_name ?? m.username} 삭제`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setDeleteTarget(m);
                              }}
                              className="rounded p-1 text-rose-700 transition hover:bg-rose-500/10"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {members.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="py-3 text-muted-foreground">
                          회원이 없습니다
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 선택 회원 상세 + 대여 처리 */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border p-4">
            {selected === null ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                왼쪽에서 회원을 선택하면 대출 이력과 대여 처리가 나옵니다.
              </p>
            ) : (
              <div className="flex h-full min-h-0 flex-col">
                <h3 className="mb-3 shrink-0 text-sm font-semibold">
                  {members.find((m) => m.id === selected)?.full_name ??
                    members.find((m) => m.id === selected)?.username}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    대출 이력
                  </span>
                </h3>

                {/* 대출이력 : 대여/반납처리 = 1 : 2 세로 비율 — 대여/반납 쪽 헤더·버튼이
                    더 넉넉히 보이게, 이력은 스크롤로 충분 */}
                <div className="mb-4 min-h-0 flex-1 overflow-y-auto">
                  {memberLoans.length === 0 ? (
                    <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
                      대출 이력이 없습니다
                    </p>
                  ) : (
                    <div className="divide-y rounded border">
                      <div className="grid grid-cols-[2fr_1fr_auto] gap-2 bg-muted/40 px-3 py-1.5 text-[10px] font-semibold text-muted-foreground">
                        <span>도서명</span>
                        <span>날짜</span>
                        <span>상태</span>
                      </div>
                      {memberLoans.map((l) => (
                        <div
                          key={l.id}
                          className="grid grid-cols-[2fr_1fr_auto] items-center gap-2 px-3 py-2 text-sm"
                        >
                          <span className="min-w-0 truncate">
                            {l.book_title}
                          </span>
                          <span className="text-xs tabular-nums text-muted-foreground">
                            {l.status === "returned"
                              ? fmt(l.returned_at ?? "")
                              : fmt(l.due_at)}
                          </span>
                          <span
                            className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                              l.status === "returned"
                                ? "bg-muted text-muted-foreground"
                                : l.overdue
                                  ? "bg-rose-500/15 text-rose-700"
                                  : "bg-amber-500/15 text-amber-700"
                            }`}
                          >
                            {l.status === "returned"
                              ? "반납완료"
                              : l.overdue
                                ? "연체"
                                : "대출중"}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 대여/반납 처리 — 대시보드 회원관리 카드와 같은 부품 재사용.
                    LoanQueuePanel 자체에 대여/반납 탭이 있어 별도 제목 없이도 명확하다
                    (제목을 빼야 대출이력 2 : 대여반납처리 1 비율 안에서 버튼까지 다 보인다). */}
                <div className="flex min-h-0 flex-1 flex-col overflow-y-auto border-t pt-3">
                  <LoanQueuePanel memberId={selected} onDone={load} />
                </div>
              </div>
            )}
          </section>
        </div>
      </div>

      {/* 회원 등록 다이얼로그 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>회원 추가</DialogTitle>
          </DialogHeader>
          <form onSubmit={createMember} className="space-y-3">
            <div>
              <Label htmlFor="new-username" className="text-xs">
                아이디
              </Label>
              <Input
                id="new-username"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="new-fullname" className="text-xs">
                이름 (선택)
              </Label>
              <Input
                id="new-fullname"
                value={newFullName}
                onChange={(e) => setNewFullName(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="new-password" className="text-xs">
                비밀번호
              </Label>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                className="mt-1"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
              >
                취소
              </Button>
              <Button type="submit" disabled={creating}>
                {creating ? "등록 중..." : "등록"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 회원 수정 다이얼로그 */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>회원 정보 수정</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="edit-fullname">이름</Label>
            <Input
              id="edit-fullname"
              value={editFullName}
              onChange={(e) => setEditFullName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="edit-password">비밀번호 재설정 (선택)</Label>
            <Input
              id="edit-password"
              type="password"
              placeholder="비워두면 그대로 유지"
              value={editPassword}
              onChange={(e) => setEditPassword(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setEditing(null)}
              disabled={saving}
            >
              취소
            </Button>
            <Button onClick={() => void saveEdit()} disabled={saving}>
              {saving ? "저장 중..." : "저장"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 회원 삭제 확인 */}
      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="회원 삭제"
        description={
          <>
            «{deleteTarget?.full_name ?? deleteTarget?.username}» 회원을
            삭제할까요? 대출/요청/예약 이력이 함께 영구 삭제되며 되돌릴 수
            없습니다. 처리 중인 대출/요청/예약이 있으면 실패합니다.
          </>
        }
        confirmLabel="삭제"
        onConfirm={() => void confirmDelete()}
        busy={deleting}
      />
    </AdminShell>
  );
}
