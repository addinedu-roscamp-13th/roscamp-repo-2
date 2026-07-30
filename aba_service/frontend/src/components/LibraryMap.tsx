import {
  Armchair,
  BookOpen,
  DoorOpen,
  FlaskConical,
  GraduationCap,
  Info,
  Inbox,
  Paintbrush,
  Palette,
  Toilet,
  type LucideIcon,
} from "lucide-react";

/**
 * 도서관 내부 지도 — 로봇이 실제로 주행하는 `arte3` 점유격자에서 뽑은 평면도.
 *
 * ## 좌표가 어디서 오나
 * 예전 버전은 사서가 그린 안내판 일러스트를 눈대중으로 옮긴 고정 배치였다. 보기엔
 * 좋았지만 실제 지도와 벽·구역 위치가 어긋나 있었다. 지금은 두 정본에서 그대로 뽑는다:
 *
 * - 벽/기둥/테이블: `pinky_navigation/map/arte3.pgm` (P5, 63×108, 0.02 m/px) 의 점유 셀
 * - 구역 앵커: `pinky_navigation/params/waypoint.yaml` 정점 좌표 + `arte3.yaml` origin
 *
 * ## 좌표계
 * 1. 월드(m) → 픽셀: `col = (x − ox)/res`, `row = H − (y − oy)/res`
 *    (`ox, oy = −0.248, −1.958`, `res = 0.02`, `W, H = 63, 108` — arte3.yaml 값)
 * 2. 픽셀 → 내부 평면도 비율: 벽 바깥 여백(unknown)을 잘라낸 외벽 바운딩 박스
 *    `cols 2‥53, rows 4‥105` (52 × 102) 기준 퍼센트
 * 3. 세로로 긴 지도를 카드에 담기 위해 **반시계 90°** 회전 — `(X, Y) → (Y, 100 − X)`.
 *    관제 화면(`admin/robots.tsx`, FMS `WaypointEditor.tsx`)과 같은 회전 규칙이다.
 *    새로 유도하지 말 것 — 구역이 벽 안쪽에 찍히는 사고가 난다.
 *
 * 아래 상수는 전부 위 변환을 거친 **회전 후** 퍼센트다. 지도(pgm)나 origin이 바뀌면
 * 다시 계산해야 한다.
 *
 * ## 이름 규칙
 * 각 구역의 `waypoints` 는 `map-waypoints.ts`(= 실제 `waypoint.yaml`)의 정점 이름을
 * 그대로 쓴다 — 지어내면 탭했을 때 나가는 도서 조회·요청이 실제 로봇 목적지와 어긋난다.
 */

/** 회전 후 평면도의 가로:세로 — 원본 내부 영역 52×102 를 90° 돌린 값. */
const PLAN_ASPECT = "102 / 52";

interface WallRect {
  key: string;
  left: number;
  top: number;
  width: number;
  height: number;
  /** 벽이 아니라 가구(테이블 상판)면 true — 조금 옅게, 둥글게 그린다. */
  furniture?: boolean;
}

/** arte3.pgm 점유 셀 덩어리 — 회전 후 퍼센트. */
const WALLS: WallRect[] = [
  // 외벽 (원본 rows 4·105, cols 2·53 — 회전하면 위아래/좌우가 서로 바뀐다)
  { key: "w-top", left: 0, top: 0, width: 100, height: 1.92 },
  { key: "w-bottom", left: 0, top: 98.08, width: 100, height: 1.92 },
  { key: "w-left", left: 0, top: 0, width: 0.98, height: 100 },
  { key: "w-right", left: 99.02, top: 0, width: 0.98, height: 100 },
  // 로봇 구역(충전·분류)을 가르는 칸막이 — 원본 rows 19‥20, cols 2‥32
  { key: "w-robot-room", left: 14.71, top: 40.38, width: 1.96, height: 59.62 },
  // 과학·인문학 서가가 놓인 ㄴ자 벽 — 원본 rows 36‥53 col 28‥29 + rows 52‥53 cols 16‥30
  { key: "w-shelf-a", left: 31.37, top: 46.15, width: 17.65, height: 3.85 },
  { key: "w-shelf-b", left: 47.06, top: 44.23, width: 1.96, height: 28.85 },
  // 열람 테이블 구역을 가르는 칸막이 — 원본 rows 68‥70, cols 33‥53
  { key: "w-reading", left: 62.75, top: 0, width: 2.94, height: 40.38 },
  // 열람 테이블 상판 — 원본 rows 77‥82 / 93‥98, cols 38‥43
  { key: "f-table1", left: 71.57, top: 19.23, width: 5.88, height: 11.54, furniture: true },
  { key: "f-table2", left: 87.25, top: 19.23, width: 5.88, height: 11.54, furniture: true },
];

interface ZoneDef {
  key: string;
  label: string;
  /** 지도 위 알약에 넣을 짧은 표기 — 없으면 label 그대로. */
  pillLabel?: string;
  icon: LucideIcon;
  tone: string;
  /** 가로로 넓은 알약은 "row"(아이콘+글자 한 줄), 좁은 자리는 "col"(아이콘 위·글자 아래). */
  layout: "row" | "col";
  /** 클릭했을 때 보여줄 설명. */
  desc: string;
  /** 서가라면 이 카테고리의 도서가 꽂혀 있다(`cb_books.category`). */
  category?: string;
  /** 실제 waypoint.yaml 정점 이름(들) — 도서 조회·pickup/dropoff 비교에 쓴다. */
  waypoints: string[];
  /** 정점의 회전 후 위치(%) — 알약의 **중심**이 여기 온다. */
  x: number;
  y: number;
}

const TONE = {
  sky: "bg-sky-100 border-sky-400 text-sky-900",
  amber: "bg-amber-100 border-amber-400 text-amber-900",
  orange: "bg-orange-100 border-orange-400 text-orange-900",
  pink: "bg-rose-100 border-rose-300 text-rose-900",
  violet: "bg-violet-100 border-violet-300 text-violet-900",
  red: "bg-red-100 border-red-300 text-red-900",
  emerald: "bg-emerald-100 border-emerald-300 text-emerald-900",
  stone: "bg-stone-100 border-stone-400 text-stone-700",
};

const ZONE_DEFS: ZoneDef[] = [
  {
    key: "wc",
    label: "화장실",
    icon: Toilet,
    tone: TONE.sky,
    layout: "row",
    desc: "화장실입니다. 남녀 공용으로 각 1칸씩 있어요.",
    waypoints: ["화장실"],
    x: 23.7,
    y: 7.71,
  },
  {
    key: "exh",
    label: "미술작품",
    icon: Palette,
    tone: TONE.amber,
    layout: "row",
    desc: "도서관에 전시된 미술작품 구역입니다. 로봇이 순찰하며 지나가요.",
    waypoints: ["미술작품"],
    x: 39.37,
    y: 7.71,
  },
  {
    key: "bin",
    label: "수거함",
    icon: Inbox,
    tone: TONE.orange,
    layout: "row",
    desc: "다 본 책이나 반납할 책을 넣어두면 로봇이 거둬 갑니다.",
    waypoints: ["수거함"],
    x: 55.05,
    y: 7.71,
  },
  {
    key: "t1",
    label: "1번 테이블",
    pillLabel: "1번",
    icon: Armchair,
    tone: TONE.stone,
    layout: "col",
    desc: "열람 테이블입니다. 「도서 요청」에서 자리로 받기를 고르면 로봇이 여기로 책을 가져다 줘요.",
    waypoints: ["1번테이블"],
    x: 73.87,
    y: 25,
  },
  {
    key: "t2",
    label: "2번 테이블",
    pillLabel: "2번",
    icon: Armchair,
    tone: TONE.stone,
    layout: "col",
    desc: "열람 테이블입니다. 「도서 요청」에서 자리로 받기를 고르면 로봇이 여기로 책을 가져다 줘요.",
    waypoints: ["2번테이블"],
    x: 89.51,
    y: 25,
  },
  {
    key: "art",
    label: "예술서가",
    icon: Paintbrush,
    tone: TONE.amber,
    layout: "row",
    desc: "미술·디자인·음악·사진 서가입니다.",
    category: "art",
    waypoints: ["예술서가"],
    x: 23.7,
    y: 51.15,
  },
  {
    key: "lit",
    label: "문학서가",
    icon: BookOpen,
    tone: TONE.pink,
    layout: "row",
    desc: "소설·시·고전이 있는 서가입니다.",
    category: "literature",
    waypoints: ["문학서가"],
    x: 23.7,
    y: 77.47,
  },
  // 과학·인문학은 waypoint.yaml 상 정점이 하나(과학-인문학서가)다. 회원 화면에서는
  // 분야를 갈라 보여주되, 두 알약 모두 ㄴ자 서가 벽의 각 팔에 붙여 실제 위치를 지킨다.
  {
    key: "sci",
    label: "과학 서가",
    icon: FlaskConical,
    tone: TONE.sky,
    layout: "row",
    desc: "과학·수학·자연 서가입니다.",
    category: "science",
    waypoints: ["과학-인문학서가"],
    // 정점(38.33)에서 살짝 오른쪽 — 서가 벽 가로팔(x 31.4‥49.0) 위에 그대로 얹히면서
    // 바로 왼쪽의 예술서가 알약과 겹치지 않는 위치다.
    x: 41,
    y: 48.08,
  },
  {
    key: "hum",
    label: "인문학서가",
    icon: GraduationCap,
    tone: TONE.violet,
    layout: "row",
    desc: "철학·역사·사회 서가입니다.",
    category: "humanities",
    waypoints: ["과학-인문학서가"],
    x: 48.04,
    y: 62,
  },
  {
    key: "gate",
    label: "출입구",
    icon: DoorOpen,
    tone: TONE.red,
    layout: "row",
    desc: "도서관 출입구입니다.",
    waypoints: ["도서관출입구"],
    x: 89.51,
    y: 51.15,
  },
  {
    key: "desk",
    label: "안내데스크",
    icon: Info,
    tone: TONE.emerald,
    layout: "row",
    desc: "대여 신청한 도서를 여기서 사서에게 받아요. 궁금한 점도 안내데스크에서 물어볼 수 있어요.",
    waypoints: ["안네데스크"],
    x: 89.51,
    y: 77.47,
  },
];

export interface ZoneBox {
  key: string;
  label: string;
  desc: string;
  category?: string;
  /** 이 구역이 대응하는 실제 waypoint.yaml 정점 이름들 — 도서 zone 필터에 그대로 쓴다. */
  members: string[];
}

function toZoneBox(z: ZoneDef): ZoneBox {
  return {
    key: z.key,
    label: z.label,
    desc: z.desc,
    category: z.category,
    members: z.waypoints,
  };
}

/** 지도를 그리지 않고 구역 목록만 필요할 때(예: LiBi 봇의 "화장실 어디야" 도구). */
export function listZones(): ZoneBox[] {
  return ZONE_DEFS.map(toZoneBox);
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
  return (
    <div
      className={`relative w-full overflow-hidden rounded-3xl border-[3px] border-[#1c3a5e] bg-[#FBF3E4] ${className}`}
      style={{
        aspectRatio: PLAN_ASPECT,
        backgroundImage:
          "radial-gradient(circle, rgba(28,58,94,0.14) 1px, transparent 1px)",
        backgroundSize: "22px 22px",
      }}
    >
      {/* arte3.pgm 점유 격자에서 그대로 옮긴 벽·가구 — 배경 구조물이라 탭할 수 없다. */}
      {WALLS.map((w) => (
        <div
          key={w.key}
          aria-hidden
          className={`pointer-events-none absolute ${
            w.furniture
              ? "rounded-[3px] bg-[#1c3a5e]/45"
              : "rounded-[2px] bg-[#1c3a5e]"
          }`}
          style={{
            left: `${w.left}%`,
            top: `${w.top}%`,
            width: `${w.width}%`,
            height: `${w.height}%`,
          }}
        />
      ))}

      {/* 로봇 충전·분류 구역(주차장/주차장입구/분류함) — 회원이 갈 곳이 아니라 탭할 수 없다. */}
      <div
        aria-hidden
        className="pointer-events-none absolute flex items-center justify-center text-center text-[8px] font-bold leading-tight text-stone-500"
        style={{ left: "0.98%", top: "1.92%", width: "13.73%", height: "96.16%" }}
      >
        로봇 충전·분류 구역
      </div>

      {ZONE_DEFS.map((z) => {
        const active =
          activeZone === z.key || (activeZone ? z.waypoints.includes(activeZone) : false);
        const Icon = z.icon;
        return (
          <button
            key={z.key}
            onClick={() => onSelect?.(toZoneBox(z))}
            disabled={!onSelect}
            style={{
              left: `${z.x}%`,
              top: `${z.y}%`,
              // 알약의 **중심**을 정점에 맞춘다. scale 도 여기서 같이 준다 — Tailwind 의
              // scale-* 유틸리티는 transform 을 통째로 덮어써서 이 이동이 사라진다.
              transform: `translate(-50%, -50%) scale(${active ? 1.12 : 1})`,
            }}
            className={`absolute flex items-center justify-center whitespace-nowrap rounded-full border-2 px-1.5 py-0.5 text-[8px] font-bold leading-none shadow-sm transition-transform ${
              z.layout === "row" ? "flex-row gap-1" : "flex-col gap-0.5"
            } ${z.tone} ${
              active ? "z-10 shadow-lg ring-2 ring-primary" : ""
            } ${onSelect ? "cursor-pointer" : "cursor-default"}`}
          >
            <Icon className="size-2.5 shrink-0" />
            <span className="text-center">{z.pillLabel ?? z.label}</span>
          </button>
        );
      })}
    </div>
  );
}
