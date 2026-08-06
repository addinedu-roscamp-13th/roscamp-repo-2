/**
 * 도서관 내부 지도 — 사서가 그린 안내판 이미지(`public/maps/library-map.png`) 위에
 * **투명한 클릭 영역**을 얹는다.
 *
 * ## 왜 CSS 평면도에서 이미지로 바뀌었나
 *
 * 예전 버전은 `arte3.pgm` 점유격자에서 벽·구역을 뽑아 div 로 그렸다. 실제 지도와
 * 정확히 맞는다는 장점이 있었지만, 회원이 보는 화면으로는 읽기 어려웠다(회전한
 * 좌표계·작은 알약·벽만 있는 평면도). 이 화면의 목적은 **주행 검증이 아니라 안내**라
 * 그림을 정본으로 삼는다.
 *
 * ⚠️ 로봇의 실제 주행 좌표는 여전히 `waypoint.yaml` 이 정본이다. 이 그림은 그것을
 *    사람에게 보여주는 표현일 뿐, 좌표의 출처가 아니다. 각 구역의 `waypoints` 는
 *    `map-waypoints.ts`(= 실제 `waypoint.yaml`)의 정점 이름을 그대로 쓴다 —
 *    지어내면 탭했을 때 나가는 도서 조회·요청이 실제 로봇 목적지와 어긋난다.
 *
 * ## 클릭 영역 좌표는 어디서 왔나
 *
 * 눈대중이 아니라 **이미지에서 픽셀로 측정**했다(1672×941). 각 알약의 채움색 영역을
 * 골라 바운딩 박스를 재고 퍼센트로 환산한 값이다.
 *
 * ⚠️ 그림을 새로 그리면 이 좌표도 다시 재야 한다. 안 하면 **탭이 엉뚱한 곳에 붙는데
 *    화면은 멀쩡해 보인다** — 알약은 그림에 있고 클릭 영역은 투명하기 때문이다.
 *    측정 방법: 배경색(249,241,230)에서 먼 밝은(max>195) 픽셀만 남겨 연결요소를
 *    라벨링하면 알약별 박스가 나온다.
 */
/** `public/` 아래 자산이라 **import 가 아니라 URL** 로 참조한다(Vite 관례). */
const MAP_SRC = "/maps/library-map.png";

/** 그림의 가로:세로 — 1672×941. 박스 좌표가 이 비율을 전제한다. */
const PLAN_ASPECT = "1672 / 941";

/** 그림 위 클릭 영역(%) — left/top/width/height. */
interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface ZoneDef {
  key: string;
  label: string;
  /** 클릭했을 때 보여줄 설명. */
  desc: string;
  /** 서가라면 이 카테고리의 도서가 꽂혀 있다(`cb_books.category`). */
  category?: string;
  /** 실제 waypoint.yaml 정점 이름(들) — 도서 조회·pickup/dropoff 비교에 쓴다. */
  waypoints: string[];
  /** 그림 위 클릭 영역. 한 구역이 그림에 여러 조각으로 있으면 여러 개다(화장실). */
  rects: Rect[];
}

const ZONE_DEFS: ZoneDef[] = [
  {
    key: "wc",
    label: "화장실",
    desc: "화장실입니다. 남녀 각 1칸씩 있어요.",
    waypoints: ["화장실"],
    // 그림에는 남자/여자가 따로 그려져 있지만 정점은 하나다 — 어느 쪽을 눌러도 같은 안내.
    rects: [
      { left: 4.1, top: 7.3, width: 13.9, height: 8.7 },
      { left: 18.7, top: 7.3, width: 14.3, height: 10.0 },
    ],
  },
  {
    key: "exh",
    label: "미술작품",
    desc: "도서관에 전시된 미술작품 구역입니다. 로봇이 순찰하며 지나가요.",
    waypoints: ["미술작품"],
    rects: [{ left: 34.8, top: 3.3, width: 22.3, height: 9.6 }],
  },
  {
    key: "bin",
    label: "수거함",
    desc: "다 본 책이나 반납할 책을 넣어두면 로봇이 거둬 갑니다.",
    waypoints: ["수거함"],
    rects: [{ left: 60.3, top: 10.1, width: 4.6, height: 23.8 }],
  },
  {
    key: "t1",
    label: "1번 테이블",
    desc: "열람 테이블입니다. 「도서 요청」에서 자리로 받기를 고르면 로봇이 여기로 책을 가져다 줘요.",
    waypoints: ["1번테이블"],
    rects: [{ left: 69.6, top: 15.1, width: 10.7, height: 18.1 }],
  },
  {
    key: "t2",
    label: "2번 테이블",
    desc: "열람 테이블입니다. 「도서 요청」에서 자리로 받기를 고르면 로봇이 여기로 책을 가져다 줘요.",
    waypoints: ["2번테이블"],
    rects: [{ left: 82.1, top: 15.1, width: 10.4, height: 18.1 }],
  },
  {
    key: "sci",
    label: "과학 서가",
    desc: "과학·수학·자연 서가입니다.",
    category: "science",
    waypoints: ["과학-인문학서가"],
    rects: [{ left: 28.1, top: 37.0, width: 23.6, height: 9.9 }],
  },
  {
    key: "art",
    label: "예술서가",
    desc: "미술·디자인·음악·사진 서가입니다.",
    category: "art",
    waypoints: ["예술서가"],
    rects: [{ left: 12.1, top: 35.3, width: 5.4, height: 26.6 }],
  },
  {
    key: "lit",
    label: "문학서가",
    desc: "소설·시·고전이 있는 서가입니다.",
    category: "literature",
    waypoints: ["문학서가"],
    rects: [{ left: 12.1, top: 63.1, width: 5.4, height: 25.9 }],
  },
  {
    key: "hum",
    label: "인문학서가",
    desc: "철학·역사·사회 서가입니다.",
    category: "humanities",
    // 과학·인문학은 waypoint.yaml 상 정점이 하나다. 화면에서만 분야를 갈라 보여준다.
    waypoints: ["과학-인문학서가"],
    rects: [{ left: 45.3, top: 48.5, width: 5.4, height: 32.5 }],
  },
  {
    key: "gate",
    label: "출입구",
    desc: "도서관 출입구입니다.",
    waypoints: ["도서관출입구"],
    rects: [{ left: 94.3, top: 41.7, width: 4.8, height: 25.7 }],
  },
  {
    key: "desk",
    label: "안내데스크",
    desc: "대여 신청한 도서를 여기서 사서에게 받아요. 궁금한 점도 안내데스크에서 물어볼 수 있어요.",
    waypoints: ["안네데스크"],
    rects: [{ left: 71.4, top: 88.3, width: 22.3, height: 8.3 }],
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
      className={`relative w-full overflow-hidden rounded-3xl ${className}`}
      style={{ aspectRatio: PLAN_ASPECT }}
    >
      <img
        src={MAP_SRC}
        alt="도서관 내부 지도"
        className="absolute inset-0 size-full object-contain"
        draggable={false}
      />

      {ZONE_DEFS.map((z) => {
        const active =
          activeZone === z.key ||
          (activeZone ? z.waypoints.includes(activeZone) : false);
        return z.rects.map((r, i) => (
          <button
            key={`${z.key}-${i}`}
            onClick={() => onSelect?.(toZoneBox(z))}
            disabled={!onSelect}
            // 그림이 이미 글자를 그리고 있어 버튼은 **투명**하다. 스크린리더와 탭 순서를
            // 위해 이름은 남긴다 — 안 남기면 이 지도가 보조기기에 빈 그림이 된다.
            aria-label={z.label}
            aria-pressed={active}
            style={{
              left: `${r.left}%`,
              top: `${r.top}%`,
              width: `${r.width}%`,
              height: `${r.height}%`,
            }}
            className={`absolute rounded-xl transition ${
              active
                ? "z-10 bg-primary/15 ring-[3px] ring-primary"
                : "ring-0 hover:bg-primary/10"
            } ${onSelect ? "cursor-pointer" : "cursor-default"}`}
          />
        ));
      })}
    </div>
  );
}
