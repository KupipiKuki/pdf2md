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

## 4. Branch & Git History

- **Branch Name:** `vibe/mermaid-lang-detect-and-optimizations`
- **Base:** `main`
- **Key Modified Files:**
  - `scripts/pdf2md.py`: Added language estimation, mermaid conversion, robust box detection, indentation preservation.
  - `tests/smoke_test.py`: Cross-platform temp path and dynamic tesseract configuration.
  - `tests/test_heuristics.py`: 13 pure-geometry heuristic unit tests.
  - `output.md`: Refreshed with the enhanced pipeline output.
