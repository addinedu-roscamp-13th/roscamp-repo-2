import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Loader2, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  adminApi,
  ApiError,
  type Admin,
  type AdminRole,
} from "@/lib/admin-api";

export const Route = createFileRoute("/admin/_authed/users")({
  head: () => ({ meta: [{ title: "Labi Admin — 관리자 목록" }] }),
  component: UsersPage,
});

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "—";

interface FormState {
  username: string;
  password: string;
  email: string;
  full_name: string;
  role: AdminRole;
  is_active: boolean;
}

const emptyForm: FormState = {
  username: "",
  password: "",
  email: "",
  full_name: "",
  role: "admin",
  is_active: true,
};

function UsersPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Admin | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [deleteTarget, setDeleteTarget] = useState<Admin | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "users", query],
    queryFn: () => adminApi.listUsers({ q: query || undefined, limit: 100 }),
  });

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    void qc.invalidateQueries({ queryKey: ["admin", "stats"] });
  }

  const createMut = useMutation({
    mutationFn: () =>
      adminApi.createUser({
        username: form.username.trim(),
        password: form.password,
        email: form.email.trim() || null,
        full_name: form.full_name.trim() || null,
        role: form.role,
        is_active: form.is_active,
      }),
    onSuccess: () => {
      toast.success("관리자가 추가되었습니다.");
      setDialogOpen(false);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "추가에 실패했습니다."),
  });

  const updateMut = useMutation({
    mutationFn: () =>
      adminApi.updateUser(editing!.id, {
        email: form.email.trim() || null,
        full_name: form.full_name.trim() || null,
        role: form.role,
        is_active: form.is_active,
        ...(form.password ? { password: form.password } : {}),
      }),
    onSuccess: () => {
      toast.success("관리자 정보가 수정되었습니다.");
      setDialogOpen(false);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "수정에 실패했습니다."),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => adminApi.deleteUser(id),
    onSuccess: () => {
      toast.success("관리자가 삭제되었습니다.");
      setDeleteTarget(null);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "삭제에 실패했습니다."),
  });

  function openCreate() {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  }

  function openEdit(a: Admin) {
    setEditing(a);
    setForm({
      username: a.username,
      password: "",
      email: a.email ?? "",
      full_name: a.full_name ?? "",
      role: a.role,
      is_active: a.is_active,
    });
    setDialogOpen(true);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (editing) updateMut.mutate();
    else createMut.mutate();
  }

  const saving = createMut.isPending || updateMut.isPending;
  const items = data?.items ?? [];

  return (
    <AdminShell title="관리자 목록">
      <div className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setQuery(search.trim());
            }}
            className="relative w-full sm:max-w-xs"
          >
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="아이디 · 이름 · 이메일 검색"
              className="pl-9"
            />
          </form>
          <Button onClick={openCreate}>
            <Plus className="size-4" />
            관리자 추가
          </Button>
        </div>

        <div className="rounded-xl border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>아이디</TableHead>
                <TableHead>이름</TableHead>
                <TableHead>이메일</TableHead>
                <TableHead>권한</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>마지막 로그인</TableHead>
                <TableHead className="text-right">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {error && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-destructive">
                    {(error as Error).message}
                  </TableCell>
                </TableRow>
              )}
              {isLoading && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    불러오는 중…
                  </TableCell>
                </TableRow>
              )}
              {!isLoading &&
                items.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-medium">{a.username}</TableCell>
                    <TableCell>{a.full_name ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{a.email ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant={a.role === "superadmin" ? "default" : "secondary"}>
                        {a.role}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={a.is_active ? "outline" : "destructive"}>
                        {a.is_active ? "활성" : "비활성"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fmtDate(a.last_login_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" onClick={() => openEdit(a)}>
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setDeleteTarget(a)}
                        >
                          <Trash2 className="size-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              {!isLoading && items.length === 0 && !error && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    관리자가 없습니다.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* Create / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <form onSubmit={submit}>
            <DialogHeader>
              <DialogTitle>{editing ? "관리자 수정" : "관리자 추가"}</DialogTitle>
              <DialogDescription>
                {editing
                  ? "변경할 항목을 수정하세요. 비밀번호는 입력 시에만 변경됩니다."
                  : "새 관리자 계정을 생성합니다."}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="f-username">아이디</Label>
                <Input
                  id="f-username"
                  value={form.username}
                  disabled={!!editing}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  required
                  minLength={3}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-password">
                  비밀번호 {editing && <span className="text-muted-foreground">(변경 시 입력)</span>}
                </Label>
                <Input
                  id="f-password"
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  required={!editing}
                  minLength={6}
                  placeholder={editing ? "••••••" : ""}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-fullname">이름</Label>
                <Input
                  id="f-fullname"
                  value={form.full_name}
                  onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-email">이메일</Label>
                <Input
                  id="f-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label>권한</Label>
                <Select
                  value={form.role}
                  onValueChange={(v) => setForm((f) => ({ ...f, role: v as AdminRole }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">admin</SelectItem>
                    <SelectItem value="superadmin">superadmin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                <Label htmlFor="f-active" className="cursor-pointer">
                  활성 상태
                </Label>
                <Switch
                  id="f-active"
                  checked={form.is_active}
                  onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                />
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
              >
                취소
              </Button>
              <Button type="submit" disabled={saving}>
                {saving && <Loader2 className="size-4 animate-spin" />}
                {editing ? "저장" : "추가"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>관리자 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              <strong>{deleteTarget?.username}</strong> 계정을 삭제할까요? 이 작업은 되돌릴 수 없습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                if (deleteTarget) deleteMut.mutate(deleteTarget.id);
              }}
              disabled={deleteMut.isPending}
            >
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AdminShell>
  );
}
