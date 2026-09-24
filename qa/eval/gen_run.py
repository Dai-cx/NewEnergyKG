#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAGAS 风格生成质量评估命令行入口（Week2 D3）

评分单元：{question, answer, contexts, ground_truth?}
    - contexts     = 问答时混合检索返回的参考资料文本（回答带了几个引用就有几条）
    - ground_truth = 评测集条目里的 reference_answer（没有则 context_recall 跳过）

两种取样本的方式：
    1) 直接评分 JSON 样本文件（默认 qa/eval/demo_ragas_samples.json）：
           python -m qa.eval.gen_run --limit 5
    2) 用当前 AnswerEngine 现场收集（真实问答 + 真实检索上下文）：
           python -m qa.eval.gen_run --collect-live --limit 20 --samples-output qa/eval/collected_samples.json
       要求本机服务已就绪（Neo4j/Qdrant/API Key），收集结果会先落盘供复用。

前提：评分需要 LLM 裁判（DASHSCOPE_API_KEY 或 OpenAI 兼容 Key）；
未配置时脚本仍会生成报告，但指标全部标为"无法判分"，并给出原因。

用法示例：
    python -m qa.eval.gen_run --samples qa/eval/demo_ragas_samples.json
    python -m qa.eval.gen_run --collect-live --limit 20 --output qa/eval/ragas_live_report.md
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from qa.answer_engine import AnswerEngine
from qa.eval.dataset import load_qa_golden
from qa.eval.gen_metrics import METRIC_LABELS, score_sample, summarize_scores
from qa.llm_client import create_llm_client

DEFAULT_SAMPLES = Path(__file__).resolve().parent / "demo_ragas_samples.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "ragas_metrics_report.md"
DEFAULT_COLLECTED = Path(__file__).resolve().parent / "collected_samples.json"

ALL_METRICS = list(METRIC_LABELS.keys())


def load_samples(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """加载评分样本 JSON（list of sample dict）"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"样本文件不存在：{path}\n"
            "提示：可先跑 --collect-live 现场收集，或使用默认 demo 样本"
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"样本文件应为 JSON 数组：{path}")
    samples = [s for s in raw if isinstance(s, dict) and s.get("question")]
    logger.info(f"已加载评分样本：{len(samples)} 条 <- {path.name}")
    if limit:
        samples = samples[:limit]
    return samples


def collect_samples_from_engine(
    engine: AnswerEngine,
    dataset: List[Dict[str, Any]],
    session_prefix: str = "eval-",
) -> List[Dict[str, Any]]:
    """
    用 AnswerEngine 对评测集逐条问答，把回答与检索引用收集成评分样本。

    说明：
    - answer    来自引擎生成（LLM 失败时是本地兜底，报告里可见 source）
    - contexts  取响应 references 的 text（带出处，真实反映"喂给 LLM 的资料"）
    - ground_truth 来自条目的 reference_answer（标准答案）
    """
    samples = []
    for i, entry in enumerate(dataset):
        question = entry.get("question", "")
        if not question:
            continue
        try:
            result = engine.answer(question, session_id=f"{session_prefix}{i}")
        except Exception as e:
            logger.warning(f"问答失败，跳过该样本：{question} -> {e}")
            continue
        samples.append({
            "question": question,
            "answer": result.get("answer", ""),
            "source": result.get("source", ""),
            "resolved_question": result.get("resolved_question"),
            "query_rewritten": result.get("query_rewritten", False),
            "intent_source": result.get("intent_source", ""),
            "contexts": [r.get("text", "") for r in (result.get("references") or [])],
            "ground_truth": entry.get("reference_answer") or None,
        })
        logger.info(
            f"[{i + 1}/{len(dataset)}] 已收集：{question[:24]}... "
            f"contexts={len(samples[-1]['contexts'])}"
        )
    return samples


def save_samples(samples: List[Dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"评分样本已保存：{path}（{len(samples)} 条，可复用再评）")
    return path


def write_gen_report(
    scored: List[Dict[str, Any]],
    summary: Dict[str, Any],
    output_path: Path,
    notes: Optional[List[str]] = None,
) -> Path:
    """把逐样本 + 汇总结果写成 Markdown 报告"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# RAGAS 风格生成质量评估报告", ""]
    lines.append(f"> 样本数：{summary['samples']} ｜ 指标：faithfulness / "
                 "answer_relevancy / context_precision / context_recall")
    lines.append("")

    # 汇总表
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 指标 | 得分 | 成功判分 | 跳过 |")
    lines.append("|---|---|---|---|")
    for metric in ALL_METRICS:
        info = summary.get(metric, {})
        score = info.get("score")
        score_text = "-" if score is None else f"{score:.4f}"
        lines.append(
            f"| {METRIC_LABELS[metric]} | {score_text} | "
            f"{info.get('judged', 0)} | {info.get('skipped', 0)} |"
        )
    lines.append("")

    # 逐样本
    lines.append("## 逐样本明细")
    lines.append("")
    lines.append("| # | 问题 | 回答来源 | Faith. | Rel. | Prec. | Recall |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, item in enumerate(scored, start=1):
        q = item.get("question", "")[:30]
        m = item["metrics"]
        cells = []
        for metric in ALL_METRICS:
            v = m.get(metric)
            cells.append("-" if v is None else f"{v:.2f}")
        lines.append(
            f"| {i} | {q} | {item.get('source', '') or '-'} | "
            f"{cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |"
        )
    lines.append("")

    lines.append("## 说明")
    lines.append("")
    default_notes = [
        "faithfulness：回答陈述被检索上下文支持的比例（幻觉指标）。",
        "answer_relevancy：由回答反推问题与用户问题的 embedding 余弦（官方 RAGAS 口径）。",
        "context_precision：检索片段里有用信息占比且考虑排序；无上下文无法判分。",
        "context_recall：标准答案被上下文覆盖的比例；需要条目提供 reference_answer。",
        "所有指标均需 LLM 裁判；未配置 Key 或判分失败会标记为「跳过」，不误报 0 分。",
        "示例样本可能为演示构造：正式结论请对真实问答收集的样本（--collect-live）评分。",
    ]
    for note in (notes or default_notes):
        lines.append(f"- {note}")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main(argv: Optional[List[str]] = None) -> int:
    argp = argparse.ArgumentParser(description="NewEnergyKG RAGAS 风格生成质量评估")
    argp.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES,
                      help="评分样本 JSON 路径")
    argp.add_argument("--collect-live", action="store_true",
                      help="用当前 AnswerEngine 现场收集样本（需服务就绪）")
    argp.add_argument("--limit", type=int, default=None, help="最多处理的样本数")
    argp.add_argument("--samples-output", type=Path, default=DEFAULT_COLLECTED,
                      help="现场收集的样本落盘路径")
    argp.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                      help="Markdown 报告输出路径")
    args = argp.parse_args(argv)

    # ---- 1. 取样本 ----
    if args.collect_live:
        logger.info("开始现场收集样本（真实问答 + 真实检索上下文）...")
        engine = AnswerEngine()
        dataset = [e for e in load_qa_golden() if e.get("reference_answer")]
        logger.info(f"可用于生成质量评估的条目：{len(dataset)} 条（带 reference_answer）")
        if args.limit:
            dataset = dataset[: args.limit]
        samples = collect_samples_from_engine(engine, dataset)
        if not samples:
            logger.error("现场收集失败：没有任何样本（请确认服务与 Key 已就绪）")
            return 1
        save_samples(samples, args.samples_output)
    else:
        try:
            samples = load_samples(args.samples, limit=args.limit)
        except (FileNotFoundError, ValueError) as e:
            logger.error(str(e))
            return 2

    # ---- 2. 创建 LLM 裁判 ----
    llm = create_llm_client()
    if llm is None:
        logger.warning(
            "未配置可用的 LLM（DASHSCOPE_API_KEY / OPENAI_API_KEY），"
            "全部指标将无法判分 —— 报告仅展示样本结构与跳过原因"
        )

    # ---- 3. 评分 ----
    scored = [score_sample(s, llm=llm) for s in samples]
    summary = summarize_scores(scored)

    # ---- 4. 报告 ----
    path = write_gen_report(scored, summary, args.output)
    for metric in ALL_METRICS:
        info = summary.get(metric, {})
        score = info.get("score")
        if score is not None:
            logger.info(f"{METRIC_LABELS[metric]} = {score:.4f} "
                        f"（judged {info['judged']}/{summary['samples']}）")
    logger.info(f"报告已保存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
