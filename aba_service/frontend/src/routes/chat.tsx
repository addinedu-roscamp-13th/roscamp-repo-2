import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { BottomNav } from "@/components/BottomNav";
import { LANGS, useI18n } from "@/lib/i18n";
import { QUICK_CHIPS, BOOKS, ZONES, type Book } from "@/lib/mock-data";
import {
  detectCategory,
  fetchRecommendedBooks,
  isRecommendIntent,
} from "@/lib/books-api";
import { Send, Map as MapIcon, X, Menu } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

export const Route = createFileRoute("/chat")({
  head: () => ({ meta: [{ title: "Labi Bot — Labi Bot 챗봇" }] }),
  component: ChatPage,
});

type Msg = {
  id: string;
  role: "user" | "bot";
  text: string;
  showMap?: boolean;
  pending?: boolean;
  books?: Book[];
};

// Same-origin path proxied to the local Ollama server by nginx (works through ngrok / external too).
const OLLAMA_URL = import.meta.env.VITE_OLLAMA_URL ?? "/ollama";
const OLLAMA_MODEL_KEY = "labi.ollamaModel";
const DEFAULT_OLLAMA_MODEL = import.meta.env.VITE_OLLAMA_MODEL ?? "qwen3:1.7b";

const API_BASE = (import.meta.env.VITE_ADMIN_API_URL ?? "").replace(/\/$/, "");

function getSelectedOllamaModel() {
  if (typeof window === "undefined") return DEFAULT_OLLAMA_MODEL;
  return localStorage.getItem(OLLAMA_MODEL_KEY) || DEFAULT_OLLAMA_MODEL;
}

function tryParseRobotCommand(text: string): { robot_type: string; action: string; parameters: any } | null {
  const q = text.toLowerCase().trim();

  // Mobile Robot commands
  if (/(앞으로|전진)/.test(q)) {
    const distMatch = q.match(/([\d\.]+)\s*(초|미터|m|초 동안)/);
    const duration = distMatch ? parseFloat(distMatch[1]) : 1.0;
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: 50, right: 50, duration }
    };
  }
  if (/(뒤로|후진)/.test(q)) {
    const distMatch = q.match(/([\d\.]+)\s*(초|미터|m|초 동안)/);
    const duration = distMatch ? parseFloat(distMatch[1]) : 1.0;
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: -50, right: -50, duration }
    };
  }
  if (/(좌회전|왼쪽으로)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: -40, right: 40, duration: 0.8 }
    };
  }
  if (/(우회전|오른쪽으로)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "move",
      parameters: { left: 40, right: -40, duration: 0.8 }
    };
  }
  if (/(정지|멈춰|멈춤|스톱|stop)/i.test(q)) {
    if (/팔/.test(q)) {
      return {
        robot_type: "arm",
        action: "stop",
        parameters: {}
      };
    }
    return {
      robot_type: "mobile",
      action: "stop",
      parameters: {}
    };
  }
  if (/(웃어줘|표정|행복|해피|happy)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "happy" }
    };
  }
  if (/(슬픈|슬퍼)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "sad" }
    };
  }
  if (/(화나|화내)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "emotion",
      parameters: { emotion: "angry" }
    };
  }
  if (/(소리|벨|삐|비프|소리내)/.test(q)) {
    return {
      robot_type: "mobile",
      action: "buzzer",
      parameters: { preset: "bell", count: 1 }
    };
  }

  // Robot Arm commands
  if (/로봇팔.*(원위치|홈|home)/.test(q) || /(팔.*홈)/.test(q)) {
    return {
      robot_type: "arm",
      action: "home",
      parameters: {}
    };
  }
  if (/(얼굴\s*추적|얼굴\s*따라|얼굴\s*추적\s*시작)/.test(q)) {
    return {
      robot_type: "arm",
      action: "face-track",
      parameters: { start: true }
    };
  }
  if (/(얼굴\s*추적\s*중지|얼굴\s*추적\s*멈춰)/.test(q)) {
    return {
      robot_type: "arm",
      action: "face-track",
      parameters: { start: false }
    };
  }
  if (/(사물\s*인식|객체\s*인식|물건\s*인식)/.test(q)) {
    return {
      robot_type: "arm",
      action: "classify",
      parameters: { start: true }
    };
  }
  if (/(글자\s*인식|텍스트\s*인식|ocr|글씨\s*인식)/i.test(q)) {
    return {
      robot_type: "arm",
      action: "ocr",
      parameters: { start: true }
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

async function askLocalLlm(
  input: string,
  lang: "KR" | "EN" | "ZH" | "VI",
  model: string,
  onToken?: (fullText: string) => void,
  books?: Book[],
) {
  const languageName = {
    KR: "Korean",
    EN: "English",
    ZH: "Chinese",
    VI: "Vietnamese",
  }[lang];
  const endpoint = OLLAMA_URL.endsWith("/") ? OLLAMA_URL.slice(0, -1) : OLLAMA_URL;

  const systemLines = [
    "You are Labi Bot, a helpful AI guide for a bookstore app and robot controller.",
    "You MUST write every reply only in " + languageName + ", regardless of the language the user writes in.",
    "Never answer in any other language.",
    "Keep answers concise and practical.",
    "If the user asks about books, recommend concrete titles or ask for the title or genre.",
    "If the user asks about facilities or shelf location, give a short directional answer.",
    "If the user commands you to move or control the mobile robot or robot arm (e.g. '앞으로 가줘', '로봇팔 원위치'), you MUST start your response with exact JSON string:",
    "CMD:{\"robot_type\": \"mobile\"|\"arm\", \"action\": \"...\", \"parameters\": {...}}",
    "supported actions: 'move' (params: left, right, duration), 'stop', 'emotion' (params: emotion), 'buzzer', 'home', 'angles', 'face-track' (params: start:true/false), 'classify' (params: start:true/false), 'ocr' (params: start:true/false). Examples: CMD:{\"robot_type\":\"mobile\",\"action\":\"move\",\"parameters\":{\"left\":50,\"right\":50,\"duration\":1.0}} or CMD:{\"robot_type\":\"arm\",\"action\":\"home\",\"parameters\":{}}",
  ];
  if (books && books.length > 0) {
    systemLines.push(
      "Here are real books currently in the store database. Recommend ONLY from this list,",
      "mention each book's shelf location, and do not invent titles that are not listed:",
      booksContext(books, lang),
    );
  }

  const response = await fetch(endpoint + "/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json", "ngrok-skip-browser-warning": "true" },
    body: JSON.stringify({
      model,
      stream: true,
      messages: [
        { role: "system", content: systemLines.join("\n") },
        { role: "user", content: input },
      ],
      options: {
        temperature: 0.4,
        num_ctx: 4096,
      },
    }),
  });

  if (!response.ok || !response.body) throw new Error("Ollama request failed: " + response.status);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

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
        const chunk = JSON.parse(line) as { message?: { content?: string }; done?: boolean };
        const piece = chunk.message?.content;
        if (piece) {
          full += piece;
          onToken?.(full);
        }
      } catch {
        // ignore partial / malformed line
      }
    }
  }

  const text = full.trim();
  if (!text) throw new Error("Ollama returned an empty response");
  return text;
}

export function makeReply(input: string, lang: "KR" | "EN" | "ZH" | "VI"): { text: string; showMap?: boolean } {
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
    return { text: lang === "KR" ? "지도를 띄울게요." : "Opening the map.", showMap: true };
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
  const found = BOOKS.find((b) => b.title[lang].toLowerCase().includes(q) || b.title.KR.includes(input));
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
        ? "저희 서점은 문학(A)·예술(B)·과학(C) 코너로 구성돼 있어요. 분야나 책 제목을 알려주시면 위치까지 안내해드릴게요."
        : "Our store has Literature (A), Art (B) and Science (C) sections. Tell me a field or title and I'll guide you.",
  };
}

const mdComponents: Components = {
  p: ({ node: _n, ...props }) => <p className="my-1 first:mt-0 last:mb-0" {...props} />,
  ul: ({ node: _n, ...props }) => <ul className="my-1 list-disc space-y-0.5 pl-5" {...props} />,
  ol: ({ node: _n, ...props }) => <ol className="my-1 list-decimal space-y-0.5 pl-5" {...props} />,
  li: ({ node: _n, ...props }) => <li className="leading-snug marker:text-muted-foreground" {...props} />,
  strong: ({ node: _n, ...props }) => <strong className="font-semibold text-foreground" {...props} />,
  em: ({ node: _n, ...props }) => <em className="italic" {...props} />,
  a: ({ node: _n, ...props }) => (
    <a className="text-primary underline underline-offset-2" target="_blank" rel="noreferrer" {...props} />
  ),
  h1: ({ node: _n, ...props }) => <h3 className="mb-1 mt-2 text-base font-bold first:mt-0" {...props} />,
  h2: ({ node: _n, ...props }) => <h3 className="mb-1 mt-2 text-base font-bold first:mt-0" {...props} />,
  h3: ({ node: _n, ...props }) => <h3 className="mb-1 mt-2 text-sm font-bold first:mt-0" {...props} />,
  code: ({ node: _n, ...props }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]" {...props} />
  ),
  hr: () => <hr className="my-2 border-border" />,
  blockquote: ({ node: _n, ...props }) => (
    <blockquote className="my-1 border-l-2 border-border pl-3 text-muted-foreground" {...props} />
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

// Compact recommendation cards shown under a bot reply, backed by DB books.
// Tapping a card opens the store map highlighting where the book is shelved.
function BookCards({
  books,
  lang,
  onLocate,
}: {
  books: Book[];
  lang: "KR" | "EN" | "ZH" | "VI";
  onLocate: (book: Book) => void;
}) {
  if (!books.length) return null;
  const locateLabel = { KR: "위치 보기", EN: "Show location", ZH: "查看位置", VI: "Xem vị trí" }[lang];
  return (
    <div className="mt-2 space-y-2">
      {books.map((b) => {
        const zone = zoneFor(b);
        return (
          <button
            key={b.id}
            type="button"
            onClick={() => onLocate(b)}
            className="flex w-full items-center gap-3 rounded-xl border border-border bg-card p-2.5 text-left shadow-card transition-colors hover:border-primary"
          >
            <div
              className={`flex size-12 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${b.color} text-2xl`}
            >
              {b.cover}
            </div>
            <div className="min-w-0 flex-1">
              <div className="line-clamp-1 text-sm font-bold text-foreground">{b.title[lang]}</div>
              <div className="text-[11px] text-muted-foreground">{b.author}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[11px]">
                <span className="font-semibold text-primary">
                  📍 {b.zone}{zone ? ` · ${zone.label}` : ""}
                </span>
                <span className="text-muted-foreground">{b.shelf}</span>
                <span className={b.inStock ? "text-emerald-600" : "text-rose-500"}>
                  {b.inStock ? "✅" : "⛔"}
                </span>
              </div>
            </div>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary-soft px-2 py-1 text-[10px] font-semibold text-primary">
              <MapIcon className="size-3" />
              {locateLabel}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function greetingFor(lang: "KR" | "EN" | "ZH" | "VI") {
  return lang === "KR"
    ? "안녕하세요! 저는 서점 가이드 Labi Bot이에요. 책 제목, 장르, 또는 시설 위치 무엇이든 물어봐 주세요 😊"
    : lang === "EN"
      ? "Hi! I'm Labi Bot, your bookstore guide. Ask me about any book, topic or facility."
      : lang === "ZH"
        ? "您好,我是书店向导 Labi Bot,请随意询问任何书籍或设施。"
        : "Xin chào! Tôi là Labi Bot, hướng dẫn viên nhà sách.";
}

function ChatPage() {
  const { lang, tr } = useI18n();

  const [messages, setMessages] = useState<Msg[]>([
    { id: "init", role: "bot", text: greetingFor(lang) },
  ]);
  const [input, setInput] = useState("");
  const [mapOpen, setMapOpen] = useState(false);
  const [focusBook, setFocusBook] = useState<Book | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  const openMapFor = (book: Book) => {
    setFocusBook(book);
    setMapOpen(true);
  };
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, mapOpen]);

  // keep the greeting in sync with the selected language (only before the chat starts)
  useEffect(() => {
    setMessages((m) =>
      m.length === 1 && m[0].id === "init" ? [{ ...m[0], text: greetingFor(lang) }] : m,
    );
  }, [lang]);

  const send = async (text: string) => {
    if (!text.trim()) return;
    const userMsg: Msg = { id: Math.random().toString(36), role: "user", text };
    const pendingId = Math.random().toString(36) + "p";
    setMessages((m) => [
      ...m,
      userMsg,
      {
        id: pendingId,
        role: "bot",
        text: lang === "KR" ? "Labi Bot이 로컬 LLM으로 답변을 작성 중이에요..." : "Labi Bot is thinking locally...",
        pending: true,
      },
    ]);
    setInput("");

    // 1. Check rule-based parser first
    const directCmd = tryParseRobotCommand(text);
    if (directCmd) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, text: `🤖 로봇 명령을 실행 중입니다... (${directCmd.robot_type} - ${directCmd.action})`, pending: true }
            : msg
        )
      );
      try {
        const response = await fetch(`${API_BASE}/api/robot/execute`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_message: text,
            robot_type: directCmd.robot_type,
            action: directCmd.action,
            parameters: directCmd.parameters
          })
        });
        const result = await response.json();
        const successMsg = result.success 
          ? `🤖 로봇 명령이 성공적으로 실행되었습니다.\n\n- 대상: ${directCmd.robot_type}\n- 동작: ${directCmd.action}`
          : `❌ 로봇 명령 실행에 실패했습니다.\n\n오류: ${result.response || "알 수 없는 에러"}`;
        
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? { ...msg, text: successMsg, pending: false }
              : msg
          )
        );
      } catch (err) {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? { ...msg, text: "❌ 백엔드 서버와의 연결에 실패하여 로봇을 제어할 수 없습니다.", pending: false }
              : msg
          )
        );
      }
      return;
    }

    // 2. Fall back to LLM chat
    try {
      const model = getSelectedOllamaModel();
      const showMap = /(화장실|restroom|toilet|지도|map|洗手|지도|map|洗手|지도|map|洗手|지도|map|지도|map|洗手|地图|vệ sinh|bản đồ)/i.test(text);

      // When the user asks for a recommendation, pull real books from the DB so
      // the bot grounds its answer (and we show the matching cards below it).
      let books: Book[] = [];
      if (isRecommendIntent(text)) {
        books = await fetchRecommendedBooks({ category: detectCategory(text), q: text, limit: 4 });
      }

      let robotCmdJson: string | null = null;
      let replyText = "";

      // stream tokens in as they arrive (typing effect)
      await askLocalLlm(
        text,
        lang,
        model,
        (full) => {
          if (full.startsWith("CMD:")) {
            robotCmdJson = full;
            setMessages((m) =>
              m.map((msg) => (msg.id === pendingId ? { ...msg, text: "🤖 자연어 지시 사항 분석 중...", pending: true } : msg)),
            );
          } else {
            replyText = full;
            setMessages((m) =>
              m.map((msg) => (msg.id === pendingId ? { ...msg, text: full, pending: false } : msg)),
            );
          }
        },
        books,
      );

      // If LLM returned a command JSON
      if (robotCmdJson) {
        try {
          const jsonStr = robotCmdJson.replace(/^CMD:/, "").trim();
          const parsed = JSON.parse(jsonStr);
          
          setMessages((m) =>
            m.map((msg) =>
              msg.id === pendingId
                ? { ...msg, text: `🤖 분석 완료. 로봇 명령을 실행 중입니다... (${parsed.robot_type} - ${parsed.action})`, pending: true }
                : msg
            )
          );

          const response = await fetch(`${API_BASE}/api/robot/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              user_message: text,
              robot_type: parsed.robot_type,
              action: parsed.action,
              parameters: parsed.parameters
            })
          });
          const result = await response.json();
          const successMsg = result.success 
            ? `🤖 로봇 명령이 성공적으로 실행되었습니다.\n\n- 대상: ${parsed.robot_type}\n- 동작: ${parsed.action}`
            : `❌ 로봇 명령 실행에 실패했습니다.\n\n오류: ${result.response || "알 수 없는 에러"}`;
          
          setMessages((m) =>
            m.map((msg) =>
              msg.id === pendingId
                ? { ...msg, text: successMsg, pending: false }
                : msg
            )
          );
        } catch (err) {
          setMessages((m) =>
            m.map((msg) =>
              msg.id === pendingId
                ? { ...msg, text: "❌ LLM이 생성한 제어 포맷이 올바르지 않거나 실행 중 오류가 발생했습니다.", pending: false }
                : msg
            )
          );
        }
      } else {
        // Normal chat response
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId ? { ...msg, showMap, books, pending: false } : msg,
          ),
        );
        if (showMap) setTimeout(() => setMapOpen(true), 400);
      }
    } catch (error) {
      console.error(error);
      const reply = makeReply(text, lang);
      const suffix =
        lang === "KR"
          ? "(로컬 LLM 연결 실패로 기본 안내를 사용했어요.)"
          : lang === "EN"
            ? "(Used fallback because local LLM was unavailable.)"
            : lang === "ZH"
              ? "(本地 LLM 连接失败,已使用默认指引。)"
              : "(Đã dùng hướng dẫn mặc định vì không kết nối được LLM cục bộ.)";
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, text: reply.text + "\n\n" + suffix, showMap: reply.showMap, pending: false }
            : msg,
        ),
      );
      if (reply.showMap) setTimeout(() => setMapOpen(true), 400);
    }
  };

  return (
    <AppShell showNav={false}>
      <div className="flex min-h-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pt-4 pb-36">
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
                      <span className="inline-flex items-center gap-1 py-1" aria-label="작성 중">
                        <span className="wave-dot" />
                        <span className="wave-dot [animation-delay:0.15s]" />
                        <span className="wave-dot [animation-delay:0.3s]" />
                      </span>
                    ) : (
                      <MessageMarkdown text={m.text} />
                    )}
                  </div>
                  {m.books && m.books.length > 0 && (
                    <BookCards books={m.books} lang={lang} onLocate={openMapFor} />
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
        </div>

        {/* fixed bottom footer: chips + input */}
        <div className="fixed inset-x-0 bottom-0 z-30 mx-auto max-w-md bg-background">
          {/* quick chips */}
          <div className="flex gap-2 overflow-x-auto px-4 pb-2 pt-2">
            {QUICK_CHIPS[lang].map((c) => (
            <button
              key={c}
              onClick={() => void send(c)}
              className="shrink-0 rounded-full bg-primary-soft px-3 py-1.5 text-[11px] font-semibold text-primary"
            >
              {c}
            </button>
          ))}
        </div>

        {/* input (fixed at bottom) */}
        <div className="shrink-0 border-t border-border bg-card p-3 safe-bottom">
          <form
            onSubmit={(e) => {
              e.preventDefault();
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
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={tr("chatPh")}
              className="h-11 flex-1 rounded-full border border-border bg-background px-4 text-sm outline-none focus:border-primary"
            />

            <button
              type="submit"
              disabled={!input.trim()}
              className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40"
            >
              <Send className="size-5" />
            </button>
          </form>
          </div>
        </div>

        {/* slide-up map */}
        {mapOpen && (() => {
          const focusZone = focusBook ? zoneFor(focusBook) : null;
          return (
          <div className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-md animate-in slide-in-from-bottom rounded-t-3xl border-t border-border bg-card p-5 shadow-float">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-bold text-foreground">📍 서점 지도</h3>
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
                    📍 {focusBook.zone}{focusZone ? ` · ${focusZone.label}` : ""} · {focusBook.shelf}
                  </div>
                </div>
              </div>
            )}

            <div className="relative aspect-video overflow-hidden rounded-xl bg-paper ring-1 ring-border">
              <div className="absolute inset-0 opacity-20 [background-image:linear-gradient(to_right,oklch(0.27_0.12_273)_1px,transparent_1px),linear-gradient(to_bottom,oklch(0.27_0.12_273)_1px,transparent_1px)] [background-size:20px_20px]" />
              {ZONES.map((z) => {
                const active = focusZone?.id === z.id;
                return (
                  <div
                    key={z.id}
                    style={{ left: `${z.x}%`, top: `${z.y}%`, width: `${z.w}%`, height: `${z.h}%` }}
                    className={`absolute flex items-center justify-center rounded ${z.color} text-center text-[10px] font-bold leading-tight transition-all ${
                      active
                        ? "z-10 scale-105 ring-2 ring-primary shadow-float"
                        : focusZone
                          ? "opacity-50"
                          : ""
                    }`}
                  >
                    {active ? `📍 ${z.id} · ${z.label}` : `${z.id} · ${z.label}`}
                  </div>
                );
              })}
            </div>
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
      </div>
    </AppShell>
  );
}
