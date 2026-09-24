# -*- coding: utf-8 -*-
"""
消融实验编排

"消融实验（ablation study）"：其他条件不变，只切换其中一个组件，
观察指标变化 —— 用来证明"某组件是否真的有效"。

本模块对同一份评测集分别运行多套检索配置（如 纯向量 / 纯BM25 / 混合RRF），
汇总三指标并输出 Markdown 对比报告。
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional

from qa.eval.metrics import summarize_retrieval
from qa.retrieval.base import Retriever


def evaluate_retriever(
    retriever: Retriever,
    dataset: List[Dict],
    judge: Callable,
    top_k: int = 5,
) -> Dict:
    """
    对单个检索器跑完整个评测集。

    Returns:
        {"summary": {...指标}, "per_query": [[bool...], ...]}
    """
    per_query = []
    for entry in dataset:
        question = entry.get("question", "")
        if not question:
            continue
        results = retriever.retrieve(question, top_k=top_k)
        relevant = [judge(r, entry) for r in results]
        # 结果不足 K 条时补 False，保证各查询长度一致、指标可比
        relevant = (relevant + [False] * top_k)[:top_k]
        per_query.append(relevant)

    return {
        "summary": summarize_retrieval(per_query, top_k=top_k),
        "per_query": per_query,
    }


def run_ablation(
    retrievers: Dict[str, Retriever],
    dataset: List[Dict],
    judge: Callable,
    top_k: int = 5,
) -> List[Dict]:
    """
    对多套检索配置跑消融。

    Args:
        retrievers: {"向量": VectorRetriever, "BM25": BM25Retriever, "混合RRF": ...}
        dataset: 评测条目列表。
        judge: 相关性判定函数 judge(result, entry) -> bool。

    Returns:
        每套配置一行：{"retriever", "queries", "recall_at_k", "mrr", "precision_at_k"}
    """
    rows = []
    for name, retriever in retrievers.items():
        outcome = evaluate_retriever(retriever, dataset, judge, top_k=top_k)
        summary = outcome["summary"]
        rows.append({
            "retriever": name,
            "queries": summary["queries"],
            "recall_at_k": summary["recall_at_k"],
            "mrr": summary["mrr"],
            "precision_at_k": summary["precision_at_k"],
        })
    return rows


def write_markdown_report(
    rows: List[Dict],
    output_path: Path,
    title: str = "检索消融实验报告",
    notes: Optional[List[str]] = None,
    top_k: int = 5,
    meta: Optional[List[str]] = None,
) -> Path:
    """
    把消融结果写成 Markdown 报告，返回文件路径。

    Args:
        rows: 每套配置一行（run_ablation 的输出）。
        output_path: 报告输出路径。
        title: 报告标题。
        notes: 报告末尾"说明"列表。
        top_k: 评估 K 值（写进报告头）。
        meta: 可选的实验配置描述（评测集规模 / fusion-k / 是否重排等），
              放在表头下方，让报告"可复现"。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# {title}", ""]
    lines.append(f"> K = {top_k}（Recall@{top_k} / Precision@{top_k}）")
    if meta:
        lines.append("")
        for line in meta:
            lines.append(f"> {line}")
    lines.append("")
    lines.append("| 检索配置 | 查询数 | Recall@K | MRR | Precision@K |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        lines.append(
            f"| {row['retriever']} | {row['queries']} | "
            f"{row['recall_at_k']:.4f} | {row['mrr']:.4f} | {row['precision_at_k']:.4f} |"
        )
    lines.append("")

    if notes:
        lines.append("## 说明")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
