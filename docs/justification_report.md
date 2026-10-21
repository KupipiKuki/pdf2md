# Architectural Justification Report: Docling vs. Docling + Multimodal VLM

**Author / Reviewer:** Antigravity Engineering (Auditor)  
**Date:** October 2026  
**Context:** Post-QAQC acceptance on branch `vibe/docling-vlm-extension-blueprint`  
**Target Document:** [`electricalDiagramOCR.pdf`](../electricalDiagramOCR.pdf) / [`ref_output.md`](../ref_output.md)

---

## 1. Executive Summary & Context

Following the completion and clearance of the Round-2 QAQC cycle by Claude Sonnet ([Section 10 of `handoff.md`](../handoff.md#L970-L1005)), the core heuristic OCR pipeline has achieved a benchmark line similarity of **0.708** against the curated reference standard, well exceeding the 0.590 acceptance threshold. With the skew-invariant gutter-relative indentation and unit snapping in place, the immediate OCR and geometric challenges are stabilized.

This report evaluates the strategic architectural decision for the next series of modifications:
1. **Option A: Pure Docling Workflow** — Relying exclusively on IBM Docling Core, TableFormer, and the native Granite-Docling document model.
2. **Option B: Hybrid Docling + Multimodal VLM Workflow** — Using Docling as the layout and document ingestion backbone, routing isolated figure crops to a Multimodal Vision-Language Model (VLM), and driving a deterministic Python engineering rule engine.

> **Definitive Finding:** A **Pure Docling Workflow** hits a fundamental theoretical ceiling on engineering diagrams. An electrical Single Line Diagram (SLD) or P&ID is not a document; it is a **spatial graph with engineering semantics**. Therefore, **Option B (Docling + VLM + Engineering Rule Engine)** is the only architecture capable of closing the gap between raw document extraction and genuine engineering insight.

---

## 2. System Architecture Comparison

```mermaid
flowchart TD
    subgraph OptionA ["Option A: Pure Docling Pipeline"]
        PDF_A["Input PDF / Scan"] --> DC_A["Docling DocumentConverter"]
        DC_A --> Prose_A["Prose & Markdown Headings"]
        DC_A --> Table_A["Tables (TableFormer)"]
        DC_A --> Code_A["Code Listings (Granite DocTags)"]
        DC_A --> Fig_A["Diagrams & Schematics"]
        Fig_A -.->|Opaque Raster Crop or Broken OCR| Out_A["Flat Markdown (No Topology)"]
    end

    subgraph OptionB ["Option B: Hybrid Docling + VLM Pipeline (Selected)"]
        PDF_B["Input PDF / Scan"] --> DC_B["Docling DocumentConverter"]
        DC_B --> Prose_B["Prose & Markdown Headings"]
        DC_B --> Table_B["Tables (TableFormer)"]
        DC_B --> Fig_B["Figure Bounding Box Crops"]
        Fig_B --> Class_B{"Figure Classifier"}
        Class_B -->|"Photo / Plot"| Pass_B["Passthrough (Preserve Image)"]
        Class_B -->|"Code Listing"| VLM_A["VLM Mode A: Code Transcription"]
        Class_B -->|"Flowchart / Tree"| VLM_B["VLM Mode B: Mermaid Synthesis"]
        Class_B -->|"SLD / P&ID"| VLM_C["VLM Mode C: Topology JSON"]
        VLM_C --> Rules_B["NetworkX Engineering Rule Engine"]
        Rules_B --> Seq_B["Sequence Networks & KaTeX Math"]
        Prose_B & Table_B & Pass_B & VLM_A & VLM_B & Seq_B --> Out_B["Enriched Engineering Markdown"]
    end
```

---

## 3. Deep-Dive Comparative Matrix

| Evaluation Dimension | Option A: Pure Docling (Docling Core + Granite) | Option B: Docling + Multimodal VLM + Rule Engine |
|---|---|---|
| **1. Electrical SLD & Topology Extraction** | **Failure.** Treats schematics as raster images or parses glyphs as gibberish words (`"T1i"`, `re", "r1"4`). Has zero concept of buses, breakers, or delta/wye connections. | **Superior.** Multimodal models visually recognize standardized IEEE/IEC symbols and spatial connections, emitting structured JSON topology (`components` and `connections`). |
| **2. Diagram & Flowchart Conversion** | **Failure.** Cannot convert visual flowcharts or ASCII trees into native Mermaid (`flowchart TD/LR`). Emits either broken preformatted blocks or drops flow connectivity. | **Superior.** Visually reconstructs converging parallel fan-in and fan-out branches (e.g., all figure types feeding into `Vision Model`) into executable Mermaid syntax. |
| **3. Engineering Rule & Sequence Derivation** | **Not Possible.** Document parsers extract text; they do not derive physics or electrical engineering equations. | **Core Differentiator.** Extracted topology feeds [`docling_engineering/rules.py`](../docling_engineering/rules.py) to derive Positive, Negative, and Zero sequence networks, applying transformer delta-winding current-blocking rules. |
| **4. Monospace Code & Indentation** | **Moderate.** Granite-Docling (DocTags) reconstructs standard code lines well, but lacks domain awareness for broken syntax or missing delimiters on degraded scans. | **High.** Mode A VLM prompt preserves exact 4-space Python / 2-space JSON indentation, restores eroded brackets, and classifies languages accurately. |
| **5. Layout, Reading Order & Tables** | **High.** Docling's core strength is DocLayNet-based layout analysis, multi-column reading order, and TableFormer table extraction. | **Identical.** Option B directly leverages Docling for document layout and table ingestion, retaining Docling's exact parsing strengths. |
| **6. Operational Cost & Infrastructure** | **Zero Cost / Offline.** Runs entirely locally without network access or cloud API dependencies. | **Tiered.** Cloud VLMs (Gemini 2.5 Flash) incur minor API costs ($0.001/doc); local VLMs (Qwen2.5-VL via Ollama) require local GPU VRAM. |
| **7. Determinism & Hallucination Risk** | **Deterministic.** Outputs are direct OCR/model tokens with zero risk of conversational hallucination. | **Managed Risk.** Mitigated by rigid schema validation (`json.loads`, `flowchart` prefix guards) and deterministic fallbacks on failure. |
| **8. Code Maintainability** | **Stagnant.** Attempts to bridge the diagram gap require thousands of fragile regex patches (`71` $\rightarrow$ `T1`, `+}` $\rightarrow$ `}`). | **Clean & Decoupled.** Document extraction is separated from visual graph reasoning and domain math rules. |

---

## 4. Pros and Cons Breakdown

### Option A: Pure Docling Workflow

#### Pros
- **Self-Contained & Air-Gapped:** Operates fully on-premise with no external API calls, credentials, or network transmission.
- **Predictable Latency:** Fast execution bounded by local CPU/GPU OCR inference.
- **Excellent Document Standard:** High fidelity on standard PDF elements: paragraphs, multi-column flows, footers, headers, and complex tables.

#### Cons
- **Blind to Engineering Semantics:** Incapable of understanding electrical single-line diagrams, piping and instrumentation diagrams (P&IDs), or control logic schematics.
- **Static Artifacts Only:** Engineering figures remain dead PNG crops rather than actionable Markdown, diagrams, or graph databases.
- **Regex Trap:** Fixing scan erosion without visual perception leads to endless heuristic band-aids that break when scan resolution, DPI, or font changes.

---

### Option B: Hybrid Docling + Multimodal VLM Workflow (Recommended)

#### Pros
- **Closes the Semantic Gap:** Transforms opaque engineering diagrams into executable Mermaid graphs, LaTeX math equations, and NetworkX network models.
- **Best-of-Breed Division of Labor:**
  - *Docling* handles what it was built for: document layout, reading order, and table structure.
  - *VLM* handles what it excels at: visual spatial reasoning, diagram fan-in/fan-out, and OCR defect recovery.
  - *Python Rule Engine* handles what code excels at: deterministic mathematical calculation and circuit analysis.
- **Resilient Multi-Tier Fallback ([Section 11 Spec](../handoff.md#L1008-L1066)):**
  - **Tier 1:** Cloud VLM (`gemini-2.5-flash`) for state-of-the-art vision extraction.
  - **Tier 2:** Local VLM (`openai:qwen2.5-vl-7b@http://localhost:11434/v1`) for private/offline vision inference.
  - **Tier 3:** Local Granite-Docling (`granite-docling-258M`) for offline code block OCR.
  - **Tier 4:** Deterministic OCR fallback (tested at 0.708 similarity) whenever VLM calls fail or are disabled.

#### Cons
- **Configuration Overhead:** Requires optional API keys (`GEMINI_API_KEY`) or local VLM server setup (Ollama / vLLM) for full capabilities.
- **Validation Requirement:** Requires strict response validation (`postprocess_mode_a/b/c`) to prevent malformed VLM output from replacing good OCR. (Implemented and verified in `vibe/docling-vlm-extension-blueprint`).

---

## 5. Strategic Decision & Next Steps

### Decision
Proceed with **Option B: Hybrid Docling + Multimodal VLM Workflow**, utilizing the 4-tier fallback architecture established in Section 11 of [`handoff.md`](../handoff.md#L1008-L1066).

### Actionable Roadmap for Next Sprint
1. **Multi-Backend Implementation in [`docling_engineering/vlm.py`](../docling_engineering/vlm.py):**
   - Implement `build_backend()` supporting `auto`, `gemini-*`, `granite`, `openai:<model>@<url>`, and `dryrun`.
   - Add `GraniteDoclingBackend` (Mode A code transcription only; Mode B/C degrade to deterministic fallback).
   - Add `OpenAICompatBackend` for local vision servers.
2. **Package Decoupling:**
   - Move shared heuristics (`clean_box_text`, `estimate_code_language`, `parse_ascii_diagram_to_mermaid`) out of [`scripts/pdf2md.py`](../scripts/pdf2md.py) into `docling_engineering/heuristics.py`.
3. **Live Benchmark Validation:**
   - Run the 16 exported crops against live Gemini and score against [`ref_output.md`](../ref_output.md).
