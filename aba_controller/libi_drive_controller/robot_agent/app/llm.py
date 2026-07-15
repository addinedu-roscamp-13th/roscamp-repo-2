"""Ollama/Groq/OpenAI 비전 자율주행 통합 호출 헬퍼.

호스트 .env 의 API Key 존재 여부와 프론트엔드가 선택한 모델명(gpt-*, meta-*, qwen/*)에 따라
동적으로 OpenAI 또는 Groq 비전 API로 포워딩합니다.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any
import os
from pathlib import Path

# ── API Key 로드 함수들 ────────────────────────────────────────────────────────
def _get_api_key(name: str) -> str:
    key = os.getenv(name)
    if key:
        return key
    try:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{name}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""

def _get_api_url(name: str, default: str) -> str:
    url = os.getenv(name)
    if url:
        return url
    try:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{name}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default

GROQ_API_KEY = _get_api_key("GROQ_API_KEY")
GROQ_API_URL = _get_api_url("GROQ_API_URL", "https://api.groq.com")

OPENAI_API_KEY = _get_api_key("OPENAI_API_KEY")
OPENAI_API_URL = _get_api_url("OPENAI_API_URL", "https://api.openai.com")

DEFAULT_MODEL = "gpt-4o-mini"

def generate_json(
    prompt: str,
    model: str = DEFAULT_MODEL,
    image_bytes: bytes | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """선택된 모델에 따라 OpenAI 또는 Groq API(/chat/completions)를 호출하여 JSON을 반환."""
    
    is_openai = model.startswith("gpt-")

    # 만약 비전 지원 안 되는 잘못된 모델명이 오면 각 벤더의 기본 비전 모델로 보정
    if is_openai:
        if model not in ("gpt-4o-mini", "gpt-4o"):
            model = "gpt-4o-mini"
    else:
        if model not in ("meta-llama/llama-4-scout-17b-16e-instruct", "qwen/qwen3.6-27b"):
            model = "meta-llama/llama-4-scout-17b-16e-instruct"

    # 이미지 토큰 준비
    content = [{"type": "text", "text": prompt}]
    if image_bytes:
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img_b64}"
            }
        })

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }

    # 헤더 및 엔드포인트 조립
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    if is_openai:
        endpoint = f"{OPENAI_API_URL.rstrip('/')}/v1/chat/completions"
        if OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    else:
        if "api.groq.com" in GROQ_API_URL:
            endpoint = f"{GROQ_API_URL.rstrip('/')}/openai/v1/chat/completions"
        else:
            endpoint = f"{GROQ_API_URL.rstrip('/')}/v1/chat/completions"
        if GROQ_API_KEY:
            headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        
        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"API 응답 오류 (choices 없음): {body}")
            
        text = choices[0].get("message", {}).get("content") or ""
        return json.loads(text)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_msg = err_json.get("error", {}).get("message") or err_body
        except Exception:
            err_msg = err_body
        raise RuntimeError(f"{'OpenAI' if is_openai else 'Groq'} API HTTP {e.code} 에러: {err_msg}")
    except Exception as e:
        raise RuntimeError(f"{'OpenAI' if is_openai else 'Groq'} API 호출 오류: {str(e)}")
