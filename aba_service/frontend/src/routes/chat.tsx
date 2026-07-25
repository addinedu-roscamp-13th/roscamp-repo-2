import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { BottomNav } from "@/components/BottomNav";
import { LibraryMap } from "@/components/LibraryMap";
import { BookRow } from "@/components/BookRow";
import {
  BookDetailSheet,
  reserveFromSheet,
} from "@/components/BookDetailSheet";
import { BotConfirmCard } from "@/components/BotConfirmCard";
import { LANGS, useI18n } from "@/lib/i18n";
import { QUICK_CHIPS, BOOKS, ZONES, type Book } from "@/lib/mock-data";
import {
  detectCategory,
  fetchCatalog,
  isRecommendIntent,
  type CatalogBook,
} from "@/lib/books-api";
import {
  LIBI_TOOLS,
  prepareTool,
  runPending,
  type PendingCall,
  type ToolResult,
} from "@/lib/libi-tools";
import { Send, Map as MapIcon, X, Menu, Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { useSpeechRecognition, useSpeechSupported } from "@/lib/use-speech";
import { z } from "zod";

// 홈·검색의 마이크가 인식이 끝나면 여기로 그 문장을 그대로 들고 온다.
const chatSearchSchema = z.object({ q: z.string().optional() });

export const Route = createFileRoute("/chat")({
  validateSearch: chatSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — LiBi 챗봇" }] }),
  component: ChatPage,
});

type Msg = {
  id: string;
  role: "user" | "bot";
  text: string;
  showMap?: boolean;
  pending?: boolean;
  books?: CatalogBook[];
};

/** Ollama 가 스트리밍 청크로 내려주는 도구 호출 하나. */
type ToolCallMsg = { function?: { name?: string; arguments?: unknown } };

/**
 * 모델에 보내는 원본 이력 한 턴. 화면에 그리는 `Msg[]` 와는 별개다 — 화면용에는
 * 카드·스켈레톤 같은 표시 전용 항목이 섞여 있어 그대로 못 보낸다.
 */
type ChatTurn = {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: ToolCallMsg[];
};

// 최근 12턴 고정 윈도. 1.7B 컨텍스트가 40k 라 무한히 쌓으면 앞이 잘려 나간다.
// ponytail: 요약이 필요할 만큼 길어지면 그때 붙인다.
const MAX_TURNS = 12;

// Same-origin path proxied to the local Ollama server by nginx (works through ngrok / external too).
const OLLAMA_URL = import.meta.env.VITE_OLLAMA_URL ?? "/ollama";
const OLLAMA_MODEL_KEY = "labi.ollamaModel";
const DEFAULT_OLLAMA_MODEL = import.meta.env.VITE_OLLAMA_MODEL ?? "qwen3:1.7b";

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

function getSelectedOllamaModel() {
  if (typeof window === "undefined") return DEFAULT_OLLAMA_MODEL;
  return localStorage.getItem(OLLAMA_MODEL_KEY) || DEFAULT_OLLAMA_MODEL;
}

function tryParseRobotCommand(
  text: string,
): { robot_type: string; action: string; parameters: any } | null {
  const q = text.toLowerCase().trim();

  // Mobile Robot commands
  if (/(앞으로|전진)/.test(q)) {
    const distMatch = q.match(/([\d\.]+)\s*(초|미터|m|초 동안)/);
    const duration = distMatch ? parseFloat(distMatch[1]) : 1.0;
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: 50, right: 50, duration },
    };
  }
  if (/(뒤로|후진)/.test(q)) {
    const distMatch = q.match(/([\d\.]+)\s*(초|미터|m|초 동안)/);
    const duration = distMatch ? parseFloat(distMatch[1]) : 1.0;
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: -50, right: -50, duration },
    };
  }
  if (/(좌회전|왼쪽으로)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: -40, right: 40, duration: 0.8 },
    };
  }
  if (/(우회전|오른쪽으로)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: 40, right: -40, duration: 0.8 },
    };
  }
  if (/(정지|멈춰|멈춤|스톱|stop)/i.test(q)) {
    if (/팔/.test(q)) {
      return {
        robot_type: "arm",
        action: "stop",
        parameters: {},
      };
    }
    return {
      robot_type: "mobile",
      action: "stop",
      parameters: {},
    };
  }
  if (/(웃어줘|표정|행복|해피|happy)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "happy" },
    };
  }
  if (/(슬픈|슬퍼)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "sad" },
    };
  }
  if (/(화나|화내)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "angry" },
    };
  }
  if (/(소리|벨|삐|비프|소리내)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "buzzer",
      parameters: { preset: "bell", count: 1 },
    };
  }

  // Robot Arm commands
  if (/로봇팔.*(원위치|홈|home)/.test(q) || /(팔.*홈)/.test(q)) {
    return {
      robot_type: "arm",
      action: "home",
      parameters: {},
    };
  }
  if (/(얼굴\s*추적|얼굴\s*따라|얼굴\s*추적\s*시작)/.test(q)) {
    return {
      robot_type: "arm",
      action: "face-track",
      parameters: { start: true },
    };
  }
  if (/(얼굴\s*추적\s*중지|얼굴\s*추적\s*멈춰)/.test(q)) {
    return {
      robot_type: "arm",
      action: "face-track",
      parameters: { start: false },
    };
  }
  if (/(사물\s*인식|객체\s*인식|물건\s*인식)/.test(q)) {
    return {
      robot_type: "arm",
      action: "classify",
      parameters: { start: true },
    };
  }
  if (/(글자\s*인식|텍스트\s*인식|ocr|글씨\s*인식)/i.test(q)) {
    return {
      robot_type: "arm",
      action: "ocr",
      parameters: { start: true },
    };
  }

  return null;
}

// Turn DB books into a compact context block the LLM can recommend from.
function booksContext(books: Book[], lang: "KR" | "EN" | "ZH" | "VI"): string {
  return books
    .map((b) => {
      const tags = (b.forWhom[lang] ?? []).join(" ");
      const stock = b.inStock ? "available" : "out of stock";
      return `- "${b.title[lang]}" by ${b.author} | category: ${b.category} | location: ${b.zone} ${b.shelf} | ${stock} | ${b.summary[lang] ?? ""} ${tags}`;
    })
    .join("\n");
}

// System prompt is rebuilt (and overwrites historyRef.current[0]) on every send —
// language and the current turn's book grounding can change turn to turn, but the
// rest of the conversation history stays put.
function buildSystemPrompt(
  lang: "KR" | "EN" | "ZH" | "VI",
  books: Book[],
): string {
  const languageName = {
    KR: "Korean",
    EN: "English",
    ZH: "Chinese",
    VI: "Vietnamese",
  }[lang];

  const lines = [
    "You are LiBi, a helpful AI guide for a library app and robot controller.",
    "You MUST write every reply only in " +
      languageName +
      ", regardless of the language the user writes in.",
    "Never answer in any other language.",
    "Keep answers concise and practical.",
    "You have tools for member actions (searching books, book detail, popular books, my loans/requests/reservations/wishlist, zone lookup, requesting delivery, reserving, cancelling, wishlist add/remove, extending a loan, deleting a request). Call the matching tool instead of only describing the action in text.",
    "If the user asks about facilities or shelf location, give a short directional answer.",
    "If the user commands you to move or control the mobile robot or robot arm (e.g. '앞으로 가줘', '로봇팔 원위치') and it is NOT a member/book action, you MUST start your response with exact JSON string:",
    'CMD:{"robot_type": "mobile"|"arm", "action": "...", "parameters": {...}}',
    'supported actions: \'move\' (params: left, right, duration), \'stop\', \'emotion\' (params: emotion), \'buzzer\', \'home\', \'angles\', \'face-track\' (params: start:true/false), \'classify\' (params: start:true/false), \'ocr\' (params: start:true/false). Examples: CMD:{"robot_type":"mobile","action":"move","parameters":{"left":50,"right":50,"duration":1.0}} or CMD:{"robot_type":"arm","action":"home","parameters":{}}',
  ];
  if (books.length > 0) {
    lines.push(
      "Here are real books currently in the store database. Recommend ONLY from this list,",
      "mention each book's shelf location, and do not invent titles that are not listed:",
      booksContext(books, lang),
    );
  }
  return lines.join("\n");
}

/**
 * Streams `/api/chat`. Ollama delivers `message.tool_calls` inside the streamed
 * chunks even with `stream: true` (confirmed against the current Ollama API docs),
 * so we keep the existing typing-effect UX and collect tool calls alongside it —
 * no need to fall back to `stream: false`.
 */
async function askOllama({
  messages,
  model,
  tools,
  onToken,
}: {
  messages: ChatTurn[];
  model: string;
  tools?: unknown[];
  onToken?: (fullText: string) => void;
}): Promise<{ text: string; toolCalls: ToolCallMsg[] }> {
  const endpoint = OLLAMA_URL.endsWith("/")
    ? OLLAMA_URL.slice(0, -1)
    : OLLAMA_URL;

  const response = await fetch(endpoint + "/api/chat", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    body: JSON.stringify({
      model,
      stream: true,
      messages,
      ...(tools ? { tools } : {}),
      options: {
        temperature: 0.4,
        num_ctx: 4096,
      },
    }),
  });

  if (!response.ok || !response.body)
    throw new Error("Ollama request failed: " + response.status);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";
  const toolCalls: ToolCallMsg[] = [];

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      try {
        const chunk = JSON.parse(line) as {
          message?: { content?: string; tool_calls?: ToolCallMsg[] };
          done?: boolean;
        };
        const piece = chunk.message?.content;
        if (piece) {
          full += piece;
          onToken?.(full);
        }
        const calls = chunk.message?.tool_calls;
        if (Array.isArray(calls) && calls.length > 0) toolCalls.push(...calls);
      } catch {
        // ignore partial / malformed line
      }
    }
  }

  return { text: full.trim(), toolCalls };
}

/** `/api/robot/execute` 호출 하나로 — CMD: JSON 경로와 정규식 경로가 공유한다. */
async function callRobotExecute(
  userText: string,
  robot: { robot_type: string; action: string; parameters: unknown },
): Promise<string> {
  try {
    const response = await fetch(`${API_BASE}/api/robot/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_message: userText,
        robot_type: robot.robot_type,
        action: robot.action,
        parameters: robot.parameters,
      }),
    });
    const result = await response.json();
    return result.success
      ? `🤖 로봇 명령이 성공적으로 실행되었습니다.\n\n- 대상: ${robot.robot_type}\n- 동작: ${robot.action}`
      : `❌ 로봇 명령 실행에 실패했습니다.\n\n오류: ${result.response || "알 수 없는 에러"}`;
  } catch {
    return "❌ 백엔드 서버와의 연결에 실패하여 로봇을 제어할 수 없습니다.";
  }
}

export function makeReply(
  input: string,
  lang: "KR" | "EN" | "ZH" | "VI",
): { text: string; showMap?: boolean } {
  const q = input.toLowerCase();
  if (/(화장실|restroom|toilet|洗手|vệ sinh)/.test(q)) {
    return {
      text:
        lang === "KR"
          ? "화장실은 오른쪽 끝, 북카페 옆에 있어요."
          : lang === "EN"
            ? "The restroom is at the far right, next to the book café."
            : lang === "ZH"
              ? "洗手间在最右侧,书咖旁边。"
              : "Nhà vệ sinh ở cuối bên phải, cạnh quán cà phê sách.",
      showMap: true,
    };
  }
  if (/(지도|map|地图|bản đồ)/.test(q)) {
    return {
      text: lang === "KR" ? "지도를 띄울게요." : "Opening the map.",
      showMap: true,
    };
  }
  if (/(문학|소설|literature|fiction|novel|文学|văn học)/.test(q)) {
    return {
      text:
        lang === "KR"
          ? "문학 코너(A) 추천으로는 『데미안』(A-1 첫째 줄)이 있어요. 자아를 찾아가는 성장소설이에요."
          : "From Literature (Zone A), I recommend 'Demian' (A-1), a coming-of-age classic.",
    };
  }
  if (/(예술|미술|art|design|艺术|nghệ thuật)/.test(q)) {
    return {
      text:
        lang === "KR"
          ? "예술 코너(B) 추천으로는 『서양미술사』(B-1 첫째 줄)가 있어요. 미술 입문에 좋아요."
          : "From Art (Zone B), I recommend 'The Story of Art' (B-1), a great intro to art history.",
    };
  }
  if (/(과학|science|宇宙|물리|khoa học)/.test(q)) {
    return {
      text:
        lang === "KR"
          ? "과학 코너(C) 추천으로는 『코스모스』(C-1 첫째 줄)가 있어요. 우주의 경이를 담은 명저예요."
          : "From Science (Zone C), I recommend 'Cosmos' (C-1), a classic on the wonder of the universe.",
    };
  }
  // fallback: try to match a book
  const found = BOOKS.find(
    (b) =>
      b.title[lang].toLowerCase().includes(q) || b.title.KR.includes(input),
  );
  if (found) {
    return {
      text:
        lang === "KR"
          ? `『${found.title.KR}』은(는) ${found.zone} ${found.shelf}에 있어요. ${found.inStock ? "재고 있음 ✅" : "현재 품절입니다."}`
          : `'${found.title[lang]}' is at ${found.zone}. ${found.inStock ? "In stock." : "Sold out."}`,
      showMap: found.inStock,
    };
  }
  return {
    text:
      lang === "KR"
        ? "저희 도서관은 문학(A)·예술(B)·과학(C) 코너로 구성돼 있어요. 분야나 책 제목을 알려주시면 위치까지 안내해드릴게요."
        : "Our store has Literature (A), Art (B) and Science (C) sections. Tell me a field or title and I'll guide you.",
  };
}

const mdComponents: Components = {
  p: ({ node: _n, ...props }) => (
    <p className="my-1 first:mt-0 last:mb-0" {...props} />
  ),
  ul: ({ node: _n, ...props }) => (
    <ul className="my-1 list-disc space-y-0.5 pl-5" {...props} />
  ),
  ol: ({ node: _n, ...props }) => (
    <ol className="my-1 list-decimal space-y-0.5 pl-5" {...props} />
  ),
  li: ({ node: _n, ...props }) => (
    <li className="leading-snug marker:text-muted-foreground" {...props} />
  ),
  strong: ({ node: _n, ...props }) => (
    <strong className="font-semibold text-foreground" {...props} />
  ),
  em: ({ node: _n, ...props }) => <em className="italic" {...props} />,
  a: ({ node: _n, ...props }) => (
    <a
      className="text-primary underline underline-offset-2"
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  h1: ({ node: _n, ...props }) => (
    <h3 className="mb-1 mt-2 text-base font-bold first:mt-0" {...props} />
  ),
  h2: ({ node: _n, ...props }) => (
    <h3 className="mb-1 mt-2 text-base font-bold first:mt-0" {...props} />
  ),
  h3: ({ node: _n, ...props }) => (
    <h3 className="mb-1 mt-2 text-sm font-bold first:mt-0" {...props} />
  ),
  code: ({ node: _n, ...props }) => (
    <code
      className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]"
      {...props}
    />
  ),
  hr: () => <hr className="my-2 border-border" />,
  blockquote: ({ node: _n, ...props }) => (
    <blockquote
      className="my-1 border-l-2 border-border pl-3 text-muted-foreground"
      {...props}
    />
  ),
};

function MessageMarkdown({ text }: { text: string }) {
  return (
    <div className="leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// Resolve a book's zone code (e.g. "A-2") to a ZONES entry for the store map.
function zoneFor(book: Book) {
  const prefix = book.zone.split("-")[0];
  return ZONES.find((z) => z.id === prefix) ?? null;
}

function greetingFor(lang: "KR" | "EN" | "ZH" | "VI") {
  return lang === "KR"
    ? "안녕하세요! 저는 도서관 가이드 LiBi이에요. 책 제목, 장르, 또는 시설 위치 무엇이든 물어봐 주세요 😊"
    : lang === "EN"
      ? "Hi! I'm LiBi, your library guide. Ask me about any book, topic or facility."
      : lang === "ZH"
        ? "您好,我是书店向导 LiBi,请随意询问任何书籍或设施。"
        : "Xin chào! Tôi là LiBi, hướng dẫn viên nhà sách.";
}

function ChatPage() {
  const { lang, tr } = useI18n();
  const { q } = Route.useSearch();

  const [messages, setMessages] = useState<Msg[]>([
    { id: "init", role: "bot", text: greetingFor(lang) },
  ]);
  const [input, setInput] = useState("");
  const [mapOpen, setMapOpen] = useState(false);
  const [focusBook, setFocusBook] = useState<Book | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [pendingCall, setPendingCall] = useState<PendingCall | null>(null);
  const [pickedBook, setPickedBook] = useState<CatalogBook | null>(null);
  // 요청이 날아가는 중이거나 확인 카드가 떠 있는 동안은 입력을 잠근다 — 안 잠그면
  // 연타가 두 번째 응답으로 pendingCall 을 덮어쓸 수 있다.
  const busy = sending || pendingCall !== null;

  // 음성 입력 — 홈/검색 마이크와 달리 여기선 인식된 문장을 검색어가 아니라
  // 그대로 LiBi 에게 보낸다(도구 호출까지 이어짐).
  const speechSupported = useSpeechSupported();
  const speechLang = LANGS.find((l) => l.code === lang)?.speech ?? "ko-KR";
  const { listening, transcript, start, stop } =
    useSpeechRecognition(speechLang);

  const scrollRef = useRef<HTMLDivElement>(null);
  // 화면에 그리는 Msg[] 와 별개로, 모델에 보낼 원본 이력을 따로 둔다.
  // 화면용에는 카드·스켈레톤 같은 표시 전용 항목이 섞여 있어 그대로 못 보낸다.
  const historyRef = useRef<ChatTurn[]>([{ role: "system", content: "" }]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, mapOpen]);

  // keep the greeting in sync with the selected language (only before the chat starts)
  useEffect(() => {
    setMessages((m) =>
      m.length === 1 && m[0].id === "init"
        ? [{ ...m[0], text: greetingFor(lang) }]
        : m,
    );
  }, [lang]);

  // 인식이 끝나면(침묵 감지로 자동 정지) 그 문장을 그대로 보낸다 — 홈 화면의
  // "말하면 자동으로 넘어간다" 패턴과 같다.
  useEffect(() => {
    if (!listening && transcript.trim() && !busy) {
      const text = transcript.trim();
      const id = setTimeout(() => void send(text), 400);
      return () => clearTimeout(id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listening, transcript]);

  // 홈/검색 마이크가 `?q=` 로 문장을 들고 오면 도착하자마자 한 번 보낸다.
  const autoSentRef = useRef(false);
  useEffect(() => {
    if (q?.trim() && !autoSentRef.current) {
      autoSentRef.current = true;
      void send(q.trim());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const updateMessage = (id: string, patch: Partial<Msg>) =>
    setMessages((m) =>
      m.map((msg) => (msg.id === id ? { ...msg, ...patch } : msg)),
    );

  const addBotMessage = (text: string, extra?: Partial<Msg>) =>
    setMessages((m) => [
      ...m,
      { id: Math.random().toString(36) + "b", role: "bot", text, ...extra },
    ]);

  const buildMessages = (): ChatTurn[] => [
    historyRef.current[0],
    ...historyRef.current.slice(1).slice(-MAX_TURNS),
  ];

  // 도구 결과를 이력에 넣고, 모델에게 한 번 더 물어 자연스러운 마무리 답을 받는다
  // (도구 없이). 이 왕복이 없으면 다회차 대화가 성립하지 않는다.
  // 바로 실행되는 조회형 도구와, 확인 카드를 거쳐 실행된 변경형 도구가 함께 쓴다.
  const finishToolTurn = async (call: ToolCallMsg, result: ToolResult) => {
    historyRef.current.push(
      { role: "assistant", content: "", tool_calls: [call] },
      { role: "tool", content: result.text },
    );
    try {
      const { text: follow } = await askOllama({
        messages: buildMessages(),
        model: getSelectedOllamaModel(),
      });
      if (follow.trim()) {
        historyRef.current.push({ role: "assistant", content: follow });
        addBotMessage(follow);
      }
    } catch {
      // 마무리 답을 못 받아도 이미 보여준 도구 결과로 충분하다.
    }
  };

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const userMsg: Msg = { id: Math.random().toString(36), role: "user", text };
    const pendingId = Math.random().toString(36) + "p";
    setMessages((m) => [
      ...m,
      userMsg,
      {
        id: pendingId,
        role: "bot",
        text:
          lang === "KR"
            ? "LiBi이 로컬 LLM으로 답변을 작성 중이에요..."
            : "LiBi is thinking locally...",
        pending: true,
      },
    ]);
    setInput("");
    setSending(true);

    try {
      const model = getSelectedOllamaModel();
      const showMap =
        /(화장실|restroom|toilet|지도|map|洗手|地图|vệ sinh|bản đồ)/i.test(
          text,
        );

      // When the user asks for a recommendation, pull real books from the DB so
      // the bot grounds its answer (and we show the matching cards below it).
      let books: CatalogBook[] = [];
      if (isRecommendIntent(text)) {
        books = await fetchCatalog({
          category: detectCategory(text),
          q: text,
          limit: 4,
        });
        // 자유 문장 전체가 LIKE 검색어라 거의 매칭이 안 된다 — 매칭 실패 시
        // q 없이 카테고리만으로 재시도해 그라운딩 목록을 비워두지 않는다.
        if (books.length === 0) {
          books = await fetchCatalog({
            category: detectCategory(text),
            limit: 4,
          });
        }
      }

      // 시스템 메시지는 매 턴 새로 쓴다(언어·이번 턴 도서 그라운딩) — 나머지 이력은 그대로 둔다.
      historyRef.current[0] = {
        role: "system",
        content: buildSystemPrompt(lang, books),
      };
      historyRef.current.push({ role: "user", content: text });

      // stream tokens in as they arrive (typing effect); tool_calls arrive in the
      // same stream, so we don't need to drop to stream:false.
      const { text: finalText, toolCalls } = await askOllama({
        messages: buildMessages(),
        model,
        tools: LIBI_TOOLS,
        onToken: (full) => {
          if (full.startsWith("CMD:")) {
            updateMessage(pendingId, {
              text: "🤖 자연어 지시 사항 분석 중...",
              pending: true,
            });
          } else {
            updateMessage(pendingId, { text: full, pending: false });
          }
        },
      });

      // LLM 도구를 먼저 태운다. 모델이 도구를 하나라도 골랐으면 그것만 처리하고
      // 정규식·CMD: 하드웨어 경로로는 내려가지 않는다.
      if (toolCalls.length > 0) {
        // 모델이 여러 개를 고르면 첫 변경형 하나만 다룬다 — 확인 카드가 하나뿐이라
        // 동시에 두 건을 실행하면 사용자가 무엇에 동의했는지 알 수 없다.
        const call = toolCalls[0];
        const prepared = await prepareTool(
          String(call.function?.name ?? ""),
          call.function?.arguments,
        );

        if (prepared.kind === "error") {
          updateMessage(pendingId, { text: prepared.text, pending: false });
          // 실패도 이력에 남긴다 — 안 남기면 다음 턴에 모델이 방금 거절된 걸
          // 잊고 같은 도구를 또 부르거나 이미 됐다고 우길 수 있다.
          historyRef.current.push({
            role: "assistant",
            content: prepared.text,
          });
          return;
        }
        if (prepared.kind === "choose") {
          // 제목이 여러 권에 걸린다 — 임의로 고르지 않고 후보를 카드로 보여주고 멈춘다.
          updateMessage(pendingId, {
            text: prepared.text,
            books: prepared.books,
            pending: false,
          });
          historyRef.current.push({
            role: "assistant",
            content: prepared.text,
          });
          return;
        }
        if (prepared.kind === "confirm") {
          updateMessage(pendingId, {
            text: prepared.pending.sentence,
            pending: false,
          });
          setPendingCall(prepared.pending);
          return;
        }
        // kind === "run" — 되돌릴 필요 없는 도구라 이미 실행됐다.
        updateMessage(pendingId, {
          text: prepared.result.text,
          books: prepared.result.books,
          pending: false,
        });
        await finishToolTurn(call, prepared.result);
        return;
      }

      historyRef.current.push({ role: "assistant", content: finalText });

      // 모델이 CMD: JSON 으로 하드웨어 명령을 냈으면 실행한다 (기존 동작, 그대로 둔다).
      if (finalText.startsWith("CMD:")) {
        try {
          const parsed = JSON.parse(finalText.replace(/^CMD:/, "").trim()) as {
            robot_type: string;
            action: string;
            parameters: unknown;
          };
          updateMessage(pendingId, {
            text: `🤖 분석 완료. 로봇 명령을 실행 중입니다... (${parsed.robot_type} - ${parsed.action})`,
            pending: true,
          });
          const resultText = await callRobotExecute(text, parsed);
          updateMessage(pendingId, { text: resultText, pending: false });
        } catch {
          updateMessage(pendingId, {
            text: "❌ LLM이 생성한 제어 포맷이 올바르지 않거나 실행 중 오류가 발생했습니다.",
            pending: false,
          });
        }
        return;
      }

      // 도구를 하나도 안 골랐을 때만 로봇 하드웨어 정규식으로 내려간다.
      // (순서가 반대면 "예약을 정지해줘" 가 로봇 정지 명령으로 샌다 — 이번 작업의 핵심 변경.)
      const robot = tryParseRobotCommand(text);
      if (robot) {
        updateMessage(pendingId, {
          text: `🤖 로봇 명령을 실행 중입니다... (${robot.robot_type} - ${robot.action})`,
          pending: true,
        });
        const resultText = await callRobotExecute(text, robot);
        updateMessage(pendingId, { text: resultText, pending: false });
        return;
      }

      // Normal chat response
      updateMessage(pendingId, {
        text: finalText,
        showMap,
        books,
        pending: false,
      });
      if (showMap) setTimeout(() => setMapOpen(true), 400);
    } catch (error) {
      // askOllama 가 실패하면 방금 넣은 user 턴이 응답 없이 붕 뜬다 — 다음 턴이
      // "user, user" 로 이어지지 않게 되돌린다.
      const last = historyRef.current[historyRef.current.length - 1];
      if (last?.role === "user" && last.content === text) {
        historyRef.current.pop();
      }
      console.error(error);

      // LLM 이 죽어도 정지 같은 하드웨어 명령은 살아있어야 한다 — 정규식 경로는
      // 네트워크 없이 로컬에서만 판단하므로 Ollama 장애와 무관하게 동작한다.
      const robot = tryParseRobotCommand(text);
      if (robot) {
        updateMessage(pendingId, {
          text: `🤖 로봇 명령을 실행 중입니다... (${robot.robot_type} - ${robot.action})`,
          pending: true,
        });
        const resultText = await callRobotExecute(text, robot);
        updateMessage(pendingId, { text: resultText, pending: false });
        return;
      }

      const reply = makeReply(text, lang);
      const suffix =
        lang === "KR"
          ? "(로컬 LLM 연결 실패로 기본 안내를 사용했어요.)"
          : lang === "EN"
            ? "(Used fallback because local LLM was unavailable.)"
            : lang === "ZH"
              ? "(本地 LLM 连接失败,已使用默认指引。)"
              : "(Đã dùng hướng dẫn mặc định vì không kết nối được LLM cục bộ.)";
      updateMessage(pendingId, {
        text: reply.text + "\n\n" + suffix,
        showMap: reply.showMap,
        pending: false,
      });
      if (reply.showMap) setTimeout(() => setMapOpen(true), 400);
    } finally {
      setSending(false);
    }
  };

  return (
    <AppShell showNav={false}>
      <div className="flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pt-4 pb-36"
        >
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground shadow">
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex gap-2">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-sm">
                  📚
                </div>
                <div className="max-w-[80%]">
                  <div className="rounded-2xl rounded-tl-sm bg-card px-4 py-2.5 text-sm text-foreground shadow-card">
                    {m.pending ? (
                      <span
                        className="inline-flex items-center gap-1 py-1"
                        aria-label="작성 중"
                      >
                        <span className="wave-dot" />
                        <span className="wave-dot [animation-delay:0.15s]" />
                        <span className="wave-dot [animation-delay:0.3s]" />
                      </span>
                    ) : (
                      <MessageMarkdown text={m.text} />
                    )}
                  </div>
                  {m.books && m.books.length > 0 && (
                    <div className="mt-2 space-y-2">
                      {m.books.map((b) => (
                        <BookRow key={b.id} book={b} onSelect={setPickedBook} />
                      ))}
                    </div>
                  )}
                  <div className="mt-1 flex gap-2 px-1">
                    {m.showMap && (
                      <button
                        onClick={() => setMapOpen(true)}
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-primary"
                      >
                        <MapIcon className="size-3" />
                        지도 보기
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ),
          )}

          {/* 변경형 도구 확인 카드 — 메시지 목록 아래, 대화 흐름 중 하나로 보여준다. */}
          {pendingCall && (
            <BotConfirmCard
              pending={pendingCall}
              onCancel={() => {
                setPendingCall(null);
                const text = "알겠어요, 취소했어요.";
                addBotMessage(text);
                historyRef.current.push({ role: "assistant", content: text });
              }}
              onConfirm={async (name, args) => {
                const confirmed: PendingCall = { ...pendingCall, name, args };
                setPendingCall(null);
                setSending(true);
                try {
                  const result = await runPending(confirmed);
                  addBotMessage(result.text, { books: result.books });
                  await finishToolTurn(
                    {
                      function: {
                        name: confirmed.name,
                        arguments: confirmed.args,
                      },
                    },
                    result,
                  );
                } finally {
                  setSending(false);
                }
              }}
            />
          )}
        </div>

        {/* fixed bottom footer: chips + input */}
        <div className="fixed inset-x-0 bottom-0 z-30 mx-auto max-w-md bg-background">
          {/* quick chips */}
          <div className="flex gap-2 overflow-x-auto px-4 pb-2 pt-2">
            {QUICK_CHIPS[lang].map((c) => (
              <button
                key={c}
                onClick={() => void send(c)}
                disabled={busy}
                className="shrink-0 rounded-full bg-primary-soft px-3 py-1.5 text-[11px] font-semibold text-primary disabled:opacity-40"
              >
                {c}
              </button>
            ))}
          </div>

          {/* input (fixed at bottom) */}
          <div className="shrink-0 border-t border-border bg-card p-3 safe-bottom">
            {listening && (
              <p className="mb-2 text-center text-xs font-bold text-primary">
                🎙️ {tr("listening")}
              </p>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (busy || !input.trim()) return;
                void send(input);
              }}
              className="flex items-center gap-2"
            >
              {/* hamburger → opens bottom menu */}
              <button
                type="button"
                onClick={() => setNavOpen(true)}
                className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground"
                aria-label="menu"
              >
                <Menu className="size-5" />
              </button>

              <input
                value={listening ? transcript : input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={listening ? tr("listening") : tr("chatPh")}
                disabled={busy}
                readOnly={listening}
                className="h-11 flex-1 rounded-full border border-border bg-background px-4 text-sm outline-none focus:border-primary disabled:opacity-60"
              />

              {speechSupported && (
                <button
                  type="button"
                  onClick={() => (listening ? stop() : start())}
                  disabled={busy}
                  aria-label="voice input"
                  className={`relative flex size-11 shrink-0 items-center justify-center rounded-full transition-colors disabled:opacity-40 ${
                    listening
                      ? "voice-pulse bg-accent text-accent-foreground"
                      : "bg-secondary text-secondary-foreground"
                  }`}
                >
                  <Mic className="size-5" />
                </button>
              )}

              <button
                type="submit"
                disabled={busy || !input.trim()}
                className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40"
              >
                <Send className="size-5" />
              </button>
            </form>
          </div>
        </div>

        {/* slide-up map */}
        {mapOpen &&
          (() => {
            const focusZone = focusBook ? zoneFor(focusBook) : null;
            return (
              <div className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-md animate-in slide-in-from-bottom rounded-t-3xl border-t border-border bg-card p-5 shadow-float">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-bold text-foreground">📍 도서관 지도</h3>
                  <button
                    onClick={() => {
                      setMapOpen(false);
                      setFocusBook(null);
                    }}
                    aria-label="close"
                  >
                    <X className="size-5 text-muted-foreground" />
                  </button>
                </div>

                {focusBook && (
                  <div className="mb-3 flex items-center gap-2 rounded-xl bg-primary-soft px-3 py-2">
                    <span className="text-xl">{focusBook.cover}</span>
                    <div className="min-w-0 flex-1">
                      <div className="line-clamp-1 text-sm font-bold text-foreground">
                        {focusBook.title[lang]}
                      </div>
                      <div className="text-[11px] font-semibold text-primary">
                        📍 {focusBook.zone}
                        {focusZone ? ` · ${focusZone.label}` : ""} ·{" "}
                        {focusBook.shelf}
                      </div>
                    </div>
                  </div>
                )}

                {/* 실제 arte2 맵 + waypoint 기준 구역 박스 (가로). 책의 zone 이 곧 정점 이름이라
                그대로 넘기면 해당 구역이 강조된다. */}
                <LibraryMap activeZone={focusBook ? focusBook.zone : null} />
                <button
                  onClick={() => {
                    setMapOpen(false);
                    setFocusBook(null);
                  }}
                  className="mt-4 h-11 w-full rounded-xl bg-primary text-sm font-bold text-primary-foreground"
                >
                  대화로 돌아가기
                </button>
              </div>
            );
          })()}

        {/* slide-up bottom menu (opened by hamburger) */}
        {navOpen && (
          <>
            <div
              className="fixed inset-0 z-40 bg-black/20"
              onClick={() => setNavOpen(false)}
            />
            <div
              className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-md animate-in slide-in-from-bottom"
              onClick={() => setNavOpen(false)}
            >
              <BottomNav />
            </div>
          </>
        )}

        <BookDetailSheet
          book={pickedBook}
          onOpenChange={(open) => !open && setPickedBook(null)}
          onReserve={(b) => void reserveFromSheet(b)}
        />
      </div>
    </AppShell>
  );
}
