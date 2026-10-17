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

try:
    from pdf2md import clean_box_text, estimate_code_language
except ImportError:  # executed outside the repo (installed package)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    from pdf2md import clean_box_text, estimate_code_language
from .classifier import FigureClassifier
from .prompts import MODE_PROMPTS
from .rules import sequence_markdown

FENCE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


class GeminiBackend:
    """google-genai multimodal backend. Requires `pip install google-genai`
    and GEMINI_API_KEY (or ADC) in the environment. API errors, timeouts,
    and missing credentials never abort the conversion: complete()
    returns "" so the enricher falls back to the deterministic rendering."""

    TIMEOUT_SECONDS = 60

    def __init__(self, model="gemini-2.5-flash"):
        from google import genai  # deferred import: optional dependency
        self.client = genai.Client()
        self.model = model

    def complete(self, image_path, prompt):
        try:
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
                config={"http_options": {"timeout": self.TIMEOUT_SECONDS * 1000}},
            )
            return response.text or ""
        except Exception:
            return ""


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
        return (m.group(1) or "", m.group(2).strip("\n"))
    return "", response.strip()


def postprocess_mode_a(response):
    """Re-fence the VLM's code transcription, preserving the language tag
    it produced (Mode A prompt rule 1) or falling back to the heuristic
    estimator. Returns None when the response is not usable."""
    lang, code = _strip_outer_fence(response)
    if not code.strip():
        return None
    if not lang:
        lang = estimate_code_language(code)
    fence = "```" + lang if lang else "```"
    return fence + "\n" + code + "\n```"


def postprocess_mode_b(response):
    """Re-fence a Mermaid synthesis, validating the response is a
    flowchart/graph before accepting it. Returns None otherwise."""
    lang, code = _strip_outer_fence(response)
    if lang and lang != "mermaid":
        return None
    if not re.match(r"^(flowchart|graph)\b", code.strip()):
        return None
    return "```mermaid\n" + code + "\n```"


def postprocess_mode_c(response):
    """Re-fence SLD topology JSON, validating it parses and matches the
    schema shape (components + connections). Returns None otherwise."""
    lang, code = _strip_outer_fence(response)
    if lang and lang != "json":
        return None
    try:
        topology = json.loads(code)
    except Exception:
        return None
    if not (isinstance(topology, dict) and "components" in topology
            and "connections" in topology):
        return None
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
        if mode is None:
            # photograph / plot_or_chart passthrough: no VLM call
            return None
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

    def topology_markdown(self, enriched_mode_c):
        """Given a Mode C fenced JSON block, run the rule engine
        (build_graph + sequence networks) and return extra Markdown
        blocks (sequence-network Mermaid diagrams), or "" when the
        topology is not usable."""
        try:
            _, code = _strip_outer_fence(enriched_mode_c)
            topology = json.loads(code)
            return sequence_markdown(topology)
        except Exception:
            return ""

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
