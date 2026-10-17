#!/usr/bin/env python3
"""Benchmark a pdf2md output file against the user-curated reference.

Compares the pipeline output (e.g. output.md) against ref_output.md and
reports per-section similarity plus exact-line match rates, so the effect
of the VLM enrichment pass (handoff roadmap item 3) can be measured.

Usage:
    python scripts/benchmark_ref.py [output.md] [ref_output.md] [--min-ratio R]

Defaults to ./output.md vs ./ref_output.md from the repository root.
Exits non-zero when the similarity ratio is below --min-ratio
(default 0.590, the QAQC acceptance requirement; the gate must not be
lowered to match a result).
"""

import argparse
import difflib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MIN_RATIO = 0.590


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?", default=os.path.join(REPO, "output.md"))
    ap.add_argument("reference", nargs="?", default=os.path.join(REPO, "ref_output.md"))
    ap.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO,
                    help="Exit non-zero when the similarity ratio is below this "
                         "(default: %(default)s)")
    args = ap.parse_args()

    with open(args.output, encoding="utf-8") as f:
        out_lines = [l.rstrip() for l in f]
    with open(args.reference, encoding="utf-8") as f:
        ref_lines = [l.rstrip() for l in f]

    out_nonblank = [l for l in out_lines if l.strip()]
    ref_nonblank = [l for l in ref_lines if l.strip()]

    matcher = difflib.SequenceMatcher(None, out_nonblank, ref_nonblank)
    ratio = matcher.ratio()

    matched = sum(1 for tag, *_ in matcher.get_opcodes() if tag == "equal")
    total = max(1, len(ref_nonblank))

    print(f"Output: {args.output} ({len(out_nonblank)} non-blank lines)")
    print(f"Reference: {args.reference} ({len(ref_nonblank)} non-blank lines)")
    print(f"Line similarity (SequenceMatcher): {ratio:.3f}")
    print(f"Exact matched lines: {matched}/{len(ref_nonblank)} "
          f"({matched / total:.1%} of reference)")
    if ratio < args.min_ratio:
        print(f"FAIL: similarity {ratio:.3f} is below the required "
              f"minimum {args.min_ratio:.3f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
