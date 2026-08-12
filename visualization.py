"""NetworkXグラフを授業説明向けのPlotly図へ変換する。"""

from __future__ import annotations

from collections.abc import Mapping

import networkx as nx
import plotly.graph_objects as go


GRADE_COLORS = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#4f46e5",
    "#be123c",
]


def graph_layout(graph: nx.Graph, seed: int) -> dict[object, tuple[float, float]]:
    """同じseedで比較図の配置を揃える。"""
    raw = nx.spring_layout(graph, seed=seed, weight=None, k=0.55, iterations=80)
    return {node: (float(position[0]), float(position[1])) for node, position in raw.items()}


def graph_figure(
    graph: nx.Graph,
    title: str,
    positions: Mapping[object, tuple[float, float]] | None = None,
    seed: int = 42,
) -> go.Figure:
    positions = dict(positions) if positions is not None else graph_layout(graph, seed)
    initial_x: list[float | None] = []
    initial_y: list[float | None] = []
    added_x: list[float | None] = []
    added_y: list[float | None] = []

    for u, v, data in graph.edges(data=True):
        target_x, target_y = (initial_x, initial_y) if data.get("origin") == "initial" else (added_x, added_y)
        target_x.extend((positions[u][0], positions[v][0], None))
        target_y.extend((positions[u][1], positions[v][1], None))

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=initial_x,
            y=initial_y,
            mode="lines",
            line={"width": 0.8, "color": "#94a3b8"},
            hoverinfo="skip",
            name="初期交流辺",
        )
    )
    if added_x:
        figure.add_trace(
            go.Scatter(
                x=added_x,
                y=added_y,
                mode="lines",
                line={"width": 1.8, "color": "#f59e0b"},
                hoverinfo="skip",
                name="新規交流辺",
            )
        )

    nodes = list(graph.nodes)
    grades = [int(graph.nodes[node]["grade"]) for node in nodes]
    hover = [
        f"{node}<br>学年: {graph.nodes[node]['grade']}<br>友人グループ: {graph.nodes[node]['friend_group']}<br>次数: {graph.degree(node)}"
        for node in nodes
    ]
    figure.add_trace(
        go.Scatter(
            x=[positions[node][0] for node in nodes],
            y=[positions[node][1] for node in nodes],
            mode="markers+text",
            text=[str(node).replace("S0", "") for node in nodes],
            textposition="top center",
            textfont={"size": 8, "color": "#334155"},
            hovertext=hover,
            hoverinfo="text",
            marker={
                "size": 12,
                "color": [GRADE_COLORS[(grade - 1) % len(GRADE_COLORS)] for grade in grades],
                "line": {"width": 1, "color": "white"},
            },
            name="学生（色=学年）",
        )
    )
    figure.update_layout(
        title=title,
        showlegend=True,
        hovermode="closest",
        height=520,
        margin={"l": 10, "r": 10, "t": 55, "b": 10},
        xaxis={"visible": False},
        yaxis={"visible": False},
        plot_bgcolor="white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "x": 0},
    )
    return figure
