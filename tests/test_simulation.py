import networkx as nx

from simulation import (
    NetworkParameters,
    generate_initial_graph,
    run_comparison,
    run_method,
    run_multiple_experiments,
)


def test_initial_graph_generation_is_reproducible_and_has_attributes() -> None:
    parameters = NetworkParameters(seed=123)
    first = generate_initial_graph(parameters)
    second = generate_initial_graph(parameters)
    assert first.number_of_nodes() == 40
    assert set(first.edges) == set(second.edges)
    assert {first.nodes[node]["grade"] for node in first} == {1, 2, 3, 4}
    assert {first.nodes[node]["friend_group"] for node in first} == {1, 2}


def test_methods_start_from_the_same_initial_graph_without_mutating_it() -> None:
    initial = generate_initial_graph(NetworkParameters(seed=9))
    edges_before = set(initial.edges)
    comparison = run_comparison(initial, rounds=2, seed=9)
    assert set(comparison.initial_graph.edges) == edges_before
    assert set(initial.edges) == edges_before
    assert set(comparison.random.final_graph.edges).issuperset(edges_before)
    assert set(comparison.proposed.final_graph.edges).issuperset(edges_before)


def test_edges_are_added_after_interaction() -> None:
    graph = nx.Graph()
    graph.add_node("A", grade=1, friend_group=1)
    graph.add_node("B", grade=2, friend_group=1)
    result = run_method(graph, rounds=1, method="proposed", seed=1)
    assert result.final_graph.has_edge("A", "B")
    assert len(result.matching_details) == 1
    assert result.round_metrics.iloc[-1]["cumulative_new_edges"] == 1


def test_balanced_assignment_works_for_non_divisible_counts() -> None:
    graph = generate_initial_graph(
        NetworkParameters(student_count=11, grade_count=3, groups_per_grade=2, seed=1)
    )
    grade_sizes = [
        sum(1 for _, data in graph.nodes(data=True) if data["grade"] == grade)
        for grade in (1, 2, 3)
    ]
    assert max(grade_sizes) - min(grade_sizes) <= 1


def test_multiple_experiments_reports_mean_and_population_std() -> None:
    raw, summary = run_multiple_experiments(
        NetworkParameters(student_count=12, grade_count=3, seed=10),
        rounds=2,
        experiment_count=3,
    )
    assert len(raw) == 6
    assert set(raw["method"]) == {"random", "proposed"}
    assert "component_count_mean" in summary.columns
    assert "component_count_std" in summary.columns
