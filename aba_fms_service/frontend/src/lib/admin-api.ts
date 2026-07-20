import { getCentralBase, getRobotBase } from "./active-robot";

const API_BASE = getCentralBase();

// 로봇 제어 호출은 선택된 로봇 IP 로, 그 외(인증/관리자/로봇 레지스트리)는 중앙 서버로 보낸다.
function baseFor(path: string): string {
  if (path.startsWith("/api/arm/pinky-detect/greeting")) return API_BASE;
  // 경로는 /api/robot/ 으로 시작하지만 이 라우터는 로봇이 아니라 중앙 FMS 에 있다
  // (backend/app/routers/admin_follow.py). 안 걸러내면 로봇 IP 로 가서 404 가 난다.
  if (path.startsWith("/api/robot/admin-follow")) return API_BASE;
  const isRobotControl = path.startsWith("/api/robot/") || path.startsWith("/api/arm/");
  return isRobotControl ? getRobotBase() : API_BASE;
}

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

export type RobotType = "arm" | "pinky" | "other" | "server";

export interface Robot {
  id: number;
  name: string;
  robot_type: RobotType;
  ip_address: string;
  port: number;
  domain_id: number | null;
  ai_server_url: string | null;
  description: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreateRobotInput {
  name: string;
  robot_type: RobotType;
  ip_address: string;
  port: number;
  domain_id?: number | null;
  ai_server_url?: string | null;
  description?: string | null;
  is_active: boolean;
}

export interface UpdateRobotInput {
  name?: string;
  robot_type?: RobotType;
  ip_address?: string;
  port?: number;
  domain_id?: number | null;
  ai_server_url?: string | null;
  description?: string | null;
  is_active?: boolean;
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

export interface RobotResult {
  success: boolean;
  output: string;
  error: string;
}

export interface MotorMoveInput {
  left: number;
  right: number;
  duration: number;
}

export interface LcdImageItem {
  id: number;
  filename: string;
  original_name: string;
  content_type: string | null;
  size_bytes: number;
  created_at: string | null;
}

export interface CameraAnalysis {
  timestamp: number;
  width: number;
  height: number;
  brightness: number;
  motion_score: number;
  edge_density: number;
  avg_rgb: [number, number, number];
}

export interface Nav2Grid {
  width: number;
  height: number;
  resolution: number;
  origin: { x: number; y: number; yaw: number };
  data: number[];
}

// 옛 Flask(origin_x/origin_y) · 새 FastAPI(origin:{x,y,yaw}) 양쪽 응답을 중첩 origin 으로 통일.
export function normalizeOrigin(grid: { origin?: { x: number; y: number; yaw?: number }; origin_x?: number; origin_y?: number; origin_yaw?: number }) {
  if (grid.origin && typeof grid.origin.x === "number") {
    return { x: grid.origin.x, y: grid.origin.y, yaw: grid.origin.yaw ?? 0 };
  }
  return { x: grid.origin_x ?? 0, y: grid.origin_y ?? 0, yaw: grid.origin_yaw ?? 0 };
}

export function normalizeNav2State(s: Nav2State): Nav2State {
  if (s.map) s.map.origin = normalizeOrigin(s.map);
  if (s.local_costmap) s.local_costmap.origin = normalizeOrigin(s.local_costmap);
  if (s.global_costmap) s.global_costmap.origin = normalizeOrigin(s.global_costmap);
  return s;
}

function centralWsUrl(path: string): URL {
  const origin = typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1";
  const url = new URL(API_BASE.replace(/\/$/, "") || origin, origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  return url;
}

export function controlStateWsUrl(robotId: number, navPort = 9001): string {
  const url = centralWsUrl("/api/control/ws/state");
  url.searchParams.set("robot_id", String(robotId));
  url.searchParams.set("nav_port", String(navPort));
  url.searchParams.set("token", getToken() ?? "");
  return url.toString();
}

export function fleetCoordinatorWsUrl(): string {
  const url = centralWsUrl("/api/control/ws/fleet/coordinator");
  url.searchParams.set("token", getToken() ?? "");
  return url.toString();
}

export function fsmStateWsUrl(): string {
  const url = centralWsUrl("/api/fsm/ws/state");
  url.searchParams.set("token", getToken() ?? "");
  return url.toString();
}

export function fleetFeedWsUrl(): string {
  const url = centralWsUrl("/api/fleet/ws/feed");
  url.searchParams.set("token", getToken() ?? "");
  return url.toString();
}

export interface Nav2Pose {
  x: number;
  y: number;
  yaw: number;
}

export interface Nav2Mission {
  status: string;
  current: string | null;
  names: string[];
  loop: boolean;
  schedule_minutes: number;
}

export interface Nav2State {
  map: Nav2Grid | null;
  pose: Nav2Pose | null;
  path: Array<{ x: number; y: number }>;
  local_costmap: Nav2Grid | null;
  global_costmap: Nav2Grid | null;
  battery: { percent: number | null; voltage: number | null };
  mission: Nav2Mission;
}

export type Nav2Locations = Record<string, Nav2Pose>;

export interface WaypointLane {
  from: string;
  to: string;
  bidirectional: boolean;
}
export interface WaypointGraph {
  vertices: Record<string, Nav2Pose>;
  lanes: WaypointLane[];
}

// /api/control/telemetry 항목 — 로봇별 ROS 링크 상태(브릿지 연결·텔레메트리 신선도).
export interface ControlLinkInfo {
  ip: string;
  ros_age_sec: number | null;
  status_age_sec: number | null;
  has_pose: boolean;
  has_map: boolean;
  has_costmap: boolean;
  cmd_subscribers: number | null; // 0 이면 domain_bridge/로봇 미연결 → 명령은 HTTP 폴백
}

export interface LearnedRobotAction {
  id: number;
  name: string;
  description: string | null;
  trigger_phrases: string[];
  robot_scope: string;
  action_type: string;
  params: Record<string, any>;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface LearnedScenarioNode {
  id: string;
  type: string;
  label: string;
  x: number;
  y: number;
  config: Record<string, any>;
}

export interface LearnedScenarioEdge {
  id: string;
  from: string;
  to: string;
}

export interface LearnedRobotScenario {
  id: number;
  name: string;
  description: string | null;
  trigger_phrases: string[];
  nodes: LearnedScenarioNode[];
  edges: LearnedScenarioEdge[];
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export type LearnedRobotActionInput = Omit<LearnedRobotAction, "id" | "created_at" | "updated_at">;
export type LearnedRobotScenarioInput = Omit<LearnedRobotScenario, "id" | "created_at" | "updated_at">;

export interface LearnedRunResult {
  success: boolean;
  requires_confirm?: boolean;
  message?: string;
  response?: string;
  action_type?: string;
  path?: string;
  note?: string | null;
  results?: Array<{ label?: string; action_type?: string; kind?: string; success: boolean; response?: string }>;
  stopped_at?: string;
}

export interface LearnedInterpretResult {
  matched: boolean;
  message?: string;
  kind?: "action" | "scenario";
  id?: number;
  name?: string;
  confidence?: number;
  source?: string;
  robot_number?: number | null;
  target_robot?: string | null;
  needs_robot?: boolean;
  robots?: Array<{ number: number | null; name: string }>;
  result?: LearnedRunResult;
}

export interface ChatMessageResult {
  success: boolean;
  response: string;
  bot_message: string;
  interpretation: LearnedInterpretResult;
}

export interface LcdTextConfig {
  text: string;
  font_name: string;
  font_size: number;
  color: string;
  bg_color: string;
  align: string;
  scroll?: boolean;
  scroll_speed?: number;
}

export interface PinkyGreetingSettings extends LcdTextConfig {
  enabled: boolean;
  duration_seconds: number;
  monitor?: {
    running: boolean;
    robots: Record<string, {
      remaining: number;
      last_error: string | null;
      last_detection_at: number | null;
    }>;
  };
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

  const robotBase = getRobotBase();
  const centralBase = getCentralBase();
  const base = baseFor(path);
  // 로봇 robot_agent 도 이제 /api/robot/* 정식 경로를 지원 — admin 재작성 제거 (2026-07-07)
  const targetPath = path;

  const res = await fetch(base + targetPath, { ...options, headers });

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

async function uploadFile<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const robotBase = getRobotBase();
  const centralBase = getCentralBase();
  const base = baseFor(path);
  // 로봇 robot_agent 도 이제 /api/robot/* 정식 경로를 지원 — admin 재작성 제거 (2026-07-07)
  const targetPath = path;

  const res = await fetch(base + targetPath, { method: "POST", headers, body: form });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/admin/login";
    throw new ApiError(401, "인증이 만료되었습니다.");
  }
  if (!res.ok) {
    let detail = `업로드 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* noop */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as T;
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
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      false,
    ),
  me: () => request<Admin>("/api/auth/me"),
  getStats: () => request<DashboardStats>("/api/dashboard/stats"),
  listUsers: (params?: { q?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.skip != null) qs.set("skip", String(params.skip));
    if (params?.limit != null) qs.set("limit", String(params.limit));
    const s = qs.toString();
    return request<{ items: Admin[]; total: number }>(
      `/api/users${s ? `?${s}` : ""}`,
    );
  },
  createUser: (data: CreateAdminInput) =>
    request<Admin>("/api/users", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateUser: (id: number, data: UpdateAdminInput) =>
    request<Admin>(`/api/users/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteUser: (id: number) =>
    request<void>(`/api/users/${id}`, { method: "DELETE" }),

  // ── 로봇 레지스트리 (IP 관리) ─────────────────────────────────────────────────
  listRobots: (params?: { q?: string; robot_type?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.robot_type) qs.set("robot_type", params.robot_type);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    const s = qs.toString();
    return request<{ items: Robot[]; total: number }>(
      `/api/robots${s ? `?${s}` : ""}`,
    );
  },
  createRobot: (data: CreateRobotInput) =>
    request<Robot>("/api/robots", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateRobot: (id: number, data: UpdateRobotInput) =>
    request<Robot>(`/api/robots/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteRobot: (id: number) =>
    request<void>(`/api/robots/${id}`, { method: "DELETE" }),

  getTables: () => request<SchemaTable[]>("/api/dev/tables"),
  getErd: () => request<ErdResponse>("/api/dev/erd"),

  getPinkyGreetingSettings: () =>
    request<PinkyGreetingSettings>("/api/arm/pinky-detect/greeting"),
  updatePinkyGreetingSettings: (cfg: Partial<PinkyGreetingSettings>) =>
    request<PinkyGreetingSettings>("/api/arm/pinky-detect/greeting", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),

  // ── LCD ──────────────────────────────────────────────────────────────────────
  setEmotion: (emotion: string) =>
    request<RobotResult>("/api/robot/lcd/emotion", {
      method: "POST",
      body: JSON.stringify({ emotion }),
    }),
  lcdStop: () =>
    request<RobotResult>("/api/robot/lcd/stop", { method: "POST" }),

  // 이미지
  lcdUploadImage: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return uploadFile<RobotResult & { image?: LcdImageItem }>("/api/robot/lcd/image", form);
  },
  lcdSelectImage: (filename: string) =>
    request<RobotResult>("/api/robot/lcd/image/select", {
      method: "POST",
      body: JSON.stringify({ filename }),
    }),
  listImages: () => request<{ images: LcdImageItem[] }>("/api/robot/lcd/images"),
  deleteImage: (name: string) =>
    request<{ success: boolean }>(`/api/robot/lcd/images/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  // 텍스트
  lcdText: (cfg: LcdTextConfig) =>
    request<RobotResult>("/api/robot/lcd/text", {
      method: "POST",
      body: JSON.stringify(cfg),
    }),

  // 폰트
  lcdUploadFont: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return uploadFile<{ success: boolean; filename: string }>("/api/robot/lcd/font", form);
  },
  listFonts: () => request<{ fonts: string[] }>("/api/robot/lcd/fonts"),
  deleteFont: (name: string) =>
    request<{ success: boolean }>(`/api/robot/lcd/fonts/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  // ── LED ──────────────────────────────────────────────────────────────────────
  ledFill: (r: number, g: number, b: number) =>
    request<RobotResult>("/api/robot/led/fill", {
      method: "POST",
      body: JSON.stringify({ r, g, b }),
    }),
  ledPixel: (pixels: number[], r: number, g: number, b: number) =>
    request<RobotResult>("/api/robot/led/pixel", {
      method: "POST",
      body: JSON.stringify({ pixels, r, g, b }),
    }),
  ledClear: () =>
    request<RobotResult>("/api/robot/led/clear", { method: "POST" }),
  ledBrightness: (brightness: number) =>
    request<RobotResult>("/api/robot/led/brightness", {
      method: "POST",
      body: JSON.stringify({ brightness }),
    }),

  // ── 부저 ─────────────────────────────────────────────────────────────────────
  playBuzzer: (preset: string) =>
    request<RobotResult>("/api/robot/buzzer", {
      method: "POST",
      body: JSON.stringify({ preset }),
    }),
  playBuzzerMelody: (melody: "fur_elise" | "school_bell") =>
    request<RobotResult>("/api/robot/buzzer/melody/play", {
      method: "POST",
      body: JSON.stringify({ melody }),
    }),
  stopBuzzerMelody: () =>
    request<RobotResult>("/api/robot/buzzer/melody/stop", { method: "POST" }),
  buzzerStatus: () =>
    request<{ running: boolean; melody: string | null }>("/api/robot/buzzer/status"),

  // ── 센서 ─────────────────────────────────────────────────────────────────────
  getSensorUltrasonic: () =>
    request<RobotResult>("/api/robot/sensor/ultrasonic"),
  getSensorBattery: () =>
    request<RobotResult>("/api/robot/sensor/battery"),
  getSensorIr: () =>
    request<RobotResult>("/api/robot/sensor/ir"),
  getSensorImu: () =>
    request<RobotResult>("/api/robot/sensor/imu"),

  // ── 모터 ─────────────────────────────────────────────────────────────────────
  motorMove: (data: MotorMoveInput) =>
    request<RobotResult>("/api/robot/motor/move", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  motorStop: () =>
    request<RobotResult>("/api/robot/motor/stop", { method: "POST" }),

  // ── 자율탐색 ─────────────────────────────────────────────────────────────────
  exploreStart: () =>
    request<{ ok: boolean; running: boolean; status: string; log: string[]; message: string }>(
      "/api/robot/explore/start", { method: "POST" }
    ),
  exploreStop: () =>
    request<{ ok: boolean; running: boolean; status: string; log: string[]; message: string }>(
      "/api/robot/explore/stop", { method: "POST" }
    ),
  exploreStatus: () =>
    request<{ running: boolean; status: string; log: string[]; message: string }>(
      "/api/robot/explore/status"
    ),

  // ── SLAM 맵 생성 (slam_toolbox 프로세스 · 맵 파일 저장/목록) ──────────────────
  robotStatus: () =>
    request<{ ros_active: boolean; processes: Record<string, { running: boolean; pid: number | null }> }>(
      "/api/robot/status"
    ),
  slamStart: () =>
    request<{ running?: boolean; pid?: number | null; error?: string }>(
      "/api/robot/process/slam/start", { method: "POST" }
    ),
  slamStop: () =>
    request<{ running?: boolean; pid?: number | null }>(
      "/api/robot/process/slam/stop", { method: "POST" }
    ),
  robotMapSave: (name: string) =>
    request<{ ok?: boolean; name?: string; path?: string; error?: string }>(
      "/api/robot/map/save", { method: "POST", body: JSON.stringify({ name }) }
    ),
  robotMapReset: () =>
    request<{ ok: boolean }>("/api/robot/map/reset", { method: "POST" }),
  robotMapList: () =>
    request<{ maps: RobotSavedMap[] }>("/api/robot/map/list"),
  robotMapDelete: (name: string) =>
    request<{ ok: boolean; deleted: string[] }>(`/api/robot/map/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // ── 관제시스템 (robot_agent nav 프록시, 기본 9001) ──────────────────────────
  controlState: (robotId: number, navPort = 9001) =>
    request<Nav2State>(`/api/control/state?robot_id=${robotId}&nav_port=${navPort}`).then(normalizeNav2State),
  // 로봇별 ROS 링크 신선도 + 명령 토픽 구독자 수(브릿지 연결 여부). key=pinky1.., 각 항목에 ip 포함.
  controlTelemetry: () =>
    request<Record<string, ControlLinkInfo>>("/api/control/telemetry"),
  controlStateWsUrl,
  controlLocations: (robotId: number, navPort = 9001) =>
    request<Nav2Locations>(`/api/control/locations?robot_id=${robotId}&nav_port=${navPort}`),
  controlGoal: (robotId: number, data: { x: number; y: number; yaw?: number }, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/goal?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  controlHome: (robotId: number, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/home?robot_id=${robotId}&nav_port=${navPort}`, { method: "POST" }),
  controlSlamReset: (robotId: number, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/slam/reset?robot_id=${robotId}&nav_port=${navPort}`, { method: "POST" }),
  controlSaveMap: (robotId: number, name: string, navPort = 9001) =>
    request<{ success: boolean; name: string }>(`/api/control/slam/save-map?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  controlSetLocation: (robotId: number, data: { name: string; x?: number; y?: number; yaw?: number }, navPort = 9001) =>
    request<{ success: boolean; name: string; msg?: string }>(`/api/control/locations?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  controlDeleteLocation: (robotId: number, name: string, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/locations?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "DELETE",
      body: JSON.stringify({ name }),
    }),
  controlGoto: (robotId: number, name: string, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/goto?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  waypointsGet: (robotId: number, navPort = 9001) =>
    request<WaypointGraph>(`/api/control/waypoints?robot_id=${robotId}&nav_port=${navPort}`),
  waypointsSave: (robotId: number, data: WaypointGraph, navPort = 9001) =>
    request<WaypointGraph>(`/api/control/waypoints?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  waypointGoto: (robotId: number, name: string, navPort = 9001) =>
    request<{ success: boolean; name: string; path: string[]; msg?: string }>(
      `/api/control/waypoints/${encodeURIComponent(name)}/goto?robot_id=${robotId}&nav_port=${navPort}`,
      { method: "POST" },
    ),
  controlMissionStart: (robotId: number, data: { names: string[]; loop: boolean }, navPort = 9001) =>
    request<{ success: boolean; names: string[]; loop: boolean; msg?: string }>(`/api/control/mission/start?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  controlMissionStop: (robotId: number, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/mission/stop?robot_id=${robotId}&nav_port=${navPort}`, { method: "POST" }),
  controlScheduleStart: (robotId: number, data: { minutes: number; names: string[]; loop: boolean }, navPort = 9001) =>
    request<{ success: boolean; minutes: number; names: string[]; msg?: string }>(`/api/control/schedule/start?robot_id=${robotId}&nav_port=${navPort}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  controlScheduleStop: (robotId: number, navPort = 9001) =>
    request<{ success: boolean; msg?: string }>(`/api/control/schedule/stop?robot_id=${robotId}&nav_port=${navPort}`, { method: "POST" }),

  // ── 카메라 ───────────────────────────────────────────────────────────────────
  cameraStart: () =>
    request<{ success: boolean; message: string }>("/api/robot/camera/start", { method: "POST" }),
  cameraStop: () =>
    request<{ success: boolean; message: string }>("/api/robot/camera/stop", { method: "POST" }),
  cameraStatus: () =>
    request<{ running: boolean; error: string | null }>("/api/robot/camera/status"),
  cameraAnalysis: () =>
    request<CameraAnalysis>("/api/robot/camera/analysis"),

  // ── 주행로봇 액션/시나리오 학습 ─────────────────────────────────────────────
  listLearnedActions: () =>
    request<{ items: LearnedRobotAction[] }>("/api/robot-learning/actions"),
  createLearnedAction: (data: LearnedRobotActionInput) =>
    request<LearnedRobotAction>("/api/robot-learning/actions", { method: "POST", body: JSON.stringify(data) }),
  updateLearnedAction: (id: number, data: LearnedRobotActionInput) =>
    request<LearnedRobotAction>(`/api/robot-learning/actions/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteLearnedAction: (id: number) =>
    request<{ success: boolean }>(`/api/robot-learning/actions/${id}`, { method: "DELETE" }),
  listLearnedScenarios: () =>
    request<{ items: LearnedRobotScenario[] }>("/api/robot-learning/scenarios"),
  createLearnedScenario: (data: LearnedRobotScenarioInput) =>
    request<LearnedRobotScenario>("/api/robot-learning/scenarios", { method: "POST", body: JSON.stringify(data) }),
  updateLearnedScenario: (id: number, data: LearnedRobotScenarioInput) =>
    request<LearnedRobotScenario>(`/api/robot-learning/scenarios/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteLearnedScenario: (id: number) =>
    request<{ success: boolean }>(`/api/robot-learning/scenarios/${id}`, { method: "DELETE" }),
  runLearnedAction: (id: number, confirm = false) =>
    request<LearnedRunResult>(`/api/robot-learning/actions/${id}/run`, { method: "POST", body: JSON.stringify({ confirm }) }),
  runLearnedScenario: (id: number, confirm = false, robotId?: number) =>
    request<LearnedRunResult>("/api/robot-learning/scenarios/" + id + "/run" + (robotId ? "?robot_id=" + robotId : ""), { method: "POST", body: JSON.stringify({ confirm }) }),
  interpretCommand: (text: string, opts?: { execute?: boolean; confirm?: boolean }) =>
    request<LearnedInterpretResult>("/api/robot-learning/interpret", { method: "POST", body: JSON.stringify({ text, execute: opts?.execute ?? false, confirm: opts?.confirm ?? false }) }),
  stopAllRobots: () =>
    request<{ success: boolean; count: number; results: Array<{ name: string; success: boolean }> }>("/api/robot-learning/stop-all", { method: "POST", body: "{}" }),

  // ── 챗봇 ─────────────────────────────────────────────────────────────────────
  sendChatMessage: (text: string, opts?: { sessionId?: string; execute?: boolean; confirm?: boolean }) =>
    request<ChatMessageResult>("/api/chat/message", {
      method: "POST",
      body: JSON.stringify({
        session_id: opts?.sessionId ?? "admin-robot-control-session",
        user_message: text,
        execute: opts?.execute ?? true,
        confirm: opts?.confirm ?? false,
      }),
    }),
  executeChatCommand: (data: { session_id: string; user_message: string; robot_type: string; action: string; parameters: any }) =>
    request<{ success: boolean; response: string; bot_message: string }>("/api/chat/execute", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getChatHistory: (sessionId: string) =>
    request<Array<{ id: number; role: string; content: string; created_at: string }>>(`/api/chat/history?session_id=${sessionId}`),

  // ── 드라이브 타겟 ─────────────────────────────────────────────────────────────
  getDriveTarget: () =>
    request<{ target: "real" }>("/api/robot/drive/target"),
  setDriveTarget: (target: "real") =>
    request<{ ok: boolean; target: "real" }>("/api/robot/drive/target", {
      method: "POST",
      body: JSON.stringify({ target }),
    }),

  // ── 마커 액션 설정 (아르코) ────────────────────────────────────────────────
  listMarkerActions: () =>
    request<MarkerActionItem[]>("/api/marker-actions"),
  upsertMarkerAction: (markerId: number, body: MarkerActionInput) =>
    request<MarkerActionItem>(`/api/marker-actions/${markerId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteMarkerAction: (markerId: number) =>
    request<{ success: boolean }>(`/api/marker-actions/${markerId}`, { method: "DELETE" }),

  // 관제 구역 도착 액션 설정 (robot_id + 구역명 단위, 중앙서버 DB 저장)
  listLocationActions: (robotId: number) =>
    request<LocationActionItem[]>(`/api/control/location-actions?robot_id=${robotId}`),
  upsertLocationAction: (robotId: number, name: string, body: LocationActionInput) =>
    request<LocationActionItem>(`/api/control/location-actions/${encodeURIComponent(name)}?robot_id=${robotId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteLocationAction: (robotId: number, name: string) =>
    request<{ success: boolean; name: string }>(`/api/control/location-actions/${encodeURIComponent(name)}?robot_id=${robotId}`, { method: "DELETE" }),

  // 근접 안전 코디네이터 (멀티 관제)
  fleetCoordinatorWsUrl,
  getFleetCoordinator: () =>
    request<FleetCoordinatorState>("/api/control/fleet/coordinator"),
  updateFleetCoordinator: (body: { enabled?: boolean; min_distance?: number; clear_distance?: number; priorities?: Record<number, number>; evasion_enabled?: boolean }) =>
    request<FleetCoordinatorState>("/api/control/fleet/coordinator", { method: "PUT", body: JSON.stringify(body) }),
  fleetResume: (robotId: number) =>
    request<{ success: boolean; robot_id: number; msg: string }>(`/api/control/fleet/resume?robot_id=${robotId}`, { method: "POST" }),

  // FSM + BT (2단계)
  fsmStateWsUrl,
  fsmModel: () => request<{ ok: boolean; model: FsmModel }>("/api/fsm/model"),
  fsmState: (robotId: string) =>
    request<FsmStateResponse>(
      `/api/fsm/state?robot_id=${encodeURIComponent(robotId)}`,
    ),
  fsmTransition: (input: FsmTransitionInput) =>
    request<FsmTransitionResult>("/api/fsm/transition", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  fsmHistory: (robotId: string, limit = 20) =>
    request<{ ok: boolean; items: FsmHistoryItem[] }>(
      `/api/fsm/history?robot_id=${encodeURIComponent(robotId)}&limit=${limit}`,
    ),

  // 관리자 추종 승인 기록. 추종 제어는 ai_service↔로봇 직결이라 FSM 에 안 잡히므로,
  // 이 기록이 "이 로봇이 지금 추종 중"인지 아는 유일한 단서다.
  adminFollowStatus: () =>
    request<{ following: AdminFollowGrant[] }>(
      "/api/robot/admin-follow/status",
    ),
  adminFollowRelease: (robotId: string) =>
    request<{ released: boolean; reason: string | null }>(
      "/api/robot/admin-follow/release",
      { method: "POST", body: JSON.stringify({ robot_id: robotId }) },
    ),

  // libi_fleet 배차·교통 코어
  fleetFeedWsUrl,
  fleetMeta: () => request<FleetMeta>("/api/fleet/meta"),
  fleetSnapshot: () =>
    request<{ ok: boolean; snapshot: FleetSnapshot }>("/api/fleet/snapshot"),
  fleetSubmitTask: (input: FleetTaskInput) =>
    request<FleetTaskResult>("/api/fleet/task", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  fleetSetMode: (robot: string, mode: string) =>
    request<FleetOpResult>("/api/fleet/mode", {
      method: "POST",
      body: JSON.stringify({ robot, mode }),
    }),
  fleetSetBattery: (robot: string, value: number) =>
    request<FleetOpResult>("/api/fleet/battery", {
      method: "POST",
      body: JSON.stringify({ robot, value }),
    }),
  fleetSetPlugins: (dispatcher: string, traffic: string) =>
    request<FleetPluginsResult>("/api/fleet/plugins", {
      method: "POST",
      body: JSON.stringify({ dispatcher, traffic }),
    }),
  fleetReloadNavgraph: () =>
    request<FleetOpResult>("/api/fleet/navgraph/reload", { method: "POST" }),

  // 배달 주문 오케스트레이터 (composite task 시퀀서, fleet_node 위) — /api/fleet/order*
  fleetOrders: () => request<{ orders: OrderSnapshot[] }>("/api/fleet/orders"),
  fleetCreateOrder: (input: CreateOrderInput) =>
    request<{ ok: boolean; task_id: string }>("/api/fleet/order", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  fleetAssignOrder: (taskId: string, robot: string) =>
    request<{ ok: boolean; task: OrderSnapshot }>(
      `/api/fleet/order/${taskId}/assign`,
      { method: "POST", body: JSON.stringify({ robot }) },
    ),
  fleetAdvanceOrder: (taskId: string) =>
    request<{ ok: boolean; task: OrderSnapshot }>(
      `/api/fleet/order/${taskId}/advance`,
      { method: "POST" },
    ),
  fleetCancelOrder: (taskId: string) =>
    request<{ ok: boolean; task: OrderSnapshot }>(
      `/api/fleet/order/${taskId}/cancel`,
      { method: "POST" },
    ),
};

// ── FSM + BT (2단계) ─────────────────────────────────────────────────────────
// 상태 이름과 간선은 절대 여기에 적지 않는다. 전부 GET /api/fsm/model 응답에서 온다
// (INSTRUCTION.md: "전이 박스와 화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다").
// backend/tests/test_no_frontend_state_literals.py 가 이를 감시한다.

export interface FsmEdge {
  source: string;
  target: string;
  trigger: string;
  guard: string;
}

export interface FsmModel {
  states: string[];
  descriptions: Record<string, string>;
  edges: FsmEdge[];
  allowed_targets: Record<string, string[]>;
  mermaid: string;
  start: string;
  any: string;
}

/** py_trees 노드 상태 — FSM 상태 이름과는 무관하다. */
export type BtNodeStatus = "SUCCESS" | "FAILURE" | "RUNNING" | "INVALID";

export interface FsmTreeNode {
  name: string;
  status: BtNodeStatus;
  children: FsmTreeNode[];
}

export interface FsmSnapshot {
  current_state: string | null;
  previous_state: string | null;
  active_branch: string | null;
  error_code: string;
  battery_percent: number | null;
  is_docked: boolean | null;
  tree: FsmTreeNode | null;
  transitioned_at: number;
  stale: boolean;
}

export interface FsmStateResponse {
  ok: boolean;
  robot_id: string;
  snapshot: FsmSnapshot | null;
  allowed_targets: string[];
  reason?: string;
}

export interface FsmTransitionInput {
  robot_id: string;
  target_state: string;
  force: boolean;
}

export interface FsmTransitionResult {
  accepted: boolean;
  current_state: string;
  reason: string;
}

export interface FsmHistoryItem {
  id: number;
  robot_id: string;
  from_state: string;
  to_state: string;
  forced: boolean;
  accepted: boolean;
  reason: string;
  admin_username: string;
  created_at: string;
}

export interface AdminFollowGrant {
  robot_id: string;
  granted_at: number; // epoch seconds
  state_stale: boolean; // 승인은 살아있는데 로봇 상태 수신이 끊긴 경우
}

export type MarkerActionType =
  | "none" | "dock" | "rotate" | "move" | "lcd_emotion" | "lcd_text" | "lcd_image";

export interface MarkerActionItem {
  marker_id: number;
  label: string | null;
  action_type: MarkerActionType;
  params: Record<string, unknown> | null;
  enabled: boolean;
}

export interface MarkerActionInput {
  label?: string | null;
  action_type: MarkerActionType;
  params?: Record<string, unknown> | null;
  enabled: boolean;
}

export interface LocationActionItem {
  name: string;
  action_type: MarkerActionType;
  params: Record<string, unknown> | null;
  enabled: boolean;
}

export interface LocationActionInput {
  action_type: MarkerActionType;
  params?: Record<string, unknown> | null;
  enabled: boolean;
}

export interface FleetRobotState {
  id: number;
  name: string;
  base: string;
  online: boolean;
  pose: { x: number; y: number } | null;
  moving: boolean;
  status: string | null;
  priority: number;
  near_id: number | null;
  near_name: string | null;
  near_dist: number | null;
  held: boolean;
  resumable: boolean;
}

export interface FleetHold {
  reason: string;
  peer_id: number;
  peer_name: string;
  distance: number;
  since: number;
}

export interface FleetCoordinatorState {
  enabled: boolean;
  min_distance: number;
  clear_distance: number;
  interval: number;
  last_tick: number;
  last_error: string | null;
  robots: FleetRobotState[];
  holds: Record<string, FleetHold>;
  priorities: Record<string, number>;
  evasion_enabled: boolean;
}

export interface RobotSavedMap {
  name: string;
  yaml: string;
  pgm: string | null;
  mtime: number;
  size_kb: number;
}

// ── libi_fleet 배차·교통 코어 ─────────────────────────────────────────────────
// 상태 8종은 여기에 적지 않는다 — GET /api/fleet/meta 가 libi_modes 정의를 내려준다.

export interface FleetMeta {
  ok: boolean;
  states: string[];
  dispatchers: string[];
  traffics: string[];
  domain_id: number;
}

/**
 * fleet_node 는 로봇의 상태·배터리를 어떤 토픽으로도 발행하지 않는다.
 * 그래서 `state`/`battery` 는 "이 패널로 마지막에 설정한 값"이고, 출처가 `*_source` 에 실려 온다.
 * `"unknown"` 이면 아직 아무도 설정하지 않아 fleet 내부값을 모른다는 뜻이다.
 */
export interface FleetRobotRow {
  name: string;
  x: number | null;
  y: number | null;
  task_id: string;
  task_state: string;
  progress: number;
  busy: boolean;
  goal_vertex: number | null;
  held_nodes: number[];
  state: string | null;
  state_source: "panel" | "unknown";
  battery: number | null;
  battery_source: "panel" | "unknown";
  stale: boolean;
}

export interface FleetTaskEvent {
  task_id: string;
  state: string;
  robot_id: string;
  progress: number;
  at: number;
}

export interface FleetSnapshot {
  robots: FleetRobotRow[];
  tasks: FleetTaskEvent[];
  occupancy: Record<string, string>;
  routes: Record<string, number[][]>;
  goals: Record<string, number>;
  plugins: { dispatcher: string; traffic: string };
  linked: boolean;
  domain_id: number;
  stale: boolean;
}

export interface FleetTaskInput {
  dropoff: string;
  robot?: string;
  arm_actions?: number;
  task_type?: string;
}

export interface FleetTaskResult {
  ok: boolean;
  accepted: boolean;
  task_id: string;
  /** fleet_node 가 준 사유를 그대로 — bad_goal_vertex / robot_stopped / insufficient_battery … */
  reason: string;
}

export interface FleetOpResult {
  ok: boolean;
  reason: string;
}

export interface FleetPluginsResult extends FleetOpResult {
  active_dispatcher: string;
  active_traffic: string;
}

// ── 배달 주문 오케스트레이터 (composite task) ─────────────────────────────────
// 형식은 backend app/fleet_orchestrator.py Task.snapshot() 과 1:1. fleet_node(SubmitTask)
// 보다 한 층 위 — 주문 1건을 다리(navigate/perform_action)로 분해해 하나씩 굴린다.
export type OrderStatus =
  | "PENDING" // 큐 대기, 미배정
  | "ASSIGNED" // 로봇 배정됨, 첫 다리 직전
  | "EXECUTING" // 다리 하나 진행 중
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface OrderSnapshot {
  id: string;
  task_type: string;
  requester: string;
  priority: number;
  robot: string | null;
  status: OrderStatus;
  leg_idx: number;
  leg_count: number;
  current_leg: "navigate" | "perform_action" | null;
  reason: string;
}

export interface CreateOrderInput {
  book: string;
  pickup: string;
  dropoff: string;
  requester?: string;
  priority?: number;
}
