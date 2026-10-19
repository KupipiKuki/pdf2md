"""docling_engineering: Engineering Figure Extension for IBM Docling.

Packages the pdf2md figure classifier, VLM enrichment router, and
networkx rule engine as a standalone module that can be registered with
Docling's DocumentConverter pipeline (handoff Section 6.4).
"""

from .classifier import FigureClassifier, CATEGORIES
from .vlm import (
    VLMEnricher,
    build_backend,
    GeminiBackend,
    DryRunBackend,
    OpenAICompatBackend,
    GraniteDoclingBackend,
)
from .heuristics import (
    clean_box_text,
    estimate_code_language,
    parse_ascii_diagram_to_mermaid,
    parse_ascii_diagram_block,
    is_tree,
    render_tree_view,
    reconstruct_indented_lines,
    box_gutter_range,
    boxes_continuing,
)
from .rules import (
    build_graph,
    sequence_networks,
    sequence_mermaid,
    sequence_markdown,
    chain_reactances,
    transformer_zero_sequence_rules,
)

__all__ = [
    "FigureClassifier",
    "CATEGORIES",
    "VLMEnricher",
    "build_backend",
    "GeminiBackend",
    "DryRunBackend",
    "OpenAICompatBackend",
    "GraniteDoclingBackend",
    "clean_box_text",
    "estimate_code_language",
    "parse_ascii_diagram_to_mermaid",
    "parse_ascii_diagram_block",
    "is_tree",
    "render_tree_view",
    "reconstruct_indented_lines",
    "box_gutter_range",
    "boxes_continuing",
    "build_graph",
    "sequence_networks",
    "sequence_mermaid",
    "sequence_markdown",
    "chain_reactances",
    "transformer_zero_sequence_rules",
]
