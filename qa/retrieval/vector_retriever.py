# -*- coding: utf-8 -*-
"""
向量检索器：把 QdrantStore + Embedder 包装成统一 Retriever 接口
"""

from typing import List, Optional

from loguru import logger

from qa.ingestion.embedder import Embedder
from qa.ingestion.qdrant_store import QdrantStore
from qa.retrieval.base import RetrievalResult, Retriever


class VectorRetriever(Retriever):
    """语义检索：用户问题 → 向量 → Qdrant Top-K（余弦相似度）"""

    name = "vector"

    def __init__(self, store: QdrantStore, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def available(self) -> bool:
        try:
            return self.store.collection_exists()
        except Exception:
            return False

    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]:
        if not self.available():
            logger.warning("[vector] Qdrant 集合不可用，返回空结果")
            return []

        query_vector = self.embedder.embed_one(query)
        hits = self.store.search(query_vector, top_k=top_k)

        results = []
        for hit in hits:
            # QdrantStore.search 返回 dict：{"id", "score", "payload"}
            payload = hit.get("payload") or {}
            score = hit.get("score", 0.0)
            results.append(RetrievalResult(
                text=payload.get("text", ""),
                score=score,
                source=self.name,
                doc_id=payload.get("doc_id", ""),
                section=payload.get("section", ""),
                extra={
                    "chunk_index": payload.get("chunk_index"),
                    "char_count": payload.get("char_count", 0),
                },
            ))
        return results
