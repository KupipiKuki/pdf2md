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
# Regex Patterns
# ---------------------------------------------------------------------------
GUTTER_RE = re.compile(r"^(\d{1,3})[.)]?$")
BULLET_RE = re.compile(r"^([•▪◦‣·\-–*eo©])\s+(.+)$")
BULLET_WORD_RE = re.compile(r"^(e|o|©)\s+([A-Za-z].*)$")
NUMBERED_RE = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")
ASCII_ART_RE = re.compile(r"[|\u2500-\u257f\u2190-\u21ff]|Vv|L_|L\.|\|-|-\||\+-")


# ---------------------------------------------------------------------------
# Language & Diagram Estimation (User Notes)
# ---------------------------------------------------------------------------
def estimate_code_language(code):
    """Heuristically estimate the programming language of a code block.
    Returns a markdown language identifier ('json', 'python', 'yaml', 'bash', 'mermaid')
    or an empty string '' if indeterminate."""
    if not code or not code.strip():
        return ""
    text = code.strip()

    # Mermaid check
    if re.match(r"^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|mindmap)\b", text):
        return "mermaid"

    # Strict or structural JSON check
    stripped_first = text[0] if text else ""
    if stripped_first in ("{", "["):
        try:
            json.loads(text)
            return "json"
        except Exception:
            pass
        # Common JSON snippets in technical documents (may have missing brackets or trailing commas)
        if len(re.findall(r'"[\w\s.-]+"\s*:\s*("[^"]*"|\d+|true|false|null|\[|\{)', text)) >= 1:
            return "json"

    # JSON key-value pairs without enclosing brackets
    if len(re.findall(r'"[\w\s.-]+"\s*:\s*("[^"]*"|\d+|true|false|null)', text)) >= 2:
        return "json"

    # Python patterns
    py_patterns = [
        r"\bimport\s+[\w.]+",
        r"\bfrom\s+[\w.]+\s+import\b",
        r"\bdef\s+[\w_]+\s*\(",
        r"\bclass\s+[\w_]+(?:\s*\([^)]*\))?\s*:",
        r"\bclass\s+[\w_]+:\s*pass\b",
        r"^\s*pass\b",
        r"\breturn\b",
        r"\bself\.",
        r"\b__init__\b",
        r"\belif\s+.*:",
        r"\bif\s+.*:\s*$",
        r"\bprint\s*\(",
        r"\braise\s+\w+",
        r"^\s*except(?:\s+[\w_]+)?\s*:",
        r"^\s*finally\s*:",
    ]
    py_score = sum(1 for pat in py_patterns if re.search(pat, text, re.MULTILINE))
    # Python object instantiation with kwargs, e.g., Transformer(name="T1", rating_mva=100)
    if re.search(r"\b[A-Z]\w+\s*\(\s*[\w_]+\s*=\s*", text):
        py_score += 2

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if py_score >= 1 and not re.search(r"[{};]\s*$", text, re.MULTILINE):
        if py_score >= 2 or any(re.search(r"^\s*(def|class|import|from)\b|^\s*if\s+.*:\s*|^\s*pass\b", l) for l in lines):
            return "python"

    # Bash / Shell command checks
    bash_patterns = [
        r"^\s*pip\s+install\b",
        r"^\s*sudo\s+(apt|brew|systemctl)\b",
        r"^\s*python\s+[\w.-]+\.py\b",
        r"^\s*git\s+(clone|checkout|commit|push|pull|status)\b",
        r"^\s*npm\s+(install|run|test)\b",
        r"^\s*\$\s+[a-z]",
    ]
    if any(re.search(p, text, re.MULTILINE) for p in bash_patterns):
        return "bash"

    # YAML check (guard against natural English prose with colons/lists)
    is_prose = len(re.findall(r"\b(this|the|an|analyze|identify|return|please|with|from)\b", text, re.IGNORECASE)) >= 2
    if not is_prose:
        yaml_kv = len(re.findall(r"^[A-Za-z0-9_-]+:\s+(?:\S.*|$)", text, re.MULTILINE))
        yaml_item = len(re.findall(r"^\s*-\s+[A-Za-z0-9_-]+", text, re.MULTILINE))
        if (yaml_kv >= 2 or (yaml_kv >= 1 and yaml_item >= 1)) and "{" not in text:
            return "yaml"

    return ""


def parse_ascii_diagram_to_mermaid(lines):
    """Attempt to parse an ASCII flow chart, tree diagram, or linear pipeline into Mermaid.
    Returns (is_diagram: bool, mermaid_code: str)."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    cleaned_lines = [l.strip() for l in lines if l.strip()]
    if not cleaned_lines:
        return False, ""

    # 1. Linear sequence pipeline check (e.g., "G | T1 | LINE | T2 | M" or "A -> B -> C")
    if len(cleaned_lines) == 1:
        line = cleaned_lines[0]
        if " | " in line or " -> " in line or " --> " in line:
            sep = " | " if " | " in line else (" --> " if " --> " in line else " -> ")
            parts = [p.strip() for p in line.split(sep) if p.strip()]
            if len(parts) >= 3:
                node_defs = []
                for i, p in enumerate(parts):
                    nid = f"N{i+1}"
                    lbl = p.replace('"', "'")
                    node_defs.append(f'{nid}["{lbl}"]')
                return True, "flowchart LR\n    " + " --> ".join(node_defs)

    # 2. Flowchart and tree markers
    arrow_pattern = re.compile(r"^(\||\bv\b|\bV\b|Vv|▼|↓|\^|--+>|==+>)+$", re.IGNORECASE)
    tree_branch_prefix = re.compile(r"^([|\[\]\(\)]*\s*(?:\|-|\+-|L[-_.]|r[-_.]|[├└\u2500-\u257f])\s*)(.*)$")

    arrow_lines = 0
    tree_lines = 0
    for l in cleaned_lines:
        if arrow_pattern.match(l):
            arrow_lines += 1
        elif tree_branch_prefix.match(l) or l.startswith("| ") or l.startswith("|-") or l.startswith("L-") or l.startswith("L_"):
            tree_lines += 1

    is_diagram = (arrow_lines >= 2) or (tree_lines >= 2) or (arrow_lines + tree_lines >= len(cleaned_lines) * 0.35)
    if not is_diagram:
        return False, ""

    nodes = []
    edges = []
    node_counter = 1

    def make_id(label):
        nonlocal node_counter
        clean = re.sub(r"[^A-Za-z0-9_]", "", label)
        if not clean or clean[0].isdigit():
            clean = f"node_{node_counter}"
        else:
            clean = f"{clean[:12]}_{node_counter}"
        node_counter += 1
        return clean

    def clean_label(text):
        t = re.sub(r"^([|\[\]\(\)]*\s*(?:\|-|\+-|L[-_.]|r[-_.]|[├└\u2500-\u257f])\s*)", "", text).strip()
        # Clean common OCR misreads of tree branches like 'Je Civil Drawing' or 'r- P&ID'
        t = re.sub(r"^[JjFfrI][eE—–\-]\s*", "", t).strip()
        t = re.sub(r"^[|\[\]\s]+", "", t).strip()
        t = t.replace('"', "'")
        # Sanitize HTML-like angle brackets for Mermaid
        t = t.replace("<—", "&lt;--").replace("<--", "&lt;--").replace("<-", "&lt;--")
        t = t.replace("<", "&lt;").replace(">", "&gt;")
        return t

    current_main_node = None
    last_node = None
    current_tree_parent = None
    sub_parent = None

    i = 0
    n = len(cleaned_lines)
    while i < n:
        raw = cleaned_lines[i]
        if arrow_pattern.match(raw):
            i += 1
            continue

        m_tree = tree_branch_prefix.match(raw)
        is_tree_branch = bool(m_tree or raw.startswith("| ") or raw.startswith("|-") or raw.startswith("L-") or raw.startswith("L_"))

        lbl = clean_label(raw)
        if not lbl:
            i += 1
            continue

        nid = make_id(lbl)
        nodes.append((nid, lbl))

        if is_tree_branch:
            prefix = m_tree.group(1) if m_tree else raw
            if ("L_" in prefix or "L." in prefix or "L-" in prefix or "└" in prefix) and sub_parent:
                edges.append((sub_parent, nid))
                sub_parent = None
            else:
                parent = current_tree_parent or current_main_node or last_node
                if parent and parent != nid:
                    edges.append((parent, nid))

                lbl_lower = lbl.lower()
                if any(w in lbl_lower for w in ["detector", "extension", "drawing", "scheme"]):
                    current_tree_parent = nid
                elif any(w in lbl_lower for w in ["text", "tables", "sld"]):
                    if "route here" in lbl_lower or "&lt;--" in lbl or "<--" in lbl:
                        current_main_node = nid
                    else:
                        sub_parent = nid
        else:
            prev_has_arrow = (i > 0 and arrow_pattern.match(cleaned_lines[i-1]))
            if prev_has_arrow:
                parent = current_main_node or last_node
                if parent and parent != nid:
                    edges.append((parent, nid))
                current_main_node = nid
                current_tree_parent = None
                sub_parent = None
            else:
                if not current_main_node:
                    current_main_node = nid
                else:
                    parent = current_main_node
                    if parent != nid:
                        edges.append((parent, nid))
                    current_tree_parent = nid
        last_node = nid
        i += 1

    if not edges and len(nodes) < 2:
        return False, ""

    out = ["flowchart TD"]
    for nid, lbl in nodes:
        out.append(f'    {nid}["{lbl}"]')
    for f, t in edges:
        out.append(f"    {f} --> {t}")

    return True, "\n".join(out)


# ---------------------------------------------------------------------------
# Layout Heuristics
# ---------------------------------------------------------------------------
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
        if prev is not None and ln["top"] - prev["bottom"] > PARAGRAPH_GAP_RATIO * median_height:
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


def clean_block_lines(block):
    """Filter out stray OCR artifacts (<=2 chars) that overlap vertically with a longer line."""
    filtered = []
    for i, l in enumerate(block):
        if len(l["text"].strip()) <= 2:
            overlaps = any(
                j != i and len(other["text"].strip()) > 2 and
                not (l["bottom"] < other["top"] or l["top"] > other["bottom"])
                for j, other in enumerate(block)
            )
            if overlaps:
                continue
        filtered.append(l)
    return filtered


def render_block(block, median_height, max_size):
    """Convert one block of lines into Markdown."""
    block = clean_block_lines(block)
    if not block:
        return None
    texts = [l["text"].strip() for l in block]
    first = block[0]
    text = " ".join(texts).strip()

    if not text:
        return None

    is_single = len(block) == 1
    big = first.get("size", 0) >= max_size * 0.98 or first["height"] >= HEADING_HEIGHT_RATIO * median_height
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

    if len(block) >= 3 and looks_like_list(block):
        items = []
        for l in block:
            t = l["text"].strip()
            m = BULLET_RE.match(t) or BULLET_WORD_RE.match(t)
            if m:
                t = m.group(2).strip()
            if t:
                items.append(f"- {t}")
        return "\n".join(items)

    if len(texts) > 1 and all(NUMBERED_RE.match(t) for t in texts if t):
        items = [NUMBERED_RE.match(t).group(0) for t in texts if t]
        return "\n".join(items)

    return text


def looks_like_list(block):
    """Detect vertical bullet lists where OCR dropped the bullet glyphs:
    short lines, near-uniform left alignment (within ~30px), uniform vertical rhythm."""
    lefts = [l["left"] for l in block]
    texts = [l["text"].strip() for l in block]
    if any(len(t) > 60 for t in texts):
        return False
    if max(lefts) - min(lefts) > 30:
        return False
    has_marker = any(BULLET_RE.match(t) or BULLET_WORD_RE.match(t) for t in texts)
    if not has_marker and max(lefts) - min(lefts) > 6:
        return False
    if not has_marker:
        wordish = sum(1 for t in texts if re.match(r"^[A-Za-z][A-Za-z0-9 _/&-]{1,40}$", t))
        if wordish < max(3, (len(texts) * 3) // 4):
            return False
    gaps = [block[i + 1]["top"] - block[i]["bottom"] for i in range(len(block) - 1)]
    if not gaps:
        return False
    avg = sum(gaps) / len(gaps)
    if not all(abs(g - avg) <= max(8, avg * 0.4) for g in gaps):
        return False
    return not any(t.endswith((".", ",", ";", ":")) for t in texts if len(t) > 3)


def looks_like_code(lines, gutter_idx):
    """A block is code if it contains gutter-number lines, or if most of
    its lines look code-ish (braces, brackets, operators, keywords)."""
    if any(GUTTER_RE.match(l["text"]) for l in lines):
        return True
    codey = 0
    for l in lines:
        t = l["text"]
        if re.search(r"[{}\[\]=<>;]|^def |^class |^import |if\b.*:$|\"[a-z_0-9]+\":|^\)|class\s+\w+:|pass\b", t):
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
            is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(raw_lines)
            if is_diag:
                out.append("```mermaid\n" + mermaid_code + "\n```")
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


def process_page_regions(page, dpi, lang):
    """Detect bordered boxes on the page image. Returns (PIL image,
    list of (box, ocr_text)). Boxes are OCR'd with PSM 6 (uniform block)
    which preserves line-by-line structure for code and tree diagrams."""
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    dark = numpy.array(img) < 128
    boxes = find_boxes(dark)
    open_bottom, open_top = find_open_boxes(dark, boxes)
    results = []
    for y0, y1, x0, x1 in boxes + open_bottom + open_top:
        crop = img.crop((x0 + BOX_BORDER_PAD, y0 + BOX_BORDER_PAD,
                         x1 - BOX_BORDER_PAD, y1 - BOX_BORDER_PAD))
        text = ocr_region(crop, lang, "--psm 6")
        results.append(((y0, y1, x0, x1), text))
    return img, results


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


def clean_box_text(text):
    """Normalize a box's OCR text: strip gutter numbers at line starts,
    preserve code indentation, drop empty lines, and remove bare
    gutter-number lines (code lines whose text OCR failed to see)."""
    out = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = re.match(r"^\s*(\d{1,3})[.)]?[ \t](.*)$", raw)
        if m:
            line = m.group(2).rstrip()
        else:
            line = raw.strip()
        # A bare number is a gutter line for an empty/unread code line.
        if re.fullmatch(r"\d{1,3}[.)]?", line):
            continue
        # OCR sometimes glues a bare number from the next gutter line onto
        # the end of content (e.g. 'pass3', '}5'). Split it off when the
        # preceding text is not itself numeric.
        m2 = re.match(r"^(.*\S)(\s*)(\d{1,2})$", line)
        if m2:
            stem = m2.group(1)
            if re.search(r"[})\]]$", stem):
                line = stem.rstrip()
        # OCR sometimes reads the gutter number's leading digit as '+'
        # before a closing brace (e.g. gutter '22 }' -> '+}').
        line = re.sub(r"^\+\s*([})\]])", r"\1", line)
        if line:
            out.append(line)
    return "\n".join(out)


def merge_regions(prose_lines, box_results, dpi, lang, use_mermaid=True):
    """Combine prose and fenced box blocks in top-to-bottom order.
    Prose lines are grouped into gap-separated blocks, each rendered to
    Markdown, and interleaved with boxes by pixel y-position."""
    events = []
    for (y0, y1, x0, x1), text in box_results:
        cleaned = clean_box_text(text)
        if cleaned:
            # Check if box text is a diagram and should be converted to Mermaid
            if use_mermaid:
                is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(cleaned.splitlines())
                if is_diag:
                    events.append((y0, 0, "box", "```mermaid\n" + mermaid_code + "\n```"))
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



BOX_CONT_X_OVERLAP = 0.6
BOX_CONT_GUTTER_GAP = 3


def box_gutter_range(text):
    """Extract the min and max leading gutter numbers from box OCR text."""
    nums = []
    for raw in text.splitlines():
        m = re.match(r"^\s*(\d{1,3})[.)]?[ \t]", raw)
        if m:
            nums.append(int(m.group(1)))
    return (min(nums), max(nums)) if nums else None


def boxes_continuing(prev_page_state, box_results):
    """Split box_results into (continuations, standalone) based on the
    previous page's state. A box continues the previous page's last
    bottom box when their x-extents overlap strongly and the gutter
    numbers follow (or the previous box ended near the page bottom)."""
    if not prev_page_state or not box_results:
        return [], box_results
    prev_box, prev_text, prev_bottom, prev_h = prev_page_state
    prev_gut = box_gutter_range(prev_text)
    cont, rest = [], []
    for box, text in box_results:
        y0, y1, x0, x1 = box
        x_overlap = max(0, min(x1, prev_box[3]) - max(x0, prev_box[2]))
        if x_overlap < BOX_CONT_X_OVERLAP * min(x1 - x0, prev_box[3] - prev_box[2]):
            rest.append((box, text))
            continue
        # prefer the topmost candidate box on the next page
        if y0 > prev_h * 0.4:
            rest.append((box, text))
            continue
        gut = box_gutter_range(text)
        if prev_gut and gut and gut[0] > prev_gut[1] and gut[0] - prev_gut[1] <= BOX_CONT_GUTTER_GAP + 1:
            cont.append((box, text))
            continue
        if prev_bottom and gut is None and prev_gut is None:
            cont.append((box, text))
            continue
        rest.append((box, text))
    return cont, rest

def convert(pdf_path, out_path, lang, dpi, prefer_text_layer, save_images, use_mermaid=True):
    if not os.path.isfile(pdf_path):
        sys.exit(f"File not found: {pdf_path}")
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        sys.exit(f"Cannot open PDF: {e}")

    parts = [f"# {os.path.splitext(os.path.basename(pdf_path))[0]}", ""]
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
        page_h = 0
        if not used_text_layer:
            img, all_boxes = process_page_regions(page, dpi, lang)
            page_h = img.size[1]
            cont, standalone = boxes_continuing(prev_page_state, all_boxes)
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
                    is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(merged.splitlines())
                    if is_diag:
                        fence = "```mermaid\n" + mermaid_code + "\n```"
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
                text = merge_regions(prose_lines, box_results, dpi, lang, use_mermaid=use_mermaid)
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

        src = "text layer" if used_text_layer else ("OCR + box regions" if box_results else "OCR")
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
            for m in re.finditer(r"```(\w*)\n(.*?)\n```", text, re.DOTALL):
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

    markdown = "\n".join(parts)
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
    args = ap.parse_args()

    configure_tesseract()

    out = args.output or os.path.splitext(args.pdf)[0] + ".md"
    convert(args.pdf, out, args.lang, args.dpi, args.prefer_text_layer, args.save_images, use_mermaid=not args.no_mermaid)


if __name__ == "__main__":
    main()
