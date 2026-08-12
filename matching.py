"""1ラウンド分のランダム／提案マッチングを構築する。"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import combinations
from typing import Hashable, Literal

import networkx as nx

Node = Hashable
Method = Literal["random", "proposed"]


@dataclass(frozen=True)
class WeightConstants:
    """辞書式優先順位 C > d > Y を保証する定数。"""

    n: int
    m: int
    d_max: int
    b: int
    a: int


@dataclass(frozen=True)
class PairEvaluation:
    """選ばれたペアと、選択時点での評価値。"""

    student_a: Node
    student_b: Node
    component_bridge: int
    distance: int
    different_grade: int
    weight: int | float


def weight_constants(n: int) -> WeightConstants:
    """仕様どおりの A, B を返す。"""
    if n < 1:
        raise ValueError("n は1以上である必要があります")
    m = math.floor(n / 2)
    d_max = n - 1
    b = m + 1
    a = m * b * d_max + m + 1
    return WeightConstants(n=n, m=m, d_max=d_max, b=b, a=a)


def candidate_pairs(graph: nx.Graph) -> list[tuple[Node, Node]]:
    """自己ループと既交流辺を除いた全候補ペアを返す。"""
    return [
        (u, v)
        for u, v in combinations(graph.nodes, 2)
        if not graph.has_edge(u, v)
    ]


def _normalized_pair(u: Node, v: Node) -> tuple[Node, Node]:
    """表示とテストを安定させるため、グラフのIDを文字列表現順に揃える。"""
    return (u, v) if str(u) <= str(v) else (v, u)


def _selected_edges(matching: set[tuple[Node, Node]]) -> list[tuple[Node, Node]]:
    return sorted((_normalized_pair(u, v) for u, v in matching), key=lambda p: (str(p[0]), str(p[1])))


def proposed_matching(graph: nx.Graph) -> list[PairEvaluation]:
    """最大人数を組ませ、その中で合計 C, d, Y を辞書式に最大化する。"""
    constants = weight_constants(graph.number_of_nodes())
    component_of: dict[Node, int] = {}
    distances: dict[Node, dict[Node, int]] = {}

    for component_index, nodes in enumerate(nx.connected_components(graph)):
        subgraph = graph.subgraph(nodes)
        for node in nodes:
            component_of[node] = component_index
        for source, lengths in nx.all_pairs_shortest_path_length(subgraph):
            distances[source] = dict(lengths)

    candidate_graph = nx.Graph()
    candidate_graph.add_nodes_from(graph.nodes)

    for u, v in candidate_pairs(graph):
        c = int(component_of[u] != component_of[v])
        distance = 0 if c else distances[u][v]
        y = int(graph.nodes[u]["grade"] != graph.nodes[v]["grade"])
        weight = constants.a * c + constants.b * distance + y
        candidate_graph.add_edge(
            u,
            v,
            weight=weight,
            component_bridge=c,
            distance=distance,
            different_grade=y,
        )

    matching = nx.max_weight_matching(candidate_graph, maxcardinality=True, weight="weight")
    return [
        PairEvaluation(
            student_a=u,
            student_b=v,
            component_bridge=candidate_graph[u][v]["component_bridge"],
            distance=candidate_graph[u][v]["distance"],
            different_grade=candidate_graph[u][v]["different_grade"],
            weight=candidate_graph[u][v]["weight"],
        )
        for u, v in _selected_edges(matching)
    ]


def random_matching(graph: nx.Graph, rng: random.Random) -> list[PairEvaluation]:
    """最大人数を保証し、その制約内でseed付きランダム順位により選ぶ。"""
    candidate_graph = nx.Graph()
    candidate_graph.add_nodes_from(graph.nodes)
    for u, v in candidate_pairs(graph):
        candidate_graph.add_edge(u, v, weight=rng.random())

    matching = nx.max_weight_matching(candidate_graph, maxcardinality=True, weight="weight")
    return [
        PairEvaluation(
            student_a=u,
            student_b=v,
            component_bridge=0,
            distance=0,
            different_grade=int(graph.nodes[u]["grade"] != graph.nodes[v]["grade"]),
            weight=candidate_graph[u][v]["weight"],
        )
        for u, v in _selected_edges(matching)
    ]


def make_matching(graph: nx.Graph, method: Method, rng: random.Random) -> list[PairEvaluation]:
    if method == "proposed":
        return proposed_matching(graph)
    if method == "random":
        return random_matching(graph, rng)
    raise ValueError(f"未対応の方式です: {method}")
