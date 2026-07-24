/**
 * 주문에 쓰는 waypoint 이름 목록.
 *
 * 원천은 로봇의 `pinky_navigation/params/waypoint.yaml`(arte2 맵, 정점 41개)이다.
 * 이름 문자열이 그대로 orchestrator 의 pickup/dropoff 로 나간다 —
 * `OrderRequest.pickup/dropoff` 가 str 이므로 변환 없이 실린다.
 *
 * ⚠️ 왜 API 가 아니라 여기 상수인가
 *   - `/api/control/waypoints` 는 로봇과 ROS 링크가 살아 있어야 응답한다(없으면 503).
 *   - `/api/control/locations`(DB `rc_robot_locations`)는 현재 0행이다.
 *   관제가 로봇 없이도 주문을 만들 수 있어야 하므로 정적 목록을 쓴다.
 *   나중에 DB/설정으로 옮기면 이 파일만 교체하면 된다.
 *
 * 41정점 전부를 노출하지 않는다 — `복도-*`, `테이블-복도-*` 는 경유점이라 주문 목적지가
 * 될 수 없다. 사람이 고를 수 있는 지점만 남긴다.
 */

export interface WaypointOption {
  /** waypoint.yaml 의 정점 이름 — 이 문자열이 그대로 주문에 실린다. */
  value: string;
  /** 화면 표시용 이름. */
  label: string;
}

export interface WaypointGroup {
  group: string;
  options: WaypointOption[];
}

/** 출발지 — 책을 집는 곳(서가). */
export const PICKUP_GROUPS: WaypointGroup[] = [
  {
    group: "서가",
    options: [
      { value: "문학-1", label: "문학 1" },
      { value: "문학-2", label: "문학 2" },
      { value: "예술-1", label: "예술 1" },
      { value: "예술-2", label: "예술 2" },
      { value: "과학-1", label: "과학 1" },
      { value: "과학-2", label: "과학 2" },
      { value: "인문학-1", label: "인문학 1" },
      { value: "인문학-2", label: "인문학 2" },
      // 유아는 분야 서가다(문학·과학·예술·인문학과 같은 급). 예전엔 '시설/유아 열람'
      // 으로 잘못 들어가 있어서, 유아 도서의 출발지를 고를 수 없었다.
      { value: "유아", label: "유아" },
    ],
  },
  {
    group: "전시",
    options: [
      { value: "미술작품", label: "미술작품 1" },
      { value: "미술작품-2", label: "미술작품 2" },
      { value: "미술작품-3", label: "미술작품 3" },
      { value: "미술작품-4", label: "미술작품 4" },
    ],
  },
];

/** 목적지 — 책을 전달하는 곳(사람이 있는 자리). */
export const DROPOFF_GROUPS: WaypointGroup[] = [
  {
    group: "테이블",
    options: [
      { value: "테이블-1번-상", label: "테이블 1번 (상)" },
      { value: "테이블-1번-좌", label: "테이블 1번 (좌)" },
      { value: "테이블-1번-우", label: "테이블 1번 (우)" },
      { value: "테이블-2번-하", label: "테이블 2번 (하)" },
      { value: "테이블-2번-좌", label: "테이블 2번 (좌)" },
      { value: "테이블-2번-우", label: "테이블 2번 (우)" },
    ],
  },
  {
    group: "시설",
    // ' 안네데스크' 는 waypoint.yaml 의 실제 철자다(원본 오타) — 그대로 써야 정점을 찾는다.
    options: [
      { value: "안네데스크", label: "안내데스크" },
      { value: "입구", label: "입구" },
    ],
  },
];

const ALL = [...PICKUP_GROUPS, ...DROPOFF_GROUPS].flatMap((g) => g.options);

/** 정점 이름 → 표시 이름. 모르는 이름이면 그대로 돌려준다. */
export function waypointLabel(value: string): string {
  return ALL.find((o) => o.value === value)?.label ?? value;
}
