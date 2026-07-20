import { createFileRoute } from "@tanstack/react-router";

import { AdminShell } from "@/components/admin/AdminShell";
import { FsmBtPanel } from "@/components/admin/FsmBtPanel";

export const Route = createFileRoute("/admin/_authed/fsm")({
  component: FsmPage,
});

/**
 * 이 화면은 AdminShell 우상단의 전역 로봇 셀렉터를 쓰지 않는다 — 카드마다 로봇을
 * 따로 골라 최대 3대를 동시에 보는 게 목적이라, 한 번에 한 대만 가리키는 전역
 * 선택과는 맞지 않는다. 카드가 보내는 로봇 **이름**은 백엔드 resolve_robot_id 가
 * 브릿지 키("pinky1")로 바꾼다.
 */
function FsmPage() {
  return (
    <AdminShell title="FSM + BT">
      <FsmBtPanel />
    </AdminShell>
  );
}
