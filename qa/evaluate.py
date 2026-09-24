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
- 实体识别准确率 Entity Precision / 召回率 Entity Recall / F1
- 答案准确率 Answer Accuracy（基于期望关键词命中率）
- 知识图谱召回率 KG Recall（关系/列表类问题中检索到的期望实体比例）
- 回答来源分布（llm / local_fallback）
- KG 命中率（KG+RAG 模式下检索是否成功）
- 平均回答长度
- 答案质量 LLM-as-judge 评分（可选，--judge）

使用方法：
    cd d:\\NewEnergyKG-myself
    .venv\\Scripts\\python.exe -m qa.evaluate --mode both --judge --output qa/eval_result.md

参数：
    --mode {llm_only,kg_rag,both}  评估模式，默认 both
    --dataset PATH                 测试集 JSON 路径，默认 qa/eval_dataset.json
    --output PATH                  Markdown 报告输出路径，默认 qa/eval_report.md
    --csv PATH                     CSV 原始结果输出路径，默认 qa/eval_result.csv
    --judge                        启用 LLM-as-judge 柔性判分
    --judge-model MODEL            指定判分模型，默认与生成模型相同
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# 确保能导入 qa 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qa import config
from qa.answer_engine import AnswerEngine
from qa.llm_client import create_llm_client, LLMClient, LLMError


# ==================== LLM-as-judge 判分器 ====================
class LLMJudge:
    """基于 LLM 的柔性答案质量判分器"""

    SYSTEM_PROMPT = """你是一位严谨的问答系统评估专家。请根据【问题】和【参考答案关键词】，对【系统回答】进行质量评分。

评分维度（每项 0-25 分，总分 0-100）：
1. 相关性：回答是否紧扣问题，没有答非所问。
2. 完整性：是否覆盖了参考答案关键词所代表的关键信息点。
3. 事实正确性：回答中的技术事实、数值、因果关系是否合理，无明显幻觉。
4. 表达清晰度：语言是否通顺、结构是否清楚、是否便于理解。

评分标准：
- 90-100：完全回答了问题，关键信息完整，事实准确，表达清晰。
- 75-89：回答了问题，信息较完整，有小瑕疵或少量遗漏。
- 60-74：基本相关，但明显遗漏关键信息或存在部分不准确。
- 40-59：回答片面、含糊，或包含明显错误。
- 0-39：答非所问、事实严重错误、或几乎无有效信息。

请严格按以下 JSON 格式输出，不要添加任何额外解释：
{
  "score": 整数(0-100),
  "reason": "简短说明得分理由，50字以内",
  "dimensions": {
    "relevance": 整数(0-25),
    "completeness": 整数(0-25),
    "correctness": 整数(0-25),
    "clarity": 整数(0-25)
  }
}
"""

    def __init__(self, model: Optional[str] = None):
        """
        Args:
            model: 指定判分模型，默认使用当前配置的生成模型。
        """
        self.model = model
        try:
            # 若指定了判分模型，创建专用客户端；否则使用默认配置
            self.client = LLMClient(model=model) if model else create_llm_client()
        except LLMError:
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None

    def score(
        self,
        question: str,
        answer: str,
        expected_keywords: List[str],
        fallback_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        对单个回答进行 LLM 判分。

        Returns:
            {
                "judge_score": float,       # 0-100
                "judge_reason": str,
                "judge_dimensions": Dict[str, int],
                "judge_error": Optional[str],
            }
        """
        if not self.is_available():
            return self._fallback(
                fallback_score, "LLM 未配置，无法判分"
            )

        keywords_text = "、".join(expected_keywords) if expected_keywords else "无"
        user_prompt = (
            f"【问题】\n{question}\n\n"
            f"【参考答案关键词】\n{keywords_text}\n\n"
            f"【系统回答】\n{answer}\n\n"
            f"请输出 JSON 评分结果。"
        )

        try:
            response = self.client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            content = response.get("content", "")
            parsed = self._parse_json(content)
            if parsed is None:
                return self._fallback(fallback_score, f"判分模型未返回合法 JSON: {content[:200]}")

            score = int(parsed.get("score", 0))
            score = max(0, min(100, score))
            reason = str(parsed.get("reason", "")).strip()
            dimensions = parsed.get("dimensions", {})
            if not isinstance(dimensions, dict):
                dimensions = {}

            return {
                "judge_score": float(score),
                "judge_reason": reason,
                "judge_dimensions": dimensions,
                "judge_error": None,
            }
        except LLMError as e:
            return self._fallback(fallback_score, f"LLM 判分调用失败: {e}")
        except Exception as e:
            return self._fallback(fallback_score, f"判分异常: {e}")

    @staticmethod
    def _parse_json(content: str) -> Optional[Dict[str, Any]]:
        """从模型输出中提取 JSON，兼容 Markdown 代码块"""
        import re
        content = content.strip()
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 尝试提取 ```json ... ``` 或 ``` ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # 尝试提取第一个 { ... }
        match = re.search(r"(\{.*\})", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _fallback(fallback_score: Optional[float], error: str) -> Dict[str, Any]:
        return {
            "judge_score": fallback_score if fallback_score is not None else 0.0,
            "judge_reason": f"[fallback] {error}",
            "judge_dimensions": {},
            "judge_error": error,
        }


# ==================== KG 上下文有用性判分器 ====================
class KGUsefulnessJudge:
    """判断检索到的 KG 上下文对回答问题是否有用"""

    SYSTEM_PROMPT = """你是一位知识图谱检索质量评估专家。请根据【问题】和【从知识图谱检索到的上下文】，判断该上下文对回答这个问题是否有用。

评分标准（0-100）：
- 90-100：上下文直接包含回答问题的关键信息，几乎无需额外知识即可作答。
- 70-89：上下文提供了相关且有价值的信息，虽然可能不完整，但能显著辅助回答。
- 40-69：上下文有部分相关性，但遗漏了关键信息，或对回答帮助有限。
- 0-39：上下文与问题无关、为空、或无法帮助回答。

请严格按以下 JSON 格式输出，不要添加任何额外解释：
{
  "score": 整数(0-100),
  "reason": "简短说明得分理由，50字以内"
}
"""

    def __init__(self, model: Optional[str] = None):
        self.model = model
        try:
            self.client = LLMClient(model=model) if model else create_llm_client()
        except LLMError:
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None

    @staticmethod
    def _kg_context_to_text(kg_context: Any) -> str:
        """将 kg_context 字典转换为简洁可读的文本"""
        if not kg_context:
            return "（无知识图谱上下文）"
        if not isinstance(kg_context, dict):
            return str(kg_context)[:1000]

        parts = []

        if "overview" in kg_context:
            overview = kg_context["overview"]
            parts.append("【技术概览】")
            for key in ("name", "desc", "principle", "efficiency", "cost_level",
                        "development_stage", "market_share", "maturity"):
                if overview.get(key):
                    parts.append(f"{key}: {overview[key]}")
            for key in ("companies", "materials", "equipments", "applications",
                        "indicators", "policies", "compete_technologies"):
                if overview.get(key):
                    parts.append(f"{key}: {', '.join(map(str, overview[key]))}")

        if "relation" in kg_context:
            relation = kg_context["relation"]
            parts.append("【关系信息】")
            field = relation.get("field", "")
            if field and relation.get(field):
                parts.append(f"{field}: {', '.join(map(str, relation[field]))}")
            for key in ("companies", "materials", "equipments", "applications",
                        "indicators", "policies", "compete_technologies"):
                if relation.get(key):
                    parts.append(f"{key}: {', '.join(map(str, relation[key]))}")

        if "category_list" in kg_context:
            category_list = kg_context["category_list"]
            parts.append("【类别列表】")
            if category_list.get("category"):
                parts.append(f"category: {category_list['category']}")
            if category_list.get("technologies"):
                techs = category_list["technologies"]
                parts.append(f"technologies: {', '.join(map(str, techs[:30]))}")
                if len(techs) > 30:
                    parts.append(f"... 共 {len(techs)} 项")

        if "compare" in kg_context:
            compare = kg_context["compare"]
            parts.append("【对比信息】")
            for tech_key in ("tech1", "tech2"):
                tech = compare.get(tech_key, {})
                if tech.get("name"):
                    parts.append(f"{tech['name']}:")
                    for key in ("efficiency", "cost_level", "development_stage",
                                "applications", "materials", "companies"):
                        if tech.get(key):
                            parts.append(f"  {key}: {tech[key]}")

        if "all_technologies" in kg_context:
            all_techs = kg_context["all_technologies"]
            parts.append("【全部技术】")
            parts.append(f"总数: {all_techs.get('total', '未知')}")
            examples = all_techs.get("examples", [])
            parts.append(f"示例: {', '.join(map(str, examples[:30]))}")
            if len(examples) > 30:
                parts.append(f"... 共 {len(examples)} 项示例")

        text = "\n".join(parts)
        # 限制长度，避免 token 过长
        return text[:2000]

    def score(
        self,
        question: str,
        kg_context: Any,
        fallback_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """对 KG 上下文有用性打分"""
        if not self.is_available():
            return {
                "kg_usefulness_score": fallback_score if fallback_score is not None else 0.0,
                "kg_usefulness_reason": "[fallback] LLM 未配置，无法判分",
                "kg_usefulness_error": "LLM 未配置",
            }

        if not kg_context:
            return {
                "kg_usefulness_score": 0.0,
                "kg_usefulness_reason": "未检索到任何知识图谱上下文",
                "kg_usefulness_error": None,
            }

        context_text = self._kg_context_to_text(kg_context)
        user_prompt = (
            f"【问题】\n{question}\n\n"
            f"【从知识图谱检索到的上下文】\n{context_text}\n\n"
            f"请输出 JSON 评分结果。"
        )

        try:
            response = self.client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            content = response.get("content", "")
            parsed = LLMJudge._parse_json(content)
            if parsed is None:
                return {
                    "kg_usefulness_score": fallback_score if fallback_score is not None else 0.0,
                    "kg_usefulness_reason": f"[fallback] 判分模型未返回合法 JSON",
                    "kg_usefulness_error": f"未返回合法 JSON: {content[:200]}",
                }

            score = int(parsed.get("score", 0))
            score = max(0, min(100, score))
            reason = str(parsed.get("reason", "")).strip()
            return {
                "kg_usefulness_score": float(score),
                "kg_usefulness_reason": reason,
                "kg_usefulness_error": None,
            }
        except LLMError as e:
            return {
                "kg_usefulness_score": fallback_score if fallback_score is not None else 0.0,
                "kg_usefulness_reason": f"[fallback] LLM 判分调用失败",
                "kg_usefulness_error": str(e),
            }
        except Exception as e:
            return {
                "kg_usefulness_score": fallback_score if fallback_score is not None else 0.0,
                "kg_usefulness_reason": f"[fallback] 判分异常",
                "kg_usefulness_error": str(e),
            }


# ==================== 评估指标计算工具函数 ====================
def _keyword_hit_rate(answer: str, keywords: List[str]) -> float:
    """计算答案中命中期望关键词的比例"""
    if not keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return hits / len(keywords)


def _entity_set_metrics(expected: set, predicted: set) -> Dict[str, float]:
    """计算实体识别的精确率、召回率、F1"""
    if not expected and not predicted:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not expected:
        # 无期望实体时，只要预测出实体即认为精确率为 0（无标准可依）
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    if not predicted:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    tp = len(expected & predicted)
    precision = tp / len(predicted)
    recall = tp / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _extract_kg_items(kg_context: Any, intent: str) -> set:
    """从 kg_context 中提取可用于召回率计算的实体/技术名称集合"""
    items = set()
    if not isinstance(kg_context, dict):
        return items

    if "relation" in kg_context:
        rel = kg_context["relation"]
        field = rel.get("field", "")
        if field and isinstance(rel.get(field), list):
            items.update(rel[field])
        # 兼容 overview 中的各类列表
        for key in ("companies", "materials", "equipments", "applications",
                    "indicators", "policies", "compete_technologies"):
            if isinstance(rel.get(key), list):
                items.update(rel[key])

    if "overview" in kg_context:
        overview = kg_context["overview"]
        for key in ("companies", "materials", "equipments", "applications",
                    "indicators", "policies", "compete_technologies"):
            if isinstance(overview.get(key), list):
                items.update(overview[key])

    if "category_list" in kg_context:
        techs = kg_context["category_list"].get("technologies", [])
        if isinstance(techs, list):
            items.update(techs)

    if "compare" in kg_context:
        compare = kg_context["compare"]
        for tech_key in ("tech1", "tech2"):
            tech = compare.get(tech_key, {})
            items.add(tech.get("name", ""))
            for key in ("companies", "materials", "equipments", "applications"):
                if isinstance(tech.get(key), list):
                    items.update(tech[key])

    if "all_technologies" in kg_context:
        examples = kg_context["all_technologies"].get("examples", [])
        if isinstance(examples, list):
            items.update(examples)

    return {i for i in items if i}


def _kg_recall(kg_context: Any, expected_entities: List[str], intent: str) -> Optional[float]:
    """计算 KG 检索对期望实体的召回率"""
    if not expected_entities:
        return None
    kg_items = _extract_kg_items(kg_context, intent)
    if not kg_items:
        return 0.0
    expected = set(expected_entities)
    hits = sum(1 for e in expected if any(e.lower() in item.lower() or item.lower() in e.lower() for item in kg_items))
    return hits / len(expected)


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

    # 答案准确率：基于期望关键词命中率的平均
    answer_acc_values = [r["answer_accuracy"] for r in results if r["answer_accuracy"] is not None]
    answer_accuracy = sum(answer_acc_values) / len(answer_acc_values) if answer_acc_values else 0.0

    # LLM-as-judge 平均分（仅统计成功判分的样本）
    judge_scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    avg_judge_score = sum(judge_scores) / len(judge_scores) if judge_scores else None
    judge_success_rate = len(judge_scores) / total if total > 0 else 0.0

    # KG 上下文有用性平均分（仅 KG+RAG 模式统计）
    kg_usefulness_scores = [r["kg_usefulness_score"] for r in results if r.get("kg_usefulness_score") is not None]
    avg_kg_usefulness = sum(kg_usefulness_scores) / len(kg_usefulness_scores) if kg_usefulness_scores else None
    kg_usefulness_success_rate = len(kg_usefulness_scores) / total if total > 0 else 0.0

    # 实体识别精确率、召回率、F1（仅统计有期望实体的样本）
    entity_metrics = [r["entity_metrics"] for r in results
                      if r["entity_metrics"] is not None and r.get("expected_entities")]
    if entity_metrics:
        avg_precision = sum(m["precision"] for m in entity_metrics) / len(entity_metrics)
        avg_recall = sum(m["recall"] for m in entity_metrics) / len(entity_metrics)
        avg_f1 = sum(m["f1"] for m in entity_metrics) / len(entity_metrics)
    else:
        avg_precision = avg_recall = avg_f1 = 0.0

    # KG 召回率：仅统计设置了期望关系实体且 KG 被调用的样本
    kg_recall_values = [r["kg_recall"] for r in results
                        if r["kg_recall"] is not None and r.get("expected_relation_entities")]
    kg_recall = sum(kg_recall_values) / len(kg_recall_values) if kg_recall_values else 0.0

    return {
        "total": total,
        "intent_accuracy": intent_correct / total,
        "entity_hit_rate": entity_hit / total,
        "entity_precision": avg_precision,
        "entity_recall": avg_recall,
        "entity_f1": avg_f1,
        "answer_accuracy": answer_accuracy,
        "avg_judge_score": avg_judge_score,
        "judge_success_rate": judge_success_rate,
        "avg_kg_usefulness": avg_kg_usefulness,
        "kg_usefulness_success_rate": kg_usefulness_success_rate,
        "kg_recall": kg_recall,
        "kg_hit_rate": kg_used / total,
        "llm_count": llm_source,
        "fallback_count": fallback_source,
        "avg_time_ms": avg_time,
        "avg_answer_length": avg_length,
    }


# ==================== 单条评估 ====================
def evaluate_question(
    engine: AnswerEngine,
    item: Dict[str, Any],
    answer_judge: Optional[LLMJudge] = None,
    kg_judge: Optional[KGUsefulnessJudge] = None,
) -> Dict[str, Any]:
    """评估单个问题，返回详细结果"""
    question = item["question"]
    expected_intent = item.get("expected_intent", "")
    expected_entities = set(item.get("expected_entities", []))
    expected_answer_keywords = item.get("expected_answer_keywords", [])
    expected_relation_entities = item.get("expected_relation_entities", [])

    result = engine.answer(question)

    # 意图是否匹配
    intent_match = result["intent"] == expected_intent

    # 实体命中率：期望实体中至少有一个被识别（兼容旧指标）
    recognized = {e["name"] for e in result["entities"]}
    if not expected_entities:
        # 列表类问题不强制实体，按命中空集合处理为 1
        entity_hit = 1
    else:
        entity_hit = 1 if expected_entities & recognized else 0

    # 实体识别精确率、召回率、F1
    entity_metrics = _entity_set_metrics(expected_entities, recognized)

    # 答案准确率：期望关键词在回答中的命中比例
    answer_accuracy = _keyword_hit_rate(result["answer"], expected_answer_keywords)

    # LLM-as-judge 柔性判分（可选）
    judge_result = {
        "judge_score": None,
        "judge_reason": None,
        "judge_dimensions": None,
        "judge_error": None,
        "judge_model": None,
    }
    if answer_judge is not None and answer_judge.is_available():
        judge_result = answer_judge.score(
            question=question,
            answer=result["answer"],
            expected_keywords=expected_answer_keywords,
            fallback_score=answer_accuracy * 100,
        )
        judge_result["judge_model"] = answer_judge.model

    # KG 上下文有用性判分（可选）
    kg_usefulness_result = {
        "kg_usefulness_score": None,
        "kg_usefulness_reason": None,
        "kg_usefulness_error": None,
    }
    if kg_judge is not None and kg_judge.is_available() and result.get("kg_context"):
        kg_usefulness_result = kg_judge.score(
            question=question,
            kg_context=result["kg_context"],
            fallback_score=100.0 if result.get("kg_used") else 0.0,
        )

    # KG 召回率：检索结果覆盖期望关系实体的比例
    kg_recall = _kg_recall(result.get("kg_context"), expected_relation_entities, result["intent"])

    return {
        "question": question,
        "expected_intent": expected_intent,
        "predicted_intent": result["intent"],
        "intent_match": int(intent_match),
        "expected_entities": list(expected_entities),
        "predicted_entities": [e["name"] for e in result["entities"]],
        "entity_hit": int(entity_hit),
        "entity_metrics": entity_metrics,
        "entity_precision": entity_metrics["precision"],
        "entity_recall": entity_metrics["recall"],
        "entity_f1": entity_metrics["f1"],
        "expected_answer_keywords": expected_answer_keywords,
        "answer_accuracy": answer_accuracy,
        **judge_result,
        **kg_usefulness_result,
        "expected_relation_entities": expected_relation_entities,
        "kg_recall": kg_recall,
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
        "entity_precision", "entity_recall", "entity_f1",
        "expected_answer_keywords", "answer_accuracy",
        "judge_score", "judge_reason", "judge_dimensions", "judge_error",
        "kg_usefulness_score", "kg_usefulness_reason", "kg_usefulness_error",
        "expected_relation_entities", "kg_recall",
        "source", "llm_used", "llm_model", "kg_used",
        "response_time_ms", "answer_length", "answer",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in results:
            row = {k: r[k] for k in keys}
            # 列表/字典字段序列化为字符串
            for list_key in ("expected_entities", "predicted_entities",
                             "expected_answer_keywords", "expected_relation_entities"):
                row[list_key] = "、".join(row[list_key])
            if isinstance(row.get("judge_dimensions"), dict):
                row["judge_dimensions"] = json.dumps(row["judge_dimensions"], ensure_ascii=False)
            # None 值显示为空
            for k in ("kg_recall", "judge_score", "judge_reason", "judge_error",
                      "kg_usefulness_score", "kg_usefulness_reason", "kg_usefulness_error"):
                if row[k] is None:
                    row[k] = ""
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
    # 检查是否有判分数据，推断是否启用了 judge
    has_judge = any(r.get("judge_score") is not None for r in (llm_results + kg_results))
    if has_judge:
        # 取第一个成功判分的模型名作为展示
        sample = next((r for r in (llm_results + kg_results) if r.get("judge_score") is not None), {})
        judge_model = sample.get("judge_model") or sample.get("llm_model", config.DEFAULT_MODEL)
        lines.append(f"- LLM-as-judge：已启用（判分模型：`{judge_model}`）")
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
            # None 值显示为 -
            if v1 is None or v2 is None:
                s1 = "-" if v1 is None else f"{v1:.1f}"
                s2 = "-" if v2 is None else f"{v2:.1f}"
                diff = "-"
                return f"| {label} | {s1} | {s2} | {diff} |"
            if fmt == ".1%":
                s1, s2 = f"{v1:.1%}", f"{v2:.1%}"
                diff = f"{v2 - v1:+.1%}"
            else:
                s1, s2 = f"{v1:.1f}", f"{v2:.1f}"
                diff = f"{v2 - v1:+.1f}{unit}"
            return f"| {label} | {s1} | {s2} | {diff} |"

        lines.append(row("意图准确率", "intent_accuracy", ".1%"))
        lines.append(row("实体命中率", "entity_hit_rate", ".1%"))
        lines.append(row("实体识别精确率", "entity_precision", ".1%"))
        lines.append(row("实体识别召回率", "entity_recall", ".1%"))
        lines.append(row("实体识别 F1", "entity_f1", ".1%"))
        lines.append(row("答案准确率（关键词）", "answer_accuracy", ".1%"))
        lines.append(row("答案质量（LLM 判分）", "avg_judge_score", ".1f", " /100"))
        lines.append(row("KG 召回率", "kg_recall", ".1%"))
        lines.append(row("KG 命中率", "kg_hit_rate", ".1%"))
        lines.append(row("KG 上下文有用性", "avg_kg_usefulness", ".1f", " /100"))
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
        lines.append(f"| 实体识别精确率 | {metrics['entity_precision']:.1%} |")
        lines.append(f"| 实体识别召回率 | {metrics['entity_recall']:.1%} |")
        lines.append(f"| 实体识别 F1 | {metrics['entity_f1']:.1%} |")
        lines.append(f"| 答案准确率（关键词） | {metrics['answer_accuracy']:.1%} |")
        if metrics.get('avg_judge_score') is not None:
            lines.append(f"| 答案质量（LLM 判分） | {metrics['avg_judge_score']:.1f} /100 |")
        lines.append(f"| KG 召回率 | {metrics['kg_recall']:.1%} |")
        lines.append(f"| KG 命中率 | {metrics['kg_hit_rate']:.1%} |")
        if metrics.get('avg_kg_usefulness') is not None:
            lines.append(f"| KG 上下文有用性 | {metrics['avg_kg_usefulness']:.1f} /100 |")
        lines.append(f"| 平均响应时间 | {metrics['avg_time_ms']:.1f} ms |")
        lines.append(f"| 平均回答长度 | {metrics['avg_answer_length']:.1f} 字 |")
    lines.append("")

    # 详细结果对比表
    if metrics_llm and metrics_kg:
        has_judge = any(r.get("judge_score") is not None for r in (llm_results + kg_results))
        lines.append("## 3. 逐题对比（时间、来源与准确率）")
        lines.append("")
        if has_judge:
            lines.append("| 问题 | 纯 LLM 时间 | KG+RAG 时间 | 时间差 | 纯 LLM 关键词准确率 | KG+RAG 关键词准确率 | 纯 LLM 判分 | KG+RAG 判分 | KG 命中 | KG 召回 |")
            lines.append("|------|-------------|-------------|--------|---------------------|---------------------|-------------|-------------|---------|---------|")
            for r1, r2 in zip(llm_results, kg_results):
                time_diff = r2["response_time_ms"] - r1["response_time_ms"]
                q = r1["question"]
                acc1 = f"{r1['answer_accuracy']:.0%}"
                acc2 = f"{r2['answer_accuracy']:.0%}"
                j1 = f"{r1['judge_score']:.0f}" if r1.get("judge_score") is not None else "-"
                j2 = f"{r2['judge_score']:.0f}" if r2.get("judge_score") is not None else "-"
                kg_recall = f"{r2['kg_recall']:.0%}" if r2["kg_recall"] is not None else "-"
                lines.append(
                    f"| {q} | {r1['response_time_ms']} ms | {r2['response_time_ms']} ms | "
                    f"{time_diff:+d} ms | {acc1} | {acc2} | {j1} | {j2} | {'是' if r2['kg_used'] else '否'} | {kg_recall} |"
                )
        else:
            lines.append("| 问题 | 纯 LLM 时间 | KG+RAG 时间 | 时间差 | 纯 LLM 答案准确率 | KG+RAG 答案准确率 | KG 命中 | KG 召回 |")
            lines.append("|------|-------------|-------------|--------|-------------------|-------------------|---------|---------|")
            for r1, r2 in zip(llm_results, kg_results):
                time_diff = r2["response_time_ms"] - r1["response_time_ms"]
                q = r1["question"]
                acc1 = f"{r1['answer_accuracy']:.0%}"
                acc2 = f"{r2['answer_accuracy']:.0%}"
                kg_recall = f"{r2['kg_recall']:.0%}" if r2["kg_recall"] is not None else "-"
                lines.append(
                    f"| {q} | {r1['response_time_ms']} ms | {r2['response_time_ms']} ms | "
                    f"{time_diff:+d} ms | {acc1} | {acc2} | {'是' if r2['kg_used'] else '否'} | {kg_recall} |"
                )
        lines.append("")

    # 完整答案附录
    lines.append("## 4. 完整答案附录")
    lines.append("")
    for idx, r in enumerate((kg_results or llm_results), 1):
        lines.append(f"### 4.{idx} {r['question']}")
        lines.append("")
        lines.append(f"- 意图：{r['predicted_intent']}（期望：{r['expected_intent']}）")
        lines.append(f"- 实体：{r['predicted_entities'] or '无'}（期望：{r['expected_entities'] or '无'}）")
        lines.append(f"- 实体识别：精确率 {r['entity_precision']:.1%} / 召回率 {r['entity_recall']:.1%} / F1 {r['entity_f1']:.1%}")
        lines.append(f"- 答案准确率（关键词）：{r['answer_accuracy']:.1%}")
        if r.get("judge_score") is not None:
            lines.append(f"- 答案质量（LLM 判分）：{r['judge_score']:.0f}/100 — {r.get('judge_reason', '')}")
        if r["kg_recall"] is not None:
            lines.append(f"- KG 召回率：{r['kg_recall']:.1%}")
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
    parser.add_argument(
        "--judge",
        action="store_true",
        help="启用 LLM-as-judge 对回答进行柔性质量判分",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="指定判分模型，默认使用与回答生成相同的模型",
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

    # 初始化 LLM-as-judge
    judge = None
    if args.judge:
        judge = LLMJudge(model=args.judge_model)
        if not judge.is_available():
            print("警告：LLM 未配置，--judge 选项将无效，仍使用关键词命中率。")
            judge = None
        else:
            print(f"已启用 LLM-as-judge，判分模型：{args.judge_model or config.DEFAULT_MODEL}")

    if args.mode in ("llm_only", "both"):
        print("\n[1/2] 正在评估纯 LLM 模式...")
        engine_llm = AnswerEngine(enable_kg=False)
        # Warm-up：先用一个简单问题预热，避免第一次调用冷启动影响统计
        engine_llm.answer("你好")
        for item in dataset:
            r = evaluate_question(engine_llm, item, answer_judge=judge)
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
            r = evaluate_question(engine_kg, item, answer_judge=judge)
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
