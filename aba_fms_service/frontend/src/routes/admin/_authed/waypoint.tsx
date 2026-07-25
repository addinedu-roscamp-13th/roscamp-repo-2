import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { WaypointEditor } from "@/components/admin/WaypointEditor";
import { useActiveRobotId } from "@/lib/active-robot";

export const Route = createFileRoute("/admin/_authed/waypoint")({ component: WaypointPage });

function WaypointPage() {
  // 편집·저장은 단일 공유 정본이라 로봇 무관. robotId 는 '이동' 대상의 초기 기본값으로만 넘긴다.
  const robotId = useActiveRobotId();

  return (
    <AdminShell title="Waypoint">
      <WaypointEditor robotId={robotId} />
    </AdminShell>
  );
}
