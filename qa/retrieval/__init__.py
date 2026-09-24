# -*- coding: utf-8 -*-
"""
qa.retrieval —— 统一检索层（RAG 的"召回"环节）

把多种知识来源收敛到统一接口（Retriever.retrieve）与统一结果（RetrievalResult）：
    KG 检索（结构化图谱，kg_retriever）
    向量检索（语义，vector_retriever）
    BM25 关键词检索（bm25）
    混合检索（多路召回 + RRF 融合，hybrid）

使用示例：
    from qa.retrieval.factory import build_default_pipeline
    pipeline = build_default_pipeline()
    results = pipeline.retrieve("磷酸铁锂电池有哪些优点？", top_k=5)
"""

from qa.retrieval.base import RetrievalResult, Retriever, result_key
from qa.retrieval.bm25 import BM25Index, BM25Retriever
from qa.retrieval.hybrid import HybridRetriever, rrf_fuse
from qa.retrieval.kg_retriever import KGRetriever
from qa.retrieval.reranker import (
    DashScopeReranker,
    NoopReranker,
    Reranker,
    create_reranker,
)
from qa.retrieval.vector_retriever import VectorRetriever

__all__ = [
    "RetrievalResult",
    "Retriever",
    "result_key",
    "BM25Index",
    "BM25Retriever",
    "VectorRetriever",
    "KGRetriever",
    "HybridRetriever",
    "rrf_fuse",
    "Reranker",
    "NoopReranker",
    "DashScopeReranker",
    "create_reranker",
]
