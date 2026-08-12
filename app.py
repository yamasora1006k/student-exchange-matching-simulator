"""学生交流マッチング・シミュレータのStreamlit UI。"""

from __future__ import annotations

import networkx as nx
import pandas as pd
import plotly.express as px
import streamlit as st

from matching import weight_constants
from simulation import (
    PRESETS,
    ComparisonResult,
    NetworkParameters,
    generate_initial_graph,
    run_comparison,
    run_multiple_experiments,
)
from visualization import graph_figure, graph_layout


st.set_page_config(
    page_title="学生交流マッチング・シミュレータ",
    page_icon="🔗",
    layout="wide",
)

METHOD_LABELS = {"random": "方法A：ランダム", "proposed": "方法B：最大重みマッチング"}
METRIC_LABELS = {
    "component_count": "連結成分数",
    "cross_grade_new_edge_ratio": "異学年間新規辺割合",
    "largest_component_ratio": "最大連結成分割合",
    "global_efficiency": "グローバル効率",
}


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """Excelでも文字化けしにくいUTF-8 BOM付きCSVを返す。"""
    return frame.to_csv(index=False).encode("utf-8-sig")


def final_metric_table(comparison: ComparisonResult) -> pd.DataFrame:
    rows = []
    for result in (comparison.random, comparison.proposed):
        final = result.round_metrics.iloc[-1]
        rows.append(
            {
                "方式": METHOD_LABELS[result.method],
                "最終連結成分数": int(final["component_count"]),
                "異学年間新規辺割合": float(final["cross_grade_new_edge_ratio"]),
                "最大連結成分割合": float(final["largest_component_ratio"]),
                "グローバル効率": float(final["global_efficiency"]),
                "追加辺数": int(final["cumulative_new_edges"]),
            }
        )
    return pd.DataFrame(rows)


def localized_summary(summary: pd.DataFrame) -> pd.DataFrame:
    renamed = summary.copy()
    renamed["method"] = renamed["method"].map(METHOD_LABELS)
    columns = {"method": "方式"}
    for metric, label in METRIC_LABELS.items():
        columns[f"{metric}_mean"] = f"{label} 平均"
        columns[f"{metric}_std"] = f"{label} 標準偏差"
    return renamed.rename(columns=columns)


st.title("学生交流マッチング・シミュレータ")
st.markdown(
    """
学生を頂点、すでに話したことがある2人を無向辺として表します。同じ初期グラフから、
**最大人数を組ませるランダム方式**と、**普段接点が生まれにくい学生を優先する方式**を実行し、
ネットワークの分断がどう変化するかを比較します。実在する学生データは使用しません。
"""
)

with st.sidebar:
    st.header("シミュレーション設定")
    student_count = st.number_input("学生数", min_value=2, max_value=100, value=40, step=1)
    grade_count = st.number_input(
        "学年数", min_value=1, max_value=min(10, int(student_count)), value=min(4, int(student_count)), step=1
    )
    groups_per_grade = st.number_input("友人グループ数（各学年）", min_value=1, max_value=10, value=2, step=1)
    preset_name = st.selectbox("初期状態プリセット", list(PRESETS), index=1)

    if st.session_state.get("last_preset") != preset_name:
        same_group, same_grade, different_grade = PRESETS[preset_name]
        st.session_state.update(
            probability_same_group=same_group,
            probability_same_grade=same_grade,
            probability_different_grade=different_grade,
            last_preset=preset_name,
        )

    same_group_probability = st.number_input(
        "同学年・同じ友人グループ", min_value=0.0, max_value=1.0, step=0.01, key="probability_same_group"
    )
    same_grade_probability = st.number_input(
        "同学年・別友人グループ", min_value=0.0, max_value=1.0, step=0.01, key="probability_same_grade"
    )
    different_grade_probability = st.number_input(
        "異学年", min_value=0.0, max_value=1.0, step=0.01, key="probability_different_grade"
    )
    seed = st.number_input("乱数 seed", min_value=0, max_value=2_147_483_647, value=42, step=1)
    rounds = st.number_input("交流ラウンド数", min_value=1, max_value=20, value=5, step=1)
    experiment_count = st.number_input("複数回実験回数", min_value=1, max_value=200, value=30, step=1)

parameters = NetworkParameters(
    student_count=int(student_count),
    grade_count=int(grade_count),
    groups_per_grade=int(groups_per_grade),
    same_group_probability=float(same_group_probability),
    same_grade_probability=float(same_grade_probability),
    different_grade_probability=float(different_grade_probability),
    seed=int(seed),
)
preview_graph = generate_initial_graph(parameters)

st.header("1. 初期グラフ")
initial_metrics = {
    "学生数": preview_graph.number_of_nodes(),
    "初期辺数": preview_graph.number_of_edges(),
    "連結成分数": nx.number_connected_components(preview_graph),
}
metric_columns = st.columns(3)
for column, (label, value) in zip(metric_columns, initial_metrics.items()):
    column.metric(label, value)
preview_positions = graph_layout(preview_graph, int(seed))
st.plotly_chart(
    graph_figure(preview_graph, "初期交流ネットワーク（頂点色 = 学年）", preview_positions),
    width="stretch",
)

constants = weight_constants(int(student_count))
with st.expander("提案方式の重みと優先順位"):
    st.latex(r"w(u,v)=A\,C(u,v)+B\,d(u,v)+Y(u,v)")
    st.write(
        f"n={constants.n}, m={constants.m}, D_max={constants.d_max}, "
        f"B={constants.b}, A={constants.a}。最大人数を最初に確保し、その中で合計 C → 合計距離 → 合計 Y の順に最大化します。"
    )

st.warning(
    "**方式の限界：** 1ラウンド分は辺追加前のグラフから一括計算します。同じ2連結成分間から複数ペアが選ばれる場合があるため、"
    "異なる連結成分間のペア数を多くすることと、連結成分数の減少を最大化することは一致しません。"
)

if st.button("シミュレーションを実行", type="primary", width="stretch"):
    with st.spinner("単一比較と複数回実験を計算しています…"):
        initial_graph = generate_initial_graph(parameters)
        comparison = run_comparison(initial_graph, int(rounds), int(seed))
        raw_experiments, experiment_summary = run_multiple_experiments(
            parameters, int(rounds), int(experiment_count)
        )
        st.session_state["simulation_output"] = {
            "parameters": parameters,
            "rounds": int(rounds),
            "comparison": comparison,
            "raw_experiments": raw_experiments,
            "experiment_summary": experiment_summary,
        }

output = st.session_state.get("simulation_output")
if output:
    comparison = output["comparison"]
    result_parameters = output["parameters"]
    result_positions = graph_layout(comparison.initial_graph, result_parameters.seed)

    st.header("2. 方式別の結果")
    random_column, proposed_column = st.columns(2)
    with random_column:
        st.subheader("方法A：ランダム方式")
        st.caption("最大人数を組ませる制約のもと、seed付きランダム順位でペアを選びます。")
        st.plotly_chart(
            graph_figure(comparison.random.final_graph, "ランダム方式・最終グラフ", result_positions),
            width="stretch",
        )
    with proposed_column:
        st.subheader("方法B：最大重みマッチング")
        st.caption("異成分、同一成分内の遠距離、異学年の順に優先します。")
        st.plotly_chart(
            graph_figure(comparison.proposed.final_graph, "提案方式・最終グラフ", result_positions),
            width="stretch",
        )

    st.header("3. 指標比較")
    comparison_table = final_metric_table(comparison)
    st.dataframe(
        comparison_table.style.format(
            {
                "異学年間新規辺割合": "{:.1%}",
                "最大連結成分割合": "{:.1%}",
                "グローバル効率": "{:.4f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    st.header("4. ラウンド別推移")
    round_metrics = pd.concat(
        [comparison.random.round_metrics, comparison.proposed.round_metrics], ignore_index=True
    )
    round_metrics["方式"] = round_metrics["method"].map(METHOD_LABELS)
    selected_metric = st.selectbox(
        "表示する指標",
        list(METRIC_LABELS),
        format_func=lambda key: METRIC_LABELS[key],
    )
    trend = px.line(
        round_metrics,
        x="round",
        y=selected_metric,
        color="方式",
        markers=True,
        labels={"round": "ラウンド", selected_metric: METRIC_LABELS[selected_metric]},
    )
    trend.update_layout(height=430, legend_title_text="")
    st.plotly_chart(trend, width="stretch")
    st.dataframe(round_metrics, width="stretch", hide_index=True)

    st.header("5. なぜこのペアが選ばれたか")
    proposed_details = comparison.proposed.matching_details
    if proposed_details.empty:
        st.info("未交流候補がないため、提案方式で選ばれたペアはありません。")
    else:
        selected_round = st.selectbox(
            "提案方式のラウンド",
            sorted(proposed_details["round"].unique()),
        )
        detail_columns = ["student_a", "student_b", "C", "distance", "Y", "weight"]
        st.dataframe(
            proposed_details.loc[proposed_details["round"] == selected_round, detail_columns],
            width="stretch",
            hide_index=True,
        )
        st.caption("C=1 は異なる連結成分、distance は同一成分内の最短経路長、Y=1 は異学年を表します。")

    st.header("6. 複数回実験")
    summary_for_display = localized_summary(output["experiment_summary"])
    st.caption(
        f"seed {result_parameters.seed} から順に変えた {len(output['raw_experiments']) // 2} 回の実験。標準偏差は母標準偏差（ddof=0）です。"
    )
    st.dataframe(summary_for_display, width="stretch", hide_index=True)

    st.header("7. CSVダウンロード")
    all_matching = pd.concat(
        [comparison.random.matching_details, comparison.proposed.matching_details], ignore_index=True
    )
    download_columns = st.columns(3)
    download_columns[0].download_button(
        "ラウンドごとの指標",
        csv_bytes(round_metrics.drop(columns="方式")),
        "round_metrics.csv",
        "text/csv",
        width="stretch",
    )
    download_columns[1].download_button(
        "マッチング結果",
        csv_bytes(all_matching),
        "matching_results.csv",
        "text/csv",
        width="stretch",
    )
    download_columns[2].download_button(
        "複数回実験の集計",
        csv_bytes(output["experiment_summary"]),
        "experiment_summary.csv",
        "text/csv",
        width="stretch",
    )
else:
    st.info("設定を確認し、「シミュレーションを実行」を押してください。")
