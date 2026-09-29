# -*- coding: utf-8 -*-
"""
查询改写评估（qa/eval/rewrite.py）单元测试 + 多轮指代题数据集回归

全部离线：用假 router / 假 retriever / 假 classifier 注入，不连 Qdrant、不调 LLM。
覆盖点：
- 三个检索指标（Recall@K / MRR / Precision@K）的口径
- 单题评估：原句 vs 改写句的对比结构
- 汇总与增量计算
- 报告落盘（含关键列）
- 数据集回归：多轮指代题必须存在、且必须与检索消融集隔离
"""

import json

import pytest

from qa.eval import rewrite as rw


# ==================== 假依赖 ====================

class FakeClassifier:
    """规则分类器替身：按预置表返回实体（模拟"原句抽不出实体"）"""

    def __init__(self, mapping):
        self.mapping = mapping

    def extract_entities(self, question):
        return [{"name": n} for n in self.mapping.get(question, [])]


class FakeRouter:
    """查询理解路由器替身：按预置表返回改写结果"""

    def __init__(self, rewrites, classifier):
        self.rewrites = rewrites
        self.classifier = classifier

    def analyze(self, question, history=None):
        rewritten, entities = self.rewrites.get(question, (question, []))
        return {
            "question": rewritten,
            "intent": "property",
            "intent_source": "llm",
            "entities": [{"name": n} for n in entities],
            "rewrite_applied": rewritten != question,
            "rewrite_used_llm": rewritten != question,
        }


class FakeRetriever:
    """检索器替身：问句命中关键词才返回"相关"结果"""

    def __init__(self, judge_keyword):
        self.judge_keyword = judge_keyword

    def retrieve(self, query, top_k=5):
        from qa.retrieval.base import RetrievalResult

        if self.judge_keyword in query:
            return [RetrievalResult(text=f"包含{self.judge_keyword}的资料", source="fake")]
        # 未命中：返回一批无关结果
        return [RetrievalResult(text="无关内容", source="fake") for _ in range(top_k)]


def make_entry(question, history, expected, keyword):
    return {
        "question": question,
        "history": history,
        "expected_entities": expected,
        "expected_keyword": keyword,
    }


# ==================== 指标口径 ====================

class TestMetrics:
    def test_recall_hits_when_any_relevant(self):
        assert rw._recall_at_k([False, False, True, False, False], 5) == 1.0
        assert rw._recall_at_k([False, False, False, False, False], 5) == 0.0

    def test_recall_respects_k(self):
        """相关结果排在第 4 位时，K=3 不应算命中"""
        flags = [False, False, False, True, False]
        assert rw._recall_at_k(flags, 3) == 0.0
        assert rw._recall_at_k(flags, 5) == 1.0

    def test_precision_is_ratio(self):
        assert rw._precision_at_k([True, False, True, False, False], 5) == pytest.approx(0.4)

    def test_precision_zero_k_guard(self):
        assert rw._precision_at_k([True], 0) == 0.0

    def test_mrr_uses_first_relevant_rank(self):
        assert rw._reciprocal_rank([False, True, True]) == pytest.approx(0.5)
        assert rw._reciprocal_rank([True]) == 1.0
        assert rw._reciprocal_rank([False, False]) == 0.0


# ==================== 单题评估 ====================

class TestEvaluateEntry:
    def test_rewrite_improves_entity_hit_and_recall(self):
        entry = make_entry("那它的缺点呢？", [{"role": "user", "content": "磷酸铁锂电池"}],
                           ["磷酸铁锂电池"], "磷酸铁锂电池")
        classifier = FakeClassifier({"那它的缺点呢？": []})          # 原句抽不出实体
        router = FakeRouter(
            {"那它的缺点呢？": ("磷酸铁锂电池有什么缺点？", ["磷酸铁锂电池"])}, classifier
        )
        retriever = FakeRetriever("磷酸铁锂电池")

        row = rw.evaluate_entry(entry, router=router, retriever=retriever, top_k=5)

        assert row["entity_hit_before"] is False
        assert row["entity_hit_after"] is True
        assert row["before"]["recall_at_k"] == 0.0
        assert row["after"]["recall_at_k"] == 1.0
        assert row["after"]["mrr"] == 1.0
        assert row["rewrite_applied"] is True

    def test_no_rewrite_means_identical_before_after(self):
        """改写未触发时两组指标必须完全相同（否则说明对照设计有漏洞）"""
        entry = make_entry("它和三元锂电池比怎么样？", [{"role": "user", "content": "x"}],
                           ["三元锂电池"], "三元锂电池")
        classifier = FakeClassifier({"它和三元锂电池比怎么样？": ["三元锂电池"]})
        router = FakeRouter({}, classifier)  # 改写表为空 -> 原样返回
        retriever = FakeRetriever("三元锂电池")

        row = rw.evaluate_entry(entry, router=router, retriever=retriever, top_k=5)

        assert row["rewrite_applied"] is False
        assert row["before"] == row["after"]

    def test_retriever_failure_does_not_raise(self):
        """单题检索异常不应中断整批评估，记为未命中"""
        class BrokenRetriever:
            def retrieve(self, query, top_k=5):
                raise RuntimeError("Qdrant 掉线")

        entry = make_entry("那它呢？", [{"role": "user", "content": "x"}], ["甲"], "甲")
        router = FakeRouter({}, FakeClassifier({}))
        row = rw.evaluate_entry(entry, router=router,
                                retriever=BrokenRetriever(), top_k=5)
        assert row["after"]["recall_at_k"] == 0.0

    def test_no_expected_entities_does_not_count_as_hit(self):
        entry = make_entry("那它呢？", [{"role": "user", "content": "x"}], [], "甲")
        router = FakeRouter({}, FakeClassifier({}))
        row = rw.evaluate_entry(entry, router=router,
                                retriever=FakeRetriever("甲"), top_k=5)
        assert row["entity_hit_before"] is False
        assert row["entity_hit_after"] is False


# ==================== 汇总 ====================

class TestSummarize:
    def make_row(self, before_recall, after_recall, hit_before, hit_after):
        return {
            "question": "q", "rewritten": "q'", "rewrite_applied": True,
            "rewrite_used_llm": True,
            "entity_hit_before": hit_before, "entity_hit_after": hit_after,
            "before": {"recall_at_k": before_recall, "mrr": before_recall,
                       "precision_at_k": before_recall, "hits": before_recall},
            "after": {"recall_at_k": after_recall, "mrr": after_recall,
                      "precision_at_k": after_recall, "hits": after_recall},
        }

    def test_empty_rows(self):
        assert rw.summarize([]) == {"queries": 0}

    def test_aggregates_and_deltas(self):
        rows = [
            self.make_row(0.0, 1.0, False, True),
            self.make_row(1.0, 1.0, True, True),
        ]
        s = rw.summarize(rows)
        assert s["queries"] == 2
        assert s["entity_hit_before"] == pytest.approx(0.5)
        assert s["entity_hit_after"] == pytest.approx(1.0)
        assert s["recall_at_k"]["before"] == pytest.approx(0.5)
        assert s["recall_at_k"]["after"] == pytest.approx(1.0)
        assert s["recall_at_k"]["delta"] == pytest.approx(0.5)


# ==================== 批量与报告 ====================

class TestRunAndReport:
    def test_run_only_picks_entries_with_history(self):
        entries = [
            make_entry("那它呢？", [{"role": "user", "content": "x"}], ["甲"], "甲"),
            {"question": "无历史的题", "expected_entities": ["乙"]},
        ]
        router = FakeRouter({"那它呢？": ("甲是什么？", ["甲"])}, FakeClassifier({}))
        rows, summary = rw.run_rewrite_eval(
            retriever=FakeRetriever("甲"), dataset=entries, router=router, top_k=5
        )
        assert len(rows) == 1, "只有带 history 的题应参与"
        assert summary["queries"] == 1

    def test_report_contains_comparison_columns(self, tmp_path):
        rows = [{
            "question": "那它的缺点呢？", "rewritten": "磷酸铁锂电池有什么缺点？",
            "rewrite_applied": True, "rewrite_used_llm": True,
            "intent": "property", "intent_source": "llm",
            "entities": ["磷酸铁锂电池"], "raw_entities": [],
            "expected_entities": ["磷酸铁锂电池"],
            "entity_hit_before": False, "entity_hit_after": True,
            "before": {"recall_at_k": 0.0, "mrr": 0.0, "precision_at_k": 0.0, "hits": 0.0},
            "after": {"recall_at_k": 1.0, "mrr": 1.0, "precision_at_k": 0.2, "hits": 1.0},
        }]
        path = rw.write_rewrite_report(
            rows=rows, summary=rw.summarize(rows),
            output_path=tmp_path / "r.md", top_k=5,
        )
        text = path.read_text(encoding="utf-8")
        assert "原句（含指代）" in text and "改写后" in text
        assert "那它的缺点呢？" in text
        assert "个" not in text[:5]  # 报告应为中文标题


# ==================== 数据集回归（这次改动的核心产物）====================


class TestMultiTurnDataset:
    """确保"让 D1 有数据支撑"这件事被锁住，不会undone"""

    def test_multi_turn_questions_exist(self):
        from qa.eval.dataset import load_qa_golden

        entries = load_qa_golden()
        multi_turn = [e for e in entries if e.get("history")]
        assert len(multi_turn) >= 3, "至少要有 3 道多轮指代题才能支撑改写评估"

    def test_every_multi_turn_entry_is_well_formed(self):
        from qa.eval.dataset import load_qa_golden

        for entry in [e for e in load_qa_golden() if e.get("history")]:
            history = entry["history"]
            assert history, f"{entry['question']} 的 history 为空"
            # 改写要求历史里至少有一条 user 消息（见 LLMRouter._rewrite 的场景闸门）
            assert any(m.get("role") == "user" for m in history), \
                f"{entry['question']} 的 history 缺少 user 消息，改写不会触发"
            assert all(m.get("content") for m in history), \
                f"{entry['question']} 的 history 有空消息"
            assert entry.get("expected_entities"), \
                f"{entry['question']} 缺少 expected_entities，无法判定改写是否消解了指代"
            assert entry.get("expected_intent"), f"{entry['question']} 缺少 expected_intent"

    def test_multi_turn_questions_are_excluded_from_ablation(self):
        """隔离性：多轮题不能进入检索消融，否则会拖低所有组造成系统性偏差"""
        from qa.eval.dataset import load_qa_golden, load_retrieval_dataset

        golden = load_qa_golden()
        ablation = load_retrieval_dataset()
        assert not any(e.get("history") for e in ablation), "消融集不应含多轮指代题"
        assert len(ablation) == len(golden) - len(
            [e for e in golden if e.get("history")]
        )

    def test_ablation_set_size_unchanged(self):
        """加题不得改变消融可比性：100 题基线必须保持"""
        from qa.eval.dataset import load_retrieval_dataset

        assert len(load_retrieval_dataset()) == 100

    def test_multi_turn_questions_have_reference_answer(self):
        """带标准答案才能同时评估改写后回答的 context_recall"""
        from qa.eval.dataset import load_qa_golden

        for entry in [e for e in load_qa_golden() if e.get("history")]:
            assert entry.get("reference_answer"), \
                f"{entry['question']} 缺少 reference_answer"

    def test_json_files_stay_valid(self):
        from qa import config

        for name in ("eval_dataset.json", "eval_dataset_extra.json"):
            path = config.QA_DIR / name
            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data, list) and data
