import { useSyncExternalStore } from "react";

// 관제 콘솔에서 현재 선택된 로봇(전역 상태). AdminShell 의 로봇 셀렉터가 setActiveRobot 을
// 호출하고, 각 페이지는 useActiveRobot* 훅으로 구독한다. localStorage 로 새로고침 후에도 유지.
//
// 이 파일은 2026-07-14 프로덕션 빌드 백업(dist.bak.20260714131813/assets/index-*.js)의
// 번들된 코드를 역디코딩해 복원한 버전이다 — 추측이 아니라 실제 배포됐던 로직.
export interface ActiveRobot {
  id: number;
  name: string;
  type: string;
  base: string; // 예: "http://192.168.0.42:9101"
  ai_server_url?: string | null;
}

const ID_KEY = "labi.activeRobotId";
const BASE_KEY = "labi.activeRobotBase";
const NAME_KEY = "labi.activeRobotName";
const TYPE_KEY = "labi.activeRobotType";
const AI_SERVER_KEY = "labi.activeRobotAiServer";

// VITE_ADMIN_API_URL 이 비어 있으면 same-origin(dev 서버 프록시)으로 동작.
const ENV_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

const listeners = new Set<() => void>();
function notify(): void {
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActiveRobotId(): number | null {
  if (typeof localStorage === "undefined") return null;
  const v = localStorage.getItem(ID_KEY);
  return v ? Number(v) : null;
}

export function getActiveRobotName(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(NAME_KEY);
}

// admin-api.ts 의 RobotType 은 "arm"|"pinky"|"other"|"server" 이지만, "server"(중앙서버)는
// 선택 로봇으로 취급하지 않는다 — 원본 그대로.
export function getActiveRobotType(): "arm" | "pinky" | "other" | null {
  if (typeof localStorage === "undefined") return null;
  const v = localStorage.getItem(TYPE_KEY);
  return v === "arm" || v === "pinky" || v === "other" ? v : null;
}

// 중앙 서버(aba_fms_service 백엔드) 베이스. VITE_ADMIN_API_URL 이 없으면 현재 페이지의
// 호스트명 + 포트 9001 로 절대경로를 구성한다 (dev 프록시 없이 다른 기기에서 접속해도 동작).
export function getCentralBase(): string {
  if (ENV_BASE) return ENV_BASE;
  if (typeof window !== "undefined" && window.location.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:9001`;
  }
  return ENV_BASE;
}

// 선택된 로봇의 베이스. 로봇이 선택되어 있지 않으면 ENV_BASE(같은 오리진)로 폴백한다.
export function getRobotBase(): string {
  if (typeof localStorage === "undefined") return ENV_BASE;
  return localStorage.getItem(BASE_KEY) || ENV_BASE;
}

// 팔(arm) 비전 API 전용 AI 서버 베이스.
export function getRobotAiServerBase(): string {
  if (typeof localStorage === "undefined") return ENV_BASE;
  return localStorage.getItem(AI_SERVER_KEY) || ENV_BASE;
}

export function setActiveRobot(robot: ActiveRobot | null): void {
  if (typeof localStorage !== "undefined") {
    if (robot == null) {
      localStorage.removeItem(ID_KEY);
      localStorage.removeItem(BASE_KEY);
      localStorage.removeItem(NAME_KEY);
      localStorage.removeItem(TYPE_KEY);
      localStorage.removeItem(AI_SERVER_KEY);
    } else {
      localStorage.setItem(ID_KEY, String(robot.id));
      localStorage.setItem(BASE_KEY, robot.base);
      localStorage.setItem(NAME_KEY, robot.name);
      localStorage.setItem(TYPE_KEY, robot.type);
      if (robot.ai_server_url) localStorage.setItem(AI_SERVER_KEY, robot.ai_server_url);
      else localStorage.removeItem(AI_SERVER_KEY);
    }
  }
  notify();
}

export function useActiveRobotId(): number | null {
  return useSyncExternalStore(subscribe, getActiveRobotId, () => null);
}
export function useActiveRobotName(): string | null {
  return useSyncExternalStore(subscribe, getActiveRobotName, () => null);
}
export function useActiveRobotType(): "arm" | "pinky" | "other" | null {
  return useSyncExternalStore(subscribe, getActiveRobotType, () => null);
}
export function useActiveRobotBase(): string {
  return useSyncExternalStore(subscribe, getRobotBase, () => ENV_BASE);
}

// 특정 경로가 로봇 온보드가 아니라 별도 AI 비전 서버로 가야 하는지 (팔 카메라/추적/OCR 등).
const AI_SERVER_PREFIXES = [
  "/api/arm/color-pick",
  "/api/arm/face-track/",
  "/api/arm/gesture/",
  "/api/arm/pinky-detect",
  "/api/arm/ocr/",
  "/api/arm/sequences",
  "/api/arm/playback",
  "/api/arm/camera-view",
];
const AI_SERVER_WS_PREFIXES = ["/api/arm/ws/arm", "/api/arm/pinky-detect/ws"];

function hostnameOf(base: string): string {
  try {
    return new URL(base).hostname;
  } catch {
    return base.replace(/^https?:\/\//, "").split(":")[0];
  }
}

// base(보통 선택된 로봇의 베이스) + path 로 HTTP URL 조립. 팔 비전 계열 경로는 AI 서버로
// 라우팅하면서 원래 로봇의 IP 를 ?robot_ip= 쿼리로 붙여, AI 서버가 어느 로봇 카메라인지 안다.
export function buildRobotHttpUrl(base: string, path: string): string {
  if (AI_SERVER_PREFIXES.some((p) => path.startsWith(p))) {
    const aiBase = getRobotAiServerBase().replace(/\/$/, "");
    const sep = path.includes("?") ? "&" : "?";
    return `${aiBase}${path}${sep}robot_ip=${encodeURIComponent(hostnameOf(base))}`;
  }
  const b = base.replace(/\/$/, "");
  return b ? `${b}${path}` : path;
}

export function buildRobotWsUrl(base: string, path: string): string {
  if (AI_SERVER_WS_PREFIXES.some((p) => path.startsWith(p))) {
    const aiBase = getRobotAiServerBase().replace(/\/$/, "");
    const url = new URL(aiBase);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const sep = path.includes("?") ? "&" : "?";
    return `${url.protocol}//${url.host}${path}${sep}robot_ip=${encodeURIComponent(hostnameOf(base))}`;
  }
  const b = base.replace(/\/$/, "");
  if (!b) return path;
  const url = new URL(b);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return `${url.protocol}//${url.host}${path}`;
}
