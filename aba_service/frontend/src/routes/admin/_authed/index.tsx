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
  Search,
  ShieldAlert,
  WifiOff,
  X,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";

import { toast } from "sonner";

import { MiniDonut, WeeklyTaskBars } from "@/components/admin/charts";
import {
  LoanQueuePanel,
  MemberPickerDialog,
  type QuickMember,
} from "@/components/admin/circulation";
import { AdminShell } from "@/components/admin/AdminShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  ASSIGNED: {
    color: "#0ea5e9",
    icon: Circle,
    label: "배정",
  },
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
              <div className="grid h-full grid-cols-[1.4fr_1fr] gap-3">
                <div className="flex h-full min-h-0 flex-col gap-1">
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
 * 대여/반납 숏컷 — 팝업 없이 회원관리 카드 왼쪽 안에서 진행하되(대여/반납 탭 토글),
 * 회원 고르기는 검색바 느낌의 트리거를 눌러 팝업(`MemberPickerDialog`)에서
 * 검색 → "선택" → 창 닫힘으로 담는다. 도서 고르기·큐잉·확정은 회원관리 페이지와
 * 공유하는 `LoanQueuePanel`(components/admin/circulation.tsx)이 담당한다.
 */
function MemberQuickPanel({ onDone }: { onDone: () => void }) {
  const [members, setMembers] = useState<QuickMember[]>([]);
  const [memberId, setMemberId] = useState<number | null>(null);
  const [memberPickerOpen, setMemberPickerOpen] = useState(false);

  useEffect(() => {
    void opsApi<QuickMember[]>("/api/admin/circulation/members")
      .then(setMembers)
      .catch(() => setMembers([]));
  }, []);

  const pickMember = (m: QuickMember) => {
    setMemberId(m.id);
    setMemberPickerOpen(false);
  };

  const selectedMember = members.find((m) => m.id === memberId) ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-1.5">
      {/* 회원 — 제목 + 검색바 느낌 트리거(클릭하면 팝업), 한 줄 */}
      <div className="shrink-0">
        <p className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground">
          회원
        </p>
        <button
          type="button"
          onClick={() => setMemberPickerOpen(true)}
          className="flex h-8 w-full items-center gap-1.5 rounded-md border bg-background px-2.5 text-left text-xs outline-none focus:ring-1 focus:ring-primary"
        >
          <span
            className={`min-w-0 flex-1 truncate ${selectedMember ? "" : "text-muted-foreground"}`}
          >
            {selectedMember
              ? `${selectedMember.full_name ?? selectedMember.username} (${selectedMember.username})`
              : "회원 선택"}
          </span>
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </div>

      <div className="min-h-0 flex-1">
        <LoanQueuePanel memberId={memberId} onDone={onDone} />
      </div>

      <MemberPickerDialog
        open={memberPickerOpen}
        members={members}
        onClose={() => setMemberPickerOpen(false)}
        onPick={pickMember}
      />
    </div>
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
