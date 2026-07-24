import { useEffect, useRef, useState } from "react";

import { getToken, memberApi, type MemberNotification } from "@/lib/member";

/**
 * 로봇이 내 책을 가져다줬을 때 화면 위에 띄우는 알림.
 *
 * ## 왜 요청 현황 화면이 아니라 여기인가
 *
 * 책이 도착하는 순간 회원이 「요청 현황」 탭을 보고 있을 이유가 없다 — 검색을 하고
 * 있거나 다른 책을 고르고 있다. 알림은 **어느 화면에 있든** 떠야 의미가 있어서
 * AppShell 에 붙인다.
 *
 * ## 왜 폴링인가
 *
 * 사건 자체는 FMS 가 웹소켓으로도 밀어 주지만, 그건 관제(다른 서비스·다른 인증)용이다.
 * 회원 앱이 그 소켓을 직접 물면 FMS 관리자 토큰이 브라우저로 나가야 한다.
 * 도서관 백엔드가 대신 받아 **내 주문만 걸러** 주고, 이 컴포넌트는 그걸 폴링한다.
 *
 * ## seq 를 들고 있는 이유
 *
 * 사건은 상태와 달리 놓치면 끝이다. 마지막으로 본 `seq` 를 넘겨 그 뒤 것만 받으므로,
 * 폴링 사이에 여러 개가 쌓여도 하나도 안 빠뜨린다. 첫 조회는 **지금 시점부터** 잡는다 —
 * 로그인하자마자 어제 배달까지 우르르 뜨면 알림이 아니라 소음이다.
 */

/** 폴링 주기(ms). 배달은 분 단위로 진행되므로 5초면 충분히 즉각적이다. */
const POLL_MS = 5000;
/** 한 알림이 화면에 머무는 시간(ms). */
const SHOW_MS = 12000;

type Toast = MemberNotification & { id: string };

function tone(kind: string): string {
  if (kind === "task_failed") return "border-red-300 bg-red-50 text-red-900";
  if (kind === "task_done")
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  return "border-sky-300 bg-sky-50 text-sky-900";
}

function icon(kind: string): string {
  if (kind === "task_failed") return "⚠️";
  if (kind === "task_done") return "📚";
  return "🤖";
}

export function ArrivalToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // 마지막으로 본 seq. null 이면 "아직 기준을 못 잡았다"는 뜻이라 첫 조회로 잡는다.
  const seenRef = useRef<number | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      if (stopped) return;
      // 로그인 안 했으면 부를 이유가 없다(401 을 반복해서 만들지 않는다).
      if (getToken()) {
        try {
          const since = seenRef.current;
          const events = await memberApi.notifications(since ?? 0);
          const latest = events.reduce(
            (m, e) => Math.max(m, e.seq),
            since ?? 0,
          );
          if (since === null) {
            // 첫 조회 — 지금까지 것은 이미 지난 일이다. 기준만 잡고 띄우지 않는다.
            seenRef.current = latest;
          } else if (events.length) {
            seenRef.current = latest;
            setToasts((prev) => [
              ...prev,
              ...events.map((e) => ({ ...e, id: `${e.task_id}-${e.seq}` })),
            ]);
          }
        } catch {
          // 알림 실패로 화면을 막지 않는다 — 다음 주기에 다시 시도한다.
        }
      }
      timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // 오래된 알림은 스스로 사라진다.
  useEffect(() => {
    if (!toasts.length) return;
    const t = setTimeout(() => setToasts((prev) => prev.slice(1)), SHOW_MS);
    return () => clearTimeout(t);
  }, [toasts]);

  if (!toasts.length) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex flex-col items-center gap-2 px-4">
      {toasts.slice(-3).map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-lg border px-4 py-3 shadow-lg ${tone(t.kind)}`}
          role="status"
        >
          <span aria-hidden className="text-lg leading-none">
            {icon(t.kind)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{t.text}</p>
            {t.book_title ? (
              <p className="truncate text-xs opacity-80">{t.book_title}</p>
            ) : null}
          </div>
          <button
            type="button"
            aria-label="알림 닫기"
            className="text-xs opacity-60 hover:opacity-100"
            onClick={() =>
              setToasts((prev) => prev.filter((x) => x.id !== t.id))
            }
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
