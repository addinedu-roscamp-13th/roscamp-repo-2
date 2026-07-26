import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { BtFlowSection } from "@/components/admin/BtFlowSection";
import { FsmRobotCard } from "@/components/admin/FsmRobotCard";
import { adminApi, type FsmSnapshot } from "@/lib/admin-api";

/** 카드 개수. 주행 로봇 3대를 동시에 보는 게 이 화면의 목적이다. */
const CARD_COUNT = 3;

/**
 * FSM + BT 화면 — 로봇 카드 3장.
 *
 * 카드마다 독립적으로 로봇을 고를 수 있다. AdminShell 우상단의 전역 로봇 셀렉터는
 * 쓰지 않는다 — 그건 한 번에 한 대만 볼 수 있어서 이 화면의 목적과 어긋난다.
 *
 * WebSocket 은 **페이지에 하나만** 연다. 백엔드의 /api/fsm/ws/state 는 특정 로봇을
 * 구독하는 게 아니라 갱신된 로봇을 전부 흘려보내므로, 카드마다 소켓을 열면 같은
 * 프레임을 3번 받는 낭비가 된다. 여기서 robot_id 로 접어두고 카드가 골라 쓴다.
 */
export function FsmBtPanel() {
  const [snapshots, setSnapshots] = useState<Record<string, FsmSnapshot>>({});
  const [selected, setSelected] = useState<(string | null)[]>(
    Array(CARD_COUNT).fill(null),
  );
  const [autoFilled, setAutoFilled] = useState(false);

  const modelQuery = useQuery({
    queryKey: ["fsm", "model"],
    queryFn: () => adminApi.fsmModel(),
    staleTime: Infinity, // 전이 박스는 배포 단위로만 바뀐다
  });

  const robotsQuery = useQuery({
    queryKey: ["robots", "pinky"],
    queryFn: () => adminApi.listRobots({ robot_type: "pinky", limit: 50 }),
  });

  const robots = useMemo(
    () => (robotsQuery.data?.items ?? []).filter((r) => r.is_active),
    [robotsQuery.data],
  );

  // 등록된 주행 로봇 순서대로 앞 카드부터 채운다. 로봇이 3대 미만이면 남는 카드는
  // "로봇 선택" 상태로 남는다 — 빈 카드가 깨지지 않게 하는 게 요구사항이다.
  useEffect(() => {
    if (autoFilled || robots.length === 0) return;
    setSelected((prev) => prev.map((cur, i) => cur ?? robots[i]?.name ?? null));
    setAutoFilled(true);
  }, [robots, autoFilled]);

  // 실시간 갱신은 WebSocket push 전용 — 폴링하지 않는다 (INSTRUCTION.md).
  useEffect(() => {
    const ws = new WebSocket(adminApi.fsmStateWsUrl());
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.ok && payload.robot_id && payload.snapshot) {
          setSnapshots((prev) => ({
            ...prev,
            [payload.robot_id]: payload.snapshot,
          }));
        }
      } catch {
        /* 형식이 깨진 프레임은 버린다 */
      }
    };
    return () => ws.close();
  }, []);

  const model = modelQuery.data?.model ?? null;

  if (modelQuery.isLoading || !model) {
    return (
      <p className="text-sm text-muted-foreground">FSM 모델을 불러오는 중…</p>
    );
  }

  // 3열로 나란히 — 로봇 3대를 한 화면에서 훑는 게 목적이다. 카드 자체가 세로 컬럼이라
  // 상태도와 BT 는 카드 안에서 위아래로 쌓인다. 좁은 화면에서는 2열 → 1열로 접는다.
  // items-stretch + 카드의 min-h-[75vh] 로 카드가 화면 세로를 채우고, 남는 공간은
  // 전이 이력이 flex-1 로 흡수해 스크롤한다.
  return (
    <>
      {/* 위: 한 대를 크게 — BT 흐름 그래프. 아래: 세 대를 나란히 — 전이 제어·이력. */}
      <BtFlowSection
        robots={robots.map((r) => r.name)}
        snapshots={snapshots}
      />

      <div className="grid items-stretch gap-3 md:grid-cols-2 xl:grid-cols-3">
        {selected.map((name, index) => (
          <FsmRobotCard
            key={index}
            robotName={name}
            onRobotChange={(next) =>
              setSelected((prev) =>
                prev.map((cur, i) => (i === index ? next : cur)),
              )
            }
            robots={robots}
            takenNames={selected.filter(
              (n, i): n is string => !!n && i !== index,
            )}
            model={model}
            liveSnapshots={snapshots}
          />
        ))}

        {robots.length === 0 && (
          <p className="text-sm text-muted-foreground">
            등록된 주행 로봇(pinky)이 없습니다. 로봇 레지스트리에서 먼저
            등록하세요.
          </p>
        )}
      </div>
    </>
  );
}
