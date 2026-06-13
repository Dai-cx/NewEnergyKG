#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问答系统对比评估脚本

参考 PDF 第四部分"问答系统评估与优化"思想，对以下两种模式进行批量对比：
1. 纯 LLM 模式（enable_kg=False）：仅依靠 LLM 自身知识生成回答
2. KG+RAG 模式（enable_kg=True）：先从 Neo4j 检索结构化知识，再交给 LLM 生成

评估指标包括：
- 响应时间 response_time_ms
- 意图识别准确率 Intent Accuracy
- 实体命中率 Entity Hit Rate
- 回答来源分布（llm / local_fallback）
- KG 命中率（KG+RAG 模式下检索是否成功）
- 平均回答长度

使用方法：
    cd d:\\NewEnergyKG-myself
    .venv\\Scripts\\python.exe -m qa.evaluate --mode both --output qa/eval_result.md

参数：
    --mode {llm_only,kg_rag,both}  评估模式，默认 both
    --dataset PATH                 测试集 JSON 路径，默认 qa/eval_dataset.json
    --output PATH                  Markdown 报告输出路径，默认 qa/eval_report.md
    --csv PATH                     CSV 原始结果输出路径，默认 qa/eval_result.csv
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

# 确保能导入 qa 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qa import config
from qa.answer_engine import AnswerEngine


# ==================== 评估指标计算 ====================
def compute_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据原始结果计算汇总指标"""
    total = len(results)
    if total == 0:
        return {}

    intent_correct = sum(1 for r in results if r["intent_match"])
    entity_hit = sum(1 for r in results if r["entity_hit"])
    kg_used = sum(1 for r in results if r["kg_used"])
    llm_source = sum(1 for r in results if r["source"] == "llm")
    fallback_source = sum(1 for r in results if r["source"] == "local_fallback")

    avg_time = sum(r["response_time_ms"] for r in results) / total
    avg_length = sum(r["answer_length"] for r in results) / total

    return {
        "total": total,
        "intent_accuracy": intent_correct / total,
        "entity_hit_rate": entity_hit / total,
        "kg_hit_rate": kg_used / total,
        "llm_count": llm_source,
        "fallback_count": fallback_source,
        "avg_time_ms": avg_time,
        "avg_answer_length": avg_length,
    }


# ==================== 单条评估 ====================
def evaluate_question(engine: AnswerEngine, item: Dict[str, Any]) -> Dict[str, Any]:
    """评估单个问题，返回详细结果"""
    question = item["question"]
    expected_intent = item.get("expected_intent", "")
    expected_entities = set(item.get("expected_entities", []))

    result = engine.answer(question)

    # 意图是否匹配
    intent_match = result["intent"] == expected_intent

    # 实体命中率：期望实体中至少有一个被识别
    recognized = {e["name"] for e in result["entities"]}
    if not expected_entities:
        # 列表类问题不强制实体，按命中空集合处理为 1
        entity_hit = 1
    else:
        entity_hit = 1 if expected_entities & recognized else 0

    return {
        "question": question,
        "expected_intent": expected_intent,
        "predicted_intent": result["intent"],
        "intent_match": int(intent_match),
        "expected_entities": list(expected_entities),
        "predicted_entities": [e["name"] for e in result["entities"]],
        "entity_hit": int(entity_hit),
        "source": result["source"],
        "llm_used": result["llm_used"],
        "llm_model": result["llm_model"],
        "kg_used": int(result.get("kg_used", False)),
        "response_time_ms": result["response_time_ms"],
        "answer_length": len(result["answer"]),
        "answer": result["answer"],
    }


# ==================== 报告生成 ====================
def save_csv(results: List[Dict[str, Any]], path: Path):
    """保存原始结果到 CSV"""
    if not results:
        return
    keys = [
        "question", "expected_intent", "predicted_intent", "intent_match",
        "expected_entities", "predicted_entities", "entity_hit",
        "source", "llm_used", "llm_model", "kg_used",
        "response_time_ms", "answer_length", "answer",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in results:
            row = {k: r[k] for k in keys}
            # 列表字段序列化为字符串
            for list_key in ("expected_entities", "predicted_entities"):
                row[list_key] = "、".join(row[list_key])
            writer.writerow(row)


def build_markdown_report(
    mode: str,
    llm_results: List[Dict[str, Any]],
    kg_results: List[Dict[str, Any]],
    metrics_llm: Dict[str, Any],
    metrics_kg: Dict[str, Any],
) -> str:
    """生成 Markdown 对比报告"""
    lines = []
    lines.append("# 新能源知识图谱问答系统对比评估报告")
    lines.append("")
    lines.append('> 本报告参考 PDF 第四部分"问答系统评估与优化"思想，对纯 LLM 与 KG+RAG 两种模式进行对比。')
    lines.append("")
    lines.append("## 1. 评估配置")
    lines.append("")
    lines.append(f"- 评估模式：`{mode}`")
    lines.append(f"- 测试问题数：{len(llm_results) if llm_results else len(kg_results)}")
    lines.append(f"- LLM 可用：`{config.get_llm_provider() or '否'}`")
    lines.append(f"- LLM 模型：`{config.DEFAULT_MODEL}`")
    lines.append("")

    # 汇总指标对比表
    lines.append("## 2. 汇总指标对比")
    lines.append("")
    lines.append("| 指标 | 纯 LLM | KG+RAG | 差异 |")
    lines.append("|------|--------|--------|------|")
    if metrics_llm and metrics_kg:
        def row(label, key, fmt=".1f", unit=""):
            v1 = metrics_llm.get(key, 0)
            v2 = metrics_kg.get(key, 0)
            if fmt == ".1%":
                s1, s2 = f"{v1:.1%}", f"{v2:.1%}"
                diff = f"{v2 - v1:+.1%}"
            else:
                s1, s2 = f"{v1:.1f}", f"{v2:.1f}"
                diff = f"{v2 - v1:+.1f}{unit}"
            return f"| {label} | {s1} | {s2} | {diff} |"

        lines.append(row("意图准确率", "intent_accuracy", ".1%"))
        lines.append(row("实体命中率", "entity_hit_rate", ".1%"))
        lines.append(row("KG 命中率", "kg_hit_rate", ".1%"))
        lines.append(row("平均响应时间", "avg_time_ms", ".1f", " ms"))
        lines.append(row("平均回答长度", "avg_answer_length", ".1f", " 字"))
        lines.append(f"| LLM 生成次数 | {metrics_llm.get('llm_count', 0)} | {metrics_kg.get('llm_count', 0)} | - |")
        lines.append(f"| 本地兜底次数 | {metrics_llm.get('fallback_count', 0)} | {metrics_kg.get('fallback_count', 0)} | - |")
    else:
        metrics = metrics_llm or metrics_kg
        name = "纯 LLM" if metrics_llm else "KG+RAG"
        lines.append(f"| 指标 | {name} |")
        lines.append("|------|--------|")
        lines.append(f"| 意图准确率 | {metrics['intent_accuracy']:.1%} |")
        lines.append(f"| 实体命中率 | {metrics['entity_hit_rate']:.1%} |")
        lines.append(f"| KG 命中率 | {metrics['kg_hit_rate']:.1%} |")
        lines.append(f"| 平均响应时间 | {metrics['avg_time_ms']:.1f} ms |")
        lines.append(f"| 平均回答长度 | {metrics['avg_answer_length']:.1f} 字 |")
    lines.append("")

    # 详细结果对比表
    if metrics_llm and metrics_kg:
        lines.append("## 3. 逐题对比（时间与来源）")
        lines.append("")
        lines.append("| 问题 | 纯 LLM 时间 | KG+RAG 时间 | 时间差 | 纯 LLM 来源 | KG+RAG 来源 | KG 命中 |")
        lines.append("|------|-------------|-------------|--------|-------------|-------------|---------|")
        for r1, r2 in zip(llm_results, kg_results):
            time_diff = r2["response_time_ms"] - r1["response_time_ms"]
            q = r1["question"]
            lines.append(
                f"| {q} | {r1['response_time_ms']} ms | {r2['response_time_ms']} ms | "
                f"{time_diff:+d} ms | {r1['source']} | {r2['source']} | {'是' if r2['kg_used'] else '否'} |"
            )
        lines.append("")

    # 完整答案附录
    lines.append("## 4. 完整答案附录")
    lines.append("")
    for idx, r in enumerate((kg_results or llm_results), 1):
        lines.append(f"### 4.{idx} {r['question']}")
        lines.append("")
        lines.append(f"- 意图：{r['predicted_intent']}（期望：{r['expected_intent']}）")
        lines.append(f"- 实体：{r['predicted_entities'] or '无'}")
        lines.append(f"- 来源：{r['source']} | 时间：{r['response_time_ms']} ms")
        lines.append("")
        lines.append(f"**回答**：{r['answer']}")
        lines.append("")

    return "\n".join(lines)


# ==================== 主流程 ====================
def main():
    parser = argparse.ArgumentParser(description="新能源知识图谱问答系统对比评估")
    parser.add_argument(
        "--mode",
        choices=["llm_only", "kg_rag", "both"],
        default="both",
        help="评估模式：纯 LLM / KG+RAG / 两者对比",
    )
    parser.add_argument(
        "--dataset",
        default="qa/eval_dataset.json",
        help="测试集 JSON 路径",
    )
    parser.add_argument(
        "--output",
        default="qa/eval_report.md",
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--csv",
        default="qa/eval_result.csv",
        help="CSV 原始结果输出路径",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = Path(__file__).resolve().parent.parent / dataset_path
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print(f"已加载测试集：{len(dataset)} 条问题")

    llm_results, kg_results = [], []
    metrics_llm, metrics_kg = {}, {}

    if args.mode in ("llm_only", "both"):
        print("\n[1/2] 正在评估纯 LLM 模式...")
        engine_llm = AnswerEngine(enable_kg=False)
        # Warm-up：先用一个简单问题预热，避免第一次调用冷启动影响统计
        engine_llm.answer("你好")
        for item in dataset:
            r = evaluate_question(engine_llm, item)
            llm_results.append(r)
            print(f"  - {r['question']}: {r['response_time_ms']} ms, intent={r['predicted_intent']}, source={r['source']}")
        engine_llm.kg_client.close()
        metrics_llm = compute_metrics(llm_results)
        print(f"\n纯 LLM 平均时间：{metrics_llm['avg_time_ms']:.1f} ms")

    if args.mode in ("kg_rag", "both"):
        print("\n[2/2] 正在评估 KG+RAG 模式...")
        engine_kg = AnswerEngine(enable_kg=True)
        if not engine_kg.kg_client.is_connected():
            print("  警告：Neo4j 未连接，KG+RAG 模式将退化为纯 LLM + 本地 JSON。")
            print(f"  当前 URI：{config.NEO4J_URI}，用户：{config.NEO4J_USER}")
        # Warm-up
        engine_kg.answer("你好")
        for item in dataset:
            r = evaluate_question(engine_kg, item)
            kg_results.append(r)
            print(f"  - {r['question']}: {r['response_time_ms']} ms, intent={r['predicted_intent']}, source={r['source']}, kg_used={r['kg_used']}")
        engine_kg.kg_client.close()
        metrics_kg = compute_metrics(kg_results)
        print(f"\nKG+RAG 平均时间：{metrics_kg['avg_time_ms']:.1f} ms")

    # 生成报告
    report = build_markdown_report(args.mode, llm_results, kg_results, metrics_llm, metrics_kg)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parent.parent / output_path
    output_path.write_text(report, encoding="utf-8")
    print(f"\nMarkdown 报告已保存：{output_path}")

    # 保存 CSV
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parent.parent / csv_path
    if args.mode == "both":
        # both 模式下保存两份 CSV，分别加后缀
        save_csv(llm_results, csv_path.with_stem(csv_path.stem + "_llm"))
        save_csv(kg_results, csv_path.with_stem(csv_path.stem + "_kg"))
        print(f"CSV 结果已保存：{csv_path.with_stem(csv_path.stem + '_llm')}")
        print(f"CSV 结果已保存：{csv_path.with_stem(csv_path.stem + '_kg')}")
    else:
        save_csv((llm_results or kg_results), csv_path)
        print(f"CSV 结果已保存：{csv_path}")


if __name__ == "__main__":
    main()
