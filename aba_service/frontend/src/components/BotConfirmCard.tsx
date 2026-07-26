import { useState } from "react";

import { TABLES } from "@/lib/member";
import type { PendingCall, ToolName } from "@/lib/libi-tools";

/**
 * 변경형 도구를 실행하기 전에 띄우는 카드.
 *
 * 모델이 작아 "예약"과 "대여 신청"을 헷갈릴 수 있다. 사용자가 **고쳐서** 실행할 수
 * 있어야 다시 말하지 않는다 — 자리와 요청 종류는 여기서 바꾼다.
 */
export function BotConfirmCard({
  pending,
  onConfirm,
  onCancel,
}: {
  pending: PendingCall;
  onConfirm: (name: ToolName, args: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const { name, args, book, sentence } = pending;
  const [table, setTable] = useState(String(args.table ?? TABLES[0].value));
  const [kind, setKind] = useState<ToolName>(name);

  const isDelivery = name === "request_read" || name === "request_borrow";

  return (
    <div className="rounded-2xl border-2 border-primary/40 bg-primary-soft/40 p-3">
      {/* 모델이 말한 제목이 아니라 **실제로 해석된 책**을 보여준다.
          이게 없으면 엉뚱한 책이 잡혀도 사용자가 알아챌 방법이 없다. */}
      {book && (
        <div className="mb-2 flex items-center gap-2 rounded-xl bg-card p-2">
          <span
            className={`flex size-10 items-center justify-center rounded-lg bg-gradient-to-br ${book.color} text-xl`}
          >
            {book.cover}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-xs font-bold text-foreground">
              {book.title.KR}
            </span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {book.author} · {book.zone} · {book.shelf}
            </span>
          </span>
        </div>
      )}
      <p className="text-sm font-semibold text-foreground">{sentence}</p>

      {isDelivery && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button
            onClick={() => setKind("request_read")}
            className={`rounded-xl border-2 p-2 text-left text-xs ${
              kind === "request_read"
                ? "border-primary bg-card font-bold text-primary"
                : "border-border bg-card text-muted-foreground"
            }`}
          >
            📖 자리로 받기
          </button>
          <button
            onClick={() => setKind("request_borrow")}
            className={`rounded-xl border-2 p-2 text-left text-xs ${
              kind === "request_borrow"
                ? "border-primary bg-card font-bold text-primary"
                : "border-border bg-card text-muted-foreground"
            }`}
          >
            🧾 대여 신청
          </button>
        </div>
      )}

      {kind === "request_read" && (
        <div className="mt-2 grid grid-cols-3 gap-1.5">
          {TABLES.map((t) => (
            <button
              key={t.value}
              onClick={() => setTable(t.value)}
              className={`rounded-lg border py-1.5 text-[10px] font-medium ${
                table === t.value
                  ? "border-primary bg-card text-primary"
                  : "border-border bg-card text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <button
          onClick={() =>
            // 대여로 바꾸면 table 인자는 스키마에 없다 — 넘기지 않는다.
            onConfirm(
              kind,
              kind === "request_read"
                ? { ...args, table }
                : { ...args, table: undefined },
            )
          }
          className="h-10 flex-1 rounded-xl bg-primary text-xs font-bold text-primary-foreground"
        >
          네, 진행할게요
        </button>
        <button
          onClick={onCancel}
          className="h-10 rounded-xl border border-border px-4 text-xs font-semibold text-muted-foreground"
        >
          아니요
        </button>
      </div>
    </div>
  );
}
