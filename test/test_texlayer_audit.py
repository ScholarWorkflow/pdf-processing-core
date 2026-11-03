from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def test_sparse_section_audit_keeps_physical_pages():
    from pdfx import texlayer_audit

    sections = [{
        "title": "Sparse section",
        "path": Path("section/text.md"),
        "pages": [40, 41, 42],
    }]
    done_pages = {
        41: {"verdict": "suspect"},
        42: {"verdict": "trusted"},
    }

    [section], repair_estimate_seconds = texlayer_audit._aggregate_sections(
        sections, done_pages,
    )

    assert section["audited"] == 2
    assert section["suspect_pages"] == [41]
    assert section["suspect_ratio"] == 1 / 2
    assert section["auto_repair"] is True
    assert repair_estimate_seconds == texlayer_audit.REPAIR_EST_SEC_PER_PAGE
