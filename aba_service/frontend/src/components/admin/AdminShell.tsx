import {
  Link,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  GitFork,
  Layers,
  LayoutDashboard,
  LogOut,
  Menu,
  ServerCog,
  Table2,
  Users,
  X,
} from "lucide-react";
import { useMemo, useState, type ComponentType, type ReactNode } from "react";

import { Toaster } from "@/components/ui/sonner";
import { adminApi, clearToken, getToken } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  exact?: boolean;
};
type NavGroup = {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  items: NavItem[];
};

const NAV_GROUPS: NavGroup[] = [
  {
    key: "manage",
    label: "관리",
    icon: LayoutDashboard,
    items: [
      { to: "/admin", label: "대시보드", icon: LayoutDashboard, exact: true },
      { to: "/admin/users", label: "관리자 목록", icon: Users },
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

function isItemActive(pathname: string, item: NavItem) {
  return item.exact ? pathname === item.to : pathname.startsWith(item.to);
}

export function AdminShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [mobileOpen, setMobileOpen] = useState(false);

  const { data: me } = useQuery({
    queryKey: ["admin", "me"],
    queryFn: adminApi.me,
    enabled: !!getToken(),
    retry: false,
  });

  const breadcrumb = useMemo(() => {
    for (const g of NAV_GROUPS) {
      const it = g.items.find((i) => isItemActive(pathname, i));
      if (it) return `${g.label} / ${it.label}`;
    }
    return title;
  }, [pathname, title]);

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
          <span className="truncate text-[14px] font-bold text-slate-800">Labi Admin</span>
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

function NavGroupBlock({
  group,
  pathname,
  onNavigate,
}: {
  group: NavGroup;
  pathname: string;
  onNavigate: () => void;
}) {
  const groupActive = group.items.some((i) => isItemActive(pathname, i));
  const [open, setOpen] = useState(groupActive);
  const GroupIcon = group.icon;

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
            const active = isItemActive(pathname, it);
            const Icon = it.icon;
            return (
              <Link
                key={it.to}
                to={it.to}
                onClick={onNavigate}
                className={cn(itemBase, active ? itemActive : itemDefault)}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {it.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
