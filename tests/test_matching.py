import math
import random

import networkx as nx

from matching import candidate_pairs, proposed_matching, random_matching, weight_constants


def _graph_with_grades(count: int) -> nx.Graph:
    graph = nx.Graph()
    for index in range(count):
        graph.add_node(f"S{index + 1:03d}", grade=(index % 2) + 1, friend_group=1)
    return graph


def test_existing_edges_are_not_candidates() -> None:
    graph = _graph_with_grades(3)
    graph.add_edge("S001", "S002")
    candidates = {frozenset(pair) for pair in candidate_pairs(graph)}
    assert frozenset(("S001", "S002")) not in candidates
    assert frozenset(("S001", "S003")) in candidates


def test_student_is_used_at_most_once_for_both_methods() -> None:
    graph = _graph_with_grades(9)
    for matching in (proposed_matching(graph), random_matching(graph, random.Random(7))):
        students = [student for pair in matching for student in (pair.student_a, pair.student_b)]
        assert len(students) == len(set(students))
        assert len(matching) == math.floor(graph.number_of_nodes() / 2)


def test_coefficients_guarantee_lexicographic_priority() -> None:
    constants = weight_constants(40)
    assert constants.b > constants.m
    maximum_lower_priority_difference = (
        constants.m * constants.b * constants.d_max + constants.m
    )
    assert constants.a > maximum_lower_priority_difference


def test_disconnected_pair_has_zero_distance_and_c_one() -> None:
    graph = _graph_with_grades(4)
    graph.add_edge("S001", "S003")
    graph.add_edge("S002", "S004")
    selected = proposed_matching(graph)
    assert selected
    assert all(pair.component_bridge == 1 for pair in selected)
    assert all(pair.distance == 0 for pair in selected)
