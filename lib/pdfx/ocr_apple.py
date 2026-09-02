"""Native platform OCR adapter (macOS, zero service dependency).

This is the final local engine in the chain and works without a visual service.
Platform bindings are imported lazily so the module is importable anywhere.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

LANGS = ["ja", "zh-Hans", "en"]
LEGACY_LANGS = ["ja", "en"]
DPI = 300

# 复用 VNRecognizeTextRequest（Apple 官方建议：多图批处理时复用实例，
# 让 Vision 内部准备好的识别模型保持热态）。线程局部：每个工作线程持有
# 自己的实例，避免跨线程共享非线程安全的 request 对象。
_local = threading.local()
_frameworks_cache = None


class AppleVisionUnavailable(Exception):
    pass


def _vision_frameworks():
    try:
        import Quartz
        import Vision
        # newer pyobjc makes Quartz lazy: CF symbols only live in CoreFoundation
        from CoreFoundation import CFDataCreateWithBytesNoCopy, kCFAllocatorNull

        return Quartz, Vision, CFDataCreateWithBytesNoCopy, kCFAllocatorNull
    except ImportError as e:
        raise AppleVisionUnavailable(f"pyobjc not available: {e}") from e


def _get_frameworks():
    global _frameworks_cache
    if _frameworks_cache is None:
        _frameworks_cache = _vision_frameworks()
    return _frameworks_cache


def _get_request(langs: list | None):
    """Thread-local VNRecognizeTextRequest, configured once per language set."""
    key = tuple(langs or LANGS)
    cache = getattr(_local, "requests", None)
    if cache is None:
        cache = {}
        _local.requests = cache
    req = cache.get(key)
    if req is None:
        _, Vision, _, _ = _get_frameworks()
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(list(key))
        req.setUsesLanguageCorrection_(True)
        cache[key] = req
    return req


def ocr_png(png_bytes: bytes, langs: list | None = None, return_conf: bool = False):
    """OCR one page image (PNG bytes).

    Returns text, or (text, mean_confidence) when return_conf=True — the mean
    of VNRecognizedTextObservation.confidence over recognized lines (None if
    nothing was recognized).
    """
    Quartz, Vision, CFDataCreateWithBytesNoCopy, kCFAllocatorNull = _get_frameworks()

    data = CFDataCreateWithBytesNoCopy(None, png_bytes, len(png_bytes), kCFAllocatorNull)
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    cgimage = Quartz.CGImageCreateWithPNGDataProvider(provider, None, False, Quartz.kCGRenderingIntentDefault)

    request = _get_request(langs)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(f"Apple Vision request failed: {error}")

    parts = []
    confs = []
    for obs in request.results() or []:
        top = obs.topCandidates_(1)
        if top:
            parts.append(top[0].string())
            confs.append(float(obs.confidence()))
    text = "\n".join(parts)
    if return_conf:
        mean_conf = (sum(confs) / len(confs)) if confs else None
        return text, mean_conf
    return text


def ocr_page(page, dpi: int = DPI, langs: list | None = None, return_conf: bool = False):
    from ._vlhttp import render_page_png

    return ocr_png(render_page_png(page, dpi=dpi), langs=langs, return_conf=return_conf)


__all__ = ["AppleVisionUnavailable", "DPI", "LANGS", "ocr_page", "ocr_png"]
