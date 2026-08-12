"""学生交流ネットワークの生成と複数ラウンド比較。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

import networkx as nx
import pandas as pd

from matching import Method, PairEvaluation, make_matching
from metrics import graph_metrics


PRESETS: dict[str, tuple[float, float, float]] = {
    "分断が強い": (0.70, 0.15, 0.01),
    "標準": (0.60, 0.20, 0.05),
    "分断が弱い": (0.50, 0.25, 0.15),
}

MAX_STUDENT_COUNT = 200
MAX_GRADE_COUNT = 5


@dataclass(frozen=True)
class NetworkParameters:
    student_count: int = 40
    grade_count: int = 4
    groups_per_grade: int = 2
    same_group_probability: float = 0.60
    same_grade_probability: float = 0.20
    different_grade_probability: float = 0.05
    seed: int = 42

    def validate(self) -> None:
        if not 2 <= self.student_count <= MAX_STUDENT_COUNT:
            raise ValueError(f"学生数は2人以上{MAX_STUDENT_COUNT}人以下にしてください")
        if not 1 <= self.grade_count <= min(MAX_GRADE_COUNT, self.student_count):
            raise ValueError(
                f"学年数は1以上、学生数以下かつ{MAX_GRADE_COUNT}学年以下にしてください"
            )
        if self.groups_per_grade < 1:
            raise ValueError("各学年の友人グループ数は1以上にしてください")
        for probability in (
            self.same_group_probability,
            self.same_grade_probability,
            self.different_grade_probability,
        ):
            if not 0 <= probability <= 1:
                raise ValueError("辺生成確率は0から1の範囲にしてください")


@dataclass
class MethodResult:
    method: Method
    final_graph: nx.Graph
    round_metrics: pd.DataFrame
    matching_details: pd.DataFrame


@dataclass
class ComparisonResult:
    initial_graph: nx.Graph
    random: MethodResult
    proposed: MethodResult


def _balanced_bucket(index: int, item_count: int, bucket_count: int) -> int:
    """0-based indexを、空きをできるだけ作らず均等なbucketへ割り当てる。"""
    return min(bucket_count - 1, (index * bucket_count) // item_count)


def generate_initial_graph(parameters: NetworkParameters) -> nx.Graph:
    """属性を均等配分し、指定確率で架空の初期交流辺を生成する。"""
    parameters.validate()
    rng = random.Random(parameters.seed)
    graph = nx.Graph()

    grade_members: dict[int, list[str]] = {grade: [] for grade in range(1, parameters.grade_count + 1)}
    for index in range(parameters.student_count):
        grade = _balanced_bucket(index, parameters.student_count, parameters.grade_count) + 1
        student_id = f"S{index + 1:03d}"
        grade_members[grade].append(student_id)

    for grade, members in grade_members.items():
        for local_index, student_id in enumerate(members):
            friend_group = _balanced_bucket(local_index, len(members), parameters.groups_per_grade) + 1
            graph.add_node(
                student_id,
                student_id=student_id,
                grade=grade,
                friend_group=friend_group,
            )

    nodes = list(graph.nodes)
    for left_index, u in enumerate(nodes):
        for v in nodes[left_index + 1 :]:
            u_data = graph.nodes[u]
            v_data = graph.nodes[v]
            if u_data["grade"] != v_data["grade"]:
                probability = parameters.different_grade_probability
            elif u_data["friend_group"] == v_data["friend_group"]:
                probability = parameters.same_group_probability
            else:
                probability = parameters.same_grade_probability
            if rng.random() < probability:
                graph.add_edge(u, v, origin="initial")
    return graph


def run_method(
    initial_graph: nx.Graph,
    rounds: int,
    method: Method,
    seed: int,
) -> MethodResult:
    """初期グラフのコピー上で指定方式を実行する。"""
    if rounds < 0:
        raise ValueError("ラウンド数は0以上にしてください")

    graph = initial_graph.copy()
    rng = random.Random(seed)
    metric_rows: list[dict[str, float | int | str]] = []
    matching_rows: list[dict[str, float | int | str]] = []
    total_new_edges = 0
    cross_grade_new_edges = 0

    initial_metrics = graph_metrics(graph)
    metric_rows.append(
        {
            "method": method,
            "round": 0,
            "pairs_added": 0,
            "cumulative_new_edges": 0,
            **initial_metrics,
        }
    )

    for round_number in range(1, rounds + 1):
        selected = make_matching(graph, method, rng)
        for evaluation in selected:
            u, v = evaluation.student_a, evaluation.student_b
            graph.add_edge(u, v, origin=method, round=round_number)
            total_new_edges += 1
            cross_grade_new_edges += evaluation.different_grade
            matching_rows.append(
                {
                    "method": method,
                    "round": round_number,
                    "student_a": u,
                    "student_b": v,
                    "C": evaluation.component_bridge if method == "proposed" else None,
                    "distance": evaluation.distance if method == "proposed" else None,
                    "Y": evaluation.different_grade,
                    "weight": evaluation.weight,
                    "grade_a": graph.nodes[u]["grade"],
                    "grade_b": graph.nodes[v]["grade"],
                }
            )

        current_metrics = graph_metrics(graph, cross_grade_new_edges, total_new_edges)
        metric_rows.append(
            {
                "method": method,
                "round": round_number,
                "pairs_added": len(selected),
                "cumulative_new_edges": total_new_edges,
                **current_metrics,
            }
        )
        if not selected:
            break

    return MethodResult(
        method=method,
        final_graph=graph,
        round_metrics=pd.DataFrame(metric_rows),
        matching_details=pd.DataFrame(matching_rows),
    )


def run_comparison(initial_graph: nx.Graph, rounds: int, seed: int) -> ComparisonResult:
    """完全に同じ初期グラフから両方式を実行する。"""
    return ComparisonResult(
        initial_graph=initial_graph.copy(),
        random=run_method(initial_graph, rounds, "random", seed + 1_000_003),
        proposed=run_method(initial_graph, rounds, "proposed", seed + 2_000_003),
    )


def run_multiple_experiments(
    base_parameters: NetworkParameters,
    rounds: int,
    experiment_count: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """seedを順に変えて独立実験し、最終値の生データと集計を返す。"""
    if experiment_count < 1:
        raise ValueError("実験回数は1以上にしてください")

    final_rows: list[dict[str, float | int | str]] = []
    metric_columns = [
        "component_count",
        "cross_grade_new_edge_ratio",
        "largest_component_ratio",
        "global_efficiency",
    ]
    for experiment in range(experiment_count):
        trial_seed = base_parameters.seed + experiment
        trial_parameters = NetworkParameters(
            **{**base_parameters.__dict__, "seed": trial_seed}
        )
        initial_graph = generate_initial_graph(trial_parameters)
        comparison = run_comparison(initial_graph, rounds, trial_seed)
        for result in (comparison.random, comparison.proposed):
            final = result.round_metrics.iloc[-1]
            final_rows.append(
                {
                    "experiment": experiment + 1,
                    "seed": trial_seed,
                    "method": result.method,
                    **{column: final[column] for column in metric_columns},
                }
            )

    raw = pd.DataFrame(final_rows)
    summary = (
        raw.groupby("method", sort=False)[metric_columns]
        .agg(["mean", lambda values: values.std(ddof=0)])
        .reset_index()
    )
    summary.columns = [
        "method" if column[0] == "method" else f"{column[0]}_{'std' if column[1] == '<lambda_0>' else column[1]}"
        for column in summary.columns
    ]
    return raw, summary
