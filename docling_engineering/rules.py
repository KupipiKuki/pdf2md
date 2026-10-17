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


def build_graph(topology):
    """Build an undirected networkx graph from SLD topology JSON
    ({"components": [...], "connections": [[a, b], ...]})."""
    if nx is None:
        raise ImportError("run `pip install networkx`")
    g = nx.Graph()
    for comp in topology.get("components", []):
        g.add_node(comp["id"], **{k: v for k, v in comp.items() if k != "id"})
    for a, b in topology.get("connections", []):
        g.add_edge(a, b)
    return g


def chain_reactances(graph, source, sink):
    """Return the series reactance chain from source to sink as a list of
    (component_id, z_pu) pairs along the connecting path."""
    path = nx.shortest_path(graph, source, sink)
    return [(nid, graph.nodes[nid].get("z_pu")) for nid in path]


def sequence_networks(graph, source, sink):
    """Derive the positive/negative sequence network chain between a
    source (generator) and sink (motor). Positive and negative sequence
    networks share the same series reactances for passive components."""
    chain = chain_reactances(graph, source, sink)
    net = nx.Graph()
    for i, (nid, z) in enumerate(chain):
        net.add_node(f"{nid}", z_pu=z)
    return net


def transformer_zero_sequence_rules(components):
    """Apply transformer winding-connection rules: a delta winding blocks
    zero-sequence current propagation on its side of the network."""
    rules = []
    for comp in components:
        conn = (comp.get("connection") or "").lower()
        if "delta" in conn:
            rules.append({
                "component": comp["id"],
                "rule": "zero_sequence_blocked",
                "reason": "delta winding prevents zero-sequence currents "
                          "from propagating",
            })
    return rules
