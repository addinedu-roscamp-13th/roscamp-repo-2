import { useMemo } from "react";

import { MAP_IMAGE, WAYPOINTS, type MapWaypoint } from "@/lib/map-waypoints";

/**
 * 도서관 내부 지도 — 구역을 **네모 박스**로 보여준다.
 *
 * ## 왜 점이 아니라 박스인가
 * 정점 하나하나를 점으로 찍으면 "서가 A 가 저기쯤" 같은 공간 감각이 안 온다. 사람은
 * 구역 단위로 기억하므로, 같은 구역의 정점들을 묶어 **경계 상자**로 그린다.
 * 좌표는 `waypoint.yaml`(로봇이 실제로 쓰는 정점)에서 그대로 계산하므로, 박스 위치가
 * 로봇이 가는 위치와 어긋나지 않는다.
 *
 * ## 왜 가로로 돌리나
 * arte3 맵 원본은 세로로 길다(63×108px = 1.26m × 2.16m). 모바일 화면 폭에 세로 맵을
 * 넣으면 아주 작아지므로 **시계방향 90° 회전**해 가로로 눕힌다.
 * 정규화 좌표 변환: (x, y) → (1 − y, x).
 */

/** 구역 정의 — 어떤 정점들이 한 박스로 묶이는지. 순서가 곧 렌더 순서다. */
const ZONE_DEFS: {
  key: string;
  label: string;
  tone: "pink" | "amber" | "sky" | "violet" | "emerald" | "stone";
  /** 클릭했을 때 보여줄 설명. */
  desc: string;
  /** 서가라면 이 카테고리의 도서가 꽂혀 있다(`cb_books.category`). */
  category?: string;
  match: (name: string) => boolean;
}[] = [
  {
    key: "lit",
    label: "문학",
    tone: "pink",
    desc: "소설·시·고전이 있는 서가입니다.",
    category: "literature",
    match: (n) => n.startsWith("문학"),
  },
  {
    key: "art",
    label: "예술",
    tone: "amber",
    desc: "미술·디자인·음악·사진 서가입니다.",
    category: "art",
    match: (n) => n.startsWith("예술"),
  },
  {
    key: "sci",
    label: "과학",
    tone: "sky",
    desc: "과학·수학·자연 서가입니다.",
    category: "science",
    match: (n) => n.startsWith("과학"),
  },
  {
    key: "hum",
    label: "인문학",
    tone: "violet",
    desc: "철학·역사·사회 서가입니다.",
    category: "humanities",
    match: (n) => n.startsWith("인문학"),
  },
  {
    key: "exh",
    label: "미술작품",
    tone: "amber",
    desc: "도서관에 전시된 미술작품 구역입니다.",
    match: (n) => n.startsWith("미술작품"),
  },
  {
    key: "kids",
    label: "유아",
    tone: "emerald",
    desc: "그림책·동화 등 어린이 열람 구역입니다.",
    category: "kids",
    match: (n) => n === "유아",
  },
  {
    key: "t1",
    label: "테이블 1",
    tone: "stone",
    desc: "열람 테이블입니다. 여기로 도서를 배달받을 수 있어요.",
    match: (n) => n.startsWith("테이블-1번"),
  },
  {
    key: "t2",
    label: "테이블 2",
    tone: "stone",
    desc: "열람 테이블입니다. 여기로 도서를 배달받을 수 있어요.",
    match: (n) => n.startsWith("테이블-2번"),
  },
  {
    key: "desk",
    label: "안내데스크",
    tone: "emerald",
    desc: "대여 신청한 도서를 여기서 사서에게 받습니다.",
    match: (n) => n === "안네데스크",
  },
  {
    key: "wc",
    label: "화장실",
    tone: "stone",
    desc: "화장실입니다.",
    match: (n) => n === "화장실",
  },
  {
    key: "gate",
    label: "입구",
    tone: "stone",
    desc: "도서관 출입구입니다.",
    match: (n) => n === "입구",
  },
];

const TONE: Record<string, string> = {
  pink: "bg-rose-100/90 border-rose-300 text-rose-900",
  amber: "bg-amber-100/90 border-amber-300 text-amber-900",
  sky: "bg-sky-100/90 border-sky-400 text-sky-900",
  violet: "bg-violet-100/90 border-violet-300 text-violet-900",
  emerald: "bg-emerald-100/90 border-emerald-300 text-emerald-900",
  stone: "bg-stone-100/90 border-stone-400 text-stone-700",
};

/** 박스가 너무 납작해 글자가 안 들어가는 것을 막는 최소 크기(정규화 비율). */
const MIN_SIZE = 0.09;
/** 정점 하나짜리 구역에 줄 여백. */
const PAD = 0.02;
/** 겹침 해소 시 박스 사이에 남길 최소 간격. */
const GAP = 0.008;

export interface ZoneBox {
  key: string;
  label: string;
  tone: string;
  /** 회전 후 화면 기준 % 값. */
  left: number;
  top: number;
  width: number;
  height: number;
  members: string[];
  /** 서가라면 이 분야 도서가 꽂혀 있다. */
  category?: string;
  desc: string;
}

/**
 * (x, y) → 화면 좌표.
 *
 * 원본은 세로로 길어(63×108) 가로로 눕혀야 하고, 거기서 **180° 더 돌린** 방향이
 * 실제 도서관 배치와 맞는다(사용자 확인). 90° CW + 180° = 90° CCW 이므로 결과는
 * `(x, y) → (y, 1 − x)` 다.
 */
function rotate(x: number, y: number): [number, number] {
  return [y, 1 - x];
}

/**
 * 박스가 서로 겹치지 않게 민다.
 *
 * 정점이 하나뿐인 구역(유아·안내데스크 등)은 글자를 넣으려 최소 크기로 부풀리는데, 그때
 * 이웃 구역을 파고든다. 겹친 쌍마다 **파고든 깊이가 작은 축**으로 양쪽을 절반씩 밀어
 * 떼어놓는다. 좌표를 크게 바꾸지 않으므로 실제 위치감은 유지된다.
 */
function separate(boxes: ZoneBox[]): ZoneBox[] {
  const overlap = (a: ZoneBox, b: ZoneBox) => {
    const dx =
      Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
    const dy =
      Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
    return dx > 0 && dy > 0 ? { dx, dy } : null;
  };

  // 반복해서 밀어낸다 — 한 번 밀면 다른 쌍이 새로 겹칠 수 있다.
  for (let pass = 0; pass < 12; pass++) {
    let moved = false;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        const ov = overlap(a, b);
        if (!ov) continue;
        moved = true;
        const push = (v: number) => v / 2 + GAP * 100;
        if (ov.dx < ov.dy) {
          const [l, r] = a.left < b.left ? [a, b] : [b, a];
          l.left -= push(ov.dx);
          r.left += push(ov.dx);
        } else {
          const [t, d] = a.top < b.top ? [a, b] : [b, a];
          t.top -= push(ov.dy);
          d.top += push(ov.dy);
        }
      }
    }
    if (!moved) break;
  }

  // 화면 밖으로 나간 것은 되돌린다.
  for (const b of boxes) {
    b.left = Math.max(0, Math.min(100 - b.width, b.left));
    b.top = Math.max(0, Math.min(100 - b.height, b.top));
  }
  return boxes;
}

export function buildZones(waypoints: MapWaypoint[] = WAYPOINTS): ZoneBox[] {
  const boxes: ZoneBox[] = [];
  for (const def of ZONE_DEFS) {
    const members = waypoints.filter((w) => def.match(w.name));
    if (members.length === 0) continue;

    const pts = members.map((w) => rotate(w.x, w.y));
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    let x0 = Math.min(...xs);
    let x1 = Math.max(...xs);
    let y0 = Math.min(...ys);
    let y1 = Math.max(...ys);

    // 정점이 하나거나 일렬이면 넓이가 0 이 된다 — 최소 크기를 준다.
    if (x1 - x0 < MIN_SIZE) {
      const c = (x0 + x1) / 2;
      x0 = c - MIN_SIZE / 2;
      x1 = c + MIN_SIZE / 2;
    } else {
      x0 -= PAD;
      x1 += PAD;
    }
    if (y1 - y0 < MIN_SIZE) {
      const c = (y0 + y1) / 2;
      y0 = c - MIN_SIZE / 2;
      y1 = c + MIN_SIZE / 2;
    } else {
      y0 -= PAD;
      y1 += PAD;
    }

    const clamp = (v: number) => Math.max(0, Math.min(1, v));
    boxes.push({
      key: def.key,
      label: def.label,
      tone: TONE[def.tone],
      left: clamp(x0) * 100,
      top: clamp(y0) * 100,
      width: (clamp(x1) - clamp(x0)) * 100,
      height: (clamp(y1) - clamp(y0)) * 100,
      members: members.map((w) => w.name),
      category: def.category,
      desc: def.desc,
    });
  }
  return separate(boxes);
}

export function LibraryMap({
  activeZone,
  onSelect,
  className = "",
}: {
  /** 강조할 구역 key 또는 waypoint 이름. */
  activeZone?: string | null;
  onSelect?: (zone: ZoneBox) => void;
  className?: string;
}) {
  const zones = useMemo(() => buildZones(), []);

  return (
    <div
      className={`relative w-full overflow-hidden rounded-xl bg-paper ring-1 ring-border ${className}`}
      // 원본 63×108 을 90° 돌렸으므로 가로:세로 = 108:63
      style={{ aspectRatio: "108 / 63" }}
    >
      {/* 배경: 실제 arte3 점유격자 지도를 90° 회전해 깔아둔다 */}
      <img
        src={MAP_IMAGE}
        alt=""
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 h-auto w-[58%] -translate-x-1/2 -translate-y-1/2 -rotate-90 opacity-60 [image-rendering:pixelated]"
      />

      {zones.map((z) => {
        const active =
          activeZone === z.key ||
          (activeZone ? z.members.includes(activeZone) : false);
        return (
          <button
            key={z.key}
            onClick={() => onSelect?.(z)}
            disabled={!onSelect}
            style={{
              left: `${z.left}%`,
              top: `${z.top}%`,
              width: `${z.width}%`,
              height: `${z.height}%`,
            }}
            className={`absolute flex items-center justify-center rounded-md border-2 text-[10px] font-bold transition-all ${z.tone} ${
              active ? "z-10 scale-105 shadow-lg ring-2 ring-primary" : ""
            } ${onSelect ? "cursor-pointer" : "cursor-default"}`}
          >
            <span className="px-1 text-center leading-tight">{z.label}</span>
          </button>
        );
      })}
    </div>
  );
}
