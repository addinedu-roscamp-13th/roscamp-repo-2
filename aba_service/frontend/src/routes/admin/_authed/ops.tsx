import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { ops, type Dashboard } from "@/lib/ops-api";

export const Route = createFileRoute("/admin/_authed/ops")({
  head: () => ({ meta: [{ title: "LiBi Admin — 운영 대시보드" }] }),
  component: OpsPage,
});

/** 운영 대시보드 — 도서관(우리 DB)과 로봇(FMS)을 한 화면에. */
function OpsPage() {
  const [d, setD] = useState<Dashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      ops
        .dashboard()
        .then(setD)
        .catch((e) => setErr(e instanceof Error ? e.message : "불러오기 실패"));
    void load();
    const t = setInterval(load, 4000); // 로봇/작업이 바뀌므로 주기 갱신
    return () => clearInterval(t);
  }, []);

  return (
    <AdminShell title="운영 대시보드">
      {err ? (
        <p className="mb-4 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-700">
          {err}
        </p>
      ) : null}
      {!d ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : (
        <div className="space-y-6">
          {/* 알림 — 조치가 필요한 것만 위로 */}
          <div className="space-y-2">
            {d.library.overdue > 0 ? (
              <Alert tone="bad">
                연체 {d.library.overdue}건 — 회원에게 반납 안내가 필요합니다
              </Alert>
            ) : null}
            {d.library.reservations_ready > 0 ? (
              <Alert tone="ok">
                예약 준비 완료 {d.library.reservations_ready}건 — 회원에게
                알려주세요
              </Alert>
            ) : null}
            {d.tasks.failed > 0 ? (
              <Alert tone="bad">
                실패한 작업 {d.tasks.failed}건 — 확인이 필요합니다
              </Alert>
            ) : null}
            {!d.fleet.linked ? (
              <Alert tone="warn">
                FMS 연결 없음 — 로봇 정보를 읽지 못합니다 (도서·회원 기능은
                정상)
              </Alert>
            ) : null}
          </div>

          <Section title="도서관">
            <Stat label="장서" value={d.library.books} />
            <Stat label="대출 중" value={d.library.active_loans} />
            <Stat
              label="연체"
              value={d.library.overdue}
              tone={d.library.overdue ? "bad" : undefined}
            />
            <Stat
              label="반납 임박"
              value={d.library.due_soon}
              tone={d.library.due_soon ? "warn" : undefined}
            />
            <Stat label="회원" value={d.library.members} />
            <Stat label="예약 대기" value={d.library.reservations_waiting} />
          </Section>

          <Section title="로봇">
            <Stat label="전체" value={d.fleet.robots} />
            <Stat label="PATROL" value={d.fleet.patrol} />
            <Stat label="IDLE" value={d.fleet.idle} />
            <Stat label="WORKING" value={d.fleet.working} />
            <Stat
              label="끊김"
              value={d.fleet.stale}
              tone={d.fleet.stale ? "bad" : undefined}
            />
          </Section>

          <Section title="작업">
            <Stat
              label="대기"
              value={d.tasks.pending}
              tone={d.tasks.pending ? "warn" : undefined}
            />
            <Stat label="진행" value={d.tasks.executing} />
            <Stat label="완료" value={d.tasks.completed} tone="ok" />
            <Stat
              label="실패"
              value={d.tasks.failed}
              tone={d.tasks.failed ? "bad" : undefined}
            />
          </Section>

          <div className="flex flex-wrap gap-2">
            <Quick to="/admin/tasks" label="작업 지시 →" />
            <Quick to="/admin/robots" label="실시간 모니터링 →" />
            <Quick to="/admin/members" label="회원 · 대여/반납 →" />
            <Quick to="/admin/books" label="도서 관리 →" />
          </div>
        </div>
      )}
    </AdminShell>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      <div className="flex flex-wrap gap-8">{children}</div>
    </section>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "bad" | "warn" | "ok";
}) {
  const c =
    tone === "bad"
      ? "text-rose-600"
      : tone === "warn"
        ? "text-amber-600"
        : tone === "ok"
          ? "text-emerald-600"
          : "";
  return (
    <div className="flex min-w-[4.5rem] flex-col">
      <span className={`text-2xl font-semibold tabular-nums ${c}`}>
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

function Alert({
  tone,
  children,
}: {
  tone: "bad" | "warn" | "ok";
  children: React.ReactNode;
}) {
  const c = {
    bad: "bg-rose-500/10 text-rose-700",
    warn: "bg-amber-500/10 text-amber-700",
    ok: "bg-emerald-500/10 text-emerald-700",
  }[tone];
  return (
    <p className={`rounded-lg px-3 py-2 text-sm font-medium ${c}`}>
      {children}
    </p>
  );
}

function Quick({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted"
    >
      {label}
    </Link>
  );
}
