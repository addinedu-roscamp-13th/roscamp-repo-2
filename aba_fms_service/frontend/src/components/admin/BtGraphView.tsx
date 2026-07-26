import { useEffect, useMemo, useRef } from "react";

import type { BtNodeStatus, FsmTreeNode } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

/**
 * BT 트리를 SVG 그래프로 실시간 렌더링한다. Groot2 / py_trees_ros_viewer 와 같은 그림이되,
 * 발표에서 그대로 녹화할 수 있도록 카메라가 활성 경로를 따라간다.
 *
 * ## 왜 직접 SVG 인가
 * 이 프로젝트 프론트엔드에는 그래프 라이브러리가 없다(mermaid 뿐인데, 매 프레임 SVG 를
 * 통째로 다시 그려서 10Hz 갱신에는 깜빡인다). 트리 레이아웃은 재귀 한 번이면 끝나고,
 * 직접 그리면 상태 색이 CSS transition 으로 부드럽게 넘어간다 — 의존성 0.
 *
 * ## 위→아래로 놓는다 (Groot2·BT.CPP 와 같은 방향)
 * 실측 트리는 노드 75 · 깊이 6 · **리프 51**. 좌→우로 놓으면 1392×1734 로 세로로 긴
 * 모양이 돼 가로로 넓은 패널과 어긋난다. 위→아래면 리프가 가로로 늘어서 넓어지는데,
 * 카메라가 tip 을 따라 가로로만 움직이므로 그 넓이는 문제가 되지 않는다.
 */

/** 레이아웃 상수. 노드 폭은 이름 28자 + 상태 뱃지가 들어가는 최소치다. */
const LEAF_STEP = 216; // 리프 한 칸의 **가로** 간격
const DEPTH_STEP = 100; // 깊이 한 칸의 **세로** 간격
const NODE_W = 196;
const NODE_H = 42;  // 두 줄 — 이름 + 성격 태그
const PAD = 40; // 카메라가 잡을 때 남기는 여백

const LABEL_MAX = 28; // 이름 최대 77자 — 넘치면 자르고 hover 로 전체를 보여준다

type Placed = {
  id: string;
  name: string;
  /** 노드 성격(Sequence / Selector / Parallel/… / leaf 클래스명). 없으면 표시 안 함. */
  kind: string;
  status: BtNodeStatus;
  flag: BtNodeFlag | null;
  x: number;
  y: number;
  depth: number;
  parent: Placed | null;
};

/**
 * 색은 **세 축**을 나타낸다. 섞이지 않게 축마다 다른 시각 채널을 쓴다.
 *
 *   ① 런타임 상태 (스냅샷)  → 채움색
 *   ② 배선 상태 (정적 감사)  → 빨강 + 굵은 테두리. 상태와 **무관하게** 덮어쓴다
 *   ③ 도달 가능성 (정적)     → 투명도 + 점선 테두리
 *
 * ⚠️ 빨강은 ②번 전용이다. 예전엔 FAILURE 가 빨강이었는데, py_trees 에서 조건노드의
 *    FAILURE 는 오류가 아니라 "그 조건이 아님"이라 정상 순찰 중에도 15개가 뜬다.
 *    그걸 빨갛게 두면 "미배선"과 뜻이 겹쳐 화면을 읽을 수 없다. FAILURE 는 회색으로 내렸다.
 */
const FILL: Record<BtNodeStatus, string> = {
  RUNNING: "#2563eb",
  SUCCESS: "#059669",
  FAILURE: "#334155", // 정상적인 "아니다" — 중립 회색
  INVALID: "#1e293b",
};
const STROKE: Record<BtNodeStatus, string> = {
  RUNNING: "#93c5fd",
  SUCCESS: "#6ee7b7",
  FAILURE: "#64748b",
  INVALID: "#334155",
};
const OPACITY: Record<BtNodeStatus, number> = {
  RUNNING: 1,
  SUCCESS: 0.85,
  FAILURE: 0.55, // 실행은 됐다 — 안 돈 노드(INVALID)보다는 진하게
  INVALID: 0.24,
};

/** 정적 감사 결과. 스냅샷에는 없는 정보라 밖에서 넣어 준다. */
export type BtNodeFlag = "unwired" | "partial" | "unreachable";

const FLAG_FILL: Record<"unwired" | "partial", string> = {
  unwired: "#b91c1c", // 빨강 — 이 배포에서 실제로 동작하지 않는다
  partial: "#b45309", // 주황 — 일부만 동작한다
};
const FLAG_STROKE: Record<"unwired" | "partial", string> = {
  unwired: "#fca5a5",
  partial: "#fcd34d",
};
/** 도달 불가는 "안 돈 것"보다 더 흐리게 — 이번 tick 만의 문제가 아니라는 뜻이다. */
const UNREACHABLE_OPACITY = 0.12;

/**
 * 성격 태그 색. 제어 흐름의 **종류**를 색으로 구분한다 — 이름만 봐서는
 * Sequence 인지 Selector 인지 알 수 없는데 둘은 뜻이 정반대다.
 *   Sequence  왼쪽부터 전부 통과해야 성공 (하나라도 실패하면 실패)
 *   Selector  왼쪽부터 묻다가 하나 통과하면 거기서 멈춤
 *   Parallel  자식을 동시에 — 뒤의 정책이 성공 조건
 * leaf(조건·행동)는 중립색으로 둔다. 눈에 띄어야 하는 건 제어노드다.
 */
const KIND_COLOR = (kind: string): string =>
  kind.startsWith("Sequence")
    ? "#a5b4fc" // 인디고 — 순서대로
    : kind.startsWith("Selector")
      ? "#fdba74" // 주황 — 골라내기
      : kind.startsWith("Parallel")
        ? "#5eead4" // 청록 — 동시에
        : "#64748b"; // leaf

/**
 * 위→아래 tidy 트리 배치(Groot2·BT.CPP 와 같은 방향). 리프를 순서대로 **가로**에 늘어놓고,
 * 부모는 자식들의 한가운데에 놓는다. 깊이는 **세로**로 내려간다.
 *
 * 좌→우로 놓으면 실측 트리(깊이 6 · 리프 51)가 1392×1734 로 **세로로 긴** 모양이 된다.
 * 위→아래면 11000×530 이라 가로로 넓어져 화면 비율과 맞는다.
 *
 * 노드 식별자는 **경로**(부모 인덱스를 이어붙인 것)다. 이름은 형제 간에도 겹칠 수 있어서
 * 이름으로 키를 잡으면 React 가 서로 다른 노드를 같은 것으로 보고 애니메이션이 튄다.
 */
function layout(
  root: FsmTreeNode,
  flags: Record<string, BtNodeFlag>,
): Placed[] {
  const out: Placed[] = [];
  let col = 0;

  const place = (
    node: FsmTreeNode,
    depth: number,
    id: string,
    parent: Placed | null,
  ): Placed => {
    const kids = node.children ?? [];
    // 도달 불가는 서브트리 전체에 물려준다 — 못 들어가는 가지 안쪽도 못 돈다.
    // 미배선(unwired)은 그 노드만의 사실이라 안 물려준다.
    const inherited = parent?.flag === "unreachable" ? "unreachable" : null;
    const self: Placed = {
      id,
      name: node.name,
      kind: node.kind ?? "",
      status: node.status,
      flag: flags[node.name] ?? inherited,
      x: 0,
      y: depth * DEPTH_STEP,
      depth,
      parent,
    };
    out.push(self);

    if (kids.length === 0) {
      self.x = col * LEAF_STEP;
      col += 1;
    } else {
      const placed = kids.map((c, i) => place(c, depth + 1, `${id}.${i}`, self));
      // ⚠️ 자식 **전체**의 한가운데에 놓으면(교과서 tidy 배치) 리프 51개짜리 이 트리에서는
      //    조상 체인이 옆으로 수천 px 벌어져 화면이 텅 빈다(실측). 첫 자식 위에 붙여
      //    세로 체인을 유지하고, 자식이 둘 이상이면 두 번째 자식까지만 반영해 살짝 중앙에
      //    가깝게 둔다 — 부모가 자식 무리 왼쪽 끝에만 붙어 보이는 것도 어색하다.
      const span = Math.min(placed.length, 2);
      self.x =
        placed.slice(0, span).reduce((s, p) => s + p.x, 0) / span;
    }
    return self;
  };

  place(root, 0, "0", null);
  return out;
}

type Box = { x: number; y: number; w: number; h: number };

/**
 * 활성 경로를 **읽히는 배율로** 잡는다.
 *
 * 활성 노드 전체를 담으면 안 된다 — 부모 합성노드는 자식 **전부**의 한가운데에 놓이므로,
 * 리프 51개짜리 트리에서 RUNNING 노드들의 bbox 는 트리 전체와 거의 같아진다(실측 1452×1550
 * vs 전체 1377×1731). 그러면 다시 글씨가 안 보이는 축소 화면이 된다.
 *
 * BT 에서 지금 실제로 일이 일어나는 곳은 **tip**(가장 깊은 RUNNING 노드)이다. 그 주변을
 * 고정 크기 창으로 잡으면 배율이 유지되고, tip 이 옮겨갈 때 카메라가 따라가면서 움직인다.
 */
function focusOn(nodes: Placed[], active: Placed[], full: Box): Box {
  if (active.length === 0) return full;
  // 같은 깊이가 여럿이면 마지막(오른쪽). 리프 순서상 가장 최근에 진입한 가지다.
  const tip = active.reduce((a, b) => (b.depth >= a.depth ? b : a));

  // 창 크기는 노드 5칸 폭 × 전 깊이. 위→아래 배치라 세로는 트리 전체가 6단뿐이어서
  // 통째로 담아도 된다 — 그래야 뿌리부터 tip 까지 경로가 한 화면에 보인다.
  const w = Math.min(LEAF_STEP * 5, full.w);
  const h = full.h;

  // 가로만 tip 을 따라간다. tip 이 화면 가운데 오도록.
  let x = tip.x + NODE_W / 2 - w / 2;
  x = Math.max(full.x, Math.min(x, full.x + full.w - w));
  return { x, y: full.y, w, h };
}

function boundsOf(nodes: Placed[]): Box {
  if (nodes.length === 0) return { x: 0, y: 0, w: 100, h: 100 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of nodes) {
    x0 = Math.min(x0, n.x);
    y0 = Math.min(y0, n.y - NODE_H / 2);
    x1 = Math.max(x1, n.x + NODE_W);
    y1 = Math.max(y1, n.y + NODE_H / 2);
  }
  return { x: x0 - PAD, y: y0 - PAD, w: x1 - x0 + PAD * 2, h: y1 - y0 + PAD * 2 };
}

/**
 * 목표 박스로 viewBox 를 부드럽게 옮긴다.
 *
 * viewBox 는 CSS transition 이 안 먹는 속성이라 프레임마다 직접 보간한다. 지수 감쇠라
 * 목표가 도중에 바뀌어도 튀지 않고 이어진다 — 상태 전이가 연달아 일어날 때 중요하다.
 *
 * ⚠️ setState 가 아니라 **DOM 속성을 직접 쓴다.** 60fps 로 state 를 갱신하면 노드 75개가
 *    매 프레임 리렌더돼 그게 곧 버벅임이 된다. 카메라는 React 트리와 무관한 값이다.
 */
function useCamera(svgRef: React.RefObject<SVGSVGElement | null>, target: Box) {
  const targetRef = useRef(target);
  targetRef.current = target;
  const viewRef = useRef<Box | null>(null);
  //: 사용자가 휠·드래그로 직접 잡은 화면. 있으면 자동 추적보다 우선한다 —
  //  안 그러면 확대하는 순간 카메라가 도로 끌고 가서 조작이 안 된다.
  const manualRef = useRef<Box | null>(null);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const svg = svgRef.current;
      const to = manualRef.current ?? targetRef.current;
      if (!svg) return;

      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      // 첫 프레임은 보간 없이 목표에 놓는다 — 엉뚱한 초기값에서 날아오지 않게.
      const cur = viewRef.current ?? to;
      // 감쇠 계수: 프레임률과 무관하게 같은 속도로 남은 거리를 좁힌다.
      const k = viewRef.current ? 1 - Math.exp(-6 * dt) : 1;
      const next: Box = {
        x: cur.x + (to.x - cur.x) * k,
        y: cur.y + (to.y - cur.y) * k,
        w: cur.w + (to.w - cur.w) * k,
        h: cur.h + (to.h - cur.h) * k,
      };
      viewRef.current = next;
      svg.setAttribute(
        "viewBox",
        `${next.x.toFixed(1)} ${next.y.toFixed(1)} ${next.w.toFixed(1)} ${next.h.toFixed(1)}`,
      );
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [svgRef]);

  return {
    /** 현재 화면. 휠·드래그 계산의 기준이다. */
    current: () => manualRef.current ?? viewRef.current ?? targetRef.current,
    /** 사용자가 잡은 화면으로 **즉시** 이동한다(보간 없음 — 조작은 지연되면 안 된다). */
    setManual: (b: Box) => {
      manualRef.current = b;
      viewRef.current = b;
    },
    /** 자동 추적으로 돌려준다. */
    clearManual: () => {
      manualRef.current = null;
    },
    isManual: () => manualRef.current !== null,
  };
}

function truncate(name: string): string {
  return name.length > LABEL_MAX ? `${name.slice(0, LABEL_MAX - 1)}…` : name;
}

/** 휠 한 칸당 배율. 1.0015^deltaY 라 트랙패드의 작은 델타에도 부드럽다. */
const ZOOM_PER_DELTA = 1.0015;
const MIN_W = LEAF_STEP * 1.5; // 더 확대하면 노드 두어 개만 남아 맥락이 사라진다
const MAX_W_FACTOR = 1.6; // 트리 전체보다 이만큼까지만 축소

export function BtGraphView({
  tree,
  follow,
  flags,
  frozen = false,
  onManualControl,
  className,
}: {
  tree: FsmTreeNode | null;
  /** 트리 수신이 끊긴 상태. 애니메이션을 멈추고 화면을 죽여 "살아 보이는" 걸 막는다. */
  frozen?: boolean;
  /** true 면 카메라가 활성 경로를 따라간다. false 면 트리 전체를 잡는다. */
  follow: boolean;
  /** 노드 이름 → 정적 감사 결과. 스냅샷에 없는 정보다. */
  flags: Record<string, BtNodeFlag>;
  /** 사용자가 휠·드래그로 화면을 직접 잡았을 때. 호출자가 자동 추적을 꺼 준다. */
  onManualControl?: () => void;
  className?: string;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const nodes = useMemo(
    () => (tree ? layout(tree, flags) : []),
    [tree, flags],
  );

  const full = useMemo(() => boundsOf(nodes), [nodes]);
  const active = useMemo(
    () => nodes.filter((n) => n.status === "RUNNING"),
    [nodes],
  );
  // 활성 경로가 비면(부팅 직후, 링크 끊김) 전체를 잡는다 — 빈 화면보다 낫다.
  const target = useMemo(
    () => (follow ? focusOn(nodes, active, full) : full),
    [follow, nodes, active, full],
  );
  const cam = useCamera(svgRef, target);

  // "따라가기"를 다시 켜면 사용자가 잡아둔 화면을 놓아준다.
  useEffect(() => {
    if (follow) cam.clearManual();
  }, [follow, cam]);

  // 화면 좌표 → SVG 좌표. 확대 기준점을 커서에 고정하려면 이게 필요하다.
  const toSvg = (clientX: number, clientY: number, v: Box) => {
    const svg = svgRef.current;
    if (!svg) return { x: v.x, y: v.y };
    const r = svg.getBoundingClientRect();
    // preserveAspectRatio="meet" 은 짧은 축에 여백을 넣는다. 그 여백을 빼야 좌표가 맞는다.
    const s = Math.min(r.width / v.w, r.height / v.h);
    const offX = (r.width - v.w * s) / 2;
    const offY = (r.height - v.h * s) / 2;
    return {
      x: v.x + (clientX - r.left - offX) / s,
      y: v.y + (clientY - r.top - offY) / s,
    };
  };

  // 휠 확대 — 커서 밑 지점을 고정한 채 배율만 바꾼다.
  //
  // ⚠️ React 의 onWheel 은 passive 라 preventDefault 가 안 먹어서 페이지가 같이 스크롤된다.
  //    { passive: false } 로 직접 붙여야 한다.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = cam.current();
      const p = toSvg(e.clientX, e.clientY, v);
      const factor = Math.pow(ZOOM_PER_DELTA, e.deltaY);
      const w = Math.min(
        Math.max(v.w * factor, MIN_W),
        Math.max(full.w, full.h) * MAX_W_FACTOR,
      );
      const scale = w / v.w;
      cam.setManual({
        x: p.x - (p.x - v.x) * scale,
        y: p.y - (p.y - v.y) * scale,
        w,
        h: v.h * scale,
      });
      onManualControl?.();
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [cam, full, onManualControl]);

  // 이동 — 휠 클릭(가운데 버튼) 드래그. 왼쪽 버튼은 노드 툴팁·텍스트 선택에 남겨 둔다.
  const panRef = useRef<{ id: number; box: Box; sx: number; sy: number } | null>(
    null,
  );
  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 1) return;
    e.preventDefault();
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
    panRef.current = {
      id: e.pointerId,
      box: cam.current(),
      sx: e.clientX,
      sy: e.clientY,
    };
    onManualControl?.();
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const p = panRef.current;
    if (!p || p.id !== e.pointerId) return;
    const r = e.currentTarget.getBoundingClientRect();
    const s = Math.min(r.width / p.box.w, r.height / p.box.h);
    cam.setManual({
      ...p.box,
      x: p.box.x - (e.clientX - p.sx) / s,
      y: p.box.y - (e.clientY - p.sy) / s,
    });
  };
  const endPan = (e: React.PointerEvent<SVGSVGElement>) => {
    if (panRef.current?.id === e.pointerId) panRef.current = null;
  };

  if (!tree) {
    // 빈 상태는 "없음"만 말하면 쓸모가 없다. 이 화면이 켜지려면 무엇이 필요한지 적는다.
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-3 rounded-lg bg-slate-950 px-6 text-center",
          className,
        )}
      >
        {/* 트리 모양의 자리 표시자. 스피너 대신 최종 레이아웃의 형태를 흉내낸다. */}
        <svg
          viewBox="0 0 220 90"
          className="h-20 w-56 animate-pulse text-slate-800"
          aria-hidden
        >
          <g fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M56 45 C 80 45, 80 20, 104 20" />
            <path d="M56 45 C 80 45, 80 70, 104 70" />
            <path d="M156 20 C 176 20, 176 20, 196 20" />
          </g>
          <g fill="currentColor">
            <rect x="8" y="37" width="48" height="16" rx="4" />
            <rect x="104" y="12" width="52" height="16" rx="4" />
            <rect x="104" y="62" width="52" height="16" rx="4" />
            <rect x="172" y="12" width="40" height="16" rx="4" />
          </g>
        </svg>
        <p className="text-sm text-slate-300">
          이 로봇의 BT 스냅샷이 아직 오지 않았습니다.
        </p>
        <p className="max-w-md text-xs leading-relaxed text-slate-500">
          로봇에서 <code className="text-slate-400">libi_modes</code> 가 돌고
          있는지, 그리고 해당 로봇 도메인의 domain_bridge 가{" "}
          <code className="text-slate-400">/libi/bt_snapshot</code> 을 서버
          도메인으로 넘기고 있는지 확인하세요.
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-lg bg-slate-950",
        // 끊기면 채도를 죽인다. 색만 그대로 두면 정지 화면이 여전히 살아 보인다.
        frozen && "grayscale-[0.7]",
        className,
      )}
    >
      {frozen && (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 bg-amber-500/90 px-3 py-1 text-center text-[11px] font-semibold text-amber-950">
          트리 수신이 끊겼습니다 · 아래는 마지막으로 받은 화면이며 현재 상태가 아닙니다
        </div>
      )}
      <svg
        ref={svgRef}
        preserveAspectRatio="xMidYMid meet"
        className={cn("h-full w-full touch-none", frozen && "bt-frozen")}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        // 가운데 버튼은 브라우저 기본이 "자동 스크롤"이라 눌린 채 드래그가 안 된다.
        onAuxClick={(e) => e.button === 1 && e.preventDefault()}
      >
        <defs>
          <style>{`
            @keyframes bt-flow { to { stroke-dashoffset: -24; } }
            @keyframes bt-glow {
              0%, 100% { opacity: .35; }
              50%      { opacity: .9; }
            }
            .bt-edge-live {
              stroke-dasharray: 6 6;
              animation: bt-flow .7s linear infinite;
            }
            .bt-halo { animation: bt-glow 1.4s ease-in-out infinite; }
            .bt-box { transition: fill .35s ease, stroke .35s ease, opacity .35s ease; }
            .bt-label { transition: opacity .35s ease; }
            @media (prefers-reduced-motion: reduce) {
              .bt-edge-live, .bt-halo { animation: none; }
            }
            /* 수신이 끊기면 움직임을 멈춘다 — 흐르는 점선과 맥박이 남아 있으면
               정지 화면인데도 계속 돌고 있는 것으로 읽힌다. */
            .bt-frozen .bt-edge-live, .bt-frozen .bt-halo { animation: none; }
          `}</style>
        </defs>

        {/* 간선을 먼저 깔아 노드 상자가 위에 오게 한다. */}
        <g fill="none">
          {nodes.map((n) => {
            if (!n.parent) return null;
            const p = n.parent;
            // 위→아래 배치이므로 부모의 아래 모서리 가운데에서 자식의 위 모서리 가운데로.
            const x0 = p.x + NODE_W / 2;
            const y0 = p.y + NODE_H / 2;
            const x1 = n.x + NODE_W / 2;
            const y1 = n.y - NODE_H / 2;
            const mid = (y0 + y1) / 2;
            const live = n.status === "RUNNING" && p.status === "RUNNING";
            return (
              <path
                key={`e-${n.id}`}
                d={`M ${x0} ${y0} C ${x0} ${mid}, ${x1} ${mid}, ${x1} ${y1}`}
                stroke={live ? "#60a5fa" : "#1e293b"}
                strokeWidth={live ? 2.2 : 1.2}
                opacity={live ? 0.95 : 0.5}
                className={live ? "bt-edge-live" : undefined}
              />
            );
          })}
        </g>

        <g>
          {nodes.map((n) => {
            const live = n.status === "RUNNING";
            const impl =
              n.flag === "unwired" || n.flag === "partial" ? n.flag : null;
            const dead = n.flag === "unreachable";
            // 배선 문제는 런타임 상태를 덮어쓴다 — "지금 돌고 있다"보다 "이건 못 불린다"가
            // 먼저 눈에 들어와야 한다.
            const fill = impl
              ? FLAG_FILL[impl]
              : (FILL[n.status] ?? FILL.INVALID);
            const stroke = impl
              ? FLAG_STROKE[impl]
              : (STROKE[n.status] ?? STROKE.INVALID);
            const opacity = dead
              ? UNREACHABLE_OPACITY
              : impl
                ? Math.max(0.8, OPACITY[n.status] ?? 0)
                : (OPACITY[n.status] ?? OPACITY.INVALID);
            return (
              <g key={n.id} transform={`translate(${n.x} ${n.y - NODE_H / 2})`}>
                {live && !dead && (
                  <rect
                    className="bt-halo"
                    x={-5}
                    y={-5}
                    width={NODE_W + 10}
                    height={NODE_H + 10}
                    rx={11}
                    fill={impl ? FLAG_STROKE[impl] : "#3b82f6"}
                  />
                )}
                <rect
                  className="bt-box"
                  width={NODE_W}
                  height={NODE_H}
                  rx={7}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={impl ? 2 : live ? 1.6 : 1}
                  // 도달 불가는 점선 — 흐린 것만으로는 "이번 tick 만 안 돎"과 구분이 안 된다.
                  strokeDasharray={dead ? "4 3" : undefined}
                  opacity={opacity}
                />
                <text
                  className="bt-label"
                  x={10}
                  y={17}
                  fontSize={12}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  fill={live || impl ? "#f8fafc" : "#cbd5e1"}
                  opacity={dead ? UNREACHABLE_OPACITY * 2.5 : Math.max(0.5, opacity)}
                >
                  {truncate(n.name)}
                  {/* 이름이 최대 77자라 잘린다. 전체는 hover 로 본다. */}
                  <title>
                    {`${n.name}${n.kind ? `  ·  ${n.kind}` : ""}  ·  ${n.status}` +
                      (impl === "unwired" ? "  ·  미배선 — 로직은 있으나 이 트리에서 못 부른다" : "") +
                      (impl === "partial" ? "  ·  부분 구현" : "") +
                      (dead ? "  ·  도달 불가" : "")}
                  </title>
                </text>
                {/* 둘째 줄 = 노드 **성격**. 이름만으로는 Sequence 인지 Selector 인지
                    알 수 없는데 그 둘은 뜻이 정반대라(전부 통과 vs 하나만 통과)
                    화면에서 반드시 구분돼야 한다. */}
                {n.kind && (
                  <text
                    className="bt-label"
                    x={10}
                    y={33}
                    fontSize={9.5}
                    fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                    fill={KIND_COLOR(n.kind)}
                    opacity={dead ? UNREACHABLE_OPACITY * 2.5 : Math.max(0.45, opacity)}
                  >
                    {n.kind}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
