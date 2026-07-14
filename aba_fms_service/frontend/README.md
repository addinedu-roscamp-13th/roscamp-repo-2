# Labi Bot - 서점 AI 가이드 + 관리자 패널

음성, 텍스트로 책과 매장 공간을 안내하는 챗봇과 `/admin/*` 관리자 화면을 함께 제공하는 프론트엔드입니다.

## 주요 기능

- 로컬 LLM 챗봇
- 다국어 지원 KR / EN / ZH / VI
- 음성 입력과 마크다운 렌더링
- 매장 지도, 도서 검색, 추천
- 설정: 모델 선택, 언어 변경, QR 코드 공유
- 관리자 패널: 대시보드, 관리자 목록, 로봇 제어, 부저, 카메라

## 기술 스택

| 영역 | 사용 |
|---|---|
| UI | React 19, TanStack Router |
| 상태 | TanStack Query |
| 스타일 | Tailwind CSS v4, shadcn/ui |
| 기타 | react-markdown, qrcode.react, lucide-react |
| LLM | Ollama (`qwen3:1.7b`) |

## 빠른 시작

```bash
npm install
ollama serve
ollama pull qwen3:1.7b
npm run dev
```

개발 서버는 `/ollama` 요청을 `http://127.0.0.1:11434`로 프록시합니다.

## 스크립트

| 명령 | 설명 |
|---|---|
| `npm run dev` | 개발 서버 |
| `npm run build` | 프로덕션 빌드 |
| `npm run preview` | 빌드 결과 미리보기 |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VITE_OLLAMA_URL` | `/ollama` | Ollama 엔드포인트 |
| `VITE_OLLAMA_MODEL` | `qwen3:1.7b` | 기본 모델 |
| `VITE_ADMIN_API_URL` | `http://192.168.0.43:9001` | 관리자 API 서버 |

## 프로젝트 구조

```text
src/
├── main.tsx
├── router.tsx
├── routes/
│   ├── __root.tsx
│   ├── index.tsx
│   ├── home.tsx
│   ├── chat.tsx
│   ├── search.tsx
│   ├── map.tsx
│   ├── recommend.tsx
│   ├── settings.tsx
│   └── admin/
│       ├── login.tsx
│       └── _authed/
│           ├── index.tsx
│           ├── users.tsx
│           ├── robot.tsx
│           ├── buzzer.tsx
│           ├── camera.tsx
│           └── dev/
│               ├── api-docs.tsx
│               ├── tables.tsx
│               ├── erd.tsx
│               ├── architecture.tsx
│               └── server-ops.tsx
├── components/
└── lib/
```

## 참고

- 음성 인식은 HTTPS 또는 localhost에서만 동작합니다.
- Ollama 서버가 꺼져 있으면 챗봇은 fallback 응답을 사용합니다.
