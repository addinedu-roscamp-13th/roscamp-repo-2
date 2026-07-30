// 원본: aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_navigation/params/waypoint.yaml
// 로봇이 실제로 쓰는 정점 이름 그대로다 — 회원 화면·관리자 화면에 다른 이름을 쓰면
// (예: "문학-1") 요청/작업지시가 실제 로봇 목적지와 어긋난다(이 프로젝트에서 이미 두 번
// 겪은 버그 — 테이블 이름, 그리고 이 서가 이름들). 정점을 고치거나 이름을 바꾸면 이
// 파일도 같이 고쳐야 한다.
//
// "안네데스크"는 오타가 아니라 waypoint.yaml 원본 정점 이름 그대로다 — 화면 라벨은
// "안내데스크"로 바르게 보여주고, 실제 통신(FMS pickup/dropoff)에는 이 값을 쓴다.
//
// 순회경로-N(순찰용 경유점)·주차장·주차장입구·분류함은 회원/관리자가 고를 대상이 아니라
// 여기서 뺐다 — 관제(admin) 실시간 위치 지도는 이 목록이 아니라 라이브 navgraph
// (`ops.waypoints()`)를 쓴다(`robots.tsx` 참고).

export type WaypointKind = "shelf" | "table" | "facility";

export interface MapWaypoint {
  /** waypoint.yaml 의 정점 이름 — FMS 주문의 pickup/dropoff, cb_books.zone 으로 그대로 쓰인다. */
  name: string;
  /** 화면에 보여줄 이름(오타 교정 등). */
  label: string;
  kind: WaypointKind;
}

export const WAYPOINTS: MapWaypoint[] = [
  { name: "예술서가", label: "예술서가", kind: "shelf" },
  { name: "문학서가", label: "문학서가", kind: "shelf" },
  { name: "과학-인문학서가", label: "과학·인문학서가", kind: "shelf" },
  { name: "1번테이블", label: "1번 테이블", kind: "table" },
  { name: "2번테이블", label: "2번 테이블", kind: "table" },
  { name: "미술작품", label: "미술작품", kind: "facility" },
  { name: "수거함", label: "수거함", kind: "facility" },
  { name: "화장실", label: "화장실", kind: "facility" },
  { name: "도서관출입구", label: "출입구", kind: "facility" },
  { name: "안네데스크", label: "안내데스크", kind: "facility" },
];

/** 예전엔 복도(경유점)를 걸러내는 필터였다 — 지금 목록엔 애초에 안 들어 있어 그대로 별칭. */
export const NAMED_WAYPOINTS = WAYPOINTS;

/**
 * 로봇이 실제로 주행하는 점유격자 지도(관제 실시간 위치용, `robots.tsx`).
 *
 * ⚠️ `public/map/` 이 아니라 `public/maps/` 다 — 앞의 이름은 빌드 후 `dist/map/` 디렉터리가
 * 되어 SPA 의 `/map` 라우트와 충돌한다(nginx 가 `/map` → 301 `/map/` → 403 을 내준다).
 */
export const MAP_IMAGE = "/maps/arte3.png";
