#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本向量化模块（embedder）

两个实现 + 一个工厂：
    DashScopeEmbedder  —— 调用阿里云 DashScope text-embedding-v3 API（真实使用）
    DebugHashEmbedder  —— 本地确定性伪向量（无 Key / 离线测试时兜底，向量可复现）

为什么需要 Debug 实现？
    - 单元测试与 CI 不能依赖外部 API；
    - 未配置 Key 时也能把摄取→入库→检索整条链路跑通（向量质量不高，仅验证流程）。
    Debug 向量是"文本特征哈希 + L2 归一化"，同样的文本永远得到同样的向量，
    因此相似文本在余弦距离上仍有一定区分度，足以做流程验证。

工厂 create_embedder() 决策：
    配置了 DASHSCOPE_API_KEY → DashScopeEmbedder
    否则 → DebugHashEmbedder（并给出 warning 日志）
"""

import hashlib
import math
from typing import List, Optional

from loguru import logger

from qa import config


class EmbedderError(Exception):
    """向量化过程异常"""
    pass


class Embedder:
    """所有 Embedder 的公共接口"""

    provider: str = "base"

    def embed(self, texts: List[str]) -> List[List[float]]:
        """批量把文本转成向量，返回与输入等长的 list"""
        raise NotImplementedError

    def embed_one(self, text: str) -> List[float]:
        """把单个文本转成向量"""
        return self.embed([text])[0]


# ==================== Debug：本地确定性伪向量 ====================

class DebugHashEmbedder(Embedder):
    """
    离线调试用 Embedder：用文本特征哈希生成固定维度的归一化向量。

    说明：仅供无 API Key 时验证流程/跑测试使用，检索质量远不如真实模型，
    生产环境请使用 DashScopeEmbedder。
    """

    provider = "debug_hash"

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def _hash_features(self, text: str):
        """
        提取文本的 n-gram 特征（字级别 1~2 gram），用哈希桶累积权重。
        这样语义上有重叠字符的两段文本会得到更接近的向量。
        """
        buckets = [0.0] * self.dimension
        cleaned = text.strip()
        for size in (1, 2):
            for i in range(len(cleaned) - size + 1):
                gram = cleaned[i:i + size]
                digest = hashlib.md5(gram.encode("utf-8")).digest()
                # 取前 8 字节做整数，映射到 [0, dimension) 桶
                bucket = int.from_bytes(digest[:8], "little") % self.dimension
                buckets[bucket] += 1.0
        return buckets

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            vec = self._hash_features(text)
            # L2 归一化：让向量长度=1，使余弦相似度等价于点积，便于统一检索口径
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


# ==================== 真实实现：DashScope ====================

class DashScopeEmbedder(Embedder):
    """
    阿里云 DashScope text-embedding 模型调用封装。

    - import dashscope 推迟到 embed() 内部：未安装 SDK 时，
      创建对象不报错，真正向量化时才报错，便于分层测试。
    - 模型默认 text-embedding-v3（1024 维）。
    """

    provider = "dashscope"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        batch_size: int = 10,
        timeout: int = 30,
    ):
        self.model = model or config.EMBEDDING_MODEL
        self.api_key = api_key or config.DASHSCOPE_API_KEY
        self.batch_size = batch_size or config.EMBED_BATCH_SIZE
        self.timeout = timeout

        if not self.api_key:
            raise EmbedderError(
                "使用 DashScope Embedding 需要配置 DASHSCOPE_API_KEY 环境变量"
            )

    @staticmethod
    def _extract_vector(embedding_item) -> List[float]:
        """兼容 dashscope 返回的 dict / 对象两种形态"""
        if isinstance(embedding_item, dict):
            return embedding_item["embedding"]
        return embedding_item.embedding

    def _call_batch(self, batch: List[str]) -> List[List[float]]:
        from dashscope import TextEmbedding  # 延迟导入

        resp = TextEmbedding.call(
            model=self.model,
            input=batch,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise EmbedderError(
                f"DashScope Embedding 请求失败: {resp.status_code} - "
                f"{getattr(resp, 'message', 'unknown error')}"
            )

        # 兼容两种 SDK 返回形态：
        #   旧版：resp.output.embeddings（对象属性）
        #   新版：resp.output 是 dict {"embeddings": [...]}
        output = resp.output
        if isinstance(output, dict):
            embeddings = output.get("embeddings", [])
        else:
            embeddings = output.embeddings

        # 返回顺序与输入一致（SDK 会按 text_index 排序返回）
        vectors = [self._extract_vector(item) for item in embeddings]
        if len(vectors) != len(batch):
            raise EmbedderError(
                f"Embedding 返回数量({len(vectors)})与请求数量({len(batch)})不一致"
            )
        return vectors

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            vectors.extend(self._call_batch(batch))
            logger.debug(f"[dashscope-embedder] 已向量化 {len(vectors)}/{len(texts)}")
        return vectors


# ==================== 工厂 ====================

def create_embedder() -> Embedder:
    """
    根据配置创建 Embedder：
    - 配置了 DASHSCOPE_API_KEY → DashScopeEmbedder
    - 否则 → DebugHashEmbedder（warning 提示仅用于离线验证）
    """
    if config.DASHSCOPE_API_KEY:
        return DashScopeEmbedder(model=config.EMBEDDING_MODEL)
    logger.warning(
        "未配置 DASHSCOPE_API_KEY，使用 DebugHashEmbedder（确定性伪向量），"
        "仅可用于流程验证/测试，检索质量不代表真实水平"
    )
    return DebugHashEmbedder(dimension=config.EMBEDDING_DIMENSION)
