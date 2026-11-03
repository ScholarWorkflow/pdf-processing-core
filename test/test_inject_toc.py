from __future__ import annotations

import sys
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def test_same_page_entries_keep_toc_reading_order(tmp_path, monkeypatch):
    from pdfx import inject_toc, toc_llm

    expected = [
        [1, "第1章 入门", 2],
        [2, "1.1 观察", 2],
        [1, "第2章 方法", 3],
        [2, "2.1 比较", 3],
    ]
    page_texts = [
        "目录\n第1章 入门 ...... 2\n1.1 观察 ...... 2\n"
        "第2章 方法 ...... 3\n2.1 比较 ...... 3",
        "第1章 入门\n1.1 观察",
        "第2章 方法\n2.1 比较",
    ]
    pdf_path = tmp_path / "same-page-chapters.pdf"
    with fitz.open() as document:
        for text in page_texts:
            page = document.new_page()
            page.insert_text((72, 72), text, fontname="china-s")
        document.save(pdf_path)

    parsed_entries = [
        {"level": level, "title": title, "printed": page,
         "low_confidence": False, "_src_page": 1}
        for level, title, page in expected
    ]
    monkeypatch.setattr(
        inject_toc, "extract_pdf",
        lambda *args, **kwargs: {
            "page_texts": [{"text": text} for text in page_texts],
        },
    )
    monkeypatch.setattr(
        toc_llm, "parse_toc_pages",
        lambda *args, **kwargs: ({1: parsed_entries}, {"entries": 4}),
    )
    monkeypatch.setattr(
        inject_toc.ocr_apple, "ocr_page",
        lambda page, **kwargs: page.get_text(),
    )

    inject_toc.run(str(pdf_path))

    with fitz.open(pdf_path) as document:
        assert document.get_toc() == expected
