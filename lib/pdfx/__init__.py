"""pdfx — unified PDF text extraction toolkit."""

__version__ = "0.1.0"

try:
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
except Exception:
    pass
