import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { adminApi } from "@/lib/admin-api";

/** 승인 기록이 오래 남아 있으면 GUI 가 비정상 종료된 흔적일 수 있다. */
const STALE_AFTER_SEC = 60 * 30;

function elapsedText(grantedAt: number): string {
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - grantedAt));
  if (sec < 60) return `${sec}초`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분`;
  return `${Math.floor(sec / 3600)}시간 ${Math.floor((sec % 3600) / 60)}분`;
}

/**
 * 관리자 추종 중인 로봇 표시 + 해제.
 *
 * 추종 제어는 ai_service ↔ 로봇 직결로 돌고 FSM 을 거치지 않는다. 그래서 추종 중인
 * 로봇도 FSM 상으로는 IDLE/PATROL 로 보이고, 이 승인 기록이 관제가 그걸 아는 유일한
 * 단서다.
 *
 * 해제 버튼이 필요한 이유: 로봇 패널(libi_gui)이 강제 종료되거나 전원이 나가면 해제
 * 보고가 안 와서 기록이 남는다. 그러면 다음에 추종을 다시 시작할 수 없는데, 지금까지는
 * 서버에서 curl 을 치는 것 말고 방법이 없었다. /release 가 기록 삭제와 로봇의 WORKING
 * 복귀를 함께 처리한다.
 */
export function AdminFollowBanner() {
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: ["adminFollow", "status"],
    queryFn: () => adminApi.adminFollowStatus(),
    refetchInterval: 5000, // FSM 과 달리 push 채널이 없다
  });

  const release = useMutation({
    mutationFn: (robotId: string) => adminApi.adminFollowRelease(robotId),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["adminFollow", "status"] }),
  });

  const following = statusQuery.data?.following ?? [];
  if (following.length === 0) return null; // 추종 중인 로봇이 없으면 자리를 차지하지 않는다

  return (
    <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950">
      <p className="mb-2 text-sm font-medium text-amber-900 dark:text-amber-100">
        관리자 추종 중 — FSM 에는 안 잡히므로 여기서만 보입니다
      </p>
      <ul className="space-y-1.5">
        {following.map((grant) => {
          const stuck =
            grant.state_stale ||
            Date.now() / 1000 - grant.granted_at > STALE_AFTER_SEC;
          return (
            <li
              key={grant.robot_id}
              className="flex flex-wrap items-center gap-2 text-sm"
            >
              <span className="font-mono font-medium">{grant.robot_id}</span>
              <span className="text-muted-foreground">
                {elapsedText(grant.granted_at)} 경과
              </span>
              {grant.state_stale && (
                <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700 dark:bg-red-900 dark:text-red-200">
                  상태 수신 끊김
                </span>
              )}
              {stuck && (
                <span className="text-xs text-muted-foreground">
                  패널이 비정상 종료됐을 수 있습니다
                </span>
              )}
              <Button
                size="sm"
                variant={stuck ? "destructive" : "outline"}
                disabled={release.isPending}
                onClick={() => release.mutate(grant.robot_id)}
                className="ml-auto"
              >
                추종 해제
              </Button>
            </li>
          );
        })}
      </ul>
      {release.data?.reason && (
        <p className="mt-2 text-xs text-muted-foreground">
          {release.data.reason}
        </p>
      )}
    </div>
  );
}
