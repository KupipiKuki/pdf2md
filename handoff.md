# pdf2md — Revised Handoff & Technical Audit

This document records the audit, enhancements, and architectural recommendations for the `pdf2md` project on branch `vibe/mermaid-lang-detect-and-optimizations`.

---

## 1. Summary of Enhancements Delivered in this Branch

### A. Code Block Language Estimation (`estimate_code_language`)
- Fenced blocks now automatically receive language estimation tags (`json`, `python`, `mermaid`, `yaml`, `bash`, `text`) instead of bare ```` ``` ````.
- JSON detection supports both strict JSON objects/arrays and unbracketed configuration snippets (e.g., `"figure_type": "...", "confidence": 0.94`).
- Python detection recognizes keywords (`def`, `class`, `import`, `pass`), conditional blocks, keyword argument instantiations (`Transformer(name=..., rating_mva=...)`), and domain rules (`if transformer.primary == ...:`).
- Guards against natural English prose to prevent false-positive YAML classification.

### B. Mermaid Flowchart & Diagram Synthesis (`parse_ascii_diagram_to_mermaid`)
- Added conversion of ASCII art flowcharts, box-drawing trees, and linear chains into native Mermaid syntax (`flowchart TD` / `flowchart LR`).
- Converts directional flow characters (`|`, `v`, `V`, `Vv`, `▼`, `↓`, `-->`) into Mermaid arrows (`-->`).
- Converts branch markers (`├──`, `└──`, `|-`, `L-`, `L_`, `L.`) into parent-child graph connections.
- Converts linear node pipelines (`G | T1 | LINE | T2 | M` or `A -> B -> C`) into horizontal flowchart chains.
- Scoped character glyph normalization cleans common OCR misreads (e.g., `Je Civil Drawing` -> `Civil Drawing`, `r- P&ID` -> `P&ID`).
- Added safety guards: vertical math addition chains (e.g., `X2(G) + XT1 + ...`) and bulleted prompt lists are protected from false-positive diagram conversion.
- Added `--no-mermaid` CLI flag to allow users to opt out and preserve raw ASCII.

### C. Robust Bordered Box Region Detection (Root Cause Fix)
- **Problem Discovered:** The original `find_boxes` required perfectly unbroken 1-pixel horizontal and vertical runs on integer pixel coordinates. In real scans, a minute rotational tilt of ~0.2° introduces a 6–8 px slope across the 1700 px line width, causing rasterization stair-stepping that fractured borders into sub-threshold segments. Consequently, bordered regions on pages 2, 3, 4, 5, and 6 were completely missed and misidentified as "unboxed diagrams".
- **Solution:**
  1. Added gap-tolerant line tracing (`max_gap = 4`) to bridge minor scan noise.
  2. Implemented segment stitching to join adjacent tilted horizontal runs.
  3. Replaced fragile single-column vertical line checks with continuous vertical strip evaluation (`check_vstrip_continuous`), which computes vertical dark pixel density and checks for maximum break thresholds across a 10px window.
  4. Increased endpoint tolerance (`BOX_TOLERANCE_PX = 85`) to reliably capture boxes with slightly eroded corners.
- **Result:** Detected boxes increased from 4 across the entire document to 13, enabling PSM 6 uniform block OCR on all code blocks and diagrams.

### D. Stray Noise Filtering & List Preservation (`clean_block_lines`)
- In scanned pages, isolated 1–2 character OCR artifacts (e.g., `Pp` at y=689 overlapping `Detect diagram region` at y=688 on page 6) previously distorted the bounding coordinate spread, causing `looks_like_list` to reject the block and collapse multiline numbered lists into a single run-on paragraph.
- `clean_block_lines` removes stray 1–2 char tokens that vertically overlap longer text lines, allowing the 6-step pipeline on page 6 to be cleanly reconstructed as a Markdown list.

### E. Indentation Preservation in Code Blocks (`clean_box_text`)
- Previously, `clean_box_text` stripped all whitespace from each line (`raw.strip()`), obliterating indentation essential to Python syntax.
- Updated to consume only a single whitespace/tab delimiter after the gutter number, preserving Python indentation (`4 spaces`, nested blocks).

### F. Cross-Platform Compatibility & Unit Testing
- Fixed hardcoded `/tmp` paths in `tests/smoke_test.py` by using `tempfile.gettempdir()`.
- Implemented automatic tesseract discovery across Windows (`C:\Program Files\Tesseract-OCR\tesseract.exe`), macOS (`brew`), and Linux (`PATH`).
- Added a dedicated test suite `tests/test_heuristics.py` with 13 deterministic tests covering language estimation, diagram parsing, gutter detection, list filtering, and box text cleaning.

---

## 2. Technical Audit & Feedback for the Primary Coder

### Strengths of the Architecture
1. **Separation of Geometry from Source:** The generic line record dict `{text, top, bottom, height, left, bold, size}` allows OCR and PDF text layer extraction to feed identically into downstream layout heuristics.
2. **PSM 6 vs PSM 3 Separation:** Processing bounded regions with uniform block mode (PSM 6) while masking them before full-page auto-segmentation (PSM 3) is a proven strategy for preserving tabular and monospace layouts.

### Identified Bottlenecks & Recommendations for Next Iterations

| Area | Current State | Recommended Next Step |
|---|---|---|
| **Heuristic Constants** | Extracted to module constants (`BOX_MIN_W_FRAC`, `BOX_TOLERANCE_PX`, etc.) | Wrap into a `Config` dataclass so CLI or library consumers can override parameters per document. |
| **Monolithic File** | `scripts/pdf2md.py` (~900 lines) | Modularize into `pdf2md/` package (`regions.py`, `diagrams.py`, `heuristics.py`, `ocr.py`, `cli.py`). |
| **Math Chain Formatting** | Vertical chains (e.g., `X2(G) + XT1 + ...`) render as preformatted text blocks | Introduce a math chain recognizer that formats sums as single-line KaTeX / LaTeX math ($X_2(G) + X_{T1} + \dots$). |
| **Heading Confidence in OCR** | Headings in OCR rely solely on height and isolation because `bold` is False in Tesseract word output | Calculate line stroke width or font density from pixmap crops to infer bold weight in OCR mode. |
| **Debug Visualizer** | No layout inspection tooling | Add `--debug-viz DIR` to dump annotated PNGs with detected box overlays and paragraph bounding boxes. |

---

## 3. Test Evidence

### A. Unit Tests (`tests/test_heuristics.py`)
```bash
python tests/test_heuristics.py
.............
----------------------------------------------------------------------
Ran 13 tests in 0.002s

OK
```

### B. End-to-End Smoke Test (`tests/smoke_test.py`)
```bash
python tests/smoke_test.py
Processing page 1/1...
Wrote .../sample_scan.md (278 chars)
OK: test PDF is image-only style (no extractable text layer)
OK: OCR output contains expected content
SMOKE TEST PASSED
```

### C. Regression on 6-Page Test Document (`electricalDiagramOCR.pdf`)
- **Page 1:** Flowchart converted to Mermaid `flowchart TD`; JSON box correctly fenced with ````json````.
- **Page 2:** CV feature bullet list preserved; prompt example preserved; JSON box correctly fenced with ````json````.
- **Page 3:** `import networkx as nx` recognized as single-line ````python````; `G | T1 | LINE | T2 | M` flow converted to Mermaid flowchart; JSON continuation fenced with ````json````.
- **Page 4:** Sequence math chains preserved as clean preformatted blocks; transformer rule and class Generator fenced with ````python````.
- **Page 5:** Domain classes and object instantiation fenced with ````python````; tree diagram converted to Mermaid.
- **Page 6:** Sub-figure tree converted to Mermaid; 6-item SLD workflow preserved as a structured Markdown list instead of a collapsed paragraph.

---

## 3b. Verification & Hardening Pass (primary coder)

The branch's claims were independently verified against the 6-page test
PDF, the smoke test, and the unit suite. Findings and fixes applied on
top of this branch:

- **Verified working:** robust box detection (13 boxes across the doc vs
  4 before), language tagging (`json`/`python`/`mermaid`), Mermaid
  synthesis for the page-1 pipeline tree and page-5/6 trees, page-6
  6-step list reconstruction, indentation preservation, cross-platform
  tesseract discovery.
- **Fixed: glued gutter digits in `clean_box_text`.** Raw box OCR emits
  bare gutter-number lines for empty code lines (e.g. a lone `3`),
  which survived cleaning and leaked into output; OCR also glues the
  *next* gutter number onto closing tokens (`}5`). `clean_box_text` now
  drops bare-number lines and splits glued digits after `}`/`)` only.
  A first attempt stripped digits after any word char and was rejected —
  it truncated real math tokens (`XT2` → `XT`); the conservative rule
  is deliberate.
- **Not fixable at this layer (accepted):** OCR glyph noise inside the
  page-3 JSON box (`"transformer", |`, `re", "r1"4`) and mermaid node
  misreads (`71`/`72` for `T1`/`T2`) — inherent Tesseract quality on
  the degraded scan, confirmed by OCRing an exact-interior crop by hand.
- **Tests:** 4 new unit tests for `clean_box_text` (bare gutters, glued
  brace, math-token preservation, real-digit preservation); suite is
  now 17 tests, all passing, smoke test passing.

## 3c. Cross-Page Continuation Pass

User-observed issues after the audit merge, all fixed:

- **Split code boxes merged.** The page-2→3 JSON block (gutters 1–8 then
  9–22) and the page-3→4 "Positive Sequence" math block (gutters 1–6
  then 7–9) are now detected as continuations by `boxes_continuing`
  (x-extent overlap >= 60%, top-of-page position, gutter-number sequence
  gap <= 4) and merged into a single fenced block on the earlier page.
  Merged blocks are re-rendered through the same path as fresh boxes
  (Mermaid synthesis first, then language tagging), so a tree diagram
  split across pages 5–6 renders as one `flowchart TD`.
- **Root cause of the missing Positive Sequence content**: `find_boxes`
  paired top/bottom borders by endpoint equality (tolerance 85 px); on
  this scan the skewed top border of that box starts ~140 px right of
  the bottom border. Pairing now uses >= 50% horizontal overlap instead,
  which also found the box on page 1 and page 5 x-extent variants.
- **Box masking covers continuation boxes**, so consumed content cannot
  re-enter the output through the prose pass.
- **Glued '+' repairs**: OCR reading a gutter digit as '+' before a
  closing brace (`22 }` -> `+}`) is now cleaned to `}`; the earlier
  trailing-digit strip stays conservative (only after `}`/`)`) because a
  broader rule truncated real math tokens (`XT2` -> `XT`).
- Known accepted: `rating mva: 102` (real `100,`; Tesseract `,`->`5`
  confusion is upstream), residual glyph noise inside the page-3 JSON
  box, and imperfect edge reconstruction in the merged page-5→6
  Mermaid tree (branch glyph misreads change parent-child edges).
- Tests: 6 new unit tests for `box_gutter_range`/`boxes_continuing`
  (suite: 23 tests, all passing); smoke test passing.

## 4. Branch & Git History

- **Branch Name:** `vibe/mermaid-lang-detect-and-optimizations`
- **Base:** `main`
- **Key Modified Files:**
  - `scripts/pdf2md.py`: Added language estimation, mermaid conversion, robust box detection, indentation preservation.
  - `tests/smoke_test.py`: Cross-platform temp path and dynamic tesseract configuration.
  - `tests/test_heuristics.py`: 13 pure-geometry heuristic unit tests.
  - `output.md`: Refreshed with the enhanced pipeline output.

---

## 5. Function-by-Function Diff Report

This section isolates each changed or added function so the primary coder can analyze individual modifications directly without wading through the full 900-line codebase.

### 1. `estimate_code_language(code)` [NEW]
* **Location:** [`scripts/pdf2md.py:L182-L244`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L182-L244)
* **Purpose:** Replaces bare ```` ``` ```` fences with estimated language tags (`json`, `python`, `mermaid`, `yaml`, `bash`, `text`).
* **Logic:** Checks strict/structural JSON (including unbracketed snippets), Python keywords and domain patterns (`Transformer(name=...)`, `if transformer...:`), Mermaid diagrams, and Bash commands. Includes a prose detector guard to prevent English prompt sentences from being misidentified as YAML.
```python
def estimate_code_language(code):
    if not code or not code.strip():
        return ""
    text = code.strip()
    if re.match(r"^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|mindmap)\b", text):
        return "mermaid"
    if text[0] in ("{", "["): ... # json detection
    if len(re.findall(r'"[\w\s.-]+"\s*:\s*("[^"]*"|\d+|true|false|null)', text)) >= 2: return "json"
    if py_score >= 1 ...: return "python"
    ...
```

### 2. `parse_ascii_diagram_to_mermaid(lines)` [NEW]
* **Location:** [`scripts/pdf2md.py:L247-L380`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L247-L380)
* **Purpose:** Parses ASCII flowcharts, box-drawing trees, and linear chains into native Mermaid syntax.
* **Logic:**
  1. Detects linear horizontal pipelines (`G | T1 | LINE | T2 | M` $\rightarrow$ `flowchart LR: N1["G"] --> N2["T1"] --> ...`).
  2. Detects vertical flowcharts with arrows (`|`, `v`, `V`, `Vv`, `▼`, `↓`) and converts to `flowchart TD`.
  3. Detects tree branches (`├──`, `└──`, `|-`, `L-`, `L_`, `L.`) and establishes parent-child relationships.
  4. Scoped character cleaning normalizes common OCR defects (`Je Civil Drawing` $\rightarrow$ `Civil Drawing`, `r- P&ID` $\rightarrow$ `P&ID`).
  5. Negative guards ensure vertical math addition chains (`+`) and bulleted prompt lists are not falsely parsed as diagrams.

### 3. `check_vstrip_continuous(dark, y1, y2, x_center, half_w=10, max_break=30)` [NEW]
* **Location:** [`scripts/pdf2md.py:L582-L604`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L582-L604)
* **Purpose:** Replaces the single-column vertical border check with a skew- and noise-tolerant band check.
* **Why:** In real scans, a ~0.2° tilt slopes vertical lines across 5–6 columns over 900 px. Checking a single column $x$ caused all long vertical borders on pages 2, 3, 4, 5, 6 to fail.
```python
def check_vstrip_continuous(dark, y1, y2, x_center, half_w=10, max_break=30):
    strip = dark[int(y1):int(y2), int(x_center - half_w):int(x_center + half_w + 1)]
    has_pixel = numpy.any(strip, axis=1)
    if numpy.mean(has_pixel) < 0.70:
        return False
    # Verifies maximum break threshold to prevent merging separate stacked boxes
    ...
```

### 4. `find_boxes(dark, min_w_frac, min_h, max_gap)` [MODIFIED]
* **Location:** [`scripts/pdf2md.py:L607-L701`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L607-L701)
* **Purpose:** Finds all bounded boxes on the page mask.
* **Diff:**
```diff
-def find_boxes(dark, min_w_frac=0.15, min_h=40):
+def find_boxes(dark, min_w_frac=BOX_MIN_W_FRAC, min_h=BOX_MIN_H, max_gap=BOX_MAX_GAP):
     ...
+    # 1. Added max_gap lookahead in hline_runs to bridge 1-3px breaks
+    # 2. Segment clustering stitches adjacent tilted horizontal runs
+    # 3. Replaced vline_runs single-column check with check_vstrip_continuous:
-    lv = rv = False
-    for dx in range(-6, 7):
-        for a, b in vline_runs(s + dx):
-            if a <= y + 6 and b >= y2 - 6: lv = True
+    lv = check_vstrip_continuous(dark, y, y2, box_s)
+    rv = check_vstrip_continuous(dark, y, y2, box_e)
+    if lv and rv: boxes.append(...)
```

### 5. `clean_block_lines(block)` [NEW]
* **Location:** [`scripts/pdf2md.py:L421-L436`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L421-L436)
* **Purpose:** Filters stray 1–2 character OCR artifacts that vertically overlap longer text lines.
* **Why:** On Page 6, the OCR artifact `Pp` at y=689 overlapped `Detect diagram region` at y=688. This distorted the block coordinate spread and caused `looks_like_list` to reject the block and merge all 6 list items into a single paragraph.

### 6. `render_block(block, median_height, max_size)` [MODIFIED]
* **Location:** [`scripts/pdf2md.py:L439-L488`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L439-L488)
* **Diff:**
```diff
 def render_block(block, median_height, max_size):
     """Convert one block of lines into Markdown."""
+    block = clean_block_lines(block)
+    if not block:
+        return None
     texts = [l["text"].strip() for l in block]
     first = block[0]
```

### 7. `clean_box_text(text)` [MODIFIED]
* **Location:** [`scripts/pdf2md.py:L785-L800`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L785-L800)
* **Purpose:** Preserves code block indentation after stripping gutter numbers.
* **Diff:**
```diff
 def clean_box_text(text):
     out = []
     for raw in text.splitlines():
-        line = raw.strip()
-        if not line:
-            continue
-        m = re.match(r"^(\d{1,3})[.)]?\s+(\S.*)$", line)
+        if not raw.strip():
+            continue
+        m = re.match(r"^\s*(\d{1,3})[.)]?[ \t](.*)$", raw)
         if m:
-            line = m.group(2)
+            line = m.group(2).rstrip()
+        else:
+            line = raw.strip()
         out.append(line)
     return "\n".join(out)
```

### 8. `merge_regions(...)` & `page_to_markdown(...)` [MODIFIED]
* **Locations:** [`merge_regions: L802-L828`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L802-L828), [`page_to_markdown: L536-L577`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L536-L577)
* **Purpose:** Integrates Mermaid conversion and language estimation into the rendering pipelines.
* **Diff in `merge_regions`:**
```diff
-        events.append((y0, 0, "box", "```\n" + cleaned + "\n```"))
+        if use_mermaid:
+            is_diag, mermaid_code = parse_ascii_diagram_to_mermaid(cleaned.splitlines())
+            if is_diag:
+                events.append((y0, 0, "box", "```mermaid\n" + mermaid_code + "\n```"))
+                continue
+        lang_tag = estimate_code_language(cleaned)
+        fence = f"```{lang_tag}" if lang_tag else "```"
+        events.append((y0, 0, "box", f"{fence}\n{cleaned}\n```"))
```

### 9. `configure_tesseract()` [NEW]
* **Location:** [`scripts/pdf2md.py:L862-L883`](file:///o:/Data/Programming/Github/pdf2md/scripts/pdf2md.py#L862-L883)
* **Purpose:** Automatically locates the Tesseract binary across environments without requiring manual env vars.
* **Execution:** Executed automatically at module import time and inside `main()`.

### 10. `tests/smoke_test.py` [MODIFIED]
* **Location:** [`tests/smoke_test.py`](file:///o:/Data/Programming/Github/pdf2md/tests/smoke_test.py)
* **Diff:**
```diff
-TEST_PDF = "/tmp/sample_scan.pdf"
-OUT_MD = "/tmp/sample_scan.md"
+TEMP_DIR = tempfile.gettempdir()
+TEST_PDF = os.path.join(TEMP_DIR, "sample_scan.pdf")
+OUT_MD = os.path.join(TEMP_DIR, "sample_scan.md")
+TMP_TEXT_PDF = os.path.join(TEMP_DIR, "_text.pdf")
```
* Dynamically resolves `TESSERACT_CMD` across platform default paths when not already set in `os.environ`.

