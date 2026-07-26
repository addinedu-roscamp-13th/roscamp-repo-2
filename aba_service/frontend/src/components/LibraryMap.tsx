import {
  Armchair,
  BookOpen,
  DoorOpen,
  FlaskConical,
  GraduationCap,
  Info,
  Paintbrush,
  Palette,
  ShoppingBasket,
  Toilet,
  type LucideIcon,
} from "lucide-react";

/**
 * 도서관 내부 지도 — 실사 점유격자 대신, 사서·관제팀이 그린 안내판 스타일
 * 일러스트 배치를 그대로 옮긴 고정 스키마 지도다.
 *
 * ## 왜 좌표를 계산하지 않고 고정하나
 * 예전엔 `waypoint.yaml`의 실제 좌표를 회전·정규화해 박스를 계산했다. 정확하지만
 * 실물 점유격자라 회원이 보기엔 "그냥 회색 벽"이었다 — 어디가 무슨 구역인지 아이콘도
 * 색도 없었다. 지금은 사서가 그린 안내판(문·팔레트·시험관 같은 아이콘)을 그대로
 * 배치로 옮겼다. 대신 **정점 이름은 절대 지어내지 않는다** — 아래 각 구역의
 * `waypoints` 는 `map-waypoints.ts`(= 실제 `waypoint.yaml`)에서 그대로 가져온
 * 이름이라, 탭했을 때 나가는 도서 조회·안내 문구가 실제 로봇 목적지와 어긋나지 않는다.
 */

interface ZoneDef {
  key: string;
  label: string;
  /** 지도 위 알약 안에 넣을 짧은 표기 — 없으면 label 그대로 쓴다. 좁은 박스용. */
  pillLabel?: string;
  icon: LucideIcon;
  tone: string;
  /** 가로로 넓은 박스는 "row"(아이콘+글자 한 줄), 세로로 긴 박스는 "col"(아이콘 위·글자 아래). */
  layout: "row" | "col";
  /** 클릭했을 때 보여줄 설명. */
  desc: string;
  /** 서가라면 이 카테고리의 도서가 꽂혀 있다(`cb_books.category`). */
  category?: string;
  /** 실제 waypoint.yaml 정점 이름(들) — 도서 조회·pickup/dropoff 비교에 쓴다. */
  waypoints: string[];
  left: number;
  top: number;
  width: number;
  height: number;
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
    left: 8,
    top: 2,
    width: 19,
    height: 11,
  },
  {
    key: "exh",
    label: "미술작품",
    icon: Palette,
    tone: TONE.amber,
    layout: "row",
    desc: "도서관에 전시된 미술작품 구역입니다. 로봇이 순찰하며 지나가요.",
    waypoints: ["미술작품"],
    left: 33,
    top: 1,
    width: 24,
    height: 13,
  },
  {
    key: "bin",
    label: "수거함",
    icon: ShoppingBasket,
    tone: TONE.orange,
    layout: "col",
    desc: "다 본 책이나 반납할 책을 넣어두면 로봇이 거둬 갑니다.",
    waypoints: ["수거함"],
    left: 58,
    top: 10,
    width: 7,
    height: 26,
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
    left: 67,
    top: 16,
    width: 14,
    height: 19,
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
    left: 84,
    top: 16,
    width: 14,
    height: 19,
  },
  {
    key: "art",
    label: "예술서가",
    icon: Paintbrush,
    tone: TONE.amber,
    layout: "col",
    desc: "미술·디자인·음악·사진 서가입니다.",
    category: "art",
    waypoints: ["예술서가"],
    left: 9,
    top: 35,
    width: 8,
    height: 25,
  },
  {
    key: "lit",
    label: "문학서가",
    icon: BookOpen,
    tone: TONE.pink,
    layout: "col",
    desc: "소설·시·고전이 있는 서가입니다.",
    category: "literature",
    waypoints: ["문학서가"],
    left: 9,
    top: 63,
    width: 8,
    height: 25,
  },
  {
    key: "sci",
    label: "과학 서가",
    icon: FlaskConical,
    tone: TONE.sky,
    layout: "row",
    desc: "과학·수학·자연 서가입니다.",
    category: "science",
    waypoints: ["과학-인문학서가"],
    left: 26,
    top: 36,
    width: 26,
    height: 9,
  },
  {
    key: "hum",
    label: "인문학서가",
    icon: GraduationCap,
    tone: TONE.violet,
    layout: "col",
    desc: "철학·역사·사회 서가입니다.",
    category: "humanities",
    waypoints: ["과학-인문학서가"],
    left: 42,
    top: 47,
    width: 10,
    height: 32,
  },
  {
    key: "gate",
    label: "출입구",
    icon: DoorOpen,
    tone: TONE.red,
    layout: "col",
    desc: "도서관 출입구입니다.",
    waypoints: ["도서관출입구"],
    left: 92,
    top: 40,
    width: 8,
    height: 28,
  },
  {
    key: "desk",
    label: "안내데스크",
    icon: Info,
    tone: TONE.emerald,
    layout: "row",
    desc: "대여 신청한 도서를 여기서 사서에게 받아요. 궁금한 점도 안내데스크에서 물어볼 수 있어요.",
    waypoints: ["안네데스크"],
    left: 68,
    top: 87,
    width: 27,
    height: 11,
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
        aspectRatio: "4 / 3",
        backgroundImage:
          "radial-gradient(circle, rgba(28,58,94,0.14) 1px, transparent 1px)",
        backgroundSize: "22px 22px",
      }}
    >
      {/* 1번/2번 테이블을 하나의 구역으로 묶어 보여주는 점선 테두리 — 그 자체는 탭할 수 없다. */}
      <div
        className="pointer-events-none absolute rounded-2xl border-2 border-dashed border-stone-400/70"
        style={{ left: "66%", top: "13%", width: "32%", height: "24%" }}
      >
        <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 rounded-full bg-[#FBF3E4] px-1.5 text-[9px] font-bold text-stone-500">
          테이블
        </span>
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
              left: `${z.left}%`,
              top: `${z.top}%`,
              width: `${z.width}%`,
              height: `${z.height}%`,
            }}
            className={`absolute flex items-center justify-center overflow-hidden rounded-full border-2 p-1 text-[9px] font-bold leading-tight transition-all ${
              z.layout === "row" ? "flex-row gap-1.5" : "flex-col gap-1"
            } ${z.tone} ${
              active ? "z-10 scale-105 shadow-lg ring-2 ring-primary" : ""
            } ${onSelect ? "cursor-pointer" : "cursor-default"}`}
          >
            <Icon className="size-4 shrink-0" />
            {/* 붙어 쓴 한글 라벨(예: 예술서가)은 word-break 를 강제로 막으면
                줄바꿈 자체가 안 돼 통째로 넘친다 — 기본 CJK 줄바꿈(글자 단위)에 맡긴다. */}
            <span className="text-center">{z.pillLabel ?? z.label}</span>
          </button>
        );
      })}
    </div>
  );
}
