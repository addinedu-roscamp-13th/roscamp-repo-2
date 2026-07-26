import { useEffect, useMemo, useState } from "react";

import { BtGraphView } from "@/components/admin/BtGraphView";
import type { BtNodeFlag } from "@/components/admin/BtGraphView";
import { BT_NODE_FLAGS } from "@/components/admin/btNodeFlags";
import type { BtNodeStatus, FsmSnapshot, FsmTreeNode } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

/**
 * BT 흐름 섹션 — 로봇 한 대의 행동트리를 크게, 실시간으로 본다.
 *
 * 아래 카드 3장은 "세 대를 동시에 훑는" 화면이고, 여기는 "한 대에서 지금 무슨 일이
 * 일어나는가"를 보는 화면이다. 폭을 다 쓰기 때문에 노드 이름이 읽히고, 그대로 녹화해도
 * 쓸 수 있다.
 */

const STATUSES: BtNodeStatus[] = ["RUNNING", "SUCCESS", "FAILURE", "INVALID"];

/**
 * 범례. 색이 나타내는 축이 셋이라 축을 나눠서 적는다 — 한 줄에 섞으면
 * "빨강이 실패인지 미배선인지"를 못 읽는다.
 */
const LEGEND: Record<BtNodeStatus, { label: string; hint: string; sw: string }> =
  {
    RUNNING: {
      label: "실행 중",
      hint: "이번 tick 에 돌고 있다 (조상까지 파랗게 이어진다)",
      sw: "bg-blue-600 border-blue-300",
    },
    SUCCESS: {
      label: "성공",
      hint: "조건을 통과했다",
      sw: "bg-emerald-600 border-emerald-300",
    },
    FAILURE: {
      label: "조건 불일치",
      hint: "실행은 됐고 '아니다'를 답했다 — 오류가 아니다",
      sw: "bg-slate-600 border-slate-400",
    },
    INVALID: {
      label: "미실행",
      hint: "이번 tick 에 아예 안 돌았다",
      sw: "bg-slate-800 border-slate-700",
    },
  };

const FLAG_LEGEND: { key: BtNodeFlag; label: string; hint: string; sw: string }[] =
  [
    {
      key: "unwired",
      label: "미배선",
      hint: "로직은 있는데 이 트리에서 부를 통로가 없다 — 상태색을 덮어쓴다",
      sw: "bg-red-700 border-red-300",
    },
    {
      key: "partial",
      label: "부분 구현",
      hint: "일부만 동작한다. 커서를 올리면 무엇이 빠졌는지 나온다",
      sw: "bg-amber-700 border-amber-300",
    },
    {
      key: "unreachable",
      label: "도달 불가",
      hint: "어떤 경로로도 진입할 수 없다",
      sw: "border-dashed border-slate-600 bg-slate-800 opacity-40",
    },
  ];

function tally(node: FsmTreeNode | null): Record<BtNodeStatus, number> {
  const out: Record<BtNodeStatus, number> = {
    RUNNING: 0,
    SUCCESS: 0,
    FAILURE: 0,
    INVALID: 0,
  };
  const walk = (n: FsmTreeNode) => {
    out[n.status] = (out[n.status] ?? 0) + 1;
    for (const c of n.children ?? []) walk(c);
  };
  if (node) walk(node);
  return out;
}

export function BtFlowSection({
  robots,
  snapshots,
}: {
  robots: string[];
  snapshots: Record<string, FsmSnapshot>;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);

  // 아직 안 골랐거나 고른 로봇이 목록에서 빠지면 되돌린다. 이때 **스냅샷이 실제로 오는
  // 로봇을 먼저** 고른다 — 등록만 되고 안 도는 로봇이 목록 앞에 있으면(sim-3 등) 화면이
  // "수신 대기"로 시작해서, 켜 놓고도 안 나온다고 오해하게 된다.
  useEffect(() => {
    if (robots.length === 0) return;
    if (picked && robots.includes(picked)) return;
    setPicked(robots.find((r) => snapshots[r]?.tree) ?? robots[0]);
  }, [robots, picked, snapshots]);

  const snapshot = picked ? snapshots[picked] : undefined;
  const tree = snapshot?.tree ?? null;
  // ⚠️ 트리 수신이 끊겼는데 그대로 그리면 **멈춘 트리가 살아 있는 것처럼** 보인다
  //    (RUNNING 색·halo·점선 애니메이션이 그대로 남는다). 상태(fsm_state)만 계속 오고
  //    스냅샷만 멈추는 경우가 있어서 `stale` 로는 못 잡는다 — 그래서 tree_stale 을 본다.
  const frozen = !!snapshot && (snapshot.tree_stale ?? snapshot.stale);
  const counts = useMemo(() => tally(tree), [tree]);
  const total = STATUSES.reduce((n, s) => n + counts[s], 0);

  // 정적 검사 결과는 트리에 실제로 있는 노드만 센다 — 감사 후 이름이 바뀌면
  // 범례 숫자가 0 으로 떨어져 바로 눈에 띈다.
  const flagCounts = useMemo(() => {
    const out: Record<BtNodeFlag, number> = {
      unwired: 0,
      partial: 0,
      unreachable: 0,
    };
    const walk = (n: FsmTreeNode) => {
      const f = BT_NODE_FLAGS[n.name];
      if (f) out[f] += 1;
      for (const c of n.children ?? []) walk(c);
    };
    if (tree) walk(tree);
    return out;
  }, [tree]);

  return (
    <section className="mb-3 overflow-hidden rounded-xl border bg-card">
      <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2.5">
        <h2 className="mr-1 text-sm font-bold text-slate-700">BT 흐름</h2>

        <div className="flex flex-wrap gap-1">
          {robots.map((name) => {
            const on = name === picked;
            const live = snapshots[name]?.tree != null;
            return (
              <button
                key={name}
                type="button"
                onClick={() => setPicked(name)}
                className={cn(
                  // active 시 살짝 눌리는 촉각 피드백. 색 전환은 150ms 로 짧게.
                  "flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs",
                  "transition-[color,background-color,border-color,transform] duration-150",
                  "active:translate-y-px",
                  on
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-transparent bg-muted text-muted-foreground hover:bg-muted/70",
                )}
              >
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    live ? "bg-emerald-400" : "bg-slate-400",
                  )}
                  // 스냅샷이 아직 안 온 로봇은 탭에서 바로 구분된다.
                  title={live ? "스냅샷 수신 중" : "스냅샷 없음"}
                />
                {name}
              </button>
            );
          })}
        </div>

        <span className="flex-1" />

        {frozen && (
          <span
            className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800"
            title="트리 스냅샷 수신이 끊겼습니다. 화면은 마지막으로 받은 트리이고 지금 상태가 아닙니다."
          >
            ⚠ 수신 끊김 · 정지 화면
          </span>
        )}
        {snapshot?.current_state && (
          <span className="rounded bg-slate-900 px-2 py-0.5 font-mono text-xs text-white">
            {snapshot.current_state}
          </span>
        )}

        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
            className="size-3.5 accent-slate-900"
          />
          활성 경로 따라가기
        </label>
      </header>

      {/* 범례는 그래프 **위**에 둔다 — 색을 보기 전에 뜻을 먼저 읽어야 한다. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b bg-muted/30 px-3 py-2 text-[11px]">
        <span className="font-semibold text-slate-600">실행 상태</span>
        {STATUSES.map((s) => (
          <span
            key={s}
            className="flex items-center gap-1.5 text-muted-foreground"
            title={LEGEND[s].hint}
          >
            <span className={cn("size-2.5 rounded border", LEGEND[s].sw)} />
            {LEGEND[s].label}
            <b className="font-mono font-normal text-slate-500">{counts[s]}</b>
          </span>
        ))}

        <span className="mx-1 h-3 w-px bg-border" />

        <span className="font-semibold text-slate-600">정적 검사</span>
        {FLAG_LEGEND.map((f) => (
          <span
            key={f.key}
            className="flex items-center gap-1.5 text-muted-foreground"
            title={f.hint}
          >
            <span className={cn("size-2.5 rounded border", f.sw)} />
            {f.label}
            <b className="font-mono font-normal text-slate-500">
              {flagCounts[f.key]}
            </b>
          </span>
        ))}

        <span className="flex-1" />
        <span className="text-muted-foreground">
          노드 {total} · 휠 확대 · 휠 클릭 드래그 이동 · 커서를 올리면 전체 이름
        </span>
      </div>

      <BtGraphView
        tree={tree}
        // 끊기면 애니메이션을 멈추고 화면을 죽인다 — 색만 남기면 여전히 살아 보인다.
        frozen={frozen}
        follow={follow}
        flags={BT_NODE_FLAGS}
        // 직접 확대·이동하는 순간 자동 추적을 끈다. 안 끄면 카메라가 도로 끌고 가서
        // 조작이 안 된다. 체크박스를 다시 켜면 잡아둔 화면을 놓고 추적으로 돌아간다.
        onManualControl={() => setFollow(false)}
        className="h-[52vh] min-h-[380px]"
      />
    </section>
  );
}
