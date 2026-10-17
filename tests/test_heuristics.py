#!/usr/bin/env python3
"""Unit tests for pdf2md pure heuristics and structure parsing."""

import json
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
    reconstruct_indented_lines,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from docling_engineering import FigureClassifier, VLMEnricher
from docling_engineering.rules import build_graph, sequence_networks, transformer_zero_sequence_rules
from docling_engineering.vlm import postprocess_mode_a, postprocess_mode_b, postprocess_mode_c


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


class TestIndentationReconstruction(unittest.TestCase):
    @staticmethod
    def w(text, left, width, top=100):
        return {"text": text, "left": left, "top": top, "width": width, "height": 20}

    def test_four_space_python_indent(self):
        # Monospace char width ~12px: 'class' at x=75 is the code base
        # column; 'pass' at x=123 is 48px (4 chars) right of it.
        lines = [
            [self.w("5", 40, 12), self.w("class", 75, 60)],
            [self.w("6", 42, 12), self.w("pass", 123, 48)],
        ]
        out = reconstruct_indented_lines(lines)
        # The gutter token is kept (single-space delimiter) so downstream
        # gutter-aware logic (box_gutter_range, clean_box_text repairs)
        # keeps working.
        self.assertEqual(out[0], "5 class")
        self.assertEqual(out[1], "6     pass")

    def test_two_space_json_indent(self):
        lines = [
            [self.w("1", 40, 12), self.w("{", 64, 12)],
            [self.w("2", 41, 12), self.w('"figure_type":', 88, 156)],
            [self.w("3", 39, 12), self.w('"confidence":', 160, 144)],
        ]
        out = reconstruct_indented_lines(lines)
        self.assertEqual(out[0], "1 {")
        # code indentation = spaces after the "<gutter> " delimiter
        code_part = out[1].split(" ", 1)[1]
        self.assertEqual(len(code_part) - len(code_part.lstrip()), 2)
        # clean_box_text consumes the delimiter and keeps the indent
        cleaned = clean_box_text("\n".join(out))
        self.assertEqual(cleaned.splitlines()[1], '  "figure_type":')

    def test_inter_word_gap_single_space(self):
        # Inter-word gaps are NOT expanded (char_w is a median over
        # proportional-font text); words are joined with one space.
        lines = [
            [self.w('name="T1",', 64, 84), self.w("rating_mva=100,", 184, 168)],
        ]
        out = reconstruct_indented_lines(lines)
        self.assertIn('name="T1", rating_mva=100,', out[0])
        self.assertNotIn('  ', out[0])

    def test_gutter_excluded_from_base_column(self):
        # The code base column is the first NON-gutter token, so a
        # gutter number at x=40 does not zero the indentation of code.
        lines = [
            [self.w("1", 40, 12), self.w("import", 75, 72)],
            [self.w("2", 41, 12), self.w("nx", 123, 24)],
        ]
        out = reconstruct_indented_lines(lines)
        self.assertEqual(out[0], "1 import")
        self.assertEqual(out[1], "2     nx")

    def test_empty_input(self):
        self.assertEqual(reconstruct_indented_lines([]), [])


class TestIndentationIntegration(unittest.TestCase):
    """Full reconstruct -> clean_box_text -> estimate_code_language path:
    the interaction QAQC flagged in handoff 8.2-A/B."""

    @staticmethod
    def w(text, left, width, top=100):
        return {"text": text, "left": left, "top": top, "width": width, "height": 20}

    def test_python_indent_survives_clean_box_text(self):
        lines = [
            [self.w("1", 40, 12), self.w("class", 75, 60)],
            [self.w("2", 42, 12), self.w("Generator:", 75, 120)],
            [self.w("3", 41, 12), self.w("pass", 123, 48)],
        ]
        recon = "\n".join(reconstruct_indented_lines(lines))
        cleaned = clean_box_text(recon)
        self.assertEqual(cleaned, "class\nGenerator:\n    pass")
        self.assertEqual(estimate_code_language(cleaned), "python")

    def test_json_indent_survives_clean_box_text(self):
        lines = [
            [self.w("1", 40, 12), self.w("{", 64, 12)],
            [self.w("2", 41, 12), self.w('"figure_type":', 88, 156), self.w('"electrical",', 260, 156)],
            [self.w("3", 39, 12), self.w("}", 64, 12)],
        ]
        recon = "\n".join(reconstruct_indented_lines(lines))
        cleaned = clean_box_text(recon)
        self.assertEqual(cleaned.splitlines()[1], '  "figure_type": "electrical",')
        self.assertEqual(estimate_code_language(cleaned), "json")

    def test_diagram_misread_token_survives(self):
        # '3 71' (OCR misread of 'T1') must reach the diagram parser:
        # the gutter is present, so the bare-number content is kept.
        lines = [
            [self.w("1", 40, 12), self.w("G", 75, 12)],
            [self.w("2", 41, 12), self.w("|", 75, 12)],
            [self.w("3", 39, 12), self.w("71", 75, 24)],
            [self.w("4", 40, 12), self.w("|", 75, 12)],
            [self.w("5", 42, 12), self.w("LINE", 75, 48)],
        ]
        recon = "\n".join(reconstruct_indented_lines(lines))
        cleaned = clean_box_text(recon)
        self.assertIn("71", cleaned)
        ok, code = parse_ascii_diagram_to_mermaid(cleaned.splitlines())
        self.assertTrue(ok)
        self.assertIn("T1", code)

    def test_brace_misread_repair_still_fires(self):
        # '8 ae' (gutter + misread '}') must still be repaired to '}'.
        lines = [
            [self.w("7", 40, 12), self.w('"kv":', 75, 48), self.w("13.8", 140, 48)],
            [self.w("8", 41, 12), self.w("ae", 75, 24)],
        ]
        recon = "\n".join(reconstruct_indented_lines(lines))
        cleaned = clean_box_text(recon)
        self.assertIn("}", cleaned)
        self.assertNotIn("ae", cleaned)

    def test_gutter_range_survives_reconstruction(self):
        # box_gutter_range relies on gutters being present in box text.
        lines = [
            [self.w("9", 40, 12), self.w("{", 75, 12)],
            [self.w("10", 41, 12), self.w('"id":', 75, 60)],
            [self.w("22", 39, 12), self.w("}", 75, 12)],
        ]
        recon = "\n".join(reconstruct_indented_lines(lines))
        self.assertEqual(box_gutter_range(recon), (9, 22))

    @staticmethod
    def load_words(fixture):
        fixture_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "fixtures", fixture)
        with open(fixture_path) as f:
            data = json.load(f)
        return [[dict(w) for w in line["words"]] for line in data["lines"]]

    def test_domain_model_fixture_indentation(self):
        # Real-geometry regression (QAQC 9.4-1): word boxes from a
        # skewed scan of the page-4 domain model. All class lines at 0,
        # every pass at exactly 4, regardless of per-line skew.
        lines = self.load_words("domain_model_words.json")
        out = reconstruct_indented_lines(lines)
        self.assertEqual(out[0], "1 class Generator:")
        self.assertEqual(out[1], "2     pass")
        self.assertEqual(out[2], "3 class Transformer:")
        self.assertEqual(out[3], "4     pass")
        self.assertEqual(out[4], "5 class Bus:")
        self.assertEqual(out[5], "6     pass")
        cleaned = clean_box_text("\n".join(out))
        expected = (
            "class Generator:\n"
            "    pass\n"
            "class Transformer:\n"
            "    pass\n"
            "class Bus:\n"
            "    pass"
        )
        self.assertEqual(cleaned, expected)

    def test_json_box_fixture_indentation(self):
        # Real-geometry regression (QAQC 9.4-1): word boxes from a
        # skewed scan of the page-1 JSON box. Keys at exactly 2 spaces,
        # braces flush left.
        lines = self.load_words("json_box_words.json")
        out = reconstruct_indented_lines(lines)
        self.assertEqual(out[0], "1 {")
        self.assertEqual(out[1], '2   "figure_type": "electrical_single')
        self.assertEqual(out[2], '3   "confidence": 0.94')
        self.assertEqual(out[3], "4 }")
        cleaned = clean_box_text("\n".join(out))
        self.assertEqual(cleaned.splitlines()[1], '  "figure_type": "electrical_single')
        self.assertEqual(cleaned.splitlines()[2], '  "confidence": 0.94')

    def test_snap_indents_quantizes_jitter(self):
        # OCR jitter of a column or two must not produce odd indents
        # (the 6/3/9-space artifacts QAQC observed).
        from pdf2md import _snap_indents
        self.assertEqual(_snap_indents([0.2, 3.9, 4.2, 8.1]), [0, 4, 4, 8])
        self.assertEqual(_snap_indents([1.9, 4.1, 6.2]), [2, 4, 6])
        # The snap is deliberately generous (+/-1 column): values near
        # a 4-multiple snap to the 4-column grid, values only near a
        # 2-multiple snap to the 2-column grid. The plain-rounding
        # fallback fires only for values off both grids by > 1 column.
        self.assertEqual(_snap_indents([1.0, 5.0]), [0, 4])
        self.assertEqual(_snap_indents([1.4, 5.4]), [2, 6])
        self.assertEqual(_snap_indents([1.6, 5.6]), [2, 6])


class TestFigureClassifier(unittest.TestCase):
    def setUp(self):
        self.clf = FigureClassifier()

    def test_json_box_is_code_listing(self):
        text = '{\n"figure_type": "electrical_single_line",\n"confidence": 0.94\n}'
        self.assertEqual(self.clf.classify_text(text), "code_listing")

    def test_python_box_is_code_listing(self):
        text = "1 class Generator:\n2 pass"
        self.assertEqual(self.clf.classify_text(text), "code_listing")

    def test_tree_diagram_is_flowchart(self):
        tree = ["Docling", "L_ EngineeringFigureExtension", "|- Electrical SLD", "|- P&ID"]
        text = "\n".join(tree)
        self.assertEqual(self.clf.classify_text(text), "flowchart_or_tree")

    def test_empty_text_is_photograph(self):
        self.assertEqual(self.clf.classify_text(""), "photograph")
        self.assertEqual(self.clf.classify_text("   "), "photograph")

    def test_plot_keywords(self):
        self.assertEqual(self.clf.classify_text("axis: time (s)\nxlabel: voltage"),
                         "plot_or_chart")

    def test_mode_routing(self):
        self.assertEqual(self.clf.mode_for("code_listing")[0], "A")
        self.assertEqual(self.clf.mode_for("flowchart_or_tree")[0], "B")
        self.assertEqual(self.clf.mode_for("electrical_sld")[0], "C")
        self.assertEqual(self.clf.mode_for("piping_and_instrumentation")[0], "C")

    def test_passthrough_categories(self):
        # Photographs and plots pass through unchanged: no VLM mode.
        self.assertEqual(self.clf.mode_for("photograph"), (None, "passthrough"))
        self.assertEqual(self.clf.mode_for("plot_or_chart"), (None, "passthrough"))


class TestVLMEnrichment(unittest.TestCase):
    def test_dryrun_falls_back(self):
        enricher = VLMEnricher(backend="dryrun")
        # No image path: VLM produces nothing -> enrich returns None
        self.assertIsNone(enricher.enrich("some code text", None))

    def test_enrich_box_results_fallback_rendering(self):
        enricher = VLMEnricher(backend="dryrun")
        box_results = [((0, 100, 0, 100), '1 {\n2 "a": 1\n3 }')]
        overrides = enricher.enrich_box_results(
            box_results, {},
            fallback=lambda cleaned: "```json\n" + cleaned + "\n```")
        box = (0, 100, 0, 100)
        self.assertIn("```json", overrides[box])
        self.assertIn('"a": 1', overrides[box])

    def test_postprocess_mode_a_keeps_language_tag(self):
        self.assertEqual(postprocess_mode_a("```python\nclass X:\n    pass\n```"),
                         "```python\nclass X:\n    pass\n```")

    def test_postprocess_mode_a_estimates_missing_tag(self):
        # No fence language: fall back to the heuristic estimator.
        out = postprocess_mode_a("class X:\n    pass")
        self.assertTrue(out.startswith("```python"))

    def test_postprocess_mode_a_empty_response(self):
        self.assertIsNone(postprocess_mode_a(""))
        self.assertIsNone(postprocess_mode_a("```\n\n```"))

    def test_postprocess_mode_b(self):
        resp = "```mermaid\nflowchart TD\n    A --> B\n```"
        self.assertEqual(postprocess_mode_b(resp), resp)
        bare = "flowchart TD\n    A --> B"
        self.assertEqual(postprocess_mode_b(bare), resp)

    def test_postprocess_mode_b_rejects_non_mermaid(self):
        self.assertIsNone(postprocess_mode_b("here is some prose, not a diagram"))
        self.assertIsNone(postprocess_mode_b("```json\n{}\n```"))

    def test_postprocess_mode_c(self):
        resp = '{"components": [], "connections": []}'
        self.assertEqual(postprocess_mode_c(resp),
                         "```json\n" + resp + "\n```")

    def test_postprocess_mode_c_rejects_invalid(self):
        self.assertIsNone(postprocess_mode_c("{not json"))
        self.assertIsNone(postprocess_mode_c('{"components": []}'))

    def test_passthrough_enrich_returns_none(self):
        enricher = VLMEnricher(backend="dryrun")
        self.assertIsNone(enricher.enrich("", "/nonexistent.png"))
        self.assertIsNone(enricher.enrich("axis: time", "/nonexistent.png"))

    def test_topology_markdown_from_mode_c(self):
        enricher = VLMEnricher(backend="dryrun")
        fenced = ('```json\n'
                  '{"components": [{"id": "G", "type": "generator"},'
                  '{"id": "M", "type": "motor"}],'
                  '"connections": [["G", "M"]]}\n```')
        md = enricher.topology_markdown(fenced)
        self.assertIn("Positive Sequence Network", md)
        self.assertIn("```mermaid", md)
        self.assertIn("G --- M", md)


class TestRuleEngine(unittest.TestCase):
    TOPOLOGY = {
        "components": [
            {"id": "G", "type": "generator", "z_pu": 0.10},
            {"id": "T1", "type": "transformer", "z_pu": 0.05,
             "connection": "delta-grounded_wye"},
            {"id": "LINE", "type": "transmission_line", "z_pu": 0.20},
            {"id": "M", "type": "motor", "z_pu": 0.15},
        ],
        "connections": [["G", "T1"], ["T1", "LINE"], ["LINE", "M"]],
    }

    def test_build_graph(self):
        g = build_graph(self.TOPOLOGY)
        self.assertEqual(set(g.nodes), {"G", "T1", "LINE", "M"})
        self.assertEqual(g.degree("T1"), 2)

    def test_sequence_network_chain(self):
        g = build_graph(self.TOPOLOGY)
        net = sequence_networks(g, "G", "M")
        self.assertEqual(list(net.nodes), ["G", "T1", "LINE", "M"])
        # A network needs edges, not just nodes.
        self.assertEqual(list(net.edges), [("G", "T1"), ("T1", "LINE"), ("LINE", "M")])

    def test_sequence_mermaid_emitter(self):
        from docling_engineering.rules import sequence_mermaid, sequence_markdown
        g = build_graph(self.TOPOLOGY)
        net = sequence_networks(g, "G", "M")
        mermaid = sequence_mermaid(net, "Positive")
        self.assertIn("flowchart TD", mermaid)
        self.assertIn("G --- T1", mermaid)
        self.assertIn("T1 --- LINE", mermaid)
        md = sequence_markdown(self.TOPOLOGY)
        self.assertIn("### Positive Sequence Network", md)
        self.assertIn("### Negative Sequence Network", md)
        self.assertIn("### Zero Sequence Network", md)
        self.assertIn("zero_sequence_blocked", md)

    def test_no_path_handled(self):
        from docling_engineering.rules import sequence_markdown, chain_reactances
        disconnected = {
            "components": [{"id": "G", "type": "generator"},
                           {"id": "M", "type": "motor"}],
            "connections": [],
        }
        self.assertEqual(sequence_markdown(disconnected), "")
        with self.assertRaises(ValueError):
            chain_reactances(build_graph(disconnected), "G", "M")
        with self.assertRaises(ValueError):
            chain_reactances(build_graph(self.TOPOLOGY), "G", "NOPE")

    def test_zero_sequence_rules(self):
        rules = transformer_zero_sequence_rules(self.TOPOLOGY["components"])
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["component"], "T1")
        self.assertEqual(rules[0]["rule"], "zero_sequence_blocked")


if __name__ == "__main__":
    unittest.main()
