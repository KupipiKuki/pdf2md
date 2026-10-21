#!/usr/bin/env python3
"""Convert a scanned PDF into a structured Markdown file.

Uses the PDF's embedded text layer when requested (--prefer-text-layer);
otherwise renders each page, detects rectangular bordered regions (code/diagrams)
for uniform block OCR (PSM 6), and performs layout-aware OCR (PSM 3) on the prose.
Heuristically recovers structure: headings, lists, code blocks with language
estimation, and converts ASCII/box diagrams into Mermaid diagrams.

Usage:
    python pdf2md.py input.pdf [-o output.md] [--lang eng] [--dpi 300]
                               [--prefer-text-layer] [--save-images DIR]
                               [--no-mermaid]

Requires: pymupdf, pytesseract, numpy, and the tesseract binary.
If tesseract is not on PATH, set the TESSERACT_CMD environment variable.
"""

import argparse
import io
import json
import os
import re
import shutil
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
try:
    import numpy
except ImportError:
    sys.exit("Missing dependency: run `pip install numpy`")

# ---------------------------------------------------------------------------
# Centralized Heuristic Constants
# ---------------------------------------------------------------------------
MIN_TEXT_CHARS = 30
BOX_BORDER_PAD = 8
BOX_MIN_W_FRAC = 0.15
BOX_MIN_H = 35
BOX_MAX_GAP = 4
BOX_TOLERANCE_PX = 85
PARAGRAPH_GAP_RATIO = 1.2
HEADING_HEIGHT_RATIO = 1.15
OCR_CONFIDENCE_THRESHOLD = 30


# ---------------------------------------------------------------------------
# Rendering & OCR Extraction
# ---------------------------------------------------------------------------
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
        if os.path.exists(tmp_path):
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
        if not word or conf < OCR_CONFIDENCE_THRESHOLD:
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


# ---------------------------------------------------------------------------
# Decoupled Heuristics & Layout Normalization (imported from docling_engineering)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from docling_engineering.heuristics import (
    estimate_code_language,
    math_chain_to_mermaid,
    recover_numeric_misread,
    parse_ascii_diagram_to_mermaid,
    parse_ascii_diagram_block,
    detect_gutters,
    group_into_paragraphs,
    strip_gutter_prefix,
    clean_block_lines,
    render_block,
    looks_like_list,
    looks_like_code,
    _snap_indents,
    reconstruct_indented_lines,
    clean_box_text,
    render_box_cleaned,
    box_gutter_range,
    boxes_continuing,
    strip_title_noise,
    repair_prose_pipe,
    repair_code_line_punct,
    HEADING_CUE_RE,
    GUTTER_RE,
    BULLET_RE,
    BULLET_WORD_RE,
    NUMBERED_RE,
    ASCII_ART_RE,
    MATH_TOKEN_RE,
    BOX_CONT_X_OVERLAP,
    BOX_CONT_GUTTER_GAP,
)


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


def looks_like_ascii_art(block):
    """Detect vertical flowcharts / box diagrams made of pipes and arrows."""
    if len(block) < 3:
        return False
    artsy = 0
    for l in block:
        if ASCII_ART_RE.search(l["text"]):
            artsy += 1
    return artsy >= len(block) // 2


def page_to_markdown(lines, force_biggest=None, use_mermaid=True):
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
        if looks_like_ascii_art(block) and use_mermaid:
            raw_lines = [l["text"].strip() for l in block if l["text"].strip()]
            is_diag, diagram_block = parse_ascii_diagram_block(raw_lines)
            if is_diag:
                out.append(diagram_block)
                continue

        if looks_like_code(block, gutter_idx) or looks_like_ascii_art(block):
            code = []
            for l in block:
                t = strip_gutter_prefix(l["text"].strip(), gutter_numbers)
                if GUTTER_RE.match(t):
                    continue
                if t:
                    code.append(t)
            if code:
                code_text = "\n".join(code)
                lang = estimate_code_language(code_text)
                fence = f"```{lang}" if lang else "```"
                out.append(f"{fence}\n{code_text}\n```")
        else:
            md = render_block(block, median_height, max_size)
            if md:
                out.append(md)
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# Robust Bordered Box Region Pass
# ---------------------------------------------------------------------------
def check_vstrip_continuous(dark, y1, y2, x_center, half_w=10, max_break=30):
    """Verify that a vertical column strip has a continuous dark border run,
    tolerating slight scan rotation and minute sensor breaks."""
    H, W = dark.shape
    y_start = max(0, int(y1))
    y_end = min(H, int(y2))
    if y_end <= y_start + 20:
        return False
    x_start = max(0, int(x_center - half_w))
    x_end = min(W, int(x_center + half_w + 1))
    strip = dark[y_start:y_end, x_start:x_end]
    has_pixel = numpy.any(strip, axis=1)
    if numpy.mean(has_pixel) < 0.70:
        return False
    gap = 0
    for p in has_pixel:
        if not p:
            gap += 1
            if gap > max_break:
                return False
        else:
            gap = 0
    return True


def find_boxes(dark, min_w_frac=BOX_MIN_W_FRAC, min_h=BOX_MIN_H, max_gap=BOX_MAX_GAP):
    """Detect rectangular bordered regions (code boxes, boxed diagrams)
    in a binary dark-pixel mask, robust against slight scan skew and noise.
    Returns (y0, y1, x0, x1) tuples."""
    H, W = dark.shape

    def hline_runs(y):
        row = dark[y]
        runs = []
        x = 0
        while x < W:
            if row[x]:
                s = x
                while x < W:
                    if row[x]:
                        x += 1
                    else:
                        gap = 1
                        while gap <= max_gap and x + gap < W and not row[x + gap]:
                            gap += 1
                        if gap <= max_gap and x + gap < W and row[x + gap]:
                            x += gap
                        else:
                            break
                if x - s > W * min_w_frac:
                    runs.append((s, x))
            else:
                x += 1
        return runs

    segs = []
    for y in range(0, H, 2):
        for s, e in hline_runs(y):
            segs.append((y, s, e))

    merged = []
    used = [False] * len(segs)
    segs.sort()
    for i, (y, s, e) in enumerate(segs):
        if used[i]:
            continue
        ys = [y]
        s0, e0 = s, e
        used[i] = True
        for j in range(i + 1, len(segs)):
            if used[j]:
                continue
            y2, s2, e2 = segs[j]
            if y2 <= max(ys) + 8:
                overlap = max(0, min(e0, e2) - max(s0, s2))
                adjacent = (abs(s2 - e0) <= 25 or abs(s0 - e2) <= 25)
                if overlap > 0 or adjacent:
                    used[j] = True
                    ys.append(y2)
                    s0 = min(s0, s2)
                    e0 = max(e0, e2)
        merged.append((sum(ys) / len(ys), s0, e0))

    stitched = []
    used_m = [False] * len(merged)
    merged.sort(key=lambda m: (m[1], m[0]))
    for i, (y, s, e) in enumerate(merged):
        if used_m[i]:
            continue
        cur_y, cur_s, cur_e = y, s, e
        used_m[i] = True
        for j in range(len(merged)):
            if used_m[j]:
                continue
            y2, s2, e2 = merged[j]
            if abs(y2 - cur_y) <= 12 and (cur_s - 40 <= e2 and s2 <= cur_e + 40):
                used_m[j] = True
                cur_y = (cur_y + y2) / 2
                cur_s = min(cur_s, s2)
                cur_e = max(cur_e, e2)
        stitched.append((cur_y, cur_s, cur_e))

    boxes = []
    for y, s, e in stitched:
        if e - s < W * 0.35:
            continue
        for y2, s2, e2 in stitched:
            if y2 - y > min_h and abs(y2 - y) < H:
                # Pair borders by horizontal overlap rather than exact
                # endpoint equality: skewed or corner-eroded scans shift
                # the detected segment ends between top and bottom.
                overlap = max(0, min(e, e2) - max(s, s2))
                if overlap < 0.5 * min(e - s, e2 - s2):
                    continue
                box_s = min(s, s2)
                box_e = max(e, e2)
                lv = check_vstrip_continuous(dark, y, y2, box_s)
                rv = check_vstrip_continuous(dark, y, y2, box_e)
                if lv and rv:
                    boxes.append((y, y2, box_s, box_e))

    boxes.sort(key=lambda b: (b[0], -(b[1] - b[0])))
    final = []
    for b in boxes:
        if not any(b[0] >= f[0] - 10 and b[1] <= f[1] + 10 and
                   b[2] >= f[2] - 10 and b[3] <= f[3] + 10 for f in final):
            final.append(b)
    return final


OPEN_BOX_PAGE_EDGE = 40


def find_open_boxes(dark, boxes, min_w_frac=BOX_MIN_W_FRAC, max_gap=BOX_MAX_GAP):
    """Detect code-box regions cut off by the page boundary: a top border
    near the bottom page edge with no closing bottom border (open-bottom),
    or a bottom border near the top page edge with no opening top border
    (open-top). Returns open-bottom and open-top region lists of
    (y0, y1, x0, x1) tuples."""
    H, W = dark.shape

    def hline_runs(y):
        row = dark[y]
        runs = []
        x = 0
        while x < W:
            if row[x]:
                s = x
                while x < W:
                    if row[x]:
                        x += 1
                    else:
                        gap = 1
                        while gap <= max_gap and x + gap < W and not row[x + gap]:
                            gap += 1
                        if gap <= max_gap and x + gap < W and row[x + gap]:
                            x += gap
                        else:
                            break
                if x - s > W * min_w_frac:
                    runs.append((s, x))
            else:
                x += 1
        return runs

    def seg_yse():
        segs = []
        for y in range(0, H, 2):
            for s, e in hline_runs(y):
                segs.append((y, s, e))
        return segs

    segs = seg_yse()
    if not segs:
        return [], []

    # cluster segments into horizontal border lines
    merged = []
    used = [False] * len(segs)
    segs.sort()
    for i, (y, s, e) in enumerate(segs):
        if used[i]:
            continue
        ys = [y]
        s0, e0 = s, e
        used[i] = True
        for j in range(i + 1, len(segs)):
            if used[j]:
                continue
            y2, s2, e2 = segs[j]
            if y2 <= max(ys) + 8:
                overlap = max(0, min(e0, e2) - max(s0, s2))
                adjacent = (abs(s2 - e0) <= 25 or abs(s0 - e2) <= 25)
                if overlap > 0 or adjacent:
                    used[j] = True
                    ys.append(y2)
                    s0 = min(s0, s2)
                    e0 = max(e0, e2)
        merged.append((sum(ys) / len(ys), s0, e0))

    open_bottom = []
    open_top = []
    for y, s, e in merged:
        # open-bottom: border near the bottom edge, not already a full box
        if y > H - OPEN_BOX_PAGE_EDGE - 60 and y < H - 4:
            if any(abs(y - b[1]) < 20 and abs(s - b[2]) < BOX_TOLERANCE_PX for b in boxes):
                continue
            open_bottom.append((y, H - 4, s, e))
        # open-top: border near the top edge
        if y < OPEN_BOX_PAGE_EDGE + 60 and y > 4:
            if any(abs(y - b[0]) < 20 and abs(s - b[2]) < BOX_TOLERANCE_PX for b in boxes):
                continue
            open_top.append((4, y, s, e))

    # require side borders so we don't mistake a page-wide rule for a box
    def has_sides(y0, y1, x0, x1):
        span = y1 - y0
        if span < 30:
            return False
        return (check_vstrip_continuous(dark, y0, y1, x0)
                and check_vstrip_continuous(dark, y0, y1, x1))

    open_bottom = [b for b in open_bottom if has_sides(*b)]
    open_top = [b for b in open_top if has_sides(*b)]
    return open_bottom, open_top


def ocr_region(img, lang, config):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp)
        tmp_path = tmp.name
    try:
        return pytesseract.image_to_string(
            tmp_path, lang=lang, config=config
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)




def ocr_region_geometry(img, lang, config="--psm 6"):
    """OCR a region with word-level coordinates and rebuild the text with
    its visual indentation preserved (see reconstruct_indented_lines)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp)
        tmp_path = tmp.name
    try:
        data = pytesseract.image_to_data(
            tmp_path, lang=lang, config=config,
            output_type=pytesseract.Output.DICT,
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    lines_by_key = {}
    for i in range(len(data["text"])):
        w_text = (data["text"][i] or "").strip()
        if not w_text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines_by_key.setdefault(key, []).append({
            "text": w_text,
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
        })
    word_lines = [lines_by_key[k] for k in
                  sorted(lines_by_key, key=lambda k: lines_by_key[k][0]["top"])]
    return "\n".join(reconstruct_indented_lines(word_lines))


def process_page_regions(page, dpi, lang, export_crops=None, temp_crops_dir=None):
    """Detect bordered boxes on the page image. Returns (PIL image,
    list of (box, ocr_text), crop_paths). Boxes are OCR'd with PSM 6
    (uniform block) via word-geometry reconstruction, which preserves
    line-by-line structure and code indentation for code and tree
    diagrams. When ``export_crops`` is set, each box interior is saved as
    ``pageN_boxK.png`` and crop_paths maps box coords to the saved path.
    When ``temp_crops_dir`` is set, crops are saved there temporarily for VLM processing."""
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    dark = numpy.array(img) < 128
    boxes = find_boxes(dark)
    open_bottom, open_top = find_open_boxes(dark, boxes)
    results = []
    crop_paths = {}
    for k, (y0, y1, x0, x1) in enumerate(boxes + open_bottom + open_top, start=1):
        crop = img.crop((x0 + BOX_BORDER_PAD, y0 + BOX_BORDER_PAD,
                         x1 - BOX_BORDER_PAD, y1 - BOX_BORDER_PAD))
        if export_crops:
            os.makedirs(export_crops, exist_ok=True)
            crop_path = os.path.join(export_crops, f"page{page.number + 1}_box{k}.png")
            crop.save(crop_path)
            crop_paths[(y0, y1, x0, x1)] = crop_path
        elif temp_crops_dir:
            os.makedirs(temp_crops_dir, exist_ok=True)
            crop_path = os.path.join(temp_crops_dir, f"page{page.number + 1}_box{k}.png")
            crop.save(crop_path)
            crop_paths[(y0, y1, x0, x1)] = crop_path
        text = ocr_region_geometry(crop, lang, "--psm 6")
        results.append(((y0, y1, x0, x1), text))
    return img, results, crop_paths


def ocr_masked_page(img, box_results, dpi, lang):
    """OCR the full page with box interiors whited out, returning the
    normal line records for the prose regions."""
    from PIL import ImageDraw

    masked = img.copy()
    draw = ImageDraw.Draw(masked)
    for (y0, y1, x0, x1), _ in box_results:
        draw.rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), fill=255)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        masked.save(tmp)
        tmp_path = tmp.name
    try:
        data = pytesseract.image_to_data(
            tmp_path, lang=lang, config="--psm 3",
            output_type=pytesseract.Output.DICT,
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return lines_from_ocr(data)




def merge_regions(prose_lines, box_results, dpi, lang, use_mermaid=True, box_overrides=None):
    """Combine prose and fenced box blocks in top-to-bottom order.
    Prose lines are grouped into gap-separated blocks, each rendered to
    Markdown, and interleaved with boxes by pixel y-position.
    ``box_overrides`` maps box coords to pre-rendered fenced Markdown
    (e.g. VLM-enriched blocks), which bypasses the OCR render path."""
    box_overrides = box_overrides or {}
    events = []
    for (y0, y1, x0, x1), text in box_results:
        override = box_overrides.get((y0, y1, x0, x1))
        if override:
            events.append((y0, 0, "box", override))
            continue
        cleaned = clean_box_text(text)
        if cleaned:
            # Check if box text is a diagram and should be converted to Mermaid
            if use_mermaid:
                is_diag, diagram_block = parse_ascii_diagram_block(cleaned.splitlines())
                if is_diag:
                    events.append((y0, 0, "box", diagram_block))
                    continue
            lang_tag = estimate_code_language(cleaned)
            fence = f"```{lang_tag}" if lang_tag else "```"
            events.append((y0, 0, "box", f"{fence}\n{cleaned}\n```"))

    if prose_lines:
        heights = sorted(l["height"] for l in prose_lines)
        median_height = heights[len(heights) // 2] or 1
        blocks = group_into_paragraphs(prose_lines, median_height)
        for block in blocks:
            md = page_to_markdown(block, use_mermaid=use_mermaid)
            if md:
                events.append((block[0]["top"], block[-1]["bottom"], "prose", md))

    events.sort(key=lambda e: e[0])
    return "\n\n".join(e[3] for e in events)




def convert(pdf_path, out_path, lang, dpi, prefer_text_layer, save_images, use_mermaid=True,
            export_crops=None, vlm_backend=None, polish_backend=None, pause_for_polish=False):
    if not os.path.isfile(pdf_path):
        sys.exit(f"File not found: {pdf_path}")
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        sys.exit(f"Cannot open PDF: {e}")

    vlm = None
    temp_crops_dir = None
    if vlm_backend is not None:
        from vlm_enrichment import VLMEnricher
        vlm = VLMEnricher(backend=vlm_backend)
        if export_crops:
            vlm.crops_dir = export_crops
        else:
            temp_crops_dir = tempfile.mkdtemp(prefix="pdf2md_vlm_")
            vlm.crops_dir = temp_crops_dir

    title = strip_title_noise(os.path.splitext(os.path.basename(pdf_path))[0])
    parts = [f"# {title}", ""]
    open_fence = None       # (tag, [lines]) of a fenced block awaiting continuation
    prev_page_state = None  # (box, text, near_bottom, page_h)

    for pno, page in enumerate(doc, start=1):
        print(f"Processing page {pno}/{len(doc)}...", file=sys.stderr)
        used_text_layer = False
        lines = []
        if prefer_text_layer:
            lines = lines_from_text_layer(page)
            if sum(len(l["text"]) for l in lines) >= MIN_TEXT_CHARS:
                used_text_layer = True
            else:
                lines = []

        text = ""
        box_results = []
        all_boxes = []
        page_h = 0
        if not used_text_layer:
            img, all_boxes, crop_paths = process_page_regions(page, dpi, lang, export_crops=export_crops, temp_crops_dir=temp_crops_dir)
            page_h = img.size[1]
            cont, standalone = boxes_continuing(prev_page_state, all_boxes)
            if cont and open_fence is None:
                # The continuation detector matched, but no fence was
                # registered as open (e.g. the page's last fence had a tag
                # the regex missed). Keep the content as a standalone box
                # rather than dropping it silently.
                standalone = cont + standalone
                cont = []
            if cont and open_fence is not None:
                tag, buf, fence_part_idx, fence_span = open_fence
                for _, t in cont:
                    buf.append(clean_box_text(t))
                merged = "\n".join(buf)
                # Re-render the merged block with the same logic used for
                # a fresh box: try Mermaid synthesis first, then language
                # tagging.
                fence = None
                if use_mermaid and tag in ("", "mermaid"):
                    is_diag, diagram_block = parse_ascii_diagram_block(merged.splitlines())
                    if is_diag:
                        fence = diagram_block
                if fence is None:
                    new_tag = estimate_code_language(merged) or tag
                    fence = f"```{new_tag}\n{merged}\n```"
                page_text = parts[fence_part_idx]
                s, e = fence_span
                parts[fence_part_idx] = page_text[:s] + fence + page_text[e:]
                open_fence = None
            box_results = standalone
            if all_boxes:
                # Mask every detected box (continuations included) so the
                # prose pass cannot re-read consumed continuation content.
                prose_lines = ocr_masked_page(img, all_boxes, dpi, lang)
            else:
                prose_lines = []
            if box_results:
                box_overrides = {}
                if vlm is not None:
                    box_overrides = vlm.enrich_box_results(
                        box_results, crop_paths,
                        fallback=lambda cleaned: render_box_cleaned(cleaned, use_mermaid))
                    # Mode C (SLD topology) output feeds the rule engine:
                    # sequence-network Mermaid blocks follow the JSON fence.
                    for box, enriched in box_overrides.items():
                        if enriched and enriched.startswith("```json"):
                            extra = vlm.topology_markdown(enriched)
                            if extra:
                                box_overrides[box] = enriched + "\n\n" + extra
                text = merge_regions(prose_lines, box_results, dpi, lang,
                                     use_mermaid=use_mermaid, box_overrides=box_overrides)
            elif all_boxes:
                # Every box was consumed as a continuation; the prose pass
                # still used the masked image, so just render its lines.
                text = page_to_markdown(prose_lines, use_mermaid=use_mermaid)
            else:
                lines = lines_from_ocr(ocr_page(page, dpi, lang))
                text = page_to_markdown(lines, use_mermaid=use_mermaid)
            # Track the page's bottom-most box as a continuation source
            # only when it sits near the page bottom (i.e. split mid-box).
            bottom_continues = False
            raw_bottom_text = ""
            if all_boxes:
                bottom = max(all_boxes, key=lambda r: r[0][1])
                bottom_continues = bottom[0][1] > page_h * 0.75
                raw_bottom_text = clean_box_text(bottom[1])
                prev_page_state = (bottom[0], bottom[1],
                                   bottom_continues, page_h)
            else:
                prev_page_state = None
        else:
            prev_page_state = None
            text = page_to_markdown(lines, use_mermaid=use_mermaid)

        src = ("text layer" if used_text_layer
               else ("OCR + box regions" if all_boxes else "OCR"))
        parts.append(f"<!-- page {pno} ({src}) -->\n")
        if text:
            parts.append(text)
        else:
            parts.append("*(no text detected on this page)*")

        # If the bottom-most box of this page ended near the page bottom,
        # keep its RAW cleaned text so a matching continuation box on the
        # next page can be merged and the pair re-rendered together.
        open_fence = None
        if (not used_text_layer and prev_page_state and prev_page_state[2]
                and text and bottom_continues):
            m = None
            for m in re.finditer(r"```([\w-]*)\n(.*?)\n```", text, re.DOTALL):
                pass
            if m and m.end() >= len(text) - 4:
                open_fence = ("", [raw_bottom_text],
                              len(parts) - 1, (m.start(), m.end()))

        if save_images:
            img_path = os.path.join(save_images, f"page-{pno:04d}.png")
            os.makedirs(save_images, exist_ok=True)
            with open(img_path, "wb") as f:
                f.write(render_page(page, dpi))
            parts.append(f"\n![page {pno}]({img_path})")
        parts.append("")

    doc.close()

    # Clean up temp crops directory if VLM was used without --export-crops
    if temp_crops_dir and os.path.isdir(temp_crops_dir):
        import shutil as sh
        sh.rmtree(temp_crops_dir, ignore_errors=True)

    markdown = "\n".join(parts)
    if polish_backend:
        from docling_engineering.polish import build_polish_backend, polish_markdown
        adapter = build_polish_backend(polish_backend)
        if adapter is None:
            pass
        elif (pause_for_polish and vlm_backend is not None
              and vlm_backend != polish_backend):
            print(
                f"[pdf2md] VLM stage done (backend: {vlm_backend}).\n"
                f"[pdf2md] The polish stage uses a different backend ({polish_backend}).\n"
                "[pdf2md] Load the polish model now, then press Enter to continue "
                "(Ctrl+C to skip polishing)...",
                file=sys.stderr,
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print("[pdf2md] polish skipped by user.", file=sys.stderr)
                adapter = None
        if adapter is None:
            print("[pdf2md] --polish: backend is a no-op for text repair; skipping.",
                  file=sys.stderr)
        else:
            markdown = polish_markdown(markdown, adapter)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown + "\n")
    print(f"Wrote {out_path} ({len(markdown)} chars)", file=sys.stderr)
    return out_path


def configure_tesseract():
    """Auto-detect and configure tesseract binary across Windows, macOS, and Linux."""
    if os.environ.get("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
        return
    # Check if tesseract is in PATH
    which_cmd = shutil.which("tesseract")
    if which_cmd:
        pytesseract.pytesseract.tesseract_cmd = which_cmd
        return
    # Standard Windows install locations
    if sys.platform == "win32":
        for win_path in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ]:
            if os.path.isfile(win_path):
                pytesseract.pytesseract.tesseract_cmd = win_path
                return


configure_tesseract()




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
    ap.add_argument("--no-mermaid", action="store_true",
                    help="Disable automatic Mermaid diagram conversion for flowcharts and tree diagrams.")
    ap.add_argument("--export-crops", metavar="DIR",
                    help="Save every detected box region as an isolated PNG "
                         "(pageN_boxK.png) for VLM benchmarking and classifier training.")
    ap.add_argument(
        "--vlm",
        nargs="?",
        const="auto",
        default=None,
        metavar="BACKEND",
        help="Route detected box crops through a Vision-Language Model. "
             "BACKEND can be 'auto', a local model 'openai:<model>@<url>' "
             "(e.g. 'openai:Qwen3-VL-8B-Instruct@http://localhost:8000/v1' or 'local:<model>'), "
             "'granite' for local Docling Granite model, 'gemini-2.5-flash', or 'dryrun'. "
             "Omitted: no VLM (deterministic OCR). Plain '--vlm': auto-detect backend.",
    )
    ap.add_argument(
        "--no-pause",
        action="store_true",
        help="Do not pause between the --vlm and --polish stages. By default, "
             "when both --vlm and --polish are given, pdf2md waits for Enter "
             "after the VLM stage so the polish model can be loaded (single-GPU "
             "constraint: only one local LLM at a time).",
    )
    ap.add_argument(
        "--polish",
        metavar="BACKEND",
        default=None,
        help="Route the converted prose through a text-repair LLM pass "
             "(fixes OCR misreads like '| think' -> 'I think'). BACKEND uses "
             "the same specifiers as --vlm; 'granite' and 'dryrun' are no-ops.",
    )
    args = ap.parse_args()

    configure_tesseract()

    out = args.output or os.path.splitext(args.pdf)[0] + ".md"
    convert(args.pdf, out, args.lang, args.dpi, args.prefer_text_layer, args.save_images,
            use_mermaid=not args.no_mermaid, export_crops=args.export_crops,
            vlm_backend=args.vlm, polish_backend=getattr(args, "polish", None),
            pause_for_polish=not args.no_pause)


if __name__ == "__main__":
    main()
