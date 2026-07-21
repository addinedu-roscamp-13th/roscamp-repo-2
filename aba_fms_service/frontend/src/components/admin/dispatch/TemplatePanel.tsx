import { Button } from "@/components/ui/button";

import type { OrderTemplate } from "./templates";
import { waypointLabel } from "./waypoints";

/**
 * 저장된 주문 템플릿 목록 — 같은 배달을 반복할 때 매번 고르지 않게 한다.
 * 「주문 생성」은 큐에 PENDING 으로만 넣고, 「생성 + 배차」는 접수 직후 자동 배차까지 한다.
 */
export function TemplatePanel({
  templates,
  onUse,
  onUseAndAssign,
  onRemove,
  busy,
}: {
  templates: OrderTemplate[];
  onUse: (t: OrderTemplate) => void;
  onUseAndAssign: (t: OrderTemplate) => void;
  onRemove: (id: string) => void;
  busy: boolean;
}) {
  return (
    <section className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="text-sm font-semibold">주문 템플릿</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          자주 쓰는 조합을 저장해두고 클릭 한 번으로 주문을 만든다. 이
          브라우저에만 저장된다.
        </p>
      </div>

      {templates.length === 0 ? (
        <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
          저장된 템플릿이 없습니다. 아래 「주문 만들기」에서 값을 채운 뒤
          「템플릿으로 저장」을 누르세요.
        </p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {templates.map((t) => (
            <li
              key={t.id}
              className="flex flex-col gap-2 rounded-md border p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{t.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {t.book || "도서 미지정"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {waypointLabel(t.pickup)}
                  <span className="mx-1">→</span>
                  {waypointLabel(t.dropoff)}
                  {t.priority ? ` · P${t.priority}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-1">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => onUse(t)}
                >
                  주문 생성
                </Button>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => onUseAndAssign(t)}
                  title="접수 직후 자동 배차(PATROL 우선)까지 한다"
                >
                  생성 + 배차
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onRemove(t.id)}
                >
                  삭제
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
