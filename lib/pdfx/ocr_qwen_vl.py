"""Whole-page OCR adapter for a configured general visual provider.

It is the transcription fallback after the layout-aware adapter. The prompt
requests verbatim text and uses ``[?]`` for unreadable content.
"""

from __future__ import annotations

import logging

from ._vlhttp import OCRError, post_image, provider_model, render_page_png

logger = logging.getLogger(__name__)

MODEL = provider_model("transcription", "vision-ocr")

PROMPT = (
    "Transcribe ALL text on this page verbatim, in natural reading order. "
    "Keep the original language and layout as plain text lines; write math "
    "as LaTeX. If a character or word is unreadable, output [?] instead of "
    "guessing. Do not add commentary."
)


def ocr_page(page, dpi: int = 200) -> str:
    png = render_page_png(page, dpi=dpi)
    return post_image(MODEL, png, PROMPT, role="transcription")


def ocr_png(png_bytes: bytes) -> str:
    return post_image(MODEL, png_bytes, PROMPT, role="transcription")


__all__ = ["MODEL", "OCRError", "ocr_page", "ocr_png"]
