# -*- coding: utf-8 -*-
"""
检索结果与统一检索接口

qa/retrieval 的目标：把"多种来源的检索"收敛到同一接口上：
    KG 检索（结构化图谱） / 向量检索（语义） / BM25（关键词）

统一接口：
    class Retriever:
        name: str
        def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]

统一结果：
    RetrievalResult（text 可读文本 + score 分数 + source 来源 + 溯源字段）
    这样上层（问答引擎 / Agent）可以"无差别"消费所有来源的检索结果，
    混合检索器（hybrid.py）也才能对它们做 RRF 融合。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class RetrievalResult:
    """一条检索结果（所有来源统一的数据结构）"""

    text: str                     # 可读文本（检索展示 / 注入 Prompt 用）
    score: float = 0.0            # 该来源给出的相似度/相关性分数
    source: str = "unknown"       # kg | vector | bm25 | ...
    doc_id: str = ""              # 向量/BM25 来源的文档 ID（KG 来源为空）
    section: str = ""             # 章节路径（引用溯源用）
    extra: dict = field(default_factory=dict)  # 来源特有的附加信息

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "score": self.score,
            "source": self.source,
            "doc_id": self.doc_id,
            "section": self.section,
            "extra": self.extra,
        }


def result_key(result: RetrievalResult) -> tuple:
    """
    生成结果的"身份键"，用于混合检索去重。

    - 文档类来源（vector/bm25 指向同一个物理 chunk）：
      同一 doc_id + chunk_index 视为同一份知识，**跨来源合并**，
      这样"被多路检索同时命中"的 chunk 会因多路贡献而排名更高；
    - KG 来源：没有 doc_id，退化为 (source, text) —— 不同图谱查询
      产生的文本不同，各自保留。
    """
    if result.doc_id:
        chunk_index = result.extra.get("chunk_index")
        return ("doc", result.doc_id, chunk_index)
    return (result.source, "", result.text)


class Retriever(ABC):
    """所有检索器的统一抽象接口"""

    name: str = "base"

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]:
        """
        输入一句自然语言问题，返回按相关性降序的检索结果。
        top_k 是该来源希望返回的最大条数。
        """
        raise NotImplementedError

    def available(self) -> bool:
        """该检索器是否可用（依赖是否连接/数据是否就绪）"""
        return True
