#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档摄取命令行入口

用法：
    # 摄取单个文件
    python -m qa.ingestion.run --input docs/sample_new_energy.md

    # 摄取整个目录（支持 md/txt/pdf）
    python -m qa.ingestion.run --input docs/

    # 覆盖指定集合 / 重建集合
    python -m qa.ingestion.run --input docs/ --collection my_docs --recreate

流程：parse → chunk → embed（分批）→ upsert（幂等覆盖）
依赖：Qdrant 服务已启动（本地 localhost:6333 或 docker compose up -d）
"""

import argparse
import hashlib
import sys
from pathlib import Path
from typing import List, Optional

from loguru import logger

from qa import config
from qa.ingestion import parser as parser_mod
from qa.ingestion.chunker import chunk_blocks, summarize_chunks
from qa.ingestion.embedder import DebugHashEmbedder, create_embedder
from qa.ingestion.qdrant_store import QdrantStore

# 支持的文档后缀
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".pdf")


def compute_doc_id(path: Path) -> str:
    """用文件内容 SHA-256 生成 doc_id：内容变了 id 才变，重复导入可幂等覆盖"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_files(input_path: Path, limit: Optional[int] = None) -> List[Path]:
    """收集待摄取文件：输入是文件则返回它，是目录则递归收集支持的后缀"""
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = [
            p for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        ]
        files.sort(key=lambda p: str(p))
    else:
        raise FileNotFoundError(f"输入路径不存在: {input_path}")

    if limit:
        files = files[:limit]
    return files


def ingest_path(
    path: Path,
    store: QdrantStore,
    embedder,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    doc_id: Optional[str] = None,
) -> dict:
    """
    摄取单个文档：解析 → 切分 → 向量化 → 入库。

    Args:
        doc_id: 文档内容哈希；外部已算好时传入可避免重复读文件。

    Returns:
        {"file", "blocks", "chunks", "written", "chunk_summary"}
    """
    chunk_size = chunk_size or config.CHUNK_SIZE
    overlap = overlap or config.CHUNK_OVERLAP

    # 1. 解析
    blocks = parser_mod.parse_file(path)
    stats = parser_mod.count_blocks(blocks)
    logger.info(f"[ingest] 解析完成 {path.name}: {stats}")

    # 2. 切分（doc_id 由内容哈希决定）
    doc_id = doc_id or compute_doc_id(path)
    chunks = chunk_blocks(
        blocks,
        source=path.name,
        doc_id=doc_id,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    summary = summarize_chunks(chunks)
    logger.info(f"[ingest] 切分完成 {path.name}: {summary}")

    if not chunks:
        return {
            "file": str(path), "blocks": stats["total"], "chunks": 0,
            "written": 0, "chunk_summary": summary,
        }

    # 3. 向量化（分批，避免一次塞太多文本给 API）
    texts = [c["text"] for c in chunks]
    vectors = embedder.embed(texts)

    # 4. 入库前确保集合存在（维度以实际向量为准）
    dimension = len(vectors[0])
    store.ensure_collection(dimension=dimension)

    # 5. 幂等写入（同 doc_id+chunk_index 覆盖旧点）
    written = store.upsert_chunks(chunks, vectors)

    return {
        "file": str(path),
        "blocks": stats["total"],
        "chunks": len(chunks),
        "written": written,
        "chunk_summary": summary,
    }


def classify_documents(
    files: List[Path],
    existing_doc_ids: set,
) -> tuple:
    """
    增量摄取的核心：按内容哈希把文件分为"待处理"与"已存在"两组。

    Args:
        files: 目录里扫描到的全部文件。
        existing_doc_ids: 向量库中已存在的 doc_id 集合（内容哈希）。

    Returns:
        (pending, skipped)
        pending: [(Path, doc_id), ...] 需要真正解析/向量化的文件
        skipped: [(Path, doc_id), ...] 内容未变、无需重复处理的文件
    """
    pending: List[tuple] = []
    skipped: List[tuple] = []
    for path in files:
        doc_id = compute_doc_id(path)
        if doc_id in existing_doc_ids:
            skipped.append((path, doc_id))
        else:
            pending.append((path, doc_id))
    return pending, skipped


def main(argv: Optional[List[str]] = None) -> int:
    argp = argparse.ArgumentParser(
        description="NewEnergyKG 文档摄取：把 Markdown/PDF/TXT 写入 Qdrant 向量库"
    )
    argp.add_argument("--input", required=True,
                      help="待摄取的文档文件或目录")
    argp.add_argument("--collection", default=None,
                      help=f"Qdrant 集合名（默认 {config.QDRANT_COLLECTION}）")
    argp.add_argument("--recreate", action="store_true",
                      help="重建集合（清空旧数据后全量重新摄取）")
    argp.add_argument("--force", action="store_true",
                      help="忽略增量去重，强制重新处理所有文件")
    argp.add_argument("--limit", type=int, default=None,
                      help="最多摄取前 N 个文件（用于试跑）")
    argp.add_argument("--chunk-size", type=int, default=None,
                      help=f"切块字符数（默认 {config.CHUNK_SIZE}）")
    argp.add_argument("--chunk-overlap", type=int, default=None,
                      help=f"相邻块重叠字符数（默认 {config.CHUNK_OVERLAP}）")
    args = argp.parse_args(argv)

    input_path = Path(args.input)
    files = collect_files(input_path, limit=args.limit)
    if not files:
        logger.error(f"没有找到可摄取的文档（支持后缀 {SUPPORTED_SUFFIXES}）")
        return 1

    logger.info(f"共发现 {len(files)} 个文档，目标集合："
                f"{args.collection or config.QDRANT_COLLECTION}")

    # 连接 Qdrant（docker compose 环境会自动使用 http://qdrant:6333）
    store = QdrantStore(collection=args.collection)
    embedder = create_embedder()

    # ---- 增量去重：默认只处理"内容没入库过"的新文档 ----
    if args.recreate:
        logger.warning("--recreate：清空并重建集合，将全量摄取")
        store.delete_collection()
        existing_doc_ids: set = set()
    elif args.force:
        logger.warning("--force：强制重新处理全部文件")
        existing_doc_ids = set()
    else:
        try:
            existing_doc_ids = store.list_doc_ids()
            logger.info(f"向量库已有 {len(existing_doc_ids)} 份文档，"
                        "内容未变的文件将自动跳过")
        except Exception as e:
            logger.warning(f"读取已有文档列表失败（{e}），本次全量处理")
            existing_doc_ids = set()

    pending, skipped = classify_documents(files, existing_doc_ids)
    if skipped:
        logger.info(f"跳过 {len(skipped)} 个已存在的文档（内容未变）："
                    + "、".join(str(p.name) for p, _ in skipped))

    if not pending:
        logger.info("没有需要处理的新文档，全部跳过。"
                    "（新增文件后再运行本命令；--force 可强制重处理）")
        return 0

    total_chunks = 0
    total_written = 0
    for path, doc_id in pending:
        try:
            result = ingest_path(path, store, embedder,
                                 chunk_size=args.chunk_size,
                                 overlap=args.chunk_overlap,
                                 doc_id=doc_id)
            total_chunks += result["chunks"]
            total_written += result["written"]
        except Exception as e:
            logger.exception(f"摄取失败: {path}（{e}）")
            if isinstance(embedder, DebugHashEmbedder):
                logger.error("提示：当前使用 Debug 伪向量 Embedder，仅用于离线验证")
            return 1

    logger.info(f"全部完成：新处理 {len(pending)} 个文档 → {total_chunks} 个 chunk，"
                f"写入 {total_written} 条向量，集合现有 {store.count()} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
