from __future__ import annotations

import sys
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def test_text_source_resolves_explicit_split_pdf_sibling(tmp_path):
    source = tmp_path / "book" / "extraction" / "chapter" / "section" / "text.md"
    split_pdf = tmp_path / "book" / "split_pdfs" / "book_section.pdf"
    source.parent.mkdir(parents=True)
    split_pdf.parent.mkdir(parents=True)
    source.write_text("text", encoding="utf-8")
    split_pdf.write_bytes(b"%PDF-1.7")

    from pdfx.formula_check_cache import canonicalize_source

    assert canonicalize_source(source) == split_pdf.resolve()


def test_extract_uses_stable_physical_page_markers(tmp_path):
    pdf = tmp_path / "one-page.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "page one")
    document.save(pdf)
    document.close()

    from pdfx.extract import extract_pdf

    result = extract_pdf(str(pdf), strategy="fast")

    assert result["text"].startswith("<!-- PDF_PAGE: 1 -->\n\n")
