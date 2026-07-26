import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ArrivalToast } from "./ArrivalToast";
import { BottomNav } from "./BottomNav";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { useI18n } from "@/lib/i18n";
import { getToken } from "@/lib/member";
import { STORE } from "@/lib/mock-data";
import { MapPin, User } from "lucide-react";

export function AppShell({
  children,
  showStore = true,
  showNav = true,
}: {
  children: ReactNode;
  showStore?: boolean;
  showNav?: boolean;
}) {
  const { lang, tr } = useI18n();
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col bg-background">
      {/* 로봇 도착 알림 — 어느 화면에 있든 떠야 해서 여기 둔다.
          책이 도착하는 순간 회원이 「요청 현황」 탭을 보고 있을 이유가 없다. */}
      <ArrivalToast />
      <header className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b border-border bg-paper px-4 py-3.5">
        <div className="flex min-w-0 flex-col">
          <span className="font-serif text-lg font-bold leading-none text-primary">
            Li<span className="text-accent">Bi</span>
          </span>
          {showStore && (
            <span className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <MapPin className="size-3 text-accent" />
              <span className="truncate">
                {tr("storeNow")}: {STORE.name[lang as keyof typeof STORE.name]}
              </span>
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {/* 로그인/내 정보 — 이게 없으면 회원 기능으로 들어갈 길이 없다 */}
          <Link
            to={getToken() ? "/me" : "/login"}
            aria-label={getToken() ? "내 정보" : "로그인"}
            className="flex items-center gap-1 rounded-full border border-border px-2.5 py-1.5 text-xs font-semibold text-foreground"
          >
            <User className="size-3.5" />
            {getToken() ? "내 정보" : "로그인"}
          </Link>
          <LanguageSwitcher />
        </div>
      </header>
      <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      {showNav && <BottomNav />}
    </div>
  );
}
