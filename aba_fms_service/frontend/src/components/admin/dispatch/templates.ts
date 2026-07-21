/**
 * 주문 템플릿 — 자주 쓰는 (도서·출발지·목적지) 조합을 저장해두고 클릭 한 번으로 주문을 만든다.
 *
 * ⚠️ 저장소는 브라우저 localStorage 다. 백엔드에 템플릿 API 가 아직 없어서 UI 단계에서는
 * 로컬에 둔다 — **이 브라우저에서만 보이고, 다른 관제 PC 와 공유되지 않는다.**
 * 나중에 서버로 옮길 때 바뀌는 곳은 이 파일뿐이도록 읽기/쓰기를 여기로 모아둔다
 * (호출부는 list/save/remove 만 쓴다).
 */

export interface OrderTemplate {
  id: string;
  name: string;
  book: string;
  pickup: string;
  dropoff: string;
  requester: string;
  priority: number;
}

const KEY = "libi.dispatch.templates.v1";

function newId(): string {
  // crypto.randomUUID 는 보안 컨텍스트에서만 보장된다(HTTP 로 뜬 관제 PC 대비 폴백).
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function listTemplates(): OrderTemplate[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // 손상된 항목은 조용히 버린다 — 템플릿 하나 때문에 패널이 죽으면 안 된다.
    return parsed.filter(
      (t): t is OrderTemplate =>
        !!t &&
        typeof t === "object" &&
        typeof (t as OrderTemplate).id === "string" &&
        typeof (t as OrderTemplate).name === "string",
    );
  } catch {
    return [];
  }
}

function write(items: OrderTemplate[]): OrderTemplate[] {
  try {
    localStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    // 저장 실패(용량/프라이빗 모드)는 조용히 넘긴다 — 호출부가 반환값으로 현재 상태를 쓴다.
  }
  return items;
}

/** 새 템플릿 추가. 반환값이 갱신된 전체 목록이다. */
export function saveTemplate(t: Omit<OrderTemplate, "id">): OrderTemplate[] {
  return write([...listTemplates(), { ...t, id: newId() }]);
}

/** 삭제. 반환값이 갱신된 전체 목록이다. */
export function removeTemplate(id: string): OrderTemplate[] {
  return write(listTemplates().filter((t) => t.id !== id));
}
