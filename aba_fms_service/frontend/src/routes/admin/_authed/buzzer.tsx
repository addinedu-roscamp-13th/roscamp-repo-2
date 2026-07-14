import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Bell, Music2, Play, Square, Volume2 } from "lucide-react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { adminApi, type RobotResult } from "@/lib/admin-api";
import { useActiveRobotBase, useActiveRobotId, useActiveRobotType } from "@/lib/active-robot";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/buzzer")({
  component: BuzzerPage,
});

type MelodyKey = "fur_elise" | "school_bell";

const MELODIES: Array<{ key: MelodyKey; title: string; subtitle: string }> = [
  { key: "fur_elise", title: "엘리제를 위하여", subtitle: "Beethoven · Fur Elise" },
  { key: "school_bell", title: "학교종이 땡땡땡", subtitle: "Korean nursery song" },
];

const PRESETS = [
  { key: "bell", label: "벨" },
  { key: "beep", label: "비프" },
  { key: "alarm", label: "알람" },
  { key: "success", label: "성공" },
  { key: "error", label: "오류" },
];

function resultToast(res: RobotResult, successMsg: string) {
  if (res.success) toast.success(successMsg);
  else toast.error(res.error || "명령 실패");
}

function BuzzerPage() {
  const qc = useQueryClient();
  const activeRobotId = useActiveRobotId();
  const activeRobotBase = useActiveRobotBase();
  const activeRobotType = useActiveRobotType();
  const canControl = activeRobotType === "pinky";
  const statusQuery = useQuery({
    queryKey: ["buzzer", "status", activeRobotId, activeRobotBase],
    queryFn: adminApi.buzzerStatus,
    enabled: canControl,
    refetchInterval: 1500,
  });

  const playMelody = useMutation({
    mutationFn: (melody: MelodyKey) => adminApi.playBuzzerMelody(melody),
    onSuccess: (res, melody) => {
      resultToast(res, `${MELODIES.find((m) => m.key === melody)?.title ?? melody} 재생 시작`);
      void qc.invalidateQueries({ queryKey: ["buzzer", "status"] });
    },
    onError: () => toast.error("멜로디 재생 실패"),
  });

  const stopMelody = useMutation({
    mutationFn: adminApi.stopBuzzerMelody,
    onSuccess: (res) => {
      resultToast(res, "부저 정지 완료");
      void qc.invalidateQueries({ queryKey: ["buzzer", "status"] });
    },
    onError: () => toast.error("부저 정지 실패"),
  });

  const presetMut = useMutation({
    mutationFn: (preset: string) => adminApi.playBuzzer(preset),
    onSuccess: (res, preset) => resultToast(res, `효과음 ${preset} 재생 완료`),
    onError: () => toast.error("효과음 재생 실패"),
  });

  const runningMelody = statusQuery.data?.melody as MelodyKey | null | undefined;
  const isRunning = !!statusQuery.data?.running;
  const busy = playMelody.isPending || stopMelody.isPending;

  return (
    <AdminShell title="부저">
      <div className="space-y-5">
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-orange-50 text-orange-600">
                <Volume2 className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-slate-900">부저 사운드 콘솔</h1>
                <p className="text-sm text-muted-foreground">PinkyPro 부저로 짧은 효과음과 멜로디를 재생합니다.</p>
              </div>
            </div>
            <Badge variant={isRunning ? "default" : "outline"} className="rounded-md">
              {isRunning ? "재생 중" : "대기"}
            </Badge>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          {MELODIES.map((melody) => {
            const active = runningMelody === melody.key;
            return (
              <Card key={melody.key} className={cn(active && "border-orange-300 shadow-sm")}>
                <CardHeader className="flex flex-row items-center gap-2 pb-3">
                  <Music2 className="h-5 w-5 text-orange-600" />
                  <div className="min-w-0 flex-1">
                    <CardTitle className="truncate text-base">{melody.title}</CardTitle>
                    <p className="text-xs text-muted-foreground">{melody.subtitle}</p>
                  </div>
                  {active && <Badge className="rounded-md">PLAY</Badge>}
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      className="gap-1.5"
                      disabled={!canControl || busy || active}
                      onClick={() => playMelody.mutate(melody.key)}
                    >
                      <Play className="h-4 w-4" />
                      Play
                    </Button>
                    <Button
                      variant="outline"
                      className="gap-1.5"
                      disabled={!canControl || busy || !active}
                      onClick={() => stopMelody.mutate()}
                    >
                      <Square className="h-4 w-4" />
                      Stop
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        <Card>
          <CardHeader className="flex flex-row items-center gap-2 pb-3">
            <Bell className="h-5 w-5 text-orange-600" />
            <CardTitle className="text-base">짧은 효과음</CardTitle>
            <Badge variant="outline" className="ml-auto text-xs">preset</Badge>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map(({ key, label }) => (
                <Button key={key} variant="outline" size="sm" disabled={!canControl || presetMut.isPending} onClick={() => presetMut.mutate(key)}>
                  {label}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </AdminShell>
  );
}
