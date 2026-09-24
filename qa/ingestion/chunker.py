#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义切分模块（chunker）

输入：parser 产生的 Block 列表（标题 + 段落）
输出：chunk 列表，每个 chunk 是：
    {
        "text": "...",                    # 切片文本（供 Embedding 与检索展示）
        "metadata": {
            "doc_id":  "...",             # 文档内容哈希（重导入同一文档可幂等覆盖）
            "source":  "文件名",           # 来源文件名
            "section": "一级 / 二级",       # 标题路径，回答时可作引用
            "chunk_index": 0,             # 文档内切块序号（可用于溯源）
            "char_count": 123,            # 字符数
        }
    }

切分策略（标题感知 + 贪心累积）：
1. 先把段落归入"最近的标题"名下，形成若干 section；
2. 每个 section 内按目标长度贪心合并段落；
3. 一个 section 放不下时，切出的相邻 chunk 保留 overlap 长度的尾部文本，
   避免"一句话被拦腰截断"造成的语义断裂；
4. 超长单段（> chunk_size）按固定窗口切割，同样带重叠。

为什么"按标题切"而不是"按固定字数切"？
    检索时用户问题通常命中某个主题段落，按标题组织能让一个 chunk 尽量
    语义完整（一个主题一个块），比把两个无关主题硬拼进 600 字更利于召回。
"""

from typing import List

from loguru import logger

from qa.ingestion.parser import Block


# ==================== 标题路径 ====================


def _update_heading_stack(stack: List[tuple], level: int, title: str) -> None:
    """
    维护标题栈：
    - 新标题层级 <= 栈顶层级时，说明进入了"兄弟/父级"标题，弹出栈顶；
    - 然后压入新标题。
    例：栈 [(1, 光伏), (2, 组件)]，遇到新的 (2, 电池片) → 弹出 (2, 组件)，
        栈变为 [(1, 光伏), (2, 电池片)]。
    """
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def _heading_path(stack: List[tuple]) -> str:
    """把标题栈拼成路径字符串，如 "光伏 / 组件 / 电池片" """
    return " / ".join(title for _, title in stack)


# ==================== 块级辅助 ====================


def _tail(text: str, overlap: int) -> str:
    """取文本尾部 overlap 个字符作为下一块的"记忆前缀" """
    if overlap <= 0 or len(text) <= overlap:
        return ""
    return text[-overlap:]


def _split_long_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    超长单段的固定窗口切分（带重叠）。
    start 每次前进 chunk_size - overlap，保证上下文衔接。
    """
    parts: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        parts.append(text[start:end])
        if end == n:
            break
        start = end - overlap
    return parts


def _greedy_chunk_paragraphs(paragraphs: List[str], chunk_size: int, overlap: int) -> List[str]:
    """
    贪心合并段落：尽可能把相邻段落拼进一个 chunk，满了就输出并携带 overlap 前缀。

    返回切好的文本列表（不含 metadata，由上层补充）。
    """
    chunks: List[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer.strip():
            chunks.append(buffer)
        buffer = ""

    for para in paragraphs:
        # 超长单段：先清空缓冲区，再独立切分
        if len(para) >= chunk_size:
            flush()
            chunks.extend(_split_long_text(para, chunk_size, overlap))
            continue

        # 首个段落直接放入缓冲区
        if not buffer:
            buffer = para
            continue

        # 还能装下，就拼进去（段落间用换行分隔）
        if len(buffer) + 1 + len(para) <= chunk_size:
            buffer = buffer + "\n" + para
            continue

        # 装不下了：输出当前块，并用其尾部作新块的 overlap 前缀
        tail = _tail(buffer, overlap)
        chunks.append(buffer)
        buffer = (tail + "\n" + para) if tail else para

    if buffer.strip():
        chunks.append(buffer)
    return chunks


# ==================== 核心切分 ====================


def chunk_blocks(
    blocks: List[Block],
    source: str = "",
    doc_id: str = "",
    chunk_size: int = 600,
    overlap: int = 80,
) -> List[dict]:
    """
    把 Block 列表切分成带 metadata 的 chunk 列表。

    Args:
        blocks: parser 输出的块列表。
        source: 来源文件名（写入 metadata）。
        doc_id: 文档唯一 ID（内容哈希），用于幂等入库。
        chunk_size: 目标块大小（字符数）。
        overlap: 相邻块重叠长度（字符数）。

    Returns:
        chunk dict 列表，见模块 docstring。
    """
    # ---- 第一步：把段落归入最近的标题，形成 section ----
    # sections: List[(section_path, [paragraph,...])]
    sections: List[tuple] = []
    stack: List[tuple] = []
    current_path = ""
    current_paras: List[str] = []

    def flush_section() -> None:
        nonlocal current_path, current_paras
        if current_paras:
            sections.append((current_path, current_paras))
        current_paras = []

    for block in blocks:
        if block.type == "heading":
            flush_section()
            _update_heading_stack(stack, block.level, block.text)
            current_path = _heading_path(stack)
        else:
            # 文档开头、任何标题之前的内容归入空路径
            current_paras.append(block.text)
    flush_section()

    # ---- 第二步：对每个 section 贪心切分 ----
    chunk_texts: List[str] = []          # 全局顺序，保证 chunk_index 连续
    chunk_sections: List[str] = []

    for path, paras in sections:
        for text in _greedy_chunk_paragraphs(paras, chunk_size, overlap):
            chunk_texts.append(text)
            chunk_sections.append(path)

    # ---- 第三步：组装 metadata ----
    chunks: List[dict] = []
    for index, text in enumerate(chunk_texts):
        chunks.append({
            "text": text,
            "metadata": {
                "doc_id": doc_id,
                "source": source,
                "section": chunk_sections[index],
                "chunk_index": index,
                "char_count": len(text),
            },
        })

    logger.debug(f"[chunker] {source}: {len(blocks)} 块 → {len(chunks)} 个 chunk")
    return chunks


def chunk_markdown(
    text: str,
    source: str = "",
    doc_id: str = "",
    chunk_size: int = 600,
    overlap: int = 80,
) -> List[dict]:
    """便捷函数：Markdown 文本 → chunk 列表（解析 + 切分一步到位）"""
    from qa.ingestion.parser import parse_markdown

    blocks = parse_markdown(text)
    return chunk_blocks(blocks, source=source, doc_id=doc_id,
                        chunk_size=chunk_size, overlap=overlap)


# ==================== 统计 ====================


def summarize_chunks(chunks: List[dict]) -> dict:
    """chunk 整体统计，供 CLI 展示"""
    if not chunks:
        return {"count": 0, "total_chars": 0, "avg_chars": 0}
    total = sum(len(c["text"]) for c in chunks)
    return {"count": len(chunks), "total_chars": total, "avg_chars": int(total / len(chunks))}
