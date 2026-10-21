"""Pure-geometry heuristics and layout normalization for pdf2md and docling-engineering.

Decoupled from PyMuPDF and Tesseract so heuristics can be tested and used
independently of external binaries or page rendering.
"""

import json
import re
import numpy

# ---------------------------------------------------------------------------
# Constants & Regex Patterns
# ---------------------------------------------------------------------------
PARAGRAPH_GAP_RATIO = 1.2
HEADING_HEIGHT_RATIO = 1.15

BOX_CONT_X_OVERLAP = 0.6
BOX_CONT_GUTTER_GAP = 3

GUTTER_RE = re.compile(r"^(\d{1,3})[.)]?$")
BULLET_RE = re.compile(r"^([•▪◦‣·\-–*eo©])\s+(.+)$")
BULLET_WORD_RE = re.compile(r"^(e|o|©)\s+([A-Za-z].*)$")
NUMBERED_RE = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")
ASCII_ART_RE = re.compile(r"[|\u2500-\u257f\u2190-\u21ff]|Vv|L_|L\.|\|-|-\||\+-")
MATH_TOKEN_RE = re.compile(r"^[XYZ](?:[T_]?\w+)?\s*\(?[\w,.\s]*\)?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Language & Diagram Estimation
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


def math_chain_to_mermaid(lines):
    """Convert vertical math addition chains (Xs(G) / + / XT1 / + ...)
    into an undirected Mermaid chain. Sequence-network diagrams in this
    domain use '+' as the series connector; the graph they represent is
    a chain of reactances. Returns (is_math_chain, mermaid_code)."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    cleaned = [l.strip() for l in lines if l.strip()]
    if len(cleaned) < 5:
        return False, ""
    tokens = []
    expect_plus = False
    for l in cleaned:
        if l == "+":
            if not tokens:
                return False, ""
            expect_plus = False
            continue
        if expect_plus:
            return False, ""
        if not MATH_TOKEN_RE.match(l):
            return False, ""
        tokens.append(l)
        expect_plus = True
    if len(tokens) < 3:
        return False, ""
    out = ["flowchart TD"]
    ids = []
    for i, t in enumerate(tokens):
        nid = f"N{i+1}"
        ids.append(nid)
        out.append(f'    {nid}["{t}"]')
    for a, b in zip(ids, ids[1:]):
        out.append(f"    {a} --- {b}")
    return True, "\n".join(out)


def recover_numeric_misread(label):
    """OCR often reads 'T1'/'T2' as '71'/'72' inside vertical network
    chain diagrams. Restore 'T<digit>' for bare two-digit tokens starting
    with 7 or 1 in a chain context."""
    s = label.strip()
    if re.fullmatch(r"[17]\d", s):
        return f"T{s[1]}"
    return label


def parse_ascii_diagram_to_mermaid(lines):
    """Attempt to parse an ASCII flow chart, tree diagram, or linear pipeline into Mermaid.
    Returns (is_diagram: bool, mermaid_code: str)."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    cleaned_lines = [l.strip() for l in lines if l.strip()]
    if not cleaned_lines:
        return False, ""
    # 0. Vertical math addition chains are sequence-network graphs with
    # '+' as the series connector; render as an undirected chain.
    is_math, math_code = math_chain_to_mermaid(cleaned_lines)
    if is_math:
        return True, math_code

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

        lbl = recover_numeric_misread(clean_label(raw))
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
    trunk_only = all(not (m or raw.startswith("| ") or raw.startswith("|-")
                          or raw.startswith("L-") or raw.startswith("L_"))
                     for raw in cleaned_lines
                     for m in [tree_branch_prefix.match(raw)])
    edge = "---" if trunk_only else "-->"
    for f, t in edges:
        out.append(f"    {f} {edge} {t}")

    return True, "\n".join(out)


TREE_NODE_RE = re.compile(r'^\s*(\w+)\[(?:"([^"]*)"|\'([^\']*)\'|\(([^)]*)\))\]\s*$')
TREE_EDGE_RE = re.compile(r"^\s*(\w+)\s*(?:-->|---)\s*(\w+)\s*$")


def _parse_mermaid_graph(mermaid_code):
    """Extract (nodes, edges) as ((id, label), (src_id, dst_id)) lists from the
    simple Mermaid subset emitted by parse_ascii_diagram_to_mermaid."""
    nodes = []
    edges = []
    for line in mermaid_code.splitlines():
        line = line.strip()
        if line.startswith("flowchart") or not line:
            continue
        m = TREE_EDGE_RE.match(line)
        if m:
            edges.append((m.group(1), m.group(2)))
            continue
        m = TREE_NODE_RE.match(line)
        if m:
            label = next(g for g in m.groups()[1:] if g is not None)
            nodes.append((m.group(1), label))
    return nodes, edges


def is_tree(nodes, edges):
    """True when the graph is a strict rooted tree: at least 2 nodes, exactly
    one root (in-degree 0), every other node in-degree 1, and connected
    (edge count == node count - 1 implies connectedness given the degree
    constraints). Note: a linear chain also satisfies this; callers that
    need a *hierarchy* should additionally require branching via
    has_branching_children (Section 13.2)."""
    node_ids = [nid for nid, _ in nodes]
    if len(node_ids) < 2 or not edges:
        return False
    in_degree = {nid: 0 for nid in node_ids}
    child_ids = set()
    for src, dst in edges:
        if dst not in in_degree or src not in in_degree:
            return False
        in_degree[dst] += 1
        child_ids.add(dst)
    roots = [nid for nid, deg in in_degree.items() if deg == 0]
    if len(roots) != 1:
        return False
    if any(deg != 1 for nid, deg in in_degree.items() if nid != roots[0]):
        return False
    return len(edges) == len(node_ids) - 1


def has_branching_children(nodes, edges):
    """True when at least one node has 2+ children: the graph is a genuine
    hierarchy rather than a linear chain (which is topologically a tree
    but semantically a sequence)."""
    child_counts = {}
    for src, dst in edges:
        child_counts[src] = child_counts.get(src, 0) + 1
    return any(count >= 2 for count in child_counts.values())


def _counts_hierarchy_markers(lines):
    """Count hierarchy-style markers (branch prefixes) vs flow-style
    markers (standalone vertical arrows) in the OCR lines. A tree diagram
    is drawn with branches; a process flow is drawn with arrows."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    arrow_pattern = re.compile(
        r"^(\||\bv\b|\bV\b|Vv|\u25bc|\u2193|\^|--+>|==+>)+$", re.IGNORECASE)
    tree_branch_prefix = re.compile(
        r"^([|\[\]\(\)]*\s*(?:\|-|\+-|L[-_.]|r[-_.]|[\u251c\u2514\u2500-\u257f])\s*)(.*)$")
    branches = 0
    arrows = 0
    for line in lines:
        stripped = (line or "").strip()
        if not stripped:
            continue
        if arrow_pattern.match(stripped):
            arrows += 1
        elif tree_branch_prefix.match(stripped) or stripped.startswith("| ") \
                or stripped.startswith("|-") or stripped.startswith("L-") \
                or stripped.startswith("L_"):
            branches += 1
    return branches, arrows


def render_tree_view(nodes, edges):
    """Render a parsed graph as an indented plain-text tree (the treeView
    convention used by GitHub/GitLab Markdown). Root is the unique node
    with in-degree 0; children keep their extraction order."""
    children = {}
    child_set = set()
    for src, dst in edges:
        children.setdefault(src, []).append(dst)
        child_set.add(dst)
    root = next(nid for nid, _ in nodes if nid not in child_set)
    labels = dict(nodes)

    def walk(nid, prefix, lines):
        kids = children.get(nid, [])
        for i, kid in enumerate(kids):
            last = i == len(kids) - 1
            lines.append(prefix + ("└── " if last else "├── ") + labels[kid])
            walk(kid, prefix + ("    " if last else "│   "), lines)

    lines = [labels[root]]
    walk(root, "", lines)
    return "\n".join(lines)


def parse_ascii_diagram_block(lines):
    """Parse an ASCII diagram and render it in the best-fit form.

    Returns (is_diagram: bool, fenced_block: str) where fenced_block is a
    complete fenced Markdown block: ```treeView-beta ... ``` for genuine
    hierarchies (Section 13.2) or ```mermaid ... ``` otherwise.

    A diagram is rendered as a tree only when ALL hold:
    1. the graph is a strict rooted tree (is_tree),
    2. it actually branches (has_branching_children) - a linear chain is
       topologically a tree but semantically a sequence (e.g. the
       positive-sequence network G-T1-LINE-T2-M),
    3. the source OCR lines use hierarchy markers (branch prefixes),
       not flow markers (standalone vertical arrows) - a process flow
       drawn with arrows is a tree topologically but a flow semantically
       (e.g. the Proposed Pipeline).
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(lines)
    if not is_diag:
        return False, ""
    nodes, edges = _parse_mermaid_graph(mermaid_code)
    branches, arrows = _counts_hierarchy_markers(lines)
    if (is_tree(nodes, edges)
            and has_branching_children(nodes, edges)
            and branches > 0
            and arrows == 0):
        return True, "```treeView-beta\n" + render_tree_view(nodes, edges) + "\n```"
    return True, "```mermaid\n" + mermaid_code + "\n```"


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


def looks_like_code(lines, gutter_idx=None):
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


# ---------------------------------------------------------------------------
# Monospace Indentation & Box Text Cleaning
# ---------------------------------------------------------------------------
def _snap_indents(values):
    """Snap raw column indents to the box's indentation unit (the largest
    multiple of 4 or 2 whose multiples lie within +/-1 column of every
    observed value). Boxes with no non-zero values snap to 0. Eliminates
    6/3/9-column artifacts QAQC observed)."""
    if not values:
        return values
    for unit in (4, 2):
        if all(abs(v - unit * round(v / unit)) <= 1 for v in values):
            return [unit * round(v / unit) for v in values]
    return [max(0, round(v)) for v in values]


def reconstruct_indented_lines(word_lines):
    """Reconstruct monospace text lines with their visual indentation from
    word-level bounding boxes.

    ``word_lines`` is a list of lines, each a list of word dicts with keys
    ``text``, ``left``, ``top``, ``width``, ``height``. Under PSM 6,
    Tesseract's image_to_string collapses all intra-line whitespace runs to
    a single space, permanently losing code indentation; this rebuilds
    each line from the pixel geometry.

    Skew- and glyph-bias handling (QAQC 9.2-A):

    - ``char_w`` is the median per-character width of code-like tokens
      only (len >= 2 and containing a letter or underscore): pure
      digits and single-character glyphs are narrower and biased the
      old whole-box median.
    - Indentation is measured relative to the SAME line's gutter number
      word (offset = first code word left - (gutter left + gutter
      width)), which is invariant to the ~0.2 degree scan skew because
      the gutter word shares its line's skew. The base is the minimum
      offset across the box (the outermost code column); boxes without
      gutters fall back to the minimum code left.
    - Raw column indents are snapped to the box's indentation unit
      (2 or 4 columns) inferred from the observed values
      (``_snap_indents``).

    Gutter number words are kept in the output as
    ``"<gutter> <indented code>"`` (a single space delimiter) because
    downstream gutter-aware logic (box_gutter_range, boxes_continuing,
    clean_box_text's bare-number and brace repairs) relies on the gutter
    number being present; clean_box_text consumes exactly one delimiter
    and preserves the rebuilt indentation. Inter-word gaps inside a
    line are emitted as single spaces.
    """
    if not word_lines:
        return []

    char_widths = []
    for line in word_lines:
        for w in line:
            t = w["text"].strip()
            if len(t) >= 2 and re.search(r"[A-Za-z_]", t):
                char_widths.append(w["width"] / len(t))
    char_w = float(numpy.median(char_widths)) if char_widths else 12.0
    if char_w <= 0:
        char_w = 12.0

    parsed = []
    for words in word_lines:
        content = [w for w in words if w["text"].strip()]
        if not content:
            continue
        gutter_text = None
        anchor = content[0]["left"]
        if GUTTER_RE.match(content[0]["text"]):
            gutter_text = content[0]["text"]
            anchor = content[0]["left"] + content[0]["width"]
            content = content[1:]
            if not content:
                continue
        parsed.append((gutter_text, anchor, content))

    if not parsed:
        return []

    gutter_offsets = [c[0]["left"] - anchor
                     for g, anchor, c in parsed if g]
    if gutter_offsets:
        base = min(gutter_offsets)
        base_code_left = min(c[0]["left"] for g, a, c in parsed if g)
    else:
        base = None
        base_code_left = min(c[0]["left"] for g, a, c in parsed)

    raw_indents = []
    for gutter_text, anchor, content in parsed:
        if gutter_text is not None:
            indent_px = content[0]["left"] - anchor - base
        else:
            indent_px = content[0]["left"] - base_code_left
        raw_indents.append(max(0, indent_px) / char_w)
    indents = _snap_indents(raw_indents)

    reconstructed = []
    for (gutter_text, _, content), indent_cols in zip(parsed, indents):
        lead_spaces = " " * int(indent_cols)
        code_part = lead_spaces + " ".join(w["text"] for w in content)
        if gutter_text is not None:
            reconstructed.append(f"{gutter_text} {code_part}")
        else:
            reconstructed.append(code_part)
    return reconstructed


def clean_box_text(text):
    r"""Strip gutter numbers and restore code/diagram content.

    Preserves code block indentation after stripping gutter numbers:
    gutter lines match ``^\s*(\d{1,3})[.)]?[ \t](.*)$``; the regex
    consumes only a single whitespace/tab delimiter, leaving leading
    indentation intact.

    Bare-number handling: OCR sometimes emits only the gutter number for
    a code line whose text it failed to read (e.g. a lone '3'). Such a
    line is dropped ONLY when its value equals the gutter position it
    came from on its own line (a true gutter-of-an-empty-line), or when
    it is a standalone line whose value continues the gutter sequence
    while the following gutter skips (a duplicated gutter read). A bare
    number that survives (e.g. '71' from '3 71', an OCR misread of 'T1')
    is real content and is kept.
    """
    raw_lines = text.splitlines()
    out = []
    prev_gutter = None
    for raw in raw_lines:
        if not raw.strip():
            continue
        m = re.match(r"^\s*(\d{1,3})[.)]?[ \t](.*)$", raw)
        if m:
            gutter = int(m.group(1))
            line = m.group(2).rstrip()
        else:
            gutter = None
            line = raw.strip()

        if line and re.fullmatch(r"\d{1,3}[.)]?", line):
            num = int(line.rstrip(".)"))
            if gutter is not None and num == gutter:
                prev_gutter = gutter
                continue
            if gutter is None:
                continue
        if gutter is not None:
            prev_gutter = gutter

        # OCR closing brace misread repairs: '8 }' -> 'ae', '13 }' -> 'fe'
        if re.fullmatch(r"(ae|fe|ce|ee|oe)", line):
            line = "}"
        m4 = re.fullmatch(r"(?:ae|fe|ce|ee|oe)\s+([})\]])", line)
        if m4:
            line = m4.group(1)
        line = re.sub(r"^\+\s*([})\]])", r"\1", line)
        m3 = re.match(r"^(.*[\w\"'\)\]])(?:ae|fe|ce|ee|oe)$", line)
        if m3:
            line = m3.group(1).rstrip()
            if not line.endswith("}"):
                line += "}"
        m2 = re.match(r"^(.*\S)(\s*)(\d{1,2})$", line)
        if m2:
            stem = m2.group(1)
            if re.search(r"[})\]]$", stem):
                line = stem.rstrip()
        if line:
            out.append(line)
    return "\n".join(out)


def render_box_cleaned(cleaned, use_mermaid=True):
    """Render a cleaned box's text to a fenced Markdown block using the
    standard heuristics (Mermaid synthesis first, then language tag)."""
    if use_mermaid:
        is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(cleaned.splitlines())
        if is_diag:
            return "```mermaid\n" + mermaid_code + "\n```"
    lang_tag = estimate_code_language(cleaned)
    fence = f"```{lang_tag}" if lang_tag else "```"
    return f"{fence}\n{cleaned}\n```"


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
