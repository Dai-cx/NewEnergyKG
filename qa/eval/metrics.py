# -*- coding: utf-8 -*-
"""
排名指标计算（检索评估核心）

输入：每个查询的"相关性布尔列表"（前 K 名结果是否相关，已按检索分数降序）
输出：三个聚合指标

指标定义（务必理解再使用）：
    Recall@K    : 前 K 名里是否出现相关结果（0/1），对所有查询取平均
                  → "该问题要的资料有没有被捞回来"（找得到吗）
    MRR         : 第一个相关结果排名的倒数（无相关则 0），取平均
                  → "第一个正确答案排得够不够靠前"
    Precision@K : 前 K 名中相关结果占比，取平均
                  → "捞回来的前 K 条里有多少是有用的"（噪音大吗）

    "Recall 看漏没漏，MRR 看排得靠不靠前，Precision 看噪音多不多"。
"""

from typing import Dict, List


def mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def metric_recall_at_k(relevant: List[bool], k: int) -> int:
    """前 K 名中是否至少有一个相关结果（hit）"""
    return 1 if any(relevant[:k]) else 0


def metric_mrr(relevant: List[bool]) -> float:
    """第一个相关结果的倒数排名；无相关返回 0"""
    for idx, flag in enumerate(relevant, start=1):
        if flag:
            return 1.0 / idx
    return 0.0


def metric_precision_at_k(relevant: List[bool], k: int) -> float:
    """前 K 名中相关结果的比例"""
    if k <= 0:
        return 0.0
    return sum(relevant[:k]) / k


def summarize_retrieval(per_query_relevance: List[List[bool]], top_k: int = 5) -> Dict:
    """
    汇总多查询的排名指标。

    Args:
        per_query_relevance: 每个查询的相关性布尔列表（长度通常 = top_k）。
        top_k: 计算 Recall@K / Precision@K 的 K 值。

    Returns:
        {"recall_at_k", "mrr", "precision_at_k", "queries", "relevant_queries"}
        relevant_queries = 至少有一条相关结果的查询数。
    """
    recalls = [metric_recall_at_k(rel, top_k) for rel in per_query_relevance]
    mrrs = [metric_mrr(rel) for rel in per_query_relevance]
    precisions = [metric_precision_at_k(rel, top_k) for rel in per_query_relevance]

    return {
        "queries": len(per_query_relevance),
        "relevant_queries": sum(recalls),
        "recall_at_k": round(mean(recalls), 4),
        "mrr": round(mean(mrrs), 4),
        "precision_at_k": round(mean(precisions), 4),
    }
