#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档解析模块

把不同格式的文档统一成同一种中间表示：Block 列表。
Block 分两类：
    - heading  ：标题块（含层级 level，供 chunker 判断层级关系）
    - paragraph：段落块（连续的文本行，可能包含列表/表格行）

支持格式：
    .md / .markdown ：按 Markdown 标题（# ~ ######）切分结构
    .txt            ：按空行切分段落（不做标题推断，避免误判）
    .pdf            ：pypdf 逐页抽取文本，再按空行切分段落

设计要点：
    解析只负责"把文本变成干净的块"，不做切块；
    "如何把块组合成 chunk"是 chunker 的职责——职责单一，便于分别测试。
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from loguru import logger

# 匹配 Markdown 标题：# 一级、## 二级 …… ###### 六级
HEADING_RE = re.compile(r"^(\#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Block:
    """文档中的一个块：标题或段落"""

    type: str  # "heading" | "paragraph"
    text: str
    level: int = 0  # 标题层级（1-6）；段落恒为 0

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text, "level": self.level}


def _flush_paragraph(buffer: List[str], blocks: List[Block]) -> None:
    """把 buffer 中累积的行合并为一个段落块（保留换行分隔列表/表格行）"""
    lines = [line.strip() for line in buffer if line.strip()]
    if lines:
        blocks.append(Block(type="paragraph", text="\n".join(lines)))
    buffer.clear()


def _split_text_blocks(text: str, detect_headings: bool = False) -> List[Block]:
    """
    把纯文本拆成 Block 列表。

    Args:
        text: 原始文本。
        detect_headings: 是否识别 Markdown 标题行。
    """
    blocks: List[Block] = []
    buffer: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            _flush_paragraph(buffer, blocks)
            continue

        if detect_headings:
            m = HEADING_RE.match(line)
            if m:
                _flush_paragraph(buffer, blocks)
                level = len(m.group(1))
                title = m.group(2).strip()
                blocks.append(Block(type="heading", text=title, level=level))
                continue

        buffer.append(line)

    _flush_paragraph(buffer, blocks)
    return blocks


def parse_markdown(text: str) -> List[Block]:
    """解析 Markdown 文本（保留标题层级）"""
    return _split_text_blocks(text, detect_headings=True)


def parse_plain_text(text: str) -> List[Block]:
    """解析纯文本（不推断标题，全部作为段落）"""
    return _split_text_blocks(text, detect_headings=False)


def _parse_pdf_text(text: str) -> List[Block]:
    """PDF 抽取出的文本没有可靠的标题结构，按段落处理"""
    return _split_text_blocks(text, detect_headings=False)


def parse_file(path: Union[str, Path]) -> List[Block]:
    """
    解析单个文档文件，返回 Block 列表。

    按后缀自动选择解析方式；不支持的格式抛出 ValueError。
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".md", ".markdown"):
        content = path.read_text(encoding="utf-8")
        return parse_markdown(content)

    if suffix == ".txt":
        content = path.read_text(encoding="utf-8")
        return parse_plain_text(content)

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:  # pragma: no cover
            raise ImportError("解析 PDF 需要安装 pypdf：pip install pypdf") from e

        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text)
        return _parse_pdf_text("\n\n".join(pages))

    raise ValueError(f"不支持的文档格式: {suffix}（支持 .md / .markdown / .txt / .pdf）")


def count_blocks(blocks: List[Block]) -> dict:
    """统计块数量（供日志与测试使用）"""
    return {
        "total": len(blocks),
        "headings": sum(1 for b in blocks if b.type == "heading"),
        "paragraphs": sum(1 for b in blocks if b.type == "paragraph"),
    }


# 为 dataclass 兼容旧式调用提供便捷函数
def blocks_to_dicts(blocks: List[Block]) -> List[dict]:
    return [b.to_dict() for b in blocks]
