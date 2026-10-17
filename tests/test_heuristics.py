#!/usr/bin/env python3
"""Unit tests for pdf2md pure heuristics and structure parsing."""

import unittest
import sys
import os

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from pdf2md import (
    estimate_code_language,
    parse_ascii_diagram_to_mermaid,
    clean_box_text,
    detect_gutters,
    looks_like_list,
    looks_like_code,
    clean_block_lines,
)


class TestLanguageEstimation(unittest.TestCase):
    def test_json_estimation(self):
        self.assertEqual(estimate_code_language('{"key": "value", "count": 42}'), "json")
        snippet = '"figure_type": "electrical_single_line",\n"confidence": 0.94'
        self.assertEqual(estimate_code_language(snippet), "json")
        array_snippet = '[\n  {"id": "G"},\n  {"id": "T1"}\n]'
        self.assertEqual(estimate_code_language(array_snippet), "json")

    def test_python_estimation(self):
        self.assertEqual(estimate_code_language("import networkx as nx\nimport sys"), "python")
        self.assertEqual(estimate_code_language("class Generator:\n    pass"), "python")
        instantiation = 'Transformer(\n    name="T1",\n    rating_mva=100,\n    primary_kv=13.8\n)'
        self.assertEqual(estimate_code_language(instantiation), "python")
        rule = 'if transformer.primary == "delta":\n    block_zero_sequence()'
        self.assertEqual(estimate_code_language(rule), "python")

    def test_mermaid_estimation(self):
        self.assertEqual(estimate_code_language("flowchart TD\n    A --> B"), "mermaid")
        self.assertEqual(estimate_code_language("graph LR\n    A --> B"), "mermaid")

    def test_bash_estimation(self):
        self.assertEqual(estimate_code_language("pip install pymupdf pytesseract numpy"), "bash")
        self.assertEqual(estimate_code_language("git checkout -b new-branch"), "bash")

    def test_prose_not_yaml(self):
        prompt = (
            "Analyze this electrical power-system single-line diagram.\n"
            "Identify:\n"
            "- Components\n"
            "- Connectivity\n"
            "Return JSON."
        )
        self.assertNotEqual(estimate_code_language(prompt), "yaml")


class TestMermaidDiagramConversion(unittest.TestCase):
    def test_linear_flow_chain(self):
        chain = ["G | T1 | LINE | T2 | M"]
        ok, mermaid = parse_ascii_diagram_to_mermaid(chain)
        self.assertTrue(ok)
        self.assertIn("flowchart LR", mermaid)
        self.assertIn('N1["G"] --> N2["T1"] --> N3["LINE"] --> N4["T2"] --> N5["M"]', mermaid)

    def test_vertical_flowchart(self):
        diag = [
            "PDF",
            "|",
            "Vv",
            "Docling",
            "|",
            "Vv",
            "Vision Model",
            "|",
            "Vv",
            "Structured Graph",
        ]
        ok, mermaid = parse_ascii_diagram_to_mermaid(diag)
        self.assertTrue(ok)
        self.assertIn("flowchart TD", mermaid)
        self.assertIn('PDF', mermaid)
        self.assertIn('Docling', mermaid)
        self.assertIn('Vision Model', mermaid)

    def test_tree_diagram(self):
        tree = [
            "Docling",
            "L_ EngineeringFigureExtension",
            "|- Electrical SLD",
            "|- Protection Scheme",
        ]
        ok, mermaid = parse_ascii_diagram_to_mermaid(tree)
        self.assertTrue(ok)
        self.assertIn("flowchart TD", mermaid)
        self.assertIn('EngineeringFigureExtension', mermaid)
        self.assertIn('Electrical SLD', mermaid)

    def test_math_chain_not_diagram(self):
        math = [
            "X2(G)",
            "+",
            "XT1",
            "+",
            "XLINE",
            "+",
            "XT2",
            "+",
            "X2(M)",
        ]
        ok, _ = parse_ascii_diagram_to_mermaid(math)
        self.assertFalse(ok)

    def test_bullet_list_not_diagram(self):
        bullet_text = [
            "Analyze this diagram",
            "Identify:",
            "- Components",
            "- Connectivity",
            "- Ratings",
            "Return JSON",
        ]
        ok, _ = parse_ascii_diagram_to_mermaid(bullet_text)
        self.assertFalse(ok)


class TestLayoutHeuristics(unittest.TestCase):
    def test_clean_box_text(self):
        box_ocr = "1 def run():\n2     x = 42\n3     return x\n"
        cleaned = clean_box_text(box_ocr)
        self.assertEqual(cleaned, "def run():\n    x = 42\n    return x")

    def test_detect_gutters(self):
        lines = [
            {"text": "1", "left": 100},
            {"text": "2", "left": 102},
            {"text": "3", "left": 99},
            {"text": "Some text", "left": 150},
        ]
        gutters = detect_gutters(lines)
        self.assertEqual(gutters, {0, 1, 2})

    def test_clean_block_lines_filters_stray_overlap(self):
        block = [
            {"text": "Detect diagram region", "top": 688.0, "bottom": 727.0, "left": 554.0},
            {"text": "Pp", "top": 689.0, "bottom": 725.0, "left": 479.0},
            {"text": "Send crop to VLM", "top": 750.0, "bottom": 782.0, "left": 552.0},
        ]
        cleaned = clean_block_lines(block)
        texts = [l["text"] for l in cleaned]
        self.assertIn("Detect diagram region", texts)
        self.assertIn("Send crop to VLM", texts)
        self.assertNotIn("Pp", texts)


if __name__ == "__main__":
    unittest.main()
