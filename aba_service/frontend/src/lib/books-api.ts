// Customer-facing book catalog client. Calls the FastAPI backend under the
// same-origin `/api` prefix (dev: Vite proxy, prod: nginx -> :8010).
// Used by the chatbot to ground "recommend a book" replies in real DB data.
import type { Book } from "./mock-data";

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

export type BookCategory = Book["category"];

// Keyword → category map so we can derive intent from a free-text message.
// Order matters: the first matching category wins.
// ⚠️ 시드(`seed_books.py`)와 백엔드 `books.py` 의 5분야를 모두 덮어야 한다 —
// humanities/kids 가 빠져 있으면 그 분야는 의도 추론에서 영원히 안 잡힌다.
const CATEGORY_KEYWORDS: Array<[BookCategory, RegExp]> = [
  [
    "kids",
    /(유아|아동|어린이|그림책|동화|kids|children|picture ?book|幼儿|儿童|thiếu nhi)/i,
  ],
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
}

interface RawBook extends Omit<Book, "inStock"> {
  in_stock?: boolean;
  inStock?: boolean;
}

/** DB 도서를 화면용 모양으로 정규화해서 가져온다. 실패하면 []. */
export async function fetchCatalog(
  params: {
    category?: BookCategory | null;
    q?: string | null;
    limit?: number;
  } = {},
): Promise<CatalogBook[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 100));
  try {
    const res = await fetch(`${API_BASE}/api/books?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return [];
    const rows = (await res.json()) as RawBook[];
    return rows.map((b) => ({
      ...(b as unknown as Book),
      bookId: Number(b.id),
      inStock: b.in_stock ?? b.inStock ?? false,
    }));
  } catch {
    return [];
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
