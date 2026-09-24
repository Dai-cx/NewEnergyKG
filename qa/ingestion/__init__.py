#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa.ingestion —— 文档摄取管道（RAG 的"知识生产"环节）

流程：文档 → 解析(parser) → 语义切分(chunker) → 向量化(embedder) → 入库(qdrant_store)

模块划分：
    parser.py       把 PDF / Markdown / 纯文本解析为"标题块 + 段落块"
    chunker.py      按标题层级把内容组织成带上下文的切片（chunk）
    embedder.py     文本向量化（DashScope API / 离线 Debug 两种实现）
    qdrant_store.py Qdrant 向量库的建集 / 写入 / 检索封装
    run.py          命令行入口：python -m qa.ingestion.run --input docs/
"""

from qa.ingestion.chunker import chunk_blocks, chunk_markdown
from qa.ingestion.embedder import DebugHashEmbedder, DashScopeEmbedder, create_embedder
from qa.ingestion.parser import Block, parse_file
from qa.ingestion.qdrant_store import QdrantStore

__all__ = [
    "Block",
    "parse_file",
    "chunk_blocks",
    "chunk_markdown",
    "DebugHashEmbedder",
    "DashScopeEmbedder",
    "create_embedder",
    "QdrantStore",
]
