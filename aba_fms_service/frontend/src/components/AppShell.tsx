import type { ReactNode } from "react";
import { BottomNav } from "./BottomNav";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { useI18n } from "@/lib/i18n";
import { STORE } from "@/lib/mock-data";
import { MapPin } from "lucide-react";

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
      <header className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b border-border bg-paper px-4 py-3">
        <div className="flex min-w-0 flex-col">
          <span className="font-serif text-lg font-bold leading-none text-primary">
            Labi <span className="text-accent">Bot</span>
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
        <LanguageSwitcher />
      </header>
      <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      {showNav && <BottomNav />}
    </div>
  );
}
