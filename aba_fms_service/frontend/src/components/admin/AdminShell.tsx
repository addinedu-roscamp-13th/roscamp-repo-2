import {
  Link,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Bot,
  Camera,
  ChevronDown,
  ChevronRight,
  Code2,
  Cpu,
  Crosshair,
  GitBranch,
  GitFork,
  Hand,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  LogOut,
  MapPinned,
  Menu,
  Workflow,
  ParkingCircle,
  Network,
  RefreshCw,
  ScanEye,
  Share2,
  Sliders,
  ServerCog,
  Table2,
  MessageSquare,
  Type,
  UserRound,
  Users,
  Volume2,
  Waypoints,
  Wifi,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Toaster } from "@/components/ui/sonner";
import {
  setActiveRobot,
  useActiveRobotBase,
  useActiveRobotId,
  useActiveRobotName,
  useActiveRobotType,
} from "@/lib/active-robot";
import { adminApi, clearToken, getToken } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  exact?: boolean;
  tab?: string;
  children?: NavItem[];
};
type NavGroup = {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  items: NavItem[];
  to?: string;
};

const NAV_GROUPS: NavGroup[] = [
  {
    key: "manage",
    label: "관리",
    icon: LayoutDashboard,
    items: [
      { to: "/admin", label: "대시보드", icon: LayoutDashboard, exact: true },
      { to: "/admin/users", label: "관리자 목록", icon: Users },
      { to: "/admin/robots", label: "로봇 IP 관리", icon: Network },
    ],
  },
  {
    key: "chat",
    label: "AI 챗봇 제어",
    icon: MessageSquare,
    to: "/admin/chat",
    items: [],
  },
  {
    key: "robot",
    label: "로봇 제어",
    icon: Cpu,
    items: [
      { to: "/admin/robot",    label: "PinkyPro 제어", icon: Bot },
      { to: "/admin/buzzer",   label: "부저",           icon: Volume2 },
      { to: "/admin/camera",   label: "카메라",         icon: Camera },
      { to: "/admin/joystick", label: "조이스틱 제어",  icon: Wifi },
      { to: "/admin/control",  label: "관제시스템",      icon: MapPinned },
      { to: "/admin/parking", label: "주행로봇 주차", icon: ParkingCircle },
      { to: "/admin/fleet",    label: "멀티 관제",       icon: LayoutGrid },
      { to: "/admin/fms",      label: "FMS 통합 제어",   icon: Network },
      { to: "/admin/robot-learning", label: "주행로봇 학습", icon: Workflow, children: [
        { to: "/admin/robot-learning", label: "간단 액션", icon: Bot, tab: "actions" },
        { to: "/admin/robot-learning", label: "시나리오", icon: Workflow, tab: "scenarios" },
      ] },
      { to: "/admin/pinky-detect",   label: "핑키프로 인식",  icon: ScanEye },
      { to: "/admin/human-follow",   label: "사람 추종",      icon: UserRound },
      { to: "/admin/aruco",          label: "아르코 도킹",    icon: Crosshair },
      { to: "/admin/waypoint", label: "Waypoint",       icon: Waypoints },
      { to: "/admin/fsm",      label: "FSM + BT",       icon: GitBranch },
      { to: "/admin/dispatch", label: "FMS 배차·교통", icon: Share2 },
    ],
  },
  {
    key: "arm",
    label: "로봇팔 JetCobot",
    icon: Hand,
    items: [
      { to: "/admin/arm",               label: "대시보드",       icon: LayoutDashboard, exact: true },
      { to: "/admin/arm/joint-control", label: "관절 수동 제어", icon: Sliders },
      { to: "/admin/arm/color-pick",    label: "색상 블록 집기", icon: ScanEye },
      { to: "/admin/arm/face-track",    label: "얼굴 추적",      icon: Camera },
      { to: "/admin/arm/gesture",       label: "제스처 제어",    icon: Hand },
      { to: "/admin/arm/ocr",           label: "OCR 텍스트 인식", icon: Type },
      { to: "/admin/arm/playback",      label: "모션 티칭 및 재생", icon: RefreshCw },
    ],
  },
  {
    key: "dev",
    label: "개발 센터",
    icon: Code2,
    items: [
      { to: "/admin/dev/api-docs", label: "API 문서", icon: BookOpen },
      { to: "/admin/dev/tables", label: "테이블 정의서", icon: Table2 },
      { to: "/admin/dev/erd", label: "ERD", icon: GitFork },
      { to: "/admin/dev/architecture", label: "아키텍처", icon: Layers },
      { to: "/admin/dev/server-ops", label: "서버 운영 가이드", icon: ServerCog },
    ],
  },
];

function isItemActive(pathname: string, item: NavItem, search?: Record<string, unknown>) {
  const pathActive = item.exact ? pathname === item.to : pathname === item.to || pathname.startsWith(item.to + "/");
  if (!pathActive) return false;
  if (item.tab) return search?.tab === item.tab;
  if (item.children?.length) return search?.tab == null || item.children.some((child) => isItemActive(pathname, child, search));
  return true;
}

function RobotSelector() {
  const activeId = useActiveRobotId();
  const activeBase = useActiveRobotBase();
  const location = useRouterState({ select: (s) => s.location });
  const pathname = location.pathname;
  const preferredType = pathname.startsWith("/admin/arm") ? "arm" : pathname.startsWith("/admin/robot") || pathname.startsWith("/admin/buzzer") || pathname.startsWith("/admin/camera") || pathname.startsWith("/admin/joystick") || pathname.startsWith("/admin/control") || pathname.startsWith("/admin/parking") || pathname.startsWith("/admin/fleet") || pathname.startsWith("/admin/human-follow") || pathname.startsWith("/admin/chat") ? "pinky" : null;

  const { data } = useQuery({
    queryKey: ["admin", "robots", "selector"],
    queryFn: () => adminApi.listRobots({ limit: 200 }),
    enabled: !!getToken(),
    retry: false,
  });

  const robots = useMemo(
    () => (data?.items ?? []).filter((r) => r.is_active),
    [data],
  );

  const effectiveId = useMemo(() => {
    if (activeId != null && robots.some((r) => r.id === activeId && (preferredType == null || r.robot_type === preferredType))) return activeId;
    return robots.find((r) => r.robot_type === preferredType)?.id ?? robots[0]?.id ?? null;
  }, [activeId, preferredType, robots]);

  useEffect(() => {
    if (effectiveId == null) return;
    const r = robots.find((x) => x.id === effectiveId);
    if (!r) return;
    const nextBase = "http://" + r.ip_address + ":" + r.port;
    if (effectiveId !== activeId || activeBase !== nextBase) {
      setActiveRobot({ id: r.id, name: r.name, type: r.robot_type, base: nextBase });
    }
  }, [effectiveId, activeId, activeBase, robots]);

  if (robots.length === 0) return null;

  return (
    <Select
      value={effectiveId != null ? String(effectiveId) : undefined}
      onValueChange={(v) => {
        const r = robots.find((x) => x.id === Number(v));
        if (r) setActiveRobot({ id: r.id, name: r.name, type: r.robot_type, base: "http://" + r.ip_address + ":" + r.port });
      }}
    >
      <SelectTrigger className="h-8 w-[150px] gap-1.5 border-slate-200 bg-white text-[13px] sm:w-[220px]">
        <Cpu className="h-3.5 w-3.5 shrink-0 text-orange-500" />
        <SelectValue placeholder="로봇 선택" />
      </SelectTrigger>
      <SelectContent>
        {robots.map((r) => (
          <SelectItem key={r.id} value={String(r.id)}>
            {r.robot_type === "arm" ? "로봇팔" : r.robot_type === "pinky" ? "주행로봇" : "기타"} · {r.name} · {r.ip_address}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function AdminShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const location = useRouterState({ select: (s) => s.location });
  const pathname = location.pathname;
  const [mobileOpen, setMobileOpen] = useState(false);
  const activeRobotName = useActiveRobotName();
  const activeRobotBase = useActiveRobotBase();
  const activeRobotType = useActiveRobotType();
  const expectedRobotType = pathname.startsWith("/admin/arm")
    ? "arm"
    : pathname.startsWith("/admin/robot") ||
        pathname.startsWith("/admin/buzzer") ||
        pathname.startsWith("/admin/camera") ||
        pathname.startsWith("/admin/joystick") ||
        pathname.startsWith("/admin/control") || pathname.startsWith("/admin/parking") || pathname.startsWith("/admin/fleet") ||
        pathname.startsWith("/admin/human-follow") ||
        pathname.startsWith("/admin/chat")
      ? "pinky"
      : null;
  const showRobotNotice = expectedRobotType != null;
  const robotTypeMismatch = showRobotNotice && activeRobotType != null && activeRobotType !== expectedRobotType;

  const { data: me } = useQuery({
    queryKey: ["admin", "me"],
    queryFn: adminApi.me,
    enabled: !!getToken(),
    retry: false,
  });

  const breadcrumb = useMemo(() => {
    for (const g of NAV_GROUPS) {
      if (g.to && isItemActive(pathname, { to: g.to, label: g.label, icon: g.icon })) {
        return g.label;
      }
      const search = location.search as Record<string, unknown>;
      const it = g.items.find((i) => isItemActive(pathname, i, search));
      if (it?.children?.length) {
        const child = it.children.find((c) => isItemActive(pathname, c, search));
        return `${g.label} / ${it.label}${child ? ` / ${child.label}` : ""}`;
      }
      if (it) return `${g.label} / ${it.label}`;
    }
    return title;
  }, [location.search, pathname, title]);

  function logout() {
    clearToken();
    void navigate({ to: "/admin/login" });
  }

  return (
    <div className="flex min-h-screen flex-col bg-[#EEF1F6] text-slate-900 md:h-screen">
      {/* Top header */}
      <header className="flex h-[60px] shrink-0 items-stretch border-b border-slate-200/70 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.05)]">
        <div className="hidden w-[240px] shrink-0 items-center gap-2.5 border-r border-slate-200/70 bg-[#F8F9FB] px-4 md:flex">
          <Bot className="h-6 w-6 shrink-0 text-orange-500" />
          <span className="truncate text-[14px] font-bold text-slate-800">로봇제어 관리자</span>
        </div>
        <div className="flex flex-1 items-center justify-between gap-3 px-3 md:px-4">
          <div className="flex min-w-0 items-center gap-2 md:gap-3">
            <button
              type="button"
              className="inline-flex h-9 w-9 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 md:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="메뉴 열기"
            >
              <Menu className="h-5 w-5" />
            </button>
            <Bot className="h-6 w-6 shrink-0 text-orange-500 md:hidden" />
            <span className="hidden truncate text-[13px] text-slate-500 md:block">
              {breadcrumb}
            </span>
          </div>
          <div className="flex min-w-0 items-center justify-end gap-2 md:gap-3">
            <RobotSelector />
            <span className="hidden truncate text-right text-[13px] text-slate-500 sm:block">
              {me?.full_name ?? me?.username ?? ""}
            </span>
            <button
              onClick={logout}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-slate-500 hover:bg-slate-100 hover:text-slate-800 md:px-3"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden md:inline">로그아웃</span>
            </button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {mobileOpen && (
          <button
            type="button"
            className="fixed inset-0 z-30 bg-slate-950/40 md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="메뉴 닫기"
          />
        )}

        {/* Sidebar */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[260px] max-w-[85vw] shrink-0 flex-col border-r border-slate-200/70 bg-[#F8F9FB] py-3 shadow-xl transition-transform duration-200 md:static md:w-[240px] md:max-w-none md:translate-x-0 md:shadow-none",
            mobileOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="mb-2 flex items-center justify-between px-3 md:hidden">
            <p className="text-[13px] font-semibold text-slate-700">관리자 메뉴</p>
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-slate-100"
              onClick={() => setMobileOpen(false)}
              aria-label="메뉴 닫기"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-2.5">
            {NAV_GROUPS.map((g) => (
              <NavGroupBlock
                key={g.key}
                group={g}
                pathname={pathname}
                onNavigate={() => setMobileOpen(false)}
              />
            ))}
          </nav>
        </aside>

        {/* Main */}
        <main className="min-h-0 min-w-0 flex-1 overflow-auto bg-[#EEF1F6] p-4 md:p-6">
          {showRobotNotice && (
            <div
              className={cn(
                "mb-4 rounded-lg border bg-white px-4 py-3 text-sm shadow-sm",
                robotTypeMismatch ? "border-amber-300 text-amber-800" : "border-slate-200 text-slate-700",
              )}
            >
              <span className="font-semibold">선택된 로봇을 확인하세요.</span>{" "}
              현재 동작 대상은 {activeRobotName ?? "선택 안 됨"} ({activeRobotBase || "API 없음"}) 입니다.
              {robotTypeMismatch && (
                <span className="ml-2 font-semibold">
                  이 페이지는 {expectedRobotType === "arm" ? "로봇팔" : "주행로봇"} 선택이 필요합니다.
                </span>
              )}
            </div>
          )}
          {children}
        </main>
      </div>

      <Toaster />
    </div>
  );
}

const itemBase =
  "relative flex items-center gap-3 rounded-lg pl-4 pr-3 py-2.5 text-[13.5px] font-medium transition-all";
const itemDefault =
  "text-slate-600 hover:bg-white hover:text-slate-900 hover:shadow-[0_1px_2px_rgba(15,23,42,0.06)]";
const itemActive =
  "bg-white text-orange-600 font-semibold shadow-[0_2px_4px_rgba(249,115,22,0.12)] before:content-[''] before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-r-full before:bg-orange-500";

function locationSearch(): Record<string, unknown> {
  if (typeof window === "undefined") return {};
  return Object.fromEntries(new URLSearchParams(window.location.search).entries());
}

function navSearch(item: NavItem): Record<string, string> | undefined {
  return item.tab ? { tab: item.tab } : undefined;
}

function NavGroupBlock({
  group,
  pathname,
  onNavigate,
}: {
  group: NavGroup;
  pathname: string;
  onNavigate: () => void;
}) {
  const GroupIcon = group.icon;

  if (group.to) {
    const active = isItemActive(pathname, { to: group.to, label: group.label, icon: group.icon });
    return (
      <Link
        to={group.to}
        onClick={onNavigate}
        className={cn(itemBase, "w-full text-left", active ? itemActive : itemDefault)}
      >
        <GroupIcon className="h-4 w-4 shrink-0" />
        <span className="flex-1">{group.label}</span>
      </Link>
    );
  }

  const search = locationSearch();
  const groupActive = group.items.some((i) => isItemActive(pathname, i, search));
  const [open, setOpen] = useState(groupActive);

  return (
    <div className="flex flex-col gap-0.5">
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(itemBase, "w-full text-left", groupActive ? itemActive : itemDefault)}
      >
        <GroupIcon className="h-4 w-4 shrink-0" />
        <span className="flex-1">{group.label}</span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 pl-3">
          {group.items.map((it) => {
            const active = isItemActive(pathname, it, search);
            const Icon = it.icon;
            return (
              <div key={`${it.to}-${it.label}`} className="flex flex-col gap-0.5">
                <Link
                  to={it.to}
                  search={navSearch(it) as any}
                  onClick={onNavigate}
                  className={cn(itemBase, active ? itemActive : itemDefault)}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {it.label}
                </Link>
                {it.children?.length && active ? (
                  <div className="flex flex-col gap-0.5 pl-5">
                    {it.children.map((child) => {
                      const ChildIcon = child.icon;
                      const childActive = isItemActive(pathname, child, search);
                      return (
                        <Link
                          key={`${child.to}-${child.label}`}
                          to={child.to}
                          search={navSearch(child) as any}
                          onClick={onNavigate}
                          className={cn("relative flex items-center gap-2 rounded-md py-1.5 pl-4 pr-2 text-[12.5px] transition-all", childActive ? "bg-white text-orange-600 font-semibold shadow-[0_1px_3px_rgba(249,115,22,0.12)] before:content-[''] before:absolute before:left-0 before:top-1 before:bottom-1 before:w-[2px] before:rounded-r-full before:bg-orange-500" : "text-slate-500 hover:bg-white hover:text-slate-900")}
                        >
                          <ChildIcon className="h-3.5 w-3.5 shrink-0" />
                          {child.label}
                        </Link>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
