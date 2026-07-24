import { useState } from "react";

import type { BtNodeStatus, FsmTreeNode } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

// py_trees 노드 상태 4종. FSM 상태 이름과는 다른 축이다 — 헷갈리지 말 것.
// 관제 화면은 밝은 배경이므로 진한 글씨색을 쓴다(예전 값은 다크 테마용이라 흰 바탕에서 흐렸다).
const STATUS_TEXT: Record<BtNodeStatus, string> = {
  // 실행 중인 경로가 어디인지 한눈에 찾는 게 이 화면의 목적이라, RUNNING 만 굵게 준다.
  RUNNING: "text-blue-700 font-semibold",
  SUCCESS: "text-emerald-700",
  FAILURE: "text-slate-400",
  INVALID: "text-slate-300",
};

const STATUS_TAG: Record<BtNodeStatus, string> = {
  RUNNING: "bg-blue-100 text-blue-700",
  SUCCESS: "bg-emerald-100 text-emerald-700",
  FAILURE: "bg-slate-100 text-slate-500",
  INVALID: "bg-slate-50 text-slate-400",
};

/** 이 서브트리 어딘가에서 지금 무언가 돌고 있는가. 기본 펼침 여부를 정한다. */
function hasRunning(node: FsmTreeNode): boolean {
  if (node.status === "RUNNING") return true;
  return (node.children ?? []).some(hasRunning);
}

function countNodes(node: FsmTreeNode): number {
  return 1 + (node.children ?? []).reduce((n, c) => n + countNodes(c), 0);
}

function TreeNode({
  node,
  prefix,
  connector,
}: {
  node: FsmTreeNode;
  prefix: string;
  connector: string;
}) {
  const allChildren = node.children ?? [];
  // 8개 브랜치가 매 tick 다 오지만 실제로 도는 건 하나뿐이다. 죽은 형제를 한 줄씩만
  // 남겨도 이름 8개가 깔려서 "지금 무슨 일이 일어나는가"가 묻힌다 — 아예 감춘다.
  //
  // 형제 중 하나라도 살아있을 때만 걸러낸다. 아무도 안 돌면(부팅 직후, 링크 끊김)
  // 전부 보여준다 — 그때는 빈 화면보다 트리 구조가 보이는 게 낫다.
  const liveChildren = allChildren.filter(hasRunning);
  const [showDead, setShowDead] = useState(false);
  const children =
    liveChildren.length > 0 && !showDead ? liveChildren : allChildren;
  const deadCount = allChildren.length - children.length;
  // 트리 전체(8개 브랜치)가 매번 오지만 그중 하나만 실제로 돈다. 죽은 브랜치까지 다 펼치면
  // 세로로 1400px 을 넘겨서 "지금 무슨 일이 일어나는가"가 묻힌다 — 살아있는 경로만 펼친다.
  //
  // null = 데이터를 따라간다. 상태가 전이되면 새로 살아난 브랜치가 저절로 펼쳐지고 이전
  // 브랜치는 접힌다. useState 초기값으로만 정하면 최초 1회 계산에 갇혀 그게 안 된다.
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? hasRunning(node);

  const textClass = STATUS_TEXT[node.status] ?? STATUS_TEXT.INVALID;
  const tagClass = STATUS_TAG[node.status] ?? STATUS_TAG.INVALID;
  const collapsible = children.length > 0;
  const hidden = collapsible && !open ? countNodes(node) - 1 : 0;

  return (
    <>
      <div className={cn("whitespace-pre leading-relaxed", textClass)}>
        <span className="text-slate-300">{prefix + connector}</span>
        {collapsible ? (
          <button
            type="button"
            onClick={() => setOverride(!open)}
            className="hover:underline"
            title={open ? "접기" : "펼치기"}
          >
            {node.name}
          </button>
        ) : (
          node.name
        )}
        <span
          className={cn(
            "ml-1.5 rounded px-1 align-[1px] text-[9px] font-normal",
            tagClass,
          )}
        >
          {node.status}
        </span>
        {hidden > 0 && (
          <span className="ml-1 text-[9px] font-normal text-slate-400">
            +{hidden}
          </span>
        )}
        {open && deadCount > 0 && (
          <button
            type="button"
            onClick={() => setShowDead(true)}
            className="ml-1.5 text-[9px] font-normal text-slate-400 hover:underline"
            title="지금 돌지 않는 브랜치도 보기"
          >
            (+{deadCount} 대기)
          </button>
        )}
        {open && showDead && allChildren.length > liveChildren.length && (
          <button
            type="button"
            onClick={() => setShowDead(false)}
            className="ml-1.5 text-[9px] font-normal text-slate-400 hover:underline"
          >
            (숨기기)
          </button>
        )}
      </div>
      {open &&
        children.map((child, i) => {
          const last = i === children.length - 1;
          return (
            <TreeNode
              key={`${child.name}-${i}`}
              node={child}
              // 자식의 연결선은 부모가 마지막이었는지에 따라 이어지거나 끊긴다.
              prefix={
                prefix +
                (connector ? (connector === "└─ " ? "   " : "│  ") : "")
              }
              connector={last ? "└─ " : "├─ "}
            />
          );
        })}
    </>
  );
}

/** 현재 상태에 대응하는 서브트리. 실행 중(RUNNING) 노드가 어디인지 보이는 게 핵심이다. */
export function BtTreeView({ tree }: { tree: FsmTreeNode | null }) {
  if (!tree) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground">
        BT 스냅샷을 아직 수신하지 못했습니다.
      </p>
    );
  }
  // 좁은 컬럼에서도 줄바꿈 없이 읽히도록 가로 스크롤을 이 안에 가둔다.
  // 세로도 카드 높이를 넘기지 않게 제한한다.
  return (
    <div className="max-h-[360px] overflow-auto font-mono text-[11px]">
      <TreeNode node={tree} prefix="" connector="" />
    </div>
  );
}
