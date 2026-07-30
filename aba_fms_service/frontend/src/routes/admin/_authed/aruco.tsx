import { createFileRoute } from "@tanstack/react-router";
import { Crosshair, Play, Square, ScanEye } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { buildRobotWsUrl, getRobotBase } from "@/lib/active-robot";
import {
  adminApi,
  type MarkerActionItem,
  type MarkerActionType,
} from "@/lib/admin-api";
import {
  ACTION_LABELS,
  buildParams,
  runAction,
  ROBOT_EMOTIONS as EMOTIONS,
  LCD_FONTS,
} from "@/lib/robot-actions";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/aruco")({
  component: ArucoDockPage,
});

// 로봇 온보드 에이전트(robot_agent)의 실제 경로. /admin 유지되는 별도 저장소이므로 직접 호출한다.
//
// 주행은 전부 parkp — 로봇 온보드 pose 6-DOF 기하 주차(`robot_agent/app/routers/parkp.py`)로 돈다.
// 브라우저가 하는 일은 start / stop / status / plan 네 번의 HTTP 호출뿐이고,
// 검출 → solvePnP → 경유점 투영 → P 제어는 전부 로봇 안에서 끝난다.
// 그래서 브라우저를 닫아도 로봇이 자기 timeout_s 로 스스로 멈춘다(구 dock 방식과 가장 큰 차이).
const PARKP_BASE = "/api/robot/parkp";
// ⚠️ 검출 오버레이만 구 aruco_dock 의 웹소켓을 계속 쓴다. parkp 에는 검출 '스트림'이 없고
//    1회성 `GET /parkp/plan` 만 있다. 이 경로는 모터를 건드리지 않는 읽기 전용이다.
const DETECT_WS = "/api/robot/dock/ws/detect";
const CAM_STREAM = "/api/robot/camera/stream";
const ROBOT_BASE_KEY = "labi.dockRobotBase";
const MARKER_LEN_M = 0.05;
// 480px 프레임 기준 초점거리(px). parking.tsx 가 쓰는 실측값과 같은 값이다.
const CAM_FX_PX = 471;
// 핑키 차폭(m) — 진입 경로 좌우 레일 폭.
const ROBOT_WIDTH_M = 0.13;

const DICTS = [
  "DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_50", "DICT_5X5_100",
  "DICT_6X6_50", "DICT_6X6_100", "DICT_7X7_50", "DICT_APRILTAG_36h11",
];
// 드롭다운으로 고정하지 않는다 — 사전마다 id 범위가 다르고(DICT_4X4_50 은 0~49),
// 현장 마커가 실제로 큰 번호를 쓴다(2026-07-30 로봇2 앞 마커 = id 37).
// 숫자 입력 + '보이는 마커' 칩으로 아무 id 나 고를 수 있게 한다.
const MARKER_ID_MAX = 999;
// 아래 '마커별 액션 설정' 격자용. 이쪽은 기존 동작을 그대로 둔다(0~20).
const MARKER_IDS = Array.from({ length: 21 }, (_, i) => i);

/** parkp `_state.phase` 전체 목록(parkp.py 기준). 없는 값은 원문 그대로 보여준다. */
const PHASE_LABEL: Record<string, string> = {
  idle: "대기",
  starting: "시작 중",
  parking: "접근 중",
  coast: "타성 주행",
  recall: "복구 (전역좌표)",
  recall_cam: "복구 (카메라 탐색)",
  lost: "마커 분실",
  no_frame: "카메라 프레임 없음",
  safety_stop: "안전 정지 (장애물)",
  done: "완료",
  timeout: "시간 초과",
  stopped: "사용자 중지",
  error: "오류",
};

type Marker = {
  id: number; cx: number; cy: number; ex: number;
  size_frac: number; skew: number; side_px: number;
  pose?: { x_m: number; z_m: number; yaw_deg: number };
};
type DetectResp = {
  frame: { width: number; height: number };
  opencv: string; calibrated: boolean; count: number; markers: Marker[];
};
/** `GET /parkp/status` — 평평한 상태 객체. 구 dock 처럼 {state:{...}} 로 감싸 오는 경우도 받는다. */
// telemetry 에는 waypoint·pose·sample 같은 중첩 객체도 섞여 오므로 unknown 으로 받는다.
type ParkStatus = {
  running: boolean; phase: string; message: string;
  telemetry: Record<string, unknown>;
};
/**
 * `GET /parkp/plan` — 1~3 단계(탐지·계산·투영)만 돌린 미리보기. **모터 미동작.**
 * 주행을 걸기 전에 "지금 이 자세에서 로봇이 뭘 보고 어디로 갈 생각인지" 를 확인하는 용도다.
 */
type PlanResp = {
  calibrated: boolean; marker_id: number; seen_ids: number[]; found: boolean;
  detect: { cx: number; cy: number; side_px: number } | null;
  pose: { x_m: number; z_m: number; yaw_deg: number } | null;
  projection: {
    wx?: number; wz?: number;
    bearing_deg?: number; marker_bearing_deg?: number;
    marker_range?: number; axis_lat?: number;
  } | null;
  path: Array<{ x_m: number; z_m: number; label: string }>;
};

/** 오버레이가 쓰는 단일 형태. 대기 중엔 /parkp/plan, 주행 중엔 /parkp/status 에서 만든다. */
type Guide = {
  cx: number; cy: number; sidePx: number;   // 원본 프레임 픽셀
  x: number; z: number; yawDeg: number;     // 마커 3D (카메라 기준, +x = 오른쪽)
  wx: number; wz: number;                   // 진입 경유점
  bearingDeg: number; axisLat: number; range: number;
};

function initialBase(): string {
  if (typeof localStorage !== "undefined") {
    const saved = localStorage.getItem(ROBOT_BASE_KEY);
    if (saved) return saved;
  }
  return getRobotBase();
}

function ArucoDockPage() {
  const [base, setBase] = useState<string>(initialBase);
  const [baseInput, setBaseInput] = useState<string>(base);

  const [dictionary, setDictionary] = useState("DICT_4X4_50");
  // parkp 의 marker_id 는 **필수 정수**다(구 dock 의 '자동 선택'이 없다).
  // 여러 마커가 보일 때 무엇을 향해 갈지는 사람이 정해야 안전하다.
  const [markerId, setMarkerId] = useState(1);
  // 아래 기본값은 전부 로봇 ParkPConfig 의 기본값과 같게 맞춰 두었다.
  // 어긋나면 화면에 설정해 놓은 값과 실제 주행이 달라진다(2026-07-29 벽거리 8cm 사고).
  const [targetDistM, setTargetDistM] = useState(0.2);   // 마커 앞 정지 거리
  const [wallTargetCm, setWallTargetCm] = useState(8);   // 초음파 기준 정지 거리 = 완료 조건
  // ⚠️ 로봇 ParkPConfig 기본값(lin 0.13 / ang 0.07)을 그대로 쓰면 **전진이 아예 안 된다.**
  //   2026-07-30 로봇2 실측 + _drive 재현 계산: 관측된 세 자세(방위 -25.7° / +10.3° / +3°)
  //   전부에서 안쪽 바퀴가 32(=정지마찰)로 떨어지거나 제자리 회전으로 빠졌다.
  //   ang 을 lin 의 1/5 로 낮추고 PWM 을 올린 아래 조합은 세 자세 모두 L/R = 38~50 으로 전진한다.
  const [linMax, setLinMax] = useState(0.25);
  const [angMax, setAngMax] = useState(0.05);
  const [camXSign, setCamXSign] = useState(1);           // 조향 극성. 반대로 흐르면 뒤집는다.
  // ★ 안쪽 바퀴 최저 PWM. parkp `_drive` 는 곡선 주행에서 안쪽 바퀴를 정확히 stall_pwm 으로
  //   떨어뜨린다(안쪽 = min_drive × stall_pwm/min_drive = stall_pwm). 즉 '정지마찰 밑으로는
  //   안 간다'가 실제로는 '정지마찰에 딱 붙인다'라서, 32 로 두면 안쪽 바퀴가 안 돌고
  //   그 자리에서 피벗만 한다(2026-07-30 로봇2 실측: 120초간 0.88m 에서 전진 0).
  //   이 값을 올리면 안쪽 바퀴가 마찰 위로 올라가 곡선 전진이 성립한다.
  const [innerPwm, setInnerPwm] = useState(38);
  const [minDrive, setMinDrive] = useState(50);          // 바깥(peak) 바퀴 PWM

  const [detecting, setDetecting] = useState(false);
  const [detect, setDetect] = useState<DetectResp | null>(null);
  const [status, setStatus] = useState<ParkStatus | null>(null);
  const [plan, setPlan] = useState<PlanResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 마커 액션 설정
  const [actions, setActions] = useState<MarkerActionItem[]>([]);
  const [autoAction, setAutoAction] = useState(false);
  const [actionLog, setActionLog] = useState<string[]>([]);
  const firedRef = useRef<Record<number, number>>({});

  const canvasRef = useRef<HTMLCanvasElement>(null);

  // ── 검출/상태 스트림 ───────────────────────────────────────
  useEffect(() => {
    if (!detecting) return;
    // ★ marker_len_m 을 반드시 넘긴다. 안 넘기면 aruco_dock 이 solvePnP 를 건너뛰어
    //   markers[].pose 가 통째로 빠지고, 가이드 선을 그릴 3D 정보가 없어진다
    //   (2026-07-30: 가이드가 안 그려지던 원인 중 하나).
    const path = `${DETECT_WS}?dictionary=${encodeURIComponent(dictionary)}`
      + `&marker_len_m=${MARKER_LEN_M}`;
    const ws = new WebSocket(buildRobotWsUrl(base, path));
    ws.onmessage = (event) => {
      try {
        const data: DetectResp = JSON.parse(event.data);
        setDetect(data);
        setError(null);
      } catch {
        setError("검출 스트림 해석 실패");
      }
    };
    ws.onerror = () => setError("검출 스트림 연결 오류");
    return () => ws.close();
  }, [base, detecting, dictionary]);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${base}${PARKP_BASE}/status`);
      if (r.ok) {
        const data = await r.json();
        setStatus((data?.state ?? data) as ParkStatus);
      }
    } catch {
      /* 무시 */
    }
  }, [base]);

  // parkp 에는 상태 웹소켓이 없어 폴링한다. 주행 중에는 촘촘히(400ms), 멈춰 있으면 느슨하게(2s).
  // 로봇 루프가 12Hz 라 400ms 면 화면이 끊겨 보이지 않으면서 요청도 과하지 않다.
  const running = status?.running ?? false;
  useEffect(() => {
    void fetchStatus();
    const id = window.setInterval(() => void fetchStatus(), running ? 400 : 2000);
    return () => window.clearInterval(id);
  }, [fetchStatus, running]);

  // 오버레이가 쓰는 기하 — 주행 중엔 status.telemetry(400ms), 대기 중엔 plan(1s).
  // 픽셀 좌표(cx/cy/side_px)는 telemetry 에 없으므로 검출 스트림의 대상 마커에서 가져온다.
  const guide = useMemo<Guide | null>(() => {
    const px = detect?.markers.find((m) => m.id === markerId);
    const t = status?.telemetry;
    if (running && t && typeof t.bearing_deg === "number") {
      const pose = t.pose as { x_m: number; z_m: number; yaw_deg: number } | null | undefined;
      const wp = t.waypoint as { x_m: number; z_m: number } | undefined;
      if (!pose || !wp || !px) return null;
      return {
        cx: px.cx, cy: px.cy, sidePx: px.side_px,
        x: pose.x_m, z: pose.z_m, yawDeg: pose.yaw_deg,
        wx: wp.x_m, wz: wp.z_m,
        bearingDeg: t.bearing_deg,
        axisLat: typeof t.axis_lat === "number" ? t.axis_lat : 0,
        range: typeof t.dist === "number" ? t.dist : pose.z_m,
      };
    }
    if (plan?.found && plan.pose && plan.projection && plan.detect) {
      const pr = plan.projection;
      return {
        cx: plan.detect.cx, cy: plan.detect.cy, sidePx: plan.detect.side_px,
        x: plan.pose.x_m, z: plan.pose.z_m, yawDeg: plan.pose.yaw_deg,
        wx: pr.wx ?? plan.pose.x_m, wz: pr.wz ?? plan.pose.z_m,
        bearingDeg: pr.bearing_deg ?? 0,
        axisLat: pr.axis_lat ?? 0,
        range: pr.marker_range ?? plan.pose.z_m,
      };
    }
    return null;
  }, [detect, markerId, plan, running, status]);

  // 주행 전 미리보기 — 주행 중에는 status.telemetry 가 같은 값을 더 정확히 주므로 쉰다.
  useEffect(() => {
    if (!detecting || running) { setPlan(null); return; }
    let alive = true;
    const tick = async () => {
      const q = new URLSearchParams({
        marker_id: String(markerId),
        dictionary,
        marker_len_m: String(MARKER_LEN_M),
        target_distance_m: String(targetDistM),
        cam_x_sign: String(camXSign),
      });
      try {
        const r = await fetch(`${base}${PARKP_BASE}/plan?${q}`);
        if (alive && r.ok) setPlan((await r.json()) as PlanResp);
      } catch {
        /* 카메라 미기동 등 — 오버레이는 웹소켓이 따로 그리므로 조용히 넘어간다 */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => { alive = false; window.clearInterval(id); };
  }, [base, camXSign, detecting, dictionary, markerId, running, targetDistM]);

  // ── 오버레이 그리기 (카메라는 수직 반전이므로 y 를 뒤집어 정렬) ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !detect) return;
    const { width: fw, height: fh } = detect.frame;
    if (canvas.width !== fw) canvas.width = fw;
    if (canvas.height !== fh) canvas.height = fh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, fw, fh);

    // 중앙 세로 가이드
    ctx.strokeStyle = "rgba(255,255,255,0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(fw / 2, 0);
    ctx.lineTo(fw / 2, fh);
    ctx.stroke();

    // ★ 표시 공간은 parking.tsx 와 **동일**하게 맞춘다.
    //   영상은 <img> 에 rotate(180deg) 를 걸고(= 가로·세로 both 반전),
    //   오버레이는 가로 MX(u)=W-u, 세로 H-cy 로 같은 공간에 그린다.
    //   두 화면이 서로 다른 규약을 쓰면 같은 로봇 영상이 화면마다 뒤집혀 보인다.
    const MX = (u: number) => fw - u;

    for (const m of detect.markers) {
      const side = m.side_px;
      const x = MX(m.cx) - side / 2;
      const y = fh - m.cy - side / 2;
      // parkp 는 설정한 marker_id **하나만** 쫓는다. 그래서 색을 대상/비대상으로 가른다 —
      // 여러 마커가 보일 때 "로봇이 저걸 향해 간다"를 눈으로 바로 확인할 수 있어야 한다.
      const isTarget = m.id === markerId;
      const centered = Math.abs(m.ex) <= 0.06;
      ctx.strokeStyle = isTarget ? (centered ? "#22c55e" : "#f59e0b") : "rgba(148,163,184,0.7)";
      ctx.lineWidth = isTarget ? 3 : 1.5;
      ctx.strokeRect(x, y, side, side);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.font = isTarget ? "bold 18px monospace" : "14px monospace";
      ctx.fillText(isTarget ? `▶ id ${m.id}` : `id ${m.id}`, x, Math.max(16, y - 6));
    }

    // ── 가이드 ────────────────────────────────────────────────────────────────
    // pose 가 없으면(미보정 / marker_len_m 미전달) 그릴 수 없다.
    if (!guide) return;
    const { cx, cy, sidePx, x: mxm, z: mzm, wx, wz, bearingDeg, axisLat, range } = guide;

    // 마커의 **표시 좌표** (rotate(180deg) 공간)
    const mkx = MX(cx);
    const mky = fh - cy;

    // 가로 투영: u(x,z) = W/2 + fx·x/z.
    // fx 는 마커 자신의 (표시픽셀, 3D) 대응으로 보정한다. 표시 공간이 가로 반전이라
    // 여기서 나오는 fx 는 **음수**가 되며, 그게 곧 반전을 담아낸다(parking.tsx 와 동일).
    const fxCal = -CAM_FX_PX * (fw / 480);
    const fx = Math.abs(mxm) > 0.02 && Math.abs(mkx - fw / 2) > 4
      ? ((mkx - fw / 2) * mzm) / mxm
      : fxCal;
    // 세로 투영(지면 가정): v(z) = A + B/z.
    //   두 점으로 보정 — (마커 z ↔ 마커 표시 y) 와 (아주 가까운 zNear ↔ 화면 하단).
    //   마커가 지면이 아니라 벽에 붙어 있으면 근사가 되지만, 경로의 '수렴하는 느낌'을
    //   내는 데는 충분하다(parking.tsx 가 현장에서 쓰던 것과 같은 방식).
    const zNear = Math.min(0.18, mzm * 0.5);
    const B = (fh - mky) / (1 / zNear - 1 / mzm);
    const A = fh - B / zNear;
    const uOf = (px: number, pz: number) => fw / 2 + (fx * px) / Math.max(pz, 0.08);
    const vOf = (pz: number) => A + B / Math.max(pz, 0.08);

    const aligned = Math.abs(bearingDeg) <= 6;
    const tone = aligned ? "#22c55e" : Math.abs(bearingDeg) <= 20 ? "#eab308" : "#ef4444";

    // 1) 마커 정면축 — 이 세로 점선 위에 로봇을 올리면 정면 주차다.
    ctx.strokeStyle = "rgba(34,211,238,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 5]);
    ctx.beginPath();
    ctx.moveTo(mkx, Math.max(0, mky - sidePx));
    ctx.lineTo(mkx, fh);
    ctx.stroke();
    ctx.setLineDash([]);

    // 2) 진입 경로 — 로봇(0,0) → 진입 경유점 W → 마커. parkp 가 실제로 계획한 경로다.
    //    직접 시뮬레이션하지 않고 로봇이 준 좌표를 그대로 투영한다.
    const seg = (x0: number, z0: number, x1: number, z1: number, steps: number) =>
      Array.from({ length: steps + 1 }, (_, i) => {
        const t = i / steps;
        return [x0 + (x1 - x0) * t, z0 + (z1 - z0) * t] as const;
      });
    const pts = [...seg(0, 0.02, wx, wz, 24), ...seg(wx, wz, mxm, mzm, 16)];

    ctx.shadowColor = "rgba(0,0,0,0.6)";
    ctx.shadowBlur = 3;
    ctx.fillStyle = tone;
    for (let i = 0; i < pts.length; i += 2) {
      const [px, pz] = pts[i];
      if (pz <= zNear) continue;
      const r = Math.max(2, 6 - (4 * pz) / mzm);
      ctx.beginPath();
      ctx.arc(uOf(px, pz), vOf(pz), r, 0, Math.PI * 2);
      ctx.fill();
    }
    // 차폭 레일 — 경로 양옆 ±차폭/2. 좁은 통로에서 들어갈 수 있는지 눈으로 본다.
    ctx.globalAlpha = 0.5;
    for (const s of [-1, 1]) {
      for (let i = 0; i < pts.length; i += 3) {
        const [px, pz] = pts[i];
        if (pz <= zNear) continue;
        ctx.beginPath();
        ctx.arc(uOf(px + s * (ROBOT_WIDTH_M / 2), pz), vOf(pz),
                Math.max(1.5, 3.5 - (2 * pz) / mzm), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    ctx.shadowBlur = 0;

    // 3) 진입 경유점 W 표시 — 로봇이 지금 향하고 있는 점.
    const wu = uOf(wx, wz);
    const wv = vOf(wz);
    ctx.strokeStyle = tone;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(wu, wv, 7, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(wu - 11, wv);
    ctx.lineTo(wu + 11, wv);
    ctx.stroke();

    // 4) 수치 HUD
    const hud = [
      `거리  ${(range * 100).toFixed(0)}cm`,
      `방위  ${bearingDeg >= 0 ? "+" : ""}${bearingDeg.toFixed(1)}° · 면 ${guide.yawDeg.toFixed(0)}°`,
      `축이탈 ${(axisLat * 100).toFixed(1)}cm`,
    ];
    const bw = 186;
    const bx = Math.min(fw - bw - 6, Math.max(6, mkx + sidePx / 2 + 12));
    const by = Math.max(6, mky - 34);
    ctx.fillStyle = "rgba(2,6,23,0.78)";
    ctx.fillRect(bx, by, bw, 62);
    ctx.strokeStyle = tone;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bx, by, bw, 62);
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "bold 14px monospace";
    hud.forEach((line, i) => ctx.fillText(line, bx + 8, by + 19 + i * 17));
  }, [detect, guide, markerId]);


  const loadActions = useCallback(async () => {
    try { setActions(await adminApi.listMarkerActions()); }
    catch { /* 백엔드 재시작 전이면 무시 */ }
  }, []);
  useEffect(() => { void loadActions(); }, [loadActions]);

  // 인식 시 지정된 액션 자동 실행 (마커별 6초 쿨다운)
  useEffect(() => {
    if (!autoAction || !detect) return;
    const now = Date.now();
    for (const m of detect.markers) {
      const a = actions.find((x) => x.marker_id === m.id && x.enabled && x.action_type !== "none");
      if (!a) continue;
      if (now - (firedRef.current[m.id] ?? 0) < 6000) continue;
      firedRef.current[m.id] = now;
      void runAction(base, a);
      setActionLog((log) =>
        [`${new Date().toLocaleTimeString()} · id ${m.id} → ${ACTION_LABELS[a.action_type]}`, ...log].slice(0, 6));
    }
  }, [detect, autoAction, actions, base]);

  function saveBase() {
    const v = baseInput.trim().replace(/\/$/, "");
    setBase(v);
    localStorage.setItem(ROBOT_BASE_KEY, v);
  }

  async function startPark() {
    setBusy(true);
    setError(null);
    try {
      const cfg: Record<string, unknown> = {
        marker_id: markerId,
        dictionary,
        marker_len_m: MARKER_LEN_M,
        target_distance_m: targetDistM,
        // ★ 벽 정지거리는 반드시 같이 넘긴다. 안 넘기면 로봇 기본값 8cm 가 쓰여
        //   화면에 3cm 로 맞춰 놓고 8cm 에서 서 버린다(2026-07-29 실측).
        //   감속 시작점(slow)은 정지 거리보다 확실히 커야 해서 +8cm 로 둔다.
        use_wall_sensor: true,
        target_wall_cm: Math.max(3, wallTargetCm),
        slow_wall_cm: Math.max(5, Math.max(3, wallTargetCm) + 8),
        lin_max: linMax,
        ang_max: angMax,
        cam_x_sign: camXSign,
        // 안쪽 = min_drive × (stall_pwm/min_drive) = stall_pwm 이므로, 안쪽 바퀴를 실제로
        // 마찰 위로 올리려면 stall_pwm 을 올려야 한다(min_drive 만 올려도 안쪽은 그대로다).
        min_drive: minDrive,
        stall_pwm: Math.min(innerPwm, minDrive),
        // kp_ang / kp_cross / kp_lin / 복구 파라미터는 로봇 기본값을 쓴다.
        // 실측 근거는 parkp.py 의 _drive 주석 참고.
      };
      const r = await fetch(`${base}${PARKP_BASE}/start`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!r.ok) {
        // 409 = 이미 주행 중, 422 = 설정값이 로봇 제약을 벗어남. 본문 detail 을 그대로 보여준다.
        let detail = "";
        try {
          const body = await r.json();
          if (typeof body?.detail === "string") detail = ` · ${body.detail}`;
          else if (body?.detail) detail = ` · ${JSON.stringify(body.detail)}`;
        } catch { /* 본문 없음 */ }
        throw new Error(`시작 실패: HTTP ${r.status}${detail}`);
      }
      setDetecting(true); // 주행 중에도 오버레이 유지
      await fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "주행 시작 실패");
    } finally {
      setBusy(false);
    }
  }

  async function stopPark() {
    setBusy(true);
    try {
      await fetch(`${base}${PARKP_BASE}/stop`, { method: "POST" });
      await fetchStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "정지 실패");
    } finally {
      setBusy(false);
    }
  }

  const top = detect?.markers[0];
  const tele = status?.telemetry ?? {};
  // 검출 웹소켓과 plan 응답 양쪽에서 모은다 — 인식만 켠 상태와 주행 중 어느 쪽이든 목록이 나온다.
  const seenIds = Array.from(
    new Set([...(detect?.markers.map((m) => m.id) ?? []), ...(plan?.seen_ids ?? [])]),
  ).sort((a, b) => a - b);

  return (
    <AdminShell title="아르코 마커 주행 (parkp)">
      <div className="flex flex-col gap-3 lg:flex-row">
        {/* ── 좌: 카메라 + 오버레이 ── */}
        <div className="flex flex-col gap-2 lg:w-[420px]">
          <div
            className="relative w-full overflow-hidden rounded-lg bg-black shadow-sm"
            style={{ aspectRatio: "4/3" }}
          >
            <img
              src={`${base}${CAM_STREAM}`}
              alt="로봇 카메라"
              className="absolute inset-0 h-full w-full object-contain"
              style={{ transform: "rotate(180deg)" }}
            />
            <canvas
              ref={canvasRef}
              className="absolute inset-0 h-full w-full"
            />
            <div className="absolute left-2 top-2 flex items-center gap-1.5">
              <span className="rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-300">
                ArUco · {detect?.opencv ?? "--"}
              </span>
              {detecting && (
                <span className="flex items-center gap-1 rounded bg-emerald-600/80 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">
                  <span className="h-1.5 w-1.5 animate-ping rounded-full bg-white" />
                  DETECT
                </span>
              )}
            </div>
            <div className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-400">
              {detect ? `검출 ${detect.count}` : "대기"}
            </div>
          </div>

          {/* 실시간 검출 요약 */}
          <div className="grid grid-cols-4 gap-2 text-center">
            <Metric label="ID" value={top ? String(top.id) : "--"} />
            <Metric label="좌우오차 ex" value={top ? top.ex.toFixed(2) : "--"}
              tone={top ? (Math.abs(top.ex) <= 0.06 ? "ok" : "warn") : "idle"} />
            <Metric label="크기 size" value={top ? top.size_frac.toFixed(3) : "--"} />
            <Metric label="기울기 skew" value={top ? top.skew.toFixed(2) : "--"} />
          </div>
          {error && (
            <div className="rounded border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
              {error}
            </div>
          )}
        </div>

        {/* ── 우: 설정 + 제어 + 상태 ── */}
        <div className="flex flex-1 flex-col gap-3">
          {/* 로봇 주소 */}
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-1 text-xs font-medium text-slate-500">로봇 주소 (robot_agent)</div>
            <div className="flex gap-2">
              <input
                value={baseInput}
                onChange={(e) => setBaseInput(e.target.value)}
                placeholder="http://192.168.0.28:9001"
                className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm font-mono"
              />
              <Button size="sm" variant="outline" onClick={saveBase}>저장</Button>
            </div>
            <div className="mt-1 text-[11px] text-slate-400">현재: <span className="font-mono">{base}</span></div>
          </div>

          {/* 설정 */}
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-700">주행 설정 (parkp)</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-500">
                pose 6-DOF
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-600">
                마커 사전
                <select
                  value={dictionary}
                  onChange={(e) => setDictionary(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                >
                  {DICTS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              <label className="text-xs text-slate-600">
                주행 대상 마커 ID
                <input
                  type="number"
                  min={0}
                  max={MARKER_ID_MAX}
                  value={markerId}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    if (Number.isFinite(n)) setMarkerId(Math.max(0, Math.min(MARKER_ID_MAX, Math.trunc(n))));
                  }}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm font-mono"
                />
              </label>
              {/* 지금 보이는 id 를 한 번에 집는다 — 손으로 번호를 외워 칠 필요가 없다. */}
              <div className="col-span-2 flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-slate-500">보이는 마커</span>
                {seenIds.length === 0 && <span className="text-xs text-slate-400">없음</span>}
                {seenIds.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setMarkerId(id)}
                    className={cn(
                      "rounded px-2 py-0.5 font-mono text-xs",
                      id === markerId
                        ? "bg-emerald-600 text-white"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                    )}
                  >
                    {id}
                  </button>
                ))}
              </div>
              <Slider label={`마커 앞 정지 거리 ${targetDistM.toFixed(2)} m`}
                min={0.05} max={0.6} step={0.01} value={targetDistM} onChange={setTargetDistM} />
              <Slider label={`벽 정지 거리 ${wallTargetCm} cm (초음파 · 완료 조건)`}
                min={3} max={40} step={1} value={wallTargetCm} onChange={setWallTargetCm} />
              <Slider label={`전진 속도 상한 ${linMax.toFixed(2)}`}
                min={0.02} max={0.35} step={0.01} value={linMax} onChange={setLinMax} />
              <Slider label={`회전 속도 상한 ${angMax.toFixed(2)}`}
                min={0.02} max={0.3} step={0.01} value={angMax} onChange={setAngMax} />
              <Slider label={`바깥 바퀴 PWM ${minDrive} (min_drive)`}
                min={35} max={70} step={1} value={minDrive} onChange={setMinDrive} />
              <Slider label={`안쪽 바퀴 최저 PWM ${innerPwm} (stall_pwm)`}
                min={32} max={60} step={1} value={innerPwm} onChange={setInnerPwm} />
              <label className="col-span-2 flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={camXSign < 0}
                  onChange={(e) => setCamXSign(e.target.checked ? -1 : 1)}
                />
                조향 반전 (cam_x_sign = {camXSign})
                <span className="text-slate-400">— 마커 반대로 돌면 체크</span>
              </label>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
              ⚠️ 회전은 전진보다 실효 권한이 훨씬 큽니다(트랙폭 9.6cm). <b>회전 상한은 전진 상한의
              절반 이하</b>로 두세요.
              <br />
              ⚠️ <b>안쪽 바퀴 최저 PWM</b>은 곡선 주행에서 안쪽 바퀴가 실제로 도는 하한입니다.
              로봇 기본값 32 는 정지마찰 경계라 바퀴가 안 돌고 제자리 피벗만 합니다
              (2026-07-30 실측: 120초간 0.88m 에서 전진 0). <b>36~40</b> 을 쓰세요.
              나머지 이득(kp_ang·kp_cross·kp_lin)과 분실 복구 설정은 로봇 기본값을 씁니다.
            </p>
          </div>

          {/* 주행 전 미리보기 — 모터를 건드리지 않는 GET /parkp/plan */}
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex items-center gap-2">
              <ScanEye className="h-4 w-4 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">주행 계획 미리보기</span>
              <span className="ml-auto text-[11px] text-slate-400">
                {running ? "주행 중 — 아래 상태 참고"
                  : !detecting ? "인식 시작 후 표시"
                  : plan?.found ? `id ${plan.marker_id} 포착`
                  : `id ${markerId} 안 보임${plan?.seen_ids?.length ? ` (보이는 건 ${plan.seen_ids.join(",")})` : ""}`}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
              <Metric label="마커 거리 m" value={fmt(plan?.projection?.marker_range, 3)} />
              <Metric label="목표 방위 °" value={fmt(plan?.projection?.bearing_deg, 1)} />
              <Metric label="축이탈 m" value={fmt(plan?.projection?.axis_lat, 3)} />
              <Metric label="마커 기울기 °" value={fmt(plan?.pose?.yaw_deg, 1)} />
            </div>
            {plan && !plan.calibrated && (
              <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
                카메라 미보정 — <span className="font-mono">config/camera_calib.npz</span> 가 없어
                거리(m) 대신 마커 크기 비율로만 제어합니다. 정밀 주차는 캘리브레이션 후 사용하세요.
              </div>
            )}
          </div>

          {/* 제어 */}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={detecting ? "secondary" : "outline"}
              onClick={() => setDetecting((v) => !v)}
              className="gap-1.5"
            >
              <ScanEye className="h-4 w-4" />
              {detecting ? "인식 중지" : "인식 시작"}
            </Button>
            <Button
              onClick={startPark}
              disabled={busy || running}
              className="gap-1.5 bg-emerald-600 hover:bg-emerald-700"
            >
              <Play className="h-4 w-4" /> id {markerId} 로 주행
            </Button>
            <Button
              onClick={stopPark}
              disabled={busy}
              variant="destructive"
              className="gap-1.5"
            >
              <Square className="h-4 w-4" /> 정지
            </Button>
            <Button
              variant={autoAction ? "secondary" : "outline"}
              onClick={() => setAutoAction((v) => !v)}
              className="gap-1.5"
              title="마커 인식 시 지정 액션 자동 실행"
            >
              <Crosshair className="h-4 w-4" />
              {autoAction ? "자동실행 ON" : "자동실행 OFF"}
            </Button>
          </div>

          {/* 상태 */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 flex items-center gap-2">
              <Crosshair className="h-4 w-4 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">주행 상태</span>
              <span className={cn(
                "ml-auto rounded px-2 py-0.5 text-xs font-medium",
                running ? "bg-emerald-100 text-emerald-700"
                  : status?.phase === "done" ? "bg-blue-100 text-blue-700"
                  : status?.phase === "safety_stop" || status?.phase === "error"
                    ? "bg-red-100 text-red-700"
                    : "bg-slate-200 text-slate-600"
              )}>
                {PHASE_LABEL[status?.phase ?? "idle"] ?? status?.phase ?? "idle"}
              </span>
            </div>
            {status?.message && <div className="mb-2 text-xs text-slate-500">{status.message}</div>}
            <div className="grid grid-cols-3 gap-2 text-center text-xs sm:grid-cols-6">
              <Metric label="거리 m" value={fmt(tele.dist, 3)} />
              <Metric label="방위 °" value={fmt(tele.bearing_deg, 1)}
                tone={typeof tele.bearing_ok === "boolean" ? (tele.bearing_ok ? "ok" : "warn") : "idle"} />
              <Metric label="축이탈 m" value={fmt(tele.axis_lat, 3)} />
              <Metric label="벽 cm" value={fmt(tele.wall_cm, 1)} />
              <Metric label="linear" value={fmt(tele.linear, 3)} />
              <Metric label="angular" value={fmt(tele.angular, 3)} />
              <Metric label="L" value={fmt(tele.left)} />
              <Metric label="R" value={fmt(tele.right)} />
              <Metric label="거리 출처" value={tele.dist_source === "pose_m" ? "pose" : fmt(tele.dist_source)} />
            </div>
          </div>

          {/* 마커별 액션 설정 */}
          <MarkerActionEditor actions={actions} base={base} onSaved={loadActions} />

          {/* 실행 로그 */}
          {actionLog.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <div className="mb-1 text-xs font-semibold text-slate-700">액션 실행 로그</div>
              <ul className="space-y-0.5 text-[11px] font-mono text-slate-500">
                {actionLog.map((l, i) => <li key={i}>{l}</li>)}
              </ul>
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-slate-400">
            ⚠️ <b>주행</b>은 실제 로봇 모터를 움직입니다. 넓은 공간 또는 바퀴를 띄운 상태에서
            낮은 속도로 먼저 확인하고, 이상 시 즉시 <b>정지</b>를 누르세요.
            <br />
            판단(검출 → solvePnP → 경유점 투영 → P 제어)은 전부 로봇 온보드
            <span className="font-mono"> /api/robot/parkp </span>에서 돕니다. 브라우저를 닫아도
            로봇이 자기 <span className="font-mono">timeout_s</span>(기본 120초)로 스스로 멈추며,
            마커를 놓쳐도 최근 관측 기억으로 되찾습니다.
            <br />
            <b>주행 계획 미리보기</b>는 모터를 건드리지 않으니, 시작 전에 거리·방위·축이탈을
            먼저 확인하세요.
          </p>
        </div>
      </div>
    </AdminShell>
  );
}

function fmt(v: unknown, digits?: number): string {
  if (v === undefined || v === null) return "--";
  if (typeof v === "boolean") return v ? "O" : "X";
  if (typeof v === "number") return digits === undefined ? String(v) : v.toFixed(digits);
  return typeof v === "string" ? v : "--";
}

function Metric({ label, value, tone = "idle" }: {
  label: string; value: string; tone?: "ok" | "warn" | "idle";
}) {
  return (
    <div className="rounded border border-slate-200 bg-white px-2 py-1.5">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={cn(
        "font-mono text-sm font-semibold",
        tone === "ok" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : "text-slate-800"
      )}>
        {value}
      </div>
    </div>
  );
}

function Slider({ label, min, max, step, value, onChange }: {
  label: string; min: number; max: number; step: number;
  value: number; onChange: (v: number) => void;
}) {
  return (
    <label className="col-span-2 text-xs text-slate-600">
      {label}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full"
      />
    </label>
  );
}

const inputCls = "mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm";

function MarkerActionEditor({ actions, base, onSaved }: {
  actions: MarkerActionItem[]; base: string; onSaved: () => Promise<void> | void;
}) {
  const [mid, setMid] = useState(7);
  const [type, setType] = useState<MarkerActionType>("rotate");
  const [enabled, setEnabled] = useState(true);
  const [p, setP] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const ex = actions.find((a) => a.marker_id === mid);
    if (ex) {
      setType(ex.action_type); setEnabled(ex.enabled);
      setP(Object.fromEntries(Object.entries(ex.params ?? {}).map(([k, v]) => [k, String(v)])));
    } else { setType("rotate"); setEnabled(true); setP({}); }
  }, [mid, actions]);

  const f = (k: string, d = "") => p[k] ?? d;
  const set = (k: string, v: string) => setP((prev) => ({ ...prev, [k]: v }));

  async function save() {
    setBusy(true); setMsg(null);
    try {
      await adminApi.upsertMarkerAction(mid, { action_type: type, enabled, params: buildParams(type, p), label: null });
      await onSaved(); setMsg("저장됨");
    } catch (e) { setMsg(e instanceof Error ? e.message : "저장 실패 (백엔드 재시작 필요?)"); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await adminApi.deleteMarkerAction(mid); await onSaved(); setMsg("삭제됨"); }
    catch (e) { setMsg(e instanceof Error ? e.message : "삭제 실패"); }
    finally { setBusy(false); }
  }
  async function test() {
    setMsg("테스트 전송...");
    try { await runAction(base, { marker_id: mid, action_type: type, enabled, params: buildParams(type, p), label: null }); setMsg("테스트 전송됨"); }
    catch { setMsg("테스트 실패"); }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="mb-2 text-sm font-semibold text-slate-700">마커별 액션 설정</div>

      {/* 설정된 액션 요약 */}
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
          <span>Saved actions {actions.filter((a) => a.action_type !== "none").length}</span>
          <span>Click marker to edit</span>
        </div>
        <div className="grid grid-cols-7 gap-1">
          {MARKER_IDS.map((id) => {
            const a = actions.find((x) => x.marker_id === id);
            const configured = Boolean(a && a.action_type !== "none");
            return (
              <button
                key={id}
                type="button"
                onClick={() => setMid(id)}
                className={cn(
                  "h-8 rounded border text-[11px] font-medium",
                  mid === id ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200",
                  configured
                    ? a?.enabled ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-400"
                    : "bg-white text-slate-400"
                )}
                title={configured && a ? ACTION_LABELS[a.action_type] : "not set"}
              >
                #{id}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-slate-600">
          마커 ID
          <select value={mid} onChange={(e) => setMid(Number(e.target.value))} className={inputCls}>
            {MARKER_IDS.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          액션
          <select value={type} onChange={(e) => setType(e.target.value as MarkerActionType)} className={inputCls}>
            {(Object.keys(ACTION_LABELS) as MarkerActionType[]).map((t) => <option key={t} value={t}>{ACTION_LABELS[t]}</option>)}
          </select>
        </label>

        {type === "rotate" && (<>
          <label className="text-xs text-slate-600">각도 (°, +좌/-우)
            <input value={f("angle", "90")} onChange={(e) => set("angle", e.target.value)} className={inputCls} /></label>
          <label className="text-xs text-slate-600">속도 (0~1)
            <input value={f("speed", "0.3")} onChange={(e) => set("speed", e.target.value)} className={inputCls} /></label>
        </>)}
        {type === "move" && (<>
          <label className="text-xs text-slate-600">방향
            <select value={f("direction", "forward")} onChange={(e) => set("direction", e.target.value)} className={inputCls}>
              {["forward", "backward", "left", "right"].map((d) => <option key={d} value={d}>{d}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">거리 (m)
            <input value={f("distance", "0.3")} onChange={(e) => set("distance", e.target.value)} className={inputCls} /></label>
        </>)}
        {type === "lcd_emotion" && (
          <label className="col-span-2 text-xs text-slate-600">표정
            <select value={f("emotion", "happy")} onChange={(e) => set("emotion", e.target.value)} className={inputCls}>
              {EMOTIONS.map((em) => <option key={em} value={em}>{em}</option>)}
            </select></label>
        )}
        {type === "lcd_text" && (<>
          <label className="col-span-2 text-xs text-slate-600">텍스트
            <input value={f("text")} onChange={(e) => set("text", e.target.value)} placeholder="예: B구역 도착" className={inputCls} /></label>
          <label className="text-xs text-slate-600">폰트
            <select value={f("font_name", "MaruBuri-Bold.ttf")} onChange={(e) => set("font_name", e.target.value)} className={inputCls}>
              {LCD_FONTS.map((font) => <option key={font} value={font}>{font}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">크기
            <input value={f("font_size", "24")} onChange={(e) => set("font_size", e.target.value.replace(/[^0-9]/g, ""))} className={inputCls} /></label>
          <label className="text-xs text-slate-600">글자색
            <input type="color" value={f("color", "#ffffff")} onChange={(e) => set("color", e.target.value)} className="mt-1 h-9 w-full rounded border border-slate-300 px-1 py-1" /></label>
          <label className="text-xs text-slate-600">배경색
            <input type="color" value={f("bg_color", "#000000")} onChange={(e) => set("bg_color", e.target.value)} className="mt-1 h-9 w-full rounded border border-slate-300 px-1 py-1" /></label>
          <label className="col-span-2 text-xs text-slate-600">정렬
            <select value={f("align", "center")} onChange={(e) => set("align", e.target.value)} className={inputCls}>
              {["left", "center", "right"].map((align) => <option key={align} value={align}>{align}</option>)}
            </select></label>
        </>
        )}
        {type === "lcd_image" && (
          <label className="col-span-2 text-xs text-slate-600">이미지 파일명
            <input value={f("filename")} onChange={(e) => set("filename", e.target.value)} placeholder="rc_lcd_images 파일명" className={inputCls} /></label>
        )}
        {type === "dock" && (
          <label className="col-span-2 text-xs text-slate-600">목표 크기 (정지 거리)
            <input value={f("target_size", "0.3")} onChange={(e) => set("target_size", e.target.value)} className={inputCls} /></label>
        )}
      </div>

      <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        사용 (인식 시 자동 실행 대상)
      </label>

      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy}>저장</Button>
        <Button size="sm" variant="outline" onClick={test} disabled={busy}>테스트</Button>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy} className="text-red-500">삭제</Button>
        {msg && <span className="text-[11px] text-slate-500">{msg}</span>}
      </div>
    </div>
  );
}
