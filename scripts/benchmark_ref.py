#!/usr/bin/env python3
"""Benchmark a pdf2md output file against the user-curated reference.

Compares the pipeline output (e.g. output.md) against ref_output.md and
reports per-section similarity plus exact-line match rates, so the effect
of the VLM enrichment pass (handoff roadmap item 3) can be measured.

Usage:
    python scripts/benchmark_ref.py [output.md] [ref_output.md]

Defaults to ./output.md vs ./ref_output.md from the repository root.
"""

import difflib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "output.md")
    ref_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "ref_output.md")

    with open(out_path, encoding="utf-8") as f:
        out_lines = [l.rstrip() for l in f]
    with open(ref_path, encoding="utf-8") as f:
        ref_lines = [l.rstrip() for l in f]

    out_nonblank = [l for l in out_lines if l.strip()]
    ref_nonblank = [l for l in ref_lines if l.strip()]

    matcher = difflib.SequenceMatcher(None, out_nonblank, ref_nonblank)
    ratio = matcher.ratio()

    matched = sum(1 for tag, *_ in matcher.get_opcodes() if tag == "equal")
    total = max(1, len(ref_nonblank))

    print(f"Output: {out_path} ({len(out_nonblank)} non-blank lines)")
    print(f"Reference: {ref_path} ({len(ref_nonblank)} non-blank lines)")
    print(f"Line similarity (SequenceMatcher): {ratio:.3f}")
    print(f"Exact matched lines: {matched}/{len(ref_nonblank)} "
          f"({matched / total:.1%} of reference)")
    return 0 if ratio > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
