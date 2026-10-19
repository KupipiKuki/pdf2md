"""Tech-writer LLM post-processing pass (handoff Section 13.1).

A text-only repair stage that runs after deterministic rendering and VLM
enrichment. It sends *prose segments* of the document to an LLM with a
strict repair prompt (fix OCR character misreads such as `| think` ->
`I think`, drop garbage tokens) while treating fenced code blocks as
immutable: fenced blocks are never sent to the model and the final
document is reassembled from the original fences plus the polished
prose, so structural content cannot drift.

Backend reuse: the Section 11 `build_backend` table. `granite` and
`dryrun` are no-ops here (Granite is a document-conversion model, not
an instruction-following LLM; dryrun has no model at all).
"""

import re

POLISH_PROMPT = """You are a technical editor repairing OCR output.
Fix ONLY character-level OCR misreads that create grammatical errors, e.g.:
- "| think" -> "I think"  (vertical bar misread for capital I)
- "| Transformer T1" -> "I Transformer T1"
- "| were extending" -> "I were extending"
Also remove obvious OCR garbage tokens that are not valid words or syntax.
Rules:
1. Do NOT rewrite sentences, change wording, or reformat anything.
2. Do NOT add or remove Markdown structure (headings, lists, fences).
3. Keep all original line breaks.
4. Output ONLY the repaired text, nothing else.
"""

def split_prose_and_fences(markdown):
    """Split document text into an ordered list of segments:
    ("fence", text) blocks are verbatim; ("prose", text) may be polished.
    Fences are matched from column 0 to a closing column-0 fence line."""
    segments = []
    pos = 0
    for m in re.finditer(r"(?m)^```", markdown):
        if m.start() < pos:
            continue
        close = markdown.find("\n```", m.end())
        if close == -1:
            continue
        close_end = markdown.find("\n", close + 1)
        if close_end == -1:
            close_end = len(markdown)
        if m.start() > pos:
            segments.append(("prose", markdown[pos:m.start()]))
        segments.append(("fence", markdown[m.start():close_end]))
        pos = close_end
    if pos < len(markdown):
        segments.append(("prose", markdown[pos:]))
    return segments


def polish_markdown(markdown, backend, min_chars=20):
    """Polish the prose segments of a Markdown document with the given
    LLM backend. Fenced blocks are preserved byte-for-byte. When the
    backend is None or has no `complete_text` capability, the input is
    returned unchanged. Prose segments that come back empty or that
    changed structurally (line-count drift) are kept as originals
    (fail-closed)."""
    if not backend or not markdown.strip():
        return markdown
    segments = split_prose_and_fences(markdown)
    out = []
    for kind, text in segments:
        if kind == "fence" or not text.strip():
            out.append(text)
            continue
        if len(text.strip()) < min_chars:
            out.append(text)
            continue
        try:
            polished = backend.complete_text(text) or ""
        except Exception:
            polished = ""
        polished = polished.strip()
        original_lines = [ln for ln in text.splitlines() if ln.strip()]
        new_lines = [ln for ln in polished.splitlines() if ln.strip()]
        # Fail-closed: reject empty or structurally drifted responses
        if not polished or len(new_lines) != len(original_lines):
            out.append(text)
            continue
        out.append(polished + ("\n" if text.endswith("\n") else ""))
    return "".join(out)


def build_polish_backend(backend_spec):
    """Resolve a polish-capable backend from a --polish value.
    Returns an object exposing complete_text(prompt) -> str, or None for
    no-op backends (granite, dryrun) and unresolvable specs."""
    from .vlm import build_backend, GraniteDoclingBackend, DryRunBackend
    if not backend_spec:
        return None
    backend = build_backend(backend_spec)
    if isinstance(backend, (GraniteDoclingBackend, DryRunBackend)) or backend is None:
        return None

    class _TextAdapter:
        def complete_text(self, text):
            # Reuse the vision contract with a 1x1 transparent PNG:
            # backends send the prompt text; the image is ignored by
            # text-capable chat models through the OpenAI-compat path.
            import base64
            import os
            import tempfile
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
                "nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg==")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(png)
                tmp = f.name
            try:
                return backend.complete(tmp, POLISH_PROMPT + "\n\nTEXT:\n" + text)
            finally:
                os.unlink(tmp)

    return _TextAdapter()
