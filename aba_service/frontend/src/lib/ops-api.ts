/**
 * 사서 운영 API 클라이언트 (`/api/admin/ops/*`).
 *
 * 로봇 데이터는 도서관 백엔드가 FMS 를 대신 호출해 내려준다 — 브라우저는 FMS 를 직접
 * 부르지 않는다. FMS 가 죽어도 `linked:false` 로 오고 화면은 계속 떠야 한다.
 */

const TOKEN_KEY = "labi.adminToken";

export async function opsApi<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token =
    typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(TOKEN_KEY);
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let msg = `요청 실패 (${res.status})`;
    try {
      const b = await res.json();
      if (typeof b?.detail === "string") msg = b.detail;
    } catch {
      /* JSON 이 아니면 기본 메시지 */
    }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export interface Dashboard {
  library: {
    books: number;
    books_out: number;
    available_books: number;
    unavailable_books: number;
    members: number;
    active_loans: number;
    overdue: number;
    due_soon: number;
    reservations_waiting: number;
    reservations_ready: number;
  };
  fleet: {
    linked: boolean;
    robots: number;
    working: number;
    charging: number;
    error: number;
    available: number;
    stale: number;
  };
  tasks: {
    linked: boolean;
    pending: number;
    executing: number;
    completed: number;
    failed: number;
  };
}

export interface RobotRow {
  name: string;
  x: number | null;
  y: number | null;
  state: string | null;
  battery: number | null;
  busy: boolean;
  stale: boolean;
  task_id: string;
  task_state: string;
  progress: number;
  goal_vertex: number | null;
}

export interface OrderRow {
  id: string;
  task_type: string;
  requester: string;
  robot: string | null;
  status: string;
  leg_idx: number;
  leg_count: number;
  current_leg: string | null;
  reason: string;
}

/**
 * 작업 종류 정의 — **입력 폼도 여기서 나온다.**
 *
 * 예전에는 8종이 전부 같은 폼이었다. 종류마다 필요한 입력이 다르므로 백엔드
 * (`routers/ops.py::TASK_KINDS`)가 필드 목록을 함께 내려주고 화면은 그대로 그린다.
 * `.tsx` 에 종류 목록을 다시 적지 않는다 — 두 벌을 두면 반드시 어긋난다.
 */
export interface TaskKind {
  key: string;
  label: string;
  desc: string;
  /** order = FMS 주문(task), state = 로봇 모드 변경(복귀·순찰) */
  mode: "order" | "state";
  /** mode=order 일 때 다리 구성: delivery(4다리) | navigate(1다리) */
  order_kind?: "delivery" | "navigate";
  /** mode=state 일 때 목표 상태 */
  target_state?: string;
  /** 화면이 그릴 입력칸: book | cargo | pickup | dropoff */
  fields: string[];
  /** 목적지를 자동으로 채우는 규칙 (진열 = 도서의 zone) */
  auto_dropoff?: string;
  hint?: string;
}

/** 회원 대여 신청 — 사서 승인 대기/처리 내역 한 줄. */
export interface ApprovalRow {
  id: number;
  kind: string;
  approval: "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  book_id: number;
  book_title: string;
  book_in_stock: boolean;
  member_id: number;
  member_username: string;
  member_name: string | null;
  pickup: string;
  dropoff: string;
  fms_task_id: string;
  reject_reason: string | null;
  decided_at: string | null;
  decided_by: string | null;
  created_at: string;
}

export interface AdminBook {
  id: number;
  title_kr: string;
  author: string;
  category: string;
  cover: string;
  zone: string;
  shelf: string;
  in_stock: boolean;
  unavailable: boolean;
}

export interface ShelfRow {
  zone: string;
  total: number;
  categories: Record<string, number>;
  status: { available: number; borrowed: number; unavailable: number };
}

export const CATEGORY_LABEL: Record<string, string> = {
  literature: "문학",
  art: "예술",
  science: "과학",
  humanities: "인문학",
  kids: "유아",
};

/** 표지를 책마다 따로 고르지 않는다 — 분야만 보고 바로 알아보게 분야당 아이콘 하나. */
export const CATEGORY_COVER: Record<string, string> = {
  literature: "📖",
  art: "🎨",
  science: "🔬",
  humanities: "📚",
  kids: "🧸",
};

export const ops = {
  dashboard: () => opsApi<Dashboard>("/api/admin/ops/dashboard"),
  robots: () =>
    opsApi<{
      linked: boolean;
      robots: RobotRow[];
      plugins: Record<string, string>;
    }>("/api/admin/ops/robots"),
  tasks: () =>
    opsApi<{ linked: boolean; orders: OrderRow[]; kinds: TaskKind[] }>(
      "/api/admin/ops/tasks",
    ),
  waypoints: () =>
    opsApi<{
      linked: boolean;
      vertices: Record<string, { x: number; y: number; yaw?: number }>;
      lanes: { from: string; to: string; bidirectional?: boolean }[];
    }>("/api/admin/ops/waypoints"),
  /**
   * 작업 지시. 종류에 따라 응답이 다르다 —
   * `mode:"order"` 면 주문이 생기고, `mode:"state"` 면 로봇 모드만 바뀐다(주문 없음).
   */
  createTask: (body: {
    kind: string;
    book?: string;
    cargo?: string;
    pickup?: string;
    dropoff?: string;
    robot?: string;
    priority?: number;
  }) =>
    opsApi<{
      mode: "order" | "state";
      task_id?: string;
      assigned?: string | null;
      dropoff?: string;
      warn?: string;
      robot?: string;
      state?: string;
      accepted?: boolean;
      current_state?: string;
      reason?: string;
    }>("/api/admin/ops/tasks", { method: "POST", body: JSON.stringify(body) }),
  cancelTask: (id: string) =>
    opsApi<{ ok: boolean }>(`/api/admin/ops/tasks/${id}/cancel`, {
      method: "POST",
    }),
  books: (q = "", category = "") =>
    opsApi<AdminBook[]>(
      `/api/admin/ops/books?q=${encodeURIComponent(q)}&category=${category}`,
    ),
  createBook: (body: Record<string, unknown>) =>
    opsApi<AdminBook>("/api/admin/ops/books", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateBook: (id: number, body: Record<string, unknown>) =>
    opsApi<AdminBook>(`/api/admin/ops/books/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteBook: (id: number) =>
    opsApi<void>(`/api/admin/ops/books/${id}`, { method: "DELETE" }),
  shelves: () => opsApi<ShelfRow[]>("/api/admin/ops/shelves"),
  /** 대여 승인 — 대기 목록 + 최근 처리 내역. */
  approvals: () =>
    opsApi<{ pending: ApprovalRow[]; recent: ApprovalRow[] }>(
      "/api/admin/ops/approvals",
    ),
  /** 승인 — **이때 처음으로 FMS 주문이 생긴다.** */
  approve: (id: number) =>
    opsApi<ApprovalRow>(`/api/admin/ops/approvals/${id}/approve`, {
      method: "POST",
    }),
  reject: (id: number, reason: string) =>
    opsApi<ApprovalRow>(`/api/admin/ops/approvals/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  stats: () =>
    opsApi<{
      loans_by_category: { category: string; count: number }[];
      books_by_category: { category: string; count: number }[];
      top_books: { title: string; count: number }[];
      top_members: { username: string; count: number }[];
      requests_by_kind: { kind: string; count: number }[];
      due_tomorrow: { member_name: string; book_title: string; due_at: string }[];
      tasks_by_status: { status: string; count: number }[];
      tasks_last_7_days: { date: string; completed: number; failed: number }[];
      fleet_linked: boolean;
    }>("/api/admin/ops/stats"),
  /** 작업로그 삭제 — soft delete(hidden=True). 서버가 재수입 안 되게 보장한다. */
  deleteTaskLog: (id: number) =>
    opsApi<void>(`/api/admin/ops/logs/${id}`, { method: "DELETE" }),
  resetTaskLogs: () =>
    opsApi<{ ok: boolean; hidden: number }>("/api/admin/ops/logs/reset", {
      method: "POST",
    }),
  /** 침입이벤트 삭제 — hard delete. */
  deleteIntrusionEvent: (id: number) =>
    opsApi<void>(`/api/admin/ops/security/events/${id}`, { method: "DELETE" }),
  /** 대여승인 이력 삭제 — REJECTED 또는 종료된 APPROVED만 가능(그 외 409). */
  deleteApprovalHistory: (id: number) =>
    opsApi<void>(`/api/admin/ops/approvals/${id}`, { method: "DELETE" }),
  /** 야간 자동 전환 시각 저장. 둘 다 null 이면 스케줄을 끈다. */
  setSecuritySchedule: (night_start: string | null, night_end: string | null) =>
    opsApi<{ night_start: string | null; night_end: string | null }>(
      "/api/admin/ops/security/schedule",
      { method: "POST", body: JSON.stringify({ night_start, night_end }) },
    ),
};
