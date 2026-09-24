# -*- coding: utf-8 -*-
"""
Week2 D3：RAGAS 风格生成质量四指标（LLM-as-judge）单元测试

测试要点：
1. 四个指标在"脚本化裁判 LLM"下的手工核算（公式逐值验证）；
2. 降级契约：LLM 失败 / 输出解析失败 / 缺输入 / 无上下文 → 返回 None 而非误报 0；
3. 汇总逻辑：只统计成功判分样本；
4. 报告生成：Markdown 结构与内容齐全。
"""

import pytest

from qa.eval.gen_metrics import (
    METRIC_LABELS,
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
    score_sample,
    summarize_scores,
)
from qa.ingestion.embedder import DebugHashEmbedder


class ScriptedJudge:
    """
    按 System Prompt 内容分发的脚本化 LLM 裁判。

    行为剧本：
    - 陈述拆分（最小事实陈述）→ 返回 2 条陈述
    - 证据裁判（支持度检查）  → supported = [true, false]（第 1 条支持、第 2 条不支持）
    - 检索裁判（片段相关性）  → relevant  = [false, true, true]
    - 问题反推                → 与测试问题高度相似的 1 条 + 无关的 1 条
    """

    provider = "fake"
    model = "fake-judge"

    def __init__(self, handler=None):
        self.handler = handler or self._default_handler
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return {
            "content": self.handler(system_prompt, user_prompt),
            "model": self.model,
            "provider": self.provider,
            "elapsed_ms": 1,
        }

    @staticmethod
    def _default_handler(system_prompt, user_prompt):
        if "最小事实陈述" in system_prompt:
            return ('{"claims": ["全钒液流电池循环寿命超过两万次",'
                    ' "全钒液流电池适合四小时以上长时储能"]}')
        if "证据裁判" in system_prompt:
            return '{"supported": [true, false]}'
        if "检索裁判" in system_prompt:
            return '{"relevant": [false, true, true]}'
        if "反推出" in system_prompt:
            return ('{"questions": ["磷酸铁锂电池的优点是什么？",'
                    ' "今天天气怎么样？"]}')
        raise AssertionError(f"未覆盖的裁判 System Prompt：{system_prompt[:40]}")


def embedder():
    return DebugHashEmbedder(dimension=64)


# ==================== 四个指标手工核算 ====================


class TestFaithfulness:
    def test_score_is_supported_ratio(self):
        judge = ScriptedJudge()
        contexts = ["全钒液流电池循环寿命超过两万次，适合长时储能。"]
        score = faithfulness("全钒液流电池循环寿命长。它也适合调峰。", contexts, judge)
        # claims=2，supported=[True, False] → 0.5
        assert score == 0.5

    def test_no_claims_returns_full_score(self):
        judge = ScriptedJudge(handler=lambda s, u: '{"claims": []}')
        # 回答无可证实陈述 → 无幻觉空间 → 1.0（只做一次陈述拆分）
        score = faithfulness("你好，很高兴为你服务。", [], judge)
        assert score == 1.0
        assert len(judge.calls) == 1

    def test_empty_contexts_zero_without_support_call(self):
        judge = ScriptedJudge()
        score = faithfulness("磷酸铁锂电池安全性高。", [], judge)
        # 无任何上下文 → 陈述无据可依 → 0；只调用陈述拆分，
        # 支持度检查短路（不浪费一次 LLM 调用）
        assert score == 0.0
        assert len(judge.calls) == 1
        assert "最小事实陈述" in judge.calls[0]["system"]

    def test_llm_failure_returns_none(self):
        class BrokenJudge(ScriptedJudge):
            def generate(self, system_prompt, user_prompt):
                raise RuntimeError("模拟裁判服务不可用")

        score = faithfulness("磷酸铁锂电池安全性高。", ["上下文"], BrokenJudge())
        assert score is None


class TestAnswerRelevancy:
    def test_relevant_answer_scores_higher_than_zero(self):
        judge = ScriptedJudge()
        score = answer_relevancy(
            "磷酸铁锂电池有什么优点？",
            "磷酸铁锂电池安全性高、循环寿命长、成本低。",
            judge,
            embedder=embedder(),
        )
        # 反推出的问题1与用户问题高度相似 → 平均余弦 > 0
        assert score is not None
        assert 0.0 < score <= 1.0

    def test_no_llm_returns_none(self):
        assert answer_relevancy("Q", "A", llm=None, embedder=embedder()) is None

    def test_bad_questions_output_returns_none(self):
        judge = ScriptedJudge(handler=lambda s, u: "我没有理解你的意思")
        score = answer_relevancy("Q", "A", judge, embedder=embedder())
        assert score is None


class TestContextPrecision:
    def test_mean_precision_at_relevant_ranks(self):
        judge = ScriptedJudge()
        contexts = ["无关片段", "相关片段A", "相关片段B"]
        # relevant=[False,True,True]：
        #   k=2（第2个片段相关）→ precision@2 = 1/2
        #   k=3（第3个片段相关）→ precision@3 = 2/3
        #   平均 = (0.5 + 0.6667)/2 ≈ 0.5833
        score = context_precision("问题", contexts, judge)
        assert score is not None
        assert score == pytest.approx(0.5833, abs=0.001)

    def test_all_irrelevant_is_zero(self):
        judge = ScriptedJudge(handler=lambda s, u: '{"relevant": [false, false]}')
        assert context_precision("问题", ["a", "b"], judge) == 0.0

    def test_no_context_returns_none(self):
        judge = ScriptedJudge()
        assert context_precision("问题", [], judge) is None
        assert judge.calls == []


class TestContextRecall:
    def test_ground_truth_coverage_ratio(self):
        judge = ScriptedJudge()
        score = context_recall(
            "问题",
            ["全钒液流电池循环寿命超过两万次。"],
            ground_truth="全钒液流电池循环寿命长，适合长时储能。",
            llm=judge,
        )
        # claims=2，covered=[True, False] → 0.5
        assert score == 0.5

    def test_without_ground_truth_returns_none(self):
        judge = ScriptedJudge()
        assert context_recall("Q", ["ctx"], None, judge) is None
        assert judge.calls == []

    def test_empty_contexts_zero(self):
        judge = ScriptedJudge()
        score = context_recall("Q", [], "标准答案一句话。", judge)
        assert score == 0.0


# ==================== 样本级与汇总 ====================


class TestScoreSample:
    def test_full_sample_scores_all_metrics(self):
        judge = ScriptedJudge()
        sample = {
            "question": "磷酸铁锂电池有什么优点？",
            "answer": "磷酸铁锂电池安全性高、循环寿命长。",
            "contexts": ["磷酸铁锂电池安全性高，循环寿命超过3000次。"],
            "ground_truth": "磷酸铁锂电池安全性高、循环寿命长。",
        }
        result = score_sample(sample, llm=judge, embedder=embedder())
        metrics = result["metrics"]
        assert set(metrics) == set(METRIC_LABELS.keys())
        # 无缺失输入
        assert result["missing"] == {}
        # faithfulness = 0.5（按剧本 supported=[True,False]）
        assert metrics["faithfulness"] == 0.5

    def test_missing_inputs_are_reported_not_zero(self):
        sample = {
            "question": "Q",
            "answer": "A",
            # 没有 contexts / ground_truth
        }
        result = score_sample(sample, llm=ScriptedJudge(), embedder=embedder())
        assert result["metrics"]["faithfulness"] is None
        assert result["metrics"]["context_precision"] is None
        assert result["metrics"]["context_recall"] is None
        assert "contexts" in result["missing"]["faithfulness"]
        assert "ground_truth" in result["missing"]["context_recall"]

    def test_summarize_counts_only_judged(self):
        judge = ScriptedJudge()
        sample = {
            "question": "Q",
            "answer": "磷酸铁锂电池安全性高、循环寿命长。",
            "contexts": ["磷酸铁锂电池安全性高。"],
            "ground_truth": "磷酸铁锂电池安全性高。",
        }
        ok = score_sample(sample, llm=judge, embedder=embedder())
        no_gt = score_sample({k: v for k, v in sample.items() if k != "ground_truth"},
                             llm=judge, embedder=embedder())
        summary = summarize_scores([ok, no_gt])
        assert summary["samples"] == 2
        recall = summary["context_recall"]
        assert recall["judged"] == 1
        assert recall["skipped"] == 1
        assert recall["score"] is not None
        # 其他指标两条都判了
        assert summary["faithfulness"]["judged"] == 2


# ==================== 报告生成 ====================


class TestReport:
    def test_write_gen_report(self, tmp_path):
        from qa.eval.gen_run import write_gen_report

        scored = [{
            "question": "磷酸铁锂电池有什么优点？",
            "source": "llm",
            "metrics": {
                "faithfulness": 0.5,
                "answer_relevancy": None,
                "context_precision": 0.58,
                "context_recall": 0.5,
            },
        }]
        summary = {
            "samples": 1,
            "faithfulness": {"score": 0.5, "judged": 1, "skipped": 0},
            "answer_relevancy": {"score": None, "judged": 0, "skipped": 1},
            "context_precision": {"score": 0.58, "judged": 1, "skipped": 0},
            "context_recall": {"score": 0.5, "judged": 1, "skipped": 0},
        }
        out = write_gen_report(scored, summary, tmp_path / "report.md")
        content = out.read_text(encoding="utf-8")
        assert "RAGAS" in content
        assert "| 指标 | 得分 |" in content
        assert "faithfulness · 忠实度" in content
        assert "逐样本明细" in content
