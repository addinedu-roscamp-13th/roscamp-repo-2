import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Activity,
  Battery,
  Camera,
  Gauge,
  MessageCircle,
  MessagesSquare,
  UserCheck,
  Users,
  Zap,
} from "lucide-react";
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
import { adminApi, type RobotResult } from "@/lib/admin-api";

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
  const cameraQuery = useQuery({
    queryKey: ["dashboard", "camera"],
    queryFn: adminApi.cameraStatus,
    refetchInterval: 5000,
    retry: false,
  });
  const batteryQuery = useQuery({
    queryKey: ["dashboard", "battery"],
    queryFn: adminApi.getSensorBattery,
    refetchInterval: 15000,
    retry: false,
  });
  const ultrasonicQuery = useQuery({
    queryKey: ["dashboard", "ultrasonic"],
    queryFn: adminApi.getSensorUltrasonic,
    refetchInterval: 15000,
    retry: false,
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
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-lg font-semibold text-slate-900">운영 대시보드</h1>
                <p className="text-sm text-muted-foreground">관리자 활동, 대화 지표, PinkyPro 하드웨어 상태를 한 화면에서 확인합니다.</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">AI 모델</span>
                <Badge variant="secondary">{data?.selected_model ?? "—"}</Badge>
              </div>
            </div>
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


          <RobotStatusPanel
            camera={cameraQuery.data}
            battery={batteryQuery.data}
            ultrasonic={ultrasonicQuery.data}
            loading={cameraQuery.isLoading || batteryQuery.isLoading || ultrasonicQuery.isLoading}
          />

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

function parseRobotOutput<T>(result?: RobotResult): T | null {
  if (!result?.success || !result.output) return null;
  try {
    return JSON.parse(result.output) as T;
  } catch {
    return null;
  }
}

function RobotStatusPanel({
  camera,
  battery,
  ultrasonic,
  loading,
}: {
  camera?: { running: boolean; error: string | null; frame_id?: number; timestamp?: number | null };
  battery?: RobotResult;
  ultrasonic?: RobotResult;
  loading: boolean;
}) {
  const batteryData = parseRobotOutput<{ percentage?: number; voltage?: number }>(battery);
  const distanceData = parseRobotOutput<{ distance_cm?: number }>(ultrasonic);
  const items = [
    {
      label: "카메라",
      value: camera?.running ? "온라인" : "대기",
      detail: camera?.error ?? (camera?.frame_id != null ? `frame #${camera.frame_id}` : "snapshot"),
      icon: Camera,
      tone: camera?.running ? "text-emerald-600 bg-emerald-50" : "text-slate-500 bg-slate-50",
    },
    {
      label: "배터리",
      value: batteryData?.percentage != null ? `${batteryData.percentage.toFixed(1)}%` : "—",
      detail: batteryData?.voltage != null ? `${batteryData.voltage.toFixed(2)}V` : "측정 대기",
      icon: Battery,
      tone: (batteryData?.percentage ?? 100) < 25 ? "text-red-600 bg-red-50" : "text-emerald-600 bg-emerald-50",
    },
    {
      label: "초음파",
      value: distanceData?.distance_cm != null ? `${distanceData.distance_cm.toFixed(1)}cm` : "—",
      detail: "전방 거리",
      icon: Gauge,
      tone: (distanceData?.distance_cm ?? 999) < 20 ? "text-amber-700 bg-amber-50" : "text-sky-700 bg-sky-50",
    },
    {
      label: "제어 API",
      value: loading ? "확인 중" : "준비",
      detail: "LCD · LED · Motor · Sensor",
      icon: Zap,
      tone: "text-primary bg-primary/10",
    },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {items.map(({ label, value, detail, icon: Icon, tone }) => (
        <Card key={label}>
          <CardContent className="flex items-center gap-3 p-4">
            <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${tone}`}>
              <Icon className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">{label}</p>
              <p className="text-lg font-semibold tabular-nums text-slate-900">{value}</p>
              <p className="truncate text-xs text-muted-foreground">{detail}</p>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
