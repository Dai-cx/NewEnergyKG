#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qdrant 向量库封装

职责：
    ensure_collection()  集合不存在则创建（维度/距离度量一致）
    upsert_chunks()      把 chunk + 向量写入集合（幂等：同文档同序号覆盖写）
    search()             向量检索 Top-K（可附带来源过滤）
    count() / delete_collection()

设计要点：
1. **point id 确定性**：id = uuid5(doc_id:chunk_index)，同一文档重复导入时
   覆盖旧点而不是无限堆积 —— 让"重新摄取文档"幂等。
2. **兼容新旧 qdrant-client**：v1.19 移除了 .search()，改用 .query_points()；
   这里做能力探测，两个版本都能跑。
3. **本地可测**：构造时允许注入 QdrantClient(":memory:")，测试不依赖服务器。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from qa import config


class QdrantStore:
    """Qdrant 集合操作的轻量封装"""

    def __init__(
        self,
        url: Optional[str] = None,
        collection: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        """
        Args:
            url: Qdrant 服务地址，默认读 config.QDRANT_URL。
            collection: 集合名，默认读 config.QDRANT_COLLECTION。
            client: 注入现成 QdrantClient（测试时传 QdrantClient(":memory:")）。
        """
        from qdrant_client import QdrantClient
        from qdrant_client.http import models

        self.models = models
        self.collection_name = collection or config.QDRANT_COLLECTION
        self.client = client or QdrantClient(url=url or config.QDRANT_URL)

    # ==================== 集合管理 ====================

    def ensure_collection(self, dimension: int, recreate: bool = False) -> None:
        """
        确保集合存在（使用余弦距离）。recreate=True 时删除重建（清空数据）。
        """
        exists = self.client.collection_exists(self.collection_name)
        if exists and recreate:
            logger.warning(f"[qdrant] 删除并重建集合 {self.collection_name}")
            self.client.delete_collection(self.collection_name)
            exists = False

        if not exists:
            logger.info(
                f"[qdrant] 创建集合 {self.collection_name}（维度 {dimension}，余弦距离）"
            )
            self.client.create_collection(
                self.collection_name,
                vectors_config=self.models.VectorParams(
                    size=dimension,
                    distance=self.models.Distance.COSINE,
                ),
            )

    def collection_exists(self) -> bool:
        return self.client.collection_exists(self.collection_name)

    def count(self) -> int:
        """当前集合内的向量条数"""
        return self.client.count(self.collection_name).count

    def list_doc_ids(self, limit: int = 100000) -> set:
        """
        拉取集合内全部已入库文档的 doc_id（内容哈希）集合。
        用途：增量摄取时判断"哪些文件已处理过"。

        集合不存在时返回空集（此时任何文件都算"待处理"）。
        """
        if not self.collection_exists():
            return set()
        doc_ids: set = set()
        offset = None
        while len(doc_ids) < limit:
            points, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=min(1000, limit),
                offset=offset,
                with_payload=True,
            )
            for p in points:
                doc_id = (p.payload or {}).get("doc_id")
                if doc_id:
                    doc_ids.add(doc_id)
            if next_offset is None or not points:
                break
            offset = next_offset
        return doc_ids

    def fetch_all(self, limit: int = 10000) -> List[dict]:
        """
        分页拉取集合内全部点的 payload（不带向量）。
        用途：把整个知识库的文本装进内存，供 BM25 等全量检索器建索引。

        Returns:
            payload dict 列表（每项含 text/source/section/doc_id/chunk_index/char_count）
        """
        results: List[dict] = []
        offset = None
        while len(results) < limit:
            points, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=min(1000, limit - len(results)),
                offset=offset,
                with_payload=True,
            )
            results.extend(p.payload for p in points)
            if next_offset is None or not points:
                break
            offset = next_offset
        return results[:limit]

    def delete_collection(self) -> None:
        if self.collection_exists():
            self.client.delete_collection(self.collection_name)

    # ==================== 写入 ====================

    @staticmethod
    def _point_id(doc_id: str, chunk_index: int) -> str:
        """确定性 point id：同一 (doc_id, chunk_index) 映射到同一 UUID"""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{chunk_index}"))

    def upsert_chunks(self, chunks: List[dict], vectors: List[List[float]]) -> int:
        """
        把 chunk 列表与对应向量写入集合。

        Args:
            chunks: chunker 输出的 chunk（含 text 与 metadata）。
            vectors: 与 chunks 等长的向量列表。

        Returns:
            本次写入的条数。
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks({len(chunks)}) 与 vectors({len(vectors)}) 数量不一致"
            )
        if not chunks:
            return 0

        points = []
        for chunk, vector in zip(chunks, vectors):
            meta = chunk["metadata"]
            points.append(self.models.PointStruct(
                id=self._point_id(meta["doc_id"], meta["chunk_index"]),
                vector=vector,
                payload={
                    "text": chunk["text"],
                    "source": meta.get("source", ""),
                    "section": meta.get("section", ""),
                    "doc_id": meta.get("doc_id", ""),
                    "chunk_index": meta.get("chunk_index", -1),
                    "char_count": meta.get("char_count", 0),
                },
            ))

        self.client.upsert(self.collection_name, points=points)
        logger.info(f"[qdrant] 已写入 {len(points)} 个向量 → {self.collection_name}")
        return len(points)

    # ==================== 检索 ====================

    def _build_filter(self, source: Optional[str] = None, doc_id: Optional[str] = None):
        """按来源/文档过滤（可选）"""
        if not source and not doc_id:
            return None
        must = []
        if source:
            must.append(self.models.FieldCondition(
                key="source",
                match=self.models.MatchValue(value=source),
            ))
        if doc_id:
            must.append(self.models.FieldCondition(
                key="doc_id",
                match=self.models.MatchValue(value=doc_id),
            ))
        return self.models.Filter(must=must)

    def search(
        self,
        vector: List[float],
        top_k: int = 10,
        score_threshold: Optional[float] = None,
        source: Optional[str] = None,
        doc_id: Optional[str] = None,
    ) -> List[dict]:
        """
        向量检索，返回按相似度降序的命中文档列表：
            [{"id", "score", "payload"}]
        """
        query_filter = self._build_filter(source=source, doc_id=doc_id)
        kwargs = dict(
            collection_name=self.collection_name,
            limit=top_k,
            query_filter=query_filter,
            score_threshold=score_threshold,
        )

        # qdrant-client >= 1.10 推荐 query_points；旧版本回退 search
        if hasattr(self.client, "query_points"):
            resp = self.client.query_points(**kwargs, query=vector)
            hits = resp.points
        else:  # pragma: no cover —— 旧版本兼容分支
            resp = self.client.search(**kwargs, query_vector=vector)
            hits = resp

        results = []
        for hit in hits:
            results.append({
                "id": hit.id,
                "score": hit.score,
                "payload": hit.payload,
            })
        return results

    def close(self):
        """释放连接（内存模式无操作）"""
        try:
            self.client.close()
        except Exception:
            pass
