import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  BookDetailSheet,
  reserveFromSheet,
} from "@/components/BookDetailSheet";
import { BookRow, BookRowSkeleton } from "@/components/BookRow";
import { LibraryMap, type ZoneBox } from "@/components/LibraryMap";
import { fetchCatalogResult, type CatalogBook } from "@/lib/books-api";
import { useI18n } from "@/lib/i18n";

export const Route = createFileRoute("/map")({
  head: () => ({ meta: [{ title: "LiBi — 공간 안내" }] }),
  component: MapPage,
});

/**
 * 도서관 내부 지도.
 *
 * 배경은 로봇이 실제로 주행하는 `arte2` 점유격자 지도이고, 구역 박스 좌표는
 * `waypoint.yaml` 정점에서 계산한다 — 화면에 보이는 위치가 로봇이 가는 위치와 같은 계다.
 *
 * ⚠️ 길안내(턴바이턴)는 제공하지 않는다 — 그 기능이 없으므로 버튼도 두지 않는다.
 */
function MapPage() {
  const { tr } = useI18n();
  const [zone, setZone] = useState<ZoneBox | null>(null);
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [picked, setPicked] = useState<CatalogBook | null>(null);

  // 구역을 고르면 그 서가에 실제로 꽂힌 책을 보여준다.
  useEffect(() => {
    if (!zone) {
      setBooks([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    // 구역에 속한 정점 이름으로 **서버가** 거른다. 예전처럼 전체를 받아
    // 클라이언트에서 거르면 장서가 상한(200)을 넘는 순간 뒤쪽 책이 통째로 빠진다.
    void fetchCatalogResult({ zone: zone.members, limit: 200 })
      .then(({ ok, rows }) => {
        if (cancelled) return;
        setFailed(!ok); // 빈 결과와 실패는 다른 것이다
        setBooks(rows);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [zone]);

  return (
    <AppShell>
      <div className="px-5 pb-8 pt-3">
        <h1 className="text-xl font-bold text-foreground">{tr("storeMap")}</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          구역을 탭하면 안내와 그 서가의 책이 나와요
        </p>

        <div className="mt-3 rounded-2xl border border-border bg-card p-3 shadow-card">
          <LibraryMap
            activeZone={zone?.key ?? null}
            onSelect={(z) => setZone(z.key === zone?.key ? null : z)}
          />
        </div>

        {zone ? (
          <div className="mt-5 rounded-2xl border border-border bg-card p-5 shadow-card">
            <h2 className="text-lg font-bold text-foreground">{zone.label}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{zone.desc}</p>

            {zone.category ? (
              <>
                <h3 className="mt-4 text-sm font-bold text-foreground">
                  이 서가의 책{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    ({books.length})
                  </span>
                </h3>
                {loading ? (
                  <div className="mt-2 space-y-2">
                    <BookRowSkeleton />
                    <BookRowSkeleton />
                  </div>
                ) : failed ? (
                  <p className="mt-2 rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    도서 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
                  </p>
                ) : books.length === 0 ? (
                  <p className="mt-2 rounded-xl bg-muted px-3 py-2 text-xs text-muted-foreground">
                    등록된 책이 없습니다
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {books.map((b) => (
                      <BookRow key={b.id} book={b} onSelect={setPicked} />
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="mt-4 rounded-xl bg-muted px-3 py-2 text-xs text-muted-foreground">
                도서를 자리까지 받아보려면 도서 검색에서 요청할 수 있어요.
              </p>
            )}
          </div>
        ) : (
          <p className="mt-5 rounded-2xl border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
            지도의 구역을 탭해 보세요
          </p>
        )}
      </div>

      <BookDetailSheet
        book={picked}
        onOpenChange={(open) => !open && setPicked(null)}
        onReserve={(b) => void reserveFromSheet(b)}
      />
    </AppShell>
  );
}
