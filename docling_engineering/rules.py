"""Python engineering rule engine (handoff Section 6.4, Stage 4).

Converts VLM-extracted SLD topology JSON into a networkx graph and
derives sequence networks plus transformer zero-sequence rules —
"the engineering-rule engine will provide more value than the vision
model itself."
"""

try:
    import networkx as nx
except ImportError:
    nx = None


def _require_networkx():
    if nx is None:
        raise ImportError("run `pip install networkx`")


def build_graph(topology):
    """Build an undirected networkx graph from SLD topology JSON
    ({"components": [...], "connections": [[a, b], ...]})."""
    _require_networkx()
    g = nx.Graph()
    for comp in topology.get("components", []):
        g.add_node(comp["id"], **{k: v for k, v in comp.items() if k != "id"})
    for a, b in topology.get("connections", []):
        g.add_edge(a, b)
    return g


def chain_reactances(graph, source, sink):
    """Return the series reactance chain from source to sink as a list of
    (component_id, z_pu) pairs along the connecting path. Raises
    ValueError (not a raw NetworkXError) when no path or node exists."""
    _require_networkx()
    if source not in graph or sink not in graph:
        raise ValueError(f"unknown component: {source if source not in graph else sink}")
    try:
        path = nx.shortest_path(graph, source, sink)
    except nx.NetworkXNoPath:
        raise ValueError(f"no path between {source} and {sink}")
    return [(nid, graph.nodes[nid].get("z_pu")) for nid in path]


def sequence_networks(graph, source, sink):
    """Derive the positive/negative sequence network between a source
    (generator) and sink (motor): the series reactance chain as a
    connected graph (nodes AND edges). Positive and negative sequence
    networks share the same series reactances for passive components."""
    _require_networkx()
    chain = chain_reactances(graph, source, sink)
    net = nx.Graph()
    for nid, z in chain:
        net.add_node(nid, z_pu=z)
    for a, b in zip(chain[:-1], chain[1:]):
        net.add_edge(a[0], b[0])
    return net


def sequence_mermaid(net, title):
    """Render a sequence network graph as a Mermaid flowchart chain."""
    out = [f"flowchart TD"]
    for nid, data in net.nodes(data=True):
        out.append(f'    {nid}["{nid}"]')
    for a, b in net.edges():
        out.append(f"    {a} --- {b}")
    return "\n".join(out)


def sequence_markdown(topology, source=None, sink=None):
    """Full rule-engine Markdown for a topology JSON: positive and
    negative sequence network Mermaid blocks, plus zero-sequence rules
    derived from transformer winding connections. Returns "" when the
    topology cannot produce a chain."""
    _require_networkx()
    try:
        graph = build_graph(topology)
        components = topology.get("components", [])
        if source is None:
            source = next((c["id"] for c in components
                           if c.get("type") == "generator"), None)
        if sink is None:
            sink = next((c["id"] for c in components
                         if c.get("type") == "motor"), None)
        if not source or not sink or source not in graph or sink not in graph:
            return ""
        pos = sequence_networks(graph, source, sink)
        blocks = []
        blocks.append("### Positive Sequence Network\n\n```mermaid\n"
                      + sequence_mermaid(pos, "Positive") + "\n```")
        blocks.append("### Negative Sequence Network\n\n```mermaid\n"
                      + sequence_mermaid(pos, "Negative") + "\n```")
        rules = transformer_zero_sequence_rules(components)
        if rules:
            lines = ["### Zero Sequence Network", ""]
            for r in rules:
                lines.append(f"- {r['component']}: {r['reason']} "
                             f"({r['rule']})")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)
    except (ValueError, KeyError):
        return ""


def transformer_zero_sequence_rules(components):
    """Apply transformer winding-connection rules: a delta winding blocks
    zero-sequence current propagation on its side of the network."""
    rules = []
    for comp in components:
        conn = (comp.get("connection") or comp.get("vector_group") or "").lower()
        if "delta" in conn or conn.startswith("d"):
            rules.append({
                "component": comp["id"],
                "rule": "zero_sequence_blocked",
                "reason": "delta winding prevents zero-sequence currents "
                          "from propagating",
            })
    return rules
