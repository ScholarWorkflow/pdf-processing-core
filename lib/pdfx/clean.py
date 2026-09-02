"""Cleaning for "washable" pages: strip spaces between Han characters, join broken lines.

Conservative by design: only touches whitespace sandwiched between two Han
ideographs (the ABBYY/OCR signature), never Latin words or numbers.
"""

from __future__ import annotations

import re

_HAN = r"\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
_KANA = r"\u3040-\u30FF"

RE_SPACED_BETWEEN = re.compile(rf"(?<=[{_HAN}])[ \t\u00a0\u3000]+(?=[{_HAN}])")
RE_HAN_OR_KANA_END = re.compile(rf"[{_HAN}{_KANA}，、：；]$")
RE_HAN_OR_KANA_START = re.compile(rf"^[{_HAN}{_KANA}]")


def strip_cjk_spaces(text: str) -> str:
    return RE_SPACED_BETWEEN.sub("", text)


def join_broken_lines(text: str) -> str:
    lines = text.split("\n")
    out = []
    for ln in lines:
        if (
            out
            and out[-1]
            and ln
            and not out[-1].endswith(("。", "！", "？", ".", "!", "?", ":", "；", ";", "」", "』", "）", ")"))
            and RE_HAN_OR_KANA_END.search(out[-1])
            and RE_HAN_OR_KANA_START.match(ln)
        ):
            out[-1] += ln
        else:
            out.append(ln)
    return "\n".join(out)


def clean_text(text: str, join_lines: bool = True) -> str:
    cleaned = strip_cjk_spaces(text)
    if join_lines:
        cleaned = join_broken_lines(cleaned)
    return cleaned
