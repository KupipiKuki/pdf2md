#!/usr/bin/env python3
"""Model sweep for pdf2md VLM backends (handoff Section 11.5).

Runs the full pipeline once per candidate local model, benchmarks each
output against ref_output.md, and reports a ranking. Because only one
local LLM can be loaded at a time (single GPU), the sweep is
interactive: after each run it pauses and prompts the user to load the
next model before continuing. Safe to re-run: models that already have
a result file are skipped unless --force.

Usage:
    python scripts/model_sweep.py electricalDiagramOCR.pdf \\
        --models openai:Qwen3-VL-8B-Instruct@http://localhost:8000/v1 \\
                 openai:Llama-3.2-11B-Vision@http://127.0.0.1:8080/v1 \\
        [--ref ref_output.md] [--out-dir scratch/sweep]

Each run executes:
    python scripts/pdf2md.py <pdf> -o <out-dir>/<slug>.md --vlm <model>
    python scripts/benchmark_ref.py <out-dir>/<slug>.md <ref>
"""

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF2MD = os.path.join(REPO, "scripts", "pdf2md.py")
BENCHMARK = os.path.join(REPO, "scripts", "benchmark_ref.py")


def model_slug(spec):
    """Filesystem-safe slug for a backend specifier."""
    slug = spec
    slug = re.sub(r"^[a-z]+:", "", slug, count=1)
    slug = slug.split("@", 1)[0]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug).strip("_")
    return slug or "model"


def run_pipeline(pdf, out_path, spec):
    cmd = [sys.executable, PDF2MD, pdf, "-o", out_path, "--vlm", spec]
    print(f"\n[sweep] running: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd).returncode


def run_benchmark(out_path, ref_path):
    cmd = [sys.executable, BENCHMARK, out_path, ref_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    ratio = None
    m = re.search(r"Line similarity \(SequenceMatcher\): ([0-9.]+)", res.stdout)
    if m:
        ratio = float(m.group(1))
    return ratio, res.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="Input PDF to convert")
    ap.add_argument("--models", nargs="+", required=True,
                    help="Backend specifiers to sweep, in load order, e.g. "
                         "openai:Qwen3-VL-8B-Instruct@http://localhost:8000/v1")
    ap.add_argument("--ref", default=os.path.join(REPO, "ref_output.md"),
                    help="Reference markdown (default: ref_output.md)")
    ap.add_argument("--out-dir", default=os.path.join(REPO, "scratch", "sweep"),
                    help="Directory for per-model outputs and results")
    ap.add_argument("--force", action="store_true",
                    help="Re-run models that already have a result")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    results = []
    total = len(args.models)

    for idx, spec in enumerate(args.models, start=1):
        slug = model_slug(spec)
        out_path = os.path.join(args.out_dir, f"{slug}.md")
        ratio_path = os.path.join(args.out_dir, f"{slug}.ratio.txt")

        if not args.force and os.path.isfile(ratio_path):
            with open(ratio_path, encoding="utf-8") as f:
                ratio = float(f.read().strip())
            print(f"[sweep] {idx}/{total} {spec}: cached result {ratio:.3f}",
                  file=sys.stderr)
            results.append((spec, ratio, out_path))
            continue

        print(f"\n[sweep] {idx}/{total} model: {spec}", file=sys.stderr)
        print("[sweep] make sure this model is loaded and serving, "
              "then press Enter to run (Ctrl+C to abort)...",
              file=sys.stderr)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("[sweep] aborted by user", file=sys.stderr)
            break

        rc = run_pipeline(args.pdf, out_path, spec)
        if rc != 0:
            print(f"[sweep] pipeline failed for {spec} (exit {rc}); skipping",
                  file=sys.stderr)
            continue

        ratio, report = run_benchmark(out_path, args.ref)
        if ratio is None:
            print(f"[sweep] benchmark failed for {spec}; skipping", file=sys.stderr)
            continue
        with open(ratio_path, "w", encoding="utf-8") as f:
            f.write(f"{ratio}\n")
        results.append((spec, ratio, out_path))
        print(report)

    print("\n=== Sweep results (best first) ===")
    for spec, ratio, out_path in sorted(results, key=lambda r: -r[1]):
        print(f"{ratio:.3f}  {spec}  -> {out_path}")
    if not results:
        print("No results recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
