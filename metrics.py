"""交流グラフの評価指標。"""

from __future__ import annotations

import networkx as nx


def graph_metrics(
    graph: nx.Graph,
    cross_grade_new_edges: int = 0,
    total_new_edges: int = 0,
) -> dict[str, float | int]:
    """指定された主指標・補助指標を計算する。"""
    n = graph.number_of_nodes()
    if n == 0:
        return {
            "component_count": 0,
            "cross_grade_new_edge_ratio": 0.0,
            "largest_component_ratio": 0.0,
            "global_efficiency": 0.0,
        }

    components = list(nx.connected_components(graph))
    return {
        "component_count": len(components),
        "cross_grade_new_edge_ratio": (
            cross_grade_new_edges / total_new_edges if total_new_edges else 0.0
        ),
        "largest_component_ratio": max(len(component) for component in components) / n,
        "global_efficiency": nx.global_efficiency(graph),
    }
