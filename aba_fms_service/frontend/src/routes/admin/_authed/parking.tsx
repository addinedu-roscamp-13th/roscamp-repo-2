import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, Circle, Crosshair, Loader2, Navigation, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { RobotConsole } from "@/components/admin/RobotConsole";
import { Button } from "@/components/ui/button";
import { buildRobotWsUrl, useActiveRobotBase, useActiveRobotId, useActiveRobotName, useActiveRobotType } from "@/lib/active-robot";
import { adminApi, normalizeNav2State, type Nav2State, type Robot } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/parking")({ component: ParkingPage });

const NAV_PORT = 9001;
const DOCK_BASE = "/api/robot/dock";
const LINE_BASE = "/api/robot/line";
// 아르코 마커 실측 한 변 길이(m) — 2026-07-06 자로 실측 5cm. 로봇 카메라 캘리브레이션과 함께
// pose(미터 단위 거리·횡오프셋) 산출에 쓰인다. 마커를 다시 인쇄하면 이 값도 갱신할 것.
const MARKER_LEN_M = 0.05;
// IR 테이프 판정 임계값(이하=테이프 위). 로봇별 실측값.
// 로봇1/3: 테이프 ~350, 바닥 ~1500~1800 → 650. 로봇2: 테이프 ~790~1185, 바닥 ~2470~2860 → 1700.
const DEFAULT_IR_WHITE_MAX = 650;
const ROBOT_IR_WHITE_MAX: Record<string, number> = { "192.168.0.42": 1700 };
const SENSOR_BASE = "/api/robot/sensor";
const CAM_SNAPSHOT = "/api/robot/camera/snapshot";
// MJPEG 실시간 스트림 — 스냅샷 폴링(250ms)로는 주행 중 화면이 뚝뚝 끊겨서 전환
const CAM_STREAM = "/api/robot/camera/stream";
const ARRIVE_DIST = 0.35;
const NAV_ARRIVE_DIST = 0.20;
const LINE_DETECT_GATE_DIST = 0.50;
const ARRIVE_TIMEOUT = 120_000;
const APPROACH_YAW_TOL = 0.22;
const APPROACH_YAW_TIMEOUT = 9_000;
const DEFAULT_PARKING_ZONE = "E";
const DEFAULT_LINE_ENTRY_ZONE = "D";
const DEFAULT_PARKING_APPROACH_ZONE = "C";
// ── 마커 ID 는 현장에서 바뀔 수 있으므로 코드에 고정하지 않는다. ─────────────────
// 실제 값은 사이트(아래 '마커 역할 설정' 패널)에서 지정하고 DB `rc_marker_actions.params`
// 에 저장된다:  params.dock_marker = true   → 도킹 대상 마커(마커 ID 입력이 비었을 때의 기본값)
//               params.guide_turn_deg = 45  → 유도 마커 + 도킹마커 방향 회전량(도, 양수=우회전)
// 아래 상수는 DB 에 아무것도 지정돼 있지 않을 때만 쓰는 폴백이다.
const DEFAULT_DOCK_MARKER_ID = 1;
// 유도마커 → 도킹마커 진입 회전. 값은 "예상 회전량(도)"이며 실제 정지는
// 도킹마커가 카메라에 잡히는 순간이다(개루프 타이머 오차 보정). 방향은 항상 우회전(시계방향).
const DEFAULT_GUIDE_TURN_TO_DOCK: Record<number, number> = { 2: 45, 3: 45, 4: 180, 5: 180 };
// 유도마커 회전량이 이 값 이상이면 "반대편(180°)" 유도로 본다 — 라인 주차 진입 분기에 쓰임.
const GUIDE_TURN_REVERSE_DEG = 90;
// 직접 주차: 접근 전 제자리 회전을 걸지 말지는 ex 가 아니라 **실제 방위각**(markerBearingDeg)으로 판단한다.
// 이 카메라는 fx=471 / 480px 프레임 → 수평 반화각이 atan(240/471) ≈ 27° 뿐인 좁은 화각이라
// ex 가 금방 포화된다. 실측(2026-07-29): 물리적으로 19° 밖에 안 틀어진 마커가 ex -0.81 로 찍혔고,
// ex 만 보고 제자리 회전을 걸었더니 바로 앞 마커를 두고 헛돌았다(yaw 9.9°→33.9°, 마커 80회 로스트).
// 이 각도 미만이면 회전 없이 곧장 P 제어 조향으로 접근한다.
// 12°로 잡은 근거: 이 구동계는 바퀴 하나라도 PWM 32 밑이면 멈춰서 '저속 곡선'이 불가능하다.
// 안쪽 바퀴를 살리려면 |ang| ≤ lin/7 이라 곡률이 κ≈0.14 rad/m — 0.8m 이동 중 약 6.5° 밖에 못 꺾는다.
// 그보다 크게 틀어져 있으면 곡선으로는 절대 못 붙으므로, 짧게 제자리 회전으로 각을 지운 뒤 직진한다.
const PRE_ROTATE_BEARING_DEG = 12;
// 조향 P 이득을 키우는 기준 중심오차. 이 값을 넘어도 주차를 막지는 않는다.
const DIRECT_PARK_EX_WARN = 0.5;

// ── pose 기반 기하 주차 (POSE DOCK) ──────────────────────────────────────────
// ex(이미지 중심오차)만 쓰는 제어는 근본적으로 부족하다. ex 는 "마커가 화면 어디에 있나"만
// 알려줄 뿐, "마커가 어느 방향을 보고 있나"를 모른다. 실측(2026-07-29):
//   ex -0.015 (완벽 정면) 인데 marker yaw +19° → axis_lat -0.262m
//   즉 마커는 정면에 보이지만 로봇은 마커 정면축에서 26cm 옆으로 벗어나 있었다.
//   ex 만 보고 직진하면 19° 비뚤어진 채로 처박는다.
// 그래서 solvePnP 가 주는 6-DOF(x, z, yaw, 법선 nx/nz)로 기하를 직접 푼다:
//   axis_lat = x·nz − z·nx                      (마커 정면축에서 옆으로 벗어난 거리)
//   W        = (x + nx·d_hold, z + nz·d_hold)   (마커 정면축 위 진입 경유점)
//   θ        = atan2(W.x, W.z)                  (경유점 방위각, + = 오른쪽)
// 이 식은 현재 자세에 맞춘 튜닝이 아니라 일반식이라 어느 위치에서든 성립한다.
//
// 실행은 '제자리 회전 → 직진' 프리미티브만 쓴다. 이 구동계는 바퀴 하나라도 PWM≈32 밑이면
// 멈춰서 저속 곡선 주행이 물리적으로 불가능한데, 두 프리미티브는 양 바퀴를 대칭으로 굴려
// 항상 정지마찰을 넘긴다. 대신 로봇 쪽 move/rotate 는 시간 기반 개루프(duration = 거리/가정속도)
// 라 오차가 쌓이므로, 한 스텝을 짧게 끊고 매번 pose 를 다시 재서 재계획한다(플래너 레벨 폐루프).
const POSE_DOCK = {
  targetDistM: 0.20,     // 마커 앞 최종 정지 거리
  standoffM: 0.40,       // 진입 경유점을 마커 앞 최대 이만큼 띄운다
  axisTolM: 0.05,        // 이 안쪽이면 '정면축에 올라탔다'고 보고 최종 접근으로 전환
  bearingTolDeg: 6,      // 최종 접근 전 마커를 이 각도 안으로 맞춘다
  minTurnDeg: 4,         // 개루프 오차보다 작은 회전은 건너뛴다
  maxLegM: 0.30,         // 한 번에 직진하는 최대 거리 — 짧게 끊어야 개루프 오차가 안 쌓인다
  minLegM: 0.06,
  turnSpeed: 0.4,        // /api/robot/rotate speed (노드 테스트에서 쓰는 값과 동일)
  moveSpeed: 0.35,       // /api/robot/move speed → 실제 약 0.0875 m/s
  maxSteps: 24,          // 무한루프 방지. 회전량을 화각 안으로 잘라 쓰므로 스텝 수가 늘어난다.
  // ★ 한 번에 도는 각도를 제한해 마커가 화각 밖으로 나가지 않게 한다.
  //   이 카메라 수평 반화각은 atan(240/471) ≈ 27°. 여유를 둬 20° 안쪽에 남긴다.
  //   제한 없이 계획대로 돌리면 실행 중 최대 방위각이 85°까지 튀어 196케이스 중 143개에서
  //   마커를 놓쳤다(시뮬레이션, 2026-07-29). 잘라 돌면 스텝이 늘 뿐 궤적은 같다.
  keepInViewDeg: 20,
  // 직진도 같은 이유로 제한한다. 마커를 비껴 지나가면 방위각이 벌어져 화각 밖으로 나간다.
  //   이동 후 방위각 = atan2(|x|, z−d) ≤ keepMoveDeg  →  d ≤ z − |x|/tan(keepMoveDeg)
  // 단 minLegM 은 항상 보장한다(전진을 아예 못 해 못 붙는 것보다, 놓치고 복구하는 편이 낫다).
  // 시뮬레이션 567케이스 튜닝 결과(2026-07-29): 회전20°/직진26° → 수렴 100%, 평균 9.1스텝,
  // 평균 복구 0.26회. 직진을 20°로 더 조이면 전진을 못 해 수렴이 93.7%로 떨어지고,
  // 제한을 아예 빼면 57.3%까지 붕괴한다.
  keepMoveDeg: 26,
  // 그래도 놓쳤을 때: 마지막으로 본 방위각 + 그 뒤 명령한 회전량으로 현재 방위각을 추정해 되돌린다.
  lostRetries: 3,
  lostScanDeg: 15,       // 추정으로도 못 찾으면 이만큼씩 좌우로 훑는다
} as const;
const GUIDE_TURN_PULSE = { left: 45, right: -45, duration: 0.24 }; // 우회전 1펄스(개루프) — 크고 빠르게
const GUIDE_TURN_DEG_PER_PULSE = 15; // 1펄스당 대략 회전량(실주행 튜닝값) — 클수록 펄스 수↓ 속도↑
// 노드 테스트: 마커 트리거 → 로봇 내장 POST /api/robot/rotate 로 정밀 회전(odom yaw 폐루프, 자동 정지).
// angle 음수 = 우회전(시계방향). speed 0~1(낮을수록 느리고 안전).
const NODE_TURN_SPEED = 0.4;
const MARKER_GUIDED_RIGHT_SCAN_PULSES: Array<{ left: number; right: number; duration: number; label: string }> = Array.from(
  { length: 24 },
  (_, i) => ({ left: 32, right: -32, duration: 0.22, label: `유도 마커 기준 빠른 우측 탐색 ${i + 1}` }),
);
const MARKER_GUIDE_DETECT_ATTEMPTS = 8;
const NAV_READY_TIMEOUT = 45_000;
const HYBRID_MARKER_POLL_MS = 800;
const HYBRID_SCAN_PULSES: Array<{ left: number; right: number; duration: number; label: string }> = [
  { left: -24, right: 24, duration: 0.30, label: "좌측" },
  { left: -24, right: 24, duration: 0.30, label: "좌측" },
  { left: -24, right: 24, duration: 0.30, label: "좌측" },
  { left: -24, right: 24, duration: 0.30, label: "좌측" },
  { left: -24, right: 24, duration: 0.30, label: "좌측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: 24, right: -24, duration: 0.30, label: "우측" },
  { left: -24, right: 24, duration: 0.30, label: "중앙 복귀" },
  { left: -24, right: 24, duration: 0.30, label: "중앙 복귀" },
  { left: -24, right: 24, duration: 0.30, label: "중앙 복귀" },
  { left: -24, right: 24, duration: 0.30, label: "중앙 복귀" },
  { left: -24, right: 24, duration: 0.30, label: "중앙 복귀" },
];
const LINE_ACQUIRE_PULSES: Array<{ left: number; right: number; duration: number; label: string }> = [
  { left: 20, right: 20, duration: 0.35, label: "앞쪽 저속 탐색 1" },
  { left: 20, right: 20, duration: 0.35, label: "앞쪽 저속 탐색 2" },
  { left: 20, right: 20, duration: 0.35, label: "앞쪽 저속 탐색 3" },
  { left: 20, right: 20, duration: 0.35, label: "앞쪽 저속 탐색 4" },
  { left: 20, right: 20, duration: 0.35, label: "앞쪽 저속 탐색 5" },
  ...Array.from({ length: 28 }, (_, i) => ({ left: -22, right: 22, duration: 0.28, label: `360도 회전 탐색 ${i + 1}` })),
  { left: -20, right: -20, duration: 0.30, label: "뒤쪽 확인 1" },
  { left: -20, right: -20, duration: 0.30, label: "뒤쪽 확인 2" },
  { left: -20, right: -20, duration: 0.30, label: "뒤쪽 확인 3" },
  ...Array.from({ length: 10 }, (_, i) => ({ left: 22, right: -22, duration: 0.28, label: `반대 방향 복귀 탐색 ${i + 1}` })),
  { left: 18, right: 18, duration: 0.35, label: "마지막 전진 확인" },
];
const DICTS = ["DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_50", "DICT_6X6_50", "DICT_7X7_50"];
// 색 주차: 색 이름 → 표시 색. 마커별 색은 marker_actions.params.park_color 에 저장한다.
const PARK_COLORS = [
  { name: "빨강", hex: "#ef4444" },
  { name: "주황", hex: "#f97316" },
  { name: "노랑", hex: "#eab308" },
  { name: "초록", hex: "#22c55e" },
  { name: "청록", hex: "#14b8a6" },
  { name: "파랑", hex: "#3b82f6" },
  { name: "보라", hex: "#8b5cf6" },
  { name: "분홍", hex: "#ec4899" },
];
const TERMINAL_DOCK_PHASES = new Set(["done", "timeout", "lost", "error", "stopped", "idle"]);

type ParkingMode = "front" | "rear";
type PrecisionMode = "hybrid" | "line" | "aruco";
type FlowPhase = "idle" | "navigating" | "docking" | "done" | "error" | "stopped";
type DockStatus = {
  running: boolean;
  phase: string;
  message: string;
  telemetry?: Record<string, unknown>;
};
type MarkerPose = { x_m: number; z_m: number; yaw_deg: number; nx?: number; nz?: number };
type Marker = { id: number; cx: number; cy: number; ex: number; size_frac: number; skew: number; side_px: number; pose?: MarkerPose };

// 카메라 초점거리(px, 480px 프레임 기준 — 2026-07-06 실측 캘리브레이션 fx=471).
// 마커 중심 방위각(로봇 정면축 기준 좌우 각도) 계산에 쓴다. +는 오른쪽, -는 왼쪽.
const CAM_FX_PX = 471;
// 예상 경로 시뮬레이션 — 로봇(aruco_dock)의 ex 비례 조향 제어와 동일한 게인을 써야
// 화면의 점선 = 실제 주행 경로가 된다. 로봇 쪽 게인을 바꾸면 여기도 맞출 것.
const SIM_GAIN = { kp: 0.30, angMax: 0.15, lin: 0.16, deadband: 0.05 };
// 핑키프로 실측 치수(m): 바퀴 좌우 간격(track)·차폭 — 곡률과 차폭 가이드 계산용
const PINKY_TRACK_M = 0.10;
const PINKY_WIDTH_M = 0.13;
const bearingFromEx = (ex: number) => (Math.atan2(ex * 240, CAM_FX_PX) * 180) / Math.PI;
const markerBearingDeg = (m: Marker) =>
  m.pose ? (Math.atan2(m.pose.x_m, m.pose.z_m) * 180) / Math.PI : bearingFromEx(m.ex);
const fmtDeg = (deg: number) => `${deg >= 0 ? "+" : ""}${deg.toFixed(1)}°`;
// 마커 정면축에서 로봇이 옆으로 벗어난 거리(m). +는 축의 오른쪽.
const axisLatM = (p: MarkerPose) =>
  p.nx != null && p.nz != null ? p.x_m * p.nz - p.z_m * p.nx : null;
const normRad = (rad: number) => Math.atan2(Math.sin(rad), Math.cos(rad));
type DetectResp = {
  frame?: { width: number; height: number };
  markers?: Marker[];
};

function modeLabel(mode: ParkingMode) {
  return mode === "front" ? "전면 주차" : "후면 주차";
}

// 흐름 단계별 배너 표현 (색/점/라벨)
const FLOW_META: Record<FlowPhase, { label: string; tone: string; dot: string }> = {
  idle: { label: "대기 중", tone: "border-slate-200 bg-slate-50 text-slate-600", dot: "bg-slate-400" },
  navigating: { label: "구역으로 이동 중", tone: "border-sky-300 bg-sky-50 text-sky-800", dot: "bg-sky-500 animate-pulse" },
  docking: { label: "정밀 주차 중", tone: "border-amber-300 bg-amber-50 text-amber-800", dot: "bg-amber-500 animate-pulse" },
  done: { label: "주차 완료", tone: "border-emerald-300 bg-emerald-50 text-emerald-800", dot: "bg-emerald-500" },
  error: { label: "오류", tone: "border-red-300 bg-red-50 text-red-800", dot: "bg-red-500" },
  stopped: { label: "정지됨", tone: "border-slate-300 bg-slate-100 text-slate-700", dot: "bg-slate-500" },
};

// 로봇이 돌려주는 도킹 phase 코드 → 사람이 읽는 한국어
const DOCK_PHASE_LABEL: Record<string, string> = {
  idle: "대기", starting: "시작 중", docking: "정렬·접근 중", axis_guidance: "가이드 축 합류 중", turning: "회전 중(후면 전환)",
  coast: "마커 순간 놓침(진행 유지)", no_marker: "마커 대기 중", search: "마커 재탐색(저속 회전)", done: "완료",
  align_turn: "측면 정렬(저속 회전)", center_align: "마커 중심 맞춤", align_wait: "측/후면 — 수동 정렬 필요",
  blind_advance: "순간 놓침(직진 유지)", wall_crawl: "초음파 접근 중",
  backing: "후진 밀착 중", line_align: "라인 중심 재정렬 중",
  initializing: "초기화 중", tracing: "흰 테이프 추종 중", searching: "라인 탐색 중", no_frame: "카메라 신호 없음",
  timeout: "시간 초과", lost: "마커 상실", error: "오류", stopped: "정지",
  no_calib: "보정값 없음",
};

function dockPhaseLabel(phase?: string) {
  if (!phase) return "-";
  return DOCK_PHASE_LABEL[phase] ?? phase;
}

function distSourceLabel(src: unknown) {
  if (src === "ultrasonic_cm") return "초음파/IR 센서";
  if (src === "pose_m") return "마커 거리";
  if (src === "marker_size") return "마커 크기";
  return "-";
}

type StatusEvent = { id: number; at: string; phase: string; message: string; tone: "ok" | "warn" | "danger" | "info" | "idle" };

function statusTone(phase?: string, wallCm?: unknown, ex?: unknown): StatusEvent["tone"] {
  if (["error", "lost", "timeout", "blocked", "no_frame"].includes(phase ?? "")) return "danger";
  if (["align_wait", "center_align", "edge_align", "axis_guidance", "no_marker", "wall_crawl", "pre_align", "final_align"].includes(phase ?? "")) return "warn";
  if (typeof wallCm === "number" && wallCm <= 10) return "danger";
  if (typeof ex === "number" && Math.abs(ex) > 0.45) return "warn";
  if (phase === "done") return "ok";
  if (phase === "idle" || !phase) return "idle";
  return "info";
}

const STATUS_TONE_CLASS: Record<StatusEvent["tone"], { shell: string; badge: string; dot: string }> = {
  ok: { shell: "border-emerald-300 bg-emerald-50 text-emerald-900", badge: "bg-emerald-600 text-white", dot: "bg-emerald-500" },
  warn: { shell: "border-amber-400 bg-amber-50 text-amber-950", badge: "bg-amber-500 text-white", dot: "bg-amber-500 animate-pulse" },
  danger: { shell: "border-red-500 bg-red-50 text-red-950", badge: "bg-red-600 text-white", dot: "bg-red-600 animate-pulse" },
  info: { shell: "border-sky-300 bg-sky-50 text-sky-950", badge: "bg-sky-600 text-white", dot: "bg-sky-500 animate-pulse" },
  idle: { shell: "border-slate-200 bg-white text-slate-700", badge: "bg-slate-600 text-white", dot: "bg-slate-400" },
};

function ParkingPage() {
  const robotId = useActiveRobotId();
  const robotBase = useActiveRobotBase();
  const robotName = useActiveRobotName();
  const robotType = useActiveRobotType();
  const canControl = robotType === "pinky" && robotId != null;

  const [zone, setZone] = useState(DEFAULT_PARKING_ZONE);
  const [mode, setMode] = useState<ParkingMode>("front");
  const [precisionMode, setPrecisionMode] = useState<PrecisionMode>("aruco");
  const [testBusy, setTestBusy] = useState(false); // 노드 테스트 버튼 실행 중 표시(FSM과 분리)
  const [dictionary, setDictionary] = useState("DICT_6X6_50");
  const [markerId, setMarkerId] = useState("1");
  const [frontTarget, setFrontTarget] = useState(0.4);
  const [rearTarget, setRearTarget] = useState(0.18);
  const [wallTargetCm, setWallTargetCm] = useState(3);
  const [linMax, setLinMax] = useState(0.14);
  const [angMax, setAngMax] = useState(0.28);
  const [phase, setPhase] = useState<FlowPhase>("idle");
  const [message, setMessage] = useState("대기 중");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [navLeg, setNavLeg] = useState<"idle" | "toE" | "toD" | "toC">("idle");
  const [arucoStatus, setArucoStatus] = useState<DockStatus | null>(null);
  const [lineStatus, setLineStatus] = useState<DockStatus | null>(null);
  const [detect, setDetect] = useState<DetectResp | null>(null);
  const [lineDetect, setLineDetect] = useState<{ found: boolean; ir?: { left: number; center: number; right: number; obstacle: boolean } | null; on_tape?: Record<string, boolean>; camera?: { found?: boolean; offset?: number | null; angle?: number | null; bands?: Record<string, unknown>; error?: string } | null; wall_cm?: number | null; source?: string; } | null>(null);
  const [sensor, setSensor] = useState<{ dist_cm: number | null; ir: { left: number; center: number; right: number; obstacle: boolean } | null } | null>(null);
  const [statusEvents, setStatusEvents] = useState<StatusEvent[]>([]);
  // MJPEG 스트림 끊김 시 <img> 재마운트용 시퀀스 (onError 에서 증가)
  const [camEpoch, setCamEpoch] = useState(0);
  const [directNavPose, setDirectNavPose] = useState<{ x: number; y: number; yaw?: number } | null>(null);
  const [directNavMissionStatus, setDirectNavMissionStatus] = useState<string | null>(null);
  // 색 주차: 색↔마커 매핑(marker_actions.params.park_color)
  const [colorMap, setColorMap] = useState<Array<{ marker_id: number; color: string }>>([]);
  // 마커 역할(사이트 설정값): 유도마커 id→회전각, 기본 도킹마커 id. 둘 다 DB marker_actions 에서 로드.
  const [guideTurnMap, setGuideTurnMap] = useState<Record<number, number> | null>(null);
  const [dockMarkerCfgId, setDockMarkerCfgId] = useState<number | null>(null);
  // 유도마커 설정 입력 폼
  const [guideFormId, setGuideFormId] = useState("");
  const [guideFormDeg, setGuideFormDeg] = useState("45");
  // 실시간 색 인식: 마커별로 카메라가 지금 감지한 색 이름 (perceive 폴링 결과)
  const [liveColors, setLiveColors] = useState<Record<number, string>>({});

  const dockStartedRef = useRef(false);
  const lineQueuedRef = useRef(false);
  const arucoStartedRef = useRef(false);
  const markerScanRef = useRef(false);
  const specialMarkerLineFlowRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [webRtcReady, setWebRtcReady] = useState(false);
  const [webRtcFailed, setWebRtcFailed] = useState(false);

  const [wsState, setWsState] = useState<Nav2State | null>(null);
  const stateQuery = useQuery({
    queryKey: ["parking", "state", robotId],
    queryFn: () => adminApi.controlState(robotId!, NAV_PORT),
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
  const currentState = wsState ?? stateQuery.data ?? null;

  useEffect(() => {
    if (!canControl || !robotId) {
      setWsState(null);
      return;
    }
    const ws = new WebSocket(adminApi.controlStateWsUrl(robotId, NAV_PORT));
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) setWsState(normalizeNav2State(payload.state as Nav2State));
      } catch {
        /* malformed state frame */
      }
    };
    return () => ws.close();
  }, [canControl, robotId]);

  const robotsQuery = useQuery({
    queryKey: ["parking", "robots", robotId],
    queryFn: () => adminApi.listRobots({ limit: 200 }),
    enabled: canControl,
    retry: false,
  });

  const locationsQuery = useQuery({
    queryKey: ["parking", "locations", robotId],
    queryFn: () => adminApi.controlLocations(robotId!, NAV_PORT),
    enabled: canControl,
    staleTime: Infinity,
    retry: false,
  });

  const locations = locationsQuery.data ?? {};
  const zoneNames = useMemo(() => Object.keys(locations).sort(), [locations]);
  const selectedLocation = zone ? locations[zone] : null;
  const zoneELocation = locations[DEFAULT_PARKING_ZONE] ?? null;
  const zoneDLocation = locations[DEFAULT_LINE_ENTRY_ZONE] ?? null;
  const zoneCLocation = locations[DEFAULT_PARKING_APPROACH_ZONE] ?? null;
  const parkingApproachLocation = zone === DEFAULT_PARKING_ZONE ? zoneCLocation : null;
  const navigationTarget = useMemo(() => {
    if (!selectedLocation) return null;
    if (!parkingApproachLocation) return selectedLocation;
    return {
      ...selectedLocation,
      yaw: Math.atan2(parkingApproachLocation.y - selectedLocation.y, parkingApproachLocation.x - selectedLocation.x),
    };
  }, [parkingApproachLocation, selectedLocation]);
  const robotRecord = useMemo(() => (robotsQuery.data?.items ?? []).find((r: Robot) => r.id === robotId && r.is_active) ?? null, [robotId, robotsQuery.data?.items]);
  const dockBase = robotRecord ? "http://" + robotRecord.ip_address + ":" + robotRecord.port : robotBase;
  const activeIrWhiteMax = robotRecord?.ip_address ? (ROBOT_IR_WHITE_MAX[robotRecord.ip_address] ?? DEFAULT_IR_WHITE_MAX) : DEFAULT_IR_WHITE_MAX;

  useEffect(() => {
    if (!canControl || phase !== "navigating") {
      setDirectNavPose(null);
      setDirectNavMissionStatus(null);
      return;
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(dockBase + "/api/state");
        if (!res.ok) return;
        const body = await res.json();
        const p = body?.pose;
        if (!cancelled) {
          setDirectNavMissionStatus(typeof body?.mission?.status === "string" ? body.mission.status : null);
          if (typeof p?.x === "number" && typeof p?.y === "number") {
            setDirectNavPose({ x: p.x, y: p.y, yaw: typeof p.yaw === "number" ? p.yaw : undefined });
          }
        }
      } catch {
        /* direct nav pose fallback failed */
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 800);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [canControl, dockBase, phase]);
  const pose = phase === "navigating" && directNavPose ? directNavPose : currentState?.pose ?? null;
  const distance = pose && selectedLocation ? Math.hypot(pose.x - selectedLocation.x, pose.y - selectedLocation.y) : null;
  const distanceToE = pose && zoneELocation ? Math.hypot(pose.x - zoneELocation.x, pose.y - zoneELocation.y) : null;
  const distanceToD = pose && zoneDLocation ? Math.hypot(pose.x - zoneDLocation.x, pose.y - zoneDLocation.y) : null;
  const distanceToC = pose && zoneCLocation ? Math.hypot(pose.x - zoneCLocation.x, pose.y - zoneCLocation.y) : null;
  const lineDetectNearParkingZones = [distanceToC, distanceToD, distanceToE].some((d) => d != null && d <= LINE_DETECT_GATE_DIST);
  const navMissionStatus = phase === "navigating" && directNavMissionStatus ? directNavMissionStatus : currentState?.mission?.status ?? null;
  const navGoalStopped = navMissionStatus === "idle";
  const activeTarget = mode === "front" ? frontTarget : rearTarget;
  const targetMarkerId = markerId.trim() === "" ? null : Number(markerId);
  // 사이트 설정값 우선, 없으면 폴백 상수. (마커 id 는 현장에서 바뀔 수 있어 코드에 박지 않는다)
  const guideTurns = guideTurnMap && Object.keys(guideTurnMap).length > 0 ? guideTurnMap : DEFAULT_GUIDE_TURN_TO_DOCK;
  const dockFallbackId = dockMarkerCfgId ?? DEFAULT_DOCK_MARKER_ID;
  const guideTurnRef = useRef(guideTurns);
  guideTurnRef.current = guideTurns;
  const dockFallbackRef = useRef(dockFallbackId);
  dockFallbackRef.current = dockFallbackId;
  // 유도마커를 회전량으로 분류: 반대편(≥90°) 진입용 / C 방향(<90°) 진입용. UI 버튼 라벨·동작에 사용.
  const guideIdsAll = Object.keys(guideTurns).map(Number).sort((a, b) => a - b);
  const reverseGuideIds = guideIdsAll.filter((id) => Math.abs(guideTurns[id]) >= GUIDE_TURN_REVERSE_DEG);
  const forwardGuideIds = guideIdsAll.filter((id) => Math.abs(guideTurns[id]) < GUIDE_TURN_REVERSE_DEG);
  const reverseGuideDeg = reverseGuideIds.length ? Math.abs(guideTurns[reverseGuideIds[0]]) : 180;
  const forwardGuideDeg = forwardGuideIds.length ? Math.abs(guideTurns[forwardGuideIds[0]]) : 45;
  const guideLabel = (ids: number[]) => (ids.length ? `마커 ${ids.join("·")}` : "유도 마커 미설정");
  const targetMarker = (detect?.markers ?? []).find((m) => targetMarkerId == null || m.id === targetMarkerId) ?? null;
  const activeStatus = phase === "docking" || lineStatus?.running === true || arucoStatus?.running === true
    ? (lineStatus?.running ? lineStatus : arucoStatus)
    : null;
  const running = phase === "navigating" || phase === "docking" || activeStatus?.running === true;

  useEffect(() => {
    if (!canControl) return;
    let cancelled = false;
    let pc: RTCPeerConnection | null = null;

    const waitIceComplete = (conn: RTCPeerConnection) => new Promise<void>((resolve) => {
      if (conn.iceGatheringState === "complete") {
        resolve();
        return;
      }
      const onState = () => {
        if (conn.iceGatheringState === "complete") {
          conn.removeEventListener("icegatheringstatechange", onState);
          resolve();
        }
      };
      conn.addEventListener("icegatheringstatechange", onState);
      window.setTimeout(() => {
        conn.removeEventListener("icegatheringstatechange", onState);
        resolve();
      }, 1800);
    });

    const start = async () => {
      setWebRtcReady(false);
      setWebRtcFailed(false);
      try {
        if (!("RTCPeerConnection" in window)) throw new Error("WebRTC unsupported");
        pc = new RTCPeerConnection({ iceServers: [] });
        pc.addTransceiver("video", { direction: "recvonly" });
        pc.ontrack = (event) => {
          if (cancelled || !videoRef.current) return;
          videoRef.current.srcObject = event.streams[0];
          setWebRtcReady(true);
        };
        pc.onconnectionstatechange = () => {
          if (!pc) return;
          if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
            setWebRtcFailed(true);
            setWebRtcReady(false);
          }
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitIceComplete(pc);
        const local = pc.localDescription;
        if (!local) throw new Error("WebRTC offer 생성 실패");
        const res = await fetch(dockBase + "/api/robot/camera/webrtc/offer?w=320&q=50&fps=12", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sdp: local.sdp, type: local.type }),
        });
        if (!res.ok) throw new Error("WebRTC answer 실패: HTTP " + res.status);
        const answer = await res.json();
        if (cancelled || !pc) return;
        await pc.setRemoteDescription(answer);
      } catch {
        if (!cancelled) {
          setWebRtcFailed(true);
          setWebRtcReady(false);
        }
        if (pc) void pc.close();
      }
    };

    void start();
    return () => {
      cancelled = true;
      setWebRtcReady(false);
      if (videoRef.current) videoRef.current.srcObject = null;
      if (pc) void pc.close();
    };
  }, [canControl, dockBase, camEpoch]);

  useEffect(() => {
    if (zone && zoneNames.includes(zone)) return;
    if (zoneNames.includes(DEFAULT_PARKING_ZONE)) setZone(DEFAULT_PARKING_ZONE);
    else if (zoneNames.length > 0) setZone(zoneNames[0]);
  }, [zone, zoneNames]);

  useEffect(() => {
    if (!canControl) return;
    const ws = new WebSocket(buildRobotWsUrl(dockBase, `${DOCK_BASE}/ws/status`));
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setArucoStatus((data?.state ?? data) as DockStatus);
      } catch {
        /* malformed status frame */
      }
    };
    return () => ws.close();
  }, [canControl, dockBase]);

  useEffect(() => {
    if (!canControl) return;
    const ws = new WebSocket(buildRobotWsUrl(dockBase, `${LINE_BASE}/ws/status`));
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLineStatus((data?.state ?? data) as DockStatus);
      } catch {
        /* malformed status frame */
      }
    };
    return () => ws.close();
  }, [canControl, dockBase]);

  useEffect(() => {
    if (!canControl) return;
    const ws = new WebSocket(buildRobotWsUrl(dockBase, LINE_BASE + "/ws/detect?ir_white_max=" + activeIrWhiteMax));
    ws.onmessage = (event) => {
      try {
        setLineDetect(JSON.parse(event.data));
      } catch {
        /* malformed line detect frame */
      }
    };
    return () => ws.close();
  }, [canControl, dockBase, activeIrWhiteMax]);

  useEffect(() => {
    if (!canControl) return;
    const ws = new WebSocket(buildRobotWsUrl(dockBase, `${SENSOR_BASE}/ws/status`));
    ws.onmessage = (event) => {
      try {
        setSensor(JSON.parse(event.data));
      } catch {
        /* malformed sensor frame */
      }
    };
    return () => ws.close();
  }, [canControl, dockBase]);

  useEffect(() => {
    if (!canControl || running) return;
    const path = `${DOCK_BASE}/ws/detect?dictionary=${encodeURIComponent(dictionary)}&marker_len_m=${MARKER_LEN_M}`;
    const ws = new WebSocket(buildRobotWsUrl(dockBase, path));
    ws.onmessage = (event) => {
      try {
        setDetect(JSON.parse(event.data));
      } catch {
        /* malformed dock detect frame */
      }
    };
    return () => ws.close();
  }, [canControl, dictionary, dockBase, running]);

  // 마커까지 추정 거리(cm): pose(z_m) 우선, 없으면 핀홀 근사 z = fx·L/side_px
  const distCmOf = (m: Marker) =>
    m.pose ? m.pose.z_m * 100 : (CAM_FX_PX * MARKER_LEN_M / Math.max(m.side_px, 1)) * 100;
  // 로봇 진행축에서 마커가 좌우로 벗어난 거리(cm). +는 오른쪽.
  const latCmOf = (m: Marker) =>
    m.pose ? m.pose.x_m * 100 : distCmOf(m) * Math.tan((bearingFromEx(m.ex) * Math.PI) / 180);

  const fetchDetect = useCallback(async (): Promise<DetectResp | null> => {
    try {
      const res = await fetch(`${dockBase}${DOCK_BASE}/detect?dictionary=${encodeURIComponent(dictionary)}&marker_len_m=${MARKER_LEN_M}`);
      if (!res.ok) return null;
      const body = (await res.json()) as DetectResp;
      setDetect(body);
      return body;
    } catch {
      return null;
    }
  }, [dictionary, dockBase]);

  const fetchLineDetect = useCallback(async () => {
    try {
      const res = await fetch(dockBase + LINE_BASE + "/detect?ir_white_max=" + activeIrWhiteMax);
      if (!res.ok) return null;
      const body = (await res.json()) as typeof lineDetect;
      setLineDetect(body);
      return body;
    } catch {
      return null;
    }
  }, [dockBase, activeIrWhiteMax]);

  const fetchCameraLineDetect = useCallback(async () => {
    try {
      const res = await fetch(dockBase + LINE_BASE + "/detect?source=camera&roi_top=0.55&thresh=0&min_area_px=180&ir_white_max=" + activeIrWhiteMax);
      if (!res.ok) return null;
      const body = (await res.json()) as typeof lineDetect;
      setLineDetect(body);
      return body;
    } catch {
      return null;
    }
  }, [dockBase, activeIrWhiteMax]);

  const recoverNavigation = useCallback(async () => {
    await Promise.allSettled([
      robotId != null ? adminApi.controlMissionStop(robotId, NAV_PORT) : Promise.resolve(),
      fetch(`${dockBase}/api/mission/stop`, { method: "POST" }),
      fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }),
      fetch(`${dockBase}${LINE_BASE}/stop`, { method: "POST" }),
      fetch(`${dockBase}${DOCK_BASE}/stop`, { method: "POST" }),
      // 로봇 온보드 pose 주차(parkp)도 같이 정지 — 안 그러면 정지 버튼이 안 먹는다.
      fetch(dockBase + "/api/robot/parkp/stop", { method: "POST" }),
    ]);
  }, [dockBase, robotId]);

  // park_dock(로봇 온보드) 프리미티브 실행 헬퍼 — 브라우저에서 모터를 직접 펄스로 돌리는 대신
  // 로봇 라우터(/api/robot/park/*)에 시작만 요청하고 상태(running/phase/message)를 폴링한다.
  // 실제 감지→판단→구동 폐루프는 로봇 위에서 고속으로 돈다(네트워크 왕복 제거).
  const runParkPrimitive = useCallback(
    async (
      endpoint: "rotate" | "search_line" | "wall_approach" | "start",
      body: Record<string, unknown>,
      timeoutMs: number,
    ): Promise<string> => {
      const res = await fetch(`${dockBase}/api/robot/park/${endpoint}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`park/${endpoint} 시작 실패: HTTP ${res.status}`);
      const deadline = Date.now() + timeoutMs;
      let phase = "starting";
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        try {
          const st = await fetch(`${dockBase}/api/robot/park/status`).then((r) => r.json());
          if (typeof st?.state?.phase === "string") phase = st.state.phase;
          if (typeof st?.state?.message === "string" && st.state.message) setMessage(st.state.message);
          if (st?.state?.running !== true) return phase; // 프리미티브 완료
        } catch {
          /* 상태 폴링 일시 실패 — 계속 시도 */
        }
      }
      return phase;
    },
    [dockBase],
  );

  const alignParkingApproachYaw = useCallback(async () => {
    if (!robotId || !navigationTarget || !parkingApproachLocation) return;
    const targetYaw = navigationTarget.yaw ?? 0;
    // 브라우저 P제어 루프 제거 → 로봇 온보드 park_dock 회전(odom yaw)에 위임. 중앙은 목표 각도만 전달.
    await adminApi.controlMissionStop(robotId, NAV_PORT).catch(() => undefined);
    setMessage(`${zone || DEFAULT_PARKING_ZONE} 구역 도착 · ${DEFAULT_PARKING_APPROACH_ZONE} 방향 정렬 중(로봇 온보드)`);
    await runParkPrimitive(
      "rotate",
      {
        rotate_ref: "odom",
        approach_yaw_deg: (targetYaw * 180) / Math.PI,
        rotate_tol_deg: (APPROACH_YAW_TOL * 180) / Math.PI,
        rotate_timeout_s: APPROACH_YAW_TIMEOUT / 1000,
        manage_nav2: false,
      },
      APPROACH_YAW_TIMEOUT + 4000,
    );
  }, [runParkPrimitive, navigationTarget, parkingApproachLocation, robotId, zone]);

  const ensureLineVisible = useCallback(async () => {
    const checkLineFast = async () => {
      const fresh = await fetchLineDetect();
      return fresh?.found === true;
    };

    const checkLine = async () => {
      let hits = 0;
      for (let i = 0; i < 3; i += 1) {
        if (await checkLineFast()) hits += 1;
        if (i < 2) await new Promise((resolve) => window.setTimeout(resolve, 120));
      }
      return hits >= 2;
    };

    setPhase("docking");

    if (await checkLine()) return true;

    // 브라우저 펄스 스캔(마커 유도/일반 360도) 제거 → 로봇 온보드 park_dock 테이프 탐색에 위임.
    // park_dock._search_line 은 우측 우선 → 좌측 확대 스윕으로 IR 테이프를 로컬 루프로 획득한다.
    setMessage(`${zone || DEFAULT_PARKING_ZONE} 구역 도착 · 로봇 온보드 테이프 탐색 중`);
    // nav2 는 끄지 않는다(manage_nav2:false): 탐색 중 재시작하면 위치추정(AMCL)이 리셋돼
    // 주차 후 지도/구역 클릭 주행이 먹통이 된다. 미션은 이미 중단돼 cmd_vel 충돌 없음.
    await runParkPrimitive(
      "search_line",
      { line: { ir_white_max: activeIrWhiteMax }, search_timeout_s: 14, manage_nav2: false },
      18000,
    );
    if (await checkLineFast()) {
      setMessage(`${zone || DEFAULT_PARKING_ZONE} 구역 테이프 감지 · 라인 주차 시작`);
      return true;
    }

    setPhase("error");
    setMessage(`${zone || DEFAULT_PARKING_ZONE} 구역 근처에서 테이프를 찾지 못했습니다. E 구역 도착 위치와 테이프 시작점을 다시 확인하세요.`);
    return false;
  }, [runParkPrimitive, activeIrWhiteMax, fetchLineDetect, zone]);


  // ── 내비게이션식 주차 가이드 오버레이 ──
  // 마커 중심축(세로 점선) + 로봇→마커 유도 곡선(이 선이 곧 도킹 제어가 따라가는 경로)
  // + 남은 거리 눈금 + cm/도 수치 HUD. 도킹 중에는 detect 대신 텔레메트리(ex·크기)로 그린다.
  useEffect(() => {
    const canvas = canvasRef.current;
    const frame = detect?.frame;
    if (!canvas || !frame) return;
    if (canvas.width !== frame.width) canvas.width = frame.width;
    if (canvas.height !== frame.height) canvas.height = frame.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const W = frame.width;
    const H = frame.height;
    ctx.clearRect(0, 0, W, H);

    // 로봇 진행축(화면 중앙 세로 기준선)
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(W / 2, 0);
    ctx.lineTo(W / 2, H);
    ctx.stroke();
    ctx.setLineDash([]);

    // 검출된 모든 마커 박스 — 색이 지정된 마커는 그 색으로 칠하고 색 이름을 라벨에 붙인다.
    for (const m of detect.markers ?? []) {
      const side = m.side_px;
      const x = m.cx - side / 2;
      const y = H - m.cy - side / 2;
      const selected = targetMarkerId == null || m.id === targetMarkerId;
      // 실시간 감지 색(liveColors) 우선, 없으면 지정 매핑(colorMap)
      const liveColor = liveColors[m.id];
      const mappedColor = colorMap.find((c) => c.marker_id === m.id)?.color;
      const shownColor = liveColor ?? mappedColor;
      const shownHex = shownColor ? PARK_COLORS.find((c) => c.name === shownColor)?.hex : undefined;
      ctx.strokeStyle = shownHex ?? (selected ? "#22c55e" : "#f59e0b");
      ctx.lineWidth = selected ? 4 : 2;
      ctx.strokeRect(x, y, side, side);
      // 색이 감지/지정된 마커: 반투명 채움으로 강조
      if (shownHex) {
        ctx.save();
        ctx.globalAlpha = 0.18;
        ctx.fillStyle = shownHex;
        ctx.fillRect(x, y, side, side);
        ctx.restore();
      }
      ctx.fillStyle = ctx.strokeStyle;
      ctx.font = "bold 18px monospace";
      // 실시간 감지 색은 "색명↻", 지정 매핑만 있으면 "색명" 으로 표시
      const colorLabel = liveColor ? ` · ${liveColor}↻` : mappedColor ? ` · ${mappedColor}` : "";
      ctx.fillText("id " + m.id + colorLabel, x, Math.max(18, y - 8));
    }

    // 4-1) 라인 주차 가이드 — 간결판 (복잡하다는 피드백으로 재설계)
    //      후방카메라처럼 차폭 레일 2줄이 "실제 벽(초음파) 위치"까지 이어지고,
    //      벽 위치에 정지선 + 남은 거리 라벨 하나만 표시한다.
    const lineRunning = lineStatus?.running === true;
    if (lineDetect && (lineDetect.found || lineRunning || phase === "docking")) {
      const lineTele = (lineStatus?.telemetry ?? {}) as Record<string, unknown>;
      const wallCm = typeof lineDetect.wall_cm === "number" ? lineDetect.wall_cm
        : typeof lineTele.wall_cm === "number" ? (lineTele.wall_cm as number) : null;
      // 지면 투영 v(z) = A + B/z — 로봇1 스냅샷 실측 보정 (2026-07-07):
      // 벽 46.5cm ↔ y147, 벽 59.5cm ↔ y127 두 점으로 역산. 이 상수 덕에 벽 정지선이
      // 화면의 "실제 벽 위치"에 붙는다. 카메라 장착 각도가 바뀌면 이 두 값만 재보정.
      const Ag = 55;
      const Bg = 42.6;
      const zNear = Bg / (H - 2 - Ag); // 화면 최하단이 보는 가장 가까운 바닥 (≈14cm)
      const zWall = wallCm != null ? Math.max(wallCm / 100, zNear + 0.03) : 0.6;
      const fx = CAM_FX_PX * (W / 480); // fx 캘리브레이션은 480px 프레임 기준
      const uOf = (x: number, z: number) => W / 2 + (fx * x) / z;
      const vOf = (z: number) => Ag + Bg / z;
      const railX = PINKY_WIDTH_M / 2;
      // 근접도에 따라 전체 톤 하나로: 여유 초록 / 20cm 이내 노랑 / 10cm 이내 빨강
      const tone = wallCm == null ? "#22c55e" : wallCm <= 10 ? "#ef4444" : wallCm <= 20 ? "#eab308" : "#22c55e";

      // 차폭 레일 (지면 직선의 투영은 직선 — 끝점 2개로 충분)
      ctx.lineCap = "round";
      ctx.shadowColor = "rgba(0,0,0,0.5)";
      ctx.shadowBlur = 3;
      ctx.strokeStyle = tone;
      ctx.lineWidth = 5;
      for (const s of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(uOf(s * railX, zNear), vOf(zNear));
        ctx.lineTo(uOf(s * railX, zWall), vOf(zWall));
        ctx.stroke();
      }

      // 벽 위치 정지선 + 남은 거리 라벨
      const vw = vOf(zWall);
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(uOf(-railX * 1.15, zWall), vw);
      ctx.lineTo(uOf(railX * 1.15, zWall), vw);
      ctx.stroke();
      ctx.shadowBlur = 0;
      if (wallCm != null) {
        ctx.font = "bold 15px monospace";
        const label = `${wallCm.toFixed(0)}cm`;
        const lw = ctx.measureText(label).width + 14;
        ctx.fillStyle = "rgba(2,6,23,0.78)";
        ctx.fillRect(W / 2 - lw / 2, vw - 26, lw, 20);
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        ctx.fillText(label, W / 2, vw - 11);
        ctx.textAlign = "left";
      }
    }

    // 4-2) 테이프 인식 상태 배지 — 항상 표시 (하단 좌측)
    if (lineDetect) {
      const ot = (lineDetect as { on_tape?: Record<string, boolean> }).on_tape;
      const seen = ot ? (["left", "center", "right"] as const).filter((k) => ot[k]) : [];
      const label = lineDetect.found
        ? `테이프 인식${seen.length ? " (" + seen.map((s) => (s === "left" ? "좌" : s === "center" ? "중앙" : "우")).join("·") + ")" : ""}`
        : "테이프 미인식";
      ctx.font = "bold 14px sans-serif";
      const tw = ctx.measureText(label).width + 30;
      const bx2 = 8;
      const by2 = H - 34;
      ctx.fillStyle = "rgba(2,6,23,0.78)";
      ctx.fillRect(bx2, by2, tw, 26);
      ctx.strokeStyle = lineDetect.found ? "#22c55e" : "#ef4444";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(bx2, by2, tw, 26);
      ctx.fillStyle = lineDetect.found ? "#22c55e" : "#ef4444";
      ctx.beginPath();
      ctx.arc(bx2 + 14, by2 + 13, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#e2e8f0";
      ctx.fillText(label, bx2 + 25, by2 + 18);
    }

    // 가이드 대상: 도킹 중엔 텔레메트리(실시간 ex) 우선, 아니면 detect의 선택 마커
    const tele = running ? activeStatus?.telemetry : null;
    let mx: number, my: number, sidePx: number, ex: number;
    let distCm: number | null, latCm: number | null, bearing: number, yawDeg: number | null;
    if (tele && (typeof tele.ex === "number" || typeof tele.offset === "number")) {
      ex = (typeof tele.ex === "number" ? tele.ex : tele.offset) as number;
      mx = (W / 2) * (1 + ex);
      my = H * 0.4; // 텔레메트리에는 세로 좌표가 없어 화면 40% 높이에 가상 배치
      sidePx = typeof tele.dist === "number" && (tele.dist as number) < 1 ? (tele.dist as number) * W : 30;
      distCm = typeof tele.wall_cm === "number" ? (tele.wall_cm as number)
        : sidePx > 1 ? (CAM_FX_PX * MARKER_LEN_M / sidePx) * 100 : null;
      bearing = bearingFromEx(ex);
      latCm = distCm != null ? distCm * Math.tan((bearing * Math.PI) / 180) : null;
      yawDeg = typeof tele.yaw_deg === "number" ? (tele.yaw_deg as number) : null;
    } else if (targetMarker) {
      // 대기 중이거나(폴링 detect) 도킹 초기라 텔레메트리에 아직 ex가 없을 때
      mx = targetMarker.cx;
      my = H - targetMarker.cy;
      sidePx = targetMarker.side_px;
      ex = targetMarker.ex;
      distCm = distCmOf(targetMarker);
      latCm = latCmOf(targetMarker);
      bearing = markerBearingDeg(targetMarker);
      yawDeg = targetMarker.pose?.yaw_deg ?? null;
    } else {
      return;
    }

    const robotX = W / 2;
    const robotY = H - 4;
    const centered = Math.abs(ex) <= 0.10;
    const guideColor = centered ? "#22c55e" : Math.abs(ex) <= 0.35 ? "#eab308" : "#ef4444";

    // 1) 마커 중심축(가이드 선) — 이 세로선 위에 로봇 진행축을 맞추면 정면 주차.
    //    색 지정 마커면 그 색으로 가이드 선을 그린다("색상 인식" 시각화).
    const _axisColorName = colorMap.find((c) => c.marker_id === targetMarkerId)?.color;
    ctx.strokeStyle = (_axisColorName && PARK_COLORS.find((c) => c.name === _axisColorName)?.hex) || "rgba(34,211,238,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 5]);
    ctx.beginPath();
    ctx.moveTo(mx, Math.max(0, my - sidePx));
    ctx.lineTo(mx, H);
    ctx.stroke();
    ctx.setLineDash([]);

    // 2) 예상 주행 경로 (점점점): 로봇의 ex 비례 조향 제어를 지면 좌표에서 그대로
    //    시뮬레이션해 로봇이 실제로 그리며 갈 궤적을 점선으로 투영한다.
    if (distCm != null && latCm != null && distCm > 8) {
      const zm = distCm / 100; // 마커 전방 거리(m)
      const xm = latCm / 100;  // 마커 횡오프셋(m, +우)
      const halfW = W / 2;
      // 수평 투영 배율: 마커 픽셀 위치로 보정(없으면 캘리브레이션 fx)
      const fxe = Math.abs(xm) > 0.005 && Math.abs(mx - halfW) > 4 ? ((mx - halfW) * zm) / xm : CAM_FX_PX;
      // 수직 투영: 평지 가정 v(z) = A + B/z — (마커 위치 my ↔ zm), (화면 하단 ↔ 근접거리) 두 점으로 보정
      const zNear = Math.min(0.18, zm * 0.5);
      const B = (H - my) / (1 / zNear - 1 / zm);
      const A = H - B / zNear;
      const proj = (x: number, z: number) => {
        const zc = Math.max(z, 0.08);
        return [halfW + (fxe * x) / zc, A + B / zc] as const;
      };

      // 지면 좌표 시뮬레이션: th=진행각(+는 우회전), 로봇 제어와 동일 게인
      const pts: Array<readonly [number, number, number]> = []; // x, z, th
      let x = 0, z = 0, th = 0;
      const ds = 0.02;
      for (let i = 0; i < 400; i++) {
        const dx = xm - x, dz = zm - z;
        if (Math.hypot(dx, dz) < 0.06 || dz < 0.03) break;
        const bearing = Math.atan2(dx, dz) - th;            // 마커 상대 방위(+우)
        const exSim = Math.max(-1, Math.min(1, (Math.tan(bearing) * fxe) / halfW));
        const angCmd = Math.abs(exSim) <= SIM_GAIN.deadband
          ? 0
          : Math.max(-SIM_GAIN.angMax, Math.min(SIM_GAIN.angMax, SIM_GAIN.kp * exSim));
        // 곡률 κ = dth/ds = 2·ang/(lin·track) — ex>0(우측)이면 우로 감김. 스텝 발산 방지 클램프.
        th += Math.max(-0.18, Math.min(0.18, ((2 * angCmd) / (SIM_GAIN.lin * PINKY_TRACK_M)) * ds));
        x += Math.sin(th) * ds;
        z += Math.cos(th) * ds;
        pts.push([x, z, th]);
      }

      // 중앙 경로: 점점점 (뒤로 갈수록 작아지는 점)
      ctx.shadowColor = "rgba(0,0,0,0.6)";
      ctx.shadowBlur = 3;
      ctx.fillStyle = guideColor;
      for (let i = 0; i < pts.length; i += 3) {
        const [px, pz] = pts[i];
        const [u, v] = proj(px, pz);
        if (v < my - 4) continue;
        const r = Math.max(2, 6 - (4 * pz) / zm);
        ctx.beginPath();
        ctx.arc(u, v, r, 0, Math.PI * 2);
        ctx.fill();
      }
      // 차폭 가이드: 경로 진행방향의 수직으로 ±차폭/2 오프셋한 좌우 점선
      ctx.globalAlpha = 0.55;
      for (const s of [-1, 1]) {
        for (let i = 0; i < pts.length; i += 5) {
          const [px, pz, pth] = pts[i];
          const ox = (s * (PINKY_WIDTH_M / 2)) * Math.cos(pth);
          const oz = (-s * (PINKY_WIDTH_M / 2)) * Math.sin(pth);
          const [u, v] = proj(px + ox, pz + oz);
          if (v < my - 4) continue;
          ctx.beginPath();
          ctx.arc(u, v, Math.max(1.5, 3.5 - (2 * pz) / zm), 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;

      // 남은 거리 눈금 (경로 1/3·2/3 지점)
      ctx.font = "bold 13px monospace";
      for (const t of [1 / 3, 2 / 3]) {
        const idx = Math.min(pts.length - 1, Math.floor(pts.length * t));
        if (idx < 0 || pts.length === 0) break;
        const [px, pz] = pts[idx];
        const [u, v] = proj(px, pz);
        ctx.strokeStyle = "rgba(255,255,255,0.8)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(u - 16, v);
        ctx.lineTo(u + 16, v);
        ctx.stroke();
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.fillText(`${Math.round(distCm * (1 - t))}cm`, u + 20, v + 4);
      }
    }

    // 4) 수치 HUD — 거리 cm · 좌우 오프셋 cm · 방위/마커면 각도
    const hud = [
      `거리  ${distCm != null ? distCm.toFixed(0) + "cm" : "—"}`,
      `좌우  ${latCm != null ? (latCm >= 0 ? "우 " : "좌 ") + Math.abs(latCm).toFixed(1) + "cm" : "—"}`,
      `방위  ${fmtDeg(bearing)}${yawDeg != null ? ` · 면 ${fmtDeg(yawDeg)}` : ""}`,
    ];
    const bx = Math.min(W - 196, Math.max(6, mx + sidePx / 2 + 12));
    const by = Math.max(6, my - 34);
    ctx.fillStyle = "rgba(2,6,23,0.78)";
    ctx.fillRect(bx, by, 190, 62);
    ctx.strokeStyle = guideColor;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bx, by, 190, 62);
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "bold 14px monospace";
    hud.forEach((ln, i) => ctx.fillText(ln, bx + 8, by + 18 + i * 19));

    // 5) 하단 조향 안내
    ctx.font = "bold 15px sans-serif";
    ctx.fillStyle = guideColor;
    ctx.textAlign = "center";
    ctx.fillText(
      centered ? "▲ 정렬됨 — 이 선을 따라 직진" : ex > 0 ? "마커가 오른쪽 → 우조향 중" : "마커가 왼쪽 → 좌조향 중",
      W / 2, H - 12,
    );
    ctx.textAlign = "left";
  }, [activeStatus, detect, lineDetect, lineStatus, phase, targetMarkerId, targetMarker, running, colorMap, liveColors]);

  // 도킹마커(id=dockId)가 보일 때까지 회전 — 브라우저 개루프 펄스 제거, 로봇 park_dock 마커 회전에 위임.
  // park_dock._rotate_to_marker: 마커가 안 보이면 스캔 회전, 보이면 정면 정렬 후 자동 정지(로봇 로컬 폐루프).
  const orientToDockViaGuide = useCallback(async (dockId: number): Promise<boolean> => {
    const first = await fetchDetect();
    if ((first?.markers ?? []).some((m) => m.id === dockId)) return true; // 이미 보이면 회전 불필요

    setMessage(`도킹마커(id ${dockId}) 탐색 · 로봇 온보드 회전 정렬 중`);
    await runParkPrimitive(
      "rotate",
      {
        rotate_ref: "marker",
        marker_id: dockId,
        marker_dict: dictionary,
        marker_len_m: MARKER_LEN_M,
        rotate_timeout_s: 20,
        manage_nav2: false,
      },
      24000,
    );
    const after = await fetchDetect();
    const ok = (after?.markers ?? []).some((m) => m.id === dockId);
    setMessage(
      ok
        ? `도킹마커 id ${dockId} 감지 · ArUco 정밀 주차 시작`
        : `도킹마커(id ${dockId}) 미검출 — 마커 배치/각도 확인`,
    );
    return ok;
  }, [runParkPrimitive, fetchDetect, dictionary]);

  // ── pose 기반 기하 주차 ────────────────────────────────────────────────────
  // 한 스텝 분량의 행동을 pose 로부터 계산한다. 순수 함수 — 모터를 건드리지 않는다.
  // 반환: {kind:"done"} | {kind:"turn", deg} | {kind:"move", m}  (+ 표시용 계측값)
  const planPoseDockStep = useCallback((p: MarkerPose) => {
    const { x_m: x, z_m: z } = p;
    // 법선(nx,nz)이 없으면 마커가 어느 쪽을 보는지 알 수 없다 → 축 보정은 포기하고
    // '마커 정면 정렬 + 직진'만 수행한다(axisLat=0 으로 두면 아래 ① 분기로 간다).
    const hasNormal = typeof p.nx === "number" && typeof p.nz === "number";
    const nx = p.nx ?? 0;
    const nz = p.nz ?? -1;
    const axisLat = hasNormal ? x * nz - z * nx : 0;        // 마커 정면축 횡오프셋(+ = 축의 오른쪽)
    const markerBearing = Math.atan2(x, Math.max(z, 1e-3)); // 마커 방위각(+ = 오른쪽)
    // ★ 마커까지의 거리는 z(전방 성분)가 아니라 range = hypot(x,z) 를 써야 한다.
    //   z 는 로봇이 제자리 회전만 해도 값이 변해서(물리 거리는 그대로인데) 경유점 W 가 따라 움직이고,
    //   그 결과 turn -22° → +12° → -6° 처럼 좌우로 진동한다(시뮬레이션에서 확인, 2026-07-29).
    //   range 는 회전 불변이라 W 가 고정되고 계획이 수렴한다.
    const range = Math.hypot(x, z);
    // 직진 거리 상한 — 이동 후에도 마커가 화각 안에 남도록. minLegM 은 항상 보장한다.
    const legLimit = (m: number) =>
      Math.max(POSE_DOCK.minLegM, Math.min(m, z - Math.abs(x) / Math.tan((POSE_DOCK.keepMoveDeg * Math.PI) / 180)));
    const info = {
      axisLat,
      markerBearingDeg: (markerBearing * 180) / Math.PI,
      distM: range,
      markerYawDeg: p.yaw_deg,
    };
    // ① 정면축 위에 올라와 있으면 → 마커를 정면으로 맞춘 뒤 직진 접근
    if (Math.abs(axisLat) <= POSE_DOCK.axisTolM) {
      const bearingDeg = info.markerBearingDeg;
      if (Math.abs(bearingDeg) > POSE_DOCK.bearingTolDeg && Math.abs(bearingDeg) >= POSE_DOCK.minTurnDeg) {
        return { kind: "turn" as const, deg: bearingDeg, reason: "마커 정면 정렬", info };
      }
      const remain = range - POSE_DOCK.targetDistM;
      if (remain <= POSE_DOCK.minLegM) return { kind: "done" as const, reason: "목표 거리 도달", info };
      return { kind: "move" as const, m: legLimit(Math.min(remain, POSE_DOCK.maxLegM)), reason: "정면축 직진 접근", info };
    }
    // ② 축에서 벗어나 있으면 → 마커 정면축 위 경유점 W 로 향한다
    const dHold = Math.max(0, Math.min(range - POSE_DOCK.targetDistM, POSE_DOCK.standoffM));
    const wx = x + nx * dHold;
    const wz = z + nz * dHold;
    const th1raw = (Math.atan2(wx, Math.max(wz, 1e-3)) * 180) / Math.PI;
    const wRange = Math.hypot(wx, wz);
    // 회전 후 마커 방위각은 (markerBearingDeg − 회전량) 이 된다. 이 값이 ±keepInViewDeg 안에
    // 남도록 회전량을 자른다 → 마커가 화각 밖으로 나가지 않는다. 잘린 만큼은 다음 스텝에서 이어 돈다.
    const keep = POSE_DOCK.keepInViewDeg;
    const mb = info.markerBearingDeg;
    const th1 = Math.max(mb - keep, Math.min(mb + keep, th1raw));
    if (Math.abs(th1) >= POSE_DOCK.minTurnDeg) {
      const clipped = Math.abs(th1 - th1raw) > 0.5;
      return {
        kind: "turn" as const,
        deg: th1,
        reason: clipped ? `경유점 W 방향으로(화각 유지로 ${th1raw.toFixed(0)}°→${th1.toFixed(0)}° 제한)` : "경유점 W 방향으로",
        info: { ...info, wx, wz, wRange },
      };
    }
    return {
      kind: "move" as const,
      m: legLimit(Math.min(wRange, POSE_DOCK.maxLegM)),
      reason: "경유점 W 로 직진",
      info: { ...info, wx, wz, wRange },
    };
  }, []);

  // 회전/직진 프리미티브. 둘 다 블로킹(완료 후 status:"done")이며 양 바퀴 대칭 구동이라
  // 정지마찰 문제가 없다. angle 음수 = 우회전(시계방향) — 방위각이 +(오른쪽)면 부호를 뒤집는다.
  const robotRotate = useCallback(async (bearingDeg: number) => {
    const res = await fetch(dockBase + "/api/robot/rotate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ angle: -bearingDeg, speed: POSE_DOCK.turnSpeed }),
    });
    if (!res.ok) throw new Error(`회전 실패: HTTP ${res.status}`);
    await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
    await new Promise((r) => window.setTimeout(r, 350)); // 관성 정지 + 카메라 프레임 갱신 대기
  }, [dockBase]);

  const robotForward = useCallback(async (meters: number) => {
    const res = await fetch(dockBase + "/api/robot/move", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ direction: "forward", distance: meters, speed: POSE_DOCK.moveSpeed }),
    });
    if (!res.ok) throw new Error(`직진 실패: HTTP ${res.status}`);
    await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
    await new Promise((r) => window.setTimeout(r, 350));
  }, [dockBase]);

  // ── 로봇 온보드 pose 주차(parkp) — 권장 경로 ────────────────────────────────
  // 배포된 로봇에만 있다(2026-07-29 기준 로봇2). 없으면 404 → 폴백 판단에 쓴다.
  const parkPAvailable = useCallback(async () => {
    try {
      const res = await fetch(dockBase + "/api/robot/parkp/status");
      return res.ok;
    } catch {
      return false;
    }
  }, [dockBase]);

  // 시작만 하고, 진행 상황은 로봇 /parkp/status 를 폴링해 화면에 옮긴다.
  // 브라우저가 죽어도 로봇이 자기 타임아웃(timeout_s)으로 스스로 멈춘다.
  const startParkP = useCallback(async (markerIdOverride?: number | null) => {
    const id = markerIdOverride ?? targetMarkerId ?? dockFallbackRef.current;
    setPhase("docking");
    dockStartedRef.current = true;
    setMessage(`id ${id} 마커 · 로봇 온보드 pose 기하 주차 시작`);
    const res = await fetch(dockBase + "/api/robot/parkp/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        marker_id: id,
        dictionary,
        marker_len_m: MARKER_LEN_M,
        target_distance_m: POSE_DOCK.targetDistM,
        standoff_m: POSE_DOCK.standoffM,
        // ★ UI '벽 정지거리(cm)' 를 반드시 넘긴다. 안 넘기면 로봇 기본값 8cm 가 쓰여
        //   화면에 3cm 로 설정해 놓고 8cm 에서 서 버린다(2026-07-29 실측).
        //   로봇 ParkPConfig 제약: target_wall_cm 3~80, slow_wall_cm 5~120 이고
        //   감속 시작점은 정지 거리보다 확실히 커야 한다.
        use_wall_sensor: true,
        target_wall_cm: Math.max(3, wallTargetCm),
        slow_wall_cm: Math.max(5, Math.max(3, wallTargetCm) + 8),
        // UI '전진/후진 상한' 도 그대로 전달 (로봇 제약 0.02~1.0)
        lin_max: Math.min(1.0, Math.max(0.02, linMax)),
        // UI '전면/후면 목표' 는 마커 크기 비율 기준값 — 카메라 미캘리브레이션 시의
        // 대체 제어(target_size)에만 쓰인다. 캘리브레이션이 있으면 target_distance_m 이 우선.
        target_size: Math.min(0.95, Math.max(0.05, activeTarget)),
        // min_drive / stall_pwm / bearing_limit_deg / turn_pulse_s 는 로봇 기본값을 쓴다.
        // 실측 근거는 parkp.py 의 _drive 주석 참고.
      }),
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = typeof body?.detail === "string" ? ` · ${body.detail}` : "";
      } catch { /* 본문 없음 */ }
      throw new Error(`parkp 시작 실패: HTTP ${res.status}${detail}`);
    }
    // 상태 폴링 — 로봇이 끝내거나(running=false) 사용자가 정지할 때까지
    for (let i = 0; i < 600; i += 1) {
      await new Promise((r) => window.setTimeout(r, 400));
      type ParkPState = { running?: boolean; phase?: string; message?: string; telemetry?: Record<string, unknown> };
      let st: ParkPState | null = null;
      try {
        // /parkp/status 는 상태를 평평하게 돌려준다({running, phase, ...}).
        // /dock/status 처럼 {state:{...}} 로 감싸는 형태도 있어 둘 다 받는다.
        const body = await (await fetch(dockBase + "/api/robot/parkp/status")).json();
        st = (body?.state ?? body ?? null) as ParkPState | null;
      } catch { continue; }
      if (!st) continue;
      const t = st.telemetry ?? {};
      const bits = [
        typeof t.dist === "number" ? `거리 ${t.dist.toFixed(2)}m` : null,
        typeof t.axis_lat === "number" ? `축이탈 ${t.axis_lat.toFixed(3)}m` : null,
        typeof t.bearing_deg === "number" ? `방위 ${t.bearing_deg.toFixed(1)}°` : null,
        typeof t.left === "number" && typeof t.right === "number" ? `L${t.left}/R${t.right}` : null,
      ].filter(Boolean).join(" · ");
      setMessage(`[${st.phase}] ${st.message}${bits ? " · " + bits : ""}`);
      if (st.running === false) {
        setPhase(st.phase === "done" ? "done" : "error");
        return;
      }
    }
    throw new Error("parkp 상태 폴링 시간 초과");
  }, [activeTarget, dockBase, dictionary, linMax, targetMarkerId, wallTargetCm]);

  // pose 기하 주차 본체(브라우저 폴백). 매 스텝 pose 를 다시 재서 재계획한다.
  const startPoseDock = useCallback(async (markerIdOverride?: number | null) => {
    const wantId = markerIdOverride ?? targetMarkerId;
    setPhase("docking");
    dockStartedRef.current = true;
    // 마커 기억: 마지막으로 본 방위각과, 그 뒤로 우리가 명령한 회전량의 합.
    // 놓쳤을 때 현재 방위각 ≈ (마지막 방위각 − 그 뒤 회전량) 으로 추정해 되돌린다.
    // 우리가 회전을 직접 명령하므로 odom 없이도 추정이 된다.
    let lastSeenBearingDeg: number | null = null;
    let turnedSinceSeenDeg = 0;

    const findMarker = async (): Promise<Marker> => {
      const det = await fetchDetect();
      const hit = (det?.markers ?? []).find((m) => wantId == null || m.id === wantId) ?? null;
      if (hit) return hit;
      if (lastSeenBearingDeg == null) {
        throw new Error(wantId == null ? "마커가 보이지 않습니다." : `id ${wantId} 마커가 보이지 않습니다.`);
      }
      // 기억을 이용한 복구: 추정 방위각으로 되돌린 뒤, 안 되면 좌우로 조금씩 훑는다.
      for (let t = 0; t < POSE_DOCK.lostRetries; t += 1) {
        const guess = lastSeenBearingDeg - turnedSinceSeenDeg;
        const scan = t === 0 ? 0 : (t % 2 === 1 ? 1 : -1) * POSE_DOCK.lostScanDeg * Math.ceil(t / 2);
        const back = guess + scan;
        setMessage(`마커 놓침 — 마지막으로 본 방향(추정 ${guess.toFixed(0)}°)으로 ${back >= 0 ? "우" : "좌"} ${Math.abs(back).toFixed(0)}° 되돌리는 중 (${t + 1}/${POSE_DOCK.lostRetries})`);
        if (Math.abs(back) >= 1) {
          await robotRotate(back);
          turnedSinceSeenDeg += back;
        }
        const again = await fetchDetect();
        const found = (again?.markers ?? []).find((m) => wantId == null || m.id === wantId) ?? null;
        if (found) {
          setMessage(`마커 재획득 (방위 ${((Math.atan2(found.pose?.x_m ?? 0, Math.max(found.pose?.z_m ?? 1, 1e-3)) * 180) / Math.PI).toFixed(1)}°)`);
          return found;
        }
      }
      throw new Error(`마커를 놓쳤고 마지막으로 본 방향(추정 ${(lastSeenBearingDeg - turnedSinceSeenDeg).toFixed(0)}°)으로 되돌려도 못 찾았습니다.`);
    };

    for (let step = 1; step <= POSE_DOCK.maxSteps; step += 1) {
      const marker = await findMarker();
      if (!marker.pose) {
        // 카메라 캘리브레이션이 없으면 6-DOF 를 못 구한다 → 기존 ex 기반 도킹으로 넘긴다.
        throw new Error("카메라 캘리브레이션이 없어 pose(x,z,yaw)를 못 구합니다. '아르코만' 대신 기존 도킹을 쓰세요.");
      }
      const plan = planPoseDockStep(marker.pose);
      const i = plan.info;
      // 마커를 봤으니 기억 갱신 — 이후 놓치면 이 방위각에서 명령한 회전량을 빼서 되돌린다.
      lastSeenBearingDeg = i.markerBearingDeg;
      turnedSinceSeenDeg = 0;
      const head = `[${step}/${POSE_DOCK.maxSteps}] 거리 ${i.distM.toFixed(2)}m · 축이탈 ${i.axisLat.toFixed(3)}m · 마커방위 ${i.markerBearingDeg.toFixed(1)}° · 마커yaw ${i.markerYawDeg.toFixed(1)}°`;
      if (plan.kind === "done") {
        setPhase("done");
        setMessage(`${head} → 주차 완료 (${plan.reason})`);
        return;
      }
      if (plan.kind === "turn") {
        setMessage(`${head} → 제자리 회전 ${plan.deg > 0 ? "우" : "좌"} ${Math.abs(plan.deg).toFixed(1)}° (${plan.reason})`);
        await robotRotate(plan.deg);
        turnedSinceSeenDeg += plan.deg;
      } else {
        setMessage(`${head} → 직진 ${plan.m.toFixed(2)}m (${plan.reason})`);
        await robotForward(plan.m);
      }
    }
    throw new Error(`${POSE_DOCK.maxSteps}스텝 안에 수렴하지 못했습니다. 마커/조명 상태를 확인하세요.`);
  }, [fetchDetect, planPoseDockStep, robotForward, robotRotate, targetMarkerId]);

  const startDock = useCallback(async (markerIdOverride?: number | null, opts?: { skipGuide?: boolean }) => {
    const dockMarkerId = markerIdOverride ?? targetMarkerId;
    const cfg: Record<string, unknown> = {
      dictionary,
      marker_id: dockMarkerId,
      marker_len_m: MARKER_LEN_M,
      target_size: activeTarget,
      lin_max: Math.min(linMax, 0.08),
      ang_max: 0.05,
      center_tol: 0.30,
      drive_ex_tol: 0.30,
      steer_ex_tol: 0.50,
      steer_ang_max: 0.08,
      steer_kp: 0.22,
      steer_ki: 0.01,
      steer_kd: 0,
      steer_deadband: 0.012,
      steer_ang_min: 0.025,
      // ★ 정지마찰 하한(peak 기준). _scale_min_drive 가 peak 만 올리므로, 안쪽 바퀴(= peak * r)까지
      //   정지마찰(≈32)을 넘기려면 peak 에 여유가 필요하다. r≥0.75 조합 기준 45*0.75≈34 로 둘 다 넘긴다.
      //   (22 → 아예 안 움직임 / 32 + r=0.2 → 안쪽 6 으로 stall, 둘 다 2026-07-29 실측 확인)
      //   속도가 과해지는 건 move_pulse_s/move_pause_s 의 펄스 듀티로 억제한다.
      min_drive: 45,
      ex_lpf_alpha: 0.45,
      wall_lpf_alpha: 0.35,
      move_pulse_s: 0.10,
      move_pause_s: 0.90,
      search: false,
      search_turn_speed: 0.14,
      align_ex_tol: 0.95,
      align_turn_speed: 0.14,
      align_turn_min_drive: 28,
      ang_slew: 0.015,
      perp_approach: false,
      skew_kp: 0.35,
      perp_ang_max: 0.05,
      skew_deadband: 0.06,
      pose_perp_kp_yaw: 0.65,
      pose_perp_kp_lat: 1.5,
      pose_axis_enable: false,
      pose_axis_tol_m: 0.08,
      pose_axis_standoff_m: 0.35,
      pose_axis_bearing_limit_deg: 12,
      target_wall_cm: wallTargetCm,
      lost_done_wall_cm: Math.max(2, wallTargetCm + 0.5),
      lost_crawl_wall_cm: Math.max(20, wallTargetCm + 5),
      max_lost_s: 15,
      timeout_s: 180,
      rear_turn_secs: mode === "rear" ? 2.0 : 0,
      rear_turn_speed: 0.12,
      rear_turn_dir: 1,
      rear_turn_min_drive: 24,
      approach: mode,
      use_wall_sensor: true,
      manage_nav2: false,   // 기본값: 어떤 도킹이든 nav2 를 끄지 않는다(AMCL 유지)
    };
    // 색 주차(skipGuide): 옆으로 벗어난 마커도 "제자리 회전(turn-and-stop)" 없이
    // 곡선으로 곧장 접근하도록 조향 허용범위를 넓히고 정렬-회전 구간을 사실상 제거한다.
    if (opts?.skipGuide) {
      Object.assign(cfg, {
        steer_ex_tol: 0.9,    // 로봇 허용 최대. 이 범위 안이면 강조향/회전 대신 곡선 조향
        align_ex_tol: 0.98,   // 로봇 허용 최대. 정렬(turn-and-stop) 구간 최소화
        // ★★ 이 로봇의 하드웨어 제약 — 바퀴 하나라도 PWM 이 정지마찰(≈32) 밑이면 로봇이 안 움직인다.
        //    로봇: left=(lin-ang)*MAX, right=(lin+ang)*MAX 이고 _scale_min_drive 는 **peak 만**
        //    min_drive 로 끌어올린다. 안쪽 바퀴 비율 r=(lin-|ang|)/(lin+|ang|) 는 그대로 남으므로
        //    안쪽 = min_drive * r 이 stall 밑이면 그냥 정지한다.
        //    실측(2026-07-29): lin 0.045 / ang 0.03 → r=0.2 → L=32,R=6 → 12초간 완전 정지.
        //    r ≥ 0.75 를 만족하려면 |ang| ≤ lin/7 ≈ 0.006 (lin 0.045 기준).
        steer_ang_max: 0.006,
        steer_ang_min: 0,     // 최소 조향은 0 → 중앙일 땐 강제 회전 안 함(살짝 우회전 방지)
        lin_max: 0.13,        // 전진을 조향보다 우세하게(제자리 회전 방지) + 진전 없음(blocked) 방지
        move_pulse_s: 0.25,   // 전진 펄스 / 정지 → 실측 가능한 만큼 앞으로 이동
        move_pause_s: 0.3,
        slow_wall_cm: 12,     // 근접 감속 시작점을 25→12cm 로 낮춰 20cm 대에서 정상 접근(기어감 방지)
        search: false,               // 마커 상실 시 회전 탐색 금지
        lost_reacquire_turn_speed: 0, // 마커 놓쳐도 '재획득 회전' 안 함 → 제자리 정지(회전 없음)
        pose_center_enable: false,   // ★ ㄱ자 주차(마커 정면까지 제자리 회전) 끔 → ex 조향으로 전진하며 접근
        pose_axis_enable: false,
        manage_nav2: false,          // ★★ nav2 를 껐다 켜지 않는다 → AMCL/위치추정 유지 → 주차 후 목표주행 정상
      });
    }
    // 도킹 가능한 마커: id를 지정했으면 그 id, 아니면 사이트에서 유도마커로 설정한 id 를 제외한 마커.
    // 유도마커는 방향 안내용일 뿐 절대 도킹 대상이 아니다.
    const guideMap = guideTurnRef.current;
    const isDockable = (m: { id: number }) => (dockMarkerId != null ? m.id === dockMarkerId : guideMap[m.id] == null);
    let freshDetect = await fetchDetect();
    let freshMarker = (freshDetect?.markers ?? []).find(isDockable) ?? null;
    // 도킹마커가 안 보이고 유도마커가 보이면: 우측 회전으로 도킹마커를 시야에 넣는다.
    // 단 skipGuide(색 주차 등)면 회전 없이 발견한 그 마커로 바로 접근한다.
    if (!opts?.skipGuide && !freshMarker && (freshDetect?.markers ?? []).some((m) => guideMap[m.id] != null)) {
      const oriented = await orientToDockViaGuide(dockMarkerId ?? dockFallbackRef.current);
      if (oriented) {
        freshDetect = await fetchDetect();
        freshMarker = (freshDetect?.markers ?? []).find(isDockable) ?? null;
      }
    }
    if (!freshMarker) {
      throw new Error(dockMarkerId == null ? "ArUco 마커가 감지되지 않습니다." : `ArUco id ${dockMarkerId} 마커가 감지되지 않습니다.`);
    }
    // 마커가 화면 끝에 있어도 에러로 막지 않는다 — 마커를 보고 P 제어로 붙인다.
    //
    // ⚠️ 회전 여부는 ex 가 아니라 '실제 방위각'(markerBearingDeg: pose 의 atan2, 없으면 fx=471 환산)
    //    으로 판단한다. 수평 반화각이 27° 뿐인 좁은 화각이라 ex 는 금방 포화된다 — 2026-07-29 실측에서
    //    19° 밖에 안 틀어진 마커가 ex -0.81 로 찍혔고, ex 로 판단해 제자리 회전을 걸었더니
    //    바로 앞 마커를 두고 헛돌았다(yaw 9.9°→33.9°, 마커 80회 로스트).
    const bearingDeg = Math.abs(markerBearingDeg(freshMarker));
    // 진짜로 크게 틀어져 있을 때만(≥ PRE_ROTATE_BEARING_DEG) 제자리 회전으로 중앙에 넣는다.
    // 그 미만이면 회전 없이 곧장 P 제어 조향으로 접근한다 — 앞에 있는데 도는 일이 없도록.
    if (opts?.skipGuide && bearingDeg >= PRE_ROTATE_BEARING_DEG) {
      const alignId = freshMarker.id;
      setMessage(`방위각 ${bearingDeg.toFixed(0)}° (ex ${freshMarker.ex.toFixed(2)}) · id ${alignId} 마커 쪽으로 P 회전 중(전진 없음)`);
      // ⚠️ 값은 로봇 ParkConfig 의 pydantic 범위 안이어야 한다(넘기면 HTTP 422):
      //    rotate_speed 0.03~0.5 / rotate_timeout_s 1~60 / marker_center_tol 0.01~0.3
      await runParkPrimitive(
        "rotate",
        {
          rotate_ref: "marker",
          marker_id: alignId,
          marker_dict: dictionary,
          marker_len_m: MARKER_LEN_M,
          marker_center_tol: 0.15,  // 완전 정면까지 안 돌려도 됨 — 조향 구간에 들어오면 충분
          rotate_speed: 0.12,       // 화면 끝 마커를 놓치지 않게 천천히
          // ★ 정지마찰. 로봇 park_dock 기본값 26 으로는 20초 내내 ex 가 안 변했다(실측 2026-07-29).
          //   로봇 aruco_dock._MIN_DRIVE = 32 가 코드가 선언한 하한이고, 제자리 회전은 직진보다
          //   토크가 더 필요하므로 여유를 둬 40 으로 보낸다. (허용 범위 0~70)
          rotate_min_drive: 45,   // 제자리 회전은 양 바퀴 ±45 → 둘 다 정지마찰(≈32) 초과
          rotate_timeout_s: 20,
          manage_nav2: false,
        },
        24000,
      );
      freshDetect = await fetchDetect();
      freshMarker = (freshDetect?.markers ?? []).find((m) => m.id === alignId) ?? null;
      if (!freshMarker) {
        throw new Error(`정렬 회전 중 id ${alignId} 마커를 놓쳤습니다. 로봇을 마커 쪽으로 조금 돌려 두고 다시 시작하세요.`);
      }
      setMessage(`정렬 완료 (ex ${freshMarker.ex.toFixed(2)}) · P 제어 주행으로 접근합니다`);
    }
    // 회전을 안 걸고 그대로 접근하는 경우, 중심오차가 크면 조향 P 이득만 키워 준다.
    // (전진하는 동안 마커가 프레임 밖으로 나가 로스트되는 걸 막는다 — 실측에서 lost 80회 발생)
    // 중심오차가 작을 때는 기존의 '살살 곡선 접근' 튜닝을 그대로 둔다.
    // ⚠️ 로봇 ParkConfig 상한: steer_ex_tol ≤ 0.9 / align_ex_tol ≤ 0.98 / steer_ang_max ≤ 0.3
    if (opts?.skipGuide && Math.abs(freshMarker.ex) > DIRECT_PARK_EX_WARN) {
      Object.assign(cfg, {
        steer_ex_tol: 0.9,    // 로봇 허용 최대 — 옆으로 벗어난 마커도 '곡선 조향' 구간으로 처리
        steer_kp: 0.30,       // 중심오차를 빨리 줄이도록 P 이득 상향(기본 0.22). 상한은 아래 ang_max.
        // 위와 같은 이유로 조향 상한은 lin/7 을 넘기지 않는다. 크게 틀어진 건 조향이 아니라
        // 접근 전 제자리 회전(PRE_ROTATE_BEARING_DEG)으로 처리한다 — 이 구동계는 저속 곡선이 안 된다.
        steer_ang_max: 0.006,
      });
      setMessage(`방위각 ${bearingDeg.toFixed(0)}° · 회전 없이 P 제어 조향으로 접근합니다 (ex ${freshMarker.ex.toFixed(2)})`);
    }
    const depthText = typeof freshMarker.pose?.z_m === "number" ? ` · 거리 ${freshMarker.pose.z_m.toFixed(2)} m` : "";
    setPhase("docking");
    setMessage(`id ${freshMarker.id} 마커 확인${depthText} · ArUco 정밀 주차 시작`);
    arucoStartedRef.current = true;
    dockStartedRef.current = true;

    const res = await fetch(`${dockBase}${DOCK_BASE}/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(cfg),
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") {
          detail = ` · ${body.detail}`;
        } else if (Array.isArray(body?.detail)) {
          // FastAPI 422: detail 은 문자열이 아니라 배열 → 어떤 필드가 왜 튕겼는지 보여준다.
          detail = " · " + body.detail
            .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`)
            .join(", ");
        }
      } catch {
        try {
          const raw = await res.text();
          detail = raw ? ` · ${raw}` : "";
        } catch {
          detail = "";
        }
      }
      throw new Error(`ArUco 주차 시작 실패: HTTP ${res.status}${detail}`);
    }
    setArucoStatus((prev) => ({ ...(prev ?? { telemetry: {} }), running: true, phase: "starting", message: "ArUco 정밀 주차 시작 중" }));
  }, [activeTarget, dictionary, dockBase, fetchDetect, linMax, mode, orientToDockViaGuide, runParkPrimitive, targetMarkerId, wallTargetCm]);

  const startLineDock = useCallback(async (wallOverrideCm?: number) => {
    const first = await fetchDetect();
    const guideMap = guideTurnRef.current;
    // 유도마커 id 는 사이트 설정값(marker_actions.params.guide_turn_deg)에서 온다.
    // 회전각이 큰(≥90°) 유도마커 = 반대편 진입, 작은 유도마커 = C 방향 진입.
    const guideSeen = (first?.markers ?? [])
      .filter((m) => guideMap[m.id] != null)
      .sort((a, b) => Math.abs(guideMap[b.id]) - Math.abs(guideMap[a.id]))[0] ?? null;
    const guideDeg = guideSeen ? guideMap[guideSeen.id] : null;
    let wall = Math.max(2, wallOverrideCm ?? wallTargetCm);

    if (guideSeen && guideDeg != null) {
      const reverse = Math.abs(guideDeg) >= GUIDE_TURN_REVERSE_DEG;
      if (reverse) wall = 3;
      setMessage(
        reverse
          ? `id ${guideSeen.id} 감지 · ${Math.abs(guideDeg)}° 우회전 후 라인 주차 시작`
          : `id ${guideSeen.id} 감지 · C 방향 ${Math.abs(guideDeg)}° 우회전 후 라인 주차 시작`,
      );
      const rot = await fetch(dockBase + "/api/robot/rotate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ angle: -Math.abs(guideDeg), speed: NODE_TURN_SPEED }),
      });
      if (!rot.ok) throw new Error((reverse ? "가이드 회전 실패: HTTP " : "C방향 회전 실패: HTTP ") + rot.status);
      await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
      await new Promise((r) => window.setTimeout(r, reverse ? 220 : 180));
    }

    if (!(await ensureLineVisible())) return;
    const cfg: Record<string, unknown> = {
      roi_top: 0.55,
      thresh: 0,
      min_area_px: 120,
      kp: 0.55,
      ki: 0.02,
      kang: 0.25,
      deadband: 0.02,
      lin: Math.min(linMax, 0.08),
      ang_max: Math.min(angMax, 0.08),
      // 로봇2(192.168.0.42) 실측: 22%로는 정지마찰을 못 이겨 안 움직임 → 40%로 상향(2026-07-09).
      // 라인 주행 속도는 사실상 이 min_drive 가 결정한다(lin*MAX_SPEED 가 이보다 작아 바닥값이 지배).
      min_drive: 40,
      use_wall_sensor: true,
      // 벽 정지거리는 전면/후면 모두 관리페이지 입력값을 그대로 적용한다.
      // 라인 주차에서는 추가 180° 자동회전을 비활성화한다.
      target_wall_cm: wall,
      hard_stop_wall_cm: Math.max(5, wall + 2),
      slow_wall_cm: Math.max(18, wall + 12),
      line_end_done_wall_cm: Math.max(5, wall + 2),
      max_lost_s: 8,
      timeout_s: 90,
      manage_nav2: false,   // nav2 유지 (라인 주차도 nav2 안 끔)
      // IR 3센서 조향 라인트레이싱: 테이프가 좌/우 센서에 걸리면 그쪽으로 보정 조향
      straight_only: false,
      steer_soft: 0.02,
      steer_hard: 0.04,
      steer_lin_scale: 0.75,
      // 테이프 임계값: 로봇1 실측 테이프 ≤538, 바닥 ≥872 (우측 센서가 872~1033으로
      // 낮게 깔려서 800이면 오탐 — 조기 '정지선 감지'와 엉뚱한 우조향의 원인)
      ir_white_max: activeIrWhiteMax,
      stop_ir_sensors: 3,
      ir_debounce: 3,
      rear_finish: false,
      rear_turn_secs: 0.2,
      rear_turn_speed: 0.12,
      rear_turn_dir: 1,
      rear_turn_min_drive: 26,
      rear_back_secs: 0.05,
      rear_back_speed: 0.08,
      // 회전 후 center IR 로 라인 중심 재탐색(펄스 회전) 후 후진
      rear_align: false,
      rear_align_speed: 0.10,
      rear_align_timeout_s: 5.0,
    };
    const res = await fetch(`${dockBase}${LINE_BASE}/start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(cfg),
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") {
          detail = ` · ${body.detail}`;
        } else if (Array.isArray(body?.detail)) {
          // FastAPI 422: detail 은 문자열이 아니라 배열 → 어떤 필드가 왜 튕겼는지 보여준다.
          detail = " · " + body.detail
            .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`)
            .join(", ");
        }
      } catch {
        try {
          const raw = await res.text();
          detail = raw ? ` · ${raw}` : "";
        } catch {
          detail = "";
        }
      }
      throw new Error(`라인 주차 시작 실패: HTTP ${res.status}${detail}`);
    }
    setPhase("docking");
    setMessage("흰 테이프 라인트레이싱 주차 중");
    dockStartedRef.current = true;
    setLineStatus((prev) => ({ ...(prev ?? { telemetry: {} }), running: true, phase: "starting", message: "라인 주차 시작 중" }));
  }, [activeIrWhiteMax, angMax, dockBase, ensureLineVisible, fetchDetect, linMax, mode, wallTargetCm]);

  const startPrecisionDock = useCallback(async () => {
    if (precisionMode === "line") return startLineDock();
    if (precisionMode === "hybrid") {
      lineQueuedRef.current = true;
      setMessage("라인 확인 후 마커 감시를 시작합니다.");
      return startLineDock();
    }
    // 아르코 직접 주차: pose(6-DOF) 기하 주차.
    // ★ 판단(검출→기하→제어)은 전부 로봇 온보드 parkp 라우터에서 돈다. 브라우저는 start/stop/status 만.
    //   제어 루프를 브라우저에 두면 탭을 닫거나 WiFi 가 끊겼을 때 정지를 보장할 수 없고,
    //   스텝마다 HTTP 왕복이 붙는다. 센서와 모터가 있는 로봇이 판단해야 맞다.
    // parkp 가 없는 로봇(미배포)이면 브라우저 pose 루프 → 그것도 안 되면 기존 ex 도킹으로 폴백.
    if (await parkPAvailable()) return startParkP(null);
    const det = await fetchDetect();
    const hasPose = (det?.markers ?? []).some((m) => (targetMarkerId == null || m.id === targetMarkerId) && m.pose);
    if (hasPose) {
      setMessage("이 로봇엔 parkp 가 없어 브라우저에서 pose 기하로 진행합니다.");
      return startPoseDock(null);
    }
    setMessage("카메라 캘리브레이션 없음 — 기존 ex 기반 도킹으로 진행합니다.");
    return startDock(null, { skipGuide: true });
  }, [fetchDetect, parkPAvailable, precisionMode, startDock, startLineDock, startParkP, startPoseDock, targetMarkerId]);

  // ── 노드 테스트: 마커 트리거 → 시계방향 정밀 회전 → 회전 완료 후 라인 검출 확인 ──
  // (독립 실행, FSM 미연동) 회전은 로봇 내장 /rotate(odom 폐루프)로 정확히 돌고 자동 정지.
  // Q결정: "무조건 각도 다 돈 뒤" 라인 검출 확인, 라인 소스는 IR(line/detect). 라인 추종(주차)은 다음 노드.
  const runTurnFindLine = useCallback(async (triggerIds: number[], angleDeg: number, label: string) => {
    if (!canControl || testBusy) return;
    setTestBusy(true);
    const idsText = triggerIds.join("·");
    try {
      setMessage(`${label} · 마커 확인 중 (id ${idsText} 트리거 대기)`);
      let trigger = (detect?.markers ?? []).find((m) => triggerIds.includes(m.id)) ?? null;
      for (let i = 0; !trigger && i < 12; i += 1) {
        const d = await fetchDetect();
        trigger = (d?.markers ?? []).find((m) => triggerIds.includes(m.id)) ?? null;
        if (!trigger) await new Promise((r) => window.setTimeout(r, 120));
      }
      if (!trigger) {
        setMessage(`id ${idsText} 미검출 — 트리거 마커를 카메라 중앙에 맞춰주세요`);
        return;
      }
      setMessage(`id ${trigger.id} 감지 · ${Math.abs(angleDeg)}° 우회전 중 (odom 정밀·자동 정지)`);
      // 내장 rotate: odom yaw 폐루프로 정확히 돌고 스스로 정지. 서버가 완료까지 블록 → 응답 오면 이미 멈춤.
      const res = await fetch(`${dockBase}/api/robot/rotate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ angle: angleDeg, speed: NODE_TURN_SPEED }),
      });
      if (!res.ok) throw new Error(`rotate 실패: HTTP ${res.status}`);
      const rotateBody = await res.json().catch(() => null) as { method?: string } | null;
      if (rotateBody?.method === "timeout") {
        const pulseCount = Math.max(1, Math.round(Math.abs(angleDeg) / 15));
        const left = angleDeg < 0 ? 30 : -30;
        const right = -left;
        setMessage(`회전 타임아웃 감지 · 펄스 회전 보정 중 (${pulseCount}회)`);
        for (let i = 0; i < pulseCount; i += 1) {
          await fetch(`${dockBase}/api/robot/motor/move`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ left, right, duration: 0.18 }),
          }).catch(() => undefined);
          await new Promise((r) => window.setTimeout(r, 240));
          await fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }).catch(() => undefined);
          await new Promise((r) => window.setTimeout(r, 100));
        }
      }
      // 회전 완료 후 라인(IR) 검출 확인 — 잠깐 여러 번 폴링
      setMessage(`${Math.abs(angleDeg)}° 회전 완료 · 라인 검출 확인 중`);
      let found = false;
      for (let i = 0; i < 5; i += 1) {
        const ld = await fetchLineDetect();
        if (ld?.found) { found = true; break; }
        await new Promise((r) => window.setTimeout(r, 250));
      }
      setMessage(found
        ? `id ${trigger.id} → ${Math.abs(angleDeg)}° 회전 · ✅ 라인 감지됨 (다음: 라인 추종 주차)`
        : `id ${trigger.id} → ${Math.abs(angleDeg)}° 회전 · ⚠️ 라인 미검출 (마커/테이프 배치·각도 확인)`);
    } catch (e) {
      await fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }).catch(() => undefined);
      setMessage(e instanceof Error ? e.message : "회전+라인검출 테스트 실패");
    } finally {
      setTestBusy(false);
    }
  }, [canControl, detect?.markers, dockBase, fetchDetect, fetchLineDetect, testBusy]);

  // ── 노드 테스트: 라인 쫓아가기 (바닥 IR 추종 → 설정 벽거리에서 정지) ──
  // 테이프 탐색·마커·Nav2 없이 현재 위치에서 바로 라인 추종만 시작한다. 완료까지 상태를 폴링(격리).
  const runLineFollow = useCallback(async () => {
    if (!canControl || testBusy) return;
    setTestBusy(true);
    // 정지 거리는 설정페이지 값을 그대로 존중한다(로봇2는 2cm에서도 근접 정지 잘 됨, 실측 확인).
    // (이전에 억지로 10/20cm로 올렸던 게 오히려 이상하게 만들었음 — 걷어냄)
    const wall = Math.max(2, wallTargetCm);
    try {
      const cfg: Record<string, unknown> = {
        roi_top: 0.55, thresh: 0, min_area_px: 120,
        lin: Math.min(linMax, 0.08),
        ang_max: Math.min(angMax, 0.08),
        min_drive: 40,               // 로봇2 정지마찰 극복(실측)
        straight_only: false,        // 바닥 IR 3센서로 좌우 조향(테이프 추종)
        // 조향은 전진성분보다 작게(피벗·지그재그 방지) — 양 바퀴 다 앞으로 가며 완만히 곡선.
        steer_soft: 0.02, steer_hard: 0.04, steer_lin_scale: 0.75,
        ir_white_max: activeIrWhiteMax,  // 로봇2 테이프 판정 임계값
        stop_ir_sensors: 3, ir_debounce: 3,
        use_wall_sensor: true,
        target_wall_cm: wall,
        hard_stop_wall_cm: Math.max(5, wall + 2),
        slow_wall_cm: Math.max(18, wall + 12),
        line_end_done_wall_cm: Math.max(5, wall + 2),
        max_lost_s: 8, timeout_s: 90,
        manage_nav2: false,
        rear_finish: false,          // 벽 도달 시 그냥 정지(추가 회전·후진 없음)
      };
      setMessage(`라인 쫓아가기 시작 — 바닥 IR 추종, 벽 ${wall}cm에서 정지`);
      const res = await fetch(`${dockBase}${LINE_BASE}/start`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error(`라인 시작 실패: HTTP ${res.status}`);

      const TERMINAL = ["done", "timeout", "lost", "error", "stopped"];
      const deadline = Date.now() + 100_000; // 클라 안전 상한(로봇 timeout_s=90)
      for (;;) {
        await new Promise((r) => window.setTimeout(r, 600));
        const st = await fetch(`${dockBase}${LINE_BASE}/status`).then((r) => r.json()).catch(() => null);
        const s = st?.state;
        if (s) {
          const t = s.telemetry ?? {};
          if (!TERMINAL.includes(s.phase)) {
            setMessage(`라인 추종 중… ${s.message ?? ""} (벽 ${typeof t.wall_cm === "number" ? Math.max(0, t.wall_cm).toFixed(1) : "-"}cm · L${t.left ?? "-"}/R${t.right ?? "-"})`);
          } else {
            const ok = s.phase === "done";
            setMessage(`${ok ? "✅ 라인 쫓아가기 완료" : "⚠️ 종료"}: ${s.message ?? s.phase}`);
            break;
          }
        }
        if (Date.now() > deadline) {
          await fetch(`${dockBase}${LINE_BASE}/stop`, { method: "POST" }).catch(() => undefined);
          setMessage("라인 쫓아가기 클라이언트 타임아웃 — 정지 요청");
          break;
        }
      }
    } catch (e) {
      await fetch(`${dockBase}${LINE_BASE}/stop`, { method: "POST" }).catch(() => undefined);
      setMessage(e instanceof Error ? e.message : "라인 쫓아가기 실패");
    } finally {
      setTestBusy(false);
    }
  }, [activeIrWhiteMax, canControl, dockBase, testBusy, wallTargetCm, linMax, angMax]);

  const runEZoneMarker23LineWall3 = useCallback(async () => {
    if (!canControl || testBusy) return;
    if (robotId == null) {
      setPhase("error");
      setMessage("활성 로봇 ID가 없습니다. 로봇2를 선택하세요.");
      return;
    }
    if (robotRecord?.ip_address && robotRecord.ip_address !== "192.168.0.42") {
      setPhase("error");
      setMessage("이 버튼은 로봇2(192.168.0.42) 전용입니다. 로봇2를 선택하세요.");
      return;
    }
    if (!zoneELocation || !zoneCLocation) {
      setPhase("error");
      setMessage("E 또는 C 구역 좌표가 없습니다.");
      return;
    }

    const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
    // 접근 유도에 쓸 마커 id — 사이트 설정값(유도마커) 중 회전각이 작은(=C 방향) 것들.
    const guideMap = guideTurnRef.current;
    const markerIds = Object.keys(guideMap)
      .map(Number)
      .filter((id) => Math.abs(guideMap[id]) < GUIDE_TURN_REVERSE_DEG)
      .sort((a, b) => a - b);
    const markerLabel = markerIds.length ? `id ${markerIds.join("·")}` : "미설정";
    if (markerIds.length === 0) {
      setPhase("error");
      setMessage("유도 마커가 설정돼 있지 않습니다. '마커 역할 설정'에서 유도 마커 ID를 먼저 지정하세요.");
      return;
    }
    let lineStarted = false;
    let markerSeen = false;
    let markerGoalAt = 0;
    let lineHits = 0;
    let lastLineSeenAt = 0;
    let cameraLineHits = 0;
    let lastCameraLineSeenAt = 0;

    const readRobotState = async () => {
      const res = await fetch(dockBase + "/api/state");
      if (!res.ok) return null;
      return await res.json() as { pose?: { x: number; y: number; yaw?: number }; mission?: { status?: string } };
    };

    const makeMarkerGoal = (pose: { x: number; y: number; yaw?: number }, marker: Marker) => {
      const yaw = typeof pose.yaw === "number" ? pose.yaw : 0;
      const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
      let gx: number;
      let gy: number;
      if (marker.pose && typeof marker.pose.z_m === "number" && typeof marker.pose.x_m === "number") {
        const forward = clamp(marker.pose.z_m - 0.22, 0.12, 0.45);
        const right = clamp(marker.pose.x_m, -0.30, 0.30);
        gx = pose.x + Math.cos(yaw) * forward + Math.sin(yaw) * right;
        gy = pose.y + Math.sin(yaw) * forward - Math.cos(yaw) * right;
      } else {
        const bearing = markerBearingDeg(marker) * Math.PI / 180;
        const step = 0.28;
        gx = pose.x + Math.cos(yaw + bearing) * step;
        gy = pose.y + Math.sin(yaw + bearing) * step;
      }
      const cyaw = Math.atan2(zoneCLocation.y - gy, zoneCLocation.x - gx);
      return { x: gx, y: gy, yaw: cyaw };
    };

    const alignToC = async (pose: { x: number; y: number; yaw?: number }) => {
      if (typeof pose.yaw !== "number") return;
      const cyaw = Math.atan2(zoneCLocation.y - pose.y, zoneCLocation.x - pose.x);
      const rawAngle = -normRad(cyaw - pose.yaw) * 180 / Math.PI;
      const angle = Math.max(-25, Math.min(25, rawAngle));
      if (Math.abs(angle) < 7) return;
      setMessage(`라인 검출 · C 방향 시계방향 미세정렬 중 (${Math.round(Math.abs(angle))}도)`);
      const rot = await fetch(dockBase + "/api/robot/rotate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ angle, speed: NODE_TURN_SPEED }),
      });
      if (!rot.ok) throw new Error("C 방향 정렬 실패: HTTP " + rot.status);
      await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
      await sleep(180);
    };

    const startLineWall3 = async () => {
      const wall = 3;
      const cfg: Record<string, unknown> = {
        roi_top: 0.55, thresh: 0, min_area_px: 120,
        lin: Math.min(linMax, 0.08),
        ang_max: Math.min(angMax, 0.08),
        min_drive: 40,
        straight_only: false,
        steer_soft: 0.02, steer_hard: 0.04, steer_lin_scale: 0.75,
        ir_white_max: activeIrWhiteMax,
        stop_ir_sensors: 3, ir_debounce: 3,
        use_wall_sensor: true,
        target_wall_cm: wall,
        hard_stop_wall_cm: Math.max(5, wall + 2),
        slow_wall_cm: Math.max(18, wall + 12),
        line_end_done_wall_cm: Math.max(5, wall + 2),
        max_lost_s: 8, timeout_s: 90,
        manage_nav2: false,
        rear_finish: false,
      };
      const res = await fetch(`${dockBase}${LINE_BASE}/start`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error("라인 시작 실패: HTTP " + res.status);
      dockStartedRef.current = true;
      setPhase("docking");
      setMessage("라인 검출 · C 방향 라인트레이싱 시작, 벽 3cm 정지");
      setLineStatus((prev) => ({ ...(prev ?? { telemetry: {} }), running: true, phase: "starting", message: "로봇2 라인 주차 시작 중" }));
    };

    specialMarkerLineFlowRef.current = true;
    setTestBusy(true);
    setPhase("navigating");
    setNavLeg("idle");
    setStartedAt(Date.now());
    dockStartedRef.current = false;
    setArucoStatus(null);
    setLineStatus(null);
    setLineDetect(null);

    try {
      setMessage(`로봇2 특수 주차 · E 구역 이동 시작, 유도마커(${markerLabel}) 감시`);
      const eRes = await fetch(dockBase + "/api/goto", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: DEFAULT_PARKING_ZONE }),
      });
      if (!eRes.ok) throw new Error("E 구역 이동 요청 실패: HTTP " + eRes.status);

      const deadline = Date.now() + 75_000;
      while (Date.now() < deadline) {
        const state = await readRobotState().catch(() => null);
        const poseNow = state?.pose;
        if (poseNow && typeof poseNow.x === "number" && typeof poseNow.y === "number") {
          setDirectNavPose({ x: poseNow.x, y: poseNow.y, yaw: typeof poseNow.yaw === "number" ? poseNow.yaw : undefined });
        }

        const eDistNow = poseNow ? Math.hypot(poseNow.x - zoneELocation.x, poseNow.y - zoneELocation.y) : null;
        const cameraLineGate = markerSeen || (eDistNow != null && eDistNow <= 0.95);
        if (cameraLineGate) {
          const cameraLineNow = await fetchCameraLineDetect();
          const cameraLineFound = cameraLineNow?.source === "camera" && cameraLineNow?.found === true;
          if (cameraLineFound) lastCameraLineSeenAt = Date.now();
          cameraLineHits = cameraLineFound ? cameraLineHits + 1 : 0;
          if (cameraLineHits >= 1 || Date.now() - lastCameraLineSeenAt < 700) {
            lineStarted = true;
            const offset = typeof cameraLineNow?.camera?.offset === "number" ? " · offset " + cameraLineNow.camera.offset.toFixed(2) : "";
            setMessage("이동 중 카메라 흰 테이프 검출" + offset + " · Nav 중단 후 라인트레이싱 시작");
            await adminApi.controlMissionStop(robotId, NAV_PORT).catch(() => undefined);
            await fetch(dockBase + "/api/mission/stop", { method: "POST" }).catch(() => undefined);
            await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
            await startLineWall3();
            return;
          }
        }

        const detectNow = await fetchDetect();
        const marker = (detectNow?.markers ?? []).find((m) => markerIds.includes(m.id)) ?? null;
        if (marker && poseNow && (!markerSeen || Date.now() - markerGoalAt > 1400)) {
          markerSeen = true;
          const goal = makeMarkerGoal(poseNow, marker);
          markerGoalAt = Date.now();
          setMessage(`id ${marker.id} 감지 · 마커 쪽으로 접근하며 라인 검출 (목표 C방향)`);
          const goalRes = await adminApi.controlGoal(robotId, goal, NAV_PORT);
          if (goalRes.success === false) throw new Error(goalRes.msg ?? "마커 접근 목표 전송 실패");
        }

        if (markerSeen) {
          const lineNow = await fetchLineDetect();
          const lineFound = lineNow?.found === true;
          if (lineFound) lastLineSeenAt = Date.now();
          lineHits = lineFound ? lineHits + 1 : 0;
          if (lineHits >= 1 || Date.now() - lastLineSeenAt < 900) {
            lineStarted = true;
            await adminApi.controlMissionStop(robotId, NAV_PORT).catch(() => undefined);
            await fetch(dockBase + "/api/mission/stop", { method: "POST" }).catch(() => undefined);
            await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
            if (poseNow) await alignToC(poseNow);
            await startLineWall3();
            return;
          }
        }

        const eDist = eDistNow;
        const missionIdle = state?.mission?.status === "idle";
        if (!markerSeen && missionIdle && eDist != null && eDist <= LINE_DETECT_GATE_DIST) {
          setMessage(`E 근처 도착 · 유도마커(${markerLabel}) 탐색 중`);
        }
        await sleep(260);
      }

      setPhase("error");
      setMessage(markerSeen ? `유도마커(${markerLabel}) 접근 중 라인을 찾지 못했습니다.` : `E 이동 중 유도마커(${markerLabel})를 찾지 못했습니다.`);
    } catch (e) {
      if (!lineStarted) {
        await fetch(dockBase + "/api/robot/motor/stop", { method: "POST" }).catch(() => undefined);
        await adminApi.controlMissionStop(robotId, NAV_PORT).catch(() => undefined);
        setPhase("error");
      }
      setMessage(e instanceof Error ? e.message : "로봇2 특수 주차 실패");
    } finally {
      specialMarkerLineFlowRef.current = false;
      setTestBusy(false);
    }
  }, [activeIrWhiteMax, angMax, canControl, dockBase, fetchCameraLineDetect, fetchDetect, fetchLineDetect, linMax, robotId, robotRecord?.ip_address, testBusy, zoneCLocation, zoneELocation]);


  const scanForMarkerAfterLine = useCallback(async () => {
    if (markerScanRef.current || arucoStartedRef.current) return;
    markerScanRef.current = true;
    setPhase("docking");

    try {
      for (let i = 0; i < HYBRID_SCAN_PULSES.length; i += 1) {
        const pulse = HYBRID_SCAN_PULSES[i];
        setMessage(`라인 완료 · 마커 주변 탐색 중 (${pulse.label} ${i + 1}/${HYBRID_SCAN_PULSES.length})`);
        const before = await fetchDetect();
        const seenBefore = (before?.markers ?? []).find((m) => targetMarkerId == null || m.id === targetMarkerId) ?? null;
        if (seenBefore) {
          arucoStartedRef.current = true;
          setMessage(`id ${seenBefore.id} 마커 감지 · ArUco 주차 전환`);
          await startDock();
          return;
        }

        await fetch(`${dockBase}/api/robot/motor/move`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ left: pulse.left, right: pulse.right, duration: pulse.duration }),
        }).catch(() => undefined);
        await new Promise((resolve) => window.setTimeout(resolve, Math.round(pulse.duration * 1000) + 120));
        await fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }).catch(() => undefined);
        await new Promise((resolve) => window.setTimeout(resolve, 650));

        const after = await fetchDetect();
        const seenAfter = (after?.markers ?? []).find((m) => targetMarkerId == null || m.id === targetMarkerId) ?? null;
        if (seenAfter) {
          arucoStartedRef.current = true;
          setMessage(`id ${seenAfter.id} 마커 감지 · ArUco 주차 전환`);
          await startDock();
          return;
        }
      }
      await fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }).catch(() => undefined);
      setPhase("error");
      setMessage("라인 완료 후 주변 탐색까지 했지만 마커가 감지되지 않았습니다. 마커 방향/높이를 확인하세요.");
    } catch (e) {
      await fetch(`${dockBase}/api/robot/motor/stop`, { method: "POST" }).catch(() => undefined);
      setPhase("error");
      setMessage(e instanceof Error ? e.message : "마커 주변 탐색 실패");
    } finally {
      markerScanRef.current = false;
    }
  }, [dockBase, fetchDetect, startDock, targetMarkerId]);

  useEffect(() => {
    const markerWatchId = targetMarkerId;
    // 라인 모드 = 순수 라인 트레이싱으로 벽(target_wall_cm)까지 주행. id=1 마커가 보여도
    // 중단하지 않는다. 라인→마커 자동 전환은 hybrid 모드에서만. (사용자 요구: 라인으로 벽 3cm까지)
    const shouldWatchMarker = precisionMode === "hybrid";
    if (!shouldWatchMarker) return;
    if (phase !== "docking") return;
    if (precisionMode === "hybrid" && !lineQueuedRef.current) return;
    if (arucoStartedRef.current) return;
    if (lineStatus?.running !== true) return;

    let cancelled = false;
    const timer = window.setInterval(() => {
      void (async () => {
        const fresh = await fetchDetect();
        if (cancelled || arucoStartedRef.current) return;
        const marker = (fresh?.markers ?? []).find((m) => markerWatchId == null || m.id === markerWatchId) ?? null;
        if (!marker) {
          setMessage(markerWatchId == null ? "라인트레이싱 중 · 마커 감시 중" : `라인트레이싱 중 · id ${markerWatchId} 마커 감시 중`);
          return;
        }
        arucoStartedRef.current = true;
        setMessage(`id ${marker.id} 마커 감지 · 라인 정지 후 마커 방향으로 이동`);
        await fetch(`${dockBase}${LINE_BASE}/stop`, { method: "POST" }).catch(() => undefined);
        if (cancelled) return;
        await startDock(markerWatchId).catch((e) => {
          setPhase("error");
          setMessage(e instanceof Error ? e.message : "마커 방향 이동 전환 실패");
        });
      })();
    }, HYBRID_MARKER_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [dockBase, fetchDetect, lineStatus?.running, phase, precisionMode, startDock, targetMarkerId, zone]);

  useEffect(() => {
    if (!lineStatus || lineStatus.running) return;
    const lineTerminal = ["done", "timeout", "lost", "error", "stopped"].includes(lineStatus.phase);
    if (lineTerminal && lineStatus.phase !== "done") {
      setNavLeg("idle");
      void recoverNavigation();
    }
    if (precisionMode === "hybrid") {
      if (!lineQueuedRef.current || arucoStartedRef.current) return;
      if (lineStatus.phase === "done") {
        void scanForMarkerAfterLine();
      } else if (!["idle", "stopped"].includes(lineStatus.phase)) {
        setPhase("error");
        setMessage(lineStatus.message || `라인 상태: ${lineStatus.phase}`);
      }
      return;
    }
    if (lineStatus.phase === "done") {
      setPhase("done");
      setMessage("흰 테이프 라인 미세조정 완료");
      void recoverNavigation();
    } else if (lineStatus.phase !== "idle") {
      setPhase(lineStatus.phase === "stopped" ? "stopped" : "error");
      setMessage(lineStatus.message || `라인 상태: ${lineStatus.phase}`);
    }
  }, [lineStatus, precisionMode, recoverNavigation, scanForMarkerAfterLine]);

  useEffect(() => {
    if (phase !== "navigating" || !startedAt) return;
    if (specialMarkerLineFlowRef.current) return;

    if (lineDetect?.found && lineDetectNearParkingZones && !dockStartedRef.current) {
      dockStartedRef.current = true;
      setNavLeg("idle");
      setPhase("docking");
      setMessage("주행 중 라인 감지 · 즉시 라인 추종 주차 전환");
      void adminApi.controlMissionStop(robotId!, NAV_PORT).catch(() => undefined).finally(() => {
        void startLineDock().catch((e) => {
          setPhase("error");
          setMessage(e instanceof Error ? e.message : "라인 주차 시작 실패");
        });
      });
      return;
    }

    if (navLeg === "toE") {
      if (distanceToE != null && (distanceToE <= NAV_ARRIVE_DIST || (navGoalStopped && distanceToE <= LINE_DETECT_GATE_DIST)) && !dockStartedRef.current) {
        if (!zoneDLocation || robotId == null) {
          setPhase("error");
          setNavLeg("idle");
          setMessage("D 구역 좌표를 찾지 못했습니다.");
          return;
        }
        setNavLeg("toD");
        setMessage("E 도착 · D 구역으로 이동하며 라인 탐색");
        void Promise.race([
          fetch(dockBase + "/api/goto", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ name: DEFAULT_LINE_ENTRY_ZONE }),
          }).then((r) => r.ok ? r.json() : Promise.reject(new Error("D 구역 이동 요청 실패: HTTP " + r.status))),
          new Promise<{ success: boolean; msg?: string }>((_, rej) => window.setTimeout(() => rej(new Error("D 구역 이동 요청 타임아웃")), 8000)),
        ]).then((res) => {
          if (res.success === false) throw new Error(res.msg ?? "D 구역 이동 요청 실패");
        }).catch((e) => {
          setPhase("error");
          setNavLeg("idle");
          setMessage(e instanceof Error ? e.message : "D 구역 이동 시작 실패");
        });
      }
      return;
    }

    if (navLeg === "toD") {
      if (distanceToD != null && (distanceToD <= NAV_ARRIVE_DIST || (navGoalStopped && distanceToD <= LINE_DETECT_GATE_DIST)) && !dockStartedRef.current) {
        if (!zoneCLocation || robotId == null) {
          setPhase("error");
          setNavLeg("idle");
          setMessage("C 구역 좌표를 찾지 못했습니다.");
          return;
        }
        setNavLeg("toC");
        setMessage("D 도착 · C 구역으로 이동하며 라인 탐색");
        void Promise.race([
          fetch(dockBase + "/api/goto", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ name: DEFAULT_PARKING_APPROACH_ZONE }),
          }).then((r) => r.ok ? r.json() : Promise.reject(new Error("C 구역 이동 요청 실패: HTTP " + r.status))),
          new Promise<{ success: boolean; msg?: string }>((_, rej) => window.setTimeout(() => rej(new Error("C 구역 이동 요청 타임아웃")), 8000)),
        ]).then((res) => {
          if (res.success === false) throw new Error(res.msg ?? "C 구역 이동 요청 실패");
        }).catch((e) => {
          setPhase("error");
          setNavLeg("idle");
          setMessage(e instanceof Error ? e.message : "C 구역 이동 시작 실패");
        });
      }
      return;
    }

    if (navLeg === "toC") {
      if (distanceToC != null && (distanceToC <= NAV_ARRIVE_DIST || (navGoalStopped && distanceToC <= LINE_DETECT_GATE_DIST)) && !dockStartedRef.current) {
        setNavLeg("idle");
        setPhase("docking");
        setMessage("C 구역 도착 · 라인 추종 주차 시작");
        void startLineDock().catch((e) => {
          setPhase("error");
          setMessage(e instanceof Error ? e.message : "라인 주차 시작 실패");
        });
      }
      return;
    }

    if (precisionMode !== "hybrid" && targetMarker && Date.now() - startedAt > 1200 && !dockStartedRef.current) {
      void startPrecisionDock().catch((e) => {
        setPhase("error");
        setMessage(e instanceof Error ? e.message : "정밀 주차 시작 실패");
      });
      return;
    }
    if (distance != null && distance <= ARRIVE_DIST && !dockStartedRef.current) {
      void startPrecisionDock().catch((e) => {
        setPhase("error");
        setMessage(e instanceof Error ? e.message : "정밀 주차 시작 실패");
      });
      return;
    }
    if (Date.now() - startedAt > ARRIVE_TIMEOUT) {
      setPhase("error");
      setNavLeg("idle");
      setMessage("주차 구역 도착 대기 시간이 초과되었습니다.");
    }
  }, [distance, distanceToC, distanceToD, distanceToE, lineDetect?.found, lineDetectNearParkingZones, navGoalStopped, navLeg, phase, precisionMode, robotId, startLineDock, startPrecisionDock, startedAt, targetMarker, zoneCLocation, zoneDLocation]);

  useEffect(() => {
    if (phase !== "docking" || !arucoStatus || arucoStatus.running) return;
    if (!TERMINAL_DOCK_PHASES.has(arucoStatus.phase)) return;
    if (arucoStatus.phase !== "idle") {
      setNavLeg("idle");
      void recoverNavigation();
    }
    if (arucoStatus.phase === "done") {
      setPhase("done");
      setMessage(`${modeLabel(mode)} 완료`);
    } else if (arucoStatus.phase !== "idle") {
      setPhase(arucoStatus.phase === "stopped" ? "stopped" : "error");
      setMessage(arucoStatus.message || `도킹 상태: ${arucoStatus.phase}`);
    }
  }, [arucoStatus, mode, phase, precisionMode, recoverNavigation]);

  async function waitForNavigationReady() {
    const deadline = Date.now() + NAV_READY_TIMEOUT;
    let lastError = "";
    while (Date.now() < deadline) {
      try {
        const state = await adminApi.controlState(robotId!, NAV_PORT);
        if (state.pose) return true;
        lastError = "현재 위치를 아직 받지 못했습니다.";
      } catch (e) {
        lastError = e instanceof Error ? e.message : "Nav2 상태 확인 실패";
      }
      setMessage(`Nav2 준비 중... ${Math.max(1, Math.ceil((deadline - Date.now()) / 1000))}초`);
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
    throw new Error(lastError ? `Nav2 준비 시간 초과: ${lastError}` : "Nav2 준비 시간 초과");
  }

  async function startParking() {
    if (!canControl) {
      setPhase("error");
      setMessage("활성 로봇이 주행로봇이 아니거나 연결 상태가 아닙니다.");
      return;
    }
    if (robotId == null) {
      setPhase("error");
      setMessage("활성 로봇 ID가 없습니다. 로봇을 다시 선택하세요.");
      return;
    }
    if (!zone) {
      setPhase("error");
      setMessage("목표 구역이 선택되지 않았습니다.");
      return;
    }
    dockStartedRef.current = false;
    lineQueuedRef.current = false;
    arucoStartedRef.current = false;
    markerScanRef.current = false;
    setArucoStatus(null);
    setLineStatus(null);
    setLineDetect(null);
    setDirectNavPose(null);
    setDirectNavMissionStatus(null);
    setStartedAt(Date.now());
    setMessage("주차 경로 준비 중");

    if (!zoneELocation || !zoneDLocation || !zoneCLocation) {
      setPhase("error");
      setMessage("위치 저장소에 E, D 또는 C 구역이 없습니다.");
      return;
    }

    try {
      await waitForNavigationReady();
    } catch (e) {
      setPhase("error");
      setNavLeg("idle");
      setMessage(e instanceof Error ? e.message : "Nav2 준비 실패");
      return;
    }

    let firstNavLeg: "toE" | "toD" = "toE";
    let firstZoneName = DEFAULT_PARKING_ZONE;
    try {
      const stateRes = await fetch(dockBase + "/api/state");
      if (stateRes.ok) {
        const stateBody = await stateRes.json();
        const p = stateBody?.pose;
        if (typeof p?.x === "number" && typeof p?.y === "number") {
          setDirectNavPose({ x: p.x, y: p.y, yaw: typeof p.yaw === "number" ? p.yaw : undefined });
          const eDist = Math.hypot(p.x - zoneELocation.x, p.y - zoneELocation.y);
          if (eDist <= LINE_DETECT_GATE_DIST) {
            firstNavLeg = "toD";
            firstZoneName = DEFAULT_LINE_ENTRY_ZONE;
          }
        }
      }
    } catch {
      /* start pose fallback failed; start from E */
    }

    setPhase("navigating");
    setNavLeg(firstNavLeg);
    setMessage(firstNavLeg === "toD" ? "E 근처 확인 · D 구역으로 이동하며 라인 탐색" : "E 구역으로 이동 중");
    try {
      const res = await Promise.race([
        fetch(dockBase + "/api/goto", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: firstZoneName }),
        }).then((r) => r.ok ? r.json() : Promise.reject(new Error(firstZoneName + " 구역 이동 요청 실패: HTTP " + r.status))),
        new Promise<{ success: boolean; msg?: string }>((_, rej) => window.setTimeout(() => rej(new Error(firstZoneName + " 구역 이동 요청 타임아웃")), 8000)),
      ]);
      if (res.success === false) throw new Error(res.msg ?? firstZoneName + " 구역 이동 요청 실패");
    } catch (e) {
      setPhase("error");
      setNavLeg("idle");
      setMessage(e instanceof Error ? e.message : "주차 이동 시작 실패");
    }
  }

  async function stopParking() {
    setPhase("stopped");
    setMessage("정지 요청 전송");
    setStartedAt(null);
    setNavLeg("idle");
    dockStartedRef.current = false;
    arucoStartedRef.current = false;
    markerScanRef.current = false;
    await Promise.allSettled([
      robotId != null ? adminApi.controlMissionStop(robotId, NAV_PORT) : Promise.resolve(),
      fetch(dockBase + "/api/mission/stop", { method: "POST" }),
      fetch(`${dockBase}${DOCK_BASE}/stop`, { method: "POST" }),
      fetch(`${dockBase}${LINE_BASE}/stop`, { method: "POST" }),
      fetch(dockBase + "/api/robot/parkp/stop", { method: "POST" }),
    ]);
    await recoverNavigation();
  }

  // ── 색 주차: 색↔마커 매핑 로드/지정 + 색으로 주차(기존 startDock 재사용) ──────────
  const loadColorMap = useCallback(async () => {
    try {
      const list = await adminApi.listMarkerActions();
      const m = list
        .map((a) => ({ marker_id: a.marker_id, color: String((a.params as Record<string, unknown> | null)?.park_color ?? "") }))
        .filter((x) => x.color);
      setColorMap(m);
      // 마커 역할(사이트 설정값): 유도마커 회전각 / 기본 도킹마커
      const guides: Record<number, number> = {};
      let dockId: number | null = null;
      for (const a of list) {
        const p = (a.params ?? {}) as Record<string, unknown>;
        const deg = Number(p.guide_turn_deg);
        if (a.enabled !== false && Number.isFinite(deg) && p.guide_turn_deg !== null && p.guide_turn_deg !== "") {
          guides[a.marker_id] = deg;
        }
        if (dockId == null && p.dock_marker === true) dockId = a.marker_id;
      }
      setGuideTurnMap(guides);
      setDockMarkerCfgId(dockId);
    } catch {
      /* 매핑 로드 실패 — 무시 */
    }
  }, []);

  useEffect(() => {
    if (canControl) void loadColorMap();
  }, [canControl, loadColorMap]);

  // 실시간 색 인식: 주행 중이 아닐 때 perceive 를 주기적으로 호출해 마커별 감지 색을 갱신.
  useEffect(() => {
    if (!canControl || robotId == null || running) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await adminApi.parkPerceive(robotId);
        if (cancelled) return;
        const m: Record<number, string> = {};
        for (const mk of res.markers ?? []) if (mk.color?.name) m[mk.id] = mk.color.name;
        setLiveColors(m);
      } catch {
        /* perceive 실패 — 무시 */
      }
    };
    void poll();
    const t = window.setInterval(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [canControl, robotId, running]);

  async function assignParkColor(color: string) {
    const id = markerId.trim() === "" ? null : Number(markerId);
    if (id == null) {
      setMessage("먼저 '마커 ID'를 입력한 뒤 색을 지정하세요.");
      return;
    }
    try {
      const list = await adminApi.listMarkerActions();
      const cur = list.find((a) => a.marker_id === id);
      const params: Record<string, unknown> = { ...(cur?.params ?? {}) };
      if (color) params.park_color = color;
      else delete params.park_color;
      await adminApi.upsertMarkerAction(id, {
        label: cur?.label ?? null,
        action_type: cur?.action_type && cur.action_type !== "none" ? cur.action_type : "dock",
        params,
        enabled: cur?.enabled ?? true,
      });
      await loadColorMap();
      setMessage(`${id}번 마커를 '${color || "색 없음"}'(으)로 지정했어요.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  // ── 마커 역할 설정(사이트에서 지정) ────────────────────────────────────────────
  // 마커 id 는 현장에서 바뀌므로 코드가 아니라 여기서 지정한 값을 쓴다.
  // params.guide_turn_deg = 회전각(도) → 유도 마커 / params.dock_marker = true → 기본 도킹 마커
  const saveMarkerParams = useCallback(async (id: number, patch: Record<string, unknown>) => {
    const list = await adminApi.listMarkerActions();
    const cur = list.find((a) => a.marker_id === id);
    const params: Record<string, unknown> = { ...(cur?.params ?? {}) };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === null) delete params[k];
      else params[k] = v;
    }
    await adminApi.upsertMarkerAction(id, {
      label: cur?.label ?? null,
      action_type: cur?.action_type && cur.action_type !== "none" ? cur.action_type : "dock",
      params,
      enabled: cur?.enabled ?? true,
    });
    await loadColorMap();
  }, [loadColorMap]);

  async function saveGuideMarker() {
    const id = guideFormId.trim() === "" ? null : Number(guideFormId);
    const deg = Number(guideFormDeg);
    if (id == null || !Number.isFinite(deg) || deg === 0) {
      setMessage("유도 마커 ID 와 회전각(도)을 입력하세요.");
      return;
    }
    try {
      await saveMarkerParams(id, { guide_turn_deg: Math.abs(deg) });
      setGuideFormId("");
      setMessage(`${id}번 마커를 유도 마커(회전 ${Math.abs(deg)}°)로 지정했어요.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeGuideMarker(id: number) {
    try {
      await saveMarkerParams(id, { guide_turn_deg: null });
      setMessage(`${id}번 마커의 유도 지정을 해제했어요.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveDockMarker() {
    const id = markerId.trim() === "" ? null : Number(markerId);
    if (id == null) {
      setMessage("먼저 '마커 ID' 를 입력한 뒤 기본 도킹 마커로 지정하세요.");
      return;
    }
    try {
      // 기존 기본 도킹 마커는 해제하고 하나만 유지
      if (dockMarkerCfgId != null && dockMarkerCfgId !== id) {
        await saveMarkerParams(dockMarkerCfgId, { dock_marker: null });
      }
      await saveMarkerParams(id, { dock_marker: true });
      setMessage(`${id}번 마커를 기본 도킹 마커로 지정했어요.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  // 색 버튼 = 대상 마커 '선택'만. 실제 주차는 '직접 주차' 버튼으로(회전 없이).
  function parkByColor(color: string) {
    const hit = colorMap.find((x) => x.color === color);
    if (!hit) {
      setMessage(`'${color}'에 지정된 마커가 없어요. 마커 ID 입력 후 색을 지정하세요.`);
      return;
    }
    setMarkerId(String(hit.marker_id));
    setMessage(`${color} = ${hit.marker_id}번 마커 선택됨. '직접 주차' 또는 '구역이동+주차'를 누르세요.`);
  }

  // 선택 구역으로 nav2 주행 → 도착하면 nav2 를 끄지 않고(미션만 정지, AMCL 보존)
  // 이어서 마커+색 정밀 도킹. 사용자 요청 흐름: "A 이동 후 마커+색으로 정밀 주행".
  async function startColorParkFlow() {
    const id = markerId.trim() === "" ? null : Number(markerId);
    if (robotId == null) return;
    if (id == null) {
      setMessage("먼저 색 버튼으로 마커를 선택하세요(또는 마커 ID 입력).");
      return;
    }
    const zoneName = zone || DEFAULT_PARKING_ZONE;
    try {
      setPhase("navigating");
      setMessage(`${zoneName} 구역으로 이동 중 (nav2 유지)`);
      await adminApi.controlGoto(robotId, zoneName, NAV_PORT);
      // 도착 대기: '목표 근처 실제 도달' 만 도착으로 인정한다.
      // 미션 idle 은 '완료'일 수도 '주행 실패/미시작'일 수도 있으므로, idle 이어도 목표 근처가
      // 아니면 도착이 아니다(초반 3.5초는 미션이 아직 시작 전일 수 있어 idle 무시).
      // 도착 판정: mission.status 는 주행 중에도 idle 로 나와 신뢰 불가 → 실제 pose 로 판단.
      //  · 목표 근처 도달 → 도착
      //  · pose 가 바뀌는 동안(주행 중)엔 계속 대기
      //  · 초반(5초) 이후 로봇이 ~7초간 전혀 안 움직이고 목표도 아니면 → 주행 실패
      const target = locations[zoneName];
      const t0 = Date.now();
      let arrived = false;
      let lastPose: { x: number; y: number } | null = null;
      let stuck = 0;
      while (Date.now() - t0 < ARRIVE_TIMEOUT) {
        await new Promise((r) => window.setTimeout(r, 700));
        const st = await adminApi.controlState(robotId, NAV_PORT).catch(() => null);
        const p = st?.pose;
        if (p && target && Math.hypot(p.x - target.x, p.y - target.y) <= NAV_ARRIVE_DIST) { arrived = true; break; }
        if (p) {
          if (lastPose && Math.hypot(p.x - lastPose.x, p.y - lastPose.y) < 0.008) stuck += 1;
          else stuck = 0;
          lastPose = { x: p.x, y: p.y };
        }
        if (stuck >= 10 && Date.now() - t0 > 5000) break; // ~7초간 안 움직임 + 목표 아님 → 실패
      }
      if (!arrived) {
        setPhase("error");
        setMessage(`${zoneName} 로 주행하지 못했어요(로봇이 멈춰 있음). 위치추정·맵·목표를 확인하세요.`);
        return;
      }
      // 목표(미션)만 정지 → nav2 노드는 유지(재시작 X → AMCL 리셋 없음)
      await adminApi.controlMissionStop(robotId, NAV_PORT).catch(() => undefined);
      // 도착 직후엔 로봇이 아직 정지·정렬 중이라 마커가 안 잡힐 수 있다.
      // 잠깐 안정화 대기 후, 넉넉히(약 25초) 재검출하며 기다린다(너무 빨리 포기 방지).
      setMessage(`${zoneName} 도착 · 자리 잡는 중…`);
      await new Promise((r) => window.setTimeout(r, 2500));
      setMessage(`${zoneName} 도착 · 마커 ${id} 탐지 중… (정면에 보이게 두세요)`);
      let seen = false;
      for (let i = 0; i < 25; i += 1) {
        const det = await fetchDetect();
        if ((det?.markers ?? []).some((m) => m.id === id)) { seen = true; break; }
        setMessage(`${zoneName} 도착 · 마커 ${id} 탐지 중… (${i + 1}/25)`);
        await new Promise((r) => window.setTimeout(r, 1000));
      }
      if (!seen) {
        setPhase("error");
        setMessage(`${zoneName} 근처에서 ${id}번 마커가 아직 안 보여요. 로봇이 마커를 바라보게 둔 뒤 다시 시도하세요.`);
        return;
      }
      setMessage(`${zoneName} 도착 · 마커 ${id} 감지됨 · 정밀 주차 시작 (nav2 유지)`);
      await startDock(id, { skipGuide: true });
    } catch (e) {
      setPhase("error");
      setMessage(e instanceof Error ? e.message : "색 주차(구역이동) 실패");
    }
  }

  const tele = activeStatus?.telemetry ?? {};
  const topMarker = detect?.markers?.[0] ?? null;
  const shownDockPhase = activeStatus?.phase ?? phase;
  const shownDockMessage = activeStatus?.phase === "starting" ? "도킹 준비 및 카메라 시작 중..." : (activeStatus?.message || message);
  const shownEx = typeof tele.ex === "number" ? tele.ex : typeof tele.offset === "number" ? tele.offset : targetMarker?.ex;
  const shownWallCm = (typeof tele.wall_cm === "number" && Number.isFinite(tele.wall_cm)) ? Math.max(0, tele.wall_cm) : ((typeof sensor?.dist_cm === "number" && Number.isFinite(sensor.dist_cm)) ? Math.max(0, sensor.dist_cm) : null);
  const currentTone = statusTone(shownDockPhase, shownWallCm, shownEx);
  const currentToneClass = STATUS_TONE_CLASS[currentTone];

  useEffect(() => {
    const phaseLabel = dockPhaseLabel(shownDockPhase);
    const detail = shownDockMessage || message || phaseLabel;
    const extra = [
      typeof shownEx === "number" ? `중심오차 ${shownEx.toFixed(3)}` : null,
      typeof shownWallCm === "number" ? `벽 ${shownWallCm.toFixed(1)}cm` : null,
      typeof tele.left === "number" || typeof tele.right === "number" ? `모터 ${tele.left ?? "-"}/${tele.right ?? "-"}` : null,
    ].filter(Boolean).join(" · ");
    const eventMessage = extra ? `${detail} · ${extra}` : detail;
    const signature = `${phaseLabel}|${eventMessage}`;
    setStatusEvents((prev) => {
      if (`${prev[0]?.phase}|${prev[0]?.message}` === signature) return prev;
      const next: StatusEvent = {
        id: Date.now(),
        at: new Date().toLocaleTimeString("ko-KR", { hour12: false }),
        phase: phaseLabel,
        message: eventMessage,
        tone: currentTone,
      };
      return [next, ...prev].slice(0, 8);
    });
  }, [currentTone, message, shownDockMessage, shownDockPhase, shownEx, shownWallCm, tele.left, tele.right]);

  return (
    <AdminShell title="주행로봇 주차">
      <section className={cn("mb-4 rounded-lg border-2 px-4 py-4 shadow-sm", currentToneClass.shell)}>
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2">
              <span className={cn("h-4 w-4 shrink-0 rounded-full", currentToneClass.dot)} />
              <span className={cn("rounded px-2 py-1 text-xs font-bold", currentToneClass.badge)}>
                {dockPhaseLabel(shownDockPhase)}
              </span>
              {currentTone === "danger" && <AlertTriangle className="h-5 w-5 text-red-600" />}
            </div>
            <div className="text-2xl font-black leading-tight tracking-normal md:text-3xl">
              {shownDockMessage || FLOW_META[phase].label}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center md:min-w-[360px]">
            <StatusBig label="중심오차" value={typeof shownEx === "number" ? shownEx.toFixed(3) : "-"} warn={typeof shownEx === "number" && Math.abs(shownEx) > 0.45} />
            <StatusBig label="벽거리" value={typeof shownWallCm === "number" ? `${shownWallCm.toFixed(1)}cm` : "-"} warn={typeof shownWallCm === "number" && shownWallCm <= 10} />
            <StatusBig label="모터 L/R" value={typeof tele.left === "number" || typeof tele.right === "number" ? `${tele.left ?? "-"}/${tele.right ?? "-"}` : "-"} />
          </div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(360px,520px)_1fr]">
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <div className="relative aspect-[4/3] bg-black">
            {!webRtcFailed && (
              <video
                ref={videoRef}
                autoPlay
                muted
                playsInline
                className="absolute inset-0 h-full w-full object-contain"
                style={{ transform: "scaleY(-1)", opacity: webRtcReady ? 1 : 0.25 }}
              />
            )}
            {webRtcFailed && (
              <img
                key={camEpoch}
                src={dockBase + CAM_STREAM + "?w=320&q=35&fps=10&e=" + camEpoch}
                alt="로봇 카메라"
                className="absolute inset-0 h-full w-full object-contain"
                style={{ transform: "scaleY(-1)" }}
                onError={() => setTimeout(() => setCamEpoch((v) => v + 1), 1000)}
              />
            )}
            <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
            <div className="absolute left-3 top-3 rounded bg-black/65 px-2 py-1 text-xs font-medium text-white">
              {robotName ?? "Pinky"} · {modeLabel(mode)}
            </div>
          </div>
          <div className="grid grid-cols-4 border-t border-slate-200 text-center text-sm">
            <Metric label="마커" value={topMarker ? `id ${topMarker.id}` : "-"} />
            <Metric label="중앙 오차" value={topMarker ? topMarker.ex.toFixed(3) : "-"} />
            <Metric label="각도(+우/-좌)" value={topMarker ? fmtDeg(markerBearingDeg(topMarker)) : "-"} />
            <Metric
              label="거리"
              value={topMarker?.pose ? `${topMarker.pose.z_m.toFixed(2)} m` : topMarker ? topMarker.size_frac.toFixed(3) : "-"}
            />
          </div>

          {/* 실시간 센서·인식 상태(텍스트) */}
          <div className="space-y-1.5 border-t border-slate-200 px-3 py-3 font-mono text-xs">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-500">실시간 상태</span>
              <span className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                (detect?.markers?.length ?? 0) > 0 ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500",
              )}>
                {(detect?.markers?.length ?? 0) > 0 ? `마커 ${detect?.markers?.length}개 인식` : "마커 미검출"}
              </span>
            </div>
            <SensorLine
              label="인식 마커"
              value={
                running && typeof tele.ex === "number"
                  ? `id ${tele.id ?? "?"}   중심오차 ${(tele.ex as number).toFixed(3)}   각도 ${fmtDeg(bearingFromEx(tele.ex as number))}   크기 ${typeof tele.dist === "number" ? (tele.dist as number).toFixed(3) : "-"}   (도킹)`
                  : targetMarker
                    ? `id ${targetMarker.id}   중심오차 ${targetMarker.ex.toFixed(3)}   각도 ${fmtDeg(markerBearingDeg(targetMarker))}${targetMarker.pose ? `   거리 ${targetMarker.pose.z_m.toFixed(2)}m   정면각 ${fmtDeg(targetMarker.pose.yaw_deg)}${axisLatM(targetMarker.pose) != null ? `   축이탈 ${axisLatM(targetMarker.pose)!.toFixed(2)}m` : ""}` : `   크기 ${targetMarker.size_frac.toFixed(3)}`}`
                    : "—"
              }
            />
            <SensorLine
              label="초음파(벽)"
              value={sensor?.dist_cm != null ? `${sensor.dist_cm.toFixed(1)} cm` : "—"}
              warn={sensor?.dist_cm != null && sensor.dist_cm <= 15}
            />
            <SensorLine
              label="IR L/C/R"
              value={sensor?.ir ? `${sensor.ir.left} / ${sensor.ir.center} / ${sensor.ir.right}  (원시값)` : "—"}
            />
            <SensorLine
              label="마커 상태"
              value={`${dockPhaseLabel(arucoStatus?.phase)}${arucoStatus?.message ? " · " + arucoStatus.message : ""}`}
            />
            <SensorLine
              label="2차 상태"
              value={`${dockPhaseLabel(lineStatus?.phase)}${lineStatus?.message ? " · " + lineStatus.message : ""}`}
            />
            <SensorLine
              label="라인 검출"
              value={lineDetect ? `${lineDetect.found ? "검출" : "미검출"}${lineDetect.ir ? ` · IR ${lineDetect.ir.left} / ${lineDetect.ir.center} / ${lineDetect.ir.right}` : ""}${typeof lineDetect.wall_cm === "number" ? ` · 벽 ${Math.max(0, lineDetect.wall_cm).toFixed(1)}cm` : ""}` : "—"}
            />
            {typeof shownWallCm === "number" && (
              <SensorLine label="도킹 벽거리" value={`${shownWallCm.toFixed(1)} cm`} />
            )}
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3">
              <Label title="주차 구역">
                <select
                  className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm"
                  value={zone}
                  onChange={(e) => setZone(e.target.value)}
                  disabled={running}
                >
                  {zoneNames.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </Label>

              <div className="grid grid-cols-2 gap-2">
                <ModeButton active={mode === "front"} disabled={running} onClick={() => setMode("front")}>전면 주차</ModeButton>
                <ModeButton active={mode === "rear"} disabled={running} onClick={() => setMode("rear")}>후면 주차</ModeButton>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <ModeButton active={precisionMode === "hybrid"} disabled={running} onClick={() => setPrecisionMode("hybrid")}>라인 → 마커</ModeButton>
                <ModeButton active={precisionMode === "aruco"} disabled={running} onClick={() => setPrecisionMode("aruco")}>아르코만</ModeButton>
                <ModeButton active={precisionMode === "line"} disabled={running} onClick={() => setPrecisionMode("line")}>라인만</ModeButton>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Label title="사전">
                  <select
                    className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm"
                    value={dictionary}
                    onChange={(e) => setDictionary(e.target.value)}
                    disabled={running}
                  >
                    {DICTS.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </Label>
                <Label title="마커 ID">
                  <input
                    className="h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
                    value={markerId}
                    onChange={(e) => setMarkerId(e.target.value.replace(/[^\d]/g, ""))}
                    placeholder="auto"
                    disabled={running}
                  />
                </Label>
              </div>

              {/* 마커 역할 설정 — 마커 ID 는 현장에서 바뀌므로 코드가 아니라 여기 값을 쓴다 */}
              <div className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-2 text-xs font-bold text-slate-600">
                  마커 역할 설정 <span className="font-normal text-slate-400">— 현장에서 마커 ID 가 바뀌면 여기서 바꾼다</span>
                </div>

                <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                  <span>기본 도킹 마커: <b className="text-slate-700">{dockMarkerCfgId ?? `${DEFAULT_DOCK_MARKER_ID} (미설정 기본값)`}</b></span>
                  <button
                    type="button"
                    disabled={!canControl || running || markerId.trim() === ""}
                    onClick={() => void saveDockMarker()}
                    className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
                  >
                    마커 {markerId || "?"}번으로 지정
                  </button>
                </div>

                <div className="mb-1.5 text-[11px] text-slate-500">
                  유도 마커 <span className="text-slate-400">(도킹마커 방향 우회전 각도 · {GUIDE_TURN_REVERSE_DEG}° 이상이면 반대편 진입)</span>
                </div>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {guideIdsAll.length === 0 ? (
                    <span className="text-[11px] text-amber-600">
                      설정 없음 — 폴백값 사용 ({Object.entries(DEFAULT_GUIDE_TURN_TO_DOCK).map(([k, v]) => `${k}:${v}°`).join(", ")})
                    </span>
                  ) : guideIdsAll.map((id) => (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-700"
                    >
                      id {id} · {Math.abs(guideTurns[id])}°
                      <button
                        type="button"
                        disabled={!canControl || running}
                        onClick={() => void removeGuideMarker(id)}
                        className="ml-0.5 rounded px-1 text-[10px] text-slate-400 transition hover:text-rose-600 disabled:opacity-40"
                        title="유도 지정 해제"
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                  <span>마커</span>
                  <input
                    className="h-7 w-14 rounded-md border border-slate-300 px-2 text-[11px]"
                    value={guideFormId}
                    onChange={(e) => setGuideFormId(e.target.value.replace(/[^\d]/g, ""))}
                    placeholder="id"
                    disabled={!canControl || running}
                  />
                  <span>번, 회전</span>
                  <input
                    className="h-7 w-16 rounded-md border border-slate-300 px-2 text-[11px]"
                    value={guideFormDeg}
                    onChange={(e) => setGuideFormDeg(e.target.value.replace(/[^\d]/g, ""))}
                    placeholder="45"
                    disabled={!canControl || running}
                  />
                  <span>도</span>
                  <button
                    type="button"
                    disabled={!canControl || running || guideFormId.trim() === ""}
                    onClick={() => void saveGuideMarker()}
                    className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
                  >
                    유도 마커로 저장
                  </button>
                </div>
              </div>

              {/* 색 주차 — 색 → 지정된 마커로 도킹(기존 정밀 도킹 재사용) */}
              <div className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-2 text-xs font-bold text-slate-600">색 주차 <span className="font-normal text-slate-400">— 색 = 마커 선택, 실행은 '직접 주차'</span></div>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {PARK_COLORS.map((c) => {
                    const mapped = colorMap.find((x) => x.color === c.name);
                    return (
                      <button
                        key={c.name}
                        type="button"
                        disabled={!canControl || running || !mapped}
                        onClick={() => void parkByColor(c.name)}
                        className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold transition hover:opacity-90 disabled:opacity-40"
                        style={{ borderColor: c.hex }}
                        title={mapped ? `${c.name} → ${mapped.marker_id}번 마커로 주차` : `${c.name}: 지정된 마커 없음`}
                      >
                        <span className="inline-block h-3 w-3 rounded-full" style={{ background: c.hex }} />
                        {c.name}
                        {mapped ? <span className="text-slate-400">({mapped.marker_id})</span> : null}
                      </button>
                    );
                  })}
                </div>
                {/* 선택 구역으로 nav2 이동 → 도착 후 nav2 유지한 채 마커+색 정밀 주차 */}
                <button
                  type="button"
                  disabled={!canControl || running || markerId.trim() === ""}
                  onClick={() => void startColorParkFlow()}
                  className="mb-2 inline-flex w-full items-center justify-center gap-1.5 rounded-md bg-sky-600 px-3 py-2 text-[12px] font-semibold text-white shadow-sm transition hover:bg-sky-700 disabled:opacity-40"
                >
                  <Navigation className="h-3.5 w-3.5" /> {zone || DEFAULT_PARKING_ZONE} 구역 이동 후 주차 (nav2 유지)
                </button>
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                  <span>마커 <b>{markerId || "?"}</b>번에 색 지정:</span>
                  {PARK_COLORS.map((c) => (
                    <button
                      key={c.name}
                      type="button"
                      disabled={!canControl || running || markerId.trim() === ""}
                      onClick={() => void assignParkColor(c.name)}
                      title={c.name}
                      className="h-4 w-4 rounded-full border border-slate-300 transition hover:scale-110 disabled:opacity-40"
                      style={{ background: c.hex }}
                    />
                  ))}
                  <button
                    type="button"
                    disabled={!canControl || running || markerId.trim() === ""}
                    onClick={() => void assignParkColor("")}
                    className="ml-1 rounded border border-slate-300 px-1.5 text-[10px] text-slate-500 disabled:opacity-40"
                  >
                    해제
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <NumberField title="전면 목표" value={frontTarget} onChange={setFrontTarget} min={0.05} max={0.9} step={0.01} disabled={running} />
                <NumberField title="후면 목표" value={rearTarget} onChange={setRearTarget} min={0.05} max={0.9} step={0.01} disabled={running} />
                <NumberField title="벽 정지거리(cm)" value={wallTargetCm} onChange={setWallTargetCm} min={2} max={30} step={0.5} disabled={running} />
                <NumberField title="전진/후진 상한" value={linMax} onChange={setLinMax} min={0.05} max={0.5} step={0.01} disabled={running} />
              </div>
            </div>

            <div className="flex min-h-[330px] flex-col rounded-lg border border-slate-200 bg-slate-50 p-3">
              {/* 한눈에 보이는 상태 배너 */}
              <div className={cn("mb-3 flex items-center gap-3 rounded-lg border px-3 py-3", FLOW_META[phase].tone)}>
                <span className={cn("h-3.5 w-3.5 shrink-0 rounded-full", FLOW_META[phase].dot)} />
                <div className="min-w-0 flex-1">
                  <div className="text-base font-bold leading-tight">{FLOW_META[phase].label}</div>
                  <div className="truncate text-xs opacity-80">{message}</div>
                </div>
                {phase === "docking" && typeof shownWallCm === "number" && (
                  <div className="shrink-0 text-right leading-none">
                    <div className="text-xl font-bold tabular-nums">
                      {shownWallCm.toFixed(1)}<span className="ml-0.5 text-xs font-medium">cm</span>
                    </div>
                    <div className="mt-1 text-[10px] opacity-70">벽까지</div>
                  </div>
                )}
              </div>

              {/* 정렬/거리 준비 상태 배지 */}
              <div className="mb-3 grid grid-cols-2 gap-2">
                <ReadyBadge label="마커 확인" ok={precisionMode === "line" ? true : arucoStatus?.phase === "done" || arucoStatus?.telemetry?.center_ok === true} active={phase === "docking"} />
                <ReadyBadge label="2차 라인" ok={lineDetect?.found === true || lineStatus?.phase === "done"} active={phase === "docking"} />
              </div>

              <div className="mb-3 grid grid-cols-2 gap-2 text-sm">
                <StatusItem label="구역까지 거리" value={distance == null ? "-" : `${distance.toFixed(2)} m`} />
                <StatusItem label="마커 단계" value={dockPhaseLabel(arucoStatus?.phase)} />
                <StatusItem label="2차 단계" value={dockPhaseLabel(lineStatus?.phase)} />
                <StatusItem label="정밀 방식" value={precisionMode === "hybrid" ? "라인→마커" : precisionMode === "line" ? "흰 테이프" : "아르코"} />
                <StatusItem label="거리 기준" value={precisionMode === "line" ? "초음파/라인" : distSourceLabel(tele.dist_source)} />
                <StatusItem label="접근 방식" value={(typeof tele.approach === "string" ? tele.approach : mode) === "rear" ? "후면" : "전면"} />
                <StatusItem label="선속도" value={typeof tele.linear === "number" ? tele.linear.toFixed(3) : "-"} />
                <StatusItem label="각속도" value={typeof tele.angular === "number" ? tele.angular.toFixed(3) : "-"} />
              </div>

              <div className="mb-3 rounded-lg border border-slate-200 bg-white">
                <div className="border-b border-slate-200 px-3 py-2 text-xs font-bold text-slate-600">최근 상태 변경</div>
                <div className="max-h-48 overflow-auto p-2">
                  {statusEvents.length === 0 ? (
                    <div className="px-2 py-3 text-sm text-slate-400">상태 변경 대기 중</div>
                  ) : statusEvents.map((event) => (
                    <div key={event.id} className={cn("mb-2 rounded-md border px-2 py-2 text-xs last:mb-0", STATUS_TONE_CLASS[event.tone].shell)}>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="font-bold">{event.phase}</span>
                        <span className="font-mono text-[10px] opacity-70">{event.at}</span>
                      </div>
                      <div className="break-words leading-snug">{event.message}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-auto flex gap-2">
                <Button
                  className="h-10 flex-1 gap-2 bg-emerald-600 text-white hover:bg-emerald-700"
                  disabled={!canControl || !zone || running}
                  onClick={() => void startParking()}
                >
                  {phase === "navigating" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                  주차 시작
                </Button>
                <Button
                  variant="outline"
                  className="h-10 gap-2 border-emerald-300 text-emerald-700"
                  disabled={!canControl || phase === "docking"}
                  onClick={() => void startPrecisionDock().catch((e) => { setPhase("error"); setMessage(e instanceof Error ? e.message : "정밀 주차 시작 실패"); })}
                >
                  <Crosshair className="h-4 w-4" />
                  직접 주차
                </Button>
                <Button
                  variant="outline"
                  className="h-10 gap-2 border-slate-300"
                  disabled={!canControl}
                  onClick={() => void stopParking()}
                >
                  <Square className="h-4 w-4" />
                  정지
                </Button>
              </div>

              <div className="mt-2 rounded-lg border border-dashed border-amber-300 bg-amber-50/60 p-2">
                <div className="mb-1 text-[11px] font-bold text-amber-700">노드 테스트 (설정값 기반 · 단계별 검증)</div>
                <Button
                  variant="outline"
                  className="mb-2 h-10 w-full gap-2 border-emerald-400 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                  disabled={!canControl || testBusy || running}
                  onClick={() => void runEZoneMarker23LineWall3()}
                >
                  로봇2 E+{guideLabel(forwardGuideIds)}+라인+3cm
                </Button>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    className="h-9 flex-1 gap-2 border-amber-300 text-amber-800 hover:bg-amber-100"
                    disabled={!canControl || testBusy || running || reverseGuideIds.length === 0}
                    onClick={() => void runTurnFindLine(reverseGuideIds, -reverseGuideDeg, guideLabel(reverseGuideIds))}
                  >
                    {testBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Crosshair className="h-4 w-4" />}
                    {guideLabel(reverseGuideIds)} → {reverseGuideDeg}° 회전+라인
                  </Button>
                  <Button
                    variant="outline"
                    className="h-9 flex-1 gap-2 border-amber-300 text-amber-800 hover:bg-amber-100"
                    disabled={!canControl || testBusy || running || forwardGuideIds.length === 0}
                    onClick={() => void runTurnFindLine(forwardGuideIds, -forwardGuideDeg, guideLabel(forwardGuideIds))}
                  >
                    {testBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Crosshair className="h-4 w-4" />}
                    {guideLabel(forwardGuideIds)} → {forwardGuideDeg}° 회전+라인
                  </Button>
                </div>
                <Button
                  variant="outline"
                  className="mt-2 h-9 w-full gap-2 border-sky-300 text-sky-800 hover:bg-sky-100"
                  disabled={!canControl || testBusy || running}
                  onClick={() => void runLineFollow()}
                >
                  {testBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4" />}
                  라인 쫓아가기 (바닥 IR → 벽 설정값 정지, 현재 {Math.max(2, wallTargetCm)}cm)
                </Button>
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-3">
            <Step active={phase === "navigating"} done={["docking", "done"].includes(phase)} icon={<Navigation className="h-4 w-4" />} title="구역 이동" />
            <Step active={phase === "docking"} done={phase === "done"} icon={<Crosshair className="h-4 w-4" />} title={modeLabel(mode)} />
            <Step active={phase === "done"} done={phase === "done"} icon={<Square className="h-4 w-4" />} title="완료" />
          </div>
        </section>
      </div>

      <section className="mt-4 rounded-lg border border-slate-200 bg-slate-950 p-3">
        <RobotConsole
          robotId={robotId}
          robotBase={robotBase}
          robotName={robotName ?? undefined}
          canControl={canControl}
          variant="compact"
        />
      </section>
    </AdminShell>
  );
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-600">{title}</span>
      {children}
    </label>
  );
}

function NumberField({ title, value, onChange, min, max, step, disabled }: {
  title: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
}) {
  return (
    <Label title={title}>
      <input
        type="number"
        className="h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value) || min)}
      />
    </Label>
  );
}

function ModeButton({ active, disabled, onClick, children }: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "h-10 rounded-md border px-3 text-sm font-semibold transition",
        active ? "border-emerald-500 bg-emerald-50 text-emerald-800" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      {children}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-r border-slate-200 px-2 py-2 last:border-r-0">
      <div className="text-[11px] font-semibold text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-900">{value}</div>
    </div>
  );
}

function SensorLine({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex gap-2 leading-relaxed">
      <span className="w-24 shrink-0 text-slate-400">{label}</span>
      <span className={cn("flex-1 break-all", warn ? "font-bold text-red-600" : "text-slate-800")}>{value}</span>
    </div>
  );
}

function ReadyBadge({ label, ok, active }: { label: string; ok: boolean; active: boolean }) {
  const done = active && ok;
  return (
    <div className={cn(
      "flex items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-sm font-semibold transition",
      done ? "border-emerald-400 bg-emerald-50 text-emerald-700"
        : active ? "border-amber-300 bg-amber-50 text-amber-700"
          : "border-slate-200 bg-white text-slate-400",
    )}>
      {done ? <CheckCircle2 className="h-4 w-4" /> : active ? <Loader2 className="h-4 w-4 animate-spin" /> : <Circle className="h-4 w-4" />}
      {label}
      {done && <span className="text-xs font-normal">완료</span>}
    </div>
  );
}

function StatusBig({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={cn("rounded-md border px-2 py-2", warn ? "border-red-300 bg-red-100 text-red-950" : "border-white/70 bg-white/70")}>
      <div className="text-[10px] font-bold uppercase text-slate-500">{label}</div>
      <div className={cn("mt-1 font-mono text-xl font-black leading-none", warn ? "text-red-700" : "text-slate-950")}>{value}</div>
    </div>
  );
}

function StatusItem({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-2 py-2">
      <div className="text-[11px] font-semibold text-slate-500">{label}</div>
      <div className={cn("truncate text-sm text-slate-800", strong && "font-semibold")}>{value}</div>
    </div>
  );
}

function Step({ active, done, icon, title }: { active: boolean; done: boolean; icon: ReactNode; title: string }) {
  return (
    <div className={cn(
      "flex h-12 items-center gap-2 rounded-lg border px-3 text-sm font-semibold",
      done ? "border-emerald-500 bg-emerald-50 text-emerald-800" : active ? "border-sky-500 bg-sky-50 text-sky-800" : "border-slate-200 bg-slate-50 text-slate-500",
    )}>
      {icon}
      <span className="truncate">{title}</span>
    </div>
  );
}
