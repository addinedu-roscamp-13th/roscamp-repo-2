"""Ollama(로컬 LLM) 호출 헬퍼.

프로젝트는 Ollama 를 LLM 백엔드로 쓴다(기본 모델 qwen3:1.7b). 자연어 지시를
등록된 학습 액션/시나리오로 매핑할 때 사용한다. 소형 모델이므로 format=json 으로
출력을 강제하고, 실패 시 호출부가 결정적 매칭으로 폴백한다.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from app.config import OLLAMA_URL

DEFAULT_MODEL = "qwen3:1.7b"


def generate_json(
    prompt: str,
    model: str = DEFAULT_MODEL,
    url: str = OLLAMA_URL,
    timeout: int = 45,
) -> dict[str, Any]:
    """Ollama /api/generate 를 format=json 으로 호출하고 파싱된 dict 를 반환.

    파싱 실패/네트워크 오류는 예외로 전달(호출부에서 폴백 처리).
    """
    # qwen3 계열은 사고(thinking) 토큰을 뱉을 수 있어 /no_think 로 억제한다.
    payload = {
        "model": model,
        "prompt": f"{prompt}\n\n/no_think",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = (body.get("response") or "").strip()
    return json.loads(text)
