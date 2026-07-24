import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { adminApi, type FleetSnapshot } from "@/lib/admin-api";

/** 거절 사유는 요약하지 않고 그대로 보여준다 — 이 패널의 목적이 그 사유를 보는 것이다. */
function outcomeToast(ok: boolean, reason: string, okText: string) {
  if (ok) toast.success(okText);
  else toast.error(reason || "실패");
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      {hint ? (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function FleetPanel() {
  const [snapshot, setSnapshot] = useState<FleetSnapshot | null>(null);

  // 태스크 투입
  const [dropoff, setDropoff] = useState("");
  const [taskRobot, setTaskRobot] = useState("");
  const [armActions, setArmActions] = useState("0");

  // 로봇 조작
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState("");
  const [battery, setBattery] = useState("");

  const metaQuery = useQuery({
    queryKey: ["fleet", "meta"],
    queryFn: () => adminApi.fleetMeta(),
    staleTime: Infinity, // 상태 목록·플러그인 목록은 배포 단위로만 바뀐다
  });

  // 최초 1회 — WebSocket 이 붙기 전 빈 화면을 막는다.
  const snapshotQuery = useQuery({
    queryKey: ["fleet", "snapshot"],
    queryFn: () => adminApi.fleetSnapshot(),
  });

  // 실시간 갱신은 push 전용 — 폴링하지 않는다.
  useEffect(() => {
    const ws = new WebSocket(adminApi.fleetFeedWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.snapshot) setSnapshot(payload.snapshot);
      } catch {
        /* 형식이 깨진 프레임은 버린다 */
      }
    };
    return () => ws.close();
  }, []);

  const live = snapshot ?? snapshotQuery.data?.snapshot ?? null;
  const meta = metaQuery.data ?? null;
  // `?? []` 를 그대로 두면 매 렌더마다 새 배열이라 아래 useMemo 가 늘 재계산된다.
  const robots = useMemo(() => live?.robots ?? [], [live]);
  const robotNames = useMemo(() => robots.map((r) => r.name), [robots]);

  const submitTask = useMutation({
    mutationFn: () =>
      adminApi.fleetSubmitTask({
        dropoff,
        robot: taskRobot,
        arm_actions: Number(armActions) || 0,
      }),
    onSuccess: (res) =>
      outcomeToast(res.accepted, res.reason, `배차됨 — task ${res.task_id}`),
    onError: () => toast.error("요청 실패"),
  });

  const setModeMutation = useMutation({
    mutationFn: () => adminApi.fleetSetMode(selected, mode),
    onSuccess: (res) =>
      outcomeToast(res.ok, res.reason, `${selected} → ${mode}`),
    onError: () => toast.error("요청 실패"),
  });

  const setBatteryMutation = useMutation({
    mutationFn: () => adminApi.fleetSetBattery(selected, Number(battery)),
    onSuccess: (res) =>
      outcomeToast(res.ok, res.reason, `${selected} 배터리 ${battery}%`),
    onError: () => toast.error("요청 실패"),
  });

  const [dispatcher, setDispatcher] = useState("");
  const [traffic, setTraffic] = useState("");
  const setPluginsMutation = useMutation({
    mutationFn: () => adminApi.fleetSetPlugins(dispatcher, traffic),
    onSuccess: (res) =>
      outcomeToast(
        res.ok,
        res.reason,
        `배차=${res.active_dispatcher} 교통=${res.active_traffic}`,
      ),
    onError: () => toast.error("요청 실패"),
  });

  const reloadMutation = useMutation({
    mutationFn: () => adminApi.fleetReloadNavgraph(),
    onSuccess: (res) => outcomeToast(res.ok, res.reason, "navgraph 리로드됨"),
    onError: () => toast.error("요청 실패"),
  });

  const linked = live?.linked ?? false;

  return (
    <div className="space-y-4">
      {/* 링크 상태 */}
      <div
        className={`rounded-lg border p-3 text-sm ${
          linked
            ? "border-emerald-500/40 bg-emerald-500/5"
            : "border-amber-500/40 bg-amber-500/5"
        }`}
      >
        {linked ? (
          <>
            <span className="font-medium">fleet_node 연결됨</span>
            <span className="ml-2 text-muted-foreground">
              domain {live?.domain_id} · 배차 {live?.plugins.dispatcher || "—"}{" "}
              · 교통 {live?.plugins.traffic || "—"}
              {live?.stale ? " · 피드 끊김" : ""}
            </span>
          </>
        ) : (
          <>
            <span className="font-medium">fleet_node 미연결</span>
            <span className="ml-2 text-muted-foreground">
              fleet_ws 를 빌드·기동하세요:{" "}
              <code>
                ros2 run libi_fleet fleet_node --ros-args -p
                navgraph_file:=…/new_map.navgraph.yaml
              </code>
            </span>
          </>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="태스크 투입"
          hint="로봇을 비우면 dispatcher 가 경매로 고른다. 거절 사유가 그대로 표시된다."
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="fleet-dropoff">목표 정점</Label>
              <Input
                id="fleet-dropoff"
                value={dropoff}
                onChange={(e) => setDropoff(e.target.value)}
                placeholder="예: 7"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="fleet-arm">팔 동작 수</Label>
              <Input
                id="fleet-arm"
                type="number"
                min={0}
                value={armActions}
                onChange={(e) => setArmActions(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="fleet-task-robot">지정 로봇</Label>
              <Input
                id="fleet-task-robot"
                value={taskRobot}
                onChange={(e) => setTaskRobot(e.target.value)}
                placeholder="(비우면 경매)"
              />
            </div>
          </div>
          <Button
            className="mt-3"
            disabled={!dropoff || submitTask.isPending}
            onClick={() => submitTask.mutate()}
          >
            투입
          </Button>
          <p className="mt-2 text-xs text-muted-foreground">
            완주 관문: 배터리 ≥ 주행_m×1% + 팔동작×0.5% + 여유 15%. 못 넘으면{" "}
            <code>insufficient_battery</code> 로 거절된다.
          </p>
        </Section>

        <Section
          title="로봇 조작 (sim · 디버그)"
          hint="⚠️ 상태의 소유자는 libi_modes 다. 여기서 바꾸면 로봇의 실제 FSM 과 어긋난다 — 운영 중에는 FSM + BT 패널의 전이 요청을 쓸 것."
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>대상 로봇</Label>
              <Select value={selected} onValueChange={setSelected}>
                <SelectTrigger>
                  <SelectValue placeholder="로봇 선택" />
                </SelectTrigger>
                <SelectContent>
                  {robotNames.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {robotNames.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  아직 관측된 로봇이 없습니다. 아래에 이름을 직접 넣어 상태를
                  밀어 넣을 수도 있습니다.
                </p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="fleet-robot-manual">직접 입력</Label>
              <Input
                id="fleet-robot-manual"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                placeholder="예: pinky1"
              />
            </div>

            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label>상태</Label>
                <Select value={mode} onValueChange={setMode}>
                  <SelectTrigger>
                    <SelectValue placeholder="상태 선택" />
                  </SelectTrigger>
                  <SelectContent>
                    {(meta?.states ?? []).map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="secondary"
                disabled={!selected || !mode || setModeMutation.isPending}
                onClick={() => setModeMutation.mutate()}
              >
                설정
              </Button>
            </div>

            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label htmlFor="fleet-battery">배터리 %</Label>
                <Input
                  id="fleet-battery"
                  type="number"
                  min={0}
                  max={100}
                  value={battery}
                  onChange={(e) => setBattery(e.target.value)}
                  placeholder="0-100"
                />
              </div>
              <Button
                variant="secondary"
                disabled={
                  !selected || battery === "" || setBatteryMutation.isPending
                }
                onClick={() => setBatteryMutation.mutate()}
              >
                설정
              </Button>
            </div>
          </div>
        </Section>
      </div>

      <Section
        title="로봇 상태"
        hint="위치·태스크·점유는 fleet 피드에서 온다. 상태·배터리는 fleet_node 가 발행하지 않으므로 이 패널로 설정한 값만 표시된다(미설정이면 —)."
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="pb-2 pr-3">로봇</th>
                <th className="pb-2 pr-3">상태</th>
                <th className="pb-2 pr-3">배터리</th>
                <th className="pb-2 pr-3">busy</th>
                <th className="pb-2 pr-3">태스크</th>
                <th className="pb-2 pr-3">목표</th>
                <th className="pb-2 pr-3">점유 노드</th>
                <th className="pb-2 pr-3">위치</th>
              </tr>
            </thead>
            <tbody>
              {robots.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-3 text-muted-foreground">
                    관측된 로봇이 없습니다.
                  </td>
                </tr>
              ) : (
                robots.map((r) => (
                  <tr key={r.name} className="border-t">
                    <td className="py-2 pr-3 font-medium">
                      {r.name}
                      {r.stale ? (
                        <span className="ml-1 text-xs text-amber-600">
                          (끊김)
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">
                      {r.state ?? "—"}
                      {/* 로봇이 실제 발행한 값과, 이 화면에서 손으로 설정한 값을 구분해서
                          보여준다 — 둘을 섞으면 "왜 배차가 안 되지"를 추적할 수 없다. */}
                      {r.state_source === "fsm" ? (
                        <span className="ml-1 text-xs text-emerald-600">(로봇)</span>
                      ) : r.state_source === "panel" ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          (패널 설정)
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">
                      {r.battery === null ? "—" : `${r.battery}%`}
                    </td>
                    <td className="py-2 pr-3">{r.busy ? "○" : "—"}</td>
                    <td className="py-2 pr-3">
                      {r.task_id ? `${r.task_id} (${r.task_state})` : "—"}
                    </td>
                    <td className="py-2 pr-3">{r.goal_vertex ?? "—"}</td>
                    <td className="py-2 pr-3">
                      {r.held_nodes.length ? r.held_nodes.join(", ") : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {r.x === null
                        ? "—"
                        : `${r.x.toFixed(2)}, ${r.y?.toFixed(2)}`}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="알고리즘 교체"
          hint="pluginlib — 런타임에 배차·교통 전략을 갈아끼운다."
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>배차</Label>
              <Select value={dispatcher} onValueChange={setDispatcher}>
                <SelectTrigger>
                  <SelectValue
                    placeholder={live?.plugins.dispatcher || "선택"}
                  />
                </SelectTrigger>
                <SelectContent>
                  {(meta?.dispatchers ?? []).map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>교통</Label>
              <Select value={traffic} onValueChange={setTraffic}>
                <SelectTrigger>
                  <SelectValue placeholder={live?.plugins.traffic || "선택"} />
                </SelectTrigger>
                <SelectContent>
                  {(meta?.traffics ?? []).map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                disabled={
                  (!dispatcher && !traffic) || setPluginsMutation.isPending
                }
                onClick={() => setPluginsMutation.mutate()}
              >
                적용
              </Button>
              <Button
                variant="outline"
                disabled={reloadMutation.isPending}
                onClick={() => reloadMutation.mutate()}
              >
                navgraph 리로드
              </Button>
            </div>
          </div>
        </Section>

        <Section title="태스크 이력" hint="/fms/task_states — 최근 것이 위.">
          <div className="max-h-64 overflow-y-auto text-sm">
            {(live?.tasks ?? []).length === 0 ? (
              <p className="text-muted-foreground">아직 이벤트가 없습니다.</p>
            ) : (
              <ul className="space-y-1">
                {[...(live?.tasks ?? [])].reverse().map((t, i) => (
                  <li
                    key={`${t.task_id}-${t.at}-${i}`}
                    className="flex gap-2 border-b py-1"
                  >
                    <span className="font-mono text-xs">
                      {t.task_id || "—"}
                    </span>
                    <span className="text-xs">{t.state}</span>
                    <span className="text-xs text-muted-foreground">
                      {t.robot_id}
                    </span>
                    {t.progress > 0 ? (
                      <span className="ml-auto text-xs text-muted-foreground">
                        {Math.round(t.progress * 100)}%
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
