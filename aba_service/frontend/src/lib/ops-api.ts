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
    idle: number;
    patrol: number;
    working: number;
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

export interface TaskKind {
  key: string;
  label: string;
  desc: string;
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
}

export interface ShelfRow {
  zone: string;
  total: number;
  categories: Record<string, number>;
}

export const CATEGORY_LABEL: Record<string, string> = {
  literature: "문학",
  art: "예술",
  science: "과학",
  humanities: "인문학",
  kids: "유아",
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
  createTask: (body: {
    kind: string;
    book: string;
    pickup: string;
    dropoff: string;
    robot?: string;
    priority?: number;
  }) =>
    opsApi<{ task_id: string; assigned: string | null; warn?: string }>(
      "/api/admin/ops/tasks",
      { method: "POST", body: JSON.stringify(body) },
    ),
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
  search: (q: string) =>
    opsApi<{
      books: AdminBook[];
      members: { id: number; username: string; full_name: string | null }[];
      zones: string[];
    }>(`/api/admin/ops/search?q=${encodeURIComponent(q)}`),
  stats: () =>
    opsApi<{
      loans_by_category: { category: string; count: number }[];
      top_books: { title: string; count: number }[];
      top_members: { username: string; count: number }[];
      requests_by_kind: { kind: string; count: number }[];
      tasks_by_status: { status: string; count: number }[];
      fleet_linked: boolean;
    }>("/api/admin/ops/stats"),
};
