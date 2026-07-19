import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { WaypointEditor } from "@/components/admin/WaypointEditor";
import { useActiveRobotId, useActiveRobotType } from "@/lib/active-robot";

export const Route = createFileRoute("/admin/_authed/waypoint")({ component: WaypointPage });

function WaypointPage() {
  const robotId = useActiveRobotId();
  const robotType = useActiveRobotType();
  const canControl = robotType === "pinky" && robotId != null;

  return (
    <AdminShell title="Waypoint">
      <WaypointEditor robotId={robotId} canControl={canControl} />
    </AdminShell>
  );
}
