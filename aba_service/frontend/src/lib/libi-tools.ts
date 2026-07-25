/**
 * LiBi 챗봇 도구 레이어.
 *
 * 모델(Ollama)이 자유 텍스트로 부르는 16개 "도구"를, zod 스키마로 검증하고
 * 실제 `memberApi` 호출로 옮기는 유일한 통로. 챗 UI는 `prepareTool` 만 호출한다 —
 * `runTool` 을 직접 부르지 않는다.
 *
 * 설계 원칙 (codex 지적 반영):
 * 1. 모델이 준 인자는 신뢰하지 않는다 — 도구마다 zod 스키마로 검증하고, 실패하면
 *    실행도 확인 카드도 없이 되묻는다.
 * 2. 확인 카드는 정본을 보여준다 — 모델이 말한 제목이 아니라 해석된 실제 도서
 *    (제목·저자·서가)를 띄운다. 애매하면 확인 카드를 만들지 않고 후보를 고르게 한다.
 * 3. 결과는 접수번호·자리·상태를 포함한다 (R73).
 */
import { z } from "zod";
import { memberApi, getToken, TABLES, APPROVAL_LABEL } from "./member";
import { fetchCatalog, fetchPopular } from "./books-api";
import type { CatalogBook, BookCategory } from "./books-api";
import { bookAvailability, availabilitySentence } from "./book-status";
import { buildZones } from "../components/LibraryMap";

export type ToolName =
  | "search_books"
  | "book_detail"
  | "popular_books"
  | "my_loans"
  | "my_requests"
  | "my_reservations"
  | "my_wishlist"
  | "where_is_zone"
  | "request_read"
  | "request_borrow"
  | "reserve_book"
  | "cancel_reservation"
  | "add_wishlist"
  | "remove_wishlist"
  | "extend_loan"
  | "delete_request";

/** 되돌리기 어려운 도구 — 실행 전에 사용자 확인을 받는다.
 *  모델이 1.7B 라 "예약"과 "대여 신청"을 헷갈릴 수 있다. */
export const NEEDS_CONFIRM: Set<ToolName> = new Set([
  "request_read",
  "request_borrow",
  "reserve_book",
  "cancel_reservation",
  "add_wishlist",
  "remove_wishlist",
  "extend_loan",
  "delete_request",
]);

/** 로그인이 필요한 도구. 비로그인이면 실행하지 않고 안내한다. */
const NEEDS_LOGIN: Set<ToolName> = new Set([
  ...NEEDS_CONFIRM,
  "my_loans",
  "my_requests",
  "my_reservations",
  "my_wishlist",
]);

const CATEGORY = z.enum(["literature", "art", "science", "humanities", "kids"]);
// `TABLES` (member.ts) 가 백엔드 화이트리스트와 같아야 하는 유일한 출처다 — 여기서
// 값을 다시 나열하면 둘이 어긋날 수 있으니 그대로 파생시킨다.
const TABLE = z.enum(TABLES.map((t) => t.value) as [string, ...string[]]);
const TITLE = z.string().trim().min(1);
const ZONE_LABEL = z.string().trim().min(1);

/** 도구 하나의 정의. `schema` 가 곧 검증기이자 문서다. */
type ToolDef = {
  name: ToolName;
  description: string;
  schema: z.ZodObject<z.ZodRawShape>;
  /** 인자 하나하나의 사람말 설명 — JSON 스키마에 그대로 실린다. */
  argDocs: Record<string, string>;
};

const TOOL_DEFS: ToolDef[] = [
  // ── 조회형 ──────────────────────────────────────────────
  {
    name: "search_books",
    description:
      "도서관 장서를 제목·저자·내용으로 검색한다. 사용자가 책을 찾을 때 쓴다.",
    schema: z.object({ query: TITLE, category: CATEGORY.optional() }),
    argDocs: { query: "검색어", category: "분야 (선택)" },
  },
  {
    name: "book_detail",
    description: "특정 책의 줄거리, 서가 위치, 지금 빌릴 수 있는지를 알려준다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "책 제목" },
  },
  {
    name: "popular_books",
    description:
      "대출 횟수 기준 인기 도서를 알려준다. '요즘 뭐가 인기야' 같은 질문에 쓴다.",
    schema: z.object({ category: CATEGORY.optional() }),
    argDocs: { category: "분야 (선택)" },
  },
  {
    name: "my_loans",
    description: "지금 내가 빌린 책과 반납 예정일을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_requests",
    description: "내가 넣은 배달·대여 요청의 진행 상황을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_reservations",
    description: "내가 예약해 둔 책 목록을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "my_wishlist",
    description: "내가 읽고 싶다고 담아 둔 책 목록을 알려준다.",
    schema: z.object({}),
    argDocs: {},
  },
  {
    name: "where_is_zone",
    description:
      "도서관 구역(문학·예술·과학·인문학·유아·테이블·안내데스크·화장실·입구)이 어디인지 알려준다.",
    schema: z.object({ zone_label: ZONE_LABEL }),
    argDocs: { zone_label: "구역 이름 (예: 문학, 과학, 테이블 1)" },
  },
  // ── 변경형 (확인 카드 필요) ─────────────────────────────
  {
    name: "request_read",
    description:
      "책을 회원이 앉은 테이블로 배달 요청한다(열람). 대출이 아니고 사서 승인도 필요 없으며, 로봇이 바로 움직인다.",
    schema: z.object({ book_title: TITLE, table: TABLE }),
    argDocs: { book_title: "요청할 책 제목", table: "받을 자리" },
  },
  {
    name: "request_borrow",
    description:
      "책을 대여 신청한다. 사서 승인 후 로봇이 안내데스크로 가져다 놓고, 대출 확정은 사서가 한다. 관외 반출이라 열람과 다르다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "대여 신청할 책 제목" },
  },
  {
    name: "reserve_book",
    description: "지금 대출 중인 책을 예약한다. 반납되면 알림을 받는다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "예약할 책 제목" },
  },
  {
    name: "cancel_reservation",
    description: "내가 예약한 책의 예약을 취소한다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "예약을 취소할 책 제목" },
  },
  {
    name: "add_wishlist",
    description: "책을 읽고 싶은 목록에 담는다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "담을 책 제목" },
  },
  {
    name: "remove_wishlist",
    description: "읽고 싶은 목록에서 책을 뺀다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "뺄 책 제목" },
  },
  {
    name: "extend_loan",
    description:
      "빌린 책의 반납일을 7일 미룬다. 연장은 대출 1건당 1회만 가능하다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "연장할 책 제목" },
  },
  {
    name: "delete_request",
    description:
      "끝났거나 반려된 내 요청 이력을 지운다. 사서 승인을 기다리는 건은 지울 수 없다.",
    schema: z.object({ book_title: TITLE }),
    argDocs: { book_title: "이력을 지울 요청의 책 제목" },
  },
];

/**
 * zod 스키마를 Ollama 가 읽는 JSON 스키마로 옮기는 변환기.
 * 우리가 쓰는 타입은 문자열·enum·optional 셋뿐이라 짧다.
 */
function toJsonSchema(def: ToolDef) {
  const shape = def.schema.shape;
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const [key, value] of Object.entries(shape)) {
    const optional = value instanceof z.ZodOptional;
    const inner = optional
      ? (value as z.ZodOptional<z.ZodTypeAny>).unwrap()
      : value;
    properties[key] =
      inner instanceof z.ZodEnum
        ? { type: "string", enum: inner.options, description: def.argDocs[key] }
        : { type: "string", description: def.argDocs[key] };
    if (!optional) required.push(key);
  }
  return { type: "object", properties, required };
}

export const LIBI_TOOLS = TOOL_DEFS.map((d) => ({
  type: "function",
  function: {
    name: d.name,
    description: d.description,
    parameters: toJsonSchema(d),
  },
}));

const BY_NAME = new Map(TOOL_DEFS.map((d) => [d.name, d]));

// ── 제목 → 도서 해석 (모호하면 실행하지 않는다) ──────────────

type Resolution =
  | { kind: "one"; book: CatalogBook }
  | { kind: "many"; books: CatalogBook[] }
  | { kind: "none" };

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, "");

/**
 * 제목으로 도서를 특정한다.
 *
 * 1순위: 정규화 완전일치가 **정확히 하나**  → 그 책
 * 그 외: 후보를 돌려주고 사용자가 고르게 한다(모호), 또는 없음
 *
 * 예전 설계의 `rows[0]` 폴백은 `/api/books` 정렬이 `in_stock DESC, id DESC` 라서
 * "아무 재고 있는 책"을 집어 엉뚱한 책을 배달시킬 수 있었다 — 여기서는 절대 그렇게
 * 하지 않는다.
 */
async function resolveBook(title: string): Promise<Resolution> {
  const rows = await fetchCatalog({ q: title, limit: 20 });
  if (rows.length === 0) return { kind: "none" };

  const target = norm(title);
  const exact = rows.filter((b) =>
    Object.values(b.title).some((t) => norm(t) === target),
  );
  if (exact.length === 1) return { kind: "one", book: exact[0] };
  if (exact.length > 1) return { kind: "many", books: exact };
  if (rows.length === 1) return { kind: "one", book: rows[0] };
  return { kind: "many", books: rows.slice(0, 10) };
}

/** 내 목록(대출·예약·위시·요청) 안에서 제목으로 항목을 찾는다. */
function pickMine<T>(rows: T[], title: string, titleOf: (r: T) => string): T[] {
  const target = norm(title);
  const exact = rows.filter((r) => norm(titleOf(r)) === target);
  if (exact.length > 0) return exact;
  return rows.filter((r) => norm(titleOf(r)).includes(target));
}

// ── 준비 단계 — 검증 · 로그인 확인 · 정본 해석 ──────────────

export type ToolResult = { ok: boolean; text: string; books?: CatalogBook[] };

export type PendingCall = {
  name: ToolName;
  args: Record<string, unknown>;
  /** 해석된 **정본** 도서. 확인 카드는 모델이 말한 제목이 아니라 이걸 보여준다. */
  book: CatalogBook | null;
  sentence: string;
};

export type PrepareResult =
  | { kind: "run"; result: ToolResult }
  | { kind: "confirm"; pending: PendingCall }
  | { kind: "choose"; books: CatalogBook[]; text: string }
  | { kind: "error"; text: string };

/**
 * 챗봇은 `runTool` 을 직접 부르지 않는다. **`prepareTool` 한 곳**을 통과시킨다.
 * 여기서 막히면 확인 카드도 안 만든다.
 */
export async function prepareTool(
  name: string,
  rawArgs: unknown,
): Promise<PrepareResult> {
  const def = BY_NAME.get(name as ToolName);
  if (!def) return { kind: "error", text: "제가 할 수 있는 일이 아니에요." };

  // 모델이 준 인자는 신뢰하지 않는다. 빠지거나 형식이 틀리면 되묻는다.
  const parsed = def.schema.safeParse(rawArgs ?? {});
  if (!parsed.success) {
    return {
      kind: "error",
      text: "어떤 책인지(또는 어느 자리인지) 다시 말씀해 주시겠어요?",
    };
  }
  const args = parsed.data as Record<string, unknown>;

  if (NEEDS_LOGIN.has(def.name) && getToken() === null) {
    return {
      kind: "error",
      text: "이 기능은 로그인이 필요해요. 상단의 「로그인」을 눌러 주세요.",
    };
  }

  // 책을 다루는 도구는 **실행 전에** 정본을 확정한다.
  let book: CatalogBook | null = null;
  if (typeof args.book_title === "string") {
    const r = await resolveBook(args.book_title);
    if (r.kind === "none")
      return {
        kind: "error",
        text: `«${args.book_title}» 은(는) 찾지 못했어요.`,
      };
    if (r.kind === "many") {
      return {
        kind: "choose",
        books: r.books,
        text: "어떤 책을 말씀하시는 걸까요? 눌러서 골라 주세요.",
      };
    }
    book = r.book;
  }

  if (!NEEDS_CONFIRM.has(def.name)) {
    return { kind: "run", result: await runTool(def.name, args, book) };
  }
  return {
    kind: "confirm",
    pending: {
      name: def.name,
      args,
      book,
      sentence: describeCall(def.name, args, book),
    },
  };
}

/** 확인 카드에서 「네」를 눌렀을 때. 카드가 인자를 고쳤을 수 있으므로 다시 검증한다. */
export async function runPending(pending: PendingCall): Promise<ToolResult> {
  const def = BY_NAME.get(pending.name);
  if (!def) return { ok: false, text: "알 수 없는 요청이에요." };
  const parsed = def.schema.safeParse(pending.args);
  if (!parsed.success)
    return { ok: false, text: "요청 내용이 올바르지 않아요." };
  return runTool(
    pending.name,
    parsed.data as Record<string, unknown>,
    pending.book,
  );
}

// ── 실행기 — 16개 전부 ───────────────────────────────────────

async function runTool(
  name: ToolName,
  args: Record<string, unknown>,
  book: CatalogBook | null,
): Promise<ToolResult> {
  try {
    switch (name) {
      // ── 조회형 ────────────────────────────────────────
      case "search_books": {
        const books = await fetchCatalog({
          q: String(args.query),
          category: (args.category as BookCategory | undefined) ?? null,
          limit: 10,
        });
        return {
          ok: true,
          books,
          text: books.length
            ? `${books.length}권 찾았어요.`
            : "그런 책은 못 찾았어요.",
        };
      }
      case "book_detail": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const a = bookAvailability(book);
        return {
          ok: true,
          books: [book],
          text: `«${book.title.KR}» — ${book.author}. ${book.summary.KR || "소개가 아직 없어요."} ${availabilitySentence(a, book.zone, book.shelf)}`,
        };
      }
      case "popular_books": {
        const books = await fetchPopular({
          category: (args.category as BookCategory | undefined) ?? null,
          limit: 10,
        });
        return {
          ok: true,
          books,
          text: books.length
            ? "요즘 많이 빌려 가는 책이에요."
            : "아직 대출 기록이 없어요.",
        };
      }
      case "my_loans": {
        const rows = await memberApi.loans();
        const open = rows.filter((l) => l.status !== "returned");
        if (open.length === 0)
          return { ok: true, text: "지금 빌린 책이 없어요." };
        return {
          ok: true,
          text: open
            .map(
              (l) =>
                `«${l.book.title}» — ${l.overdue ? `연체 ${-l.days_left}일` : `반납 D-${l.days_left}`}`,
            )
            .join("\n"),
        };
      }
      case "my_requests": {
        const rows = await memberApi.requests();
        if (rows.length === 0)
          return { ok: true, text: "넣어 둔 요청이 없어요." };
        return {
          ok: true,
          text: rows
            .map(
              (r) =>
                `#${r.id} «${r.book_title}» ${r.kind === "borrow" ? "대여" : "열람"} · ${r.dropoff} · ${r.status ?? APPROVAL_LABEL[r.approval]}`,
            )
            .join("\n"),
        };
      }
      case "my_reservations": {
        const rows = await memberApi.reservations();
        const alive = rows.filter((r) => r.status !== "cancelled");
        if (alive.length === 0)
          return { ok: true, text: "예약해 둔 책이 없어요." };
        return {
          ok: true,
          text: alive.map((r) => `«${r.book.title}» — ${r.status}`).join("\n"),
        };
      }
      case "my_wishlist": {
        const rows = await memberApi.wishlist();
        if (rows.length === 0)
          return { ok: true, text: "담아 둔 책이 없어요." };
        return {
          ok: true,
          text: rows
            .map((w) => `«${w.book.title}» — ${w.book.author}`)
            .join("\n"),
        };
      }
      case "where_is_zone": {
        const label = String(args.zone_label).trim();
        const zone = buildZones().find(
          (z) =>
            norm(z.label) === norm(label) ||
            norm(label).includes(norm(z.label)),
        );
        if (!zone) return { ok: false, text: `«${label}» 구역은 못 찾았어요.` };
        return {
          ok: true,
          text: `${zone.label} — ${zone.desc} (정점: ${zone.members.join(", ")})`,
        };
      }

      // ── 변경형 ────────────────────────────────────────
      case "request_read": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        if (book.unavailable)
          return { ok: false, text: "훼손·분실로 대출이 막힌 도서예요." };
        if (!book.inStock)
          return { ok: false, text: "지금 대출 중이라 예약만 가능해요." };
        const res = await memberApi.requestRead(
          book.bookId,
          String(args.table),
        );
        return {
          ok: true,
          text: `접수 #${res.id} — «${res.book_title}» 을(를) ${res.dropoff} 로 가져다 드릴게요. (${APPROVAL_LABEL[res.approval]})`,
        };
      }
      case "request_borrow": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        if (book.unavailable)
          return { ok: false, text: "훼손·분실로 대출이 막힌 도서예요." };
        if (!book.inStock)
          return { ok: false, text: "지금 대출 중이라 예약만 가능해요." };
        const res = await memberApi.requestBorrow(book.bookId);
        return {
          ok: true,
          text: `접수 #${res.id} — «${res.book_title}» 대여를 신청했어요. 사서 승인 후 ${res.dropoff} 에서 받으실 수 있어요. (${APPROVAL_LABEL[res.approval]})`,
        };
      }
      case "reserve_book": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const res = await memberApi.reserve(book.bookId);
        return {
          ok: true,
          text: `«${res.book.title}» 예약했어요 (${res.status}). 반납되면 알려드릴게요.`,
        };
      }
      case "cancel_reservation": {
        const rows = await memberApi.reservations();
        const hits = pickMine(
          rows.filter((r) => r.status !== "cancelled"),
          String(args.book_title),
          (r) => r.book.title,
        );
        if (hits.length === 0)
          return { ok: false, text: "그 책은 예약 목록에 없어요." };
        if (hits.length > 1)
          return {
            ok: false,
            text: "같은 제목이 여러 건이에요. 「내 정보」에서 취소해 주세요.",
          };
        await memberApi.cancelReservation(hits[0].id);
        return { ok: true, text: `«${hits[0].book.title}» 예약을 취소했어요.` };
      }
      case "add_wishlist": {
        if (!book) return { ok: false, text: "그 책을 찾지 못했어요." };
        const res = await memberApi.addWishlist(book.bookId);
        return {
          ok: true,
          text: `«${res.book.title}» 을(를) 읽고 싶은 목록에 담았어요.`,
        };
      }
      case "remove_wishlist": {
        const rows = await memberApi.wishlist();
        const hits = pickMine(
          rows,
          String(args.book_title),
          (w) => w.book.title,
        );
        if (hits.length === 0)
          return { ok: false, text: "그 책은 목록에 없어요." };
        if (hits.length > 1)
          return {
            ok: false,
            text: "같은 제목이 여러 건이에요. 「내 정보」에서 빼 주세요.",
          };
        await memberApi.removeWishlist(hits[0].id);
        return {
          ok: true,
          text: `«${hits[0].book.title}» 을(를) 목록에서 뺐어요.`,
        };
      }
      case "extend_loan": {
        const rows = await memberApi.loans();
        const hits = pickMine(
          rows.filter((l) => l.status !== "returned"),
          String(args.book_title),
          (l) => l.book.title,
        );
        if (hits.length === 0)
          return { ok: false, text: "그 책은 빌린 목록에 없어요." };
        if (hits.length > 1)
          return {
            ok: false,
            text: "같은 제목이 여러 건이에요. 「내 정보」에서 연장해 주세요.",
          };
        if (!hits[0].can_extend)
          return {
            ok: false,
            text: "이미 연장했거나 연장할 수 없는 대출이에요.",
          };
        const res = await memberApi.extendLoan(hits[0].id);
        return {
          ok: true,
          text: `«${res.book.title}» 반납일을 ${res.due_at.slice(0, 10)} 로 미뤘어요.`,
        };
      }
      case "delete_request": {
        const rows = await memberApi.requests();
        const hits = pickMine(
          rows,
          String(args.book_title),
          (r) => r.book_title,
        );
        if (hits.length === 0) return { ok: false, text: "그 요청은 없어요." };
        if (hits.length > 1)
          return {
            ok: false,
            text: "같은 제목이 여러 건이에요. 「내 정보」에서 지워 주세요.",
          };
        if (hits[0].approval === "PENDING_APPROVAL") {
          return {
            ok: false,
            text: "사서 승인을 기다리는 요청은 지울 수 없어요.",
          };
        }
        await memberApi.deleteRequest(hits[0].id);
        return { ok: true, text: `요청 #${hits[0].id} 이력을 지웠어요.` };
      }
    }
  } catch (err) {
    return {
      ok: false,
      text: err instanceof Error ? err.message : "처리하지 못했어요.",
    };
  }
}
// `switch` 가 `ToolName` 16개를 전부 덮으므로 `default` 를 두지 않는다 — 도구를
// 새로 넣고 케이스를 빠뜨리면 `tsc` 가 "not all code paths return a value" 로 잡아 준다.

// ── 확인 문장 생성기 ─────────────────────────────────────────

/** 확인 카드에 띄울 문장. 열람과 대여의 차이를 문구로 드러낸다.
 *
 * **모델이 말한 제목이 아니라 해석된 정본 제목**을 쓴다 — 이래야 사용자가
 * "내가 말한 책이 아니네"를 알아챌 수 있다.
 */
export function describeCall(
  name: ToolName,
  args: Record<string, unknown>,
  book: CatalogBook | null,
): string {
  // 정본이 있으면 저자·서가까지 붙여 다른 책과 헷갈리지 않게 한다.
  const t = book
    ? `«${book.title.KR}»(${book.author} · ${book.zone})`
    : `«${args.book_title}»`;
  switch (name) {
    case "request_read":
      return `${t} 을(를) ${args.table} 자리로 가져다 드릴까요? 사서 승인 없이 로봇이 바로 움직여요.`;
    case "request_borrow":
      return `${t} 대여를 신청할까요? 사서 승인 후 안내데스크에서 받으실 수 있어요.`;
    case "reserve_book":
      return `${t} 을(를) 예약할까요? 반납되면 알려드려요.`;
    case "cancel_reservation":
      return `${t} 예약을 취소할까요?`;
    case "add_wishlist":
      return `${t} 을(를) 읽고 싶은 목록에 담을까요?`;
    case "remove_wishlist":
      return `${t} 을(를) 목록에서 뺄까요?`;
    case "extend_loan":
      return `${t} 반납일을 7일 미룰까요? 연장은 1회만 가능해요.`;
    case "delete_request":
      return `${t} 요청 이력을 지울까요? 되돌릴 수 없어요.`;
    default:
      return "이대로 진행할까요?";
  }
}
