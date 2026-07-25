// 자동 생성 — 원본: pinky_navigation/map/arte3.yaml + params/waypoint.yaml
// x,y 는 arte3.png 안에서의 정규화 좌표(0~1). ROS 의 y축은 이미지와 반대라 뒤집어 계산했다.
// 지도를 다시 뜨거나 waypoint 를 바꾸면 이 파일을 재생성해야 한다.

export type WaypointKind = "shelf" | "table" | "facility" | "corridor";

export interface MapWaypoint {
  /** waypoint.yaml 의 정점 이름 — FMS 주문의 pickup/dropoff 로 그대로 나간다. */
  name: string;
  label: string;
  kind: WaypointKind;
  x: number;
  y: number;
}

export const MAP_IMAGE = "/map/arte3.png";
/** 이미지 원본 비율(가로/세로) — 컨테이너 aspect-ratio 에 쓴다. */
export const MAP_ASPECT = 63 / 108;

export const WAYPOINTS: MapWaypoint[] = [
  { name: "과학-1", label: "과학-1", kind: "shelf", x: 0.4232, y: 0.3858 },
  { name: "과학-2", label: "과학-2", kind: "shelf", x: 0.4232, y: 0.4376 },
  { name: "문학-1", label: "문학-1", kind: "shelf", x: 0.2244, y: 0.2509 },
  { name: "문학-2", label: "문학-2", kind: "shelf", x: 0.1232, y: 0.237 },
  { name: "미술작품", label: "미술작품", kind: "shelf", x: 0.7586, y: 0.265 },
  {
    name: "미술작품-2",
    label: "미술작품-2",
    kind: "shelf",
    x: 0.7586,
    y: 0.3637,
  },
  {
    name: "미술작품-3",
    label: "미술작품-3",
    kind: "shelf",
    x: 0.7586,
    y: 0.4624,
  },
  {
    name: "미술작품-4",
    label: "미술작품-4",
    kind: "shelf",
    x: 0.7586,
    y: 0.5611,
  },
  { name: "복도-1", label: "복도-1", kind: "corridor", x: 0.6067, y: 0.265 },
  { name: "복도-10", label: "복도-10", kind: "corridor", x: 0.4442, y: 0.5615 },
  { name: "복도-11", label: "복도-11", kind: "corridor", x: 0.6067, y: 0.5615 },
  { name: "복도-12", label: "복도-12", kind: "corridor", x: 0.6067, y: 0.4624 },
  { name: "복도-13", label: "복도-13", kind: "corridor", x: 0.6067, y: 0.3637 },
  { name: "복도-2", label: "복도-2", kind: "corridor", x: 0.5113, y: 0.265 },
  { name: "복도-3", label: "복도-3", kind: "corridor", x: 0.345, y: 0.265 },
  { name: "복도-4", label: "복도-4", kind: "corridor", x: 0.345, y: 0.38 },
  { name: "복도-5", label: "복도-5", kind: "corridor", x: 0.1669, y: 0.265 },
  { name: "복도-6", label: "복도-6", kind: "corridor", x: 0.1669, y: 0.38 },
  { name: "복도-7", label: "복도-7", kind: "corridor", x: 0.1669, y: 0.4904 },
  { name: "복도-8", label: "복도-8", kind: "corridor", x: 0.1669, y: 0.5615 },
  { name: "복도-9", label: "복도-9", kind: "corridor", x: 0.3305, y: 0.5615 },
  {
    name: "안네데스크",
    label: "안내데스크",
    kind: "facility",
    x: 0.7586,
    y: 0.1131,
  },
  { name: "예술-1", label: "예술-1", kind: "shelf", x: 0.429, y: 0.237 },
  { name: "예술-2", label: "예술-2", kind: "shelf", x: 0.3239, y: 0.237 },
  // 유아는 **분야(카테고리) 서가**다 — 문학·과학·예술·인문학과 같은 급.
  // 열람 공간이 아니라서 facility 로 두면 「출발지(서가)」 목록에서 빠져,
  // 유아 도서는 이송 주문을 만들 수 없다(seed_books.py 의 zone 값이 '유아').
  { name: "유아", label: "유아", kind: "shelf", x: 0.12, y: 0.2731 },
  { name: "인문학-1", label: "인문학-1", kind: "shelf", x: 0.4232, y: 0.4376 },
  { name: "인문학-2", label: "인문학-2", kind: "shelf", x: 0.3475, y: 0.4376 },
  { name: "입구", label: "입구", kind: "facility", x: 0.6067, y: 0.1131 },
  { name: "주차장", label: "주차장", kind: "facility", x: 0.1455, y: 0.1131 },
  {
    name: "테이블-1번-상",
    label: "테이블-1번-상",
    kind: "table",
    x: 0.7179,
    y: 0.8175,
  },
  {
    name: "테이블-1번-우",
    label: "테이블-1번-우",
    kind: "table",
    x: 0.558,
    y: 0.9069,
  },
  {
    name: "테이블-1번-좌",
    label: "테이블-1번-좌",
    kind: "table",
    x: 0.558,
    y: 0.7255,
  },
  {
    name: "테이블-2번-우",
    label: "테이블-2번-우",
    kind: "table",
    x: 0.3305,
    y: 0.9069,
  },
  {
    name: "테이블-2번-좌",
    label: "테이블-2번-좌",
    kind: "table",
    x: 0.3305,
    y: 0.7255,
  },
  {
    name: "테이블-2번-하",
    label: "테이블-2번-하",
    kind: "table",
    x: 0.1669,
    y: 0.8175,
  },
  {
    name: "테이블-복도-1",
    label: "테이블-복도-1",
    kind: "corridor",
    x: 0.7179,
    y: 0.7255,
  },
  {
    name: "테이블-복도-2",
    label: "테이블-복도-2",
    kind: "corridor",
    x: 0.4442,
    y: 0.7255,
  },
  {
    name: "테이블-복도-3",
    label: "테이블-복도-3",
    kind: "corridor",
    x: 0.1669,
    y: 0.7255,
  },
  {
    name: "테이블-복도-4",
    label: "테이블-복도-4",
    kind: "corridor",
    x: 0.1669,
    y: 0.9069,
  },
  {
    name: "테이블-복도-5",
    label: "테이블-복도-5",
    kind: "corridor",
    x: 0.7179,
    y: 0.9069,
  },
  { name: "화장실", label: "화장실", kind: "facility", x: 0.1669, y: 0.6435 },
];

/** 통로 연결 — 지도에 경로선을 그릴 때 쓴다. */
export const LANES: [string, string][] = [
  ["안네데스크", "입구"],
  ["입구", "주차장"],
  ["복도-5", "유아"],
  ["미술작품", "복도-1"],
  ["복도-1", "복도-2"],
  ["복도-2", "복도-3"],
  ["과학-1", "복도-4"],
  ["복도-3", "복도-5"],
  ["복도-4", "복도-6"],
  ["복도-8", "복도-9"],
  ["복도-10", "복도-9"],
  ["복도-10", "복도-11"],
  ["미술작품-4", "복도-11"],
  ["미술작품-3", "복도-12"],
  ["미술작품-2", "복도-13"],
  ["예술-1", "예술-2"],
  ["문학-1", "예술-2"],
  ["문학-1", "문학-2"],
  ["문학-1", "복도-3"],
  ["문학-1", "복도-5"],
  ["과학-2", "인문학-2"],
  ["테이블-1번-좌", "테이블-복도-1"],
  ["테이블-1번-우", "테이블-복도-5"],
  ["테이블-2번-좌", "테이블-복도-2"],
  ["테이블-1번-우", "테이블-2번-우"],
  ["테이블-1번-좌", "테이블-복도-2"],
  ["테이블-2번-좌", "테이블-복도-3"],
  ["테이블-2번-우", "테이블-복도-4"],
  ["인문학-1", "인문학-2"],
  ["복도-8", "화장실"],
  ["문학-2", "유아"],
  ["미술작품", "안네데스크"],
  ["미술작품", "미술작품-2"],
  ["미술작품-2", "미술작품-3"],
  ["미술작품-3", "미술작품-4"],
  ["복도-1", "입구"],
  ["복도-3", "복도-4"],
  ["복도-5", "복도-6"],
  ["복도-6", "복도-7"],
  ["복도-7", "복도-8"],
  ["복도-11", "복도-12"],
  ["복도-12", "복도-13"],
  ["복도-1", "복도-13"],
  ["과학-1", "예술-1"],
  ["과학-1", "과학-2"],
  ["과학-1", "인문학-1"],
  ["복도-4", "인문학-2"],
  ["테이블-1번-상", "테이블-복도-1"],
  ["테이블-2번-하", "테이블-복도-3"],
  ["복도-9", "테이블-2번-좌"],
  ["복도-10", "테이블-복도-2"],
  ["테이블-복도-3", "화장실"],
  ["테이블-2번-하", "테이블-복도-4"],
  ["테이블-1번-상", "테이블-복도-5"],
];

/** 복도(경유점)를 뺀, 사람이 고를 만한 지점. */
export const NAMED_WAYPOINTS = WAYPOINTS.filter((w) => w.kind !== "corridor");

export function waypointByName(name: string): MapWaypoint | undefined {
  return WAYPOINTS.find((w) => w.name === name);
}
