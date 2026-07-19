// Customer-facing book catalog client. Calls the FastAPI backend under the
// same-origin `/api` prefix (dev: Vite proxy, prod: nginx -> :8010).
// Used by the chatbot to ground "recommend a book" replies in real DB data.
import type { Book } from "./mock-data";

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

export type BookCategory = Book["category"];

// Keyword → category map so we can derive intent from a free-text message.
// Order matters: the first matching category wins.
const CATEGORY_KEYWORDS: Array<[BookCategory, RegExp]> = [
  ["science", /(과학|물리|천문|우주|생물|수학|환경|science|physics|astronomy|biology|科学|khoa học)/i],
  ["art", /(예술|미술|그림|디자인|사진|음악|art|design|painting|photo|艺术|美术|nghệ thuật|mỹ thuật)/i],
  ["literature", /(문학|소설|시집|고전|literature|fiction|novel|poetry|文学|小说|văn học|tiểu thuyết)/i],
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
  params: { category?: BookCategory | null; q?: string | null; limit?: number } = {},
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

/** Fetch recommended books from the DB. Returns [] on any failure. */
export async function fetchRecommendedBooks(params: RecommendParams = {}): Promise<Book[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 4));
  qs.set("in_stock_only", String(params.inStockOnly ?? true));

  try {
    const res = await fetch(`${API_BASE}/api/books/recommend?${qs.toString()}`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (!res.ok) return [];
    return (await res.json()) as Book[];
  } catch {
    return [];
  }
}
