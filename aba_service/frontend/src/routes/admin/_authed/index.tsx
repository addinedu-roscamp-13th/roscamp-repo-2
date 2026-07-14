import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  MessageCircle,
  MessagesSquare,
  UserCheck,
  Users,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AdminShell } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { adminApi, type DayCount } from "@/lib/admin-api";

export const Route = createFileRoute("/admin/_authed/")({
  head: () => ({ meta: [{ title: "Labi Admin — 대시보드" }] }),
  component: DashboardPage,
});

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "—";

function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: adminApi.getStats,
  });

  const metrics = [
    { label: "전체 관리자", value: data?.total_admins, icon: Users },
    { label: "활성 관리자", value: data?.active_admins, icon: UserCheck },
    { label: "대화 수", value: data?.total_conversations, icon: MessagesSquare },
    { label: "메시지 수", value: data?.total_messages, icon: MessageCircle },
  ];

  return (
    <AdminShell title="대시보드">
      {error ? (
        <p className="text-sm text-destructive">
          통계를 불러오지 못했습니다: {(error as Error).message}
        </p>
      ) : (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">선택된 AI 모델</span>
            <Badge variant="secondary">{data?.selected_model ?? "—"}</Badge>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {metrics.map((m) => {
              const Icon = m.icon;
              return (
                <Card key={m.label}>
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-sm font-medium text-muted-foreground">
                      {m.label}
                    </CardTitle>
                    <Icon className="size-4 text-muted-foreground" />
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold tabular-nums">
                      {isLoading ? "—" : (m.value ?? 0).toLocaleString("ko-KR")}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">최근 14일 대화 추이</CardTitle>
            </CardHeader>
            <CardContent>
              <ConversationsChart data={data?.conversations_per_day ?? []} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">최근 등록된 관리자</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>아이디</TableHead>
                    <TableHead>이름</TableHead>
                    <TableHead>권한</TableHead>
                    <TableHead>등록일</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data?.recent_admins ?? []).map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="font-medium">{a.username}</TableCell>
                      <TableCell>{a.full_name ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant={a.role === "superadmin" ? "default" : "secondary"}>
                          {a.role}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {fmtDate(a.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                  {!isLoading && (data?.recent_admins?.length ?? 0) === 0 && (
                    <TableRow>
                      <TableCell colSpan={4} className="text-center text-muted-foreground">
                        데이터가 없습니다.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </AdminShell>
  );
}

function ConversationsChart({ data }: { data: DayCount[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <defs>
          <linearGradient id="convFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--color-primary)" stopOpacity={0.35} />
            <stop offset="95%" stopColor="var(--color-primary)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(v: string) => v.slice(5)}
          tick={{ fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={32} />
        <Tooltip
          contentStyle={{
            background: "var(--color-card)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelFormatter={(v) => `날짜 ${v}`}
          formatter={(value) => [`${value}건`, "대화"]}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="var(--color-primary)"
          strokeWidth={2}
          fill="url(#convFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
