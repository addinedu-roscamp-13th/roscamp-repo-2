"""회원 음성 대화 — OpenAI Realtime API(WebRTC) ephemeral 토큰 발급.

흐름:
  1) 프론트(`/chat`)가 POST /api/voice/session 으로 1회용 세션 토큰을 받는다.
     - OPENAI_API_KEY 는 서버에만 두고, 브라우저에는 만료 짧은 client_secret 만 내려간다.
  2) 브라우저가 그 토큰으로 OpenAI Realtime 과 WebRTC 직결한다
     (마이크 → STT → LiBi 응답 → TTS → 스피커).
  3) 도구(도서 검색·대여 신청 등)는 **프론트가** `libi-tools.ts` 로 실행한다.
     세션의 tools 목록도 프론트가 접속 직후 `session.update` 로 올린다 — 16개 도구 정의를
     TS/파이썬 양쪽에 복사해두면 반드시 어긋나므로, 정본은 `libi-tools.ts` 하나로 둔다.

푸시투토크(PTT): `turn_detection` 을 꺼서 서버 VAD 가 마음대로 발화를 끊지 않게 하고,
버튼을 누르는 동안만 마이크를 켠다. 발화 종료는 프론트가 input_audio_buffer.commit 으로
명시한다(`use-realtime-voice.ts`).

⚠️ 이 엔드포인트는 로그인 없이도 부를 수 있다 — `/chat` 이 비로그인 사용자에게도 열려
   있기 때문이다. 대신 발급 자체가 OpenAI 과금으로 이어지므로 IP 당 호출수를 제한한다.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings

router = APIRouter(prefix="/api/voice", tags=["voice"])

# GA(정식) 엔드포인트. 구 베타 /v1/realtime/sessions 는 삭제됐다(2025 GA 전환).
#   mint: POST /v1/realtime/client_secrets  → {value: "ek_...", expires_at, session}
#   브라우저 SDP 교환: POST /v1/realtime/calls (프론트가 ek_ 로 직접 호출)
OPENAI_SESSION_URL = "https://api.openai.com/v1/realtime/client_secrets"

# LiBi 페르소나 — 화면 챗(`buildSystemPrompt`)과 같은 역할·말투를 음성용으로 줄인 것.
# 도구 사용 규칙은 프론트가 session.update 로 tools 를 올릴 때 함께 갱신한다.
LIBI_PERSONA = (
    "너는 도서관 안내 AI 'LiBi' 다. 회원의 음성 질문을 듣고 도서관과 장서에 대해 답한다. "
    "말로 듣는 답변이므로 짧고 또렷하게, 두세 문장 안에서 말한다. 목록을 길게 읽지 않는다. "
    "도서 검색·인기도서·내 대출/예약/요청/위시리스트 조회, 구역 위치 안내, 대여 신청·예약·"
    "취소·위시리스트·연장 같은 회원 동작은 반드시 해당 도구(function)를 호출해서 처리한다 — "
    "말로만 '해드렸어요' 라고 하지 않는다. "
    "대여 신청·예약·취소처럼 되돌리기 어려운 도구는 호출하면 화면에 확인 카드가 뜬다. "
    "그때는 '확인 카드를 띄웠어요. 맞으면 「네」를 눌러 주세요.' 라고 안내한다. "
    "책을 특정하지 못했거나 후보가 여럿이면 임의로 고르지 말고 되묻는다. "
    # PTT 는 버튼을 뗀 시점에 발화가 잘릴 수 있다. 그때 추측해서 답하면 '혼자 엉뚱한 말을
    # 한다' 로 느껴지므로, 못 알아들었으면 답 대신 되묻게 못박는다.
    "말이 중간에 끊겼거나 잘 들리지 않으면 추측해서 답하지 말고 "
    "'잘 못 들었어요. 다시 한 번 말씀해 주세요.' 라고 짧게 되묻는다. "
    "사용자가 다른 언어로 말하면 그 언어로 답한다."
)

# 전사 힌트 — 이 서비스에서 자주 나오는 말들. 모델이 비슷한 발음 중에 고를 때 참고한다.
TRANSCRIBE_PROMPT = (
    "도서관 안내 대화입니다. 자주 나오는 말: 리비, 대출, 반납, 예약, 연장, 위시리스트, "
    "희망도서 신청, 서가, 열람실, 베스트셀러, 신간, 저자, 출판사, 청구기호, 회원증."
)

# ── IP 당 발급 제한 ────────────────────────────────────────────────────────────
# 세션 발급 1건 = OpenAI 과금 시작점이다. 공개 URL(ngrok)로 열려 있으므로 최소한의
# 브레이크를 건다. 프로세스 메모리 기반이라 워커가 여러 개면 워커당 한도가 된다 —
# 완전한 방어가 아니라 '실수로 새는 것'을 막는 수준이다.
_RATE_WINDOW_SEC = 60.0
_RATE_MAX_PER_WINDOW = 6
_recent_mints: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    hits = _recent_mints[client_ip]
    while hits and now - hits[0] > _RATE_WINDOW_SEC:
        hits.popleft()
    if len(hits) >= _RATE_MAX_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="음성 연결 요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
        )
    hits.append(now)


def _mint_ephemeral_session(model: str, voice: str, api_key: str) -> dict[str, Any]:
    """OpenAI 에 Realtime 세션을 만들고 ephemeral(client_secret) 응답을 돌려준다(blocking).

    GA 스키마: 세션 설정을 {"session": {...}} 로 감싸고, 오디오/전사/VAD 는
    audio.input / audio.output 아래에 둔다. 응답 최상위 value 가 브라우저용 키(ek_...)다.
    """
    body = json.dumps(
        {
            "session": {
                "type": "realtime",
                "model": model,
                "instructions": LIBI_PERSONA,
                "audio": {
                    "input": {
                        # 언어를 안 박으면 짧거나 조용한 발화를 영어로 환각 전사한다
                        # ("Thank you." 등). 회원 앱 기본 언어인 ko 로 고정.
                        #
                        # 전사 모델은 whisper-1 대신 gpt-4o-transcribe 를 쓴다 — 한국어
                        # 짧은 발화·고유명사(책 제목, 저자)에서 눈에 띄게 정확하다.
                        # prompt 로 도메인 어휘를 미리 알려주면 '위시리스트', '서가'
                        # 같은 말이 엉뚱하게 적히는 걸 줄일 수 있다.
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "ko",
                            "prompt": TRANSCRIBE_PROMPT,
                        },
                        # 도서관 로비 소음 대응. 마이크를 가까이 두고 쓰는 폰/노트북
                        # 사용을 가정해 near_field.
                        "noise_reduction": {"type": "near_field"},
                        # PTT: 서버 VAD 를 끈다. 발화 종료는 버튼을 뗄 때 프론트가
                        # input_audio_buffer.commit 으로 알린다.
                        "turn_detection": None,
                    },
                    "output": {"voice": voice},
                },
            }
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_SESSION_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


@router.post("/session", summary="음성 대화용 OpenAI Realtime ephemeral 토큰 발급")
async def create_session(request: Request) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "OPENAI_API_KEY 가 설정되지 않았습니다 "
                "(aba_service/backend/.env 에 추가한 뒤 백엔드를 재기동하세요)."
            ),
        )
    _check_rate_limit(request.client.host if request.client else "unknown")

    try:
        data = await asyncio.to_thread(
            _mint_ephemeral_session,
            settings.openai_realtime_model,
            settings.openai_realtime_voice,
            settings.openai_api_key,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") or str(exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI 세션 발급 실패({exc.code}): {detail}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI 에 연결할 수 없습니다: {exc.reason}",
        ) from exc

    # GA: 최상위 value 가 ephemeral 키. (구 베타는 client_secret.value 였다)
    secret = data.get("value") or (data.get("client_secret") or {}).get("value")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="ephemeral 토큰이 응답에 없습니다.",
        )
    return {
        "client_secret": secret,
        "expires_at": data.get("expires_at")
        or (data.get("client_secret") or {}).get("expires_at"),
        "model": settings.openai_realtime_model,
        "voice": settings.openai_realtime_voice,
    }
