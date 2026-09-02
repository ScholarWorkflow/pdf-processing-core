"""Small, shared formula-check cache for offline consumers."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CACHE_SCHEMA = "formula-check-cache/1"
ENTRY_FIELDS = frozenset({"fingerprint", "verdict", "pdf", "checked_at", "reason"})
VERDICTS = frozenset({"ok", "suspect", "unverified", "pending_l3", "empty", "no_sidecar", "degraded", "error"})

class FormulaCacheError(ValueError):
    pass

class FormulaCheckBlocked(FormulaCacheError):
    pass

def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _section_pdf_candidates(path: Path) -> list[Path]:
    """Find derived PDFs whose stem identifies the section directory.

    The resolver deliberately searches only nearby artifact roots. It avoids
    requiring a particular project layout while retaining the conventional
    section-directory suffix matching used by existing consumers.
    """
    section = path.parent.name
    candidates: set[Path] = set()

    def add_from(root: Path) -> None:
        if not root.is_dir():
            return
        try:
            for candidate in root.rglob("*.pdf"):
                if candidate.stem == section or candidate.stem.endswith(f"_{section}"):
                    candidates.add(candidate.resolve())
        except OSError:
            return

    # The split directory is an explicit artifact root, so searching within it
    # is bounded and works for both flat and chapter-organized split layouts.
    for ancestor in list(path.parents)[:6]:
        add_from(ancestor / "split_pdfs")

    roots = [path.parent]
    if path.parent.parent != path.parent:
        roots.append(path.parent.parent)
    if path.parent.parent.parent != path.parent.parent:
        roots.append(path.parent.parent.parent)
    for root in roots:
        try:
            for candidate in root.glob("*.pdf"):
                if candidate.stem == section or candidate.stem.endswith(f"_{section}"):
                    candidates.add(candidate.resolve())
            for child in root.iterdir():
                if child.is_dir():
                    for candidate in child.glob("*.pdf"):
                        if candidate.stem == section or candidate.stem.endswith(f"_{section}"):
                            candidates.add(candidate.resolve())
        except OSError:
            continue
    return sorted(candidates)

def canonicalize_source(source: str | Path) -> Path:
    path = _absolute(source)
    if path.suffix.lower() == ".pdf":
        pdf = path
    elif path.name == "text.md":
        matches = _section_pdf_candidates(path)
        if len(matches) != 1:
            raise FormulaCacheError(f"cannot resolve text.md to one split PDF: {path}")
        pdf = matches[0]
    else:
        pdf = path.with_suffix(".pdf")
    if not pdf.is_file():
        raise FormulaCacheError(f"formula-check PDF not found: {pdf}")
    return pdf.resolve()

def source_fingerprint(source: str | Path) -> str:
    digest = hashlib.sha256()
    with canonicalize_source(source).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def empty_cache() -> dict[str, Any]:
    return {"schema": CACHE_SCHEMA, "entries": {}}

def _short_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, Mapping) or not ENTRY_FIELDS.issuperset(entry.keys()):
        raise FormulaCacheError("formula-check cache entry contains non-short fields")
    required = ("fingerprint", "verdict", "pdf", "checked_at")
    if any(not isinstance(entry.get(k), str) or not entry.get(k) for k in required):
        raise FormulaCacheError("formula-check cache entry has missing required fields")
    if entry["verdict"] not in VERDICTS:
        raise FormulaCacheError(f"unknown formula-check verdict: {entry['verdict']}")
    return {k: entry[k] for k in required} | ({"reason": entry["reason"]} if entry.get("reason") else {})

def _entries(cache: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not cache: return {}
    raw = cache.get("entries") if "entries" in cache else cache
    if not isinstance(raw, Mapping): raise FormulaCacheError("formula-check cache entries must be an object")
    return {str(k): _short_entry(v) for k, v in raw.items()}

def load_cache(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists(): return empty_cache()
    try: value = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise FormulaCacheError(f"invalid formula-check cache: {p}") from exc
    _entries(value)
    return value if "entries" in value else {"schema": CACHE_SCHEMA, "entries": value}

def validate_short_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping) or result.get("verdict") not in VERDICTS:
        raise FormulaCacheError("formula-check result must contain a valid verdict")
    return {"verdict": result["verdict"], "reason": result.get("reason", ""), "pdf": result.get("pdf", "")}

def record(cache, source, result, *, checked_at=None):
    compact = validate_short_result(result); pdf = canonicalize_source(source)
    if compact["pdf"] and canonicalize_source(compact["pdf"]) != pdf: raise FormulaCacheError("formula-check result PDF does not match source")
    fp = source_fingerprint(pdf)
    if result.get("fingerprint") and result["fingerprint"] != fp: raise FormulaCacheError("formula-check result fingerprint is stale")
    entry = {"fingerprint": fp, "verdict": compact["verdict"], "pdf": str(pdf), "checked_at": checked_at or result.get("checked_at") or datetime.now(timezone.utc).isoformat()}
    if compact["reason"]: entry["reason"] = compact["reason"]
    entries = _entries(cache); entries[str(pdf)] = _short_entry(entry); return entries

def merge(existing, incoming):
    entries = _entries(existing)
    for source, result in (incoming or {}).items(): entries = record(entries, source, result)
    return entries

def lookup(cache, source):
    try:
        pdf = canonicalize_source(source); fp = source_fingerprint(pdf); entry = _entries(cache).get(str(pdf))
        if entry is None: return None
        short = _short_entry(entry)
    except (FormulaCacheError, TypeError): return None
    return short if short["pdf"] == str(pdf) and short["fingerprint"] == fp else None

def require_ok(cache, source):
    entry = lookup(cache, source)
    if entry is None or entry["verdict"] != "ok": raise FormulaCheckBlocked(f"formula-check blocked: {source}")
    return entry

def write_cache(path, source, result):
    p = Path(path); payload = {"schema": CACHE_SCHEMA, "entries": record(load_cache(p), source, result)}; p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle: json.dump(payload, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return payload
