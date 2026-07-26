// Customer-facing book catalog client. Calls the FastAPI backend under the
// same-origin `/api` prefix (dev: Vite proxy, prod: nginx -> :8010).
// Used by the chatbot to ground "recommend a book" replies in real DB data.
import type { Book } from "./mock-data";

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

export type BookCategory = Book["category"];

// Keyword → category map so we can derive intent from a free-text message.
// Order matters: the first matching category wins.
// ⚠️ 시드(`seed_books.py`)와 백엔드 `books.py` 의 4분야를 모두 덮어야 한다 —
// humanities 가 빠져 있으면 그 분야는 의도 추론에서 영원히 안 잡힌다.
const CATEGORY_KEYWORDS: Array<[BookCategory, RegExp]> = [
  [
    "humanities",
    /(인문|철학|역사|심리|사회|humanities|philosophy|history|psychology|人文|哲学|nhân văn|triết học)/i,
  ],
  [
    "science",
    /(과학|물리|천문|우주|생물|수학|환경|science|physics|astronomy|biology|科学|khoa học)/i,
  ],
  [
    "art",
    /(예술|미술|그림|디자인|사진|음악|art|design|painting|photo|艺术|美术|nghệ thuật|mỹ thuật)/i,
  ],
  [
    "literature",
    /(문학|소설|시집|고전|literature|fiction|novel|poetry|文学|小说|văn học|tiểu thuyết)/i,
  ],
];

const RECOMMEND_INTENT =
  /(추천|추천도서|추천 도서|뭐 ?읽|읽을 ?만|recommend|suggest|推荐|gợi ý|nên đọc)/i;

/** True when the message is asking for a book recommendation. */
export function isRecommendIntent(text: string): boolean {
  return RECOMMEND_INTENT.test(text);
}

/** Best-effort category guess from a free-text message, or null. */
export function detectCategory(text: string): BookCategory | null {
  for (const [cat, re] of CATEGORY_KEYWORDS) {
    if (re.test(text)) return cat;
  }
  return null;
}

export interface RecommendParams {
  category?: BookCategory | null;
  q?: string | null;
  limit?: number;
  inStockOnly?: boolean;
}

/** Fetch catalog books from the DB (search/list). Returns [] on any failure. */
export async function fetchBooks(
  params: {
    category?: BookCategory | null;
    q?: string | null;
    limit?: number;
  } = {},
): Promise<Book[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 100));
  try {
    const res = await fetch(`${API_BASE}/api/books?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return [];
    return (await res.json()) as Book[];
  } catch {
    return [];
  }
}

/**
 * 요청/예약 기능이 쓰는 정규화된 도서.
 *
 * 백엔드는 `id` 를 문자열로, 재고를 `in_stock`(snake) 로 준다. 반면 화면들은 mock 시절의
 * `inStock` 을 쓰고, 요청 API 는 **숫자 id** 를 요구한다. 그 간극을 여기서 한 번만 메운다.
 */
export interface CatalogBook extends Book {
  /** 요청·예약·위시리스트 API 에 넣을 숫자 id. */
  bookId: number;
  /** 훼손·분실로 사서가 대출 불가 처리했는지 (`inStock` 과 별개). */
  unavailable: boolean;
}

interface RawBook extends Omit<Book, "inStock"> {
  in_stock?: boolean;
  inStock?: boolean;
  unavailable?: boolean;
}

/** 한 곳에서만 하는 정규화 — 백엔드는 snake, 화면은 camel 을 쓴다. */
function normalize(rows: RawBook[]): CatalogBook[] {
  return rows.map((b) => ({
    ...(b as unknown as Book),
    bookId: Number(b.id),
    inStock: b.in_stock ?? b.inStock ?? false,
    unavailable: b.unavailable ?? false,
  }));
}

export interface CatalogQuery {
  category?: BookCategory | null;
  q?: string | null;
  /** 서가 정점 이름. 지도 구역 조회가 쓴다. */
  zone?: string[] | null;
  limit?: number;
}

/**
 * 카탈로그 조회 — **실패와 "결과 없음"을 구분해서** 돌려준다.
 *
 * 예전에는 어떤 실패든 `[]` 로 뭉개서, 지도 화면이 422 를 받고도 "등록된 책이
 * 없습니다" 를 띄웠다. 사용자에게 보이는 경로는 이 함수를 써야 한다.
 */
export async function fetchCatalogResult(
  params: CatalogQuery = {},
): Promise<{ ok: boolean; rows: CatalogBook[] }> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  for (const z of params.zone ?? []) qs.append("zone", z);
  // 백엔드 상한은 200 이다. 넘기면 422 가 난다.
  qs.set("limit", String(Math.min(params.limit ?? 100, 200)));
  try {
    const res = await fetch(`${API_BASE}/api/books?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return { ok: false, rows: [] };
    return { ok: true, rows: normalize((await res.json()) as RawBook[]) };
  } catch {
    return { ok: false, rows: [] };
  }
}

/** 실패를 빈 목록으로 삼키는 얇은 껍데기. 실패 표시가 필요 없는 화면만 쓴다. */
export async function fetchCatalog(
  params: CatalogQuery = {},
): Promise<CatalogBook[]> {
  return (await fetchCatalogResult(params)).rows;
}

/** 도서 1권. 없거나 실패하면 null. */
export async function fetchBook(bookId: number): Promise<CatalogBook | null> {
  try {
    const res = await fetch(`${API_BASE}/api/books/${bookId}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return null;
    return normalize([(await res.json()) as RawBook])[0] ?? null;
  } catch {
    return null;
  }
}

/** Fetch recommended books from the DB. Returns [] on any failure. */
export async function fetchRecommendedBooks(
  params: RecommendParams = {},
): Promise<Book[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 4));
  qs.set("in_stock_only", String(params.inStockOnly ?? true));

  try {
    const res = await fetch(
      `${API_BASE}/api/books/recommend?${qs.toString()}`,
      {
        headers: { "ngrok-skip-browser-warning": "true" },
      },
    );
    if (!res.ok) return [];
    return (await res.json()) as Book[];
  } catch {
    return [];
  }
}

/** 대출 횟수 기준 인기 도서. 실패하면 []. */
export async function fetchPopular(
  params: { category?: BookCategory | null; limit?: number } = {},
): Promise<CatalogBook[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  qs.set("limit", String(Math.min(params.limit ?? 10, 50)));
  try {
    const res = await fetch(`${API_BASE}/api/books/popular?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return [];
    return normalize((await res.json()) as RawBook[]);
  } catch {
    return [];
  }
}
