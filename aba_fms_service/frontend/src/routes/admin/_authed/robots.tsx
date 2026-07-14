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
  type Robot,
  type RobotType,
} from "@/lib/admin-api";

export const Route = createFileRoute("/admin/_authed/robots")({
  head: () => ({ meta: [{ title: "Labi Admin — 로봇 IP 관리" }] }),
  component: RobotsPage,
});

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "—";

const TYPE_LABEL: Record<RobotType, string> = {
  arm: "로봇팔",
  pinky: "핑키봇",
  other: "기타",
  server: "AI 서버",
};

interface FormState {
  name: string;
  robot_type: RobotType;
  ip_address: string;
  port: string;
  domain_id: string;
  ai_server_url: string;
  description: string;
  is_active: boolean;
}

const emptyForm: FormState = {
  name: "",
  robot_type: "arm",
  ip_address: "",
  port: "9001",
  domain_id: "",
  ai_server_url: "",
  description: "",
  is_active: true,
};

function RobotsPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Robot | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [deleteTarget, setDeleteTarget] = useState<Robot | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "robots", query],
    queryFn: () => adminApi.listRobots({ q: query || undefined, limit: 200 }),
  });

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["admin", "robots"] });
  }

  const createMut = useMutation({
    mutationFn: () =>
      adminApi.createRobot({
        name: form.name.trim(),
        robot_type: form.robot_type,
        ip_address: form.ip_address.trim(),
        port: Number(form.port) || 9001,
        domain_id: form.domain_id.trim() ? Number(form.domain_id) : null,
        ai_server_url: form.ai_server_url.trim() || null,
        description: form.description.trim() || null,
        is_active: form.is_active,
      }),
    onSuccess: () => {
      toast.success("로봇이 추가되었습니다.");
      setDialogOpen(false);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "추가에 실패했습니다."),
  });

  const updateMut = useMutation({
    mutationFn: () =>
      adminApi.updateRobot(editing!.id, {
        name: form.name.trim(),
        robot_type: form.robot_type,
        ip_address: form.ip_address.trim(),
        port: Number(form.port) || 9001,
        domain_id: form.domain_id.trim() ? Number(form.domain_id) : null,
        ai_server_url: form.ai_server_url.trim() || null,
        description: form.description.trim() || null,
        is_active: form.is_active,
      }),
    onSuccess: () => {
      toast.success("로봇 정보가 수정되었습니다.");
      setDialogOpen(false);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "수정에 실패했습니다."),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => adminApi.deleteRobot(id),
    onSuccess: () => {
      toast.success("로봇이 삭제되었습니다.");
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

  function openEdit(r: Robot) {
    setEditing(r);
    setForm({
      name: r.name,
      robot_type: r.robot_type,
      ip_address: r.ip_address,
      port: String(r.port),
      domain_id: r.domain_id != null ? String(r.domain_id) : "",
      ai_server_url: r.ai_server_url ?? "",
      description: r.description ?? "",
      is_active: r.is_active,
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
    <AdminShell title="로봇 IP 관리">
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
              placeholder="이름 · IP · 설명 검색"
              className="pl-9"
            />
          </form>
          <Button onClick={openCreate}>
            <Plus className="size-4" />
            로봇 추가
          </Button>
        </div>

        <div className="rounded-xl border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>이름</TableHead>
                <TableHead>종류</TableHead>
                <TableHead>IP 주소</TableHead>
                <TableHead>포트</TableHead>
                <TableHead>도메인 아이디</TableHead>
                <TableHead>AI 서버 URL</TableHead>
                <TableHead>설명</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>수정일</TableHead>
                <TableHead className="text-right">작업</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {error && (
                <TableRow>
                  <TableCell colSpan={10} className="text-center text-destructive">
                    {(error as Error).message}
                  </TableCell>
                </TableRow>
              )}
              {isLoading && (
                <TableRow>
                  <TableCell colSpan={10} className="text-center text-muted-foreground">
                    불러오는 중…
                  </TableCell>
                </TableRow>
              )}
              {!isLoading &&
                items.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{TYPE_LABEL[r.robot_type]}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">{r.ip_address}</TableCell>
                    <TableCell className="font-mono text-[13px]">{r.port}</TableCell>
                    <TableCell className="font-mono text-[13px]">{r.domain_id ?? "—"}</TableCell>
                    <TableCell className="font-mono text-[13px]">{r.ai_server_url ?? "—"}</TableCell>
                    <TableCell className="max-w-[220px] truncate text-muted-foreground">
                      {r.description ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={r.is_active ? "outline" : "destructive"}>
                        {r.is_active ? "활성" : "비활성"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fmtDate(r.updated_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" onClick={() => openEdit(r)}>
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setDeleteTarget(r)}
                        >
                          <Trash2 className="size-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              {!isLoading && items.length === 0 && !error && (
                <TableRow>
                  <TableCell colSpan={10} className="text-center text-muted-foreground">
                    등록된 로봇이 없습니다.
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
              <DialogTitle>{editing ? "로봇 수정" : "로봇 추가"}</DialogTitle>
              <DialogDescription>
                {editing
                  ? "변경할 항목을 수정하세요."
                  : "새 로봇의 접속 정보를 등록합니다."}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="f-name">이름</Label>
                <Input
                  id="f-name"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  required
                  minLength={2}
                  placeholder="예: JetCobot-1"
                />
              </div>
              <div className="space-y-2">
                <Label>종류</Label>
                <Select
                  value={form.robot_type}
                  onValueChange={(v) => setForm((f) => ({ ...f, robot_type: v as RobotType }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="arm">로봇팔</SelectItem>
                    <SelectItem value="pinky">핑키봇</SelectItem>
                    <SelectItem value="other">기타</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-[1fr_110px] gap-3">
                <div className="space-y-2">
                  <Label htmlFor="f-ip">IP 주소</Label>
                  <Input
                    id="f-ip"
                    value={form.ip_address}
                    onChange={(e) => setForm((f) => ({ ...f, ip_address: e.target.value }))}
                    required
                    placeholder="192.168.0.70"
                    className="font-mono"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="f-port">포트</Label>
                  <Input
                    id="f-port"
                    type="number"
                    value={form.port}
                    onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                    required
                    min={1}
                    max={65535}
                    className="font-mono"
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-domainid">도메인 아이디</Label>
                <Input
                  id="f-domainid"
                  type="number"
                  value={form.domain_id}
                  onChange={(e) => setForm((f) => ({ ...f, domain_id: e.target.value }))}
                  placeholder="로봇 접속 아이디 (숫자)"
                  className="font-mono"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-aiurl">AI 서버 URL</Label>
                <Input
                  id="f-aiurl"
                  value={form.ai_server_url}
                  onChange={(e) => setForm((f) => ({ ...f, ai_server_url: e.target.value }))}
                  placeholder="예: http://192.168.0.19:9002/"
                  className="font-mono"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="f-desc">설명</Label>
                <Input
                  id="f-desc"
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="선택 입력"
                />
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
            <AlertDialogTitle>로봇 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              <strong>{deleteTarget?.name}</strong> 로봇을 삭제할까요? 이 작업은 되돌릴 수 없습니다.
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
