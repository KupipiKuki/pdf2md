# electricalDiagramOCR

<!-- page 1 (OCR + box regions) -->

I think that's actually the architecture I'd recommend.

The weakness isn't Docling's OCR or document understanding. The weakness is that an electrical single-line diagram is fundamentally a graph with engineering semantics, not a document.

### Proposed Pipeline

```mermaid
flowchart TD
    PDF --> Docling
    Docling --> Normal_text_tables["Normal text/tables"]
    Docling --> Figure_detector["Figure detector"]
    Normal_text_tables --> Standard_Docling_extraction["Standard Docling extraction"]
    Figure_detector --> Photograph["Photograph"]
    Figure_detector --> Plot["Plot"]
    Figure_detector --> Schematic["Schematic"]
    Figure_detector --> P_ID["P&ID"]
    Figure_detector --> Electrical_SLD["Electrical SLD"]
    Electrical_SLD <--- route_here["route here"]
    Figure_detector --> Vision_Model_Engineering_Agent["Vision Model + Engineering Agent"]
    Vision_Model_Engineering_Agent --> Structured_Graph["Structured Graph"]
    Structured_Graph --> Engineering_Documentation["Engineering Documentation"]
```

### Stage 1: Detect “engineering drawings"

The extension doesn't need to fully understand the diagram initially.

Just classify:

```json
{
  "figure_type": "electrical_single_line",
  "confidence": 0.94
}
```

<!-- page 2 (OCR + box regions) -->

Features are surprisingly simple:

- electrical symbols
- transmission lines
- breakers
- transformers
- ANSI device numbers
- bus bars
- relay symbols

A lightweight CV classifier could do this.

### Stage 2: Vision Model Extraction

Once identified, send only the cropped diagram to a multimodal model.

Prompt example:

```json
{
  "components": [],
  "connections": []
}
```

Expected result:

```json
{
  "components": [
    {
      "id": "G",
      "type": "generator",
      "rating mva": 102,
      "kv": 13.8
    }
    {
      "id": "T1",
      "type": "transformer", |
      "connection": "delta-grounded_wye" |
}
  ],
  "connections": [
    re", "r1"4,
    ["T1i","LINE"],
    ["LINE","T2"],
    ["T2","M"]
  ]
}
```

<!-- page 3 (OCR + box regions) -->

### Stage 3: Python Engineering Plugin

This is where the real value lives.

Convert extracted JSON into a network graph.

```python
import networkx as nx
```

Build:

```mermaid
flowchart TD
    G --> T1
    T1 --> LINE
    LINE --> T2
    T2 --> M
```

Then automatically derive:

### Positive Sequence Network

```mermaid
flowchart TD
    N1["Xs(G)"]
    N2["XT1"]
    N3["XLINE"]
    N4["XT2"]
    N5["Xs(M)"]
    N1 --- N2
    N2 --- N3
    N3 --- N4
    N4 --- N5
```

<!-- page 4 (OCR + box regions) -->

### Negative Sequence Network

```mermaid
flowchart TD
    X2(G) --> +
    + --> XT1
    XT1 --> +
    + --> XLINE
    XLINE --> +
    + --> XT2
    XT2 --> +
    + --> X2(M)
```

### Zero Sequence Network

Apply transformer rules:

```python
if transformer.primary == "delta":
    block_zero_sequence()
```

Now you are producing engineering knowledge instead of OCR.

### Stage 4: Specialized Engineering Agent

The agent can then generate explanations like: I Transformer T1 is connected A-Yg. The delta winding prevents zero-sequence currents I from propagating between the generator and transmission network. Consequently, the _ generator zero-sequence network is isolated from the transmission line zero-sequence network.

That's exactly the sort of explanation that utilities, consultants, and protection engineers want.

An Even Better Approach i'd represent everything in a domain model rather than plain JSON.

```python
class Generator:
    pass
class Transformer:
    pass
class TransmissionLine:
    pass
class Bus:
    pass
class Breaker:
    pass
```

<!-- page 5 (OCR + box regions) -->

Then the vision output simply fills these objects.

Example:

```json
{
  "components": [
    {
      "id": "T1",
      "type": "transformer",
      "rating_mva": 100,
      "kv": null,
      "connection": null,
      "vector_group": "Dyg",
      "z_pu": 0.1
    }
  ],
  "connections": []
}
```

Now downstream modules can:

- Generate sequence networks
- Produce relay settings documentation
- Create one-line reports
- Build CIM-compatible exports
- Calculate fault current paths
- Generate protection studies

without asking the LLM to reason from raw text every time.

### What I'd Actually Build

If I were extending Docling today, I'd add:

```treeView-beta
Docling
└── EngineeringFigureExtension
    ├── Electrical SLD
    ├── Protection Scheme
    ├── P&ID
    ├── Civil Drawing
    ├── Mechanical Assembly
    └── Control Narrative
```

<!-- page 6 (OCR + box regions) -->

For electrical SLDs specifically:

- Detect diagram region
- Send crop to VLM
- Extract topology JSON
- Build NetworkX graph
- Run engineering-rule engine
- Generate documentation

The engineering-rule engine will provide more value than the vision model itself. The vision model identifies "A-Yg transformer"; the rule engine knows "zero-sequence current is blocked through the delta side." That's where the domain expertise really comes from.

