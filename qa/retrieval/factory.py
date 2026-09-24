# -*- coding: utf-8 -*-
"""
检索器工厂：根据当前环境可用性，组装"默认检索管线"

优先级：
    KG 可用（Neo4j 已连接）        → KGRetriever
    Qdrant 集合存在且有数据        → VectorRetriever + BM25Retriever
    （Embedder 未配 Key 时用 Debug 伪向量，仅验证流程）
"""

from typing import List, Optional, Tuple

from loguru import logger

from qa.data_fallback import LocalDataStore
from qa.ingestion.embedder import create_embedder
from qa.ingestion.qdrant_store import QdrantStore
from qa.kg_client import KGClient
from qa.retrieval.bm25 import BM25Retriever
from qa.retrieval.hybrid import HybridRetriever
from qa.retrieval.kg_retriever import KGRetriever
from qa.retrieval.reranker import create_reranker
from qa.retrieval.vector_retriever import VectorRetriever


def try_build_kg_retriever(entity_names: List[str]) -> Optional[KGRetriever]:
    """Neo4j 已连接则返回 KGRetriever，否则 None"""
    kg_client = KGClient()
    if not kg_client.is_connected():
        logger.warning("[factory] Neo4j 未连接，跳过 KG 检索器")
        return None
    logger.info("[factory] Neo4j 已连接，启用 KG 检索器")
    return KGRetriever(kg_client=kg_client, entity_names=entity_names)


def try_build_corpus_retrievers(
    collection: Optional[str] = None,
) -> Optional[Tuple[VectorRetriever, BM25Retriever]]:
    """
    连接 Qdrant，若集合存在且有数据，返回 (VectorRetriever, BM25Retriever)。
    任何一步失败都返回 None（不抛异常，让检索管线优雅降级）。

    Args:
        collection: 目标集合名；None 使用 config.QDRANT_COLLECTION。
                    切分粒度实验可对同一语料生成多个集合（如
                    newenergy_c400 / newenergy_c800）后分别评估。
    """
    try:
        store = QdrantStore(collection=collection)
        if not store.collection_exists():
            logger.warning("[factory] Qdrant 集合不存在，跳过文档检索 "
                           f"（可先运行 python -m qa.ingestion.run --input docs/）")
            return None

        embedder = create_embedder()
        vector_retriever = VectorRetriever(store=store, embedder=embedder)

        payloads = store.fetch_all()
        if not payloads:
            logger.warning("[factory] Qdrant 集合为空，跳过文档检索")
            return None

        bm25_retriever = BM25Retriever(documents=payloads)
        logger.info(f"[factory] 文档检索就绪：{len(payloads)} 个 chunk")
        return vector_retriever, bm25_retriever
    except Exception as e:
        logger.warning(f"[factory] Qdrant 不可用，跳过文档检索：{e}")
        return None


def try_build_document_retriever(
    collection: Optional[str] = None,
) -> Optional[HybridRetriever]:
    """
    构建"文档检索"混合管线（向量 + BM25），供问答引擎使用。

    与 build_default_pipeline 的区别：不含 KG 检索——
    引擎内部已有结构化的图谱检索路径（kg_context），
    这里只负责补上向量库里的文档知识，避免同一事实被注入两次。

    Args:
        collection: 目标 Qdrant 集合名（默认 config.QDRANT_COLLECTION）。
    """
    corpus = try_build_corpus_retrievers(collection=collection)
    if corpus is None:
        return None
    vector_retriever, bm25_retriever = corpus
    # 两阶段检索：RRF 融合粗排 → rerank 精排（未配 Key 时 Noop 保持原序）
    return HybridRetriever(
        retrievers=[vector_retriever, bm25_retriever],
        reranker=create_reranker(),
    )


def build_default_pipeline() -> HybridRetriever:
    """
    组装默认混合检索管线（可用什么装什么）：
        KG（若 Neo4j 连上） + 向量 + BM25（若 Qdrant 有数据）
    """
    # 实体名词表来自本地 JSON（供 KG 实体识别）
    try:
        data_store = LocalDataStore()
        entity_names = data_store.get_all_names()
    except Exception as e:
        logger.warning(f"[factory] 本地数据加载失败：{e}")
        entity_names = []

    retrievers: List = []
    kg = try_build_kg_retriever(entity_names)
    if kg is not None:
        retrievers.append(kg)

    corpus = try_build_corpus_retrievers()
    if corpus is not None:
        vector_retriever, bm25_retriever = corpus
        retrievers.append(vector_retriever)
        retrievers.append(bm25_retriever)

    if not retrievers:
        logger.error(
            "[factory] 没有任何检索器可用：请配置 Neo4j（图谱）或先摄取文档到 Qdrant"
        )
    else:
        logger.info(f"[factory] 混合检索管线就绪：{[r.name for r in retrievers]}")

    return HybridRetriever(retrievers=retrievers, reranker=create_reranker())
