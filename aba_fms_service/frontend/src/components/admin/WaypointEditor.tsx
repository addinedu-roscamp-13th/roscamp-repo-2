import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Navigation, Plus, RotateCcw, Save, Trash2, Unlink } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminApi, normalizeNav2State, type Nav2State, type WaypointGraph, type WaypointLane } from "@/lib/admin-api";
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
// (resolution/dims 는 arte2 와 같지만 **origin 은 다르다** — arte2 는 [-0.184, -1.949],
//  arte3 는 [-0.248, -1.958]. arte2 값을 물려쓰면 정점이 통째로 밀린다.)
const MAP_IMAGE_SRC = "/maps/arte3.png";
const STATIC_MAP: MapMeta = {
  width: 63,
  height: 108,
  resolution: 0.02,
  origin: { x: -0.248, y: -1.958, yaw: 0 },
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
    { name: string; x: number; y: number; state: string | null }[]
  >([]);
  const [gotoRobotId, setGotoRobotId] = useState<number | null>(robotId);

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
        const rows = payload?.snapshot?.robots;
        if (Array.isArray(rows)) {
          setFleetRobots(
            rows
              .filter((r) => r.x != null && r.y != null)
              .map((r) => ({ name: r.name, x: r.x, y: r.y, state: r.state ?? null })),
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
      ctx.fillStyle = "#38bdf8";
      ctx.strokeStyle = "#0f172a";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
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
  }, [map, wsState, graph, zoom, pan, selected, linkFirst, selectedLane, mapImageReady, fleetRobots, gotoRobot]);

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
