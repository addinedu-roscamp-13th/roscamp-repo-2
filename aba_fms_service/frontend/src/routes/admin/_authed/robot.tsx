import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Battery,
  Cpu,
  Gauge,
  Gamepad2,
  ImageIcon,
  Lightbulb,
  RefreshCw,
  RotateCcw,
  Smile,
  Square,
  Type,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { AdminShell } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { adminApi, type LcdTextConfig, type RobotResult } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin/_authed/robot")({
  component: RobotPage,
});

function resultToast(res: RobotResult, successMsg: string) {
  if (res.success) {
    toast.success(successMsg);
  } else {
    toast.error(res.error || "명령 실패");
  }
}

// ── LCD 카드 (탭: 표정 / 이미지 / 텍스트 / 폰트) ───────────────────────────

type LcdTab = "emotion" | "image" | "text" | "font";

const EMOTIONS = [
  { key: "hello",    label: "인사" },
  { key: "happy",    label: "행복" },
  { key: "fun",      label: "신남" },
  { key: "interest", label: "관심" },
  { key: "basic",    label: "기본" },
  { key: "bored",    label: "지루함" },
  { key: "sad",      label: "슬픔" },
  { key: "angry",    label: "화남" },
];

// ── 표정 섹션 ─────────────────────────────────────────────────────────────────

function EmotionSection() {
  const [active, setActive] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: (emotion: string) => adminApi.setEmotion(emotion),
    onSuccess: (res, emotion) => resultToast(res, `표정 "${emotion}" 설정 완료`),
    onError: () => toast.error("LCD 표정 설정 실패"),
  });
  const stopMut = useMutation({
    mutationFn: () => adminApi.lcdStop(),
    onSuccess: (res) => {
      if (res.success) { setActive(null); toast.success("LCD 화면 끄기 완료"); }
      else toast.error(res.error || "실패");
    },
  });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
        {EMOTIONS.map(({ key, label }) => (
          <button
            key={key}
            disabled={mut.isPending}
            onClick={() => { setActive(key); mut.mutate(key); }}
            className={cn(
              "rounded-xl border-2 px-2 py-3 text-sm font-medium transition-all",
              active === key
                ? "border-primary bg-primary/10 text-primary shadow-sm"
                : "border-slate-200 bg-white text-slate-700 hover:border-primary/50 hover:bg-primary/5",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={() => stopMut.mutate()}
        disabled={stopMut.isPending}
        className="gap-1.5"
      >
        <Square className="h-3.5 w-3.5" />
        화면 끄기
      </Button>
    </div>
  );
}

// ── 이미지 섹션 ───────────────────────────────────────────────────────────────

function ImageSection() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const uploadMut = useMutation({
    mutationFn: (file: File) => adminApi.lcdUploadImage(file),
    onSuccess: (res) => {
      resultToast(res, "이미지 업로드 및 표시 완료");
      void qc.invalidateQueries({ queryKey: ["robot", "images"] });
    },
    onError: () => toast.error("이미지 업로드 실패"),
  });

  const selectMut = useMutation({
    mutationFn: (filename: string) => adminApi.lcdSelectImage(filename),
    onSuccess: (res, name) => resultToast(res, `"${name}" 화면 표시 완료`),
    onError: () => toast.error("이미지 표시 실패"),
  });

  const deleteMut = useMutation({
    mutationFn: (filename: string) => adminApi.deleteImage(filename),
    onSuccess: () => {
      toast.success("삭제 완료");
      void qc.invalidateQueries({ queryKey: ["robot", "images"] });
    },
    onError: () => toast.error("삭제 실패"),
  });

  const { data: imagesData, isLoading } = useQuery({
    queryKey: ["robot", "images"],
    queryFn: adminApi.listImages,
  });

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) uploadMut.mutate(file);
    e.target.value = "";
  }

  function formatBytes(size: number) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  function formatDate(value: string | null) {
    if (!value) return "-";
    return new Date(value).toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  const images = imagesData?.images ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-800">LCD 이미지 라이브러리</p>
          <p className="mt-1 text-xs text-muted-foreground">업로드한 이미지는 SQLite 목록에 저장되고, 필요한 이미지를 다시 화면에 표시할 수 있습니다.</p>
        </div>
        <div>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={onFileChange}
          />
          <Button
            onClick={() => fileRef.current?.click()}
            disabled={uploadMut.isPending}
            className="gap-2"
          >
            <Upload className="h-4 w-4" />
            {uploadMut.isPending ? "업로드 중..." : "이미지 업로드"}
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="grid grid-cols-[minmax(0,1fr)_96px_96px_160px] gap-3 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500 max-md:hidden">
          <span>이미지</span>
          <span>크기</span>
          <span>업로드</span>
          <span className="text-right">작업</span>
        </div>

        {isLoading ? (
          <div className="p-4 text-sm text-slate-500">이미지 목록을 불러오는 중...</div>
        ) : images.length === 0 ? (
          <div className="p-4 text-sm text-slate-500">저장된 이미지가 없습니다.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {images.map((image) => (
              <div key={image.id} className="grid gap-3 px-3 py-3 md:grid-cols-[minmax(0,1fr)_96px_96px_160px] md:items-center">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800">{image.original_name}</p>
                  <p className="truncate text-[11px] text-slate-500">{image.filename}</p>
                </div>
                <div className="text-xs text-slate-500">{formatBytes(image.size_bytes)}</div>
                <div className="text-xs text-slate-500">{formatDate(image.created_at)}</div>
                <div className="flex justify-start gap-2 md:justify-end">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5"
                    disabled={selectMut.isPending}
                    onClick={() => selectMut.mutate(image.filename)}
                  >
                    <ImageIcon className="h-3.5 w-3.5" />
                    화면표시
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 text-red-600 hover:text-red-700"
                    disabled={deleteMut.isPending}
                    onClick={() => deleteMut.mutate(image.filename)}
                  >
                    <X className="h-3.5 w-3.5" />
                    삭제
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 텍스트 섹션 ───────────────────────────────────────────────────────────────

function TextSection() {
  const [text, setText] = useState("");
  const [fontName, setFontName] = useState("default");
  const [fontSize, setFontSize] = useState(28);
  const [color, setColor] = useState("#ffffff");
  const [bgColor, setBgColor] = useState("#000000");
  const [align, setAlign] = useState<"left" | "center" | "right">("center");
  const [scroll, setScroll] = useState(false);
  const [scrollSpeed, setScrollSpeed] = useState(3);

  const { data: fontsData } = useQuery({
    queryKey: ["robot", "fonts"],
    queryFn: adminApi.listFonts,
  });

  const mut = useMutation({
    mutationFn: (cfg: LcdTextConfig) => adminApi.lcdText(cfg),
    onSuccess: (res) => resultToast(res, "텍스트 표시 완료"),
    onError: () => toast.error("텍스트 표시 실패"),
  });

  function submit() {
    if (!text.trim()) { toast.error("텍스트를 입력해주세요"); return; }
    mut.mutate({ text, font_name: fontName, font_size: fontSize, color, bg_color: bgColor, align, scroll, scroll_speed: scrollSpeed });
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">표시할 텍스트</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder={"안녕하세요!\nPinkyPro 로봇입니다 :)"}
          className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-600">폰트</label>
          <select
            value={fontName}
            onChange={(e) => setFontName(e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:border-primary focus:outline-none"
          >
            <option value="default">기본 폰트 (PIL 내장)</option>
            {(fontsData?.fonts ?? []).map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-600">정렬</label>
          <div className="flex gap-1">
            {(["left", "center", "right"] as const).map((a) => (
              <button key={a} onClick={() => setAlign(a)}
                className={cn("flex-1 rounded-lg border py-1.5 text-xs font-medium transition-all",
                  align === a ? "border-primary bg-primary text-white" : "border-slate-200 bg-white text-slate-600 hover:border-primary/50")}>
                {a === "left" ? "왼쪽" : a === "center" ? "가운데" : "오른쪽"}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">
          글꼴 크기: <span className="tabular-nums">{fontSize}px</span>
        </label>
        <Slider min={8} max={80} step={2} value={[fontSize]} onValueChange={([v]) => setFontSize(v)} />
      </div>

      <div className="flex flex-wrap gap-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-600">글자 색상</label>
          <div className="flex items-center gap-2">
            <input type="color" value={color} onChange={(e) => setColor(e.target.value)}
              className="h-9 w-9 cursor-pointer rounded-lg border border-slate-200 p-0.5" />
            <span className="font-mono text-xs text-slate-500">{color}</span>
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-600">배경 색상</label>
          <div className="flex items-center gap-2">
            <input type="color" value={bgColor} onChange={(e) => setBgColor(e.target.value)}
              className="h-9 w-9 cursor-pointer rounded-lg border border-slate-200 p-0.5" />
            <span className="font-mono text-xs text-slate-500">{bgColor}</span>
          </div>
        </div>
        <div className="ml-auto flex items-end">
          <div className="flex h-16 w-24 items-center justify-center overflow-hidden rounded-lg border border-slate-200"
            style={{ backgroundColor: bgColor }}>
            <span className="truncate px-1 text-center text-[10px] leading-tight"
              style={{ color, fontSize: Math.min(fontSize * 0.4, 12) }}>
              {text || "미리보기"}
            </span>
          </div>
        </div>
      </div>

      {/* 스크롤 애니메이션 */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-3">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={scroll} onChange={(e) => setScroll(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 accent-primary" />
            <span className="text-sm font-medium text-slate-700">왼쪽 흐르기 (스크롤 애니메이션)</span>
          </label>
        </div>
        {scroll && (
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              스크롤 속도: <span className="tabular-nums">{scrollSpeed}px/프레임</span>
            </label>
            <Slider min={1} max={15} step={1} value={[scrollSpeed]} onValueChange={([v]) => setScrollSpeed(v)} />
          </div>
        )}
      </div>

      <Button onClick={submit} disabled={mut.isPending} className="gap-2">
        <Type className="h-4 w-4" />
        {mut.isPending ? "표시 중..." : "LCD에 텍스트 표시"}
      </Button>
    </div>
  );
}

// ── 폰트 섹션 ─────────────────────────────────────────────────────────────────

function FontSection() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const uploadMut = useMutation({
    mutationFn: (file: File) => adminApi.lcdUploadFont(file),
    onSuccess: (res) => {
      if (res.success) {
        toast.success(`폰트 "${res.filename}" 업로드 완료`);
        void qc.invalidateQueries({ queryKey: ["robot", "fonts"] });
      }
    },
    onError: () => toast.error("폰트 업로드 실패"),
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => adminApi.deleteFont(name),
    onSuccess: () => {
      toast.success("폰트 삭제 완료");
      void qc.invalidateQueries({ queryKey: ["robot", "fonts"] });
    },
    onError: () => toast.error("폰트 삭제 실패"),
  });

  const { data: fontsData } = useQuery({
    queryKey: ["robot", "fonts"],
    queryFn: adminApi.listFonts,
  });

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) uploadMut.mutate(file);
    e.target.value = "";
  }

  return (
    <div className="space-y-4">
      <div>
        <input
          ref={fileRef}
          type="file"
          accept=".ttf,.otf"
          className="hidden"
          onChange={onFileChange}
        />
        <Button
          variant="outline"
          onClick={() => fileRef.current?.click()}
          disabled={uploadMut.isPending}
          className="gap-2"
        >
          <Upload className="h-4 w-4" />
          {uploadMut.isPending ? "업로드 중..." : "폰트 파일 업로드"}
        </Button>
        <p className="mt-1.5 text-xs text-muted-foreground">
          TTF, OTF 파일 지원 · 한글 폰트 (NanumGothic, Malgun 등)
        </p>
      </div>

      {(fontsData?.fonts ?? []).length === 0 ? (
        <p className="text-xs text-slate-400">업로드된 폰트 없음</p>
      ) : (
        <div className="space-y-1">
          <p className="text-xs font-medium text-slate-600">업로드된 폰트</p>
          {fontsData!.fonts.map((name) => (
            <div
              key={name}
              className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2"
            >
              <span className="text-sm text-slate-700">{name}</span>
              <button
                onClick={() => deleteMut.mutate(name)}
                className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-500"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── LCD 카드 통합 ─────────────────────────────────────────────────────────────

const LCD_TABS: { key: LcdTab; label: string; icon: typeof Smile }[] = [
  { key: "emotion", label: "표정",   icon: Smile },
  { key: "image",   label: "이미지", icon: ImageIcon },
  { key: "text",    label: "텍스트", icon: Type },
  { key: "font",    label: "폰트",   icon: Upload },
];

function LcdCard() {
  const [tab, setTab] = useState<LcdTab>("emotion");

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 pb-3">
        <Smile className="h-5 w-5 text-primary" />
        <CardTitle className="text-base">LCD 화면 제어</CardTitle>
        <Badge variant="outline" className="ml-auto text-xs">pinky_lcd + PIL</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 탭 버튼 */}
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
          {LCD_TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-all",
                tab === key
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        {tab === "emotion" && <EmotionSection />}
        {tab === "image"   && <ImageSection />}
        {tab === "text"    && <TextSection />}
        {tab === "font"    && <FontSection />}
      </CardContent>
    </Card>
  );
}

// ── LED 카드 ──────────────────────────────────────────────────────────────────

type LedMode = "solid" | "gradient";

const DEFAULT_GRADIENT = [
  "#ff0000", "#ff8000", "#ffff00", "#00ff00",
  "#00ffff", "#0000ff", "#8000ff", "#ff00ff",
];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function LedControlCard() {
  const [mode, setMode] = useState<LedMode>("solid");
  const [solidColor, setSolidColor] = useState("#ff0000");
  const [gradColors, setGradColors] = useState<string[]>([...DEFAULT_GRADIENT]);
  const [brightness, setBrightness] = useState(128);

  const clearMut  = useMutation({ mutationFn: () => adminApi.ledClear(),                onSuccess: (res) => resultToast(res, "LED 초기화 완료"),        onError: () => toast.error("LED 초기화 실패") });
  const brightMut = useMutation({ mutationFn: () => adminApi.ledBrightness(brightness), onSuccess: (res) => resultToast(res, `밝기 ${brightness} 설정`), onError: () => toast.error("밝기 설정 실패") });

  const solidMut = useMutation({
    mutationFn: () => { const [r, g, b] = hexToRgb(solidColor); return adminApi.ledFill(r, g, b); },
    onSuccess: (res) => resultToast(res, "전체 LED 색상 설정 완료"),
    onError: () => toast.error("LED 설정 실패"),
  });

  const gradMut = useMutation({
    mutationFn: async () => {
      for (let i = 0; i < 8; i++) {
        const [r, g, b] = hexToRgb(gradColors[i]);
        await adminApi.ledPixel([i], r, g, b);
      }
    },
    onSuccess: () => toast.success("그라데이션 적용 완료"),
    onError: () => toast.error("그라데이션 적용 실패"),
  });

  const isAnyPending = solidMut.isPending || clearMut.isPending || brightMut.isPending || gradMut.isPending;

  function setGradColor(idx: number, hex: string) {
    setGradColors((prev) => prev.map((c, i) => (i === idx ? hex : c)));
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 pb-3">
        <Lightbulb className="h-5 w-5 text-primary" />
        <CardTitle className="text-base">LED 제어</CardTitle>
        <Badge variant="outline" className="ml-auto text-xs">pinkylib.LED</Badge>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* 모드 탭 */}
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
          {(["solid", "gradient"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={cn("flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-all",
                mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700")}>
              {m === "solid" ? "단색" : "그라데이션"}
            </button>
          ))}
        </div>

        {/* 단색 모드 */}
        {mode === "solid" && (
          <div className="space-y-3">
            <div>
              <label className="mb-2 block text-xs font-medium text-slate-600">색상 선택</label>
              <div className="flex items-center gap-3">
                <input type="color" value={solidColor} onChange={(e) => setSolidColor(e.target.value)}
                  className="h-12 w-12 cursor-pointer rounded-xl border border-slate-200 p-0.5" />
                <div>
                  <p className="font-mono text-sm text-slate-700">{solidColor.toUpperCase()}</p>
                  <p className="text-xs text-muted-foreground">
                    RGB ({hexToRgb(solidColor).join(", ")})
                  </p>
                </div>
                {/* 미리보기 바 */}
                <div className="ml-auto flex gap-0.5">
                  {Array.from({ length: 8 }, (_, i) => (
                    <div key={i} className="h-8 w-5 rounded" style={{ backgroundColor: solidColor }} />
                  ))}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => solidMut.mutate()} disabled={isAnyPending}>전체 적용</Button>
              <Button size="sm" variant="outline" onClick={() => clearMut.mutate()} disabled={isAnyPending}>전체 끄기</Button>
            </div>
          </div>
        )}

        {/* 그라데이션 모드 */}
        {mode === "gradient" && (
          <div className="space-y-4">
            {/* 미리보기 바 */}
            <div className="flex gap-1 rounded-lg overflow-hidden border border-slate-200">
              {gradColors.map((c, i) => (
                <div key={i} className="flex-1 h-8" style={{ backgroundColor: c }} />
              ))}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-slate-600">픽셀별 색상 (0–7)</p>
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
                {gradColors.map((c, i) => (
                  <div key={i} className="flex flex-col items-center gap-1">
                    <span className="text-[10px] font-medium text-slate-500">{i}</span>
                    <input type="color" value={c} onChange={(e) => setGradColor(i, e.target.value)}
                      className="h-10 w-10 cursor-pointer rounded-lg border border-slate-200 p-0.5" />
                  </div>
                ))}
              </div>
            </div>

            <div className="flex gap-2">
              <Button size="sm" onClick={() => gradMut.mutate()} disabled={isAnyPending}>
                {gradMut.isPending ? "적용 중..." : "그라데이션 적용"}
              </Button>
              <Button size="sm" variant="outline"
                onClick={() => setGradColors([...DEFAULT_GRADIENT])}>
                초기화
              </Button>
              <Button size="sm" variant="outline" onClick={() => clearMut.mutate()} disabled={isAnyPending}>전체 끄기</Button>
            </div>
          </div>
        )}

        {/* 밝기 — 공통 */}
        <div>
          <p className="mb-2 text-xs font-medium text-slate-600">
            밝기: <span className="tabular-nums">{brightness}</span>
          </p>
          <div className="flex items-center gap-3">
            <Slider min={0} max={255} step={1} value={[brightness]} onValueChange={([v]) => setBrightness(v)} className="flex-1" />
            <Button size="sm" variant="outline" onClick={() => brightMut.mutate()} disabled={isAnyPending}>적용</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 모터 카드 ─────────────────────────────────────────────────────────────────

function MotorCard() {
  const [speed, setSpeed] = useState(45);
  const [duration, setDuration] = useState(0.5);

  const moveMut = useMutation({
    mutationFn: (input: { left: number; right: number; duration: number }) => adminApi.motorMove(input),
    onSuccess: (res) => resultToast(res, "모터 명령 전송 완료"),
    onError: () => toast.error("모터 명령 실패"),
  });
  const stopMut = useMutation({
    mutationFn: () => adminApi.motorStop(),
    onSuccess: (res) => resultToast(res, "모터 정지 완료"),
    onError: () => toast.error("모터 정지 실패"),
  });

  const run = (left: number, right: number) => moveMut.mutate({ left, right, duration });
  const busy = moveMut.isPending || stopMut.isPending;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 pb-3">
        <Gamepad2 className="h-5 w-5 text-primary" />
        <CardTitle className="text-base">모터 주행</CardTitle>
        <Badge variant="outline" className="ml-auto text-xs">pinkylib.Motor</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 lg:grid-cols-[180px_minmax(0,1fr)]">
          <div className="grid grid-cols-3 gap-2">
            <span />
            <Button variant="outline" size="icon" disabled={busy} onClick={() => run(speed, speed)} title="전진">
              <ArrowUp className="h-4 w-4" />
            </Button>
            <span />
            <Button variant="outline" size="icon" disabled={busy} onClick={() => run(-speed, speed)} title="좌회전">
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <Button variant="destructive" size="icon" disabled={busy} onClick={() => stopMut.mutate()} title="정지">
              <Square className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="icon" disabled={busy} onClick={() => run(speed, -speed)} title="우회전">
              <ArrowRight className="h-4 w-4" />
            </Button>
            <span />
            <Button variant="outline" size="icon" disabled={busy} onClick={() => run(-speed, -speed)} title="후진">
              <ArrowDown className="h-4 w-4" />
            </Button>
            <span />
          </div>

          <div className="space-y-4 rounded-lg border border-slate-200 p-3">
            <div>
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium text-slate-600">속도</span>
                <span className="font-mono text-slate-500">{speed}</span>
              </div>
              <Slider min={10} max={100} step={5} value={[speed]} onValueChange={([v]) => setSpeed(v ?? speed)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">동작 시간(초)</label>
              <input
                type="number"
                min={0.1}
                max={5}
                step={0.1}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value) || 0.1)}
                className="w-28 rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
              />
            </div>
            {moveMut.data && (
              <pre className="overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">
                {moveMut.data.success ? moveMut.data.output || "명령 완료" : `오류: ${moveMut.data.error}`}
              </pre>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 센서 카드 ─────────────────────────────────────────────────────────────────

function SensorCard() {
  const usQuery = useQuery({ queryKey: ["robot", "sensor", "ultrasonic"], queryFn: adminApi.getSensorUltrasonic, enabled: false, retry: false });
  const batQuery = useQuery({ queryKey: ["robot", "sensor", "battery"],   queryFn: adminApi.getSensorBattery,    enabled: false, retry: false });
  const irQuery = useQuery({ queryKey: ["robot", "sensor", "ir"], queryFn: adminApi.getSensorIr, enabled: false, retry: false });
  const imuQuery = useQuery({ queryKey: ["robot", "sensor", "imu"], queryFn: adminApi.getSensorImu, enabled: false, retry: false });

  const sensors = [
    { label: "초음파 거리",  query: usQuery,  desc: "pinkylib.Ultrasonic · 거리 측정", icon: Gauge },
    { label: "IR 장애물",  query: irQuery, desc: "pinkylib.IR · 좌/중/우 감지", icon: Activity },
    { label: "IMU 자세",  query: imuQuery, desc: "pinkylib.IMU · acc/mag/gyro/euler", icon: RotateCcw },
    { label: "배터리 상태",  query: batQuery, desc: "pinkylib.Battery · 전압/잔량", icon: Battery },
  ];

  function formatOutput(output: string) {
    try {
      return JSON.stringify(JSON.parse(output), null, 2);
    } catch {
      return output;
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 pb-3">
        <Cpu className="h-5 w-5 text-primary" />
        <CardTitle className="text-base">센서 데이터</CardTitle>
        <Badge variant="outline" className="ml-auto text-xs">pinkylib 직접 제어</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {sensors.map(({ label, query, desc, icon: Icon }) => (
          <div key={label} className="rounded-lg border border-slate-200 p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Icon className="h-4 w-4 text-primary" />
                <div>
                  <p className="text-sm font-medium">{label}</p>
                  <p className="text-[11px] text-muted-foreground">{desc}</p>
                </div>
              </div>
              <Button size="sm" variant="outline" onClick={() => query.refetch()} disabled={query.isFetching} className="gap-1.5">
                <RefreshCw className={cn("h-3.5 w-3.5", query.isFetching && "animate-spin")} />
                읽기
              </Button>
            </div>
            {query.data && (
              <pre className="mt-2 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">
                {query.data.success ? formatOutput(query.data.output) || "(데이터 없음)" : `오류: ${query.data.error}`}
              </pre>
            )}
          </div>
        ))}
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
          <p className="text-[11px] text-muted-foreground">
            센서 값은 명령 실행 시점의 단발 측정값입니다. 연속 모니터링은 대시보드 자동 갱신 영역에서 다룹니다.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 페이지 ────────────────────────────────────────────────────────────────────

function RobotPage() {
  const capabilities = [
    "LCD", "LED", "Motor", "Ultrasonic", "IR", "IMU", "Battery", "Camera",
  ];

  return (
    <AdminShell title="로봇 제어">
      <div className="space-y-5">
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Zap className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-900">PinkyPro 운영 콘솔</h1>
              <p className="text-sm text-muted-foreground">Wiki part1 기준 하드웨어 모듈을 직접 점검하고 제어합니다.</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {capabilities.map((name) => (
              <Badge key={name} variant="secondary" className="rounded-md">{name}</Badge>
            ))}
          </div>
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-5">
            <MotorCard />
            <LcdCard />
          </div>
          <div className="space-y-5">
            <SensorCard />
            <LedControlCard />
          </div>
        </div>
      </div>
    </AdminShell>
  );
}
