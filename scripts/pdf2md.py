#!/usr/bin/env python3
"""Convert a scanned PDF into a Markdown file.

Uses the PDF's embedded text layer when one exists (searchable scanned
PDFs); otherwise renders each page and falls back to Tesseract OCR.

Usage:
    python pdf2md.py input.pdf [-o output.md] [--lang eng] [--dpi 300]
                               [--force-ocr] [--save-images DIR]

Requires: pymupdf (pip install pymupdf), and the tesseract binary
(e.g. `apt install tesseract-ocr` or `brew install tesseract`) when OCR
is needed. If tesseract is not on PATH, set the TESSERACT_CMD
environment variable to its full path.
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
except ImportError:
    sys.exit("Missing dependency: run `pip install pytesseract`")

MIN_TEXT_CHARS = 30


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


def lines_from_ocr(data):
    """Normalize Tesseract word data into generic line records."""
    lines = {}
    for i in range(len(data["text"])):
        word = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not word or conf < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        top, height = data["top"][i], data["height"][i]
        entry = lines.setdefault(
            key,
            {"words": [], "top": top, "bottom": top + height, "height": height, "left": data["left"][i]},
        )
        entry["words"].append(word)
        entry["top"] = min(entry["top"], top)
        entry["bottom"] = max(entry["bottom"], top + height)
        entry["height"] = max(entry["height"], height)
        entry["left"] = min(entry["left"], data["left"][i])
    result = []
    for key in sorted(lines):
        e = lines[key]
        result.append({
            "text": " ".join(e["words"]),
            "top": e["top"],
            "bottom": e["bottom"],
            "height": e["height"],
            "left": e["left"],
            "bold": False,
        })
    result.sort(key=lambda l: l["top"])
    return result


def lines_from_text_layer(page):
    """Normalize the embedded text layer into generic line records.
    Coordinates are converted from points to 300-DPI pixel scale so the
    same layout heuristics work for both sources."""
    scale = 300 / 72
    raw = page.get_text("dict")
    lines = []
    for block in raw["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            size = max(s["size"] for s in spans)
            bold = all(bool(s["flags"] & 2) or "bold" in s["font"].lower() for s in spans)
            x0, y0, x1, y1 = line["bbox"]
            lines.append({
                "text": text,
                "top": y0 * scale,
                "bottom": y1 * scale,
                "height": (y1 - y0) * scale,
                "left": x0 * scale,
                "bold": bold,
                "size": size,
            })
    lines.sort(key=lambda l: l["top"])
    return lines


GUTTER_RE = re.compile(r"^(\d{1,3})[.)]?$")
BULLET_RE = re.compile(r"^([•▪◦‣·\-–*eo©])\s+(.+)$")
BULLET_WORD_RE = re.compile(r"^(e|o|©)\s+([A-Za-z].*)$")
NUMBERED_RE = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")


def detect_gutters(lines):
    """Find left-gutter line numbers: a vertical run of >=3 pure-number
    lines at nearly the same left edge. Returns a set of line indices."""
    gutter_idx = set()
    candidate_cols = {}
    for i, ln in enumerate(lines):
        if GUTTER_RE.match(ln["text"]):
            candidate_cols.setdefault(round(ln["left"] / 10), []).append(i)
    for col, idxs in candidate_cols.items():
        if len(idxs) >= 3:
            gutter_idx.update(idxs)
    return gutter_idx


def group_into_paragraphs(lines, median_height):
    """Split line sequence into blocks on vertical gaps."""
    blocks = []
    current = []
    prev = None
    for ln in lines:
        if prev is not None and ln["top"] - prev["bottom"] > 1.2 * median_height:
            if current:
                blocks.append(current)
            current = []
        current.append(ln)
        prev = ln
    if current:
        blocks.append(current)
    return blocks


def strip_gutter_prefix(text, gutter_numbers):
    """Remove a leading gutter number like '12 ' from a code line."""
    for num in gutter_numbers:
        if text.startswith(num + " ") and len(text) > len(num) + 1:
            return text[len(num) + 1:]
    return text


def render_block(block, median_height, max_size):
    """Convert one block of lines into Markdown."""
    texts = [l["text"].strip() for l in block]
    first = block[0]
    text = " ".join(texts).strip()

    if not text:
        return None

    is_single = len(block) == 1
    big = first.get("size", 0) >= max_size * 0.98 or first["height"] >= 1.15 * median_height
    bold = first.get("bold", False)

    m = NUMBERED_RE.match(text) if is_single and (big or bold) else None
    if m and len(text) < 100:
        return f"{text}"

    if is_single and (big or bold) and len(text) < 100 and not text.endswith((".", ",", ";", ":")):
        return f"## {text}"

    if len(texts) >= 2 and all(
        BULLET_RE.match(t) or BULLET_WORD_RE.match(t) for t in texts if t
    ):
        items = []
        for t in texts:
            m = BULLET_RE.match(t) or BULLET_WORD_RE.match(t)
            if m:
                items.append(f"- {m.group(2).strip()}")
        return "\n".join(items)

    if len(texts) > 1 and all(NUMBERED_RE.match(t) for t in texts if t):
        items = [NUMBERED_RE.match(t).group(0) for t in texts if t]
        return "\n".join(items)

    return text


def looks_like_code(lines, gutter_idx):
    """A block is code if it contains gutter-number lines, or if most of
    its lines look code-ish (braces, brackets, operators, keywords)."""
    if any(GUTTER_RE.match(l["text"]) for l in lines):
        return True
    codey = 0
    for l in lines:
        t = l["text"]
        if re.search(r"[{}\[\]=<>;]|^def |^class |^import |if\b.*:$|\"[a-z_0-9]+\":|^\)", t):
            codey += 1
    return codey >= max(2, len(lines) // 2 + 1)


def strip_inline_gutters(lines):
    """OCR often merges left-margin code line numbers into line text
    (e.g. '1 PDF'). If >=3 lines start with a short bare number, treat it
    as a gutter and strip the number."""
    pat = re.compile(r"^(\d{1,3})[.)]?\s+(\S.*)$")
    hits = sum(1 for l in lines if pat.match(l["text"]))
    if hits < 3:
        return lines
    out = []
    for l in lines:
        m = pat.match(l["text"])
        if m:
            l = dict(l, text=m.group(2))
        out.append(l)
    return out


ASCII_ART_RE = re.compile(r"[|\u2500-\u257f\u2190-\u21ff]|Vv|L_|L\.|\|-|-\||\+-")


def looks_like_ascii_art(block):
    """Detect vertical flowcharts / box diagrams made of pipes and arrows."""
    if len(block) < 3:
        return False
    artsy = 0
    for l in block:
        if ASCII_ART_RE.search(l["text"]):
            artsy += 1
    return artsy >= len(block) // 2


def page_to_markdown(lines, force_biggest=None):
    if not lines:
        return ""
    heights = sorted(l["height"] for l in lines)
    median_height = heights[len(heights) // 2] or 1
    sizes = sorted(l.get("size", 0) for l in lines if l.get("size"))
    max_size = (sizes[-1] if sizes else 0) or (median_height / 4.2)

    gutter_idx = detect_gutters(lines)
    gutter_numbers = {lines[i]["text"].rstrip(".)") for i in gutter_idx}
    content_lines = [l for i, l in enumerate(lines) if i not in gutter_idx]
    if not content_lines:
        content_lines = lines
    content_lines = strip_inline_gutters(content_lines)

    blocks = group_into_paragraphs(content_lines, median_height)

    out = []
    for block in blocks:
        if looks_like_code(block, gutter_idx) or looks_like_ascii_art(block):
            code = []
            for l in block:
                t = strip_gutter_prefix(l["text"].strip(), gutter_numbers)
                if GUTTER_RE.match(t):
                    continue
                if t:
                    code.append(t)
            if code:
                out.append("```\n" + "\n".join(code) + "\n```")
        else:
            md = render_block(block, median_height, max_size)
            if md:
                out.append(md)
    return "\n\n".join(out)


def convert(pdf_path, out_path, lang, dpi, prefer_text_layer, save_images):
    if not os.path.isfile(pdf_path):
        sys.exit(f"File not found: {pdf_path}")
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        sys.exit(f"Cannot open PDF: {e}")

    parts = [f"# {os.path.splitext(os.path.basename(pdf_path))[0]}", ""]
    for pno, page in enumerate(doc, start=1):
        print(f"Processing page {pno}/{len(doc)}...", file=sys.stderr)
        used_text_layer = False
        if prefer_text_layer:
            lines = lines_from_text_layer(page)
            if sum(len(l["text"]) for l in lines) >= MIN_TEXT_CHARS:
                used_text_layer = True
            else:
                lines = []
        if not used_text_layer:
            lines = lines_from_ocr(ocr_page(page, dpi, lang))
            src = "OCR"
        else:
            src = "text layer"

        text = page_to_markdown(lines)
        parts.append(f"<!-- page {pno} ({src}) -->\n")
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
    ap.add_argument("--prefer-text-layer", action="store_true",
                    help="Use the PDF's embedded text layer when present instead of OCR. "
                         "Caution: scanned PDFs often embed a low-quality OCR layer.")
    ap.add_argument("--save-images", metavar="DIR", help="Also save page images to DIR")
    args = ap.parse_args()

    if os.environ.get("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

    out = args.output or os.path.splitext(args.pdf)[0] + ".md"
    convert(args.pdf, out, args.lang, args.dpi, args.prefer_text_layer, args.save_images)


if __name__ == "__main__":
    main()
