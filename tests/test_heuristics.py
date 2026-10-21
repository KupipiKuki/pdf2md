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
    box_gutter_range,
    boxes_continuing,
    recover_numeric_misread,
    detect_gutters,
    looks_like_list,
    looks_like_code,
    clean_block_lines,
    repair_prose_pipe,
    repair_code_line_punct,
    strip_title_noise,
    HEADING_CUE_RE,
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

    def test_math_chain_becomes_network_graph(self):
        # Sequence-network math chains ('+' as series connector) are
        # network graphs and should render as an undirected Mermaid chain.
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
        ok, code = parse_ascii_diagram_to_mermaid(math)
        self.assertTrue(ok)
        self.assertIn("flowchart TD", code)
        self.assertIn("---", code)
        self.assertIn("XT1", code)
        self.assertIn("X2(M)", code)
        # Balanced equation-like content must NOT be treated as a chain.
        not_chain = ["a = b", "+ c", "= d"]
        ok2, _ = parse_ascii_diagram_to_mermaid(not_chain)
        self.assertFalse(ok2)

    def test_tree_marker_depth_not_keywords(self):
        # Branch parents come from marker depth (L_ vs |-), not from
        # keyword matching on labels.
        tree = [
            "Docling",
            "L_ EngineeringFigureExtension",
            "|- Electrical SLD",
            "|- Protection Scheme",
            "Je Civil Drawing",
            "r- P&ID",
            "|- Mechanical Assembly",
            "|- Control Narrative",
        ]
        ok, code = parse_ascii_diagram_to_mermaid(tree)
        self.assertTrue(ok)
        edges = [l.strip() for l in code.splitlines() if "-->" in l or "---" in l]
        self.assertIn("EngineeringF_2 --> ElectricalSL_3", edges)
        self.assertIn("EngineeringF_2 --> CivilDrawing_5", edges)
        self.assertIn("EngineeringF_2 --> PID_6", edges)
        self.assertIn("EngineeringF_2 --> MechanicalAs_7", edges)
        self.assertIn("EngineeringF_2 --> ControlNarra_8", edges)
        self.assertNotIn("ProtectionSc_4 --> PID_6", edges)
        self.assertNotIn("Docling_1 --> CivilDrawing_5", edges)

    def test_sibling_fan_in_converges_to_next_trunk(self):
        # >=2 sibling branches followed by a trunk node: all siblings
        # converge into that node (fan-in), and the chain skips the
        # redundant parent->trunk edge.
        diag = ["Start", "L_ Group", "|- A", "|- B", "|- C", "Results"]
        ok, code = parse_ascii_diagram_to_mermaid(diag)
        self.assertTrue(ok)
        edges = [l.strip() for l in code.splitlines() if "-->" in l or "---" in l]
        self.assertIn("A_3 --> Results_6", edges)
        self.assertIn("B_4 --> Results_6", edges)
        self.assertIn("C_5 --> Results_6", edges)
        self.assertNotIn("Start_1 --> Results_6", edges)

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

    def test_clean_box_text_bare_gutter_lines(self):
        box_ocr = "13 class Breaker:\n\n14 pass\n\n3\n"
        cleaned = clean_box_text(box_ocr)
        self.assertEqual(cleaned, "class Breaker:\npass")

    def test_clean_box_text_keeps_numeric_content(self):
        # '3 71' is a gutter line whose content '71' is an OCR misread of
        # 'T1' (recovered later by the diagram parser); it must be kept.
        box_ocr = "1 G\n\n2. |\n\n3 71\n\n4 |\n\n5 LINE\n"
        cleaned = clean_box_text(box_ocr)
        self.assertIn("71", cleaned)

    def test_numeric_misread_recovery(self):
        self.assertEqual(recover_numeric_misread("71"), "T1")
        self.assertEqual(recover_numeric_misread("72"), "T2")
        self.assertEqual(recover_numeric_misread("LINE"), "LINE")
        self.assertEqual(recover_numeric_misread("13"), "T3")

    def test_chain_diagram_recovers_transformers(self):
        chain = ["G", "|", "71", "|", "LINE", "|", "72", "|", "M"]
        ok, code = parse_ascii_diagram_to_mermaid(chain)
        self.assertTrue(ok)
        self.assertIn("T1", code)
        self.assertIn("T2", code)
        self.assertIn("---", code)

    def test_clean_box_text_brace_misreads(self):
        # OCR frequently reads a lone closing brace (with its gutter
        # number) as two-letter noise.
        self.assertEqual(clean_box_text("1 Transformer (\n7 Z_pu=@.10\n\nae"),
                         "Transformer (\nZ_pu=@.10\n}")
        self.assertEqual(clean_box_text("fe }"), "}")
        self.assertEqual(clean_box_text("ae }"), "}")
        self.assertEqual(clean_box_text("ae"), "}")
        self.assertEqual(clean_box_text('2 name="T1",'), 'name="T1",')

    def test_clean_box_text_glued_closing_brace(self):
        box_ocr = "7 \"kv\": 13.8\n\n8 }5\n"
        cleaned = clean_box_text(box_ocr)
        self.assertIn("}", cleaned)
        self.assertNotIn("}5", cleaned)

    def test_clean_box_text_preserves_math_tokens(self):
        box_ocr = "3 XT2\n4 +\n5 X2(M)\n"
        cleaned = clean_box_text(box_ocr)
        self.assertEqual(cleaned, "XT2\n+\nX2(M)")

    def test_clean_box_text_preserves_real_digits(self):
        box_ocr = "4     x = 123\n5     y = 13.8\n"
        cleaned = clean_box_text(box_ocr)
        self.assertEqual(cleaned, "    x = 123\n    y = 13.8")

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


class TestCrossPageContinuation(unittest.TestCase):
    def test_box_gutter_range(self):
        text = "9 {\n\n10 \"id\": \"T1\",\n\n22 }\n"
        self.assertEqual(box_gutter_range(text), (9, 22))

    def test_box_gutter_range_none(self):
        self.assertIsNone(box_gutter_range("no numbers here\n"))

    def test_continuation_by_gutter_sequence(self):
        prev_state = ((2403, 2922, 413, 2131), "7 \"kv\": 13.8\n8 }", True, 3300)
        boxes = [((317, 1226, 420, 2150), "9 {\n10 \"id\": \"T1\","),
                 ((1619, 1685, 422, 2140), "1 import networkx as nx")]
        cont, rest = boxes_continuing(prev_state, boxes)
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0][0], (317, 1226, 420, 2150))
        self.assertEqual(len(rest), 1)

    def test_no_continuation_when_gutter_restarts(self):
        prev_state = ((2403, 2922, 413, 2131), "7 \"kv\": 13.8\n8 }", False, 3300)
        boxes = [((317, 1226, 420, 2150), "1 {\n2 \"id\": \"G\",")]
        cont, rest = boxes_continuing(prev_state, boxes)
        self.assertEqual(cont, [])
        self.assertEqual(len(rest), 1)

    def test_no_continuation_x_mismatch(self):
        prev_state = ((100, 200, 413, 700), "7 x\n8 y", True, 3300)
        boxes = [((317, 1226, 1500, 2150), "9 {\n10 \"id\": \"T1\",")]
        cont, rest = boxes_continuing(prev_state, boxes)
        self.assertEqual(cont, [])

    def test_no_state_no_continuation(self):
        boxes = [((317, 1226, 420, 2150), "9 {\n10 \"id\": \"T1\",")]
        cont, rest = boxes_continuing(None, boxes)
        self.assertEqual(cont, [])
        self.assertEqual(rest, boxes)


class TestHeadingCues(unittest.TestCase):
    def test_cue_matches_headings(self):
        for t in ["Proposed Pipeline",
                  "Stage 1: Detect \u201cengineering drawings\u201d",
                  "Stage 2: Vision Model Extraction Once identified, send only the cropped diagram to a multimodal model.",
                  "Positive Sequence Network",
                  "Negative Sequence Network",
                  "Zero Sequence Network",
                  "What I'd Actually Build"]:
            self.assertTrue(HEADING_CUE_RE.match(t), t)

    def test_cue_rejects_prose(self):
        for t in ["The agent can then generate explanations like:",
                  "I think that's actually the architecture I'd recommend.",
                  "Stage is a word in a sentence."]:
            self.assertFalse(HEADING_CUE_RE.match(t), t)


class TestCharacterRepair(unittest.TestCase):
    def test_prose_pipe_to_letter(self):
        self.assertEqual(repair_prose_pipe("| think that's actually"), "I think that's actually")
        self.assertEqual(repair_prose_pipe("currents | from propagating"), "currents I from propagating")

    def test_ascii_chain_pipe_preserved(self):
        self.assertEqual(repair_prose_pipe("G | T1 | LINE | T2 | M"), "G | T1 | LINE | T2 | M")
        self.assertEqual(repair_prose_pipe("|- delta"), "|- delta")
        self.assertEqual(repair_prose_pipe("a |-> b"), "a |-> b")

    def test_lone_punct_line_dropped(self):
        self.assertEqual(repair_code_line_punct(":"), "")
        self.assertEqual(repair_code_line_punct(";"), "")
        self.assertEqual(repair_code_line_punct("pass"), "pass")
        self.assertEqual(repair_code_line_punct("if x:"), "if x:")

    def test_title_prefix_strip(self):
        import pdf2md as p
        self.assertEqual(p.strip_title_noise("Cz5IuxVR-electricalDiagramOCR"), "electricalDiagramOCR")
        self.assertEqual(p.strip_title_noise("electricalDiagramOCR"), "electricalDiagramOCR")


if __name__ == "__main__":
    unittest.main()
