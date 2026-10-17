# electricalDiagramOCR

<!-- page 1 (OCR + box regions) -->

| think that's actually the architecture I'd recommend.

The weakness isn't Docling's OCR or document understanding. The weakness is that an electrical single-line diagram is fundamentally a graph with engineering semantics, not a document.

Proposed Pipeline

```mermaid
flowchart TD
    PDF_1["PDF"]
    Docling_2["Docling"]
    Normaltextta_3["Normal text/tables"]
    StandardDocl_4["Standard Docling extraction"]
    Figuredetect_5["Figure detector"]
    Photograph_6["Photograph"]
    Plot_7["Plot"]
    schematic_8["schematic"]
    PID_9["P&ID"]
    ElectricalSL_10["Electrical SLD &lt;-- route here"]
    VisionModelE_11["Vision Model + Engineering Agent"]
    StructuredGr_12["Structured Graph"]
    EngineeringD_13["Engineering Documentation"]
    PDF_1 --> Docling_2
    Docling_2 --> Normaltextta_3
    Normaltextta_3 --> StandardDocl_4
    Normaltextta_3 --> Figuredetect_5
    Figuredetect_5 --> Photograph_6
    Figuredetect_5 --> Plot_7
    Figuredetect_5 --> schematic_8
    Figuredetect_5 --> PID_9
    Figuredetect_5 --> ElectricalSL_10
    ElectricalSL_10 --> VisionModelE_11
    VisionModelE_11 --> StructuredGr_12
    StructuredGr_12 --> EngineeringD_13
```

Stage 1: Detect “engineering drawings" The extension doesn't need to fully understand the diagram initially.

Just classify:

```json
{
"figure_type": "electrical_single line",
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

Stage 2: Vision Model Extraction Once identified, send only the cropped diagram to a multimodal model.

Prompt example:

```
Analyze this electrical power-system
single-line diagram.
3
Identify:
5
- Components
- Connectivity
- Ratings
- Grounding methods
- Transformer vector groups
- Breakers
- Protective devices
13
Return JSON.
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
}5
```

<!-- page 3 (OCR + box regions) -->

```json
{
"id": "T1",
"type": "transformer", |
"connection": "delta-grounded_wye" |
fe }
],
15
"connections": [
re", "r1"4,
["T1i","LINE"],
["LINE","T2"],
["T2","M"]
]
+}
```

Stage 3: Python Engineering Plugin This is where the real value lives.

Convert extracted JSON into a network graph.

```python
import networkx as nx
```

Build:

```mermaid
flowchart TD
    G_1["G"]
    node_2["71"]
    LINE_3["LINE"]
    node_4["72"]
    M_5["M"]
    G_1 --> node_2
    node_2 --> LINE_3
    LINE_3 --> node_4
    node_4 --> M_5
```

Then automatically derive:

Positive Sequence Network

<!-- page 4 (OCR + box regions) -->

```
XT2
+
Xs(M)
```

Negative Sequence Network

```
X2(G)
+
XT1
+
XLINE
+
XT2
+
X2(M)
```

Zero Sequence Network Apply transformer rules:

```python
if transformer.primary == "delta":
block zero sequence()
```

Now you are producing engineering knowledge instead of OCR.

Stage 4: Specialized Engineering Agent The agent can then generate explanations like: | Transformer T1 is connected A-Yg. The delta winding prevents zero-sequence currents | from propagating between the generator and transmission network. Consequently, the _ generator zero-sequence network is isolated from the transmission line zero-sequence network.

That's exactly the sort of explanation that utilities, consultants, and protection engineers want.

An Even Better Approach i'd represent everything in a domain model rather than plain JSON.

```python
class Generator:
pass
3
```

<!-- page 5 (OCR + box regions) -->

```python
class Transformer:
pass
6
class TransmissionLine:
pass
:
class Bus:
pass
12
class Breaker:
pass
```

Then the vision output simply fills these objects.

Example:

```python
Transformer (
name="T1",
rating mva=100,
primary_kv=13.8,
secondary_kv=138,
vector_group="Dyg",
Z_pu=@.10
ae
```

Now downstream modules can:

- Generate sequence networks
- Produce relay settings documentation
- Create one-line reports
- Build CIM-compatible exports
- Calculate fault current paths
- Generate protection studies

without asking the LLM to reason from raw text every time.

What I'd Actually Build If | were extending Docling today, I'd add:

```mermaid
flowchart TD
    Docling_1["Docling"]
    EngineeringF_2["EngineeringFigureExtension"]
    ElectricalSL_3["Electrical SLD"]
    ProtectionSc_4["Protection Scheme"]
    Docling_1 --> EngineeringF_2
    EngineeringF_2 --> ElectricalSL_3
    EngineeringF_2 --> ProtectionSc_4
```

<!-- page 6 (OCR + box regions) -->

```mermaid
flowchart TD
    PID_1["P&ID"]
    CivilDrawing_2["Civil Drawing"]
    MechanicalAs_3["Mechanical Assembly"]
    ControlNarra_4["Control Narrative"]
    CivilDrawing_2 --> MechanicalAs_3
    CivilDrawing_2 --> ControlNarra_4
```

For electrical SLDs specifically:

- Detect diagram region
- Send crop to VLM
- Extract topology JSON
- Build NetworkX graph
- Run engineering-rule engine
- Generate documentation

The engineering-rule engine will provide more value than the vision model itself. The vision model identifies "A-Yg transformer"; the rule engine knows "zero-sequence current is blocked through the delta side." That's where the domain expertise really comes from.

