"""VLM backend adapters and enrichment router (handoff Section 6.4/6.5).

Routes classified figure crops to a multimodal model with the
Mode A/B/C prompts, post-processes the responses into fenced
Markdown blocks, and falls back to deterministic rendering when no
VLM output is available (dryrun backend, missing crop, API error).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from pdf2md import clean_box_text
from .classifier import FigureClassifier
from .prompts import MODE_PROMPTS

FENCE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


class GeminiBackend:
    """google-genai multimodal backend. Requires `pip install google-genai`
    and GEMINI_API_KEY (or ADC) in the environment."""

    def __init__(self, model="gemini-2.5-flash"):
        try:
            from google import genai
        except ImportError:
            sys.exit("Missing dependency for --vlm: run `pip install google-genai`")
        self.client = genai.Client()
        self.model = model

    def complete(self, image_path, prompt):
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        response = self.client.models.generate_content(
            model=self.model,
            contents=[{
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": image_bytes}},
                    {"text": prompt},
                ]
            }],
        )
        return response.text or ""


class DryRunBackend:
    """No-op backend for pipeline testing: always falls back to the
    deterministic rendering, exercising routing without API calls."""

    def complete(self, image_path, prompt):
        return ""


def build_backend(name):
    if name == "dryrun":
        return DryRunBackend()
    if name.startswith("gemini"):
        return GeminiBackend(model=name)
    raise ValueError(f"Unknown VLM backend: {name!r}")


def _strip_outer_fence(response):
    m = FENCE_RE.search(response)
    if m:
        return m.group(2).strip("\n")
    return response.strip()


def postprocess_mode_a(response):
    return "```\n" + _strip_outer_fence(response) + "\n```"


def postprocess_mode_b(response):
    return "```mermaid\n" + _strip_outer_fence(response) + "\n```"


def postprocess_mode_c(response):
    code = _strip_outer_fence(response)
    return "```json\n" + code + "\n```"


class VLMEnricher:
    """Enrich pdf2md box results with VLM transcription when available,
    falling back to the deterministic OCR rendering otherwise."""

    def __init__(self, backend="dryrun", crops_dir=None):
        self.backend = build_backend(backend)
        self.classifier = FigureClassifier()
        self.crops_dir = crops_dir

    def enrich(self, text, image_path):
        category = self.classifier.classify(text, image_path)
        mode, prompt_key = self.classifier.mode_for(category)
        prompt = MODE_PROMPTS[prompt_key]
        response = ""
        if image_path and os.path.isfile(image_path):
            response = self.backend.complete(image_path, prompt) or ""
        if not response.strip():
            return None
        if mode == "A":
            return postprocess_mode_a(response)
        if mode == "B":
            return postprocess_mode_b(response)
        return postprocess_mode_c(response)

    def enrich_box_results(self, box_results, crop_paths, fallback):
        crop_paths = crop_paths or {}
        overrides = {}
        for box, text in box_results:
            enriched = self.enrich(text, crop_paths.get(box))
            if enriched:
                overrides[box] = enriched
            else:
                cleaned = clean_box_text(text)
                if cleaned:
                    overrides[box] = fallback(cleaned)
        return overrides
