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

import difflib
import os
import re
import sys

POLISH_DEBUG = bool(os.environ.get("PDF2MD_DEBUG"))


def _dbg(msg):
    if POLISH_DEBUG:
        print(f"[pdf2md:polish] {msg}", file=sys.stderr)

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


def _normalize_line(ln):
    """Collapse all whitespace so reflowed text compares by content."""
    return re.sub(r"\s+", " ", ln.strip()).lower()


def _lines_align(original_lines, new_lines):
    """Fuzzy line alignment for the polish drift guard (handoff 15.5-3).

    A response with a different line count is accepted only when every
    original line has a distinct new line with near-identical normalized
    content (same words after whitespace collapse). This admits repairs
    that reflow whitespace (e.g. a single long line the model split, or
    trailing blank-line noise stripped) while still rejecting invented,
    deleted, or reordered structure.
    """
    if not original_lines or not new_lines:
        return False
    # Pure reflow: same words in the same order, only the line wrapping
    # changed (model split a long line or merged a short one).
    if (_normalize_line(" ".join(original_lines))
            == _normalize_line(" ".join(new_lines))):
        return True
    # Character repairs with reflow: same number of words, joined text
    # near-identical (the only edits are single-character misread fixes
    # such as '|' -> 'I', wrapped differently across lines).
    o_words = _normalize_line(" ".join(original_lines)).split()
    n_words = _normalize_line(" ".join(new_lines)).split()
    if (len(o_words) == len(n_words)
            and difflib.SequenceMatcher(
                None, " ".join(o_words), " ".join(n_words)).ratio() >= 0.9):
        return True
    remaining = list(new_lines)
    for o in original_lines:
        no = _normalize_line(o)
        match = None
        for j, n in enumerate(remaining):
            if _normalize_line(n) == no:
                match = j
                break
        if match is None:
            for j, n in enumerate(remaining):
                if difflib.SequenceMatcher(
                        None, no, _normalize_line(n)).ratio() >= 0.9:
                    match = j
                    break
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


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
    n_prose = sum(1 for k, _ in segments if k == "prose")
    n_fenced = sum(1 for k, _ in segments if k == "fence")
    _dbg(f"segments: {n_prose} prose, {n_fenced} fenced (fences never sent)")
    warned_unreachable = False
    stats = {"polished": 0, "unchanged": 0, "drift": 0, "empty": 0,
             "error": 0, "skipped": 0}
    for i, (kind, text) in enumerate(segments):
        if kind == "fence" or not text.strip():
            out.append(text)
            continue
        if len(text.strip()) < min_chars:
            _dbg(f"segment {i}: skipped ({len(text.strip())} chars < {min_chars})")
            stats["skipped"] += 1
            out.append(text)
            continue
        leading_ws = text[:len(text) - len(text.lstrip())]
        try:
            polished = backend.complete_text(text) or ""
        except Exception as exc:
            if not warned_unreachable:
                print(
                    f"[pdf2md:polish] Polish backend unreachable or failed ({exc}); "
                    "prose kept unpolished.",
                    file=sys.stderr,
                )
                warned_unreachable = True
            _dbg(f"segment {i}: backend error: {exc!r}")
            stats["error"] += 1
            polished = ""
        polished = polished.strip()
        original_lines = [ln for ln in text.splitlines() if ln.strip()]
        new_lines = [ln for ln in polished.splitlines() if ln.strip()]
        # Fail-closed: reject empty or structurally drifted responses
        if not polished:
            _dbg(f"segment {i}: empty response -> kept original")
            stats["empty"] += 1
            out.append(text)
            continue
        if len(new_lines) != len(original_lines) and not _lines_align(
                original_lines, new_lines):
            _dbg(f"segment {i}: line drift "
                 f"({len(original_lines)} -> {len(new_lines)} lines) -> kept original")
            stats["drift"] += 1
            out.append(text)
            continue
        if polished == text.strip():
            _dbg(f"segment {i}: unchanged response")
            stats["unchanged"] += 1
        else:
            _dbg(f"segment {i}: polished OK")
            stats["polished"] += 1
        out.append(leading_ws + polished + ("\n" if text.endswith("\n") else ""))
    print(
        "[pdf2md:polish] prose segments: "
        f"{stats['polished']} polished, {stats['unchanged']} unchanged, "
        f"{stats['drift']} rejected (line drift), {stats['empty']} empty, "
        f"{stats['error']} errors, {stats['skipped']} skipped; "
        f"{n_fenced} fenced blocks untouched.",
        file=sys.stderr,
    )
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

    has_native_text = hasattr(backend, "complete_text")

    class _TextAdapter:
        def complete_text(self, text):
            prompt = POLISH_PROMPT + "\n\nTEXT:\n" + text
            if has_native_text:
                # Native text-only path: no image part, no temp file (Section 14.2)
                return backend.complete_text(prompt)
            # Legacy fallback for backends that only implement the vision
            # contract: reuse it with a 1x1 transparent PNG.
            import base64
            import tempfile
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
                "nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg==")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(png)
                tmp = f.name
            try:
                return backend.complete(tmp, prompt)
            finally:
                os.unlink(tmp)

    return _TextAdapter()
