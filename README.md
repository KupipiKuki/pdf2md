# pdf2md

Scanned PDF to Markdown converter with OCR.

Renders each PDF page, runs Tesseract OCR, and assembles the recognized
text into structured Markdown: paragraphs, headings, bullet/numbered
lists, fenced code blocks, and preserved ASCII diagrams.

## Install

```bash
pip install pymupdf pytesseract numpy
sudo apt install tesseract-ocr        # Linux; `brew install tesseract` on macOS
```

## Usage

```bash
python scripts/pdf2md.py input.pdf -o output.md
```

| Option | Description |
|---|---|
| `-o, --output` | Output Markdown path (default: `input.pdf` → `input.md`) |
| `--lang LANG` | Tesseract language code (default: `eng`) |
| `--dpi N` | Render resolution (default: `300`; use `400` for small print) |
| `--prefer-text-layer` | Use the PDF's embedded text layer instead of OCR. Caution: scanned PDFs often embed a low-quality OCR layer that is worse than a fresh Tesseract pass. |
| `--save-images DIR` | Also save page PNGs and embed them in the Markdown |
| `--export-crops DIR` | Save every detected box region as an isolated PNG (`pageN_boxK.png`) for VLM benchmarking and classifier training |
| `--vlm [BACKEND]` | Route detected box crops through a Vision-Language Model for code and diagram transcription. Supports local models via `openai:<model>@<url>` (e.g. `openai:Qwen3-VL-8B-Instruct@http://localhost:8000/v1` or `local:<model>`), `granite` for local Docling Granite, `gemini-2.5-flash`, or `auto` / `dryrun`. Omitted = deterministic OCR only. |

If the `tesseract` binary is not on your `PATH`, point to it with the
`TESSERACT_CMD` environment variable.

### Local LLMs & VLMs (Unsloth, Qwen-VL, HF Llama Windows, Ollama)

`pdf2md` connects to any OpenAI-compatible multimodal endpoint using standard Python library networking (no extra pip packages required):

```bash
# Unsloth / vLLM (e.g. Qwen3-VL-8B-Instruct) on port 8000:
python scripts/pdf2md.py input.pdf --vlm openai:Qwen3-VL-8B-Instruct@http://localhost:8000/v1

# Hugging Face Llama for Windows / llama-server on port 8080:
python scripts/pdf2md.py input.pdf --vlm local:Llama-3.2-11B-Vision@http://localhost:8080/v1

# Ollama on port 11434:
python scripts/pdf2md.py input.pdf --vlm openai:qwen2.5-vl:7b@http://localhost:11434/v1

# Auto-detection via environment variable:
export OPENAI_BASE_URL="http://localhost:8000/v1"
python scripts/pdf2md.py input.pdf --vlm
```

## How it works

1. **Render** each page to grayscale PNG at the configured DPI (PyMuPDF).
2. **Detect bordered regions** (code boxes, boxed diagrams) from the
   page image by finding rectangular border lines. Each box is OCR'd
   separately with block-mode PSM 6, preserving its line-by-line
   structure (tree diagrams, vertical chains, code). Boxes are masked
   out before the prose pass and re-inserted as fenced blocks in
   reading order.
3. **OCR** the remaining page with Tesseract (`image_to_data`, PSM 3),
   keeping word positions and confidences; words below confidence 30
   are dropped.
3. **Assemble Markdown** from the word geometry:
   - Words → lines → paragraphs, split on vertical gaps
     (> 1.2 × median line height).
   - Single short bold/large-font lines become `##` headings.
   - Gutter line numbers from printed code blocks are detected and
     stripped; code-looking blocks are fenced with ` ``` `.
   - Bullet lists are detected across OCR variants of `•` (`e`, `o`, `©`).
   - ASCII flowcharts (pipes, `Vv`, `L_`, box-drawing characters) are
     fenced to preserve their layout.
4. **Cross-page continuation**: bordered boxes split by a page break are
   matched by x-extent overlap and gutter-number sequence, and merged
   into a single fenced block (re-rendered, e.g. as Mermaid when
   applicable) on the earlier page.

## Tests

```bash
python tests/test_heuristics.py
python tests/smoke_test.py
python scripts/benchmark_ref.py
```

`test_heuristics.py` unit-tests the pure heuristics (language estimation,
Mermaid synthesis, gutter cleaning, cross-page continuation, word-geometry
indentation reconstruction, figure classification, VLM routing, and the
networkx rule engine). `smoke_test.py` builds a synthetic scanned PDF
(rasterized, no text layer), converts it, and asserts the expected text and
structure are recovered. `benchmark_ref.py` scores `output.md` against
`ref_output.md` for regression tracking.

## Docling Engineering Extension

The `docling_engineering/` package implements the Engineering Figure
Extension blueprint: `FigureClassifier` (six figure categories),
`VLMEnricher` (Mode A/B/C domain prompts; Gemini or dryrun backends),
`rules.py` (networkx graph builder and transformer zero-sequence rules),
and `docling_plugin.EngineeringFigureExtension` for registering with
Docling's `DocumentConverter` pipeline (`pip install docling`, optional).
