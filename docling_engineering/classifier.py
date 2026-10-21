"""Figure classification for engineering document crops.

Deterministic keyword/structure router over a box region's OCR text
(handoff Section 6.4, Stage 2). The same classify() interface can be
backed by a CV classifier (CLIP/ViT zero-shot) or a fast VLM call.

Per the blueprint, photographs and plots are NOT sent to Mermaid or
topology extraction: they pass through unchanged (standard Docling
image handling), so classify() distinguishes them by OCR emptiness /
non-textual content and mode_for() maps them to a passthrough mode.
"""

from .heuristics import (
    clean_box_text,
    estimate_code_language,
    parse_ascii_diagram_to_mermaid,
    parse_ascii_diagram_block,
)

CATEGORIES = (
    "photograph",
    "plot_or_chart",
    "tree_diagram",
    "flowchart_or_tree",
    "code_listing",
    "electrical_sld",
    "piping_and_instrumentation",
)


class FigureClassifier:
    """Classify a figure crop (via its OCR text) into one of the six
    engineering figure categories."""

    SLD_KEYWORDS = ("sld", "single-line", "single line", "bus", "breaker",
                    "transformer", "relay", "ansi")
    PID_KEYWORDS = ("p&id", "piping", "instrumentation", "valve")
    PLOT_KEYWORDS = ("axis", "xlabel", "ylabel", "chart", "graph of",
                     "plot of", "curve", "waveform")

    def classify_text(self, text):
        t = (text or "").lower()
        if not t.strip():
            return "photograph"
        if any(k in t for k in self.PLOT_KEYWORDS):
            return "plot_or_chart"
        lang = estimate_code_language(clean_box_text(text))
        if lang in ("json", "python", "yaml", "bash"):
            return "code_listing"
        is_diag, block = parse_ascii_diagram_block(
            clean_box_text(text).splitlines())
        if is_diag:
            if block.startswith("```treeView-beta"):
                return "tree_diagram"
            return "flowchart_or_tree"
        if any(k in t for k in self.PID_KEYWORDS):
            return "piping_and_instrumentation"
        if any(k in t for k in self.SLD_KEYWORDS):
            return "electrical_sld"
        return "code_listing"

    def classify(self, text, image_path=None):
        return self.classify_text(text)

    def mode_for(self, category):
        """Map a category to its VLM prompt mode (A/B/C, handoff 6.4).
        Photographs and plots are passthrough: no VLM call."""
        if category in ("photograph", "plot_or_chart"):
            return None, "passthrough"
        if category in ("electrical_sld", "piping_and_instrumentation"):
            return "C", "sld_topology"
        if category == "flowchart_or_tree":
            return "B", "diagram_to_mermaid"
        if category == "tree_diagram":
            return "B", "tree_to_treeview"
        return "A", "code_transcription"
