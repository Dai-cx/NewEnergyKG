# -*- coding: utf-8 -*-
"""
数据集加载与相关性判定

golden set 结构（与 qa/eval_dataset.json 兼容）：
    {"question": "...", "expected_entities": ["..."], "expected_intent": "...",
     "expected_answer_keywords": [...], "expected_relation_entities": [...]}

Week2 D2 起评测集拆成两个文件、加载时合并（合计 100 题）：
    - qa/eval_dataset.json        （原始 60 题，KG/文档/对比类）
    - qa/eval_dataset_extra.json  （新增 40 题：补 list/aggregate/path 等意图覆盖，
                                   并带 reference_answer 供 RAGAS 生成质量评估）
条目可选的扩展字段（加载时原样透传）：
    - reference_answer: 标准答案句（context_recall 的 ground truth）
    - history: 多轮对话历史（查询改写类评测用，检索消融会自动跳过）

相关性的三种来源：
1. expected_entities：检索结果文本/章节里出现任一期望实体 → 相关
   （适合"技术/企业/材料"类问题，同时覆盖 KG 与文档两条路）；
2. expected_section：检索结果的章节路径命中期望章节 → 相关
   （适合按 chunk 自动生成的"章节问答"评测）；
3. 都没有 → 该题不参与相关性判定（返回 False）。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from qa.retrieval.base import RetrievalResult

# 默认问答 golden set 路径（D1 前就存在的 60 题评估集）
DEFAULT_QA_DATASET = Path(__file__).resolve().parent.parent / "eval_dataset.json"
# Week2 D2：新增的 40 题扩展集（与主文件合并使用）
DEFAULT_QA_EXTRA = Path(__file__).resolve().parent.parent / "eval_dataset_extra.json"

# 参与"检索消融"的条目需要"能被独立检索"：
# 带 history 的多轮指代题只有改写后才能检索，普通消融（无改写）会误伤指标
RETRIEVAL_REQUIRED_KEYS = ("question", "expected_entities", "expected_intent")


def _normalize_entry(item: dict) -> Optional[Dict]:
    """把原始 JSON 条目规范化：question 必填，其余字段原样透传"""
    if not isinstance(item, dict) or not item.get("question"):
        return None
    entry = {
        "question": item["question"],
        "expected_entities": list(item.get("expected_entities") or []),
        "expected_intent": item.get("expected_intent", ""),
        "expected_answer_keywords": list(item.get("expected_answer_keywords") or []),
        "expected_relation_entities": list(item.get("expected_relation_entities") or []),
    }
    # 可选扩展字段：有则透传，无则不出现（保持旧条目轻量）
    for key in ("reference_answer", "history", "expected_section", "source"):
        if key in item:
            entry[key] = item[key]
    return entry


def _load_json(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    entries = []
    for item in raw:
        normalized = _normalize_entry(item)
        if normalized is not None:
            entries.append(normalized)
    return entries


def load_qa_golden(path: Optional[Path] = None) -> List[Dict]:
    """
    加载问答 golden set（默认合并 60 + 40 = 100 题），规范化成评测条目。

    注意：当 path 为默认主文件（或 None）时会自动合并扩展集；
    传入自定义文件路径时只加载该文件（便于单独调试小数据集）。
    """
    if path is None:
        path = DEFAULT_QA_DATASET
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"评测集不存在: {path}")

    entries = _load_json(path)
    if path == DEFAULT_QA_DATASET and DEFAULT_QA_EXTRA.exists():
        extra = _load_json(DEFAULT_QA_EXTRA)
        entries.extend(extra)
        logger.info(f"[eval] 已合并扩展集：{len(extra)} 条 <- {DEFAULT_QA_EXTRA.name}")

    logger.info(f"[eval] 已加载评测集：{len(entries)} 条 <- {path.name}")
    return entries


def load_retrieval_dataset(path: Optional[Path] = None) -> List[Dict]:
    """
    加载"可用于检索消融"的评测子集：过滤掉多轮指代题（需改写才能独立检索）。
    """
    entries = load_qa_golden(path=path)
    usable = [e for e in entries if not e.get("history")]
    skipped = len(entries) - len(usable)
    if skipped:
        logger.warning(f"[eval] 已跳过 {skipped} 条多轮指代题（检索消融不适用）")
    return usable


def build_chunk_dataset(chunks: List[Dict], top_k_context: int = 1) -> List[Dict]:
    """
    由 chunk 列表自动生成"章节问答"评测条目：
        每个 chunk 生成一个问题，期望命中该 chunk 所在章节。

    用于快速搭建"文档检索"的消融实验（无需手工标注）。
    """
    entries = []
    for chunk in chunks:
        section = (chunk.get("metadata") or {}).get("section", "")
        leaf = section.split("/")[-1].strip() if section else "本文档"
        entries.append({
            "question": f"请介绍一下{leaf}的相关内容。",
            "expected_entities": [],
            "expected_section": section.strip(),
        })
    return entries


def judge_result(result: RetrievalResult, entry: Dict) -> bool:
    """
    判定一条检索结果是否与评测条目相关（LLM-free 的近似判定）。

    规则：
    1. 条目声明了期望实体 → 结果文本或章节命中任一实体即相关；
    2. 否则条目声明了期望章节 → 结果章节命中即相关；
    3. 都未声明 → 不相关。

    注意：这是"可用关键词判定的近似"，严格语义相关性仍需
    LLM-as-judge / 人工标注，理解其局限再使用。
    """
    text = result.text or ""
    section = result.section or ""
    combined = text + "\n" + section

    entities = entry.get("expected_entities") or []
    for entity in entities:
        if entity and entity in combined:
            return True

    expected_section = entry.get("expected_section") or ""
    if expected_section:
        # 双向包含：章节全路径命中或结果更细/更粗都算命中
        if expected_section in section or section in expected_section:
            return True

    return False


def sample_dataset(entries: List[Dict], limit: Optional[int] = None) -> List[Dict]:
    """截取前 N 条（调试时先用小样本跑通）"""
    if limit is None:
        return entries
    return entries[:limit]
