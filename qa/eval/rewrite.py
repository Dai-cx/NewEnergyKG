#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询改写（多轮指代消解）专项评估 —— 为 Week2 D1 提供数据支撑

## 为什么需要单独一个评估入口

`qa/eval/run.py`（检索消融）刻意**过滤掉带 `history` 的多轮指代题**：
这类题只有先经查询改写、补全指代实体后才能检索，直接拿去跑消融会拖低所有组的
指标，造成"系统性偏差"而非"某组真的差"。

但这留下一个空白：**改写能力本身没有任何量化指标**。本模块补上这一块。

## 评估设计（关键：干净的对照）

同一条检索管线、同一套评分口径，**唯一切换的变量是"喂给检索器的问句"**：

    原句（含指代）        "那它的缺点呢？"          → 检索 → 指标 A
    改写句（自包含）      "磷酸铁锂电池有什么缺点？"  → 检索 → 指标 B

A → B 的增量就是改写带来的收益。因为检索器、语料、判定函数全部不变，
增益可以干净地归因给"查询改写"这一个变量。

两组指标：
1. **实体抽取命中**：改写后的问句里能否抽出期望实体
   （指代被正确消解的直接证据）；
2. **检索指标**：Recall@K / MRR / Precision@K
   （改写是否真的让检索到了正确资料）。

## 用法

    # 对 5 道多轮指代题跑改写评估
    python -m qa.eval.rewrite --collection newenergy_kb

    # 只跑前 2 题（省 API）
    python -m qa.eval.rewrite --collection newenergy_kb --limit 2

    # 关闭改写作为对照（验证"没有改写时确实更差"）
    python -m qa.eval.rewrite --collection newenergy_kb --no-rewrite
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from qa import config
from qa.eval.dataset import judge_result, load_qa_golden
from qa.retrieval.base import RetrievalResult

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "rewrite_report.md"


# ==================== 指标（口径与 qa/eval/metrics.py 保持一致）====================


def _recall_at_k(flags: List[bool], top_k: int) -> float:
    """Recall@K：K 条里只要有一条相关就算命中"""
    return 1.0 if any(flags[:top_k]) else 0.0


def _precision_at_k(flags: List[bool], top_k: int) -> float:
    """Precision@K：K 条里相关结果的比例"""
    return (sum(flags[:top_k]) / top_k) if top_k else 0.0


def _reciprocal_rank(flags: List[bool]) -> float:
    """RR：第一条相关结果的倒数排名；无相关结果记 0"""
    for rank, flag in enumerate(flags, start=1):
        if flag:
            return 1.0 / rank
    return 0.0


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _retrieve_flags(
    retriever: Any,
    question: str,
    entry: Dict[str, Any],
    top_k: int,
) -> List[bool]:
    """检索一次并判定每条结果是否相关（补足到 top_k，保证各题长度一致）"""
    try:
        results: List[RetrievalResult] = retriever.retrieve(question, top_k=top_k)
    except Exception as e:  # 单题检索失败不应中断整批评估
        logger.warning(f"[rewrite] 检索失败，该题记为未命中：{e}")
        results = []
    flags = [judge_result(r, entry) for r in results]
    return (flags + [False] * top_k)[:top_k]


# ==================== 单题与批量评估 ====================


def evaluate_entry(
    entry: Dict[str, Any],
    router: Any,
    retriever: Any,
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    评估单道多轮指代题：对比"原句检索"与"改写后检索"。

    Returns:
        {"question", "rewritten", "rewrite_applied", "rewrite_used_llm",
         "entities", "expected_entities", "entity_hit_before", "entity_hit_after",
         "before": {...指标}, "after": {...指标}}
    """
    raw_question = entry.get("question", "")
    history = list(entry.get("history") or [])
    expected = [e for e in (entry.get("expected_entities") or []) if e]

    # ---- 查询理解：这里就是被测对象（改写 + 意图 + 实体抽取）----
    analysis = router.analyze(raw_question, history)
    rewritten = analysis.get("question") or raw_question
    entities = [e.get("name") for e in (analysis.get("entities") or [])]

    # ---- 实体命中：改写后能否抽出期望实体 ----
    # 原句通常直接抽不出来（"它"），所以 before 用原句单独跑一次规则抽取
    raw_entities = [
        e.get("name") for e in router.classifier.extract_entities(raw_question)
    ]
    entity_hit_before = any(e in raw_entities for e in expected) if expected else False
    entity_hit_after = any(e in entities for e in expected) if expected else False

    # ---- 检索：同一条管线，只换问句 ----
    flags_before = _retrieve_flags(retriever, raw_question, entry, top_k)
    flags_after = _retrieve_flags(retriever, rewritten, entry, top_k)

    def metrics(flags: List[bool]) -> Dict[str, float]:
        return {
            "recall_at_k": _recall_at_k(flags, top_k),
            "mrr": _reciprocal_rank(flags),
            "precision_at_k": _precision_at_k(flags, top_k),
            "hits": float(sum(flags[:top_k])),
        }

    return {
        "question": raw_question,
        "rewritten": rewritten,
        "rewrite_applied": bool(analysis.get("rewrite_applied")),
        "rewrite_used_llm": bool(analysis.get("rewrite_used_llm")),
        "intent": analysis.get("intent", ""),
        "intent_source": analysis.get("intent_source", ""),
        "entities": entities,
        "raw_entities": raw_entities,
        "expected_entities": expected,
        "entity_hit_before": entity_hit_before,
        "entity_hit_after": entity_hit_after,
        "before": metrics(flags_before),
        "after": metrics(flags_after),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总为 before/after 两列指标 + 增量"""
    if not rows:
        return {"queries": 0}

    def agg(key: str, field: str) -> float:
        return _mean([r[key][field] for r in rows])

    summary: Dict[str, Any] = {
        "queries": len(rows),
        "rewrite_applied": sum(1 for r in rows if r["rewrite_applied"]),
        "rewrite_used_llm": sum(1 for r in rows if r["rewrite_used_llm"]),
        "entity_hit_before": _mean([1.0 if r["entity_hit_before"] else 0.0 for r in rows]),
        "entity_hit_after": _mean([1.0 if r["entity_hit_after"] else 0.0 for r in rows]),
    }
    for field in ("recall_at_k", "mrr", "precision_at_k"):
        before = agg("before", field)
        after = agg("after", field)
        summary[field] = {"before": before, "after": after, "delta": after - before}
    return summary


def run_rewrite_eval(
    retriever: Any,
    dataset: List[Dict[str, Any]],
    router: Any,
    top_k: int = 5,
) -> tuple:
    """
    对全部"带 history 的多轮指代题"跑改写评估。

    Returns:
        (rows, summary)
    """
    multi_turn = [e for e in dataset if e.get("history")]
    logger.info(f"[rewrite] 多轮指代题 {len(multi_turn)} 道（评测集共 {len(dataset)} 题）")

    rows = []
    for entry in multi_turn:
        row = evaluate_entry(entry, router=router, retriever=retriever, top_k=top_k)
        rows.append(row)
        logger.info(
            f"[rewrite] {row['question']} → {row['rewritten']} | "
            f"实体命中 {row['entity_hit_before']} → {row['entity_hit_after']} | "
            f"Recall@K {row['before']['recall_at_k']:.2f} → {row['after']['recall_at_k']:.2f}"
        )
    return rows, summarize(rows)


# ==================== 报告 ====================


def write_rewrite_report(
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    output_path: Path,
    top_k: int = 5,
    notes: Optional[List[str]] = None,
    meta: Optional[List[str]] = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# 查询改写（多轮指代消解）评估报告", ""]
    lines.append(f"> K = {top_k}（Recall@{top_k} / Precision@{top_k}）")
    lines.append(f"> 多轮指代题数：{summary.get('queries', 0)}")
    lines.append(f"> 触发改写：{summary.get('rewrite_applied', 0)} 道；"
                 f"其中由 LLM 完成：{summary.get('rewrite_used_llm', 0)} 道")
    for line in meta or []:
        lines.append(f"> {line}")
    lines.append("")

    lines.append("## 汇总（原句 vs 改写后）")
    lines.append("")
    lines.append("| 指标 | 原句（含指代） | 改写后 | 增量 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| 实体抽取命中率 | {summary['entity_hit_before']:.4f} | "
        f"{summary['entity_hit_after']:.4f} | "
        f"{summary['entity_hit_after'] - summary['entity_hit_before']:+.4f} |"
    )
    for field, label in (("recall_at_k", "Recall@K"), ("mrr", "MRR"),
                         ("precision_at_k", "Precision@K")):
        info = summary[field]
        lines.append(
            f"| {label} | {info['before']:.4f} | {info['after']:.4f} | "
            f"{info['delta']:+.4f} |"
        )
    lines.append("")

    lines.append("## 逐题对比")
    lines.append("")
    lines.append("| # | 原问题 | 改写后 | 触发改写 | 期望实体 | 实体命中 | Recall@K |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, row in enumerate(rows, start=1):
        hit = f"{'✓' if row['entity_hit_before'] else '✗'} → {'✓' if row['entity_hit_after'] else '✗'}"
        recall = f"{row['before']['recall_at_k']:.2f} → {row['after']['recall_at_k']:.2f}"
        lines.append(
            f"| {i} | {row['question']} | {row['rewritten']} | "
            f"{'是' if row['rewrite_applied'] else '否'} | "
            f"{'、'.join(row['expected_entities']) or '-'} | {hit} | {recall} |"
        )
    lines.append("")

    default_notes = [
        "对照设计：同一条检索管线、同一套判定口径，唯一变量是喂给检索器的问句"
        "（原句 vs 改写句），因此增量可干净归因于查询改写。",
        "判定口径与检索消融一致：期望实体出现在检索文本或章节路径中即视为相关"
        "（LLM-free 近似）。",
        "原句的'实体抽取'用规则抽取单独跑一次：指代句（如「那它的缺点呢？」）"
        "通常抽不出实体，这正是改写要解决的问题。",
        "若各项增量接近 0，先检查是否所有题都未触发改写（改写需同时满足："
        "有 LLM、有会话历史、且命中指代词/短问句）。",
    ]
    lines.append("## 说明")
    lines.append("")
    for note in notes or default_notes:
        lines.append(f"- {note}")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


# ==================== CLI ====================


def build_retriever(collection: Optional[str]) -> Any:
    """构建文档检索器（向量 + BM25 + 重排），与问答引擎口径一致"""
    from qa.retrieval.factory import try_build_document_retriever

    retriever = try_build_document_retriever(collection=collection)
    if retriever is None:
        raise RuntimeError(
            "文档检索器不可用：请确认 Qdrant 已启动且集合非空"
            "（可先运行 python -m qa.ingestion.kb_renderer render --ingest --collection newenergy_kb）"
        )
    return retriever


def build_router(no_rewrite: bool = False) -> Any:
    """构建查询理解路由器（真实 LLM + 规则兜底）"""
    from qa.data_fallback import LocalDataStore
    from qa.intent_classifier import IntentClassifier
    from qa.llm_client import create_llm_client
    from qa.llm_router import LLMRouter

    data_store = LocalDataStore()
    classifier = IntentClassifier(
        entity_names=data_store.get_all_names(),
        fuzzy_threshold=config.ENTITY_FUZZY_THRESHOLD,
    )
    llm_client = create_llm_client()
    if llm_client is None:
        logger.warning("[rewrite] 未配置 LLM，改写不会触发（本评估将退化为对照基线）")
    return LLMRouter(
        llm_client=llm_client,
        classifier=classifier,
        intent_mode=config.INTENT_ROUTING_MODE,
        rewrite_mode="off" if no_rewrite else config.QUERY_REWRITE_MODE,
    )


def main(argv: Optional[List[str]] = None) -> int:
    argp = argparse.ArgumentParser(
        description="查询改写（多轮指代消解）专项评估 —— Week2 D1 数据支撑"
    )
    argp.add_argument("--collection", default=None,
                      help="Qdrant 集合名（默认 config.QDRANT_COLLECTION）")
    argp.add_argument("--top-k", type=int, default=5, help="评估 K 值（默认 5）")
    argp.add_argument("--limit", type=int, default=None,
                      help="最多评估前 N 道题（试跑用，省 API）")
    argp.add_argument("--no-rewrite", action="store_true",
                      help="关闭改写作为对照（验证没有改写时确实更差）")
    argp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                      help="Markdown 报告输出路径")
    args = argp.parse_args(argv)

    retriever = build_retriever(args.collection)
    router = build_router(no_rewrite=args.no_rewrite)

    dataset = load_qa_golden()
    multi_turn = [e for e in dataset if e.get("history")]
    if args.limit:
        multi_turn = multi_turn[:args.limit]
    if not multi_turn:
        logger.error("评测集中没有带 history 的多轮指代题，无法评估改写")
        return 1

    rows, summary = run_rewrite_eval(
        retriever=retriever, dataset=multi_turn, router=router, top_k=args.top_k
    )

    meta = [
        f"文档集合：{args.collection or config.QDRANT_COLLECTION}",
        f"改写模式：{'off（对照基线）' if args.no_rewrite else config.QUERY_REWRITE_MODE}",
        f"意图路由模式：{config.INTENT_ROUTING_MODE}",
    ]
    path = write_rewrite_report(
        rows=rows, summary=summary, output_path=args.output,
        top_k=args.top_k, meta=meta,
    )
    logger.info(f"[rewrite] 报告已保存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
