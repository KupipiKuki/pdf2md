#!/usr/bin/env python3
"""Convert a scanned PDF into a Markdown file using OCR.

Usage:
    python pdf2md.py input.pdf [-o output.md] [--lang eng] [--dpi 300]
                               [--save-images dir]

Requires: pymupdf (pip install pymupdf), and the tesseract binary
(e.g. `apt install tesseract-ocr` or `brew install tesseract`).
If tesseract is not on PATH, set the TESSERACT_CMD environment variable
to its full path.
"""

import argparse
import os
import re
import sys
import tempfile

try:
    import pymupdf
except ImportError:
    sys.exit("Missing dependency: run `pip install pymupdf`")
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except ImportError:
    sys.exit("Missing dependency: run `pip install pytesseract`")


def render_page(page, dpi):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return pix.tobytes("png")


def ocr_page(page, dpi, lang):
    image_bytes = render_page(page, dpi)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        data = pytesseract.image_to_data(
            tmp_path,
            lang=lang,
            config="--psm 3",
            output_type=pytesseract.Output.DICT,
        )
    finally:
        os.unlink(tmp_path)
    return data


HEADING_RE = re.compile(r"^\s*(\d+(\.\d+)*)[.\s]\s+(.+)$")


def blocks_to_markdown(data):
    """Assemble OCR words into lines, merge lines into paragraphs using
    vertical gaps, and promote short large-font lines to headings."""
    n = len(data["text"])
    lines = {}
    for i in range(n):
        word = (data["text"][i] or "").strip()
        conf = float(data["conf"][i]) if data["conf"][i] != "-1" else -1.0
        if not word or conf < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        top, height = data["top"][i], data["height"][i]
        entry = lines.setdefault(key, {"words": [], "top": top, "bottom": top + height, "height": height})
        entry["words"].append(word)
        entry["top"] = min(entry["top"], top)
        entry["bottom"] = max(entry["bottom"], top + height)
        entry["height"] = max(entry["height"], height)

    if not lines:
        return ""

    ordered = [dict(lines[k], text=" ".join(lines[k]["words"])) for k in sorted(lines)]
    ordered.sort(key=lambda l: l["top"])

    heights = sorted(l["height"] for l in ordered)
    median_height = heights[len(heights) // 2] or 1

    md = []
    current_par = []
    prev = None
    for line in ordered:
        if prev is not None:
            gap = line["top"] - prev["bottom"]
            new_par = gap > 1.2 * median_height
            if new_par and current_par:
                md.append(flush_paragraph(current_par, median_height))
                current_par = []
        current_par.append(line)
        prev = line
    if current_par:
        md.append(flush_paragraph(current_par, median_height))
    return "\n\n".join(p for p in md if p)


def flush_paragraph(par_lines, median_height):
    """Join lines into one paragraph; single short large-font lines become headings."""
    first = par_lines[0]
    text = " ".join(l["text"] for l in par_lines).strip()
    is_single_line = len(par_lines) == 1
    looks_big = first["height"] >= 1.15 * median_height
    m = HEADING_RE.match(text) if is_single_line and looks_big else None
    if m:
        depth = min(m.group(1).count(".") + 1, 6)
        return f"{'#' * depth} {text}"
    if is_single_line and looks_big and len(text) < 80:
        return f"## {text}"
    return text


def convert(pdf_path, out_path, lang, dpi, save_images):
    if not os.path.isfile(pdf_path):
        sys.exit(f"File not found: {pdf_path}")
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        sys.exit(f"Cannot open PDF: {e}")

    parts = [f"# {os.path.splitext(os.path.basename(pdf_path))[0]}", ""]
    for pno, page in enumerate(doc, start=1):
        print(f"OCR page {pno}/{len(doc)}...", file=sys.stderr)
        data = ocr_page(page, dpi, lang)
        text = blocks_to_markdown(data)
        parts.append(f"<!-- page {pno} -->\n")
        if text:
            parts.append(text)
        else:
            parts.append("*(no text detected on this page)*")
        if save_images:
            img_path = os.path.join(save_images, f"page-{pno:04d}.png")
            os.makedirs(save_images, exist_ok=True)
            with open(img_path, "wb") as f:
                f.write(render_page(page, dpi))
            parts.append(f"\n![page {pno}]({img_path})")
        parts.append("")
    doc.close()

    markdown = "\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown + "\n")
    print(f"Wrote {out_path} ({len(markdown)} chars)", file=sys.stderr)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Convert a scanned PDF to Markdown via OCR.")
    ap.add_argument("pdf", help="Input PDF path")
    ap.add_argument("-o", "--output", help="Output Markdown path (default: input .md)")
    ap.add_argument("--lang", default="eng", help="Tesseract language (default: eng)")
    ap.add_argument("--dpi", type=int, default=300, help="Render DPI (default: 300)")
    ap.add_argument("--save-images", metavar="DIR", help="Also save page images to DIR")
    args = ap.parse_args()

    if os.environ.get("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

    out = args.output or os.path.splitext(args.pdf)[0] + ".md"
    convert(args.pdf, out, args.lang, args.dpi, args.save_images)


if __name__ == "__main__":
    main()
