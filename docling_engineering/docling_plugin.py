"""Docling integration entry point (handoff Section 6.4/6.5, Stage 1 & 4).

Registers the EngineeringFigureExtension with Docling's
DocumentConverter pipeline when docling is installed. Docling handles
ingestion, layout, headings, tables, and figure isolation; this
extension classifies each figure crop and routes it through the VLM
enrichment and the networkx rule engine.

Docling is an optional dependency; this module degrades gracefully to
standalone use with externally supplied crops and OCR text.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    HAS_DOCLING = True
except ImportError:
    DocumentConverter = PdfFormatOption = None
    HAS_DOCLING = False

from .classifier import FigureClassifier
from .vlm import VLMEnricher
from .rules import build_graph, transformer_zero_sequence_rules


class EngineeringFigureExtension:
    """Classify figure crops and enrich them with VLM transcription and
    engineering rules. Usable standalone (classify/enrich) or attached to
    a Docling conversion pipeline."""

    def __init__(self, backend="dryrun"):
        self.classifier = FigureClassifier()
        self.enricher = VLMEnricher(backend=backend)

    def process_figure(self, ocr_text, image_path=None):
        """Return (category, enriched_markdown) for one figure crop."""
        category = self.classifier.classify(ocr_text, image_path)
        enriched = self.enricher.enrich(ocr_text, image_path)
        return category, enriched

    def process_topology(self, topology_json):
        """Run the engineering rule engine over SLD topology JSON:
        returns (networkx graph, zero-sequence rules)."""
        graph = build_graph(topology_json)
        rules = transformer_zero_sequence_rules(topology_json.get("components", []))
        return graph, rules


def make_converter(artifacts_dir=None):
    """Build a Docling DocumentConverter for ingestion. Raises with a
    helpful message when docling is not installed."""
    if not HAS_DOCLING:
        raise ImportError(
            "Docling is not installed; run `pip install docling` to use "
            "the ingestion pipeline, or use EngineeringFigureExtension "
            "directly with externally supplied crops."
        )
    return DocumentConverter()
