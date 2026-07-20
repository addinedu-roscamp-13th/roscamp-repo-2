import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { FleetPanel } from "@/components/admin/FleetPanel";
import { OrderPanel } from "@/components/admin/OrderPanel";

export const Route = createFileRoute("/admin/_authed/dispatch")({
  component: DispatchPage,
});

function DispatchPage() {
  // 두 층을 한 화면에: 위=배달 주문 오케스트레이터(주문→다리 시퀀스), 아래=fleet_node
  // 배차·교통 코어. 배차 대상은 fleet_node 가 관측한 로봇 목록에서 고른다.
  return (
    <AdminShell title="FMS 배차·교통">
      <div className="space-y-6">
        <div>
          <h2 className="mb-2 text-sm font-semibold text-muted-foreground">
            배달 주문 오케스트레이터
          </h2>
          <OrderPanel />
        </div>
        <div>
          <h2 className="mb-2 text-sm font-semibold text-muted-foreground">
            fleet_node 배차·교통 코어
          </h2>
          <FleetPanel />
        </div>
      </div>
    </AdminShell>
  );
}
