import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { Battery, Loader2, Navigation, Play, Shield, Square, RefreshCw, AlertOctagon, HelpCircle, MapPin, Send } from "lucide-react";

import { AdminShell } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { adminApi, normalizeNav2State, type Robot, type Nav2State, type Nav2Grid, type Nav2Pose, type Nav2Locations } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/fms")({ component: FmsPage });

type RobotColorSchema = {
  fill: string;
  stroke: string;
  path: string;
  name: string;
};

const ROBOT_COLORS: Record<number, RobotColorSchema> = {
  0: { fill: "#3b82f6", stroke: "#93c5fd", path: "rgba(59, 130, 246, 0.4)", name: "Blue" },     // Robot 1
  1: { fill: "#a855f7", stroke: "#d8b4fe", path: "rgba(168, 85, 247, 0.4)", name: "Purple" },   // Robot 2
  2: { fill: "#f97316", stroke: "#ffedd5", path: "rgba(249, 115, 22, 0.4)", name: "Orange" },   // Robot 3
};

const DEFAULT_COLOR: RobotColorSchema = { fill: "#64748b", stroke: "#cbd5e1", path: "rgba(100, 116, 139, 0.3)", name: "Gray" };

type Viewport = { scale: number; ox: number; oy: number; width: number; height: number };

function worldToCanvas(map: Nav2Grid, vp: Viewport, wx: number, wy: number) {
  const mx = (wx - map.origin.x) / map.resolution;
  const my = (wy - map.origin.y) / map.resolution;
  return [vp.ox + mx * vp.scale, vp.oy + (map.height - 1 - my) * vp.scale] as const;
}

function canvasToWorld(map: Nav2Grid, vp: Viewport, cx: number, cy: number) {
  const ix = (cx - vp.ox) / vp.scale;
  const iy = map.height - 1 - (cy - vp.oy) / vp.scale;
  if (ix < 0 || iy < 0 || ix > map.width || iy > map.height) return null;
  return { x: map.origin.x + ix * map.resolution, y: map.origin.y + iy * map.resolution };
}

function FmsPage() {
  const queryClient = useQueryClient();
  const [selectedRobotId, setSelectedRobotId] = useState<number | null>(null);
  const [dispatchTargets, setDispatchTargets] = useState<Record<number, string>>({});
  const [scenarioTargets, setScenarioTargets] = useState<Record<number, number | "">>({});
  const [scenarioStartPending, setScenarioStartPending] = useState(false);
  const [message, setMessage] = useState("");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // 1. 활성 주행로봇 목록 가져오기
  const robotsQuery = useQuery({
    queryKey: ["fms", "robots"],
    queryFn: () => adminApi.listRobots({ robot_type: "pinky", limit: 3 }),
    staleTime: Infinity,
  });

  const robots = useMemo(
    () => (robotsQuery.data?.items ?? [])
      .filter((r) => r.is_active)
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })),
    [robotsQuery.data?.items],
  );

  useEffect(() => {
    const ws = new WebSocket(adminApi.fleetCoordinatorWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.state) queryClient.setQueryData(["fms", "coordinator"], payload.state);
      } catch {
        /* malformed coordinator frame */
      }
    };
    return () => ws.close();
  }, [queryClient]);

  // 로봇 목록 로딩 완료 후 기본 선택 로봇 설정
  useEffect(() => {
    if (robots.length > 0 && selectedRobotId === null) {
      setSelectedRobotId(robots[0].id);
    }
  }, [robots, selectedRobotId]);

  // 2. 각 로봇의 개별 Nav2 상태 실시간 갱신. /state 는 중앙 ROS 캐시를 읽는다.
  const [robotStates, setRobotStates] = useState<Record<number, Nav2State>>({});
  const locationsRobotKey = useMemo(() => robots.map((r) => r.id).join(","), [robots]);
  const locationsQuery = useQuery({
    queryKey: ["fms", "locations", locationsRobotKey],
    queryFn: async () => {
      const results = await Promise.all(robots.map(async (r) => {
        const locations = await adminApi.controlLocations(r.id).catch(() => ({}));
        return { id: r.id, locations };
      }));
      const next: Record<number, Nav2Locations> = {};
      results.forEach(({ id, locations }) => { next[id] = locations; });
      return next;
    },
    enabled: robots.length > 0,
    staleTime: Infinity,
  });
  const robotLocations = locationsQuery.data ?? {};

  const scenariosQuery = useQuery({
    queryKey: ["fms", "scenarios"],
    queryFn: adminApi.listLearnedScenarios,
    staleTime: 30_000,
  });
  const enabledScenarios = useMemo(
    () => (scenariosQuery.data?.items ?? []).filter((s) => s.enabled),
    [scenariosQuery.data?.items],
  );

  useEffect(() => {
    if (robots.length === 0 || enabledScenarios.length === 0) return;
    setScenarioTargets((prev) => {
      const next = { ...prev };
      for (const [idx, robot] of robots.entries()) {
        if (next[robot.id]) continue;
        const m = robot.name.match(/(\d+)\s*$/);
        const number = m?.[1] ?? String(idx + 1);
        const matched = enabledScenarios.find((s) =>
          s.name.includes("로봇 " + number) || s.name.includes("로봇" + number) || s.name.includes(number + "번"),
        );
        next[robot.id] = matched?.id ?? enabledScenarios[idx % enabledScenarios.length]?.id ?? "";
      }
      return next;
    });
  }, [robots, enabledScenarios]);

  useEffect(() => {
    if (robots.length === 0) {
      setRobotStates({});
      return;
    }

    const sockets = robots.map((r) => {
      const ws = new WebSocket(adminApi.controlStateWsUrl(r.id));
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.ok && payload.state) {
            const state = normalizeNav2State(payload.state as Nav2State);
            setRobotStates((prev) => ({ ...prev, [r.id]: state }));
          }
        } catch {
          /* malformed state frame */
        }
      };
      return ws;
    });

    return () => {
      sockets.forEach((ws) => ws.close());
    };
  }, [robots]);

  // 3. 공통 지도(Map) 정보 추출 (온라인 상태인 첫 번째 로봇의 지도를 바탕으로 시각화)
  const sharedMap = useMemo(() => {
    for (const r of robots) {
      const state = robotStates[r.id];
      if (state?.map?.width) return state.map;
    }
    return null;
  }, [robots, robotStates]);

  // 모든 로봇 통합 핀 좌표 목록 (첫 번째 온라인 로봇의 위치 좌표를 대표 좌표로 사용)
  const sharedLocations = useMemo(() => {
    for (const r of robots) {
      const locs = robotLocations[r.id];
      if (locs && Object.keys(locs).length > 0) return locs;
    }
    return {};
  }, [robots, robotLocations]);

  // 4. 지도 캔버스 그리기 함수
  const drawUnifiedMap = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 배경 지우기
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, rect.width, rect.height);

    if (!sharedMap) {
      // 지도 데이터를 불러올 수 없을 때 대기 화면 표시
      ctx.fillStyle = "#94a3b8";
      ctx.font = "13px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("지도를 제공할 온라인 로봇을 대기 중…", rect.width / 2, rect.height / 2);
      return;
    }

    // 뷰포트 계산
    const mapRatio = sharedMap.width / sharedMap.height;
    const canvasRatio = rect.width / rect.height;
    let scale = 1;
    let ox = 0;
    let oy = 0;

    if (mapRatio > canvasRatio) {
      scale = rect.width / sharedMap.width;
      oy = (rect.height - sharedMap.height * scale) / 2;
    } else {
      scale = rect.height / sharedMap.height;
      ox = (rect.width - sharedMap.width * scale) / 2;
    }
    const vp: Viewport = { scale, ox, oy, width: rect.width, height: rect.height };

    // 1) 배경 점유 격자 그리기
    const cell = Math.max(1, scale);
    for (let iy = 0; iy < sharedMap.height; iy++) {
      for (let ix = 0; ix < sharedMap.width; ix++) {
        const val = sharedMap.data[iy * sharedMap.width + ix];
        if (val === -1) continue; // 알 수 없는 영역은 검정 유지
        if (val === 100) {
          ctx.fillStyle = "#334155"; // 벽/장애물
          ctx.fillRect(ox + ix * scale, oy + (sharedMap.height - 1 - iy) * scale, cell, cell);
        } else if (val === 0) {
          ctx.fillStyle = "#1e293b"; // 이동 가능 빈 공간
          ctx.fillRect(ox + ix * scale, oy + (sharedMap.height - 1 - iy) * scale, cell, cell);
        }
      }
    }

    // 2) 등록된 주요 목적지(Locations) 핀 그리기
    Object.entries(sharedLocations).forEach(([name, pose]) => {
      const [cx, cy] = worldToCanvas(sharedMap, vp, pose.x, pose.y);
      ctx.fillStyle = "#38bdf8";
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#0ea5e9";
      ctx.font = "bold 10px monospace";
      ctx.textAlign = "center";
      ctx.fillText(name, cx, cy - 8);
    });

    // 3) 모든 로봇의 주행 경로(Global Path) 및 본체 그리기
    robots.forEach((r, index) => {
      const state = robotStates[r.id];
      if (!state) return;

      const colors = ROBOT_COLORS[index] ?? DEFAULT_COLOR;

      // 주행 경로 드로잉
      if (state.path && state.path.length > 0) {
        ctx.strokeStyle = colors.path;
        ctx.lineWidth = 3;
        ctx.beginPath();
        state.path.forEach((p, i) => {
          const [cx, cy] = worldToCanvas(sharedMap, vp, p.x, p.y);
          if (i === 0) ctx.moveTo(cx, cy);
          else ctx.lineTo(cx, cy);
        });
        ctx.stroke();
      }

      // 로봇 실시간 본체 드로잉
      if (state.pose) {
        const [cx, cy] = worldToCanvas(sharedMap, vp, state.pose.x, state.pose.y);

        // 로봇 원형
        ctx.fillStyle = colors.fill;
        ctx.beginPath();
        ctx.arc(cx, cy, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = selectedRobotId === r.id ? "#ffffff" : colors.stroke;
        ctx.lineWidth = selectedRobotId === r.id ? 3 : 1.5;
        ctx.stroke();

        // 선택 강조링
        if (selectedRobotId === r.id) {
          ctx.strokeStyle = "rgba(255, 255, 255, 0.4)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(cx, cy, 14, 0, Math.PI * 2);
          ctx.stroke();
        }

        // 로봇 헤딩 방향 지시선 (yaw)
        const len = 16;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + len * Math.cos(-state.pose.yaw), cy + len * Math.sin(-state.pose.yaw));
        ctx.stroke();

        // 로봇 이름 네임태그
        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 11px Inter, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(r.name, cx, cy - 14);
      }
    });

  }, [robots, robotStates, sharedMap, sharedLocations, selectedRobotId]);

  // 주기적으로 및 상태 변화 시 캔버스 갱신
  useEffect(() => {
    drawUnifiedMap();
  }, [drawUnifiedMap]);

  // 지도 마우스 클릭으로 이동 명령 전송
  const handleMapClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (selectedRobotId === null || !sharedMap) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    // 현재 뷰포트에 맞춘 스케일 재계산
    const mapRatio = sharedMap.width / sharedMap.height;
    const canvasRatio = rect.width / rect.height;
    let scale = 1;
    let ox = 0;
    let oy = 0;

    if (mapRatio > canvasRatio) {
      scale = rect.width / sharedMap.width;
      oy = (rect.height - sharedMap.height * scale) / 2;
    } else {
      scale = rect.height / sharedMap.height;
      ox = (rect.width - sharedMap.width * scale) / 2;
    }

    const vp: Viewport = { scale, ox, oy, width: rect.width, height: rect.height };
    const wpos = canvasToWorld(sharedMap, vp, cx, cy);
    if (!wpos) return;

    // 해당 좌표로 골 전송
    adminApi.controlGoal(selectedRobotId, { x: wpos.x, y: wpos.y })
      .then((res) => {
        setMessage(`${robots.find(r => r.id === selectedRobotId)?.name ?? "로봇"}에 목표 이동 요청 전송 성공 (${wpos.x.toFixed(2)}, ${wpos.y.toFixed(2)})`);
      })
      .catch((err) => {
        setMessage("목표 전송 실패: " + (err instanceof Error ? err.message : String(err)));
      });
  };

  // 5. 개별 및 그룹 이동 명령 API 통합 핸들링
  const dispatchRobot = (robotId: number, target: string) => {
    if (!target) return;
    adminApi.controlGoto(robotId, target)
      .then(() => setMessage(`${robots.find(r => r.id === robotId)?.name} -> ${target} 이동 시작`))
      .catch((err) => setMessage("이동 실패: " + (err instanceof Error ? err.message : String(err))));
  };

  const stopRobot = (robotId: number) => {
    adminApi.controlMissionStop(robotId)
      .then(() => setMessage(`${robots.find(r => r.id === robotId)?.name} 정지됨`))
      .catch((err) => setMessage("정지 실패: " + (err instanceof Error ? err.message : String(err))));
  };

  // 비상 정지 (ESTOP) - 모든 활성 로봇 중지
  const estopMutation = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(robots.map((r) => adminApi.controlMissionStop(r.id)));
      return results.filter((x) => x.status === "rejected").length;
    },
    onSuccess: (failed) => {
      setMessage(failed === 0 ? "⚠️ FMS 비상정지 완료: 모든 로봇을 제동했습니다." : `비상 정지 완료 (오류 ${failed}대)`);
    },
  });

  // 일괄 홈 복귀 (All Go Home)
  const allGoHome = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(robots.map((r) => adminApi.controlHome(r.id).catch(() => adminApi.controlGoto(r.id, "Parking"))));
      return results.filter((x) => x.status === "rejected").length;
    },
    onSuccess: (failed) => {
      setMessage(failed === 0 ? "모든 로봇에 홈 구역(또는 Parking) 복귀를 요청했습니다." : `홈 복귀 요청 완료 (오류 ${failed}대)`);
    },
  });

  // ── 우선순위 (코디네이터 설정 공유, 1이 가장 높음, 미지정 시 robot_id) ──
  // 우선순위가 높은 로봇이 먼저 출발하고, 근접 시엔 낮은 쪽이 양보(코디네이터)한다.
  const coordQuery = useQuery({
    queryKey: ["fms", "coordinator"],
    queryFn: () => adminApi.getFleetCoordinator(),
    staleTime: Infinity,
  });
  const priorities = coordQuery.data?.priorities ?? {};
  const prioOf = (id: number) => priorities[String(id)] ?? id;
  const byPriority = (list: Robot[]) => [...list].sort((x, y) => prioOf(x.id) - prioOf(y.id));
  const PRIORITY_STAGGER_MS = 2000; // 우선순위 순 시차 출발 간격

  // ── 순회: 각 로봇이 자기 저장 구역 전체를 loop 미션으로 순회 ──
  const startPatrolAll = () => {
    const ordered = byPriority(robots.filter((r) => !!robotStates[r.id]));
    if (ordered.length === 0) {
      setMessage("온라인 로봇이 없어 시나리오를 시작할 수 없습니다.");
      return;
    }
    if (enabledScenarios.length === 0) {
      setMessage("등록된 활성 시나리오가 없습니다. 로봇 학습 > 시나리오에서 먼저 등록하세요.");
      return;
    }
    const runnable = ordered
      .map((robot) => ({ robot, scenarioId: scenarioTargets[robot.id] }))
      .filter((item): item is { robot: Robot; scenarioId: number } => typeof item.scenarioId === "number");
    if (runnable.length === 0) {
      setMessage("실행할 로봇별 시나리오가 선택되지 않았습니다.");
      return;
    }

    setScenarioStartPending(true);
    runnable.forEach(({ robot, scenarioId }, i) => {
      window.setTimeout(() => {
        adminApi.runLearnedScenario(scenarioId, true, robot.id)
          .catch((err) => setMessage(`${robot.name} 시나리오 시작 실패: ` + (err instanceof Error ? err.message : String(err))))
          .finally(() => {
            if (i === runnable.length - 1) setScenarioStartPending(false);
          });
      }, i * PRIORITY_STAGGER_MS);
    });
    setMessage(`시나리오 순회 시작 — 우선순위 순 출발: ${runnable.map(({ robot }) => `${robot.name}(P${prioOf(robot.id)})`).join(" → ")}`);
  };

  const stopPatrolAll = () => {
    void Promise.allSettled(robots.map((r) => adminApi.controlMissionStop(r.id)))
      .then(() => setMessage("순회 전체 정지"));
  };

  // ── 교차 시나리오: 두 로봇이 서로의 현재 위치로 맞교환 이동 → 경로가 반드시 교차 ──
  // 순회 중이면 해당 로봇의 미션을 정지시킨 뒤, 우선순위 높은 로봇이 먼저 출발한다.
  const goalTo = (robot: Robot, pose: Nav2Pose) =>
    adminApi.controlGoal(robot.id, { x: pose.x, y: pose.y, yaw: pose.yaw ?? 0 });

  const runCrossScenario = (a: Robot, b: Robot) => {
    const pa = robotStates[a.id]?.pose;
    const pb = robotStates[b.id]?.pose;
    if (!pa || !pb) {
      setMessage(`${a.name}·${b.name}의 현재 위치를 아직 수신하지 못해 교차 시나리오를 시작할 수 없습니다.`);
      return;
    }
    const goals: Record<number, Nav2Pose> = { [a.id]: pb, [b.id]: pa };
    const [first, second] = byPriority([a, b]);
    void Promise.allSettled([adminApi.controlMissionStop(a.id), adminApi.controlMissionStop(b.id)])
      .then(() => goalTo(first, goals[first.id]))
      .then(() => {
        setMessage(`교차 ${a.name}↔${b.name}: ${first.name}(P${prioOf(first.id)}) 우선 출발 — ${second.name}은 ${PRIORITY_STAGGER_MS / 1000}초 후`);
        window.setTimeout(() => {
          goalTo(second, goals[second.id])
            .catch((err) => setMessage(`${second.name} 교차 목표 전송 실패: ` + (err instanceof Error ? err.message : String(err))));
        }, PRIORITY_STAGGER_MS);
      })
      .catch((err) => setMessage("교차 시나리오 실패: " + (err instanceof Error ? err.message : String(err))));
  };

  const runRotateScenario = () => {
    const poses = robots.map((r) => robotStates[r.id]?.pose);
    if (robots.length < 3 || poses.some((p) => !p)) {
      setMessage("순환 시나리오는 3대 모두 위치 수신 중일 때만 실행할 수 있습니다.");
      return;
    }
    // 목표: 1→2의 위치, 2→3의 위치, 3→1의 위치. 출발은 우선순위 순으로 시차를 둔다.
    const goals: Record<number, Nav2Pose> = {};
    robots.forEach((r, i) => { goals[r.id] = poses[(i + 1) % robots.length]!; });
    const ordered = byPriority(robots);
    void Promise.allSettled(robots.map((r) => adminApi.controlMissionStop(r.id))).then(() => {
      ordered.forEach((r, i) => {
        window.setTimeout(() => {
          goalTo(r, goals[r.id])
            .catch((err) => setMessage(`${r.name} 순환 목표 전송 실패: ` + (err instanceof Error ? err.message : String(err))));
        }, i * PRIORITY_STAGGER_MS);
      });
      setMessage(`순환 시나리오 — 우선순위 순 출발: ${ordered.map((r) => `${r.name}(P${prioOf(r.id)})`).join(" → ")}`);
    });
  };

  // 활성 로봇 쌍 조합 (1↔2, 1↔3, 2↔3)
  const robotPairs = useMemo(() => {
    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < robots.length; i++)
      for (let j = i + 1; j < robots.length; j++) pairs.push([i, j]);
    return pairs;
  }, [robots]);

  return (
    <AdminShell title="FMS 통합 제어">
      <div className="-m-4 min-h-[calc(100vh-60px)] bg-[radial-gradient(circle_at_top_left,#1e1b4b,#020617_60%,#000_100%)] p-4 text-slate-100 md:-m-6 md:p-6 flex flex-col gap-6">
        
        {/* 상단 컨트롤 헤더 */}
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 pb-4">
          <div>
            <div className="text-xl font-bold tracking-tight bg-gradient-to-r from-blue-400 via-indigo-300 to-purple-400 bg-clip-text text-transparent font-sans">FMS (Fleet Management System) 중앙관제</div>
            <div className="text-[12px] text-slate-400">
              최대 3대의 Pinky 주행로봇을 단일 맵 프레임 위에서 실시간 조율 및 원격 디스패치합니다.
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {message && <span className="text-[12px] bg-slate-800/80 px-3 py-1.5 rounded-full border border-white/5 text-sky-300 animate-pulse">{message}</span>}
            
            <Button
              onClick={startPatrolAll}
              size="sm"
              variant="outline"
              className="rounded-full border-emerald-500/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20"
              disabled={robots.length === 0 || scenarioStartPending}
              title="로봇별로 선택된 DB 시나리오를 우선순위 순서로 시차 실행"
            >
              <Play className="mr-1.5 h-3.5 w-3.5" />시나리오 순회 시작
            </Button>

            <Button
              onClick={stopPatrolAll}
              size="sm"
              variant="outline"
              className="rounded-full border-amber-500/30 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20"
              disabled={robots.length === 0}
            >
              <Square className="mr-1.5 h-3.5 w-3.5" />순회 정지
            </Button>

            {robotPairs.map(([i, j]) => {
              const a = robots[i];
              const b = robots[j];
              const ready = !!robotStates[a.id]?.pose && !!robotStates[b.id]?.pose;
              return (
                <Button
                  key={`${a.id}-${b.id}`}
                  onClick={() => runCrossScenario(a, b)}
                  size="sm"
                  variant="outline"
                  className="rounded-full border-slate-700 bg-slate-900/60 hover:bg-slate-800 text-slate-200"
                  disabled={!ready}
                  title={ready ? `${a.name}와 ${b.name}가 서로의 현재 위치로 맞교환 이동` : "두 로봇의 위치 수신 대기 중"}
                >
                  교차 {i + 1}↔{j + 1}
                </Button>
              );
            })}

            {robots.length >= 3 && (
              <Button
                onClick={runRotateScenario}
                size="sm"
                variant="outline"
                className="rounded-full border-slate-700 bg-slate-900/60 hover:bg-slate-800 text-slate-200"
                disabled={robots.some((r) => !robotStates[r.id]?.pose)}
                title="1→2→3→1 순서로 서로의 현재 위치로 순환 이동"
              >
                순환 1→2→3
              </Button>
            )}

            <Button
              onClick={() => allGoHome.mutate()}
              size="sm"
              variant="outline"
              className="rounded-full border-indigo-500/30 bg-indigo-500/10 text-indigo-200 hover:bg-indigo-500/20"
              disabled={robots.length === 0}
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />전체 홈 복귀
            </Button>

            <Button
              onClick={() => estopMutation.mutate()}
              size="sm"
              className="rounded-full bg-rose-600 hover:bg-rose-700 text-white font-bold animate-bounce shadow-[0_0_15px_rgba(244,63,94,0.5)]"
              disabled={robots.length === 0 || estopMutation.isPending}
            >
              <AlertOctagon className="mr-1.5 h-4 w-4" />비상 정지 (ESTOP)
            </Button>
          </div>
        </div>

        {/* 맵 시각화 및 사이드패널 레이아웃 */}
        <div className="grid gap-6 lg:grid-cols-3 xl:grid-cols-4 flex-1">
          
          {/* 중앙 통합 캔버스 맵 (좌측 2/3 또는 3/4) */}
          <div className="lg:col-span-2 xl:col-span-3 flex flex-col rounded-3xl border border-white/10 bg-slate-950/80 p-4 shadow-2xl relative">
            <div className="mb-2 flex items-center justify-between px-2">
              <span className="text-[13px] font-semibold text-slate-300 flex items-center gap-1.5"><MapPin className="h-4 w-4 text-sky-400" /> 공통 월드 맵 및 플릿 모니터</span>
              <span className="text-[11px] text-slate-500">맵 클릭 시 선택된 로봇({robots.find(r => r.id === selectedRobotId)?.name ?? "없음"})의 이동 목표가 전송됩니다.</span>
            </div>
            
            <div className="flex-1 min-h-[450px] relative rounded-2xl overflow-hidden border border-white/5 bg-slate-950">
              <canvas
                ref={canvasRef}
                onClick={handleMapClick}
                className="w-full h-full cursor-crosshair block"
              />
            </div>
          </div>

          {/* 우측 플릿 리스트 및 제어부 (우측 1/3) */}
          <div className="flex flex-col gap-4">
            
            {/* 근접 충돌 코디네이터 연계 감시 + 우선순위 설정 */}
            <FmsCoordinatorPanel robots={robots} />

            {/* 로봇별 제어 카드 목록 */}
            <div className="text-[13px] font-semibold text-slate-400 px-1">소형 로봇 플릿 상태 ({robots.length}대)</div>
            {robotsQuery.isLoading ? (
              <div className="flex items-center gap-2 text-slate-400 py-6"><Loader2 className="h-4.5 w-4.5 animate-spin" /> 로봇 연결 상태 조회 중...</div>
            ) : robots.length === 0 ? (
              <div className="rounded-2xl border border-white/10 bg-slate-900/40 p-5 text-[12px] text-slate-400">
                활성화된 Pinky 주행로봇이 없습니다. IP 관리 메뉴에서 로봇을 추가해주세요.
              </div>
            ) : (
              <div className="space-y-4">
                {robots.map((r, index) => {
                  const state = robotStates[r.id];
                  const locs = robotLocations[r.id] ?? {};
                  const colors = ROBOT_COLORS[index] ?? DEFAULT_COLOR;
                  const isSelected = selectedRobotId === r.id;
                  const targetLoc = dispatchTargets[r.id] ?? "";

                  return (
                    <div
                      key={r.id}
                      onClick={() => setSelectedRobotId(r.id)}
                      className={cn(
                        "rounded-2xl border p-4 transition-all duration-300 cursor-pointer shadow-lg relative overflow-hidden",
                        isSelected 
                          ? "bg-slate-900/90 border-white/20 ring-1 ring-white/10 scale-[1.01]" 
                          : "bg-slate-950/40 border-white/5 hover:bg-slate-950/70"
                      )}
                    >
                      {/* 카드 색상 데코 */}
                      <div className="absolute top-0 left-0 w-1.5 h-full" style={{ backgroundColor: colors.fill }} />

                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2 pl-1.5">
                          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: colors.fill }} />
                          <span className="font-bold text-[14px]">{r.name}</span>
                          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300" title="우선순위 (1이 가장 높음)">P{prioOf(r.id)}</span>
                          <span className="text-[11px] text-slate-500">({r.ip_address})</span>
                        </div>
                        <span className={cn(
                          "px-2 py-0.5 rounded-full text-[10px] font-semibold",
                          state ? "bg-emerald-500/10 text-emerald-300" : "bg-rose-500/10 text-rose-300"
                        )}>
                          {state ? "Online" : "Offline"}
                        </span>
                      </div>

                      {state && (
                        <div className="space-y-3 pl-1.5">
                          {/* 배터리 및 속도 정보 */}
                          <div className="flex items-center gap-4 text-[12px] text-slate-300">
                            <span className="flex items-center gap-1">
                              <Battery className="h-4 w-4 text-emerald-400" />
                              {state.battery.percent != null ? `${state.battery.percent.toFixed(0)}%` : "—"}
                            </span>
                            <span className="flex items-center gap-1 font-mono text-[11px] text-slate-400">
                              X:{state.pose?.x.toFixed(2)} Y:{state.pose?.y.toFixed(2)}
                            </span>
                          </div>

                          {/* 미션 상태 */}
                          <div className="text-[11px] text-slate-400 bg-slate-950/50 p-1.5 rounded-lg border border-white/5">
                            <span className="text-slate-500">상태: </span>
                            <span className="text-slate-300 font-medium">
                              {state.mission.status === "navigating" ? `이동 중 (${state.mission.current || "목표"})`
                                : state.mission.status === "running" ? `순회 중 (${state.mission.current || "구역"})`
                                : "정지 대기"}
                            </span>
                          </div>

                          {/* 시나리오 기반 순회 */}
                          <div className="space-y-1.5" onClick={(e) => e.stopPropagation()}>
                            <div className="text-[10px] font-semibold text-slate-500">DB 시나리오 순회</div>
                            <select
                              value={scenarioTargets[r.id] ?? ""}
                              onChange={(e) => setScenarioTargets((prev) => ({ ...prev, [r.id]: e.target.value ? Number(e.target.value) : "" }))}
                              className="h-8 w-full rounded-lg border border-emerald-500/20 bg-slate-950/90 px-2 text-[12px] text-slate-200 focus:outline-none"
                            >
                              <option value="">시나리오 선택...</option>
                              {enabledScenarios.map((scenario) => (
                                <option key={scenario.id} value={scenario.id}>{scenario.name}</option>
                              ))}
                            </select>
                          </div>

                          {/* 디스패치 제어 콘솔 */}
                          <div className="flex items-center gap-2 pt-1" onClick={(e) => e.stopPropagation()}>
                            <select
                              value={targetLoc}
                              onChange={(e) => setDispatchTargets(prev => ({ ...prev, [r.id]: e.target.value }))}
                              className="h-8 flex-1 rounded-lg border border-slate-700 bg-slate-950/90 px-2 text-[12px] text-slate-200 focus:outline-none"
                            >
                              <option value="">목적지 선택...</option>
                              {Object.keys(locs).map((name) => (
                                <option key={name} value={name}>{name}</option>
                              ))}
                            </select>

                            <Button
                              size="sm"
                              className="h-8 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white px-2.5"
                              disabled={!targetLoc}
                              onClick={() => dispatchRobot(r.id, targetLoc)}
                            >
                              <Send className="h-3.5 w-3.5" />
                            </Button>

                            <Button
                              size="sm"
                              variant="outline"
                              className="h-8 rounded-lg border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20 px-2"
                              onClick={() => stopRobot(r.id)}
                            >
                              <Square className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

          </div>

        </div>

      </div>
    </AdminShell>
  );
}

// FMS 세이프티 가드 패널
function FmsCoordinatorPanel({ robots }: { robots: Robot[] }) {
  const queryClient = useQueryClient();
  const [minDist, setMinDist] = useState("0.6");
  const [msg, setMsg] = useState("");
  const [prioEdit, setPrioEdit] = useState<Record<number, string>>({});

  const coordQuery = useQuery({
    queryKey: ["fms", "coordinator"],
    queryFn: () => adminApi.getFleetCoordinator(),
    staleTime: Infinity,
  });
  const coord = coordQuery.data;

  useEffect(() => {
    if (coord) setMinDist(String(coord.min_distance));
  }, [coord?.min_distance]);

  // 우선순위 편집칸 초기값: 저장된 값 → 없으면 robot_id
  useEffect(() => {
    if (!coord) return;
    setPrioEdit((prev) => {
      const next = { ...prev };
      for (const r of robots) {
        if (next[r.id] === undefined) {
          next[r.id] = String(coord.priorities?.[String(r.id)] ?? r.id);
        }
      }
      return next;
    });
  }, [coord, robots]);

  const update = useMutation({
    mutationFn: (body: { enabled?: boolean; min_distance?: number; priorities?: Record<number, number>; evasion_enabled?: boolean }) => adminApi.updateFleetCoordinator(body),
    onSuccess: (data) => queryClient.setQueryData(["fms", "coordinator"], data),
    onError: () => setMsg("오류"),
  });

  const savePriorities = () => {
    const priorities: Record<number, number> = {};
    for (const r of robots) {
      const v = Number(prioEdit[r.id]);
      priorities[r.id] = Number.isFinite(v) && v > 0 ? Math.floor(v) : r.id;
    }
    update.mutate({ priorities });
    setMsg("우선순위 저장됨");
  };
  
  const resume = useMutation({
    mutationFn: (robotId: number) => adminApi.fleetResume(robotId),
    onSuccess: () => { setMsg("재개 성공"); void queryClient.invalidateQueries({ queryKey: ["fms", "coordinator"] }); },
  });

  const enabled = coord?.enabled ?? false;
  const evasion = coord?.evasion_enabled ?? false;
  const holds = coord?.holds ?? {};
  const heldIds = Object.keys(holds);
  const robotsById = new Map((coord?.robots ?? []).map((r) => [r.id, r]));

  return (
    <div className="rounded-2xl border border-white/10 bg-slate-950/70 p-4 shadow-xl">
      <div className="flex items-center justify-between mb-3">
        <span className="flex items-center gap-1.5 text-[13px] font-bold text-slate-200">
          <Shield className={cn("h-4.5 w-4.5", enabled ? "text-emerald-400 animate-pulse" : "text-slate-500")} /> FMS 충돌 안전 조율
        </span>
        <button
          type="button"
          onClick={() => update.mutate({ enabled: !enabled })}
          className={cn(
            "rounded-full px-2.5 py-0.5 text-[11px] font-bold transition",
            enabled ? "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40" : "bg-slate-800 text-slate-400"
          )}
        >
          {enabled ? "ACTIVE" : "OFF"}
        </button>
      </div>

      <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400 mb-3">
        <span className="flex items-center gap-1">
          제동 거리
          <Input 
            value={minDist} 
            onChange={(e) => setMinDist(e.target.value)} 
            className="h-6 w-11 rounded bg-slate-900 border-slate-700 text-center font-mono text-slate-200 p-0 text-[11px]" 
          />
          m
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 text-[10px] rounded bg-slate-800 text-slate-300 px-2"
          onClick={() => update.mutate({ min_distance: Number(minDist) || 0.45 })}
        >
          설정 적용
        </Button>
      </div>

      {/* 막는 로봇 자동 비켜세우기(evasion) — 자동 주행이라 기본 OFF, 명시적 opt-in */}
      <div className="flex items-center justify-between gap-2 mb-3 rounded-xl border border-white/5 bg-slate-900/40 p-2">
        <span className="flex flex-col">
          <span className="text-[11px] font-semibold text-slate-300">막는 로봇 자동 비켜세우기</span>
          <span className="text-[10px] text-slate-500">경로 막는 로봇을 빈 구역으로 이동 · 통과 후 복귀 (자동 주행)</span>
        </span>
        <button
          type="button"
          onClick={() => update.mutate({ evasion_enabled: !evasion })}
          title={evasion ? "자동 비켜세우기 켜짐" : "꺼짐 — 켜면 로봇을 자동 주행시킵니다"}
          className={cn(
            "shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold transition",
            evasion ? "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40" : "bg-slate-800 text-slate-400"
          )}
        >
          {evasion ? "ON" : "OFF"}
        </button>
      </div>

      {/* 로봇 우선순위 설정 — 1이 가장 높음. 높은 로봇이 먼저 출발하고, 근접 시 낮은 쪽이 양보 */}
      <div className="mb-3 rounded-xl border border-white/5 bg-slate-900/40 p-2">
        <div className="mb-1.5 text-[11px] font-semibold text-slate-300">로봇 우선순위 <span className="font-normal text-slate-500">(숫자 작을수록 우선)</span></div>
        <div className="space-y-1">
          {robots.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
              <span>{r.name}</span>
              <Input
                value={prioEdit[r.id] ?? ""}
                onChange={(e) => setPrioEdit((prev) => ({ ...prev, [r.id]: e.target.value }))}
                className="h-6 w-11 rounded bg-slate-900 border-slate-700 text-center font-mono text-slate-200 p-0 text-[11px]"
              />
            </div>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="mt-1.5 h-6 w-full text-[10px] rounded bg-slate-800 text-slate-300"
          disabled={robots.length === 0 || update.isPending}
          onClick={savePriorities}
        >
          우선순위 저장
        </Button>
        {msg && <div className="mt-1 text-[10px] text-sky-300">{msg}</div>}
      </div>

      {enabled && heldIds.length > 0 && (
        <div className="space-y-2">
          {heldIds.map((idStr) => {
            const id = Number(idStr);
            const h = holds[idStr];
            const rs = robotsById.get(id);
            const resumable = rs?.resumable ?? false;
            return (
              <div key={idStr} className="flex items-center justify-between gap-1.5 rounded-xl border border-rose-500/20 bg-rose-950/30 p-2">
                <div className="text-[11px] text-rose-200 leading-tight">
                  🚨 <b>{rs?.name ?? `로봇 #${id}`}</b> 자동제동<br/>
                  ({h.peer_name} 근접: {h.distance}m)
                </div>
                <Button 
                  size="sm" 
                  className="h-6 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-slate-950 px-2 text-[10px] font-bold" 
                  disabled={!resumable || resume.isPending} 
                  onClick={() => resume.mutate(id)}
                >
                  출발 재개
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
