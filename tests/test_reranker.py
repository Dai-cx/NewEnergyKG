# -*- coding: utf-8 -*-
"""
D7 重排模块单元测试

覆盖：
- NoopReranker：保持顺序、截断
- create_reranker：无 Key → Noop；有 Key → DashScopeReranker
- DashScopeReranker：用假 dashscope SDK（sys.modules 注入）验证
  重排顺序、分数回写、top_k 截断、参数透传、异常处理
- HybridRetriever：挂载 reranker 后走"RRF 粗排 → rerank 精排"两阶段
"""

import sys

import pytest

from qa import config
from qa.retrieval.base import RetrievalResult
from qa.retrieval.hybrid import HybridRetriever
from qa.retrieval.reranker import (
    DashScopeReranker,
    NoopReranker,
    Reranker,
    RerankerError,
    create_reranker,
)


def _res(text, doc_id=None):
    return RetrievalResult(text=text, source="vector",
                           doc_id=doc_id or text, section="sec", extra={})


# ==================== 假 dashscope SDK ====================


class FakeDashScopeResult:
    """模拟 TextReRank.call 的返回值"""

    def __init__(self, items, status_code=200, message=""):
        self.status_code = status_code
        self.message = message
        self.output = type("Out", (), {"results": items})()


class FakeTextReRank:
    """测试期可配置的假 rerank API（改类属性即改行为）"""

    items: list = []       # 将要返回的 results（index + relevance_score）
    broken: bool = False   # True → 返回 400 错误
    last_call: dict = {}

    @classmethod
    def call(cls, **kwargs):
        cls.last_call = kwargs
        if cls.broken:
            return FakeDashScopeResult([], status_code=400, message="bad request")
        return FakeDashScopeResult(cls.items)


class FakeDashScopeModule:
    TextReRank = FakeTextReRank


@pytest.fixture
def fake_dashscope(monkeypatch):
    """把假 dashscope 模块塞进 sys.modules，并重置状态"""
    monkeypatch.setitem(sys.modules, "dashscope", FakeDashScopeModule())
    FakeTextReRank.items = []
    FakeTextReRank.broken = False
    FakeTextReRank.last_call = {}
    return FakeTextReRank


# ==================== Noop ====================


class TestNoopReranker:
    def test_keeps_order_and_limits(self):
        results = [_res("a", "d1"), _res("b", "d2"), _res("c", "d3")]
        reranked = NoopReranker().rerank("q", results, top_k=2)
        assert [r.doc_id for r in reranked] == ["d1", "d2"]

    def test_empty_results(self):
        assert NoopReranker().rerank("q", []) == []


# ==================== 工厂 ====================


class TestCreateReranker:
    def test_without_key_returns_noop(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
        assert isinstance(create_reranker(), NoopReranker)

    def test_with_key_returns_dashscope(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
        assert isinstance(create_reranker(), DashScopeReranker)


# ==================== DashScope 实现 ====================


class TestDashScopeReranker:
    def test_reorder_by_relevance_score(self, fake_dashscope):
        reranker = DashScopeReranker(model="gte-rerank-v2", api_key="sk")
        results = [_res("不相关内容", "d1"), _res("高度相关", "d2"), _res("一般相关", "d3")]
        # API 返回：d2 最相关(0.95)、d3 其次(0.60)、d1 最差(0.10)
        fake_dashscope.items = [
            {"index": 1, "relevance_score": 0.95},
            {"index": 2, "relevance_score": 0.60},
            {"index": 0, "relevance_score": 0.10},
        ]

        reranked = reranker.rerank("测试查询", results, top_k=2)

        assert [r.doc_id for r in reranked] == ["d2", "d3"]
        assert reranked[0].score == pytest.approx(0.95)   # 精排分数回写
        assert reranked[1].score == pytest.approx(0.60)

    def test_api_params_passed(self, fake_dashscope):
        reranker = DashScopeReranker(model="my-rerank", api_key="sk")
        fake_dashscope.items = [{"index": 0, "relevance_score": 1.0}]

        reranker.rerank("问题", [_res("x", "d1")], top_k=1)
        call = fake_dashscope.last_call
        assert call["model"] == "my-rerank"
        assert call["query"] == "问题"
        assert call["documents"] == ["x"]
        assert call["top_n"] == 1

    def test_api_error_raises(self, fake_dashscope):
        reranker = DashScopeReranker(model="gte-rerank", api_key="sk")
        fake_dashscope.broken = True
        with pytest.raises(RerankerError):
            reranker.rerank("q", [_res("x", "d1")], top_k=1)

    def test_empty_results_no_api_call(self, fake_dashscope):
        reranker = DashScopeReranker(model="gte-rerank", api_key="sk")
        assert reranker.rerank("q", []) == []
        assert fake_dashscope.last_call == {}  # 未发生 API 调用

    def test_missing_required_results_appended(self, fake_dashscope):
        reranker = DashScopeReranker(model="gte-rerank", api_key="sk")
        fake_dashscope.items = [{"index": 1, "relevance_score": 0.9}]

        reranked = reranker.rerank("q", [_res("a", "d1"), _res("b", "d2")], top_k=10)
        # d2 精排第一；d1 未被 API 返回 → 按原序补在后面
        assert [r.doc_id for r in reranked] == ["d2", "d1"]


# ==================== Hybrid 两阶段 ====================


class ReverseReranker(Reranker):
    """测试用：把候选倒序，验证 Hybrid 确实走了 rerank 分支"""

    name = "reverse"

    def rerank(self, query, results, top_k=10):
        return list(reversed(results))[:top_k]


class TestHybridWithReranker:
    class StubRetriever:
        name = "stub"

        def __init__(self, results):
            self._results = results

        def available(self):
            return True

        def retrieve(self, query, top_k=10):
            return self._results[:top_k]

    def test_two_stage_retrieval(self):
        base = HybridRetriever(
            retrievers=[self.StubRetriever([_res("1", "d1"), _res("2", "d2"), _res("3", "d3")])],
            reranker=ReverseReranker(),
        )
        results = base.retrieve("q", top_k=2)
        assert [r.doc_id for r in results] == ["d3", "d2"]  # rerank 反转生效

    def test_without_reranker_keeps_rrf_order(self):
        base = HybridRetriever(
            retrievers=[self.StubRetriever([_res("1", "d1"), _res("2", "d2")])],
        )
        results = base.retrieve("q", top_k=2)
        assert [r.doc_id for r in results] == ["d1", "d2"]

    def test_reranker_deepens_recall_pool(self):
        # 关键回归测试：有 reranker 时，每路必须召回 rerank_candidates 宽的池子，
        # 否则跨语言/低相似度的相关 chunk 会在重排前被截掉
        class CapturingRetriever:
            name = "capture"

            def __init__(self):
                self.requested_top_k = None

            def available(self):
                return True

            def retrieve(self, query, top_k=10):
                self.requested_top_k = top_k
                return [_res("1", "d1"), _res("2", "d2")]

        route = CapturingRetriever()
        hybrid = HybridRetriever(
            retrievers=[route],
            reranker=NoopReranker(),  # 用空实现验证管线行为，不依赖真实 API
            rerank_candidates=50,
        )
        hybrid.retrieve("q", top_k=5)

        # 单路召回深度 = max(top_k*3, rerank_candidates) = 50
        assert route.requested_top_k == 50
