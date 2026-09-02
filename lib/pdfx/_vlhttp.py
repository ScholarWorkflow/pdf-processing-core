"""Shared HTTP plumbing for optional visual providers.

Providers use an OpenAI-compatible image completion contract by default. The
endpoint and model identifiers are deployment configuration, not package
constants, so the core remains usable with local or remote providers. Stdlib
only.

Retry observability: module-level counters (requests / retries / timeouts /
failures) let large fan-in runs expose retry storms via pdfx.extract's
"vision_http" report field instead of only log lines.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT_S = 300
ATTEMPTS = 2
MAX_IMAGE_BYTES = 28 * 1024 * 1024

_HTTP_STATS = {"requests": 0, "retries": 0, "timeouts": 0, "failures": 0}
_HTTP_STATS_LOCK = threading.Lock()


def http_stats() -> dict:
    """Snapshot of cumulative HTTP attempt counters (thread-safe copy)."""
    with _HTTP_STATS_LOCK:
        return dict(_HTTP_STATS)


class OCRError(Exception):
    pass


def _env_name(role: str, suffix: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in role.upper())
    return f"PDFX_{token}_{suffix}"


def provider_endpoint(role: str = "vision") -> str:
    """Return a configured provider endpoint or raise a clear OCR error."""
    endpoint = os.environ.get(_env_name(role, "ENDPOINT"), "").strip()
    if not endpoint:
        endpoint = os.environ.get("PDFX_VISION_ENDPOINT", "").strip()
    if not endpoint:
        raise OCRError(
            f"visual provider endpoint is not configured; set "
            f"{_env_name(role, 'ENDPOINT')} or PDFX_VISION_ENDPOINT"
        )
    return endpoint


def provider_model(role: str, default: str | None = None) -> str:
    """Resolve a model identifier from role-specific provider configuration."""
    model = os.environ.get(_env_name(role, "MODEL"), "").strip()
    if not model:
        model = os.environ.get("PDFX_VISION_MODEL", "").strip()
    if not model:
        if default is None:
            raise OCRError(
                f"visual provider model is not configured; set "
                f"{_env_name(role, 'MODEL')} or PDFX_VISION_MODEL"
            )
        model = default
    return model


def configured_models(role: str = "vision", structured: bool = False) -> list[str]:
    """Read an optional JSON provider chain from environment configuration.

    Each item may be a model string or an object with ``model``/``name`` and an
    optional ``structured`` boolean. Structured consumers skip entries that
    explicitly declare that they cannot return structured output.
    """
    raw = (os.environ.get("PDFX_VISION_CHAIN", "").strip()
           or os.environ.get("VISION_CHAIN", "").strip())
    if not raw:
        try:
            return [provider_model(role)]
        except OCRError:
            return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    models = []
    for item in items:
        if isinstance(item, str):
            model, supports_structured = item.strip(), True
        elif isinstance(item, dict):
            model = str(item.get("model") or item.get("name") or "").strip()
            supports_structured = item.get("structured", True) is not False
        else:
            continue
        if model and (not structured or supports_structured):
            models.append(model)
    return models


def render_page_png(page, dpi: int = 200) -> bytes:
    import pymupdf as fitz

    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def post_image(model: str, png_bytes: bytes, prompt: str,
               max_tokens: int | None = None, *, role: str = "vision",
               image_mime: str = "image/png") -> str:
    """Send one image + prompt, return the completion text. Retries once."""
    if len(png_bytes) > MAX_IMAGE_BYTES:
        raise OCRError(f"image too large for {model}: {len(png_bytes)} bytes")

    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    body = json.dumps(payload).encode("utf-8")

    last_err = None
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.time()
        with _HTTP_STATS_LOCK:
            _HTTP_STATS["requests"] += 1
            if attempt > 1:
                _HTTP_STATS["retries"] += 1
        try:
            req = urllib.request.Request(
                provider_endpoint(role),
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            logger.info("%s attempt %d ok in %.1fs (%d chars)", model, attempt, time.time() - t0, len(content))
            return content
        except Exception as e:
            last_err = e
            timed_out = isinstance(e, (TimeoutError, urllib.error.URLError)) and \
                not isinstance(e, urllib.error.HTTPError)
            logger.warning("%s attempt %d failed after %.1fs: %s", model, attempt, time.time() - t0, e)
            with _HTTP_STATS_LOCK:
                if timed_out:
                    _HTTP_STATS["timeouts"] += 1
                if attempt == ATTEMPTS:
                    _HTTP_STATS["failures"] += 1
    raise OCRError(f"{model} failed after {ATTEMPTS} attempts: {last_err}")


def post_multipart(png_bytes: bytes, *, role: str = "layout") -> dict:
    """Post a PNG to a configured multipart provider and decode its JSON body."""
    if len(png_bytes) > MAX_IMAGE_BYTES:
        raise OCRError(f"image too large: {len(png_bytes)} bytes")
    boundary = f"pdfx-{threading.get_ident()}-{time.time_ns()}"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f"name=\"file\"; filename=\"page.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).encode()
    body = head + png_bytes + f"\r\n--{boundary}--\r\n".encode()
    last_err = None
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.time()
        with _HTTP_STATS_LOCK:
            _HTTP_STATS["requests"] += 1
            if attempt > 1:
                _HTTP_STATS["retries"] += 1
        req = urllib.request.Request(
            provider_endpoint(role),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                value = json.loads(resp.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("provider returned a non-object JSON body")
            logger.info("multipart layout attempt %d ok in %.1fs",
                        attempt, time.time() - t0)
            return value
        except Exception as exc:
            last_err = exc
            logger.warning("multipart provider attempt %d failed after %.1fs: %s",
                           attempt, time.time() - t0, exc)
            if attempt == ATTEMPTS:
                with _HTTP_STATS_LOCK:
                    _HTTP_STATS["failures"] += 1
    raise OCRError(f"multipart visual provider failed after {ATTEMPTS} attempts: {last_err}")
