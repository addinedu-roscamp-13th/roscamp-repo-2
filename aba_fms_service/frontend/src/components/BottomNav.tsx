import { Link, useRouterState } from "@tanstack/react-router";
import { Home, Search, Map, Bot, Settings } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function BottomNav() {
  const { tr } = useI18n();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  const items: { to: "/home" | "/search" | "/map" | "/chat" | "/settings"; icon: typeof Home; label: string; highlight?: boolean }[] = [
    { to: "/home", icon: Home, label: tr("navHome") },
    { to: "/search", icon: Search, label: tr("navSearch") },
    { to: "/map", icon: Map, label: tr("navMap") },
    { to: "/chat", icon: Bot, label: tr("navChat"), highlight: true },
    { to: "/settings", icon: Settings, label: tr("navMe") },
  ];

  return (
    <nav className="sticky bottom-0 z-40 border-t border-border bg-card safe-bottom">
      <ul className="mx-auto grid max-w-md grid-cols-5">
        {items.map(({ to, icon: Icon, label, highlight }) => {
          const active = pathname === to || (to === "/chat" && pathname.startsWith("/chat"));
          return (
            <li key={to}>
              <Link
                to={to}
                className="flex flex-col items-center justify-center gap-1 py-2.5 text-[11px] font-medium"
              >
                <span
                  className={`flex size-10 items-center justify-center rounded-2xl transition-colors ${
                    highlight
                      ? "bg-primary text-primary-foreground shadow-md"
                      : active
                        ? "bg-primary-soft text-primary"
                        : "text-muted-foreground"
                  }`}
                >
                  <Icon className="size-5" />
                </span>
                <span className={active ? "text-primary" : "text-muted-foreground"}>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
