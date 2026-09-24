# -*- coding: utf-8 -*-
"""
重排（rerank）模块 —— Week1 检索四件套的最后一环

为什么需要重排？
    多路召回（向量/BM25/RRF）用的是"双塔（bi-encoder）"思路：文本各自
    先编码成向量再比对，速度快，但问题和段落没有"真正互相看过对方"，
    精度有限。
    重排用"交叉编码（cross-encoder）"：把「问题 + 候选段落」拼在一起
    送进模型做精细相关性打分，精度高得多，但每条都要单独过模型、开销大。

    工程上的标准姿势（QAnything / 业界两阶段检索）：
        召回阶段用双塔快速粗筛 Top-50；
        重排阶段用交叉编码器精排，只留 Top-10 进 LLM。

本模块两个实现 + 工厂：
    DashScopeReranker —— 真实实现：调用 DashScope gte-rerank（交叉编码）
    NoopReranker      —— 无 Key / 离线：保持输入顺序（粗排结果直接截断）

    工厂 create_reranker()：配置了 DASHSCOPE_API_KEY → DashScopeReranker，
    否则 → NoopReranker。管线代码无需感知后端差异。
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from loguru import logger

from qa import config
from qa.retrieval.base import RetrievalResult


class RerankerError(Exception):
    """重排过程异常"""
    pass


class Reranker(ABC):
    """重排器统一接口：输入候选结果，按与 query 的相关性精排后取 Top-K"""

    name: str = "base"

    @abstractmethod
    def rerank(
        self,
        query: str,
        results: List[RetrievalResult],
        top_k: int = 10,
    ) -> List[RetrievalResult]:
        raise NotImplementedError


class NoopReranker(Reranker):
    """
    空实现：不改变顺序，只截断到 top_k。

    用途：未配置 API Key 时保持管线可用（等价于"召回即最终结果"），
    配置 Key 后无需改任何调用代码即可切换到真实重排。
    """

    name = "noop"

    def rerank(self, query, results, top_k=10):
        return results[:top_k]


class DashScopeReranker(Reranker):
    """
    DashScope gte-rerank 交叉编码重排。

    - dashscope 延迟导入：没装 SDK 时创建对象不报错；
    - 结果按 relevance_score 降序，并把分数写回 result.score，
      供上层统一使用（覆盖 RRF 的排名分）。
    """

    name = "dashscope_rerank"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model = model or config.RERANK_MODEL
        self.api_key = api_key or config.DASHSCOPE_API_KEY
        if not self.api_key:
            raise RerankerError(
                "使用 DashScope 重排需要配置 DASHSCOPE_API_KEY 环境变量"
            )

    def rerank(self, query: str, results: List[RetrievalResult], top_k: int = 10):
        if not results:
            return []
        if not query.strip():
            return results[:top_k]

        from dashscope import TextReRank  # 延迟导入

        documents = [r.text for r in results]
        resp = TextReRank.call(
            model=self.model,
            query=query,
            documents=documents,
            top_n=len(documents),  # 让 API 对全部候选打分，由我们截取 top_k
        )
        if resp.status_code != 200:
            raise RerankerError(
                f"DashScope 重排请求失败: {resp.status_code} - "
                f"{getattr(resp, 'message', 'unknown error')}"
            )

        ordered = []
        # output.results 形如 [{"index": int, "relevance_score": float}, ...]（已降序）
        # 兼容旧版对象属性与新版 dict 两种返回形态
        output = resp.output
        if isinstance(output, dict):
            results_out = output.get("results", [])
        else:
            results_out = output.results
        for item in results_out:
            index = item["index"] if isinstance(item, dict) else item.index
            score = (
                item["relevance_score"]
                if isinstance(item, dict)
                else item.relevance_score
            )
            if 0 <= index < len(results):
                results[index].score = score  # 覆盖粗排分数为精排分数
                ordered.append(results[index])

        # 防御：API 漏返回某些候选时，把缺失的按原序补在后面
        if len(ordered) < len(results):
            seen = {id(r) for r in ordered}
            ordered.extend(r for r in results if id(r) not in seen)

        return ordered[:top_k]


def create_reranker() -> Reranker:
    """按配置创建重排器：有 Key → DashScope；否则 → Noop"""
    if config.DASHSCOPE_API_KEY:
        try:
            return DashScopeReranker(model=config.RERANK_MODEL)
        except RerankerError as e:
            logger.warning(f"重排器创建失败：{e}，使用 NoopReranker")
    else:
        logger.warning(
            "未配置 DASHSCOPE_API_KEY，使用 NoopReranker（召回顺序即最终顺序）；"
            "配置 Key 后自动启用 gte-rerank 精排"
        )
    return NoopReranker()
