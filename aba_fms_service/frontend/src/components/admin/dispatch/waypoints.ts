/**
 * 주문에 쓰는 waypoint 이름 목록.
 *
 * 원천은 로봇의 `pinky_navigation/params/waypoint.yaml`(arte2 맵, 정점 22개)이다.
 * 이름 문자열이 그대로 orchestrator 의 pickup/dropoff 로 나간다 —
 * `OrderRequest.pickup/dropoff` 가 str 이므로 변환 없이 실린다.
 *
 * ⚠️ 왜 API 가 아니라 여기 상수인가
 *   - `/api/control/waypoints` 는 로봇과 ROS 링크가 살아 있어야 응답한다(없으면 503).
 *   - `/api/control/locations`(DB `rc_robot_locations`)는 현재 0행이다.
 *   관제가 로봇 없이도 주문을 만들 수 있어야 하므로 정적 목록을 쓴다.
 *   나중에 DB/설정으로 옮기면 이 파일만 교체하면 된다.
 *
 * ⚠️ waypoint.yaml 을 고치면 여기도 같이 고친다. 예전 목록(`문학-1`, `테이블-1번-상`,
 *   `입구`, `유아` …)은 맵이 바뀐 뒤로 **정점에 없는 이름**이었고, 그대로 주문을 넣으면
 *   orchestrator 가 목적지를 못 풀어 조용히 실패한다. 값은 반드시 yaml 의 키와 글자까지 같아야 한다.
 *
 * 22정점 전부를 노출하지 않는다 — `순회경로-*` 는 경유점, `주차장`·`주차장입구` 는 충전
 * 도크 진입로라 주문 목적지가 될 수 없다. 사람이 고를 수 있는 지점만 남긴다.
 *
 * 서가 이름 3종(`문학서가`·`예술서가`·`과학-인문학서가`)은 도서 DB `cb_books.zone` 값과
 * 글자까지 같다 — 그래서 책을 고르면 출발지가 매핑 없이 그대로 채워진다(`OrderCreate.tsx`).
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

/** 출발지 — 책을 집는 곳. */
export const PICKUP_GROUPS: WaypointGroup[] = [
  {
    group: "서가",
    // cb_books.zone 이 이 셋 중 하나다(과학·인문학은 서가를 공유한다).
    options: [
      { value: "문학서가", label: "문학서가" },
      { value: "예술서가", label: "예술서가" },
      { value: "과학-인문학서가", label: "과학·인문학서가" },
    ],
  },
  {
    group: "수거",
    // 분류함은 [2026-07-30] 도서관에서 없어졌다 — 그 정점 이름은 `미정` 이 됐다.
    options: [{ value: "수거함", label: "수거함" }],
  },
  {
    group: "전시",
    options: [{ value: "미술작품", label: "미술작품" }],
  },
];

/** 목적지 — 책을 전달하는 곳(사람이 있는 자리). */
export const DROPOFF_GROUPS: WaypointGroup[] = [
  {
    group: "테이블",
    options: [
      { value: "1번테이블", label: "1번 테이블" },
      { value: "2번테이블", label: "2번 테이블" },
    ],
  },
  {
    group: "시설",
    // '안네데스크' 는 waypoint.yaml 의 실제 철자다(원본 오타) — 그대로 써야 정점을 찾는다.
    options: [
      { value: "안네데스크", label: "안내데스크" },
      { value: "도서관출입구", label: "도서관 출입구" },
      { value: "화장실", label: "화장실" },
      // 옛 `분류함` 정점은 [2026-07-30] 이름이 `미정` 이 됐다(도서관에서 분류함이 없어졌고,
      // 그 자리가 무엇이 될지는 지도 그림을 다시 그려야 정해진다). 정점은 살아 있지만
      // **주문 목적지로는 안 내놓는다** — 뜻이 없는 곳으로 배달을 보낼 이유가 없다.
    ],
  },
  {
    group: "수거",
    options: [{ value: "수거함", label: "수거함" }],
  },
];

const ALL = [...PICKUP_GROUPS, ...DROPOFF_GROUPS].flatMap((g) => g.options);

/** 정점 이름 → 표시 이름. 모르는 이름이면 그대로 돌려준다. */
export function waypointLabel(value: string): string {
  return ALL.find((o) => o.value === value)?.label ?? value;
}
