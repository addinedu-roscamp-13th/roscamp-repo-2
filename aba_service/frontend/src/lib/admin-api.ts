// Typed client for the FastAPI admin backend. Stores the JWT in localStorage
// and attaches it as a Bearer token; on 401 it clears the token and bounces
// to the login page.
//
// All requests go to the same-origin `/api/...` prefix (dev: Vite proxy,
// prod: nginx proxy -> :8010). Override only for special setups via
// VITE_ADMIN_API_URL (e.g. an absolute backend URL on another host).
const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

const TOKEN_KEY = "labi.adminToken";

export function getToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof localStorage !== "undefined") localStorage.removeItem(TOKEN_KEY);
}

export type AdminRole = "superadmin" | "admin";

export interface Admin {
  id: number;
  username: string;
  email: string | null;
  full_name: string | null;
  role: AdminRole;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string | null;
}

export interface DayCount {
  date: string;
  count: number;
}

export interface DashboardStats {
  total_admins: number;
  active_admins: number;
  total_conversations: number;
  total_messages: number;
  selected_model: string | null;
  conversations_per_day: DayCount[];
  recent_admins: Admin[];
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  admin: Admin;
}

// --- Dev Center: live DB schema ---
export interface SchemaColumn {
  name: string;
  type: string;
  key: "" | "PK" | "FK" | "UQ";
  nullable: boolean;
  defaultValue: string | null;
  description: string;
}

export interface SchemaTable {
  tableName: string;
  description: string;
  columns: SchemaColumn[];
}

export interface ErdRelation {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
}

export interface ErdResponse {
  tables: SchemaTable[];
  relations: ErdRelation[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  auth = true,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(API_BASE + path, { ...options, headers });

  if (res.status === 401 && auth) {
    clearToken();
    if (
      typeof window !== "undefined" &&
      !window.location.pathname.endsWith("/admin/login")
    ) {
      window.location.href = "/admin/login";
    }
    throw new ApiError(401, "인증이 만료되었습니다. 다시 로그인해주세요.");
  }

  if (!res.ok) {
    let detail = `요청에 실패했습니다 (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface CreateAdminInput {
  username: string;
  password: string;
  email?: string | null;
  full_name?: string | null;
  role: AdminRole;
  is_active: boolean;
}

export interface UpdateAdminInput {
  password?: string;
  email?: string | null;
  full_name?: string | null;
  role?: AdminRole;
  is_active?: boolean;
}

export const adminApi = {
  login: (username: string, password: string) =>
    request<LoginResult>(
      "/api/admin/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      false,
    ),
  me: () => request<Admin>("/api/admin/auth/me"),
  getStats: () => request<DashboardStats>("/api/admin/dashboard/stats"),
  listUsers: (params?: { q?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.skip != null) qs.set("skip", String(params.skip));
    if (params?.limit != null) qs.set("limit", String(params.limit));
    const s = qs.toString();
    return request<{ items: Admin[]; total: number }>(
      `/api/admin/users${s ? `?${s}` : ""}`,
    );
  },
  createUser: (data: CreateAdminInput) =>
    request<Admin>("/api/admin/users", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateUser: (id: number, data: UpdateAdminInput) =>
    request<Admin>(`/api/admin/users/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteUser: (id: number) =>
    request<void>(`/api/admin/users/${id}`, { method: "DELETE" }),
  getTables: () => request<SchemaTable[]>("/api/admin/dev/tables"),
  getErd: () => request<ErdResponse>("/api/admin/dev/erd"),
};
