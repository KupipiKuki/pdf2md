# pdf2md — Handoff for Audit & Refactor

This document hands the `pdf2md` project to a new agent (Gemini) for an
audit and refactor. It summarizes the project's purpose, architecture,
design decisions (and the reasoning behind them), known limitations,
and refactor suggestions. Work should be delivered on a branch and
merged via PR as before.

## Project purpose

Convert scanned PDFs into structured Markdown. The driving test case is
a scanned technical document containing prose, headings, bullet lists,
printed code blocks with gutter line numbers, boxed code, ASCII/box-drawing
tree and flow diagrams, and vertical math chains. The tool must recover
as much document *structure* as possible, not just raw text.

CLI:

```
python scripts/pdf2md.py input.pdf [-o output.md] [--lang eng]
                                    [--dpi 300] [--prefer-text-layer]
                                    [--save-images DIR]
```

Dependencies (all user-level pip): `pymupdf`, `pytesseract`, `numpy`,
plus the `tesseract` binary (env var `TESSERACT_CMD` overrides the
path; `TESSDATA_PREFIX` for language data).

## Architecture (scripts/pdf2md.py, ~580 lines, single file)

Per page, the pipeline is:

1. `process_page_regions(page, dpi, lang)` — pre-OCR region pass.
   - Renders the page grayscale at `--dpi` (default 300).
   - `find_boxes(dark)` detects rectangular bordered regions on the
     binary page mask (long horizontal runs clustered into border
     segments, paired by y-distance, verified by vertical border runs).
   - Each box interior (inset by `BOX_BORDER_PAD=8` px) is OCR'd
     separately with Tesseract **PSM 6** (uniform block) via
     `ocr_region` → preserves line-by-line structure of code and tree
     diagrams.
2. `ocr_masked_page(img, box_results, dpi, lang)` — whites out detected
   boxes, then full-page OCR of the remainder with **PSM 3**
   (`image_to_data`, word-level) → prose line records.
3. `lines_from_ocr(data)` — normalizes word data into generic line
   records `{text, top, bottom, height, left, bold, size?}`; words with
   confidence < 30 are dropped. (All coordinates in rendered-pixel
   space.)
4. `merge_regions(prose_lines, box_results, ...)` — groups prose lines
   into gap-separated blocks (`group_into_paragraphs`, split when the
   vertical gap > 1.2 × median line height), renders each block to
   Markdown, and interleaves box blocks (as fenced code) by pixel
   y-position.
5. `page_to_markdown(lines)` — the structure heuristics for a set of
   lines (used for prose blocks; box text is fenced directly):
   - `detect_gutters` / `strip_inline_gutters` / `strip_gutter_prefix`
     — handle printed line-number gutters (standalone number lines, or
     numbers merged into line text by OCR).
   - `looks_like_code` — gutter lines, or majority of lines match
     code-ish regexes (braces/brackets/operators/keywords) → fence.
   - `looks_like_ascii_art` — pipes, arrows, `Vv`, `L_`, box-drawing
     ranges → fence to preserve layout.
   - `render_block` — bullets (`BULLET_RE` incl. OCR misreads of `•` as
     `e`/`o`/`©`), numbered lists, headings (single short line, large
     or bold, not sentence-ending), list fallback via
     `looks_like_list` (see below), else joined paragraph text.
   - `clean_box_text` — strips gutter numbers from box OCR lines.

Other paths: `lines_from_text_layer` supports `--prefer-text-layer`
(embedded text layer instead of OCR; coordinates scaled points→300dpi
pixels). `main()`/`convert()` handle CLI, per-page loop, page
separators (`<!-- page N (source) -->`), and optional page-image
embedding (`--save-images`).

## Design decisions & rationale (important for the audit)

1. **OCR is the default; the embedded text layer is opt-in.** The
   driving test PDF embeds an OCR layer from another engine that is
   *worse* than a fresh Tesseract pass (`ftonmal`→Normal, `genenator`
   →generator). Do not "optimize" by preferring the text layer by
   default.
2. **PSM 6 for boxed regions, PSM 3 for prose.** PSM 6 reads a cropped
   box line-by-line (correct for code/trees); PSM 3 auto-segments the
   masked page. This split is what fixed both the tree diagram and the
   boxed-code extraction.
3. **Generic line records decouple geometry from source.** Both OCR
   and text-layer paths emit the same `{text, top, bottom, height,
   left, bold, size}` dicts in pixel space, so all downstream
   heuristics work for either source. Preserve this contract in a
   refactor.
4. **Thresholds were empirically calibrated** on the test document at
   300 DPI. Key ones: paragraph split `gap > 1.2 × median height`;
   heading `height >= 1.15 × median` or bold, single line, < 80–100
   chars, not ending in `.,;:`; box detection min width 15% page, min
   height 40 px, segment cluster tolerance 6/12 px; border inset 8 px;
   OCR word confidence cutoff 30.
5. **`looks_like_list` is deliberately conservative.** It detects
   bullet lists whose glyphs OCR dropped entirely: ≥3 short (<60 char)
   lines, uniform vertical rhythm (gaps within ±40% of mean), left
   edge spread ≤ 30 px if any bullet-ish marker exists, ≤ 6 px if not,
   plus a "word-like items" regex guard. The guards exist because an
   earlier version turned the math chain `Xs(G) + XT1 + nm XLINE +`
   into a fake bullet list. Any loosening must re-test that case
   (test PDF page 3).
6. **No new heavyweight dependencies.** numpy is the only addition
   (border-mask analysis). Avoid OCR frameworks (Docling, etc.) — the
   single-file zero-framework design is a project constraint.

## Known limitations (candidates for improvement, not regressions)

- **Unboxed diagrams** (pages 3–6 of the test doc, e.g. the vertical
  `G/T1/LINE/T2/M` chain and the page-6 tree) rely on the ASCII-art
  heuristic; some lines still merge into prose. Fix direction:
  whitespace-column/row segmentation to build regions without borders.
- **OCR glyph noise on character art**: `Vv` for ▼, `L_` for `├─`,
  `nm` for `+`, `hs`/`5` artifacts, `|` for `I`. Tesseract-level; a
  post-OCR substitution table inside fenced blocks is a possible fix,
  but risky for real text — keep it scoped to fenced diagram blocks.
- `merge_regions` re-OCRs nothing but re-fences box text via
  `clean_box_text` separately from `page_to_markdown`; the double
  cleaning path is slightly redundant.
- `looks_like_code`/`looks_like_ascii_art` regexes are tuned to the
  test document; expect false negatives on other documents.
- Headings inside prose blocks that are bold-but-not-large depend on
  the text-layer path only (`bold` is always False in the OCR path).
- No unit tests for the heuristics — only the end-to-end smoke test.

## Test procedure (must keep passing)

- `python tests/smoke_test.py` — builds a synthetic scanned PDF
  (rasterized via PyMuPDF so it has *no* text layer), converts it, and
  asserts expected content and structure (headings, list items,
  paragraph joining). Requires `TESSERACT_CMD` if tesseract is not on
  PATH.
- Manual regression: convert the 6-page scanned test PDF and check:
  page-1 tree diagram fenced line-by-line; page-2 JSON box clean with
  gutters stripped; `Components/Connectivity/...` renders as a bullet
  list; `Xs(G) + XT1 + ...` does *not* render as a list.

## Refactor suggestions

1. Split `scripts/pdf2md.py` into a package (e.g. `render.py`,
   `regions.py`, `ocr.py`, `lines.py`, `structure.py`, `cli.py`) while
   keeping the line-record dict as the shared contract; or keep the
   single file but group the functions under clear section comments.
2. Extract the magic numbers in §Design decisions 4 into a named
   constants block or dataclass so tuning is centralized.
3. Add unit tests for the pure-geometry heuristics
   (`find_boxes` on synthetic masks, `looks_like_list`,
   `group_into_paragraphs`, `clean_box_text`) — they are deterministic
   and easy to test with fabricated line records.
4. Consider a `--json` debug output (per-page line records and box
   coordinates) to make future layout work inspectable.
5. Type hints + a `pyproject.toml` with pinned dependency floors
   (`pymupdf>=1.24`, `pytesseract>=0.3.10`, `numpy>=1.26`).

## Collaboration conventions

- Trunk: `main`. Work on feature branches (`vibe/<slug>` or your own
  naming), deliver via draft PR, reference test evidence in the PR
  body (as in PRs #1 and #2).
- The repo has `.gitignore` for `__pycache__/` and `*.pyc`; keep
  compiled artifacts out of the tree.
- Smoke test must pass before every PR; run the manual regression on
  the 6-page test PDF for any change touching layout heuristics.
