#!/usr/bin/env python3
"""Smoke test: build a scanned-style PDF (rendered text image, no text layer),
then run pdf2md on it and check the OCR output."""
import os
import subprocess
import sys


import pymupdf

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "pdf2md.py")
TEST_PDF = "/tmp/sample_scan.pdf"
OUT_MD = "/tmp/sample_scan.md"

content = """# Invoice 42

1. First item with some words
2. Second item here

This paragraph has a few words to check paragraph assembly. It should
be joined into a single line of text.

## Subheading two

The quick brown fox jumps over the lazy dog.
"""


def build_pdf():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    top = 60
    for line in content.splitlines():
        if line.startswith("#"):
            size, text = 16, line.lstrip("# ").strip()
            if line.startswith("##"):
                size = 13
        elif line:
            size, text = 11, line
        else:
            top += 6
            continue
        page.insert_text((60, top), text, fontsize=size, fontname="helv")
        top += size + 10
    doc.save("/tmp/_text.pdf")
    doc.close()

    # Rasterize to simulate a scanned (image-only) document.
    src = pymupdf.open("/tmp/_text.pdf")
    pix = src[0].get_pixmap(dpi=300)
    scan = pymupdf.open()
    page = scan.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    scan.save(TEST_PDF)
    scan.close()
    src.close()


def verify_no_text_layer():
    doc = pymupdf.open(TEST_PDF)
    text = doc[0].get_text().strip()
    doc.close()
    assert not text, f"Test PDF unexpectedly has a text layer: {text!r}"
    print("OK: test PDF is image-only style (no extractable text layer)")


def run_script():
    env = dict(os.environ)
    env["TESSERACT_CMD"] = env.get("TESSERACT_CMD", "/tmp/squashfs-root/usr/bin/tesseract")
    subprocess.run(
        [sys.executable, SCRIPT, TEST_PDF, "-o", OUT_MD],
        check=True,
        env=env,
    )
    assert os.path.isfile(OUT_MD), "Output markdown missing"


def check_output():
    with open(OUT_MD, encoding="utf-8") as f:
        md = f.read()
    print("---- markdown output ----")
    print(md)
    print("---- end ----")
    for needle in ["Invoice 42", "quick brown fox", "Subheading"]:
        assert needle.lower() in md.lower(), f"Missing expected text: {needle}"
    print("OK: OCR output contains expected content")


if __name__ == "__main__":
    build_pdf()
    verify_no_text_layer()
    run_script()
    check_output()
    print("SMOKE TEST PASSED")
