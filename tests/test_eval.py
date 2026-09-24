# -*- coding: utf-8 -*-
"""
D6 检索评估模块单元测试

覆盖：
- metrics：Recall@K / MRR / Precision@K 的手工核算
- dataset：golden set 加载、chunk 自动生成评测条目、相关性判定
- ablation：内存语料上的"向量 vs BM25 vs 混合"消融 + Markdown 报告
"""

import pytest
from qdrant_client import QdrantClient

from qa.eval.ablation import evaluate_retriever, run_ablation, write_markdown_report
from qa.eval.dataset import build_chunk_dataset, judge_result, load_qa_golden
from qa.eval.metrics import (
    metric_mrr,
    metric_precision_at_k,
    metric_recall_at_k,
    summarize_retrieval,
)
from qa.ingestion.embedder import DebugHashEmbedder
from qa.ingestion.qdrant_store import QdrantStore
from qa.retrieval.base import RetrievalResult
from qa.retrieval.bm25 import BM25Retriever
from qa.retrieval.hybrid import HybridRetriever
from qa.retrieval.vector_retriever import VectorRetriever


# ==================== metrics ====================


class TestMetrics:
    def test_recall_at_k_hit(self):
        # 前 3 名里有相关 → Recall@3 = 1
        assert metric_recall_at_k([False, True, False, True], k=3) == 1
        # 前 3 名里没有相关 → 0
        assert metric_recall_at_k([False, False, False, True], k=3) == 0

    def test_mrr(self):
        # 第一个相关排第 2 → MRR = 1/2
        assert metric_mrr([False, True, False]) == 0.5
        # 无相关 → 0
        assert metric_mrr([False, False]) == 0.0

    def test_precision_at_k(self):
        # 前 4 名中 2 条相关 → 0.5
        assert metric_precision_at_k([True, False, True, False, True], k=4) == 0.5

    def test_summarize(self):
        # 两条查询：q1 第一个命中；q2 前 3 名完全没命中
        summary = summarize_retrieval(
            [[True, False, False], [False, False, False]], top_k=3
        )
        assert summary["queries"] == 2
        assert summary["relevant_queries"] == 1
        assert summary["recall_at_k"] == 0.5   # (1+0)/2
        assert summary["mrr"] == 0.5           # (1+0)/2
        assert round(summary["precision_at_k"], 4) == round((1 / 3 + 0) / 2, 4)


# ==================== dataset ====================


class TestDataset:
    def test_load_qa_golden(self):
        entries = load_qa_golden()
        # Week2 D2：评测集由 60 题扩到 100 题（主文件 + 扩展文件合并）
        assert len(entries) >= 100
        assert all({"question", "expected_entities", "expected_intent"} <= set(e) for e in entries)
        # 扩展集补齐了意图覆盖（aggregate/path 此前为 0）
        intents = {e["expected_intent"] for e in entries}
        assert {"aggregate", "path"} <= intents
        # 新条目携带 reference_answer，供 RAGAS context_recall 使用
        with_gt = [e for e in entries if e.get("reference_answer")]
        assert len(with_gt) >= 40

    def test_build_chunk_dataset(self):
        chunks = [
            {"text": "正文A", "metadata": {"section": "光伏 / 晶硅电池", "source": "s.md"}},
            {"text": "正文B", "metadata": {"section": "储能 / 液流电池", "source": "s.md"}},
        ]
        entries = build_chunk_dataset(chunks)
        assert len(entries) == 2
        assert "晶硅电池" in entries[0]["question"]
        assert entries[0]["expected_section"] == "光伏 / 晶硅电池"

    def test_judge_by_entity(self):
        result = RetrievalResult(text="宁德时代是动力电池龙头", source="vector")
        assert judge_result(result, {"expected_entities": ["宁德时代"]}) is True
        assert judge_result(result, {"expected_entities": ["比亚迪"]}) is False

    def test_judge_by_section(self):
        result = RetrievalResult(text="x", section="光伏 / 晶硅电池", source="vector")
        entry = {"expected_section": "光伏 / 晶硅电池"}
        assert judge_result(result, entry) is True
        # 章节不匹配
        entry2 = {"expected_section": "储能 / 液流电池"}
        assert judge_result(result, entry2) is False


# ==================== 内存语料夹具 ====================


@pytest.fixture
def doc_corpus():
    """内存 Qdrant + 3 条带章节的 chunk + 可组装的各路检索器"""
    client = QdrantClient(":memory:")
    store = QdrantStore(collection="ablation", client=client)
    embedder = DebugHashEmbedder(dimension=16)
    chunks = [
        {"text": "宁德时代是全球动力电池龙头，磷酸铁锂装机量领先。",
         "metadata": {"doc_id": "doc1", "source": "电池.md",
                      "section": "动力电池 / 磷酸铁锂", "chunk_index": 0, "char_count": 1}},
        {"text": "全钒液流电池循环寿命上万次，适合四小时以上长时储能。",
         "metadata": {"doc_id": "doc2", "source": "储能.md",
                      "section": "储能技术 / 液流电池", "chunk_index": 0, "char_count": 1}},
        {"text": "氢燃料电池汽车以氢气为燃料，排放物只有水。",
         "metadata": {"doc_id": "doc3", "source": "氢能.md",
                      "section": "氢能 / 燃料电池汽车", "chunk_index": 0, "char_count": 1}},
    ]
    store.ensure_collection(dimension=16)
    store.upsert_chunks(chunks, embedder.embed([c["text"] for c in chunks]))

    vector = VectorRetriever(store=store, embedder=embedder)
    bm25 = BM25Retriever(documents=store.fetch_all())
    hybrid = HybridRetriever(retrievers=[vector, bm25])
    return {"vector": vector, "bm25": bm25, "hybrid": hybrid}


class TestAblation:
    def test_ablation_runs_and_returns_bounded_metrics(self, doc_corpus):
        chunks = [
            {"text": c["text"],
             "metadata": {"section": c["metadata"]["section"], "source": c["metadata"]["source"]}}
            for c in [
                {"text": "宁德时代是全球动力电池龙头，磷酸铁锂装机量领先。",
                 "metadata": {"section": "动力电池 / 磷酸铁锂", "source": "电池.md"}},
                {"text": "全钒液流电池循环寿命上万次，适合四小时以上长时储能。",
                 "metadata": {"section": "储能技术 / 液流电池", "source": "储能.md"}},
                {"text": "氢燃料电池汽车以氢气为燃料，排放物只有水。",
                 "metadata": {"section": "氢能 / 燃料电池汽车", "source": "氢能.md"}},
            ]
        ]
        dataset = build_chunk_dataset(chunks)

        rows = run_ablation(
            retrievers=doc_corpus,
            dataset=dataset,
            judge=judge_result,
            top_k=3,
        )
        assert len(rows) == 3
        names = {r["retriever"] for r in rows}
        assert names == {"vector", "bm25", "hybrid"}
        for row in rows:
            assert 0 <= row["recall_at_k"] <= 1
            assert 0 <= row["mrr"] <= 1
            assert 0 <= row["precision_at_k"] <= 1
            assert row["queries"] == 3

    def test_evaluate_retriever_per_query(self, doc_corpus):
        entries = [{
            "question": "宁德时代是谁？",
            "expected_entities": ["宁德时代"],
            "expected_intent": "",
        }]
        outcome = evaluate_retriever(doc_corpus["vector"], entries, judge_result, top_k=3)
        assert outcome["summary"]["queries"] == 1
        # 该查询至少在前 3 名里有相关结果（Debug 向量对原文召回应当命中）
        assert outcome["summary"]["recall_at_k"] == 1

    def test_write_markdown_report(self, doc_corpus, tmp_path):
        dataset = [
            {"question": "宁德时代是谁？", "expected_entities": ["宁德时代"], "expected_intent": ""}
        ]
        rows = run_ablation(doc_corpus, dataset, judge_result, top_k=3)
        out = write_markdown_report(rows, tmp_path / "report.md", top_k=3)

        content = out.read_text(encoding="utf-8")
        assert "检索消融实验报告" in content
        assert "| 检索配置 |" in content
        assert "| vector |" in content
        assert "| hybrid |" in content


# ==================== Week2 D4-5：消融对比组装配 ====================


class TestAblationGroups:
    def test_build_groups_with_corpus_and_rerank(self, doc_corpus):
        from qa.eval.run import build_retriever_groups
        from qa.retrieval.hybrid import HybridRetriever
        from qa.retrieval.reranker import NoopReranker

        groups = build_retriever_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            reranker=NoopReranker(),
            fusion_k=60,
        )
        assert set(groups) == {"纯向量", "纯BM25", "混合(RRF)", "混合(RRF)+重排"}

        # 重排组是带 reranker 的 HybridRetriever（两阶段检索配置）
        rerank_group = groups["混合(RRF)+重排"]
        assert isinstance(rerank_group, HybridRetriever)
        assert rerank_group.reranker is not None
        assert rerank_group.reranker.name == "noop"
        assert rerank_group.fusion_k == 60

        # 没有 reranker 时不应出现重排组
        groups2 = build_retriever_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            reranker=None,
        )
        assert "混合(RRF)+重排" not in groups2

    def test_build_groups_kg_only(self):
        from qa.eval.run import build_retriever_groups

        groups = build_retriever_groups(kg=object())  # 装配阶段不调用检索器方法
        assert set(groups) == {"纯KG图谱"}

    def test_rerank_group_runs_ablation(self, doc_corpus, tmp_path):
        from qa.eval.run import build_retriever_groups
        from qa.retrieval.reranker import NoopReranker

        chunks = [
            {"text": "宁德时代是全球动力电池龙头，磷酸铁锂装机量领先。",
             "metadata": {"section": "动力电池 / 磷酸铁锂", "source": "电池.md"}},
            {"text": "全钒液流电池循环寿命上万次，适合四小时以上长时储能。",
             "metadata": {"section": "储能技术 / 液流电池", "source": "储能.md"}},
        ]
        dataset = build_chunk_dataset(chunks)
        groups = build_retriever_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            reranker=NoopReranker(),
        )
        rows = run_ablation(groups, dataset, judge_result, top_k=3)
        assert len(rows) == 4
        assert {r["retriever"] for r in rows} == set(groups)

    def test_write_markdown_report_with_meta(self, doc_corpus, tmp_path):
        from qa.eval.run import build_retriever_groups
        from qa.retrieval.reranker import NoopReranker

        dataset = [
            {"question": "宁德时代是谁？",
             "expected_entities": ["宁德时代"], "expected_intent": ""}
        ]
        groups = build_retriever_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            reranker=NoopReranker(),
            fusion_k=30,
        )
        rows = run_ablation(groups, dataset, judge_result, top_k=3)
        out = write_markdown_report(
            rows,
            tmp_path / "report_meta.md",
            top_k=3,
            meta=["评测集：2 题", "RRF 平滑常数 k = 30", "重排：Noop（离线）"],
        )
        content = out.read_text(encoding="utf-8")
        assert "RRF 平滑常数 k = 30" in content
        assert "重排：Noop（离线）" in content


# ==================== Week2 D6：参数扫描 ====================


class TestTuneSweep:
    def test_build_sweep_groups_labels(self, doc_corpus):
        from qa.eval.tune import build_sweep_groups
        from qa.retrieval.reranker import NoopReranker

        groups = build_sweep_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            fusion_ks=(30, 60),
            reranker=NoopReranker(),
            rerank_candidates=(50,),
        )
        assert set(groups) == {
            "纯向量", "纯BM25",
            "混合(RRF) k=30", "混合(RRF) k=60",
            "混合(RRF)+重排 k=30 cand=50", "混合(RRF)+重排 k=60 cand=50",
        }

    def test_sweep_without_reranker_has_no_cand_groups(self, doc_corpus):
        from qa.eval.tune import build_sweep_groups

        groups = build_sweep_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            fusion_ks=(60, 100),
            reranker=None,
        )
        names = set(groups)
        assert "混合(RRF) k=60" in names
        assert "混合(RRF) k=100" in names
        assert not any("重排" in n for n in names)

    def test_sweep_runs_ablation(self, doc_corpus):
        from qa.eval.run import build_retriever_groups
        from qa.eval.tune import build_sweep_groups
        from qa.retrieval.reranker import NoopReranker

        dataset = [
            {"question": "宁德时代是谁？",
             "expected_entities": ["宁德时代"], "expected_intent": ""}
        ]
        groups = build_sweep_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            fusion_ks=(60,),
            reranker=NoopReranker(),
            rerank_candidates=(50,),
        )
        rows = run_ablation(groups, dataset, judge_result, top_k=3)
        assert len(rows) == len(groups)
        # 组名携带参数，报告中可直接读
        assert any("k=60 cand=50" in r["retriever"] for r in rows)
        # 对照：run.py 的常规组装配不含扫描变体
        base = build_retriever_groups(
            corpus=(doc_corpus["vector"], doc_corpus["bm25"]),
            reranker=None,
        )
        assert len(base) == 3
