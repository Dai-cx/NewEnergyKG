#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检索消融实验命令行入口（Week2 D4-5 升级版）

对比组（能装什么装什么，真实跑分会自动跳过缺失的组）：
    纯KG图谱 / 纯向量 / 纯BM25 / 混合(RRF) / 混合(RRF)+重排

用法：
    # 全量（默认评测集 100 题；有 Key 时自动加入"混合+重排"组）
    python -m qa.eval.run

    # 试跑：只取前 20 题 + 调 RRF 平滑常数 k
    python -m qa.eval.run --limit 20 --fusion-k 30 --output qa/eval/ablation_fk30.md

前提：
    - Neo4j 已连接（KG 图谱检索器）和/或 Qdrant 已有文档（向量/BM25 检索器）；
    - 评测集 = qa/eval_dataset.json + qa/eval_dataset_extra.json（合计 100 题）；
    - 配置 DASHSCOPE_API_KEY 时，"混合(RRF)+重排"组使用真实 gte-rerank，
      否则跳过该组（与"混合(RRF)"完全重复没有对比价值）。
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from qa.data_fallback import LocalDataStore
from qa.eval.ablation import run_ablation, write_markdown_report
from qa.eval.dataset import judge_result, load_retrieval_dataset, sample_dataset
from qa.retrieval.base import Retriever
from qa.retrieval.factory import (
    try_build_corpus_retrievers,
    try_build_kg_retriever,
)
from qa.retrieval.hybrid import HybridRetriever
from qa.retrieval.reranker import Reranker, create_reranker

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "retrieval_ablation_report.md"


def build_retriever_groups(
    kg: Optional[Retriever] = None,
    corpus: Optional[tuple] = None,
    fusion_k: int = 60,
    reranker: Optional[Reranker] = None,
    rerank_candidates: int = 50,
) -> Dict[str, Retriever]:
    """
    按"能装什么装什么"装配消融对比组（纯函数，便于离线测试）。

    Args:
        kg: KG 图谱检索器（None 则跳过该组）。
        corpus: (vector_retriever, bm25_retriever)；None 则跳过文档相关组。
        fusion_k: RRF 平滑常数 k。
        reranker: 提供时加入"混合(RRF)+重排"组（传入 NoopReranker 也可，
                  测试/离线需要显式对比时使用；真实 CLI 只在非 Noop 时加组）。
        rerank_candidates: 送入重排器的候选条数（两阶段检索池子）。

    Returns:
        {"纯KG图谱": ..., "纯向量": ..., "纯BM25": ..., "混合(RRF)": ...,
         "混合(RRF)+重排": ...}
    """
    groups: Dict[str, Retriever] = {}

    if kg is not None:
        groups["纯KG图谱"] = kg

    if corpus is not None:
        vector_retriever, bm25_retriever = corpus
        groups["纯向量"] = vector_retriever
        groups["纯BM25"] = bm25_retriever

        hybrid = HybridRetriever(
            retrievers=[vector_retriever, bm25_retriever],
            fusion_k=fusion_k,
        )
        groups["混合(RRF)"] = hybrid

        if reranker is not None:
            groups["混合(RRF)+重排"] = HybridRetriever(
                retrievers=[vector_retriever, bm25_retriever],
                fusion_k=fusion_k,
                reranker=reranker,
                rerank_candidates=rerank_candidates,
            )

    return groups


def main(argv=None) -> int:
    argp = argparse.ArgumentParser(description="NewEnergyKG 检索消融实验（多路对比）")
    argp.add_argument("--top-k", type=int, default=5, help="评估 K 值（默认 5）")
    argp.add_argument("--collection", default=None,
                      help="Qdrant 集合名（切分粒度对比实验用；默认见 qa/config.py 的 QDRANT_COLLECTION）")
    argp.add_argument("--fusion-k", type=int, default=60,
                      help="RRF 平滑常数 k（调参实验用，默认 60）")
    argp.add_argument("--rerank-candidates", type=int, default=50,
                      help="重排候选池大小（默认 50）")
    argp.add_argument("--limit", type=int, default=None, help="评测集抽样数量（试跑用）")
    argp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                      help="Markdown 报告输出路径")
    args = argp.parse_args(argv)

    # ---- 1. 本地实体表（KG 检索器需要实体名做实体识别）----
    try:
        entity_names = LocalDataStore().get_all_names()
    except Exception as e:
        logger.error(f"本地数据加载失败：{e}")
        entity_names = []

    # ---- 2. 装配对比组 ----
    kg = try_build_kg_retriever(entity_names)
    corpus = try_build_corpus_retrievers(collection=args.collection)

    reranker = create_reranker()
    rerank_group_enabled = reranker.name != "noop"
    groups = build_retriever_groups(
        kg=kg,
        corpus=corpus,
        fusion_k=args.fusion_k,
        reranker=reranker if rerank_group_enabled else None,
        rerank_candidates=args.rerank_candidates,
    )

    if not groups:
        logger.error("没有任何检索器可用：请配置 Neo4j 或先摄取文档到 Qdrant")
        return 1

    logger.info(f"本次消融对比组：{list(groups.keys())}")
    if corpus is not None and not rerank_group_enabled:
        logger.warning(
            "未配置 DASHSCOPE_API_KEY（或重排器不可用）："
            "跳过「混合(RRF)+重排」组 —— 当前等价于「混合(RRF)」，对比无意义"
        )

    # ---- 3. 加载评测集（过滤多轮指代题后为可独立检索子集）----
    dataset = sample_dataset(load_retrieval_dataset(), limit=args.limit)
    logger.info(f"评测集规模：{len(dataset)} 条（多轮指代题已自动过滤）")

    # ---- 4. 跑消融 ----
    rows = run_ablation(
        retrievers=groups,
        dataset=dataset,
        judge=judge_result,
        top_k=args.top_k,
    )
    for row in rows:
        logger.info(
            f"[{row['retriever']}] Recall@{args.top_k}={row['recall_at_k']:.2%} "
            f"MRR={row['mrr']:.4f} Precision@{args.top_k}={row['precision_at_k']:.2%}"
        )

    # ---- 5. 输出报告（带实验配置，保证可复现）----
    meta = [
        f"评测集：qa/eval_dataset.json + qa/eval_dataset_extra.json"
        f"（共 {len(dataset)} 题参与，100 题中自动过滤多轮指代题）",
        f"文档集合：{args.collection or 'config.QDRANT_COLLECTION（默认）'}",
        f"RRF 平滑常数 k = {args.fusion_k}",
        f"重排候选池 = {args.rerank_candidates}；"
        f"真实重排组：{'已启用（' + reranker.name + '）' if rerank_group_enabled else '跳过（无 Key 时与混合组重复）'}",
    ]
    notes = [
        "相关性判定为 LLM-free 近似：期望实体/章节出现在检索文本或章节路径中即视为相关。",
        "纯KG 组只评估图谱能答的问题；文档组（向量/BM25/混合）评估文档语料召回。",
        "生成质量四指标（faithfulness 等）用 LLM-as-judge：python -m qa.eval.gen_run。",
    ]
    path = write_markdown_report(
        rows=rows,
        output_path=args.output,
        notes=notes,
        top_k=args.top_k,
        meta=meta,
    )
    logger.info(f"报告已保存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
