"""OCR endpoint backed by EasyOCR (deep-learning OCR, strong Korean accuracy).

The EasyOCR Reader is heavy to construct (it loads detection/recognition
models and, on first run, downloads their weights), so it is created lazily on
the first request and cached for the process lifetime. Recognition itself is
CPU/GPU bound, so the endpoint is a plain ``def`` — FastAPI runs it in a
threadpool, keeping the event loop responsive.
"""

import threading
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# Languages: Korean + English. EasyOCR allows 'en' alongside 'ko'.
_LANGS = ["ko", "en"]
_MAX_BYTES = 12 * 1024 * 1024  # 12 MB upload cap

_reader = None
_reader_lock = threading.Lock()
_reader_error: Optional[str] = None


def _get_reader():
    """Lazily build and cache the EasyOCR reader (thread-safe)."""
    global _reader, _reader_error
    if _reader is not None:
        return _reader
    with _reader_lock:
        if _reader is not None:
            return _reader
        try:
            import easyocr  # imported lazily so the app boots without the dep
            import torch

            gpu = torch.cuda.is_available()
            # verbose=False suppresses EasyOCR's progress-bar spam on stdout.
            _reader = easyocr.Reader(_LANGS, gpu=gpu, verbose=False)
            _reader_error = None
        except Exception as exc:  # pragma: no cover - surfaced to the client
            _reader_error = f"{type(exc).__name__}: {exc}"
            raise
    return _reader


def warmup() -> None:
    """Preload the EasyOCR reader so the first real request is fast.

    Safe to call from app startup; runs in a background thread there so it never
    blocks uvicorn boot. Failures (e.g. missing dep) are swallowed — the first
    request will then surface a clear 503.
    """
    try:
        _get_reader()
        print("[ocr] EasyOCR reader warmed up.", flush=True)
    except Exception as exc:  # pragma: no cover
        print(f"[ocr] warmup failed: {exc}", flush=True)


class OcrLine(BaseModel):
    text: str
    confidence: float


class OcrResponse(BaseModel):
    text: str
    lines: List[OcrLine]
    engine: str
    gpu: bool


@router.post("", response_model=OcrResponse)
def recognize(image: UploadFile = File(...)) -> OcrResponse:
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드할 수 있어요.")

    data = image.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다 (최대 12MB).")

    try:
        reader = _get_reader()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"OCR 엔진을 초기화할 수 없어요: {_reader_error or 'unknown error'}",
        )

    try:
        # detail=1 -> list of (bbox, text, confidence)
        results = reader.readtext(data, detail=1, paragraph=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"인식 중 오류: {exc}")

    lines = [
        OcrLine(text=str(text), confidence=round(float(conf), 4))
        for (_box, text, conf) in results
        if str(text).strip()
    ]
    full_text = "\n".join(line.text for line in lines)

    import torch

    return OcrResponse(
        text=full_text,
        lines=lines,
        engine="easyocr",
        gpu=bool(torch.cuda.is_available()),
    )
