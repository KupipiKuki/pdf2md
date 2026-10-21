#!/usr/bin/env python3
"""Optional VLM enrichment pass for pdf2md (thin wrapper).

The implementation lives in the `docling_engineering` package
(classifier, prompts, VLM backends, rule engine); this wrapper keeps the
`--vlm` flag of scripts/pdf2md.py importable exactly as specified in the
handoff roadmap (Section 6.5, item 3).

Backends:
- ``gemini-<model>``: google-genai (Gemini) image + text prompt.
- ``dryrun``: no API calls; exercises the classification and routing
  logic and falls back to the deterministic OCR rendering.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from docling_engineering.vlm import (
        VLMEnricher,
        build_backend,
        GeminiBackend,
        DryRunBackend,
        OpenAICompatBackend,
        GraniteDoclingBackend,
    )
    from docling_engineering.classifier import FigureClassifier, CATEGORIES
    from docling_engineering.prompts import MODE_PROMPTS
except ImportError:  # script executed from an installed package
    from docling_engineering.vlm import (
        VLMEnricher,
        build_backend,
        GeminiBackend,
        DryRunBackend,
        OpenAICompatBackend,
        GraniteDoclingBackend,
    )
    from docling_engineering.classifier import FigureClassifier, CATEGORIES
    from docling_engineering.prompts import MODE_PROMPTS

__all__ = [
    "VLMEnricher",
    "build_backend",
    "GeminiBackend",
    "DryRunBackend",
    "OpenAICompatBackend",
    "GraniteDoclingBackend",
    "FigureClassifier",
    "CATEGORIES",
    "MODE_PROMPTS",
]
