import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Navigation, Plus, RotateCcw, Save, Trash2, Unlink } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminApi, normalizeNav2State, type FleetPlan, type Nav2State, type WaypointGraph, type WaypointLane } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

type Viewport = { scale: number; ox: number; oy: number; zoom: number; pan: { x: number; y: number } };
type MapMeta = { width: number; height: number; resolution: number; origin: { x: number; y: number; yaw: number } };

export interface WaypointEditorProps {
  /** goto 대상 로봇의 초기 기본값. 편집·저장은 로봇과 무관(단일 공유 정본). */
  robotId: number | null;
  navPort?: number;
}

const ZOOM_MIN = 1.0;
const ZOOM_MAX = 8.0;

// arte3.pgm → public/maps/arte3.png 로 미리 변환해둔 정적 배경. 로봇 연결 여부와
// 무관하게 항상 뜨도록(편집은 로봇 없이도 가능해야 함) 라이브 occupancy grid 대신
// 이 고정 이미지+메타데이터를 쓴다. arte3.yaml 값과 반드시 일치해야 한다.
// (arte3 는 arte2 와 resolution/origin/dims 동일, pgm 픽셀만 갱신 — STATIC_MAP 불변.)
const MAP_IMAGE_SRC = "/maps/arte3.png";
const STATIC_MAP: MapMeta = {
  width: 63,
  height: 108,
  resolution: 0.02,
  origin: { x: -0.184, y: -1.949, yaw: 0 },
};

// 순회(patrol) 루프 간선 — 이 노드열의 연속쌍(닫힌 루프)을 빨강으로 그려 순회 경로를
// 한눈에 보이게 한다. fleet_node 의 patrol_route(런치 때 이름→인덱스 해석)와 같은 순서·CCW.
// ⚠️ 순회 노드/간선은 전용차선이 아니다 — 그 상태일 때만 로봇이 여기를 돌 뿐, 다른 로봇도 통행한다.
const PATROL_LOOP = [
  "순회경로-1", "예술서가", "문학서가", "순회경로-6", "순회경로-7",
  "순회경로-8", "순회경로-5", "순회경로-4", "순회경로-3", "순회경로-2",
];
const patrolPairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);
const PATROL_PAIRS = new Set(
  PATROL_LOOP.map((n, i) => patrolPairKey(n, PATROL_LOOP[(i + 1) % PATROL_LOOP.length])),
);

// 보기 좋게 지도를 반시계(CCW) 90도 돌려서 그린다 — 세로로 긴 지도가 가로로
// 넓게 표시됨. 픽셀 회전 공식: rx = iyTop, ry = width - ix (표준 90° CCW).
function worldToCanvas(map: MapMeta, vp: Viewport, wx: number, wy: number) {
  const ix = (wx - map.origin.x) / map.resolution;
  const iyTop = map.height - (wy - map.origin.y) / map.resolution;
  const rx = iyTop;
  const ry = map.width - ix;
  return [vp.ox + rx * vp.scale * vp.zoom + vp.pan.x, vp.oy + ry * vp.scale * vp.zoom + vp.pan.y] as const;
}

function canvasToWorld(map: MapMeta, vp: Viewport, cx: number, cy: number) {
  const rx = (cx - vp.pan.x - vp.ox) / (vp.scale * vp.zoom);
  const ry = (cy - vp.pan.y - vp.oy) / (vp.scale * vp.zoom);
  const iyTop = rx;
  const ix = map.width - ry;
  const mx = ix;
  const my = map.height - iyTop;
  return { x: map.origin.x + mx * map.resolution, y: map.origin.y + my * map.resolution };
}

// 회전 후 지도가 캔버스에 꽉 차게: width/height를 바꿔서 fit 계산.
function fitScale(map: MapMeta, rectW: number, rectH: number) {
  const scale = Math.min(rectW / map.height, rectH / map.width);
  return { scale, ox: (rectW - map.height * scale) / 2, oy: (rectH - map.width * scale) / 2 };
}

// yaw(방향) 화살표도 지도 회전과 같이 돌아가야 한다 — world 상에서 yaw 방향으로
// 살짝 이동한 점을 worldToCanvas로 같이 변환해서, 화면상 실제 각도를 역산한다.
function canvasHeading(map: MapMeta, vp: Viewport, wx: number, wy: number, yaw: number) {
  const [x0, y0] = worldToCanvas(map, vp, wx, wy);
  const [x1, y1] = worldToCanvas(map, vp, wx + Math.cos(yaw) * map.resolution, wy + Math.sin(yaw) * map.resolution);
  return Math.atan2(y1 - y0, x1 - x0);
}

// 로봇별 색.
//
// ⚠️ **번호로 정한다. 이름 해시가 아니다.** `pinky-1` 과 `pinky-sim-1` 은 같은 로봇의
//    실물/시뮬레이션이라 **같은 색이어야** 한다. 해시로 정하면 이름이 다르니 색이 갈리고,
//    sim 으로 검증한 것과 실물에서 보는 것이 화면상 다른 로봇처럼 보인다.
//    번호가 없는 이름은 뒤에서부터 안정적으로 접어 넣는다(순서에 안 흔들리게).
//
// 주행(pinky)과 팔(arm)은 색 계열을 나눠 **종류가 먼저** 읽히게 한다.
const DRIVE_COLORS = [
  { line: "56,189,248",  hex: "#38bdf8" },   // 1 cyan
  { line: "251,191,36",  hex: "#fbbf24" },   // 2 amber
  { line: "167,139,250", hex: "#a78bfa" },   // 3 violet
  { line: "244,114,182", hex: "#f472b6" },   // 4 pink
  { line: "163,230,53",  hex: "#a3e635" },   // 5 lime
];
const ARM_COLORS = [
  { line: "52,211,153", hex: "#34d399" },
  { line: "251,146,60", hex: "#fb923c" },
];
function robotColor(name: string) {
  const pool = /arm|handy/i.test(name) ? ARM_COLORS : DRIVE_COLORS;
  const m = name.match(/(\d+)\s*$/);            // 끝의 번호 — sim 접미사가 앞에 있어도 잡힌다
  if (m) return pool[(parseInt(m[1], 10) - 1 + pool.length * 4) % pool.length];
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return pool[h % pool.length];
}

// 로봇 마커 — 바라보는 방향을 가리키는 삼각형.
//
// 지도를 90° 돌려 그리므로 world yaw 를 그대로 쓰면 안 된다. canvasHeading 이
// world 상에서 yaw 방향으로 한 칸 간 점을 같이 변환해 **화면상 각도**를 역산한다.
// yaw 가 null 이면(아직 안 온 로봇) 방향을 지어내지 말고 점으로 그린다.
function drawRobotMarker(
  ctx: CanvasRenderingContext2D, map: MapMeta, vp: Viewport,
  cx: number, cy: number, wx: number, wy: number, yaw: number | null, color: string,
) {
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 2.5;
  ctx.fillStyle = color;
  if (yaw == null) {
    ctx.beginPath();
    ctx.arc(cx, cy, 12, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    return;
  }
  const ang = canvasHeading(map, vp, wx, wy, yaw);
  const L = 16;   // 코끝까지. 길이와 폭을 비슷하게 둬 방향은 읽히되 뾰족하지 않게
  const W = 15;   // 뒤쪽 반폭
  ctx.beginPath();
  ctx.moveTo(cx + L * Math.cos(ang), cy + L * Math.sin(ang));
  ctx.lineTo(cx + W * Math.cos(ang + 2.45), cy + W * Math.sin(ang + 2.45));
  ctx.lineTo(cx + 6 * Math.cos(ang + Math.PI), cy + 6 * Math.sin(ang + Math.PI));
  ctx.lineTo(cx + W * Math.cos(ang - 2.45), cy + W * Math.sin(ang - 2.45));
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

type Hold = {
  node: string; robot: string; hex: string;
  at: number;     // 예약 시각(unix 초)
  left: number;   // 남은 시간(초). 음수면 지났다
  late: boolean;  // drift_limit 을 넘겼나 — 넘어야 "지연" 이다
};
type EdgeHold = {
  from: string; to: string; robot: string; hex: string;
  at: number; until: number; left: number; late: boolean;
};

type ResvView = "node" | "edge" | "robot";

// 예약 시각의 **벽시계 표기**. 이제 화면에 바로 찍지 않고 툴팁으로만 쓴다 —
// 로그·영상과 시각을 맞춰야 할 때가 있어 값 자체는 남긴다(아래 leftLabel 머리말).
// `at` 은 `plan.epoch_wall + arrive*tick_sec`(fleet_node publish_plan) — 이미 벽시계다.
// 초까지만 쓴다(틱이 1초라 밀리초는 의미가 없다).
function clockLabel(at: number) {
  const d = new Date(at * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 예약 시각 대비 **얼마나 남았나/지났나** 한 줄. drift_limit 안쪽은 정상 주행이라
// "지연" 으로 쓰지 않는다.
//
// ⚠️ [2026-08-02] 벽시계("· 19:23:21까지")를 **뺐다.** 한 줄에 상대시간과 절대시간이
//    같이 있으니 정작 읽고 싶은 "몇 초 남았나" 가 묻혔다(사용자 지적). 벽시계는
//    툴팁(`title`)으로 내렸다 — 로그와 시각을 맞출 때는 거기서 본다.
function leftLabel(left: number, late: boolean) {
  if (!Number.isFinite(left)) return "예약 없음";
  if (left >= 0) return `T-${left.toFixed(1)}s`;
  // 이미 지난 칸. 봐주는 폭(drift_limit) 안이면 아직 "지연" 이 아니다.
  return late ? `지연 +${(-left).toFixed(1)}s` : `도착 중 +${(-left).toFixed(1)}s`;
}

// 그 칸의 벽시계 마감 — 툴팁 문구. 값이 없으면 빈 문자열(툴팁 안 뜸).
function dueTitle(at: number | undefined, late: boolean) {
  if (at == null || !Number.isFinite(at)) return "";
  return late ? `${clockLabel(at)} 까지였다` : `${clockLabel(at)} 까지`;
}

// ⚠️ [2026-08-02] **묵은 계획을 현재처럼 그리지 않는다.**
//
//   실측: 로봇이 전부 멎으면 `fleet_node` 는 재계획할 이유가 없어 마지막 시간표를
//   그대로 들고 있는다. 그 계획은 **틀리지 않았다** — 그냥 옛것이다. 그런데 화면은
//   `epoch_wall + arrive*tick` 로 남은 시간을 재므로, 계획이 묵은 만큼이 그대로
//   "지연" 으로 찍힌다. 04:11 계획을 09:15 에 보면 **지연 +18198.6s** 다.
//   경로도 그때의 출발점(충전소)에서 그려져, 지금 도서관출입구에 선 로봇과 어긋난다.
//
//   숫자가 틀린 게 아니라 **재료가 5시간 묵은 것**인데, 화면에 그 말이 없어서
//   "예약이 깨졌나 / 프론트 버그인가" 로 읽힌다. 어젯밤 고친 "꺼진 로봇 안 그리기" 와
//   같은 부류 — **화면이 옛 것을 현재처럼 보여주는 것**.
//
//   기준을 `drift_limit`(플래너가 봐주는 폭)에 매단다. 그 폭을 크게 넘겼는데도
//   재계획이 안 왔다는 건 계획이 살아 있지 않다는 뜻이다. 절대 하한 60초는 tick 이
//   아주 짧은 설정에서 정상 주행을 묵었다고 오판하지 않기 위한 바닥이다.
const PLAN_STALE_DRIFTS = 20;
const PLAN_STALE_FLOOR_SEC = 60;
// `nowSec` 을 받는다 — 컴포넌트가 500ms 마다 갱신하는 그 값이라, 배지가 시간과 함께
// 자연히 갱신된다. 여기서 `Date.now()` 를 직접 부르면 렌더 밖에서는 안 변한다.
function planAgeSec(plan: FleetPlan | null | undefined, nowSec: number,
                    skew: number): number | null {
  if (!plan || typeof plan.epoch_wall !== "number") return null;
  return nowSec - skew - plan.epoch_wall;
}
function planStaleAfter(plan: FleetPlan | null | undefined): number {
  const tick = plan?.tick_sec ?? 1;
  const drift = plan?.drift_limit ?? 0;
  return Math.max(PLAN_STALE_FLOOR_SEC, drift * tick * PLAN_STALE_DRIFTS);
}
// "5.1시간 전" 처럼 사람이 읽는 나이.
function ageLabel(sec: number) {
  if (sec < 90) return `${sec.toFixed(0)}초 전`;
  if (sec < 5400) return `${(sec / 60).toFixed(1)}분 전`;
  return `${(sec / 3600).toFixed(1)}시간 전`;
}
const clockOf = (at: number) =>
  new Date(at * 1000).toLocaleTimeString("ko-KR", { hour12: false });

//: 두 값이 내용상 같은가. WebSocket 스냅샷이 **내용은 같은데 참조만 새로** 오는 것을
//  끊는 데 쓴다. plan/routes 는 순수 데이터(숫자·문자열·배열)라 직렬화 비교가 안전하다.
//  값이 같으면 이전 state 를 그대로 돌려줘 리렌더 자체를 안 일으킨다.
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;   // 순환 참조 등 — 다르다고 보고 갱신한다(안전한 쪽)
  }
}

export function WaypointEditor({ robotId, navPort = 9001 }: WaypointEditorProps) {
  const queryClient = useQueryClient();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [wsState, setWsState] = useState<Nav2State | null>(null);
  const [zoom, setZoom] = useState(1.0);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedLane, setSelectedLane] = useState<WaypointLane | null>(null);
  const [linkMode, setLinkMode] = useState(false);
  const [linkFirst, setLinkFirst] = useState<string | null>(null);
  const [addMode, setAddMode] = useState(false);
  const [goingTo, setGoingTo] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const panDrag = useRef<{ startCanvas: { x: number; y: number }; startPan: { x: number; y: number } } | null>(null);
  const mapImageRef = useRef<HTMLImageElement | null>(null);
  const [mapImageReady, setMapImageReady] = useState(false);
  // Phase B — 전 로봇 실시간 위치(fleet 피드) + goto 대상은 패널에서 고른다(전역 선택 무시).
  const [fleetRobots, setFleetRobots] = useState<
    { name: string; x: number; y: number; yaw: number | null; state: string | null }[]
  >([]);
  const [gotoRobotId, setGotoRobotId] = useState<number | null>(robotId);
  const [resvView, setResvView] = useState<ResvView>("node");
  // "예약된 것만" — 기본은 **전부 보여 준다.** 비어 있는 자리를 봐야 지금 어디가 한가한지
  // 읽히기 때문이다. 붐빌 때만 켜서 좁혀 본다.
  const [resvOnly, setResvOnly] = useState(false);
  // CBS 시간표(예약 시각). 반응형 교통이면 안 오고, robots 가 비면 "계획을 버렸다" 는 뜻이다.
  const [plan, setPlan] = useState<FleetPlan | null>(null);
  // 각 로봇의 **남은** 경로(월드 좌표). 매 틱 갱신되므로 화면이 비지 않는다.
  const [routes, setRoutes] = useState<Record<string, number[][]>>({});
  // 길잡이는 GuideExec가 nav2에 직접 goal을 내므로 fleet_node routes가 없다. 목표만
  // 별도 표시하고, 직선/순회선을 만들어 실제 Nav2 경로인 척하지 않는다.
  const [guideTargets, setGuideTargets] = useState<Record<string, { x: number; y: number; name: string }>>({});
  //: nav2 가 지금 실제로 따라가는 경로. `routes`(fleet_node 의 CBS 순회 경로)와 **다르다**
  //  — 길잡이는 nav2 가 직접 몰아서 fleet_node 가 그 목적지를 모른다. 그래서 지도에
  //  안내 경로가 안 그려지고 순찰 경로만 떴다(실측 2026-08-02: 화장실로 가는 중인데
  //  순회 경로만 보였다). 로봇이 낸 값이라 **추정이 아니다.**
  const [navPaths, setNavPaths] = useState<Record<string, number[][]>>({});
  // 예약까지 남은 시간을 초 단위로 다시 그리기 위한 틱. **통신이 아니라 브라우저 시계다** —
  // 예약 시각은 epoch + arrive*tick_sec 로 고정이라 서버에 더 물어볼 것이 없다.
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  // 브라우저 시계 - 서버 시계. 예약 시각은 fleet_node 의 벽시계 기준이라, 브라우저가
  // 몇 초 틀어져 있으면 "T-12.3초" 가 통째로 어긋난다(휴대폰이 특히 잘 틀어진다).
  const clockSkew = useRef(0);
  //: `draw`(useCallback) 안에서 살아 있는 로봇 집합을 보려면 ref 가 필요하다.
  //  상태를 직접 의존성에 넣으면 로봇이 움직일 때마다 draw 가 재생성돼
  //  깜빡임을 다시 만든다(위 sameJson 가드와 같은 이유).
  const liveRobotsRef = useRef<Set<string>>(new Set());
  //: 계획이 묵었나. 그리기 콜백이 렌더 본문보다 먼저 정의되므로 ref 로 넘긴다
  //  (`liveRobotsRef` 와 같은 이유).
  const planStaleRef = useRef(false);
  useEffect(() => {
    const id = setInterval(() => setNowSec(Date.now() / 1000), 500);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const img = new Image();
    img.onload = () => setMapImageReady(true);
    img.src = MAP_IMAGE_SRC;
    mapImageRef.current = img;
  }, []);

  // 단일 공유 정본 waypoint.yaml — 로봇 선택과 무관하다. 저장하면 이 파일에 쓰고
  // fleet_node navgraph 도 재생성된다(반영은 각자 재기동 시). 예전엔 로봇별 ROS
  // waypoint_get/save 였다 — 로봇마다 그래프가 갈라져 관제·배차와 어긋났다.
  const graphQuery = useQuery({
    queryKey: ["waypoints-shared"],
    queryFn: () => adminApi.waypointsSharedGet(),
    staleTime: Infinity,
  });
  // 서버 미응답 시에도 편집 가능하도록 waypoint.yaml을 그대로 복사해둔 정적
  // 폴백(public/maps/waypoint.json)을 쓴다.
  const staticGraphQuery = useQuery({
    queryKey: ["waypoints-static"],
    queryFn: () => fetch("/maps/waypoint.json").then((r) => r.json() as Promise<WaypointGraph>),
    staleTime: Infinity,
  });
  const graph: WaypointGraph = graphQuery.data ?? staticGraphQuery.data ?? { vertices: {}, lanes: [] };

  // goto 대상 선택기 후보 — 주행 로봇. 전역 상단 드롭다운과 무관하게 여기서 고른다.
  const robotsQuery = useQuery({
    queryKey: ["robots-driving"],
    queryFn: () => adminApi.listRobots({ robot_type: "pinky", limit: 50 }),
    staleTime: 60_000,
  });
  const driveRobots = robotsQuery.data?.items ?? [];
  const gotoRobot = driveRobots.find((r) => r.id === gotoRobotId) ?? null;

  // 선택한 goto 로봇의 정밀 nav2 pose (초록 강조). 전역 robotId 가 아니라 패널 선택을 따른다.
  useEffect(() => {
    if (gotoRobotId == null) {
      setWsState(null);
      return;
    }
    const ws = new WebSocket(adminApi.controlStateWsUrl(gotoRobotId, navPort));
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) setWsState(normalizeNav2State(payload.state as Nav2State));
      } catch {
        // 무시 — 다음 프레임 대기
      }
    };
    return () => ws.close();
  }, [gotoRobotId, navPort]);

  // 전 로봇 실시간 위치 — fleet 피드(관제 스냅샷). 한 페이지에서 모든 로봇을 지도에 찍는다.
  useEffect(() => {
    const ws = new WebSocket(adminApi.fleetFeedWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const snap = payload?.snapshot;
        // 브라우저 시계 − 서버 시계. 예약 시각은 fleet_node 벽시계 기준이라, 브라우저가
        // 몇 초 틀어져 있으면 "T-12.3초" 가 통째로 어긋난다(휴대폰이 특히 잘 틀어진다).
        if (typeof snap?.server_now === "number") {
          clockSkew.current = Date.now() / 1000 - snap.server_now;
        }
        // ⚠️ `stale` 이면 **둘 다 지운다.** `/fms/plan` 은 transient_local 이고 백엔드도
        //    캐시하므로, fleet_node 가 죽어도 마지막 시간표가 영원히 남는다 — 아무도
        //    지키지 않는 예약을 화면이 계속 카운트다운하게 된다.
        // ⚠️ [2026-08-02] **plan·routes 도 값이 같으면 상태를 안 바꾼다.**
        //
        //   아래 `fleetRobots` 에만 이 가드가 있었다. 백엔드는 ROS 메시지 하나하나마다
        //   스냅샷을 통째로 밀기 때문에(fleet_link `_notify`) 내용이 **똑같아도**
        //   초당 수십 번 새 객체가 온다. 그대로 setState 하면 참조가 매번 달라져
        //   → 리렌더 → `draw` 재생성 → 캔버스 재그리기, 그리고 아래 예약 표가
        //   통째로 갈아엎힌다. 화면이 눈에 띄게 깜빡이는 원인이다(실측 2026-08-02).
        //
        //   rAF 묶음은 **한 프레임 안의 중복만** 없앤다 — 참조가 계속 바뀌는 것 자체는
        //   못 막는다. 그래서 근원인 여기서 끊는다.
        //
        //   비교는 JSON 직렬화로 한다. plan/routes 는 숫자·문자열·배열뿐인 순수
        //   데이터고(키 순서는 서버가 같은 코드로 만드니 안정적이다), 깊은 비교를
        //   손으로 쓰는 것보다 짧고 틀릴 자리가 없다. 크기도 노드 수십 개 수준이다.
        const p = snap?.stale ? null : snap?.plan;
        const nextPlan = p && typeof p === "object" && p.robots ? (p as FleetPlan) : null;
        setPlan((prev) => (sameJson(prev, nextPlan) ? prev : nextPlan));
        const rt = snap?.stale ? {} : snap?.routes;
        const nextRoutes = rt && typeof rt === "object" ? (rt as Record<string, number[][]>) : {};
        setRoutes((prev) => (sameJson(prev, nextRoutes) ? prev : nextRoutes));
        const np = snap?.stale ? {} : snap?.nav_paths;
        const nextNavPaths = np && typeof np === "object"
          ? (np as Record<string, number[][]>) : {};
        setNavPaths((prev) => (sameJson(prev, nextNavPaths) ? prev : nextNavPaths));
        const gt = snap?.stale ? {} : snap?.guide_targets;
        const nextGuideTargets = gt && typeof gt === "object"
          ? (gt as Record<string, { x: number; y: number; name: string }>) : {};
        setGuideTargets((prev) => (sameJson(prev, nextGuideTargets) ? prev : nextGuideTargets));
        // ⚠️ 마커도 `stale` 이면 **지운다.** 위 plan/routes 만 지우고 여기는 빠져
        //    있었는데, 그러면 fleet_node 가 죽거나 로봇이 내려가도 **마지막 위치에
        //    로봇이 계속 떠 있다.** 실측 2026-08-01: pinky-3 스택을 완전히 내린
        //    상태에서도 지도에 pinky-3 이 그대로 찍혀 있었다. "지도에 있으니 거기
        //    있다"는 화면의 거짓말이라, 없는 로봇을 보고 배차를 판단하게 된다.
        //    위치를 모를 때는 마지막으로 알던 자리를 보여주지 않는 것이 맞다
        //    (libi_gui 의 poseValid 도 같은 규칙이다).
        const rows = payload?.snapshot?.stale ? [] : payload?.snapshot?.robots;
        if (Array.isArray(rows)) {
          const next = rows
            // ⚠️ [2026-08-02] **로봇별 `stale` 도 본다 — 좌표 유무만으로는 못 거른다.**
            //
            //   백엔드는 로봇마다 마지막 ROS 수신 시각을 들고 `stale` 을 실어 보낸다
            //   (`fleet_link.build_rows`). 그런데 프론트는 전역 `snapshot.stale` 과
            //   좌표 유무만 봤다. **꺼진 로봇도 마지막 좌표는 갖고 있으므로** 그 필터를
            //   그대로 통과했다.
            //
            //   실측 2026-08-02: pinky-3 의 Pi 를 완전히 내린 뒤에도 지도에 계속 떠 있었다.
            //   스냅샷을 직접 받아 보니 `{name: "pinky-3", stale: true, x: 0.067, ...}` —
            //   **백엔드는 이미 죽었다고 말하고 있었는데 화면이 안 들었다.**
            //   없는 로봇을 보고 사람이 배차를 판단하게 되므로 화면이 거짓말을 한 것이다.
            .filter((r) => !r.stale && r.x != null && r.y != null)
            .map((r) => ({
              name: r.name, x: r.x, y: r.y,
              yaw: typeof r.yaw === "number" ? r.yaw : null,
              state: r.state ?? null,
            }));
          // ⚠️ 값이 같으면 **상태를 안 바꾼다.** 백엔드가 ROS 메시지마다 스냅샷을
          //    통째로 밀기 때문에(fleet_link `_notify`), 로봇이 가만히 있어도 초당
          //    수십 번 새 배열이 온다. 그대로 setState 하면 참조가 매번 달라져
          //    리렌더 → 캔버스 재그리기가 계속 돈다. 얕은 비교로 끊는다.
          setFleetRobots((prev) =>
            prev.length === next.length
            && prev.every((p, i) =>
              p.name === next[i].name && p.x === next[i].x
              && p.y === next[i].y && p.yaw === next[i].yaw
              && p.state === next[i].state)
              ? prev : next,
          );
        }
      } catch {
        // 무시
      }
    };
    return () => ws.close();
  }, []);

  const [dirty, setDirty] = useState(false);

  const saveMutation = useMutation({
    mutationFn: (g: WaypointGraph) => adminApi.waypointsSharedSave(g),
    onSuccess: (res, g) => {
      queryClient.setQueryData(["waypoints-shared"], g);
      setDirty(false);
      setMessage(
        res.navgraph_regenerated.ok
          ? "저장됨 — 공유 waypoint.yaml + fleet_node navgraph 재생성 (재기동 시 반영)"
          : "저장됨 — waypoint.yaml (navgraph 재생성 실패, 로그 확인)",
      );
    },
    onError: (err: Error) => setMessage(`저장 실패: ${err.message}`),
  });

  const gotoMutation = useMutation({
    mutationFn: (name: string) => adminApi.waypointGoto(gotoRobotId!, name, navPort),
    onMutate: (name) => setGoingTo(name),
    onSettled: () => setGoingTo(null),
    onSuccess: (res) => setMessage(res.success ? `'${res.name}' 도착 (경로: ${res.path.join(" → ")})` : `실패: ${res.msg ?? ""}`),
    onError: (err: Error) => setMessage(`이동 실패: ${err.message}`),
  });

  // 편집은 로컬 상태만 바꾼다 — "저장" 버튼을 눌러야 waypoint_save 액션으로
  // fleet_link에 반영되고 로봇의 waypoint.yaml이 실제로 바뀐다.
  const persist = useCallback((next: WaypointGraph) => {
    queryClient.setQueryData(["waypoints-shared"], next);
    setDirty(true);
  }, [queryClient]);

  const handleSave = () => {
    saveMutation.mutate(graph);
  };

  // 저장 안 한 로컬 편집을 버리고 마지막 저장 상태(로봇 연결 시 서버, 아니면 기본
  // waypoint.yaml)로 되돌린다.
  const handleRevert = () => {
    queryClient.setQueryData(["waypoints-shared"], undefined);
    graphQuery.refetch();
    setDirty(false);
    setSelected(null);
    setSelectedLane(null);
    setMessage("변경사항을 되돌렸습니다");
  };

  const renameVertex = (oldName: string, newName: string) => {
    if (!newName || newName === oldName || graph.vertices[newName]) return;
    const vertices = { ...graph.vertices };
    vertices[newName] = vertices[oldName];
    delete vertices[oldName];
    const lanes = graph.lanes.map((ln) => ({
      ...ln,
      from: ln.from === oldName ? newName : ln.from,
      to: ln.to === oldName ? newName : ln.to,
    }));
    persist({ vertices, lanes });
    setSelected(newName);
  };

  const moveVertex = (name: string, x: number, y: number) => {
    const vertices = { ...graph.vertices, [name]: { ...graph.vertices[name], x, y } };
    persist({ vertices, lanes: graph.lanes });
  };

  const setYaw = (name: string, yaw: number) => {
    const vertices = { ...graph.vertices, [name]: { ...graph.vertices[name], yaw } };
    persist({ vertices, lanes: graph.lanes });
  };

  const deleteVertex = (name: string) => {
    const vertices = { ...graph.vertices };
    delete vertices[name];
    const lanes = graph.lanes.filter((ln) => ln.from !== name && ln.to !== name);
    persist({ vertices, lanes });
    if (selected === name) setSelected(null);
  };

  const addVertex = (x: number, y: number) => {
    let n = 1;
    while (graph.vertices[`노드-${n}`]) n += 1;
    const name = `노드-${n}`;
    persist({ vertices: { ...graph.vertices, [name]: { x, y, yaw: 0 } }, lanes: graph.lanes });
    setSelected(name);
  };

  const toggleLane = (a: string, b: string) => {
    const idx = graph.lanes.findIndex((ln) => new Set([ln.from, ln.to]).size === 2 && new Set([ln.from, ln.to, a, b]).size === 2);
    if (idx >= 0) {
      persist({ vertices: graph.vertices, lanes: graph.lanes.filter((_, i) => i !== idx) });
    } else {
      persist({ vertices: graph.vertices, lanes: [...graph.lanes, { from: a, to: b, bidirectional: true }] });
    }
  };

  const setLaneDirection = (lane: WaypointLane, bidirectional: boolean) => {
    const lanes = graph.lanes.map((ln) => (ln === lane ? { ...ln, bidirectional } : ln));
    persist({ vertices: graph.vertices, lanes });
  };

  const deleteLane = (lane: WaypointLane) => {
    persist({ vertices: graph.vertices, lanes: graph.lanes.filter((ln) => ln !== lane) });
  };

  const map = STATIC_MAP;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    // ⚠️ **크기가 실제로 바뀔 때만 대입한다.** `canvas.width` 는 같은 값을 넣어도
    // 캔버스를 통째로 리셋하고 화면을 비운다(HTML 사양). `draw` 의 의존성에
    // `nowSec`(0.5초마다 갱신)이 있어 이 함수가 초당 2번 도는데, 그때마다 비웠다
    // 다시 그리니 지도가 **눈에 띄게 깜빡였다**. 크기는 거의 안 바뀌므로 대부분의
    // 호출에서 이 블록을 건너뛰게 되고, 아래 fillRect 가 이전 프레임을 덮어쓴다.
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, rect.width, rect.height);

    const { scale: baseScale, ox, oy } = fitScale(map, rect.width, rect.height);
    const vp: Viewport = { scale: baseScale, ox, oy, zoom, pan };

    const img = mapImageRef.current;
    if (mapImageReady && img) {
      const cell = vp.scale * vp.zoom;
      const w = map.width * cell;   // 원본(회전 전) 픽셀 크기
      const h = map.height * cell;
      ctx.imageSmoothingEnabled = false;
      ctx.save();
      // 90° CCW 회전 + worldToCanvas의 rx/ry 좌표계와 일치하도록 배치.
      ctx.translate(vp.ox + vp.pan.x, vp.oy + vp.pan.y + w);
      ctx.rotate(-Math.PI / 2);
      ctx.drawImage(img, 0, 0, w, h);
      ctx.restore();
    } else {
      ctx.fillStyle = "#94a3b8";
      ctx.font = "14px system-ui";
      ctx.fillText("지도 이미지 로딩 중...", 24, 34);
    }

    const z = Math.sqrt(zoom);
    const byName = graph.vertices;

    // 간선 — 양방향=파랑 실선, 단방향=주황 + to쪽으로 화살표(방향 명확히 표시)
    graph.lanes.forEach((ln) => {
      const a = byName[ln.from];
      const b = byName[ln.to];
      if (!a || !b) return;
      const [ax, ay] = worldToCanvas(map, vp, a.x, a.y);
      const [bx, by] = worldToCanvas(map, vp, b.x, b.y);
      const isLaneSel = selectedLane === ln;
      const isPatrol = PATROL_PAIRS.has(patrolPairKey(ln.from, ln.to));
      const baseColor = isPatrol
        ? [239, 68, 68]                                            // 순회 간선 = 빨강
        : ln.bidirectional ? [120, 180, 255] : [251, 146, 60];
      ctx.strokeStyle = isLaneSel ? "#fb7185" : `rgba(${baseColor.join(",")},0.75)`;
      ctx.lineWidth = (isLaneSel ? 4 : 2) * z;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
      if (!ln.bidirectional) {
        // to쪽 70% 지점에 화살표 — from→to 방향을 명확히 보여줌
        const ang = Math.atan2(by - ay, bx - ax);
        const px = ax + (bx - ax) * 0.7, py = ay + (by - ay) * 0.7;
        const hl = 9 * z;
        ctx.fillStyle = isLaneSel ? "#fb7185" : `rgba(${baseColor.join(",")},0.95)`;
        ctx.beginPath();
        ctx.moveTo(px + hl * Math.cos(ang), py + hl * Math.sin(ang));
        ctx.lineTo(px + hl * Math.cos(ang + 2.6), py + hl * Math.sin(ang + 2.6));
        ctx.lineTo(px + hl * Math.cos(ang - 2.6), py + hl * Math.sin(ang - 2.6));
        ctx.closePath();
        ctx.fill();
      }
    });

    // ── 주행 경로 + 예약 시각 오버레이 ──────────────────────────────────────
    //
    // ⚠️ **경로는 `routes`, 시각은 `plan` 에서 온다. 둘을 섞지 않는다.**
    //    `plan`(CBS 시간표)은 **재계획할 때만** 나온다. 그것만으로 그리면, 로봇이 계획대로
    //    잘 가는 동안에는 시간표가 갱신되지 않아 예약 시각이 전부 과거가 되고 화면이
    //    **통째로 비어 버린다**(실측: 두 대가 멀쩡히 주행 중인데 아무것도 안 그려졌다).
    //    `routes` 는 매 틱(150 ms) 남은 경로를 좌표로 내므로 항상 살아 있다.
    //    반응형 교통에서는 `plan` 자체가 없다 — 그때도 경로는 보여야 한다.
    //
    // `routes[r][0]` 은 **떠나온 노드**다(fleet_node publish_routes 가 idx-1 부터 낸다).
    // 그래서 향하는 곳은 `[1]`.
    const planRobots = plan?.robots ?? {};
    // 예약 시각(초). 계획에 그 정점이 있으면 값, 아니면 null.
    //
    // ⚠️ **좌표로 맞춘다. 정점 인덱스로 맞추지 않는다.** 화면의 waypoint.yaml 과
    //    fleet_node navgraph 의 정점 **순서가 같다는 보장이 없어서다.** 한 칸만
    //    어긋나도 엉뚱한 정점에 시각이 붙는데, 그건 화면만 보고는 못 알아챈다.
    const reservedAt = (rname: string, wx: number, wy: number): number | null => {
      // 묵은 시간표에서 뽑은 "지연 +3697.5s" 는 계획이 묵은 만큼일 뿐 실제 지연이
      // 아니다. 근원에서 끊는다 — 이 함수를 쓰는 라벨이 여러 곳이라 각각 막으면
      // 하나를 빠뜨린다.
      if (planStaleRef.current) return null;
      const pr = planRobots[rname];
      if (!pr || !plan || !Array.isArray(pr.xy)) return null;
      for (let i = 0; i < pr.xy.length; i += 1) {
        const [px, py] = pr.xy[i];
        if (Math.hypot(px - wx, py - wy) > 0.03) continue;
        if (pr.arrive[i] < 0) return null;              // 이미 떠난 칸 — 마감 없음
        return plan.epoch_wall + pr.arrive[i] * plan.tick_sec + clockSkew.current;
      }
      return null;
    };

    // ⚠️ [2026-08-02] **묵은 계획의 경로는 통째로 안 그린다.**
    //
    //   `routes` 는 `plan` 과 같은 플래너 출력이라 같이 얼어붙는다. 실측: 로봇은
    //   (0.304, -1.656) 인데 경로 첫 점이 (0.6005, -0.3615) 로 **1.33m 어긋나 있었고**,
    //   그 상태로 62분이 지나 있었다. 그걸 진하게 그리면 화면이 "로봇이 이 길로 가는
    //   중" 이라고 **거짓 주장**을 한다. 어디까지가 관제 문제이고 어디부터 화면 문제인지
    //   사람이 가릴 수 없게 된다(사용자 지적 2026-08-02).
    //
    //   지우는 대신 배지가 이유를 말한다 — "묵은 계획 · N분 전".
    //   **로봇 마커는 계속 그린다** — 그건 현재 사실이다. 그래서 이 검사는 아래 콜백
    //   **안**에 있다. 바깥에서 early return 하면 뒤따르는 로봇 마커까지 통째로 사라진다.
    //   (노드 점유 occupancy 는 이 지도에 안 그린다 — 스냅샷에 오기는 하지만 여기서
    //    읽지 않는다. 보려면 배차 화면의 「점유 노드」 칼럼.)
    Object.entries(routes).forEach(([rname, pts]) => {
      if (planStaleRef.current) return;
      // 안 켜진 로봇의 경로는 안 그린다 — `liveRobots` 주석 참고.
      // 지도에 마커가 없는 로봇의 선만 떠 있으면 그게 유령이다.
      if (!liveRobotsRef.current.has(rname)) return;
      if (!Array.isArray(pts) || pts.length < 2) return;
      const col = robotColor(rname);
      const isSelected = rname === gotoRobot?.name;
      const cv = pts.map(([wx, wy]) => worldToCanvas(map, vp, wx, wy));

      // ── 전체 남은 경로 — **투명하게, 두껍게.** "앞으로 지나갈 길" 이다.
      //    굵기로 존재를 알리고 투명도로 "아직 내 것이 아님" 을 말한다. 얇고 옅게 하면
      //    흰 지도 위에서 아예 안 보인다(실측: 첫 캡처에 한 픽셀도 안 나왔다).
      ctx.strokeStyle = `rgba(${col.line},${isSelected ? 0.4 : 0.28})`;
      ctx.lineWidth = (isSelected ? 11 : 9) * z;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(cv[0][0], cv[0][1]);
      for (let i = 1; i < cv.length; i += 1) ctx.lineTo(cv[i][0], cv[i][1]);
      ctx.stroke();

      // ── 다음 구간 — **불투명하게, 더 두껍게.** 예약을 쥐고 실제로 지금 가는 칸이다.
      //    이 한 칸만 "완전히 내 것" 이고 나머지는 아직 남과 겹칠 수 있다.
      ctx.strokeStyle = col.hex;
      ctx.lineWidth = (isSelected ? 13 : 11) * z;
      ctx.beginPath();
      ctx.moveTo(cv[0][0], cv[0][1]);
      ctx.lineTo(cv[1][0], cv[1][1]);
      ctx.stroke();

      // 다음 노드 — 꽉 찬 고리. 예약이 걸린 정점.
      ctx.fillStyle = col.hex;
      ctx.strokeStyle = "rgba(2,6,23,0.85)";
      ctx.lineWidth = 2.5 * z;
      ctx.beginPath();
      ctx.arc(cv[1][0], cv[1][1], 8 * z, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // 예약 시각. 계획이 없으면(반응형 운행) 아무것도 안 쓴다 — 없는 예약을 지어내지 않는다.
      const t = reservedAt(rname, pts[1][0], pts[1][1]);
      if (t == null) return;
      const left = t - nowSec;
      // ⚠️ **조금 늦은 것은 "지연" 이 아니다.** 플래너가 봐주는 폭(drift_limit)을
      //    넘어야 계획이 깨진다. 그 전까지는 정상 주행이라, 빨간 "지연" 으로 쓰면
      //    없는 문제를 만들어 낸다.
      const tol = (plan?.drift_limit ?? 0) * (plan?.tick_sec ?? 1);
      const overdue = left < -tol;
      // 지도 위에는 **상대시간만** 찍는다. 캔버스라 툴팁을 달 수 없어서 벽시계를 같이
      // 쓰면 라벨이 두 배로 길어지고, 정작 읽고 싶은 "몇 초 남았나" 가 묻힌다.
      // 벽시계가 필요하면 아래 예약 표에서 본다(거기엔 툴팁이 붙는다).
      const label = leftLabel(left, overdue);
      ctx.font = "bold 12px system-ui";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = "rgba(2,6,23,0.92)";
      ctx.strokeText(label, cv[1][0] + 13, cv[1][1] - 12);
      ctx.fillStyle = overdue ? "#fca5a5" : col.hex;
      ctx.fillText(label, cv[1][0] + 13, cv[1][1] - 12);
    });

    // 길잡이 목표: nav2가 계산하는 실제 경로는 이 피드에 없으므로 선을 그리지 않는다.
    // ⚠️ [2026-08-02] **nav2 원시 경로(`nav_paths`)는 그리지 않는다.**
    //
    //   길잡이 목적지가 fleet_node 에 없어 "화장실로 가는데 순찰 경로만 보인다" 를
    //   고치려고 `/plan` 을 점선으로 그려 봤는데, **읽기가 더 나빠졌다.**
    //   nav2 경로는 costmap 격자 위의 좌표열이라 **정점을 안 지나고** 벽 사이를
    //   구불구불 지난다. 지도에는 정점·간선이 이미 그려져 있어서, 그 위에 격자 곡선이
    //   겹치면 "저 줄파리 같은 건 뭐냐"가 된다(사용자 지적 2026-08-02).
    //
    //   원래 규칙이 더 낫다: **예약을 쥔 다음 구간은 진하게, 남은 경로는 연하게.**
    //   둘 다 `routes`(fleet_node 가 정점 좌표로 주는 것)라 지도와 결이 같다.
    //   길잡이 목적지는 선이 아니라 **다이아몬드 마커**로만 표시한다(아래).
    //
    //   `nav_paths` 자체는 백엔드에 남겨 둔다 — 지우면 "지금 nav2 가 어디로 가는가" 를
    //   확인할 수단이 사라진다. 그리지 않을 뿐이다.

    Object.entries(guideTargets).forEach(([rname, target]) => {
      if (!liveRobotsRef.current.has(rname)) return;
      const [x, y] = worldToCanvas(map, vp, target.x, target.y);
      const col = robotColor(rname);
      ctx.strokeStyle = col.hex;
      ctx.fillStyle = "rgba(15,23,42,0.88)";
      ctx.lineWidth = 3 * z;
      ctx.beginPath();
      ctx.moveTo(x, y - 12 * z); ctx.lineTo(x + 12 * z, y);
      ctx.lineTo(x, y + 12 * z); ctx.lineTo(x - 12 * z, y); ctx.closePath();
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = col.hex;
      ctx.font = "bold 12px system-ui";
      ctx.fillText(`안내 목표: ${target.name || rname} (경로: Nav2)`, x + 15 * z, y - 10 * z);
    });

    // 노드
    Object.entries(graph.vertices).forEach(([name, v]) => {
      const [x, y] = worldToCanvas(map, vp, v.x, v.y);
      const isSel = name === selected;
      const isLinkFirst = name === linkFirst;
      const r = (isSel ? 6 : 4) * z;
      ctx.fillStyle = isLinkFirst ? "#facc15" : isSel ? "#fb7185" : "#5BB9E0";
      ctx.strokeStyle = "#0f172a";
      ctx.lineWidth = 1.5 * z;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      if (v.yaw != null) {
        const len = 14 * z;
        const ang = canvasHeading(map, vp, v.x, v.y, v.yaw);
        ctx.strokeStyle = "#facc15";
        ctx.lineWidth = 2 * z;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + len * Math.cos(ang), y + len * Math.sin(ang));
        ctx.stroke();
      }
      const fsize = Math.max(9, Math.round(10 * z));
      ctx.font = `bold ${fsize}px system-ui`;
      ctx.lineJoin = "round";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(2,6,23,0.85)";
      ctx.strokeText(name, x + r + 4, y - r - 2);
      ctx.fillStyle = "#e2e8f0";
      ctx.fillText(name, x + r + 4, y - r - 2);
    });

    // 전 로봇 — fleet 피드 위치. goto 선택 로봇은 아래 wsState(정밀 pose)로 덮는다.
    fleetRobots.forEach((r) => {
      // ⚠️ **wsState 가 있을 때만 건너뛴다.** 예전에는 선택했다는 이유만으로 무조건
      //    건너뛰었는데, 그 정밀 pose 는 로봇 IP 로 직접 붙는 별도 WebSocket 이라
      //    robot_agent 가 안 떠 있거나 네트워크가 흔들리면 안 온다. 그러면 **선택한
      //    로봇만 지도에서 사라진다** — 관제는 fleet 피드로 위치를 알고 있는데도.
      //    (실측: sim 에는 그 WebSocket 자체가 없어 선택 로봇이 통째로 안 보였다.)
      if (r.name === gotoRobot?.name && wsState?.pose) return;
      const [x, y] = worldToCanvas(map, vp, r.x, r.y);
      // ⚠️ 점이 아니라 **화살표**다. 점만 찍으면 로봇이 어디를 보고 있는지 알 수 없어,
      //    서가를 등지고 섰는지·도킹 방향이 맞는지를 화면에서 못 가른다.
      //    yaw 가 아직 안 온 로봇은 점으로 떨어진다 — 없는 방향을 지어내지 않는다.
      drawRobotMarker(ctx, map, vp, x, y, r.x, r.y, r.yaw, robotColor(r.name).hex);
      ctx.font = "bold 10px system-ui";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(2,6,23,0.85)";
      ctx.strokeText(r.name, x + 8, y + 3);
      ctx.fillStyle = "#e0f2fe";
      ctx.fillText(r.name, x + 8, y + 3);
    });

    // goto 선택 로봇 — 정밀 nav2 pose (초록 + 방향).
    if (wsState?.pose) {
      const [x, y] = worldToCanvas(map, vp, wsState.pose.x, wsState.pose.y);
      ctx.fillStyle = "#22c55e";
      ctx.beginPath();
      ctx.arc(x, y, 7, 0, Math.PI * 2);
      ctx.fill();
      const len = 20;
      const ang = canvasHeading(map, vp, wsState.pose.x, wsState.pose.y, wsState.pose.yaw);
      ctx.strokeStyle = "#bbf7d0";
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + len * Math.cos(ang), y + len * Math.sin(ang));
      ctx.stroke();
      if (gotoRobot) {
        ctx.font = "bold 10px system-ui";
        ctx.lineJoin = "round";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(2,6,23,0.85)";
        ctx.strokeText(gotoRobot.name, x + 9, y + 3);
        ctx.fillStyle = "#bbf7d0";
        ctx.fillText(gotoRobot.name, x + 9, y + 3);
      }
    }
  }, [map, wsState, graph, zoom, pan, selected, linkFirst, selectedLane, mapImageReady, fleetRobots, gotoRobot, plan, routes, guideTargets, navPaths, nowSec]);

  // ── 예약 데이터 ──────────────────────────────────────────────────────────
  //
  // 한 덩어리로 쏟아 놓으면 "지금 뭐가 뭔지" 를 못 읽는다. 세 갈래로 나눈다:
  //   노드별 — 지도 위 모든 정점. 예약된 것은 누가 언제, 나머지는 "비어 있음"
  //   간선별 — 지나가는 통로. 정점만 봐서는 어느 길이 물리는지 모른다
  //   로봇별 — 한 대가 앞으로 어디를 어떤 순서로 밟는지
  //
  // ⚠️ 정점 이름은 **좌표로 되찾는다.** 계획이 싣고 오는 것은 fleet_node navgraph 의
  //    인덱스인데, 화면이 든 waypoint.yaml 과 정점 순서가 같다는 보장이 없다.
  // ⚠️ [2026-08-02] **살아 있는 로봇만 그린다.**
  //
  //   `fleet_node` 는 **설정에 있는 모든 로봇**의 CBS 계획을 발행한다 — 그 로봇이
  //   실제로 켜져 있는지는 안 본다. 그대로 그리면 꺼 놓은 pinky-1·pinky-2 의
  //   경로와 "T-15.4s" 카운트다운이 화면에 뜬다(실측 2026-08-02). 아무도 지키지
  //   않는 예약을 보고 사람이 배차를 판단하게 되므로, 화면이 거짓말을 하는 것이다.
  //
  //   판단 기준은 **위치를 아는가** 하나다. `fleetRobots` 는 스냅샷이 stale 하거나
  //   로봇이 내려가면 비워지고(위 WS 핸들러), 좌표가 없는 행은 애초에 걸러진다.
  //   즉 "지금 지도에 찍히는 로봇" 과 "계획을 보여줄 로봇" 이 같아진다.
  //
  //   ⚠️ DB 와 무관하다. 계획은 ROS(`/fms/plan`)로 오고 DB 를 거치지 않는다.
  const liveRobots = new Set(fleetRobots.map((r) => r.name));
  liveRobotsRef.current = liveRobots;
  // 계획의 나이. 크면 배지로 알린다 — 근거는 `planAgeSec` 위 주석.
  const planAge = planAgeSec(plan, nowSec, clockSkew.current);
  const planStale = planAge !== null && planAge > planStaleAfter(plan);
  planStaleRef.current = planStale;
  const resv = (() => {
    const verts = Object.entries(graph.vertices);
    const empty = {
      byNode: verts.map(([n]) => ({ node: n, holds: [] as Hold[] })),
      byEdge: [] as { from: string; to: string; holds: EdgeHold[] }[],
      byRobot: [] as { robot: string; hex: string; stops: Hold[] }[],
      count: 0,
      resvCount: 0,
      edgeCount: 0,
    };
    // 묵은 계획에서는 예약 시각표를 만들지 않는다 — 아래 표의 "지연" 이 전부
    // 계획이 묵은 만큼이 되어 거짓이 된다. `empty` 를 돌려주면 표는 "비어 있음" 으로
    // 떨어지고, 배지가 왜 비었는지 말한다.
    if (!plan || planStale) return empty;
    const nameAt = (wx: number, wy: number) => {
      for (const [n, v] of verts) if (Math.hypot(v.x - wx, v.y - wy) < 0.03) return n;
      return null;
    };
    const tol = (plan.drift_limit ?? 0) * (plan.tick_sec ?? 1);
    const nodeMap = new Map<string, Hold[]>(verts.map(([n]) => [n, []]));
    // 간선도 **전부** 깐다. 예약된 것만 보여 주면 "지금 어디가 비었나" 를 못 읽는다.
    // 방향쌍(a→b, b→a)은 물리적으로 같은 통로라 하나로 접는다.
    const laneKey = (a: string, b: string) => (a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`);
    const edgeMap = new Map<string, EdgeHold[]>();
    for (const ln of graph.lanes) edgeMap.set(laneKey(ln.from, ln.to), []);
    const holdsOnEdge: EdgeHold[] = [];
    const byRobot: { robot: string; hex: string; stops: Hold[] }[] = [];

    for (const [robot, pr] of Object.entries(plan.robots ?? {})) {
      if (!liveRobots.has(robot)) continue;      // 안 켜진 로봇 — 위 주석
      if (!Array.isArray(pr.xy)) continue;
      const hex = robotColor(robot).hex;
      const stops: Hold[] = [];
      for (let i = 0; i < pr.xy.length; i += 1) {
        if (pr.arrive[i] < 0) continue;                     // 이미 떠난 칸 — 마감 없음
        const node = nameAt(pr.xy[i][0], pr.xy[i][1]);
        if (!node) continue;
        const at = plan.epoch_wall + pr.arrive[i] * plan.tick_sec + clockSkew.current;
        const left = at - nowSec;
        if (left < -tol - 20) continue;                     // 한참 지난 것은 소음이다
        const h: Hold = { node, robot, hex, at, left, late: left < -tol };
        stops.push(h);
        nodeMap.get(node)!.push(h);
      }
      stops.sort((a, b) => a.at - b.at);
      for (let i = 0; i + 1 < stops.length; i += 1) {
        const e: EdgeHold = {
          from: stops[i].node, to: stops[i + 1].node,
          robot, hex, at: stops[i].at, until: stops[i + 1].at,
          left: stops[i].left, late: stops[i].late,
        };
        holdsOnEdge.push(e);
        const k = laneKey(e.from, e.to);
        if (edgeMap.has(k)) edgeMap.get(k)!.push(e);
      }
    }
    // ── 로봇별은 **전체 남은 경로**를 깐다 ────────────────────────────────
    //
    // 계획(plan)만 쓰면 예약된 칸만 나온다. 순회는 일부러 다음 한 정점까지만 계획하므로
    // 2칸밖에 안 보이고, "이 로봇이 앞으로 어디로 가는가" 를 못 읽는다.
    // `routes` 는 남은 경로 전체를 좌표로 내므로 그걸 깔고, 예약이 걸린 칸에만 시각을 붙인다.
    const reservedAtXY = (robot: string, wx: number, wy: number) => {
      const pr = plan.robots?.[robot];
      if (!pr || !Array.isArray(pr.xy)) return null;
      for (let i = 0; i < pr.xy.length; i += 1) {
        if (Math.hypot(pr.xy[i][0] - wx, pr.xy[i][1] - wy) > 0.03) continue;
        if (pr.arrive[i] < 0) return null;
        return plan.epoch_wall + pr.arrive[i] * plan.tick_sec + clockSkew.current;
      }
      return null;
    };
    for (const [robot, pts] of Object.entries(routes)) {
      if (!liveRobots.has(robot)) continue;      // 안 켜진 로봇 — 위 주석
      if (!Array.isArray(pts) || pts.length === 0) continue;
      const hex = robotColor(robot).hex;
      const stops: Hold[] = [];
      for (const [wx, wy] of pts) {
        const node = nameAt(wx, wy);
        if (!node) continue;
        const at = reservedAtXY(robot, wx, wy);
        stops.push({
          node, robot, hex,
          at: at ?? 0,
          left: at == null ? NaN : at - nowSec,          // NaN = 예약 없는 칸
          late: at != null && at - nowSec < -tol,
        });
      }
      byRobot.push({ robot, hex, stops });
    }
    byRobot.sort((a, b) => a.robot.localeCompare(b.robot));
    const byEdge = [...edgeMap.entries()]
      .map(([k, holds]) => {
        const [from, to] = k.split("\u0000");
        return { from, to, holds: holds.sort((a, b) => a.at - b.at) };
      })
      .sort((a, b) => {
        // 예약된 것 먼저. 비교 방향을 뒤집으면 빈 간선이 위로 올라와 목록이 쓸모없어진다.
        const d = (a.holds.length ? 0 : 1) - (b.holds.length ? 0 : 1);
        return d !== 0 ? d : (a.holds[0]?.at ?? 0) - (b.holds[0]?.at ?? 0) || a.from.localeCompare(b.from);
      });
    return {
      byNode: verts
        .map(([n]) => ({ node: n, holds: (nodeMap.get(n) ?? []).sort((a, b) => a.at - b.at) }))
        .sort((a, b) => {
          const d = (a.holds.length ? 0 : 1) - (b.holds.length ? 0 : 1);
          return d !== 0 ? d
            : a.holds.length ? a.holds[0].at - b.holds[0].at
            : a.node.localeCompare(b.node);
        }),
      byEdge, byRobot,
      // ⚠️ **두 수를 구분한다.** `count` 는 "표에 깔 줄이 있나" 를 가르는 값이라
      //    `routes`(남은 경로 전체) 기준이고, 예약이 없는 칸도 센다.
      //    배지에 그 값을 쓰면 "노드 12" 라고 적히는데 실제 예약은 2칸인 일이 생긴다 —
      //    순회는 일부러 다음 한 정점까지만 계획하기 때문이다. 배지는 `resvCount` 를 쓴다.
      count: byRobot.reduce((n, r) => n + r.stops.length, 0),
      resvCount: [...nodeMap.values()].reduce((n, h) => n + h.length, 0),
      edgeCount: holdsOnEdge.length,
    };
  })();

  // ⚠️ **프레임당 한 번만 그린다.** 그냥 `draw()` 를 부르면 지도가 눈에 띄게 깜빡인다.
  //
  //   백엔드가 ROS 메시지 **하나하나마다** 스냅샷을 WebSocket 으로 민다
  //   (fleet_link.py 의 on_task_state/on_robot_state/on_occupancy/on_plan/on_routes/
  //   on_goals 가 각각 `_notify()`). 로봇이 여럿이면 초당 수십 번이고, 그때마다
  //   setFleetRobots/setPlan/setRoutes 가 **새 객체**로 갱신돼 리렌더 → `draw` 재생성
  //   → 캔버스 전체 재그리기가 된다. 브라우저가 실제로 칠하는 건 60fps 인데 그보다
  //   훨씬 자주 지우고 그리니 중간 상태가 보인다.
  //
  //   rAF 로 묶으면 한 프레임 안에 몇 번이 몰려도 **마지막 한 번만** 그린다
  //   (다음 effect 실행이 이전 예약을 취소한다). 값은 최신이고 그림은 한 번이다.
  useEffect(() => {
    const id = requestAnimationFrame(() => draw());
    return () => cancelAnimationFrame(id);
  }, [draw]);

  // 그리드 레이아웃이 자리잡기 전에 캔버스 크기를 재면 0px로 잡혀 안 보이는 문제 방지 —
  // 컨테이너 실제 크기 변화를 관찰해서 다시 그린다.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [draw]);

  // React onWheel은 passive 리스너로 등록돼 preventDefault()가 무시된다(브라우저 페이지
  // 확대가 같이 발생). 네이티브 리스너를 non-passive로 직접 붙여야 Ctrl+휠을 캔버스
  // 전용 zoom으로 가로챌 수 있다.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handler = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom((prevZoom) => {
        const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, prevZoom * factor));
        if (newZoom === prevZoom) return prevZoom;
        setPan((prevPan) => {
          const bx = (mx - prevPan.x) / prevZoom, by = (my - prevPan.y) / prevZoom;
          return newZoom === 1 ? { x: 0, y: 0 } : { x: mx - bx * newZoom, y: my - by * newZoom };
        });
        return newZoom === 1 ? 1 : newZoom;
      });
    };
    canvas.addEventListener("wheel", handler, { passive: false });
    return () => canvas.removeEventListener("wheel", handler);
  }, []);

  const hitTest = (cx: number, cy: number): string | null => {
    if (!map) return null;
    const rect = canvasRef.current!.getBoundingClientRect();
    const { scale: baseScale, ox, oy } = fitScale(map, rect.width, rect.height);
    const vp: Viewport = { scale: baseScale, ox, oy, zoom, pan };
    for (const [name, v] of Object.entries(graph.vertices)) {
      const [x, y] = worldToCanvas(map, vp, v.x, v.y);
      if (Math.hypot(x - cx, y - cy) < 14) return name;
    }
    return null;
  };

  // 점-선분 거리 — 선분 범위 밖으로 투영되면 null (간선 클릭 판정용)
  const pointSegDistance = (px: number, py: number, ax: number, ay: number, bx: number, by: number): number | null => {
    const dx = bx - ax, dy = by - ay;
    const segSq = dx * dx + dy * dy;
    if (segSq < 1e-6) return null;
    const t = ((px - ax) * dx + (py - ay) * dy) / segSq;
    if (t < 0 || t > 1) return null;
    const projx = ax + t * dx, projy = ay + t * dy;
    return Math.hypot(px - projx, py - projy);
  };

  const hitTestLane = (cx: number, cy: number): WaypointLane | null => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const { scale: baseScale, ox, oy } = fitScale(map, rect.width, rect.height);
    const vp: Viewport = { scale: baseScale, ox, oy, zoom, pan };
    for (const ln of graph.lanes) {
      const a = graph.vertices[ln.from], b = graph.vertices[ln.to];
      if (!a || !b) continue;
      const [ax, ay] = worldToCanvas(map, vp, a.x, a.y);
      const [bx, by] = worldToCanvas(map, vp, b.x, b.y);
      const d = pointSegDistance(cx, cy, ax, ay, bx, by);
      if (d != null && d < 8) return ln;
    }
    return null;
  };

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    if (e.button === 1) {
      panDrag.current = { startCanvas: { x: e.clientX, y: e.clientY }, startPan: { ...pan } };
      return;
    }
    if (e.button !== 0 || !map) return;
    const hit = hitTest(cx, cy);
    if (addMode) {
      if (!hit) {
        const rect2 = canvasRef.current!.getBoundingClientRect();
        const { scale: baseScale, ox, oy } = fitScale(map, rect2.width, rect2.height);
        const vp: Viewport = { scale: baseScale, ox, oy, zoom, pan };
        const w = canvasToWorld(map, vp, cx, cy);
        addVertex(Number(w.x.toFixed(3)), Number(w.y.toFixed(3)));
        setAddMode(false);
      }
      return;
    }
    if (linkMode) {
      if (hit) {
        if (!linkFirst) setLinkFirst(hit);
        else if (linkFirst !== hit) { toggleLane(linkFirst, hit); setLinkFirst(null); }
        else setLinkFirst(null);
      }
      return;
    }
    if (hit) {
      setSelected(hit);
      setSelectedLane(null);
      return;
    }
    const lane = hitTestLane(cx, cy);
    setSelectedLane(lane);
    setSelected(null);
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!panDrag.current) return;
    const dx = e.clientX - panDrag.current.startCanvas.x;
    const dy = e.clientY - panDrag.current.startCanvas.y;
    setPan({ x: panDrag.current.startPan.x + dx, y: panDrag.current.startPan.y + dy });
  };
  const onMouseUp = () => { panDrag.current = null; };

  const sel = selected ? graph.vertices[selected] : null;
  const relatedLanes = selected ? graph.lanes.filter((ln) => ln.from === selected || ln.to === selected) : [];

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex flex-col gap-4">
      <div className="relative min-h-[520px] overflow-hidden rounded-2xl border border-white/10 bg-[#020617]">
        <canvas
          ref={canvasRef}
          className={cn("h-full w-full", addMode ? "cursor-crosshair" : linkMode ? "cursor-pointer" : "cursor-default")}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
        />
        <div className="absolute left-3 top-3 flex gap-2">
          <Button size="sm" variant={addMode ? "default" : "outline"} onClick={() => { setAddMode((v) => !v); setLinkMode(false); }}>
            <Plus className="h-3.5 w-3.5" />노드 추가
          </Button>
          <Button size="sm" variant={linkMode ? "default" : "outline"} onClick={() => { setLinkMode((v) => !v); setAddMode(false); setLinkFirst(null); }}>
            <Link2 className="h-3.5 w-3.5" />간선 연결
          </Button>
          <Button size="sm" variant={dirty ? "default" : "outline"} disabled={!dirty || saveMutation.isPending} onClick={handleSave}>
            {saveMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            저장{dirty && " *"}
          </Button>
          <Button size="sm" variant="outline" disabled={!dirty} onClick={handleRevert}>
            <RotateCcw className="h-3.5 w-3.5" />되돌리기
          </Button>
        </div>
        <div className="absolute bottom-3 left-3 text-[11px] text-slate-400">
          Ctrl+휠: 확대/축소 · 휠클릭 드래그: 이동 {linkMode && "· 간선 모드: 노드 두 개 클릭"}
        </div>
        {message && <div className="absolute bottom-3 right-3 rounded bg-black/70 px-2 py-1 text-[11px] text-slate-200">{message}</div>}
      </div>

      {/* ── 예약 현황 ─────────────────────────────────────────────────────
          지도 위 라벨은 **다음 한 칸**만 말한다. 전체 그림은 여기서 읽는다.
          한 덩어리로 쏟지 않고 노드 / 간선 / 로봇 세 갈래로 나눈다 — 섞어 놓으면
          "지금 뭐가 뭔지" 를 못 읽는다는 것이 실제로 확인된 문제였다. */}
      <div className="rounded-2xl border border-white/10 bg-slate-900/85">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-3">
          <div className="flex items-center gap-1 rounded-lg bg-white/[0.04] p-0.5">
            {([
              ["node", "노드"],
              ["edge", "간선"],
              ["robot", "로봇별"],
            ] as [ResvView, string][]).map(([v, label]) => (
              <button
                key={v}
                type="button"
                onClick={() => setResvView(v)}
                className={cn(
                  "rounded-md px-3 py-1 text-[12px] transition-colors",
                  resvView === v
                    ? "bg-white/[0.10] font-medium text-slate-100"
                    : "text-slate-400 hover:text-slate-200",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-4">
            {resvView !== "robot" && (
              <label className="flex cursor-pointer select-none items-center gap-2 text-[12px] text-slate-400 hover:text-slate-200">
                <Checkbox
                  checked={resvOnly}
                  onCheckedChange={(v) => setResvOnly(v === true)}
                />
                예약된 것만
              </label>
            )}
            <div
              className="font-mono text-[11px] text-slate-500"
              title={
                "노드: 시간표에 실제로 예약된 정점 수(/fms/plan 의 arrive_tick).\n" +
                "간선: **이 화면이 계산한 값**이다. 플래너는 간선 제약을 쓰지만(cbs_planner.cpp 의 edge 제약) " +
                "/fms/plan 에 싣지 않아서, 연속한 두 정점의 예약 시각을 이어 붙여 되짚은 것이다."
              }
            >
              {plan
                ? `계획 #${plan.seq} · 노드 ${resv.resvCount} · 간선 ${resv.edgeCount} · 틱 ${plan.tick_sec}s`
                : "계획 없음 · 반응형(노드예약)으로 운행 중"}
            </div>
            {planStale && (
              <div
                className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[11px] text-amber-300"
                title={
                  "재계획이 오래 없었습니다. fleet_node 는 로봇 상태가 끊기면 그 로봇을 " +
                  "재계획 대상에서 빼므로(state_stale), 마지막 계획을 그대로 들고 있습니다.\n\n" +
                  "그 계획의 경로선과 '지연' 값은 계획 당시 기준이라 지금과 어긋납니다 " +
                  "(실측: 로봇 위치와 경로 시작점이 1.3m 차이). 그래서 그리지 않습니다.\n\n" +
                  "로봇 마커는 현재 사실이라 그대로 보여줍니다. " +
                  "노드 점유(occupancy)는 이 지도에 그리지 않습니다 — 배차 화면의 " +
                  "「점유 노드」 칼럼에서 보세요.\n" +
                  "확인: fleet_node 창의 '로봇 상태 끊김' 경고 · pgrep -af robot_state_adapter"
                }
              >
                묵은 계획 · {ageLabel(planAge!)} · 경로·시간표 숨김
              </div>
            )}
          </div>
        </div>

        {!plan ? (
          <div className="px-4 py-8 text-center text-[12.5px] text-slate-500">
            {plan === null && "시간표가 없습니다. 교통관제가 반응형으로 도는 중이라 예약 시각이 없습니다."}
          </div>
        ) : resv.count === 0 ? (
          <div className="px-4 py-8 text-center text-[12.5px] text-slate-500">
            {plan.reason ? `시간표를 세우지 못했습니다. ${plan.reason}` : "예약된 노드가 없습니다."}
          </div>
        ) : resvView === "node" ? (
          // 전 정점을 다 깐다. 비어 있는 것도 보여야 "지금 어디가 비었나" 가 읽힌다.
          <div className="grid gap-x-6 gap-y-0 px-4 py-1.5 sm:grid-cols-2 xl:grid-cols-3">
            {resv.byNode.filter((n) => !resvOnly || n.holds.length).map(({ node, holds }) => (
              <div
                key={node}
                className={cn(
                  "flex items-baseline justify-between gap-3 border-b border-white/[0.05] py-1.5",
                )}
              >
                <span
                  className={cn(
                    "truncate text-[12.5px]",
                    holds.length ? "text-slate-100" : "text-slate-400",
                  )}
                >
                  {node}
                </span>
                {holds.length === 0 ? (
                  <span className="shrink-0 font-mono text-[11px] text-slate-500">비어 있음</span>
                ) : (
                  <span className="flex shrink-0 items-center gap-2">
                    {holds.slice(0, 2).map((h, i) => (
                      <span key={i} className="flex items-center gap-1.5">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: h.hex }}
                          aria-hidden
                        />
                        <span className="text-[11.5px] text-slate-400">{h.robot}</span>
                        <span
                          className={cn(
                            "font-mono text-[11.5px] tabular-nums",
                            h.late ? "text-rose-400" : "text-slate-200",
                          )}
                          title={dueTitle(h.at, h.late)}
                        >
                          {leftLabel(h.left, h.late)}
                        </span>
                      </span>
                    ))}
                    {holds.length > 2 && (
                      <span className="font-mono text-[11px] text-slate-500">+{holds.length - 2}</span>
                    )}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : resvView === "edge" ? (
          // 간선. 정점만 봐서는 **어느 통로가 언제 물리는지** 알 수 없다.
          // 방향쌍(a→b, b→a)은 물리적으로 같은 통로라 한 줄로 접었다.
          <div className="grid gap-x-6 gap-y-0 px-4 py-1.5 xl:grid-cols-2">
            {resv.byEdge.filter((e) => !resvOnly || e.holds.length).map((e, i) => (
              <div
                key={i}
                className="flex items-baseline justify-between gap-3 border-b border-white/[0.05] py-1.5"
              >
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate text-[12.5px]",
                    e.holds.length ? "text-slate-100" : "text-slate-400",
                  )}
                >
                  {e.from} <span className="text-slate-600">↔</span> {e.to}
                </span>
                {e.holds.length === 0 ? (
                  <span className="shrink-0 font-mono text-[11px] text-slate-500">비어 있음</span>
                ) : (
                  <span className="flex shrink-0 items-center gap-2.5">
                    {e.holds.slice(0, 2).map((h, k) => (
                      <span key={k} className="flex items-center gap-1.5">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: h.hex }}
                          aria-hidden
                        />
                        <span className="text-[11.5px] text-slate-400">{h.robot}</span>
                        {/* 간선을 붙잡고 있는 **길이**. 벽시계 구간(19:23:21~19:23:26)은
                            툴팁으로 내렸다 — 표에서 읽고 싶은 건 "얼마나 잡고 있나" 다. */}
                        <span
                          className="font-mono text-[11px] tabular-nums text-slate-500"
                          title={`${clockOf(h.at)} ~ ${clockOf(h.until)}`}
                        >
                          {(h.until - h.at).toFixed(1)}s간
                        </span>
                        <span
                          className={cn(
                            "font-mono text-[11.5px] tabular-nums",
                            h.late ? "text-rose-400" : "text-slate-200",
                          )}
                          title={dueTitle(h.at, h.late)}
                        >
                          {leftLabel(h.left, h.late)}
                        </span>
                      </span>
                    ))}
                    {e.holds.length > 2 && (
                      <span className="font-mono text-[11px] text-slate-500">+{e.holds.length - 2}</span>
                    )}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          // 로봇별. "이 한 대가 앞으로 어디를 어떤 순서로 밟는가."
          <div className="divide-y divide-white/[0.06]">
            {resv.byRobot.map(({ robot, hex, stops }) => (
              <div key={robot} className="px-4 py-3">
                <div className="mb-2 flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: hex }}
                    aria-hidden
                  />
                  <span className="text-[13px] font-medium text-slate-100">{robot}</span>
                  <span className="font-mono text-[11px] text-slate-500">
                    남은 {stops.length}칸 · 예약 {stops.filter((h) => !Number.isNaN(h.left)).length}칸
                  </span>
                </div>
                {stops.length === 0 ? (
                  <div className="text-[12px] text-slate-500">예약 없음</div>
                ) : (
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
                    {stops.map((h, i) => (
                      <span key={i} className="flex items-center gap-1.5">
                        {i > 0 && <span className="text-slate-600">→</span>}
                        {/* 예약이 걸린 칸만 시각이 붙는다. 나머지는 "지나갈 길" 이라 흐리게 —
                            없는 예약을 지어내지 않는다. */}
                        <span
                          className={cn(
                            "rounded-md px-2 py-1 text-[12px]",
                            Number.isNaN(h.left)
                              ? "bg-white/[0.02] text-slate-500"
                              : i === 0
                                ? "bg-white/[0.10] text-slate-100"
                                : "bg-white/[0.06] text-slate-200",
                          )}
                        >
                          {h.node}
                          {!Number.isNaN(h.left) && (
                            <span
                              className={cn(
                                "ml-2 font-mono text-[11px] tabular-nums",
                                h.late ? "text-rose-400" : "text-slate-300",
                              )}
                              title={dueTitle(h.at, h.late)}
                            >
                              {leftLabel(h.left, h.late)}
                            </span>
                          )}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      </div>


      <div className="rounded-2xl border border-white/10 bg-slate-900/85 p-3.5">
        {selectedLane ? (
          <div className="space-y-3">
            <div className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">간선</div>
            <div className="rounded bg-white/5 px-2 py-2 text-[13px] text-slate-200">
              {selectedLane.from} {selectedLane.bidirectional ? "↔" : "→"} {selectedLane.to}
            </div>
            <div className="flex items-center gap-2">
              <Checkbox checked={selectedLane.bidirectional}
                onCheckedChange={(v) => { setLaneDirection(selectedLane, Boolean(v)); setSelectedLane({ ...selectedLane, bidirectional: Boolean(v) }); }} />
              <Label className="text-[12px] text-slate-300">양방향</Label>
            </div>
            <Button variant="outline" className="w-full gap-1.5 text-rose-300"
              onClick={() => { deleteLane(selectedLane); setSelectedLane(null); }}>
              <Unlink className="h-3.5 w-3.5" />간선 삭제
            </Button>
          </div>
        ) : !sel ? (
          <div className="text-[12px] text-slate-500">노드나 간선을 클릭해 선택하세요.</div>
        ) : (
          <div className="space-y-3">
            <div>
              <Label className="text-[11px] text-slate-400">이름</Label>
              <Input className="h-8 bg-slate-950/70 text-slate-100" defaultValue={selected!}
                onBlur={(e) => renameVertex(selected!, e.target.value.trim())} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="text-[11px] text-slate-400">x</Label>
                <Input className="h-8 bg-slate-950/70 text-slate-100" type="number" step="0.01" defaultValue={sel.x}
                  onBlur={(e) => moveVertex(selected!, Number(e.target.value), sel.y)} />
              </div>
              <div>
                <Label className="text-[11px] text-slate-400">y</Label>
                <Input className="h-8 bg-slate-950/70 text-slate-100" type="number" step="0.01" defaultValue={sel.y}
                  onBlur={(e) => moveVertex(selected!, sel.x, Number(e.target.value))} />
              </div>
            </div>
            <div>
              <Label className="text-[11px] text-slate-400">yaw (rad)</Label>
              <Input className="h-8 bg-slate-950/70 text-slate-100" type="number" step="0.01" defaultValue={sel.yaw ?? 0}
                onBlur={(e) => setYaw(selected!, Number(e.target.value))} />
            </div>

            {/* goto 대상 로봇 — 패널에서 고른다(상단 전역 선택 무시). */}
            <div>
              <Label className="text-[11px] text-slate-400">이동 로봇</Label>
              <select
                className="mt-1 h-8 w-full rounded border border-white/10 bg-slate-950/70 px-2 text-sm text-slate-100"
                value={gotoRobotId ?? ""}
                onChange={(e) => setGotoRobotId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">로봇 선택</option>
                {driveRobots.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </div>

            <Button className="w-full gap-1.5" disabled={gotoRobotId == null || goingTo != null} onClick={() => gotoMutation.mutate(selected!)}>
              {goingTo === selected ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4" />}
              이 노드로 이동
            </Button>

            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-400">연결된 간선</div>
              <div className="space-y-1">
                {relatedLanes.length === 0 && <div className="text-[11px] text-slate-500">없음</div>}
                {relatedLanes.map((ln, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 rounded bg-white/5 px-2 py-1 text-[11px] text-slate-300">
                    <span className="truncate">{ln.from} ↔ {ln.to}</span>
                    <div className="flex items-center gap-1.5">
                      <Checkbox checked={ln.bidirectional} onCheckedChange={(v) => setLaneDirection(ln, Boolean(v))} />
                      <span className="text-slate-500">양방향</span>
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => deleteLane(ln)}><Unlink className="h-3 w-3" /></Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <Button variant="outline" className="w-full gap-1.5 text-rose-300" onClick={() => deleteVertex(selected!)}>
              <Trash2 className="h-3.5 w-3.5" />노드 삭제
            </Button>
          </div>
        )}
        {saveMutation.isPending && <div className="mt-3 flex items-center gap-1 text-[11px] text-slate-500"><Save className="h-3 w-3" />저장 중...</div>}
      </div>
    </div>
  );
}
