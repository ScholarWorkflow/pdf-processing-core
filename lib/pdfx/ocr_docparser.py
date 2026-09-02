"""Whole-page OCR adapter for a configured layout-aware provider.

The provider may perform layout segmentation, element recognition, and
reading-order merging. This adapter only renders a page and posts it.
"""

from __future__ import annotations

import logging

from ._vlhttp import OCRError, post_image, provider_model, render_page_png

logger = logging.getLogger(__name__)

MODEL = provider_model("layout", "layout-ocr")


def ocr_page(page, dpi: int = 200) -> str:
    png = render_page_png(page, dpi=dpi)
    return post_image(MODEL, png, "", role="layout")


def ocr_png(png_bytes: bytes) -> str:
    return post_image(MODEL, png_bytes, "", role="layout")


__all__ = ["MODEL", "OCRError", "ocr_page", "ocr_png"]
