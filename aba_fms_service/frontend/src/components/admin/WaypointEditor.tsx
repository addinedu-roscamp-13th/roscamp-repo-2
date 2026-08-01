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

// 남은 시간 한 줄. drift_limit 안쪽은 정상 주행이라 "지연" 으로 쓰지 않는다.
function leftLabel(left: number, late: boolean) {
  if (left >= 0) return `T-${left.toFixed(1)}s`;
  return late ? `지연 +${(-left).toFixed(1)}s` : "도착 중";
}
const clockOf = (at: number) =>
  new Date(at * 1000).toLocaleTimeString("ko-KR", { hour12: false });

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
  // 예약까지 남은 시간을 초 단위로 다시 그리기 위한 틱. **통신이 아니라 브라우저 시계다** —
  // 예약 시각은 epoch + arrive*tick_sec 로 고정이라 서버에 더 물어볼 것이 없다.
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  // 브라우저 시계 - 서버 시계. 예약 시각은 fleet_node 의 벽시계 기준이라, 브라우저가
  // 몇 초 틀어져 있으면 "T-12.3초" 가 통째로 어긋난다(휴대폰이 특히 잘 틀어진다).
  const clockSkew = useRef(0);
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
        const p = snap?.stale ? null : snap?.plan;
        setPlan(p && typeof p === "object" && p.robots ? (p as FleetPlan) : null);
        const rt = snap?.stale ? {} : snap?.routes;
        setRoutes(rt && typeof rt === "object" ? (rt as Record<string, number[][]>) : {});
        const rows = payload?.snapshot?.robots;
        if (Array.isArray(rows)) {
          setFleetRobots(
            rows
              .filter((r) => r.x != null && r.y != null)
              .map((r) => ({
                name: r.name, x: r.x, y: r.y,
                yaw: typeof r.yaw === "number" ? r.yaw : null,
                state: r.state ?? null,
              })),
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
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
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

    Object.entries(routes).forEach(([rname, pts]) => {
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
      const label = left >= 0
        ? `T-${left.toFixed(1)}s`
        : overdue ? `지연 +${(-left).toFixed(1)}s` : "도착 중";
      ctx.font = "bold 12px system-ui";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = "rgba(2,6,23,0.92)";
      ctx.strokeText(label, cv[1][0] + 13, cv[1][1] - 12);
      ctx.fillStyle = overdue ? "#fca5a5" : col.hex;
      ctx.fillText(label, cv[1][0] + 13, cv[1][1] - 12);
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

    // 전 로봇 — fleet 피드 위치. goto 선택 로봇은 아래 wsState(초록 정밀 pose)로 덮인다.
    fleetRobots.forEach((r) => {
      if (r.name === gotoRobot?.name) return; // 선택 로봇은 wsState 로 그린다
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
  }, [map, wsState, graph, zoom, pan, selected, linkFirst, selectedLane, mapImageReady, fleetRobots, gotoRobot, plan, routes, nowSec]);

  // ── 예약 데이터 ──────────────────────────────────────────────────────────
  //
  // 한 덩어리로 쏟아 놓으면 "지금 뭐가 뭔지" 를 못 읽는다. 세 갈래로 나눈다:
  //   노드별 — 지도 위 모든 정점. 예약된 것은 누가 언제, 나머지는 "비어 있음"
  //   간선별 — 지나가는 통로. 정점만 봐서는 어느 길이 물리는지 모른다
  //   로봇별 — 한 대가 앞으로 어디를 어떤 순서로 밟는지
  //
  // ⚠️ 정점 이름은 **좌표로 되찾는다.** 계획이 싣고 오는 것은 fleet_node navgraph 의
  //    인덱스인데, 화면이 든 waypoint.yaml 과 정점 순서가 같다는 보장이 없다.
  const resv = (() => {
    const verts = Object.entries(graph.vertices);
    const empty = {
      byNode: verts.map(([n]) => ({ node: n, holds: [] as Hold[] })),
      byEdge: [] as { from: string; to: string; holds: EdgeHold[] }[],
      byRobot: [] as { robot: string; hex: string; stops: Hold[] }[],
      count: 0,
      edgeCount: 0,
    };
    if (!plan) return empty;
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
      count: byRobot.reduce((n, r) => n + r.stops.length, 0),
      edgeCount: holdsOnEdge.length,
    };
  })();

  useEffect(() => { draw(); }, [draw]);

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
            <div className="font-mono text-[11px] text-slate-500">
              {plan
                ? `계획 #${plan.seq} · 노드 ${resv.count} · 간선 ${resv.edgeCount} · 틱 ${plan.tick_sec}s`
                : "계획 없음 · 반응형(노드예약)으로 운행 중"}
            </div>
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
                        <span className="font-mono text-[11px] tabular-nums text-slate-500">
                          {clockOf(h.at)}~{clockOf(h.until)}
                        </span>
                        <span
                          className={cn(
                            "font-mono text-[11.5px] tabular-nums",
                            h.late ? "text-rose-400" : "text-slate-200",
                          )}
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
