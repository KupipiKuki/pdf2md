"""VLM backend adapters and enrichment router (handoff Sections 6.4/6.5 and Section 11).

Routes classified figure crops to a multimodal model with the
Mode A/B/C prompts, post-processes the responses into fenced
Markdown blocks, and falls back to deterministic rendering when no
VLM output is available (dryrun backend, missing crop, API error).

Supported backends:
- Local models (Unsloth Qwen3-VL, Qwen2.5-VL, Llama-Vision, Ollama, vLLM, llama.cpp):
  via `OpenAICompatBackend` (`openai:<model>@<url>` or `local:<model>@<url>`).
- Local document conversion:
  via `GraniteDoclingBackend` (`granite`), using `granite-docling-258M`.
- Cloud multimodal models:
  via `GeminiBackend` (`gemini-2.5-flash`, etc.).
- Auto-resolution:
  `auto` (checks OPENAI_BASE_URL, GEMINI_API_KEY, Granite, or dryrun).
"""

import base64
import json
import os
import re
import sys
import urllib.request
import urllib.error

from .heuristics import clean_box_text, estimate_code_language
from .classifier import FigureClassifier
from .prompts import MODE_PROMPTS
from .rules import sequence_markdown

FENCE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)

VLM_DEBUG = bool(os.environ.get("PDF2MD_DEBUG"))


def _dbg(msg):
    if VLM_DEBUG:
        print(f"[pdf2md:vlm] {msg}", file=sys.stderr)


class OpenAICompatBackend:
    """OpenAI-compatible multimodal backend for local LLMs (Unsloth, vLLM, Ollama,
    llama.cpp, Hugging Face, LM Studio).

    Works seamlessly with models like Qwen3-VL-8B-Instruct, Qwen2.5-VL, Llama-Vision, etc.
    Connects to any standard /v1/chat/completions endpoint using standard library
    urllib.request (zero extra dependencies).
    """

    # Model-swapping local servers (llama.cpp router, LM Studio) can take
    # 30-60s to load a model on the first request; allow overriding via env.
    TIMEOUT_SECONDS = int(os.environ.get("PDF2MD_VLM_TIMEOUT", "60"))

    URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?(/.*)?$")

    def __init__(self, model="Qwen3-VL-8B-Instruct", base_url="http://localhost:8000/v1", api_key=None):
        self.model = model or "Qwen3-VL-8B-Instruct"
        self.base_url = (base_url or "http://localhost:8000/v1").rstrip("/")
        if not self.URL_RE.match(self.base_url):
            print(
                f"[docling-engineering] OpenAI-compatible backend base_url looks "
                f"malformed: {self.base_url!r} (expected e.g. "
                f"'http://localhost:8000/v1'). Check the @<url> part of the "
                "--vlm/--polish specifier.",
                file=sys.stderr,
            )
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "not-needed")
        self._unreachable_warned = False

    def complete(self, image_path, prompt):
        try:
            with open(image_path, "rb") as f:
                b64_img = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_img}"
                                }
                            }
                        ]
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 2048,
            }
            req_data = json.dumps(payload).encode("utf-8")
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                choice = res_json.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                    return "".join(text_parts).strip()
                return (content or "").strip()
        except Exception as exc:
            # Fall back to deterministic OCR rendering on connection error or timeout
            if not self._unreachable_warned:
                detail = ""
                body = getattr(exc, "read", None)
                if body is not None:
                    try:
                        detail = " :: " + body().decode("utf-8", "replace")[:500]
                    except Exception:
                        pass
                print(
                    f"[docling-engineering] OpenAI-compatible backend ({self.base_url}) "
                    f"unreachable or call failed: {exc}{detail}; "
                    "falling back to deterministic OCR.",
                    file=sys.stderr,
                )
                self._unreachable_warned = True
            _dbg(f"{type(self).__name__}.complete failed for {image_path!r}: {exc!r}")
            return ""

    def complete_text(self, prompt):
        """Text-only chat completion: no image part in the payload, for
        pure-text local models (Section 14.2). Fails closed like complete()."""
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 4096,
            }
            req_data = json.dumps(payload).encode("utf-8")
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                choice = res_json.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                    return "".join(text_parts).strip()
                return (content or "").strip()
        except Exception as exc:
            if not self._unreachable_warned:
                detail = ""
                body = getattr(exc, "read", None)
                if body is not None:
                    try:
                        detail = " :: " + body().decode("utf-8", "replace")[:500]
                    except Exception:
                        pass
                print(
                    f"[docling-engineering] OpenAI-compatible backend ({self.base_url}) "
                    f"unreachable or call failed: {exc}{detail}; "
                    "falling back to deterministic OCR.",
                    file=sys.stderr,
                )
                self._unreachable_warned = True
            _dbg(f"{type(self).__name__}.complete_text failed: {exc!r}")
            return ""


class GraniteDoclingBackend:
    """Docling native Granite document model backend (granite-docling-258M).

    Granite-Docling produces DocTags representing document layout and text.
    It is suited for Mode A (code transcription and indentation preservation).
    For Mode B (Mermaid) and Mode C (SLD topology JSON), it returns "" to fall
    back to the deterministic renderer, per Section 11.2.
    """

    _cached_converter = None

    def __init__(self):
        pass

    @classmethod
    def _is_docling_vlm_available(cls):
        try:
            from docling.datamodel.base_models import InputFormat  # noqa: F401
            from docling.pipeline.vlm_pipeline import VlmPipeline  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def get_converter(cls):
        if cls._cached_converter is not None:
            return cls._cached_converter
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import VlmPipelineOptions
        from docling.datamodel.vlm_model_specs import GRANITEDOCLING_TRANSFORMERS
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.pipeline.vlm_pipeline import VlmPipeline

        opts = VlmPipelineOptions(vlm_options=GRANITEDOCLING_TRANSFORMERS)
        cls._cached_converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: PdfFormatOption(
                    pipeline_cls=VlmPipeline, pipeline_options=opts
                )
            }
        )
        return cls._cached_converter

    def complete(self, image_path, prompt):
        # Mode B and C return "" per Section 11.2
        if "Mermaid" in prompt or "single-line diagram" in prompt or "SLD" in prompt:
            return ""
        if not self._is_docling_vlm_available():
            print(
                "[docling-engineering] Granite backend requested but docling[vlm] is not installed. "
                "Run `pip install 'docling[vlm]'` or use a local LLM via --vlm openai:<model>.",
                file=sys.stderr,
            )
            return ""
        try:
            converter = self.get_converter()
            doc = converter.convert(image_path).document
            code_parts = []
            for item, _ in doc.body.iterate_items():
                if hasattr(item, "text") and item.text:
                    code_parts.append(item.text)
            if not code_parts:
                return doc.export_to_markdown() or ""
            return "\n".join(code_parts)
        except Exception:
            return ""


class GeminiBackend:
    """google-genai multimodal backend. Requires `pip install google-genai`
    and GEMINI_API_KEY (or ADC) in the environment. API errors, timeouts,
    and missing credentials never abort the conversion: complete()
    returns "" so the enricher falls back to the deterministic rendering."""

    TIMEOUT_SECONDS = 60

    def __init__(self, model="gemini-2.5-flash"):
        self._granite = None
        self._fallback_warned = False
        try:
            from google import genai  # deferred import: optional dependency
            self.client = genai.Client()
        except Exception:
            self.client = None
        self.model = model

    def _fallback_to_granite(self, image_path, prompt):
        if not self._fallback_warned:
            print(
                "[docling-engineering] Gemini unavailable or call failed; "
                "falling back to the Granite backend.",
                file=sys.stderr,
            )
            self._fallback_warned = True
        if self._granite is None:
            self._granite = GraniteDoclingBackend()
        return self._granite.complete(image_path, prompt)

    def complete(self, image_path, prompt):
        if self.client is None:
            return self._fallback_to_granite(image_path, prompt)
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
            text = response.text or ""
            if text.strip():
                return text
            return self._fallback_to_granite(image_path, prompt)
        except Exception:
            return self._fallback_to_granite(image_path, prompt)

    def complete_text(self, prompt):
        """Text-only generation without synthetic image bytes (Section 14.2).
        Fails closed: returns "" on any error."""
        if self.client is None:
            return ""
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt],
                config={"http_options": {"timeout": self.TIMEOUT_SECONDS * 1000}},
            )
            return response.text or ""
        except Exception as exc:
            _dbg(f"GeminiBackend.complete_text failed: {exc!r}")
            return ""


class DryRunBackend:
    """No-op backend for pipeline testing: always falls back to the
    deterministic rendering, exercising routing without API calls."""

    def complete(self, image_path, prompt):
        return ""


def build_backend(name):
    """Instantiate a VLM backend adapter from a backend specifier string.

    Supported formats:
    - None / "none": No VLM
    - "dryrun": Exercises classification and routing, falls back to deterministic OCR
    - "granite": Docling Granite-Docling local model (Mode A code transcription only)
    - "gemini-<model>": Google Gemini via google-genai
    - "openai:<model>@<url>" or "local:<model>@<url>": OpenAI-compatible vision endpoint
    - "openai:<model>" or "local:<model>": Uses OPENAI_BASE_URL (default http://localhost:8000/v1)
    - "auto" / "": Auto-resolves in order: OPENAI_BASE_URL -> GEMINI_API_KEY -> Granite -> dryrun
    """
    if not name or name == "none":
        return None
    if name == "dryrun":
        return DryRunBackend()
    if name == "granite":
        return GraniteDoclingBackend()
    if name.startswith("gemini"):
        return GeminiBackend(model=name)
    if name.startswith("openai:") or name.startswith("local:"):
        spec = name.split(":", 1)[1].strip()
        if "@" in spec:
            model, base_url = spec.split("@", 1)
        else:
            model = spec
            base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
        if not model:
            model = os.environ.get("OPENAI_MODEL", "Qwen3-VL-8B-Instruct")
        return OpenAICompatBackend(model=model, base_url=base_url)
    if name in ("auto", ""):
        # Check environment in priority order:
        # 1. Explicit local OpenAI-compatible server configured
        if os.environ.get("OPENAI_BASE_URL"):
            model = os.environ.get("OPENAI_MODEL", "Qwen3-VL-8B-Instruct")
            return OpenAICompatBackend(model=model, base_url=os.environ["OPENAI_BASE_URL"])
        # 2. Cloud Gemini API key configured
        if os.environ.get("GEMINI_API_KEY"):
            return GeminiBackend(model="gemini-2.5-flash")
        # 3. Docling VLM installed
        if GraniteDoclingBackend._is_docling_vlm_available():
            return GraniteDoclingBackend()
        # 4. Fallback to dryrun with note
        return DryRunBackend()
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
    # An empty topology means the crop was not actually an SLD: reject it
    # so the caller falls back to the deterministic rendering instead of
    # replacing real content with an empty JSON shell.
    if not topology["components"] and not topology["connections"]:
        return None
    return "```json\n" + code + "\n```"


def postprocess_tree_view(response):
    """Validate and fence a tree-view response (Mode B, tree_to_treeview).
    Accepts plain text or a pre-fenced treeView-beta block; requires at
    least one branch marker line. Returns None when not usable."""
    code = response.strip()
    if code.startswith("```") and code.endswith("```"):
        first_nl = code.find("\n")
        last_fence = code.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            code = code[first_nl + 1:last_fence].strip("\n")
    branch_lines = [ln for ln in code.splitlines() if ln.lstrip().startswith(("\u251c\u2500\u2500", "\u2514\u2500\u2500"))]
    if len(branch_lines) < 1 or not code.strip():
        return None
    return "```treeView-beta\n" + code + "\n```"


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
        if image_path and os.path.isfile(image_path) and self.backend is not None:
            response = self.backend.complete(image_path, prompt) or ""
        if not response.strip():
            return None
        if mode == "A":
            return postprocess_mode_a(response)
        if mode == "B":
            if prompt_key == "tree_to_treeview":
                return postprocess_tree_view(response)
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
