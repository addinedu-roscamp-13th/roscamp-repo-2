import { createFileRoute, Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  Check,
  CheckCircle2,
  Circle,
  Library,
  ListChecks,
  Radar,
  ShieldAlert,
  WifiOff,
  X,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentType,
} from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  CATEGORY_LABEL,
  ops,
  opsApi,
  type ApprovalRow,
  type Dashboard,
} from "@/lib/ops-api";

/** 분야별 대출 색 — 카테고리는 정체성(identity)이라 고정 순서 팔레트를 그대로 매핑한다. */
const CATEGORY_CHART_COLOR: Record<string, string> = {
  literature: "var(--chart-1)",
  art: "var(--chart-2)",
  science: "var(--chart-3)",
  humanities: "var(--chart-4)",
  kids: "var(--chart-5)",
};

/** 도서 재고 상태 — books.tsx 서가 현황 차트와 같은 색 언어(3상태 고정). */
const BOOK_STATUS_COLOR = {
  available: "#10b981", // emerald-500
  borrowed: "#f59e0b", // amber-500
  unavailable: "#f43f5e", // rose-500
} as const;

/** 로봇 상태 — 도서 현황 도넛과 같은 색 언어(가용=좋음/작업중=사용중/에러=나쁨), 충전중만 새 파랑. */
const FLEET_STATE_COLOR = {
  available: BOOK_STATUS_COLOR.available,
  working: BOOK_STATUS_COLOR.borrowed,
  charging: "#0ea5e9", // sky-500
  error: BOOK_STATUS_COLOR.unavailable,
} as const;

type StatusStyle = {
  color: string;
  icon: ComponentType<{ className?: string; style?: React.CSSProperties }>;
  label: string;
};

/** 작업 상태 — 완료/실패/취소는 진짜 좋고나쁨의 의미가 있어 상태색을 쓴다(카테고리색과 섞지 않음). */
const TASK_STATUS_STYLE: Record<string, StatusStyle> = {
  COMPLETED: {
    color: "var(--chart-status-good)",
    icon: CheckCircle2,
    label: "완료",
  },
  FAILED: {
    color: "var(--chart-status-critical)",
    icon: XCircle,
    label: "실패",
  },
  CANCELLED: {
    color: "var(--chart-status-warning)",
    icon: AlertTriangle,
    label: "취소",
  },
};

export const Route = createFileRoute("/admin/_authed/")({
  head: () => ({ meta: [{ title: "LiBi Admin — 대시보드" }] }),
  component: DashboardPage,
});

/**
 * 카드 대시보드 — 도서관·회원·로봇·작업 현황을 한눈에, 카드를 누르면 해당 섹션으로.
 *
 * 예전엔 이 자리가 AI 챗봇 통계였고, 진짜 운영 현황(`/admin/ops`)과 통합 검색·통계
 * (`/admin/insight`)가 각각 딴 화면이었다. 셋을 여기 하나로 합친다 — 데이터 소스는
 * 그대로(`ops.dashboard()`/`ops.stats()`) 재사용.
 */

type Stats = Awaited<ReturnType<typeof ops.stats>>;
interface IntrusionRow {
  id: number;
  source: string;
  zone: string | null;
  note: string | null;
  at: string;
  clip_path: string | null;
}
interface AlertsMini {
  intrusions: IntrusionRow[];
}
interface LogRow {
  id: number;
  task_id: string;
  kind: string;
  robot: string | null;
  status: string;
  recorded_at: string;
}

/** 알림용 스냅샷 — 이전 폴링과 비교해 "늘어난 만큼"만 이벤트로 토스트한다. */
interface AlertSnapshot {
  overdue: number;
  reservationsReady: number;
  tasksFailed: number;
  tasksCompleted: number;
  tasksPending: number;
  pendingApprovals: number;
  unackedIntrusions: number;
  fleetLinked: boolean;
}

function DashboardPage() {
  const [d, setD] = useState<Dashboard | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [intrusions, setIntrusions] = useState<IntrusionRow[]>([]);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [approvalsPending, setApprovalsPending] = useState<ApprovalRow[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const prevAlertRef = useRef<AlertSnapshot | null>(null);

  const load = useCallback(async () => {
    try {
      const [dash, st, alerts, appr, taskLogs] = await Promise.all([
        ops.dashboard(),
        ops.stats(),
        opsApi<AlertsMini>("/api/admin/ops/alerts"),
        ops.approvals(),
        opsApi<LogRow[]>("/api/admin/ops/logs?limit=20"),
      ]);
      setD(dash);
      setStats(st);
      setIntrusions(alerts.intrusions);
      setApprovalsPending(appr.pending);
      setPendingApprovals(appr.pending.length);
      setLogs(taskLogs);
      setErr(null); // 이전 폴링에서 실패했더라도 이번에 성공했으면 에러 배너를 치운다

      // 팝업 알림 — 조건이 계속 참인 동안 매번 다시 띄우지 않고, "늘어난 시점"에만 한 번.
      // X 를 눌러 직접 닫을 때까지 화면에 남는다(duration: Infinity).
      const prev = prevAlertRef.current;
      if (prev) {
        if (dash.library.overdue > prev.overdue) {
          toast.error(
            `연체 ${dash.library.overdue}건 — 회원에게 반납 안내가 필요합니다`,
            { duration: Infinity },
          );
        }
        if (dash.library.reservations_ready > prev.reservationsReady) {
          toast.success(
            `예약 준비 완료 ${dash.library.reservations_ready}건 — 회원에게 알려주세요`,
            { duration: Infinity },
          );
        }
        if (dash.tasks.failed > prev.tasksFailed) {
          toast.error(`실패한 작업 ${dash.tasks.failed}건 발생`, {
            duration: Infinity,
          });
        }
        if (dash.tasks.completed > prev.tasksCompleted) {
          toast.success(
            `작업 ${dash.tasks.completed - prev.tasksCompleted}건 완료됐습니다`,
            { duration: Infinity },
          );
        }
        if (dash.tasks.pending > prev.tasksPending) {
          toast.info(
            `새 작업 ${dash.tasks.pending - prev.tasksPending}건 접수됐습니다`,
            { duration: Infinity },
          );
        }
        if (appr.pending.length > prev.pendingApprovals) {
          toast.info(
            `새 대여 승인 요청 ${appr.pending.length - prev.pendingApprovals}건`,
            { duration: Infinity },
          );
        }
        if (alerts.intrusions.length > prev.unackedIntrusions) {
          toast.error(
            `침입 감지 ${alerts.intrusions.length - prev.unackedIntrusions}건!`,
            { duration: Infinity },
          );
        }
        if (prev.fleetLinked && !dash.fleet.linked) {
          toast.warning(
            "FMS 연결이 끊겼습니다 — 로봇 정보를 읽지 못합니다(도서·회원 기능은 정상)",
            { duration: Infinity },
          );
        }
      }
      prevAlertRef.current = {
        overdue: dash.library.overdue,
        reservationsReady: dash.library.reservations_ready,
        tasksFailed: dash.tasks.failed,
        tasksCompleted: dash.tasks.completed,
        tasksPending: dash.tasks.pending,
        pendingApprovals: appr.pending.length,
        unackedIntrusions: alerts.intrusions.length,
        fleetLinked: dash.fleet.linked,
      };
    } catch (e) {
      setErr(e instanceof Error ? e.message : "불러오기 실패");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 4000); // 로봇/작업이 바뀌므로 주기 갱신
    return () => clearInterval(t);
  }, [load]);

  return (
    <AdminShell title="대시보드">
      {err ? (
        <p className="mb-4 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
          {err}
        </p>
      ) : null}
      {!d ? (
        err ? null : (
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        )
      ) : (
        <div className="flex h-full flex-col gap-6">
          {/* 2x2(정자 井) — 카드 4개가 화면을 꽉 채우도록 행도 균등하게 늘린다.
              연체/예약준비/작업실패/침입 등은 더 이상 상단에 쌓이는 배너가 아니라
              toast 팝업(X로 닫기)으로 뜬다 — load() 안의 "팝업 알림" 참고. */}
          <div className="grid min-h-0 flex-1 auto-rows-fr gap-4 sm:grid-cols-2">
            <DashCard
              to="/admin/books"
              icon={Library}
              title="도서관리"
              tone="indigo"
            >
              <div className="grid h-full grid-cols-2 gap-3">
                <div className="flex h-full min-h-0 flex-col gap-2">
                  {stats ? (
                    <MiniDonut
                      title="도서 분포 (책 종류)"
                      data={stats.books_by_category.map((r) => ({
                        label: CATEGORY_LABEL[r.category] ?? r.category,
                        value: r.count,
                        color:
                          CATEGORY_CHART_COLOR[r.category] ??
                          "var(--chart-neutral)",
                      }))}
                    />
                  ) : null}
                  <MiniDonut
                    title="도서 현황 (대출)"
                    data={[
                      {
                        label: "대출가능",
                        value: d.library.available_books,
                        color: BOOK_STATUS_COLOR.available,
                      },
                      {
                        label: "대출중",
                        value:
                          d.library.books -
                          d.library.available_books -
                          d.library.unavailable_books,
                        color: BOOK_STATUS_COLOR.borrowed,
                      },
                      {
                        label: "대출불가능",
                        value: d.library.unavailable_books,
                        color: BOOK_STATUS_COLOR.unavailable,
                      },
                    ]}
                  />
                </div>
                <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
                  <p className="mb-1 shrink-0 text-sm font-semibold text-muted-foreground">
                    인기 도서 TOP
                  </p>
                  {stats && stats.top_books.length > 0 ? (
                    <div className="flex min-h-0 flex-1 flex-col justify-center">
                      <ol className="grid grid-cols-[1.25rem_1fr_auto] gap-x-1.5 gap-y-0.5 text-xs">
                        {stats.top_books.slice(0, 15).map((b, i) => (
                          <li key={b.title} className="contents">
                            <span className="text-muted-foreground tabular-nums">
                              {i + 1}
                            </span>
                            <span className="truncate">{b.title}</span>
                            <span className="shrink-0 text-right tabular-nums text-muted-foreground">
                              {b.count}
                            </span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">데이터 없음</p>
                  )}
                </div>
              </div>
            </DashCard>

            <DashCard
              to="/admin/members"
              icon={BookMarked}
              title="회원관리"
              tone="emerald"
            >
              <div className="grid h-full grid-cols-2 gap-3">
                {/* 1. 대여/반납 숏컷 — 왼쪽 전체 높이, 팝업 없이 카드 안에서 바로 처리 */}
                <MemberQuickPanel onDone={load} />
                <div className="flex h-full min-h-0 flex-col gap-3">
                  {/* 2. 반납 임박(1일 이내) 리스트 — 오른쪽 위 */}
                  <div className="flex min-h-0 flex-1 flex-col rounded-lg border p-2">
                    <p className="mb-1 shrink-0 text-sm font-semibold whitespace-nowrap text-muted-foreground">
                      반납 임박 (1일 이내)
                    </p>
                    {stats && stats.due_tomorrow.length > 0 ? (
                      <ol className="grid min-h-0 flex-1 grid-cols-[1fr_auto] content-start gap-x-1.5 gap-y-0.5 overflow-y-auto text-xs">
                        {stats.due_tomorrow.map((r, i) => (
                          <li
                            key={`${r.member_name}-${r.book_title}-${i}`}
                            className="contents"
                          >
                            <span className="truncate">
                              {r.book_title}
                              <span className="text-muted-foreground">
                                {" "}
                                · {r.member_name}
                              </span>
                            </span>
                            <span className="shrink-0 tabular-nums text-muted-foreground">
                              {r.due_at.slice(5, 10)}
                            </span>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        반납 임박 대출이 없습니다
                      </p>
                    )}
                  </div>
                  {/* 3. 도서 상태 비율(대출/연체/진열) — 오른쪽 아래 */}
                  <MiniDonut
                    title="도서 상태 비율"
                    data={[
                      {
                        label: "진열(대출가능)",
                        value: d.library.available_books,
                        color: BOOK_STATUS_COLOR.available,
                      },
                      {
                        label: "정상 대출중",
                        value:
                          d.library.books -
                          d.library.available_books -
                          d.library.unavailable_books -
                          d.library.overdue,
                        color: BOOK_STATUS_COLOR.borrowed,
                      },
                      {
                        label: "연체",
                        value: d.library.overdue,
                        color: BOOK_STATUS_COLOR.unavailable,
                      },
                    ]}
                  />
                </div>
              </div>
            </DashCard>

            <DashCard
              to="/admin/robots"
              icon={Radar}
              title="실시간 모니터링"
              tone="sky"
            >
              <div className="grid h-full grid-cols-2 gap-3">
                {/* 1. 로봇 상태 — 왼쪽 전체 높이 */}
                <div className="flex h-full min-h-0 flex-col gap-1.5">
                  <MiniDonut
                    title="로봇 상태"
                    data={[
                      {
                        label: "가용중",
                        value: d.fleet.available,
                        color: FLEET_STATE_COLOR.available,
                      },
                      {
                        label: "작업중",
                        value: d.fleet.working,
                        color: FLEET_STATE_COLOR.working,
                      },
                      {
                        label: "충전중",
                        value: d.fleet.charging,
                        color: FLEET_STATE_COLOR.charging,
                      },
                      {
                        label: "에러",
                        value: d.fleet.error,
                        color: FLEET_STATE_COLOR.error,
                      },
                    ]}
                  />
                  {d.fleet.stale > 0 ? (
                    <p className="flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-semibold text-rose-700">
                      <WifiOff className="h-3.5 w-3.5" />
                      연결 끊김 {d.fleet.stale}대
                    </p>
                  ) : null}
                </div>
                <div className="flex h-full min-h-0 flex-col gap-3">
                  {/* 2. 최신 20개 작업이력 — 오른쪽 위, 스크롤 가능 */}
                  <div className="flex min-h-0 flex-1 flex-col rounded-lg border p-2">
                    <p className="mb-1 shrink-0 text-sm font-semibold whitespace-nowrap text-muted-foreground">
                      작업이력 (최신 20개)
                    </p>
                    {logs.length > 0 ? (
                      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto text-xs">
                        {logs.map((l) => {
                          const style = TASK_STATUS_STYLE[l.status] ?? {
                            color: "var(--chart-neutral)",
                            icon: Circle,
                            label: l.status,
                          };
                          const Icon = style.icon;
                          return (
                            <li
                              key={l.id}
                              className="flex items-center gap-1.5"
                            >
                              <Icon
                                className="h-3 w-3 shrink-0"
                                style={{ color: style.color }}
                              />
                              <span className="min-w-0 flex-1 truncate">
                                {l.kind}
                                {l.robot ? (
                                  <span className="text-muted-foreground">
                                    {" "}
                                    · {l.robot}
                                  </span>
                                ) : null}
                              </span>
                              <span className="shrink-0 tabular-nums text-muted-foreground">
                                {l.recorded_at.slice(11, 16)}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        작업이력이 없습니다
                      </p>
                    )}
                  </div>
                  {/* 3. 침입감지 이력 — 오른쪽 아래, 스크롤 가능 */}
                  <div className="flex min-h-0 flex-1 flex-col rounded-lg border p-2">
                    <p className="mb-1 flex shrink-0 items-center gap-1.5 whitespace-nowrap text-sm font-semibold text-muted-foreground">
                      <ShieldAlert className="h-3.5 w-3.5" />
                      침입감지 이력
                    </p>
                    {intrusions.length > 0 ? (
                      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto text-xs">
                        {intrusions.map((e) => (
                          <li key={e.id} className="flex items-center gap-1.5">
                            <span className="min-w-0 flex-1 truncate text-rose-700">
                              {e.zone ?? e.source}
                              {e.note ? (
                                <span className="text-muted-foreground">
                                  {" "}
                                  · {e.note}
                                </span>
                              ) : null}
                            </span>
                            <span className="shrink-0 tabular-nums text-muted-foreground">
                              {e.at.slice(11, 16)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        미확인 침입이 없습니다
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </DashCard>

            <DashCard
              to="/admin/tasks"
              icon={ListChecks}
              title="운영"
              tone="amber"
            >
              <div className="grid h-full grid-cols-2 gap-3">
                {/* 1. 대여 요청 승인 숏컷 — 왼쪽 전체 높이 */}
                <ApprovalShortcut
                  pending={approvalsPending}
                  count={pendingApprovals}
                  onDone={load}
                />
                {/* 2. 최근 7일 작업 성공/실패 — 오른쪽 전체 높이 */}
                <WeeklyTaskBars data={stats?.tasks_last_7_days ?? []} />
              </div>
            </DashCard>
          </div>
        </div>
      )}
    </AdminShell>
  );
}

const CARD_TONE: Record<string, string> = {
  indigo: "bg-indigo-500/10 text-indigo-600",
  emerald: "bg-emerald-500/10 text-emerald-600",
  sky: "bg-sky-500/10 text-sky-600",
  amber: "bg-amber-500/10 text-amber-600",
};

function DashCard({
  to,
  icon: Icon,
  title,
  tone,
  children,
}: {
  to: string;
  icon: ComponentType<{ className?: string }>;
  title: string;
  tone: keyof typeof CARD_TONE;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden">
      <CardHeader className="flex shrink-0 flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <span
            className={`flex h-8 w-8 items-center justify-center rounded-lg ${CARD_TONE[tone]}`}
          >
            <Icon className="h-4 w-4" />
          </span>
          {title}
        </CardTitle>
        <Link
          to={to}
          aria-label={`${title} 상세로 이동`}
          className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-primary"
        >
          <ArrowRight className="h-4 w-4" />
        </Link>
      </CardHeader>
      {/* 화면 전체는 절대 스크롤 안 생기게 하고, 정 안 맞으면 이 카드 안에서만 스크롤 */}
      <CardContent className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 pt-0">
        {children}
      </CardContent>
    </Card>
  );
}

/**
 * 전체 중 비율(part-of-whole) 강조용 도넛 — 조각이 몇 개 안 될 때만 쓴다.
 * 차트 박스를 정사각형으로 고정해 원이 항상 박스를 꽉 채우게 한다 — 이전엔 박스가
 * 가로로 긴 비율(2:1, 6:1)이라 원 좌우로 빈 여백이 크게 남았었다(실제 버그는
 * "도넛이라서"가 아니라 "원을 담는 박스가 원 모양이 아니라서"였다).
 * 각도만으로는 못 읽으니 옆에 값 목록을 항상 같이 보여준다(범례 겸 직접 라벨).
 */
function MiniDonut({
  title,
  data,
}: {
  title: string;
  data: { label: string; value: number; color: string }[];
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col rounded-lg border p-2">
      <p className="mb-1 shrink-0 text-sm font-semibold whitespace-nowrap text-muted-foreground">
        {title}
      </p>
      {total === 0 ? (
        <p className="text-xs text-muted-foreground">데이터 없음</p>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-12">
          <ul className="grid shrink-0 grid-cols-[auto_auto_auto] items-center gap-x-2 gap-y-0.5 text-xs">
            {data.map((d) => (
              <li key={d.label} className="contents">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: d.color }}
                />
                <span className="text-muted-foreground">{d.label}</span>
                <span className="text-right font-semibold tabular-nums">
                  {d.value}
                </span>
              </li>
            ))}
          </ul>
          <div className="aspect-square h-full min-w-0 max-w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="label"
                  innerRadius="55%"
                  outerRadius="100%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {data.map((d) => (
                    <Cell key={d.label} fill={d.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number, name: string) => [
                    `${value}건`,
                    name,
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

interface QuickMember {
  id: number;
  username: string;
  full_name: string | null;
}

interface QuickBook {
  id: number;
  title: string;
  author: string;
  zone: string;
  in_stock: boolean;
  unavailable: boolean;
}

interface QuickLoan {
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

const SLOT_COUNT = 3;

/**
 * 대여/반납 숏컷 — 팝업 없이 회원관리 카드 왼쪽 안에서 진행하되(대여/반납 탭 토글),
 * 도서 고르기만 주소검색 팝업처럼 별도 창으로 뺀다: 슬롯 3칸 중 하나를 누르면
 * `BookPickerDialog` 가 뜨고, 거기서 "선택"을 누르면 슬롯에 담기고 창은 닫힌다.
 * 슬롯을 다 채운 뒤 "확정" 한 번으로 담긴 것 전부를 처리한다(DB 반영은 확정 시점에만).
 */
function MemberQuickPanel({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"borrow" | "return">("borrow");
  const [members, setMembers] = useState<QuickMember[]>([]);
  const [memberId, setMemberId] = useState<number | null>(null);
  const [borrowSlots, setBorrowSlots] = useState<(QuickBook | null)[]>(
    Array(SLOT_COUNT).fill(null),
  );
  const [returnSlots, setReturnSlots] = useState<(QuickLoan | null)[]>(
    Array(SLOT_COUNT).fill(null),
  );
  const [pickerSlot, setPickerSlot] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void opsApi<QuickMember[]>("/api/admin/circulation/members")
      .then(setMembers)
      .catch(() => setMembers([]));
  }, []);

  const reset = () => {
    setMemberId(null);
    setBorrowSlots(Array(SLOT_COUNT).fill(null));
    setReturnSlots(Array(SLOT_COUNT).fill(null));
  };

  const confirm = async () => {
    setBusy(true);
    try {
      if (mode === "borrow") {
        const picked = borrowSlots.filter((b): b is QuickBook => b !== null);
        if (memberId === null) {
          toast.error("회원을 선택하세요");
          return;
        }
        if (picked.length === 0) {
          toast.error("도서를 하나 이상 선택하세요");
          return;
        }
        for (const b of picked) {
          await opsApi("/api/admin/circulation/borrow", {
            method: "POST",
            body: JSON.stringify({ member_id: memberId, book_id: b.id }),
          });
        }
        toast.success(`${picked.length}건 대출 처리했습니다 (14일)`);
      } else {
        const picked = returnSlots.filter((l): l is QuickLoan => l !== null);
        if (picked.length === 0) {
          toast.error("반납할 도서를 하나 이상 선택하세요");
          return;
        }
        for (const l of picked) {
          await opsApi(`/api/admin/circulation/loans/${l.id}/return`, {
            method: "POST",
          });
        }
        toast.success(`${picked.length}건 반납 처리했습니다`);
      }
      reset();
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "처리에 실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  const excludeIds = (mode === "borrow" ? borrowSlots : returnSlots)
    .filter((s): s is QuickBook | QuickLoan => s !== null)
    .map((s) => s.id);

  const pick = (item: QuickBook | QuickLoan) => {
    if (pickerSlot === null) return;
    const i = pickerSlot;
    if (mode === "borrow") {
      setBorrowSlots((prev) =>
        prev.map((s, idx) => (idx === i ? (item as QuickBook) : s)),
      );
    } else {
      setReturnSlots((prev) =>
        prev.map((s, idx) => (idx === i ? (item as QuickLoan) : s)),
      );
    }
    setPickerSlot(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-1.5">
      <div className="flex shrink-0 gap-1 text-xs font-semibold">
        <button
          type="button"
          onClick={() => setMode("borrow")}
          className={`flex-1 rounded px-2 py-1 transition ${mode === "borrow" ? PANEL_TAB.active : PANEL_TAB.inactive}`}
        >
          대여
        </button>
        <button
          type="button"
          onClick={() => setMode("return")}
          className={`flex-1 rounded px-2 py-1 transition ${mode === "return" ? PANEL_TAB.active : PANEL_TAB.inactive}`}
        >
          반납
        </button>
      </div>

      <select
        value={memberId ?? ""}
        onChange={(e) => {
          setMemberId(e.target.value ? Number(e.target.value) : null);
          // 회원이 바뀌면 이전 회원 기준으로 담아둔 슬롯은 의미가 없어진다.
          setBorrowSlots(Array(SLOT_COUNT).fill(null));
          setReturnSlots(Array(SLOT_COUNT).fill(null));
        }}
        className="h-7 shrink-0 rounded border bg-transparent px-1.5 text-xs outline-none focus:ring-1 focus:ring-primary"
      >
        <option value="">회원 선택</option>
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.full_name ?? m.username} ({m.username})
          </option>
        ))}
      </select>

      <div className="grid min-h-0 flex-1 grid-cols-3 gap-1.5">
        {mode === "borrow"
          ? borrowSlots.map((item, i) => (
              <SlotButton
                key={i}
                label={item?.title}
                sub={
                  item
                    ? item.unavailable
                      ? "(사용불가)"
                      : !item.in_stock
                        ? "(대출중)"
                        : undefined
                    : undefined
                }
                onClick={() => setPickerSlot(i)}
              />
            ))
          : returnSlots.map((item, i) => (
              <SlotButton
                key={i}
                label={item?.book_title}
                sub={item ? `· ${item.member_name}` : undefined}
                onClick={() => setPickerSlot(i)}
              />
            ))}
      </div>

      <div className="flex shrink-0 gap-1.5 text-xs font-semibold">
        <button
          type="button"
          disabled={busy}
          onClick={reset}
          className="flex-1 rounded border py-1 disabled:opacity-40"
        >
          초기화
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void confirm()}
          className="flex-1 rounded bg-primary py-1 text-primary-foreground disabled:opacity-40"
        >
          확정
        </button>
      </div>

      <BookPickerDialog
        open={pickerSlot !== null}
        mode={mode}
        memberId={memberId}
        excludeIds={excludeIds}
        onClose={() => setPickerSlot(null)}
        onPick={pick}
      />
    </div>
  );
}

function SlotButton({
  label,
  sub,
  onClick,
}: {
  label?: string;
  sub?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-full flex-col items-center justify-center gap-0.5 rounded border p-1 text-center transition hover:bg-muted/50"
    >
      {label ? (
        <>
          <span className="line-clamp-2 text-[10px] font-semibold">
            {label}
          </span>
          {sub ? (
            <span className="text-[9px] text-muted-foreground">{sub}</span>
          ) : null}
        </>
      ) : (
        <span className="text-[10px] text-muted-foreground">+ 책 선택</span>
      )}
    </button>
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
  const [books, setBooks] = useState<QuickBook[]>([]);
  const [loans, setLoans] = useState<QuickLoan[]>([]);

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  useEffect(() => {
    if (!open || mode !== "borrow") return;
    const t = setTimeout(() => {
      void opsApi<QuickBook[]>(
        `/api/admin/circulation/available-books?include_unavailable=true&q=${encodeURIComponent(query)}`,
      )
        .then(setBooks)
        .catch(() => setBooks([]));
    }, 250);
    return () => clearTimeout(t);
  }, [open, mode, query]);

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

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {mode === "borrow" ? "도서 선택" : "반납 도서 선택"}
          </DialogTitle>
        </DialogHeader>
        {mode === "borrow" ? (
          <>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="도서 제목 검색"
              className="h-9 w-full rounded-md border px-3 text-sm outline-none focus:ring-2 focus:ring-primary"
            />
            <div className="max-h-72 space-y-1 overflow-y-auto">
              <div className="grid grid-cols-[1fr_auto_auto_auto] gap-2 px-2 text-xs font-semibold text-muted-foreground">
                <span>제목</span>
                <span>저자</span>
                <span>상태</span>
                <span />
              </div>
              {books.map((b) => {
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
                    className={`grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 rounded border px-2 py-1.5 text-sm ${disabled ? "opacity-50" : ""}`}
                  >
                    <span className="min-w-0 truncate">{b.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {b.author}
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
          </>
        ) : memberId === null ? (
          <p className="p-3 text-center text-xs text-muted-foreground">
            회원을 먼저 선택하세요
          </p>
        ) : (
          <div className="max-h-80 space-y-1 overflow-y-auto">
            <div className="grid grid-cols-[1fr_auto_auto_auto] gap-2 px-2 text-xs font-semibold text-muted-foreground">
              <span>제목</span>
              <span>회원</span>
              <span>상태</span>
              <span />
            </div>
            {loans.map((l) => {
              const already = excludeIds.includes(l.id);
              return (
                <div
                  key={l.id}
                  className={`grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 rounded border px-2 py-1.5 text-sm ${already ? "opacity-50" : ""}`}
                >
                  <span className="min-w-0 truncate">{l.book_title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
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
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * 대여 승인 숏컷 — 대여 신청 대기 목록을 카드 안에서 바로 승인/반려한다(approvals.tsx 로직 재사용).
 */
function ApprovalShortcut({
  pending,
  count,
  onDone,
}: {
  pending: ApprovalRow[];
  count: number;
  onDone: () => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const act = async (row: ApprovalRow, kind: "approve" | "reject") => {
    setBusyId(row.id);
    try {
      if (kind === "approve") {
        const res = await ops.approve(row.id);
        toast.success(`«${res.book_title}» 승인했습니다`);
      } else {
        const res = await ops.reject(row.id, "");
        toast.success(`«${res.book_title}» 반려했습니다`);
      }
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "처리에 실패했습니다");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <p className="mb-1 shrink-0 whitespace-nowrap text-sm font-semibold text-muted-foreground">
        대여 승인 대기 {count > 0 ? `(${count})` : null}
      </p>
      {pending.length > 0 ? (
        <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto text-xs">
          {pending.map((a) => (
            <li
              key={a.id}
              className="flex items-center gap-1.5 rounded border px-1.5 py-1"
            >
              <span className="min-w-0 flex-1 truncate">
                {a.book_title}
                <span className="text-muted-foreground">
                  {" "}
                  · {a.member_name ?? a.member_username}
                </span>
              </span>
              {!a.book_in_stock ? (
                <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[9px] font-bold text-amber-700">
                  재고없음
                </span>
              ) : null}
              <button
                type="button"
                disabled={busyId === a.id}
                onClick={() => void act(a, "approve")}
                aria-label="승인"
                className="shrink-0 rounded bg-emerald-500/15 p-0.5 text-emerald-700 transition hover:bg-emerald-500/25 disabled:opacity-40"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                disabled={busyId === a.id}
                onClick={() => void act(a, "reject")}
                aria-label="반려"
                className="shrink-0 rounded bg-rose-500/15 p-0.5 text-rose-700 transition hover:bg-rose-500/25 disabled:opacity-40"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">
          승인 대기 요청이 없습니다
        </p>
      )}
    </div>
  );
}

/**
 * 최근 7일 작업 성공/실패 — 하루 하나의 막대에 완료(good)/실패(critical) 두 상태를
 * 쌓아 올린다. 2개 시리즈라 범례는 항상 붙인다(색만으로 구분하게 두지 않음).
 */
function WeeklyTaskBars({
  data,
}: {
  data: { date: string; completed: number; failed: number }[];
}) {
  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <div className="mb-1 flex shrink-0 items-center justify-between">
        <p className="text-sm font-semibold whitespace-nowrap text-muted-foreground">
          최근 7일 작업 성공/실패
        </p>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-good)" }}
            />
            완료
          </span>
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-critical)" }}
            />
            실패
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          >
            <XAxis
              dataKey="date"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <Tooltip
              cursor={{ fill: "var(--color-muted)" }}
              contentStyle={{
                background: "var(--color-card)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="completed"
              name="완료"
              stackId="a"
              fill="var(--chart-status-good)"
              radius={[0, 0, 0, 0]}
              maxBarSize={28}
            />
            <Bar
              dataKey="failed"
              name="실패"
              stackId="a"
              fill="var(--chart-status-critical)"
              radius={[4, 4, 0, 0]}
              maxBarSize={28}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
