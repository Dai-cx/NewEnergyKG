#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检索参数扫描工具（Week2 D6：依据消融结果调参的第一步）

对同一份评测集，扫描一组可配置参数，输出一张可对比的报告：
    1) RRF 平滑常数 k（融合的"排名→分数"衰减快慢）；
    2) 重排候选池大小 rerank_candidates（两阶段检索的粗排池子，仅真实重排时有意义）。

用法：
    # 只扫 fusion-k（离线/无 Key 也可跑，对照 纯向量/纯BM25 基线）
    python -m qa.eval.tune --fusion-k-values 30,60,100 --limit 30

    # 有 DashScope Key 时同时扫重排池
    python -m qa.eval.tune --fusion-k-values 60 --rerank-candidates-values 30,50,100

    # 指定语料集合（配合切分粒度实验：同一批文档按不同 chunk 尺寸分别摄取）
    python -m qa.eval.tune --collection newenergy_c400 --fusion-k-values 60

前提：
    - 与 qa.eval.run 相同：Neo4j/Qdrant 按需就绪；
    - 评测集 = qa/eval_dataset.json + qa/eval_dataset_extra.json（合计 100 题）。
说明：切分粒度本身不在此命令内调——它需要重新摄取文档到不同集合
    （qa.ingestion.run --collection X --chunk-size N），再对每个集合分别跑本命令对比。
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger

from qa import config
from qa.data_fallback import LocalDataStore
from qa.eval.ablation import run_ablation, write_markdown_report
from qa.eval.dataset import judge_result, load_retrieval_dataset, sample_dataset
from qa.eval.run import build_retriever_groups
from qa.retrieval.base import Retriever
from qa.retrieval.factory import try_build_corpus_retrievers, try_build_kg_retriever
from qa.retrieval.hybrid import HybridRetriever
from qa.retrieval.reranker import Reranker, create_reranker

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "retrieval_tuning_report.md"


def build_sweep_groups(
    kg: Optional[Retriever] = None,
    corpus: Optional[tuple] = None,
    fusion_ks: Sequence[int] = (60,),
    reranker: Optional[Reranker] = None,
    rerank_candidates: Sequence[int] = (),
) -> Dict[str, Retriever]:
    """
    装配"参数扫描"所需的对比组（纯函数，可离线测试）。

    组命名自带参数，保证报告行可读：
        - 纯KG图谱 / 纯向量 / 纯BM25（基线，只出现一次）
        - 混合(RRF) k=30 / 混合(RRF) k=60 / ...
        - 混合(RRF)+重排 k=60 cand=30 / ...

    Args:
        kg: KG 图谱检索器。
        corpus: (vector_retriever, bm25_retriever)。
        fusion_ks: 要扫描的 RRF 平滑常数列表（去重保序）。
        reranker: 非 None 时才生成"重排"变体（调用方决定是否传真实重排器）。
        rerank_candidates: 要扫描的重排候选池大小列表。
    """
    groups: Dict[str, Retriever] = {}
    if kg is not None:
        groups["纯KG图谱"] = kg
    if corpus is None:
        return groups
    vector_retriever, bm25_retriever = corpus
    groups["纯向量"] = vector_retriever
    groups["纯BM25"] = bm25_retriever

    ks = list(dict.fromkeys(int(k) for k in fusion_ks)) or [60]
    for k in ks:
        groups[f"混合(RRF) k={k}"] = HybridRetriever(
            retrievers=[vector_retriever, bm25_retriever], fusion_k=k
        )
        if reranker is not None:
            cands = [int(c) for c in dict.fromkeys(rerank_candidates)] or [50]
            for cand in cands:
                groups[f"混合(RRF)+重排 k={k} cand={cand}"] = HybridRetriever(
                    retrievers=[vector_retriever, bm25_retriever],
                    fusion_k=k,
                    reranker=reranker,
                    rerank_candidates=cand,
                )
    return groups


def _parse_int_list(value: Optional[str]) -> List[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    argp = argparse.ArgumentParser(description="NewEnergyKG 检索参数扫描（D6 调参）")
    argp.add_argument("--top-k", type=int, default=5, help="评估 K 值（默认 5）")
    argp.add_argument("--limit", type=int, default=None, help="评测集抽样数量（试跑用）")
    argp.add_argument("--collection", default=None,
                      help="Qdrant 集合名（切分粒度实验用；默认见 qa/config.py）")
    argp.add_argument("--fusion-k-values", default="30,60,100",
                      help="RRF 平滑常数 k 扫描列表，逗号分隔（默认 30,60,100）")
    argp.add_argument("--rerank-candidates-values", default=None,
                      help=f"重排候选池扫描列表，逗号分隔"
                           f"（默认仅扫 config.RERANK_CANDIDATES={config.RERANK_CANDIDATES}；"
                           f"注意组数 = fusion-k 数 × 候选数，每次重排都按 token 计费）")
    argp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                      help="Markdown 报告输出路径")
    args = argp.parse_args(argv)

    fusion_ks = _parse_int_list(args.fusion_k_values) or [60]
    cands = _parse_int_list(args.rerank_candidates_values) or [config.RERANK_CANDIDATES]

    # ---- 检索器 ----
    try:
        entity_names = LocalDataStore().get_all_names()
    except Exception as e:
        logger.error(f"本地数据加载失败：{e}")
        entity_names = []
    kg = try_build_kg_retriever(entity_names)
    corpus = try_build_corpus_retrievers(collection=args.collection)

    reranker: Optional[Reranker] = None
    if corpus is not None:
        reranker = create_reranker()
        if reranker.name == "noop":
            logger.warning("未配置真实重排（NoopReranker）：本次只扫描 fusion-k，"
                           "重排候选池维度跳过（离线无对比意义）")
            reranker = None

    groups = build_sweep_groups(
        kg=kg,
        corpus=corpus,
        fusion_ks=fusion_ks,
        reranker=reranker,
        rerank_candidates=cands if reranker else (),
    )
    if not groups:
        logger.error("没有任何检索器可用：请配置 Neo4j 或先摄取文档到 Qdrant")
        return 1
    logger.info(f"本次扫描组数：{len(groups)} -> {list(groups)}")

    # ---- 评测 ----
    dataset = sample_dataset(load_retrieval_dataset(), limit=args.limit)
    logger.info(f"评测集规模：{len(dataset)} 条（多轮指代题已过滤）")

    rows = run_ablation(groups, dataset, judge_result, top_k=args.top_k)
    for row in rows:
        logger.info(
            f"[{row['retriever']}] Recall@{args.top_k}={row['recall_at_k']:.2%} "
            f"MRR={row['mrr']:.4f} Precision@{args.top_k}={row['precision_at_k']:.2%}"
        )

    # ---- 报告 ----
    meta = [
        f"评测集 {len(dataset)} 题；文档集合：{args.collection or '默认'}",
        f"扫描维度：fusion-k ∈ {fusion_ks}"
        + (f"，rerank_candidates ∈ {cands}" if reranker else "（无真实重排，仅 k 维）"),
    ]
    notes = [
        "调参原则：其他条件不变只改一个参数；同一份评测集、同一批语料。",
        "建议：先看 Recall@K 是否下降（降=参数把相关结果排丢了），"
        "再在同 Recall 下选 Precision/MRR 更高的一组。",
        "切分粒度调参流程：qa.ingestion.run --collection <名> --chunk-size N 摄取后，"
        "对每个集合分别运行本命令对比。",
    ]
    path = write_markdown_report(
        rows=rows,
        output_path=args.output,
        title="检索参数扫描报告（fusion-k / rerank 候选池）",
        notes=notes,
        top_k=args.top_k,
        meta=meta,
    )
    logger.info(f"参数扫描报告已保存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
