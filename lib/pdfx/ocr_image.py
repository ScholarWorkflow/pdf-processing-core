"""OCR one rendered page image with pdfx's shared provider configuration."""
from __future__ import annotations

import argparse
from pathlib import Path

from pdfx.ocr_qwen_vl import ocr_png


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--ocr", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    print(ocr_png(args.image.read_bytes()))


if __name__ == "__main__":
    main()
