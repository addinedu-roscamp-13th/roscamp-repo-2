import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { RobotConsole } from "@/components/admin/RobotConsole";
import { useActiveRobotBase, useActiveRobotId, useActiveRobotName, useActiveRobotType } from "@/lib/active-robot";

export const Route = createFileRoute("/admin/_authed/control")({ component: ControlPage });

function ControlPage() {
  const robotId = useActiveRobotId();
  const robotBase = useActiveRobotBase();
  const robotType = useActiveRobotType();
  const robotName = useActiveRobotName();
  const canControl = robotType === "pinky" && robotId != null;

  return (
    <AdminShell title="관제시스템">
      <RobotConsole
        robotId={robotId}
        robotBase={robotBase}
        robotName={robotName ?? undefined}
        canControl={canControl}
        variant="full"
      />
    </AdminShell>
  );
}
