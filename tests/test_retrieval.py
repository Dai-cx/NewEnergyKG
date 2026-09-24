# -*- coding: utf-8 -*-
"""
D4 检索层单元测试

覆盖：
- BM25：索引建立、打分排序、空查询
- BM25Retriever：统一接口输出
- VectorRetriever：内存 Qdrant 上的语义检索
- QdrantStore.fetch_all：全量拉取
- KGRetriever：用 Fake KG 客户端验证概览/关系/对比三类图谱检索
- RRF 融合：跨路去重、权重、排序
- HybridRetriever：多路编排
- 向量 + BM25 混合端到端
"""

import pytest
from qdrant_client import QdrantClient

from conftest import FakeRecord, FakeResult, make_kg_client

from qa.ingestion.embedder import DebugHashEmbedder
from qa.ingestion.qdrant_store import QdrantStore
from qa.retrieval.base import RetrievalResult, Retriever
from qa.retrieval.bm25 import BM25Index, BM25Retriever
from qa.retrieval.hybrid import HybridRetriever, rrf_fuse
from qa.retrieval.kg_retriever import KGRetriever
from qa.retrieval.vector_retriever import VectorRetriever

# ==================== BM25 ====================


class TestBM25:
    DOCS = [
        "磷酸铁锂电池安全性高、循环寿命长、成本较低，适合储能与动力电池。",
        "天气预报说明天有雨，气温下降，记得带伞。",
        "三元锂电池能量密度高，但热稳定性一般，成本相对较高。",
    ]

    def test_relevant_doc_ranks_first(self):
        index = BM25Index(self.DOCS)
        hits = index.search("磷酸铁锂成本低", top_k=3)
        assert hits[0][0] == 0  # 命中文档 0

    def test_unrelated_query_ranks_other_doc(self):
        index = BM25Index(self.DOCS)
        hits = index.search("明天下雨", top_k=1)
        assert hits[0][0] == 1

    def test_empty_query_returns_nothing(self):
        index = BM25Index(self.DOCS)
        assert index.search("") == []

    def test_tokenize_english_lowercased(self):
        from qa.retrieval.bm25 import tokenize

        tokens = tokenize("PERC Battery 2024")
        assert any(t == "perc" for t in tokens)
        assert "2024" in tokens

    def test_bm25_retriever_interface(self):
        docs = [{"text": t, "doc_id": f"d{i}", "source": "s.md",
                 "section": "章节", "chunk_index": i}
                for i, t in enumerate(self.DOCS)]
        retriever = BM25Retriever(documents=docs)
        results = retriever.retrieve("磷酸铁锂循环寿命", top_k=2)

        assert results
        assert results[0].source == "bm25"
        assert results[0].doc_id == "d0"
        assert "磷酸铁锂" in results[0].text


# ==================== 向量检索 ====================


@pytest.fixture
def corpus_store():
    """内存 Qdrant + Debug embedder + 两条样例 chunk"""
    client = QdrantClient(":memory:")
    store = QdrantStore(collection="corpus", client=client)
    embedder = DebugHashEmbedder(dimension=16)
    chunks = [
        {"text": "宁德时代是全球动力电池龙头，磷酸铁锂装机量领先。",
         "metadata": {"doc_id": "doc1", "source": "企业.md",
                      "section": "电池企业", "chunk_index": 0, "char_count": 1}},
        {"text": "全钒液流电池循环寿命上万次，适合四小时以上长时储能。",
         "metadata": {"doc_id": "doc2", "source": "储能.md",
                      "section": "液流电池", "chunk_index": 0, "char_count": 1}},
    ]
    store.ensure_collection(dimension=16)
    store.upsert_chunks(chunks, embedder.embed([c["text"] for c in chunks]))
    return store, embedder


class TestVectorRetriever:
    def test_retrieve_semantic_hit(self, corpus_store):
        store, embedder = corpus_store
        retriever = VectorRetriever(store=store, embedder=embedder)
        assert retriever.available()

        results = retriever.retrieve("宁德时代电池", top_k=1)
        assert len(results) == 1
        assert results[0].source == "vector"
        assert results[0].doc_id == "doc1"
        assert results[0].score > 0

    def test_fetch_all_returns_payloads(self, corpus_store):
        store, _ = corpus_store
        payloads = store.fetch_all()
        assert len(payloads) == 2
        assert {p["doc_id"] for p in payloads} == {"doc1", "doc2"}


# ==================== KG 检索 ====================


def _kg_handler_with(query, params):
    """支持 ping / overview / produced_by / compare 四类查询的 Fake 响应。

    判别依据用各查询独有的返回字段（AS items / AS desc / AS desc1），
    而不是关系名——overview 的 Cypher 里同样出现 [:produced_by]->。
    """
    if "ping" in query:
        return FakeResult([FakeRecord({"1": 1})])
    if "AS desc1" in query:  # compare_techs
        return FakeResult([FakeRecord({
            "desc1": "A 技术描述", "efficiency1": "95%", "cost_level1": "低",
            "development_stage1": "成熟", "market_share1": "68%", "maturity1": "成熟",
            "desc2": "B 技术描述", "efficiency2": "92%", "cost_level2": "高",
            "development_stage2": "成熟", "market_share2": "29%", "maturity2": "成熟",
            "categories1": ["锂离子电池"], "companies1": ["宁德时代"],
            "materials1": ["磷酸铁锂"], "applications1": ["电动汽车"],
            "categories2": ["锂离子电池"], "companies2": ["宁德时代"],
            "materials2": ["三元材料"], "applications2": ["电动汽车"],
        })])
    if "AS items" in query:  # get_related
        return FakeResult([FakeRecord({"items": ["宁德时代", "比亚迪"]})])
    if "AS desc" in query:  # get_tech_overview
        return FakeResult([FakeRecord({
            "desc": "A 技术描述", "principle": "原理说明",
            "efficiency": "95%", "cost_level": "中低",
            "development_stage": "商业化成熟", "market_share": "68%", "maturity": "成熟",
            "categories": ["锂离子电池"], "companies": ["宁德时代"],
            "materials": ["磷酸铁锂"], "equipments": ["涂布机"],
            "applications": ["电动汽车"], "indicators": ["能量密度"],
            "policies": [], "compete_technologies": ["三元锂电池"],
        })])
    raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def kg_retriever():
    client = make_kg_client(_kg_handler_with)
    return KGRetriever(kg_client=client, entity_names=["磷酸铁锂电池", "三元锂电池"])


class TestKGRetriever:
    def test_overview_retrieval(self, kg_retriever):
        results = kg_retriever.retrieve("磷酸铁锂电池有什么优点？", top_k=5)
        assert results
        assert results[0].source == "kg"
        assert results[0].extra["kg_type"] == "overview"
        assert "A 技术描述" in results[0].text

    def test_relation_retrieval(self, kg_retriever):
        results = kg_retriever.retrieve("磷酸铁锂电池有哪些公司？", top_k=5)
        assert results
        assert all(r.extra["kg_type"] == "relation" for r in results)
        names = [r.extra["item"] for r in results]
        assert "宁德时代" in names
        assert "比亚迪" in names

    def test_compare_retrieval(self, kg_retriever):
        results = kg_retriever.retrieve("三元锂电池和磷酸铁锂电池哪个好？", top_k=5)
        assert results
        assert results[0].extra["kg_type"] == "compare"
        assert "对比" in results[0].text

    def test_returns_empty_when_no_entity(self, kg_retriever):
        assert kg_retriever.retrieve("随便聊聊吧", top_k=5) == []

    def test_unavailable_without_kg(self):
        from qa.kg_client import _NullKGClient

        retriever = KGRetriever(kg_client=_NullKGClient(), entity_names=[])
        assert retriever.available() is False
        assert retriever.retrieve("磷酸铁锂电池有什么优点？") == []


# ==================== RRF 融合 ====================


def _res(text, source, doc_id=None, chunk_index=None):
    return RetrievalResult(
        text=text, score=0.0, source=source,
        doc_id=doc_id or "", section="章节",
        extra={"chunk_index": chunk_index},
    )


class TestRRF:
    def test_fusion_order_and_dedupe(self):
        a = _res("A内容", "vector", doc_id="d1", chunk_index=0)
        b = _res("B内容", "vector", doc_id="d2", chunk_index=0)
        c = _res("C内容", "vector", doc_id="d3", chunk_index=0)
        d = _res("D内容", "bm25", doc_id="d4", chunk_index=0)
        c2 = _res("C内容", "vector", doc_id="d3", chunk_index=0)  # 与 c 同一身份

        fused = rrf_fuse(
            [[a, b, c], [c2, a, d]],
        )
        texts = [r.text for r in fused]
        # C 出现两次但只保留一次；A 在两路都是前排 → 总分最高
        assert texts.count("C内容") == 1
        assert fused[0].text == "A内容"
        assert len(fused) == 4
        # 分数已被统一为 RRF 分（0 < rrf < 1）
        assert all(0 < r.score < 1 for r in fused)

    def test_same_chunk_from_two_sources_merges(self):
        # 同一 doc_id+chunk_index 被 vector 与 bm25 同时命中 → 合并为一条
        # 注意：rrf_fuse 会改写传入结果的 score，因此每组对比都用全新对象
        fused = rrf_fuse([
            [_res("宁德时代是电池龙头", "vector", doc_id="doc1", chunk_index=0)],
            [_res("宁德时代是电池龙头", "bm25", doc_id="doc1", chunk_index=0)],
        ])
        assert len(fused) == 1
        assert set(fused[0].extra["sources"]) == {"vector", "bm25"}
        # 被多路命中 → RRF 分数高于单路命中（1/61 + 1/61 > 1/61）
        single = rrf_fuse([[_res("宁德时代是电池龙头", "vector", doc_id="doc1", chunk_index=0)]])
        assert fused[0].score > single[0].score

    def test_weights_change_winner(self):
        heavy = _res("重权内容", "bm25", doc_id="w", chunk_index=0)
        light = _res("轻权内容", "vector", doc_id="l", chunk_index=0)
        fused = rrf_fuse(
            [[light], [heavy]],
            weights=[10.0, 1.0],  # 第一路权重远大于第二路
        )
        assert fused[0].text == "轻权内容"

    def test_weights_mismatch_raises(self):
        with pytest.raises(ValueError):
            rrf_fuse([[_res("x", "v", "d1", 0)]], weights=[1.0, 2.0])


class TestHybridRetriever:
    class StubRetriever(Retriever):
        def __init__(self, name, results):
            self.name = name
            self._results = results

        def retrieve(self, query, top_k=10):
            return self._results[:top_k]

    def test_orchestrates_and_limits(self):
        r1 = self.StubRetriever("vector", [_res("x", "vector", "d1", 0),
                                           _res("y", "vector", "d2", 0)])
        r2 = self.StubRetriever("bm25", [_res("z", "bm25", "d3", 0),
                                         _res("x", "bm25", "d1", 0)])
        hybrid = HybridRetriever(retrievers=[r1, r2])
        results = hybrid.retrieve("query", top_k=2)
        assert len(results) == 2
        assert hybrid.list_sources() == ["vector", "bm25"]

    def test_skips_unavailable(self):
        class Offline(Retriever):
            name = "offline"

            def available(self):
                return False

            def retrieve(self, query, top_k=10):
                return [_res("不应出现", "offline", "x", 0)]

        online = self.StubRetriever("vector", [_res("在线结果", "vector", "d1", 0)])
        hybrid = HybridRetriever(retrievers=[Offline(), online])
        results = hybrid.retrieve("query")
        assert len(results) == 1
        assert results[0].text == "在线结果"

    def test_vector_plus_bm25_end_to_end(self, corpus_store):
        store, embedder = corpus_store
        vector = VectorRetriever(store=store, embedder=embedder)
        bm25 = BM25Retriever(documents=store.fetch_all())
        hybrid = HybridRetriever(retrievers=[vector, bm25])

        results = hybrid.retrieve("宁德时代电池", top_k=3)
        assert results
        sources = {r.source for r in results}
        assert sources.issubset({"vector", "bm25"})
        # 文档 1（宁德时代）应排在最前
        assert results[0].doc_id == "doc1"
