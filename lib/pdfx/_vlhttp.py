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

_HTTP_STATS = {"requests": 0, "retries": 0, "timeouts": 0, "failures": 0,
               "shared_calls": 0, "shared_failures": 0}
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


_RUNTIME_LOCK = threading.Lock()
_RUNTIME_LOADED = False


def _vision_runtime(required: bool = False):
    """Load the shared client's local configuration once per process.

    Import stays optional for existing non-Nix text/native and PDFX endpoint
    users. Nix supplies this module in the same Python environment as pdfx.
    """
    global _RUNTIME_LOADED
    try:
        import vision_client
    except ImportError as exc:
        if required:
            raise OCRError(
                "shared vision runtime is unavailable; install agent-vision-runtime "
                "or use the Nix pdfx package"
            ) from exc
        return None
    with _RUNTIME_LOCK:
        if not _RUNTIME_LOADED:
            vision_client.load_default_env()
            _RUNTIME_LOADED = True
    return vision_client


def _endpoint_override(role: str) -> str:
    _vision_runtime()
    return (os.environ.get(_env_name(role, "ENDPOINT"), "").strip()
            or os.environ.get("PDFX_VISION_ENDPOINT", "").strip())


def _shared_chain(role: str, model: str | None = None) -> list[dict]:
    client = _vision_runtime(required=True)
    try:
        chain = client.load_model_chain()
    except (client.VisionError, ValueError, TypeError) as exc:
        raise OCRError(str(exc)) from exc
    override = (os.environ.get(_env_name(role, "MODEL"), "").strip()
                or os.environ.get("PDFX_VISION_MODEL", "").strip())
    if override:
        selected = [entry for entry in chain if override in (entry["name"], entry["model"])]
        chain = selected or [dict(chain[0], model=override)]
    # A chain name identifies one deployment even when multiple deployments
    # serve the same model. Prefer that identity over a model-name match.
    selected = [entry for entry in chain if model == entry["name"]]
    if not selected:
        selected = [entry for entry in chain if model == entry["model"]]
    if not selected and model and os.environ.get("PDFX_VISION_CHAIN", "").strip():
        return [dict(chain[0], name=model, model=model)]
    return selected or chain


def provider_endpoint(role: str = "vision") -> str:
    """Return a configured provider endpoint or raise a clear OCR error."""
    endpoint = _endpoint_override(role)
    if not endpoint:
        raise OCRError(
            f"visual provider endpoint is not configured; set "
            f"{_env_name(role, 'ENDPOINT')} or PDFX_VISION_ENDPOINT"
        )
    return endpoint


def provider_model(role: str, default: str | None = None) -> str:
    """Resolve a model identifier from role-specific provider configuration."""
    _vision_runtime()
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
    _vision_runtime()
    if not _endpoint_override(role) and not os.environ.get("PDFX_VISION_CHAIN", "").strip():
        try:
            return [entry["name"] for entry in _shared_chain(role)
                    if not structured or entry.get("structured", True) is not False]
        except OCRError:
            return []
    raw = (os.environ.get("PDFX_VISION_CHAIN", "").strip()
           or os.environ.get("VISION_CHAIN", "").strip())
    if not raw:
        if not _endpoint_override(role):
            try:
                return [entry["name"] for entry in _shared_chain(role)]
            except OCRError:
                return []
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
    if not _endpoint_override(role):
        # Resolve legacy model selectors to the shared deployment identity
        # before filtering capabilities. The request must use that same entry.
        try:
            return list(dict.fromkeys(
                entry["name"]
                for model in models for entry in _shared_chain(role, model)
                if not structured or entry.get("structured", True) is not False
            ))
        except OCRError:
            return []
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
    if not _endpoint_override(role):
        if role == "layout" and not prompt and not os.environ.get("PDFX_LAYOUT_MODEL", "").strip():
            raise OCRError("dedicated layout OCR is not configured; use transcription fallback")
        client = _vision_runtime(required=True)
        chain = _shared_chain(role, model)
        # Shared client's retry/budget policy owns its HTTP attempts. Count
        # logical calls separately rather than presenting them as attempts.
        with _HTTP_STATS_LOCK:
            _HTTP_STATS["shared_calls"] += 1
        try:
            return client.describe_image(
                f"data:{image_mime};base64,{b64}", prompt, max_tokens,
                apply_lang=False, models=chain,
            )
        except client.VisionError as exc:
            with _HTTP_STATS_LOCK:
                _HTTP_STATS["shared_failures"] += 1
            raise OCRError(str(exc)) from exc
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
