# -*- coding: utf-8 -*-
"""
BM25 全文检索（关键词检索）

为什么混合检索里需要 BM25？
    向量检索擅长"语义相近但用词不同"（如"电池龙头"≈"宁德时代"）；
    关键词检索擅长"精确术语/缩写/数字"（如 "PERC"、"2024"、"全钒液流电池"）。
    两者互补，融合后整体召回更稳 —— 这是 RAG 工程里"多路召回"的标准做法。

本模块实现了经典的 Okapi BM25：
    score(d, q) = Σ_{qi∈q} IDF(qi) × f(qi,d)×(k1+1) / (f(qi,d) + k1×(1-b+b×|d|/avgdl))

    IDF(qi) = ln(1 + (N - n(qi) + 0.5) / (n(qi) + 0.5))
    其中：f 词频，|d| 文档长度（token 数），avgdl 平均文档长度，
          k1≈1.5 控制词频饱和，b≈0.75 控制文档长度惩罚。
"""

import math
from typing import List

from loguru import logger

from qa.retrieval.base import RetrievalResult, Retriever

# BM25 超参数（业界常用默认值）
K1 = 1.5
B = 0.75


def tokenize(text: str) -> List[str]:
    """
    中文分词：优先用 jieba 搜索引擎模式（能切出更细的 token，利于召回）；
    未安装 jieba 时退化为按字符切分（中文场景仍可用）。
    """
    try:
        import jieba
        tokens = list(jieba.cut_for_search(text))
    except ImportError:  # pragma: no cover
        tokens = list(text)

    cleaned = []
    for tok in tokens:
        tok = tok.strip().lower()
        if tok and not tok.isspace():
            cleaned.append(tok)
    return cleaned


class BM25Index:
    """离线 BM25 索引：构建时统计词频/文档频率，检索时打分"""

    def __init__(self, documents: List[str], k1: float = K1, b: float = B):
        """
        Args:
            documents: 每篇文档的原始文本（与结果一一对应）。
        """
        self.k1 = k1
        self.b = b
        self.doc_tokens: List[List[str]] = [tokenize(doc) for doc in documents]

        self.doc_count = len(self.doc_tokens)
        self.doc_lengths = [len(toks) for toks in self.doc_tokens]
        self.avgdl = (sum(self.doc_lengths) / self.doc_count) if self.doc_count else 0.0

        # 文档频率 df(term)：包含该词的文档数
        self.df: dict = {}
        for toks in self.doc_tokens:
            for term in set(toks):
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term: str) -> float:
        """逆文档频率：越罕见的词权重越高（对热门词降权）"""
        n = self.df.get(term, 0)
        if n == 0:
            return 0.0
        return math.log(1 + (self.doc_count - n + 0.5) / (n + 0.5))

    def _score_doc(self, doc_index: int, query_terms: List[str]) -> float:
        """计算单篇文档对查询的打分"""
        if self.doc_lengths[doc_index] == 0:
            return 0.0
        term_freq: dict = {}
        for term in self.doc_tokens[doc_index]:
            term_freq[term] = term_freq.get(term, 0) + 1

        score = 0.0
        dl = self.doc_lengths[doc_index]
        for term in query_terms:
            tf = term_freq.get(term, 0)
            if tf == 0:
                continue
            # 词频饱和：文档里出现 5 次和出现 20 次的差距被压缩
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += self.idf(term) * numerator / denominator
        return score

    def search(self, query: str, top_k: int = 10) -> List[tuple]:
        """
        返回 [(doc_index, score), ...] 按分数降序。
        分数为 0（无公共词）的文档不返回。
        """
        query_terms = tokenize(query)
        if not query_terms or self.doc_count == 0:
            return []

        scored = []
        for i in range(self.doc_count):
            s = self._score_doc(i, query_terms)
            if s > 0:
                scored.append((i, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def normalize_document(doc: dict) -> dict:
    """
    兼容两种输入形态（chunker 输出 / Qdrant payload）：
        {"text": ..., "metadata": {...}}
        {"text": ..., "source": ..., "doc_id": ..., "section": ..., "chunk_index": ...}
    """
    meta = doc.get("metadata") or {}
    return {
        "text": doc["text"],
        "doc_id": meta.get("doc_id") or doc.get("doc_id", ""),
        "source": meta.get("source") or doc.get("source", ""),
        "section": meta.get("section") or doc.get("section", ""),
        "chunk_index": meta.get("chunk_index", doc.get("chunk_index")),
    }


class BM25Retriever(Retriever):
    """把 BM25Index 包装成统一 Retriever 接口"""

    name = "bm25"

    def __init__(self, documents: List[dict]):
        """
        Args:
            documents: chunk dict 列表（含 text 与来源信息），
                       典型来源是 QdrantStore.fetch_all() 的结果。
        """
        self.documents = [normalize_document(d) for d in documents]
        texts = [d["text"] for d in self.documents]
        self.index = BM25Index(texts)
        logger.info(f"[bm25] 索引建立完成：{len(texts)} 篇文档")

    def available(self) -> bool:
        return self.index.doc_count > 0

    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]:
        results = []
        for doc_index, score in self.index.search(query, top_k=top_k):
            doc = self.documents[doc_index]
            results.append(RetrievalResult(
                text=doc["text"],
                score=score,
                source=self.name,
                doc_id=doc["doc_id"],
                section=doc["section"],
                extra={"chunk_index": doc["chunk_index"]},
            ))
        return results
