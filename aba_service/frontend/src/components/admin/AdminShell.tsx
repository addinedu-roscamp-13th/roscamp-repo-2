import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bell,
  BookMarked,
  BookOpen,
  ClipboardCheck,
  Library,
  ListChecks,
  ShieldAlert,
  Bot,
  ChevronDown,
  Code2,
  GitFork,
  Layers,
  LayoutDashboard,
  LogOut,
  ServerCog,
  Stamp,
  Table2,
  Users,
} from "lucide-react";
import { useMemo, type ComponentType, type ReactNode } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
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
    key: "ops",
    label: "운영",
    icon: ListChecks,
    items: [
      { to: "/admin/ops", label: "운영 대시보드", icon: LayoutDashboard },
      { to: "/admin/robots", label: "실시간 모니터링", icon: Bot },
      { to: "/admin/tasks", label: "작업 지시", icon: ListChecks },
      { to: "/admin/approvals", label: "대여 승인", icon: Stamp },
      { to: "/admin/alerts", label: "작업 알림 · 로그", icon: Bell },
      {
        to: "/admin/reports",
        label: "정리 · 분류 리포트",
        icon: ClipboardCheck,
      },
      { to: "/admin/members", label: "회원 · 대여/반납", icon: BookMarked },
      { to: "/admin/books", label: "도서 · 서가 관리", icon: Library },
      { to: "/admin/insight", label: "통합 검색 · 통계", icon: BarChart3 },
      { to: "/admin/security", label: "야간 보안", icon: ShieldAlert },
    ],
  },
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
      {
        to: "/admin/dev/server-ops",
        label: "서버 운영 가이드",
        icon: ServerCog,
      },
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
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="flex items-center gap-2.5 px-2 py-1">
            <Bot className="h-6 w-6 shrink-0 text-accent" />
            <span className="truncate text-[14px] font-bold text-sidebar-foreground group-data-[collapsible=icon]:hidden">
              LiBi Admin
            </span>
          </div>
        </SidebarHeader>
        <SidebarContent>
          {NAV_GROUPS.map((g) => (
            <NavGroupBlock key={g.key} group={g} pathname={pathname} />
          ))}
        </SidebarContent>
      </Sidebar>

      <SidebarInset className="flex h-svh flex-col md:h-screen">
        {/* Top header */}
        <header className="flex h-[60px] shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-3 shadow-sm md:px-4">
          <div className="flex min-w-0 items-center gap-2 md:gap-3">
            <SidebarTrigger />
            <Bot className="h-6 w-6 shrink-0 text-accent md:hidden" />
            <span className="hidden truncate text-[13px] text-muted-foreground md:block">
              {breadcrumb}
            </span>
          </div>
          <div className="flex min-w-0 items-center justify-end gap-2 md:gap-3">
            <span className="hidden truncate text-right text-[13px] text-muted-foreground sm:block">
              {me?.full_name ?? me?.username ?? ""}
            </span>
            <button
              onClick={logout}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-muted-foreground hover:bg-muted hover:text-foreground md:px-3"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden md:inline">로그아웃</span>
            </button>
          </div>
        </header>

        {/* Main */}
        <div className="min-h-0 flex-1 overflow-auto bg-background p-4 md:p-6">
          {children}
        </div>
      </SidebarInset>

      <Toaster />
    </SidebarProvider>
  );
}

function NavGroupBlock({
  group,
  pathname,
}: {
  group: NavGroup;
  pathname: string;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const groupActive = group.items.some((i) => isItemActive(pathname, i));
  const GroupIcon = group.icon;

  function handleNavigate() {
    if (isMobile) setOpenMobile(false);
  }

  return (
    <Collapsible defaultOpen={groupActive} className="group/nav-collapsible">
      <SidebarGroup>
        <SidebarGroupLabel asChild>
          <CollapsibleTrigger
            className={cn(
              "flex h-8 w-full items-center gap-2 rounded-md px-2 text-[13px] font-medium transition-colors hover:bg-sidebar-accent",
              groupActive
                ? "text-sidebar-accent-foreground"
                : "text-sidebar-foreground/70",
            )}
          >
            <GroupIcon className="h-4 w-4 shrink-0" />
            <span className="flex-1 truncate text-left group-data-[collapsible=icon]:hidden">
              {group.label}
            </span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform duration-200 group-data-[state=closed]/nav-collapsible:-rotate-90 group-data-[collapsible=icon]:hidden" />
          </CollapsibleTrigger>
        </SidebarGroupLabel>
        <CollapsibleContent>
          <SidebarGroupContent>
            <SidebarMenu>
              {group.items.map((it) => {
                const active = isItemActive(pathname, it);
                const Icon = it.icon;
                return (
                  <SidebarMenuItem key={it.to}>
                    <SidebarMenuButton
                      asChild
                      isActive={active}
                      tooltip={it.label}
                      className="data-[active=true]:border-l-2 data-[active=true]:border-accent data-[active=true]:pl-[calc(0.5rem-2px)] data-[active=true]:font-semibold"
                    >
                      <Link to={it.to} onClick={handleNavigate}>
                        <Icon className="h-4 w-4 shrink-0" />
                        <span>{it.label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}
