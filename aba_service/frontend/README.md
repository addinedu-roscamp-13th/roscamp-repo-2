# LiBi — 도서관 AI 가이드

음성·텍스트로 책과 도서관 공간을 안내하는 **도서관 AI 가이드 챗봇**입니다.
로컬 LLM([Ollama](https://ollama.com))을 사용해 외부 API 비용 없이 동작하며, 한국어·영어·중국어·베트남어를 지원합니다.

---

## ✨ 주요 기능

- 🤖 **로컬 LLM 챗봇** — Ollama로 스트리밍 응답(타이핑 효과)
- 🌐 **다국어** — KR / EN / ZH / VI, 설정한 언어로 응답
- 🎙️ **음성 입력** — 브라우저 음성 인식(Web Speech API). 실시간 인식 + 말이 끊기면 자동 전송
- 📝 **마크다운 렌더링** — 굵게/리스트/제목 등 가독성 있게 표시
- 🗺️ **도서관 지도 / 도서 검색 / 추천**
- ⚙️ **설정** — 모델 선택, 언어 변경, **QR 코드 공유**

---

## 🧰 기술 스택

| 영역 | 사용 |
|---|---|
| UI | React 19, TanStack Router (클라이언트 SPA) |
| 빌드 | Vite 8, TypeScript |
| 스타일 | Tailwind CSS v4, shadcn/ui (Radix) |
| 기타 | react-markdown + remark-gfm, qrcode.react, lucide-react |
| LLM | 로컬 Ollama (기본 `qwen3:1.7b`) |

---

## 🚀 빠른 시작 (개발)

```bash
# 1) 의존성 설치
npm install

# 2) Ollama 실행 + 모델 받기 (별도 터미널)
ollama serve
ollama pull qwen3:1.7b

# 3) 개발 서버 실행 (http://localhost:3000)
npm run dev
```

> 개발 서버는 `/ollama` 요청을 `http://127.0.0.1:11434`로 프록시합니다(`vite.config.ts`). 별도 설정 없이 챗봇이 동작합니다.

---

## 📜 스크립트

| 명령 | 설명 |
|---|---|
| `npm run dev` | 개발 서버 (port 3000) |
| `npm run build` | 프로덕션 빌드 → `dist/` |
| `npm run preview` | 빌드 결과 미리보기 |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

---

## 🔧 환경 변수 (선택)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VITE_OLLAMA_URL` | `/ollama` | Ollama 엔드포인트. 같은 도메인 상대경로(권장) |
| `VITE_OLLAMA_MODEL` | `qwen3:1.7b` | 기본 모델 |

> 모델은 설정 화면에서 바꿀 수 있고 선택값은 `localStorage`(`labi.ollamaModel`)에 저장됩니다.

---

## 🌍 배포 (nginx + 외부 공개)

빌드 결과(`dist/`)를 nginx로 서빙하고, `/ollama`를 로컬 Ollama로 프록시합니다.
외부에서 LLM을 호출하려면 **반드시 같은 도메인에서 프록시**해야 합니다
(브라우저가 `127.0.0.1:11434`를 직접 부르면 외부에선 실패하고, https에선 mixed-content로 차단됩니다).

`/etc/nginx/conf.d/chatbot-frontend.conf`:

```nginx
server {
    listen 3000;
    server_name _;

    root /home/robotPrj_Boilerplate/lib_service/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # SPA 라우팅 fallback
    }

    location /assets/ {
        try_files $uri =404;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # 같은 도메인 /ollama -> 로컬 Ollama (스트리밍 유지)
    location /ollama/ {
        proxy_pass http://127.0.0.1:11434/;
        proxy_http_version 1.1;
        proxy_set_header Host localhost;
        proxy_set_header Origin "";          # Ollama CORS 회피
        proxy_buffering off;                 # 토큰 즉시 전달
        chunked_transfer_encoding on;
        proxy_read_timeout 600s;
    }
}
```

적용:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### ngrok으로 외부 공개 (테스트)

```bash
ngrok http --url=<your-domain>.ngrok-free.dev 3000
```

- nginx가 서빙하는 **3000** 포트를 가리킵니다(80 아님).
- 무료 플랜은 계정당 고정 도메인 1개 제공.
- API 요청에는 ngrok 경고 페이지 우회용 `ngrok-skip-browser-warning` 헤더가 자동 포함됩니다.

> 흐름: 브라우저 → `https://<domain>/ollama/api/chat` → ngrok → nginx(:3000) → 로컬 Ollama

---

## 📁 프로젝트 구조

```
src/
├── main.tsx              # 엔트리 (createRoot + RouterProvider)
├── router.tsx            # TanStack Router 설정
├── routes/
│   ├── __root.tsx        # 루트 레이아웃 (Provider, 에러/404)
│   ├── index.tsx         # 랜딩
│   ├── home.tsx          # 홈 (음성 시작)
│   ├── chat.tsx          # 챗봇 (LLM 스트리밍, 음성, 마크다운)
│   ├── search.tsx        # 도서 검색
│   ├── map.tsx           # 도서관 지도
│   ├── recommend.tsx     # 추천
│   └── settings.tsx      # 설정 (모델/언어/QR)
├── components/
│   ├── AppShell.tsx      # 공통 셸 (헤더/하단 네비)
│   ├── BottomNav.tsx
│   ├── LanguageSwitcher.tsx
│   └── ui/               # shadcn/ui 컴포넌트
├── lib/
│   ├── i18n.tsx          # 다국어 컨텍스트
│   ├── use-speech.ts     # 음성 인식/합성 훅
│   └── mock-data.ts      # 도서/도서관 더미 데이터
└── styles.css            # Tailwind + 커스텀 애니메이션
```

---

## ⚠️ 참고

- 음성 인식은 **보안 컨텍스트(HTTPS 또는 localhost)** 에서만 동작합니다. ngrok(https)·localhost는 OK, 평문 http LAN 접속은 마이크가 막힙니다.
- Ollama 서버가 꺼져 있으면 챗봇은 내장 기본 안내문(fallback)으로 응답합니다.


ngrok http --url=sanora-wretched-lenard.ngrok-free.dev 3000



  수정 후 재시작:

  sudo systemctl restart labi-admin-api

  상태 확인:

  sudo systemctl status labi-admin-api

  로그 확인:

  sudo journalctl -u labi-admin-api -f

  nginx 설정까지 바꿨으면 백엔드 재시작 후 nginx도 reload:

  sudo nginx -t
  sudo systemctl reload nginx

  서비스 목록보기
  #systemctl list-units --type=service --state=running
  
  systemctl list-units --type=service | grep labi