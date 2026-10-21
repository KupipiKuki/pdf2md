"""Domain-specific VLM prompts (handoff Section 6.4, Stage 3)."""

PROMPT_CODE_TRANSCRIPTION = """You are an expert OCR transcription engine specializing in source code.
Transcribe the text in this image crop verbatim.
Rules:
1. Detect programming language and enclose in triple backticks with language tag.
2. Preserve exact 4-space Python indentation or 2-space JSON indentation.
3. Strip margin line/gutter numbers (e.g. '1', '2', '3') from the left margin.
4. Fix obvious OCR character noise while preserving variable names and syntax."""

PROMPT_DIAGRAM_TO_MERMAID = """You are an expert technical diagram converter.
Convert the flow chart or hierarchical diagram in this image into valid Mermaid syntax.
Rules:
1. Emit either 'flowchart TD' or 'flowchart LR' based on the primary layout orientation.
2. Faithfully capture multi-node fan-in (multiple arrows converging on one node) and fan-out.
3. Clean OCR noise in node labels.
4. Output ONLY the ```mermaid code block."""

PROMPT_SLD_TOPOLOGY = """Analyze this electrical power-system single-line diagram (SLD).
Extract all electrical entities and connectivity into strict JSON matching this schema:
{
  "components": [
    {
      "id": string,
      "type": "generator" | "transformer" | "transmission_line" | "motor" | "bus" | "breaker",
      "rating_mva": number | null,
      "kv": number | null,
      "connection": string | null,
      "vector_group": string | null,
      "z_pu": number | null
    }
  ],
  "connections": [
    [string, string]
  ]
}"""

PROMPT_TREE_TO_TREEVIEW = """You are an expert technical diagram converter.
Convert the hierarchical tree diagram in this image into a plain-text tree
view using these rules:
1. Root node on its own line at column 0.
2. Children prefixed with box-drawing characters: "├── " for non-final
   children, "└── " for the final child of a parent, and "│   " for
   continuation of ancestor levels.
3. Clean OCR noise in node labels.
4. Output ONLY the tree-view text, with no code fence.
"""

MODE_PROMPTS = {
    "code_transcription": PROMPT_CODE_TRANSCRIPTION,
    "diagram_to_mermaid": PROMPT_DIAGRAM_TO_MERMAID,
    "tree_to_treeview": PROMPT_TREE_TO_TREEVIEW,
    "sld_topology": PROMPT_SLD_TOPOLOGY,
}
