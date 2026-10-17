"""Figure classification for engineering document crops.

Deterministic keyword/structure router over a box region's OCR text
(handoff Section 6.4, Stage 2). The same classify() interface can be
backed by a CV classifier (CLIP/ViT zero-shot) or a fast VLM call.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from pdf2md import clean_box_text, estimate_code_language, parse_ascii_diagram_to_mermaid

CATEGORIES = (
    "photograph",
    "plot_or_chart",
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

    def classify_text(self, text):
        t = (text or "").lower()
        if not t.strip():
            return "photograph"
        lang = estimate_code_language(clean_box_text(text))
        if lang in ("json", "python", "yaml", "bash"):
            return "code_listing"
        is_diag, _ = parse_ascii_diagram_to_mermaid(
            clean_box_text(text).splitlines())
        if is_diag:
            return "flowchart_or_tree"
        if any(k in t for k in self.PID_KEYWORDS):
            return "piping_and_instrumentation"
        if any(k in t for k in self.SLD_KEYWORDS):
            return "electrical_sld"
        return "code_listing"

    def classify(self, text, image_path=None):
        return self.classify_text(text)

    def mode_for(self, category):
        """Map a category to its VLM prompt mode (A/B/C, handoff 6.4)."""
        if category in ("electrical_sld", "piping_and_instrumentation"):
            return "C", "sld_topology"
        if category in ("flowchart_or_tree", "photograph", "plot_or_chart"):
            return "B", "diagram_to_mermaid"
        return "A", "code_transcription"
