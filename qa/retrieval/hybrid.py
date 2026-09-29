# -*- coding: utf-8 -*-
"""
混合检索器：多路召回 + RRF 融合

思路（业界标准做法，QAnything / LangChain 混合检索同款思路）：
1. 每一路检索器各自返回 Top-K（本实现按 top_k * 3 召回，留足融合空间）；
2. 用 RRF（Reciprocal Rank Fusion，倒数排名融合）把多路排名合并：
       RRF_score(d) = Σ_{r} w_r / (k + rank_r(d))
   其中 rank_r(d) 是文档 d 在第 r 路的排名（从 1 开始），k 是平滑常数（默认 60）。

为什么用"排名"而不是"分数"融合？
    各路检索器的分数量纲完全不同（余弦相似度 ∈ [-1,1]，
    BM25 分数可能上万，KG 命中是自定义分数），直接相加没有意义；
    而"排名"是相对量，天然可比 —— 这是 RRF 的核心思想。
"""

from typing import List, Optional

from loguru import logger

from qa import config
from qa.retrieval.base import RetrievalResult, Retriever, result_key
from qa.retrieval.reranker import Reranker

# RRF 平滑常数（业界常用默认值）
RRF_K = 60

# 重排候选池默认值：取自配置（重排按 token 计费，费用 ∝ 候选数 × chunk 长度）。
# 集中到 config 便于一处调优/控成本，见 qa/config.py::RERANK_CANDIDATES。
RERANK_CANDIDATES_DEFAULT = config.RERANK_CANDIDATES


def rrf_fuse(
    result_lists: List[List[RetrievalResult]],
    weights: Optional[List[float]] = None,
    k: int = RRF_K,
) -> List[RetrievalResult]:
    """
    把多路检索结果按 RRF 融合为一条按分数降序的列表（已去重）。

    Args:
        result_lists: 每路检索器的结果列表（每路内部已按相关性排序）。
        weights: 可选，每路的权重（默认等权 1.0）。
        k: RRF 平滑常数。
    """
    if not result_lists:
        return []
    if weights is None:
        weights = [1.0] * len(result_lists)
    if len(weights) != len(result_lists):
        raise ValueError("weights 长度必须与 result_lists 一致")

    score_map: dict = {}       # key -> RRF 总分
    first_rank: dict = {}      # key -> 首次出现顺序（稳定排序用）
    result_map: dict = {}      # key -> 第一个见到的结果对象
    order_counter = 0

    def _remember_sources(result, source: str) -> None:
        """记录一条结果被哪些检索来源命中（用于溯源与展示）"""
        sources = result.extra.setdefault("sources", [])
        if source not in sources:
            sources.append(source)

    for rank_list, weight in zip(result_lists, weights):
        for rank, result in enumerate(rank_list, start=1):
            key = result_key(result)
            score_map[key] = score_map.get(key, 0.0) + weight / (k + rank)
            if key not in result_map:
                result_map[key] = result
                _remember_sources(result, result.source)
                first_rank[key] = order_counter
                order_counter += 1
            else:
                # 同一份知识被另一路检索命中：累计来源，不新增重复条目
                _remember_sources(result_map[key], result.source)

    # 按 (RRF 分降序, 首次出现序升序) 排序
    ordered_keys = sorted(
        score_map.keys(),
        key=lambda key: (-score_map[key], first_rank[key]),
    )

    fused = []
    for key in ordered_keys:
        result = result_map[key]
        result.score = score_map[key]  # 用 RRF 分覆盖原分数，统一量纲
        fused.append(result)
    return fused


class HybridRetriever(Retriever):
    """把多路 Retriever 组合成一个混合检索器（可选的末尾重排）"""

    name = "hybrid"

    def __init__(
        self,
        retrievers: List[Retriever],
        weights: Optional[List[float]] = None,
        fusion_k: int = RRF_K,
        reranker: Optional[Reranker] = None,
        rerank_candidates: int = RERANK_CANDIDATES_DEFAULT,
    ):
        """
        Args:
            retrievers: 参与融合的检索器列表（建议顺序：kg, vector, bm25）。
            weights: 与 retrievers 等长的可选权重。
            fusion_k: RRF 平滑常数。
            reranker: 可选重排器；提供时对融合结果做交叉编码精排
                      （两阶段检索：RRF 粗排 Top-N → rerank 精排 Top-K）。
            rerank_candidates: 送入重排器的候选条数上限（粗排池子）。
        """
        self.retrievers = retrievers
        self.weights = weights
        self.fusion_k = fusion_k
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates

    def available(self) -> bool:
        return any(r.available() for r in self.retrievers)

    def list_sources(self) -> List[str]:
        return [r.name for r in self.retrievers]

    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]:
        # 每路召回多少候选，取决于是否还有重排这一步：
        # - 无重排：召回 top_k*3 足够，RRF 后直接截断；
        # - 有重排：召回必须覆盖 rerank_candidates 的池子（如 50 条），
        #   否则跨语言/低相似度但相关的 chunk 可能在重排前就被截掉了
        #   （"召回池太浅，重排器救不回来"是两阶段检索的经典坑）。
        if self.reranker is not None:
            recall_k = max(top_k * 3, self.rerank_candidates)
        else:
            recall_k = top_k * 3

        per_route = []
        for retriever in self.retrievers:
            try:
                if retriever.available():
                    per_route.append(retriever.retrieve(query, top_k=recall_k))
                else:
                    logger.debug(f"[hybrid] {retriever.name} 不可用，跳过")
            except Exception as e:
                logger.warning(f"[hybrid] {retriever.name} 检索失败：{e}")
                per_route.append([])

        fused = rrf_fuse(per_route, weights=self.weights, k=self.fusion_k)
        logger.debug(
            f"[hybrid] 各路结果数：{[len(r) for r in per_route]} → 融合 {len(fused)} 条"
        )

        # 两阶段检索第二步：重排（若配置）——粗排候选 → 交叉编码精排
        if self.reranker is not None and fused:
            candidates = fused[: self.rerank_candidates]
            ranked = self.reranker.rerank(query, candidates, top_k=top_k)
            logger.debug(
                f"[hybrid] rerank({self.reranker.name}): "
                f"{len(candidates)} 候选 → {len(ranked)} 精排结果"
            )
            return ranked

        return fused[:top_k]
