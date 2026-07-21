/**
 * 회원 API 클라이언트 + 토큰 보관.
 *
 * 관리자(`/api/admin/*`)와 **완전히 분리된** 회원 토큰을 쓴다. 백엔드가 토큰의 `typ` 를
 * 검증하므로 서로의 토큰으로는 401 이 난다 — 여기서도 키를 따로 둔다.
 */

const TOKEN_KEY = "libi.member.token";

export interface Member {
  id: number;
  username: string;
  full_name: string | null;
}

export interface BookBrief {
  id: number;
  title: string;
  author: string;
  cover: string;
  zone: string;
  in_stock: boolean;
}

export interface Loan {
  id: number;
  book: BookBrief;
  status: string;
  borrowed_at: string;
  due_at: string;
  returned_at: string | null;
  extend_count: number;
  days_left: number;
  overdue: boolean;
  due_soon: boolean;
  can_extend: boolean;
}

export interface Reservation {
  id: number;
  book: BookBrief;
  status: string;
  created_at: string;
}

export interface WishlistItem {
  id: number;
  book: BookBrief;
  created_at: string;
}

export interface DeliveryRequestOut {
  id: number;
  book_id: number;
  book_title: string;
  kind: "read" | "borrow";
  pickup: string;
  dropoff: string;
  fms_task_id: string;
  created_at: string;
  status: string | null;
  leg_idx: number | null;
  leg_count: number | null;
}

export function getToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof localStorage === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(
  path: string,
  init: RequestInit = {},
  auth = true,
): Promise<T> {
  const token = auth ? getToken() : null;
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (res.status === 401 && auth) {
    // 만료/무효 토큰은 즉시 버린다 — 이후 요청이 계속 401 을 맞지 않게.
    setToken(null);
    throw new ApiError(401, "로그인이 필요합니다");
  }
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* 본문이 JSON 이 아니면 기본 메시지 */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const memberApi = {
  login: (username: string, password: string) =>
    call<{ access_token: string; member: Member }>(
      "/api/member/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      false,
    ),
  me: () => call<Member>("/api/member/auth/me"),

  loans: () => call<Loan[]>("/api/member/loans"),
  extendLoan: (id: number) =>
    call<Loan>(`/api/member/loans/${id}/extend`, { method: "POST" }),

  reservations: () => call<Reservation[]>("/api/member/reservations"),
  reserve: (bookId: number) =>
    call<Reservation>("/api/member/reservations", {
      method: "POST",
      body: JSON.stringify({ book_id: bookId }),
    }),
  cancelReservation: (id: number) =>
    call<void>(`/api/member/reservations/${id}`, { method: "DELETE" }),

  wishlist: () => call<WishlistItem[]>("/api/member/wishlist"),
  addWishlist: (bookId: number) =>
    call<WishlistItem>("/api/member/wishlist", {
      method: "POST",
      body: JSON.stringify({ book_id: bookId }),
    }),
  removeWishlist: (id: number) =>
    call<void>(`/api/member/wishlist/${id}`, { method: "DELETE" }),

  /** 열람 요청 — 로봇이 자리(테이블)로 가져다 준다. 대출이 아니다. */
  requestRead: (bookId: number, table: string) =>
    call<DeliveryRequestOut>("/api/member/request/read", {
      method: "POST",
      body: JSON.stringify({ book_id: bookId, table }),
    }),
  /** 대여 요청 — 로봇이 안내데스크로 이송하고, 대출 확정은 사서가 한다. */
  requestBorrow: (bookId: number) =>
    call<DeliveryRequestOut>("/api/member/request/borrow", {
      method: "POST",
      body: JSON.stringify({ book_id: bookId }),
    }),
  requests: () => call<DeliveryRequestOut[]>("/api/member/requests"),
};

/** 열람 요청 시 고를 수 있는 자리 — 백엔드 화이트리스트와 같아야 한다. */
export const TABLES: { value: string; label: string }[] = [
  { value: "테이블-1번-상", label: "테이블 1번 (상)" },
  { value: "테이블-1번-좌", label: "테이블 1번 (좌)" },
  { value: "테이블-1번-우", label: "테이블 1번 (우)" },
  { value: "테이블-2번-하", label: "테이블 2번 (하)" },
  { value: "테이블-2번-좌", label: "테이블 2번 (좌)" },
  { value: "테이블-2번-우", label: "테이블 2번 (우)" },
];
